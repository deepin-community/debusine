#!/usr/bin/env python3

# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Debusine: Interacts with debusine server.

Debusine fetches information, submit work requests and other operations.
"""
import asyncio
import datetime
import functools
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib
from collections.abc import Coroutine, Iterable, Sequence
from pathlib import Path, PurePath
from typing import Any, Literal, Protocol, TYPE_CHECKING, overload
from urllib.parse import urlencode

import aiohttp
import magic
import requests
import tenacity
from requests_toolbelt.downloadutils.stream import stream_response_to_file

from debusine.artifacts import LocalArtifact
from debusine.artifacts.models import ArtifactData, CollectionCategory
from debusine.assets import AssetCategory, BaseAssetDataModel
from debusine.client.debusine_http_client import DebusineHttpClient
from debusine.client.exceptions import TokenDisabledError
from debusine.client.file_uploader import FileUploader
from debusine.client.models import (
    ArtifactCreateRequest,
    ArtifactResponse,
    AssetCreateRequest,
    AssetPermissionCheckResponse,
    AssetResponse,
    AssetsResponse,
    CreateWorkflowRequest,
    FileRequest,
    FilesRequestType,
    LookupChildType,
    LookupMultipleRequest,
    LookupMultipleResponse,
    LookupSingleRequest,
    LookupSingleResponse,
    LookupSingleResponseArtifact,
    LookupSingleResponseCollection,
    OnWorkRequestCompleted,
    RelationCreateRequest,
    RelationResponse,
    RelationType,
    RelationsResponse,
    RemoteArtifact,
    TaskConfigurationCollectionContents,
    TaskConfigurationCollectionUpdateResults,
    WorkRequestExternalDebsignRequest,
    WorkRequestRequest,
    WorkRequestResponse,
    WorkflowTemplateRequest,
    WorkflowTemplateResponse,
    WorkspaceInheritanceChain,
    model_to_json_serializable_dict,
)

if TYPE_CHECKING:
    from debusine.client.task_configuration import (
        LocalTaskConfigurationRepository,
        RemoteTaskConfigurationRepository,
    )
    from debusine.tasks.models import LookupMultiple, LookupSingle, OutputData


class _MessageProcessor(Protocol):
    """A callable that processes a message from the server."""

    def __call__(
        self, *, msg_content: dict[str, Any]
    ) -> Coroutine[Any, Any, None] | None: ...


def serialize_local_artifact(
    artifact: LocalArtifact[Any],
    *,
    workspace: str | None,
    work_request: int | None = None,
    expire_at: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Return dictionary to be used by the API to create an artifact."""
    files: FilesRequestType = FilesRequestType({})
    for artifact_path, local_path in artifact.files.items():
        content_type = artifact.content_types.get(artifact_path)
        if content_type is None:
            match PurePath(artifact_path).suffix:
                case ".buildinfo" | ".dsc" | ".changes":
                    # Policy requires these to be UTF-8.
                    content_type = "text/plain; charset=utf-8"
                case ".md":
                    # libmagic guesses this as text/plain.
                    content_type = "text/markdown; charset=utf-8"
                case _:
                    # Try to automatically detect a suitable Content-Type.
                    # Note that for the moment we deliberately don't look at
                    # the content of compressed files, since as a general
                    # rule we don't want browsers to automatically
                    # uncompress them.
                    #
                    # We run Content-Type detection on the client so that
                    # the server doesn't need to run possibly
                    # security-sensitive parsing/guessing code, but we also
                    # don't want to give the client entirely free rein since
                    # some Content-Types could be used to attack browsers.
                    # The server therefore squashes Content-Types that it
                    # doesn't know to be safe; see debusine.web.views.files.
                    content_type = magic.Magic(
                        mime=True, mime_encoding=True
                    ).from_file(local_path)
        files[artifact_path] = FileRequest.create_from(
            local_path, content_type=content_type
        )
    for artifact_path, file_request in artifact.remote_files.items():
        files[artifact_path] = file_request

    artifact.validate_model()

    serialized = model_to_json_serializable_dict(
        ArtifactCreateRequest(
            workspace=workspace,
            category=artifact.category,
            files=files,
            data=(
                artifact.data.dict()
                if isinstance(artifact.data, ArtifactData)
                else artifact.data
            ),
            work_request=work_request,
            expire_at=expire_at,
        )
    )

    # If the workspace was not specified: do not send it to the API.
    # The server will assign it.
    if serialized["workspace"] is None:
        del serialized["workspace"]

    return serialized


class Debusine:
    """Class to interact with debusine server."""

    API_VERSION = '1.0'

    def __init__(
        self,
        base_api_url: str,
        api_token: str | None = None,
        scope: str | None = None,
        *,
        logger: logging.Logger,
    ):
        """
        Initialize client.

        :param base_api_url: URL for API endpoint (e.g. http://localhost/api)
        :param api_token: optional token to be used for the calls.
        """
        self.base_api_url: str = base_api_url
        self.token: str | None = api_token
        self.api_url = base_api_url.rstrip("/") + "/" + self.API_VERSION

        # TODO: Currently Workers don't have access to a
        # WorkRequestResponse's scope in and can't set it here.
        # Also Workers reuse a Debusine client instance for multiple
        # requests with different scopes.
        # Ideally 'scope' will become a constructor parameter, where
        # self.scope can't be None, simplifying/hardening the code.
        self.scope: str | None = scope

        self._logger = logger

        self._debusine_http_client: DebusineHttpClient = DebusineHttpClient(
            self.api_url, api_token, scope
        )

    def work_request_iter(self) -> Iterable[WorkRequestResponse]:
        """
        List all WorkRequests.

        :raises many: see _api_request method documentation.
        """
        return self._debusine_http_client.iter_paginated_get(
            "/work-request/", WorkRequestResponse
        )

    def work_request_get(self, work_request_id: int) -> WorkRequestResponse:
        """
        Get WorkRequest for work_request_id.

        :param work_request_id: id to fetch the status of.
        :raises many: see _api_request method documentation.
        """
        return self._debusine_http_client.get(
            f"/work-request/{work_request_id}/", WorkRequestResponse
        )

    def work_request_update(
        self, work_request_id: int, *, priority_adjustment: int
    ) -> WorkRequestResponse:
        """Update properties of work_request_id."""
        return self._debusine_http_client.patch(
            path=f"/work-request/{work_request_id}/",
            data={"priority_adjustment": priority_adjustment},
            expected_class=WorkRequestResponse,
        )

    def work_request_retry(
        self,
        work_request_id: int,
    ) -> WorkRequestResponse:
        """Retry a work request."""
        return self._debusine_http_client.post(
            path=f"/work-request/{work_request_id}/retry/",
            data={},
            expected_class=WorkRequestResponse,
        )

    def work_request_abort(
        self,
        work_request_id: int,
    ) -> WorkRequestResponse:
        """Abort a work request."""
        return self._debusine_http_client.post(
            path=f"/work-request/{work_request_id}/abort/",
            data={},
            expected_class=WorkRequestResponse,
        )

    @staticmethod
    def work_request_completed_path(work_request_id: int) -> str:
        """Return path to update the completed result for work_request_id."""
        return f"/work-request/{work_request_id}/completed/"

    def work_request_completed_update(
        self, work_request_id: int, result: str, output_data: "OutputData"
    ) -> None:
        """Update work_request_id as completed with result."""
        return self._debusine_http_client.put(
            path=self.work_request_completed_path(work_request_id),
            data={
                "result": result,
                "output_data": model_to_json_serializable_dict(
                    output_data, exclude_unset=True
                ),
            },
            expected_class=None,
        )

    def work_request_create(
        self, work_request: WorkRequestRequest
    ) -> WorkRequestResponse:
        """
        Create a work request (via POST /work-request/).

        :return: WorkRequest returned by the server.
        :raises: see _api_request method documentation.
        """
        data = work_request.dict()

        # If workspace is None: the client does not want to specify
        # a workspace. Do not send it to the server: the server will
        # assign the default one
        if data["workspace"] is None:
            del data["workspace"]

        return self._debusine_http_client.post(
            "/work-request/",
            WorkRequestResponse,
            data=data,
        )

    def work_request_external_debsign_get(
        self, work_request_id: int
    ) -> WorkRequestResponse:
        """Get data about work request waiting for an external `debsign`."""
        return self._debusine_http_client.get(
            f"/work-request/{work_request_id}/external-debsign/",
            WorkRequestResponse,
        )

    def work_request_external_debsign_complete(
        self,
        work_request_id: int,
        external_debsign_request: WorkRequestExternalDebsignRequest,
    ) -> WorkRequestResponse:
        """Provide external `debsign` data to a work request."""
        data = external_debsign_request.dict()

        return self._debusine_http_client.post(
            f"/work-request/{work_request_id}/external-debsign/",
            WorkRequestResponse,
            data,
        )

    def workflow_template_create(
        self, workflow_template: WorkflowTemplateRequest
    ) -> WorkflowTemplateResponse:
        """
        Create a workflow template (via POST /workflow-template/).

        :return: WorkflowTemplate returned by the server.
        :raises: see _api_request method documentation.
        """
        data = workflow_template.dict()

        # If workspace is None: the client does not want to specify a
        # workspace. Do not send it to the server: the server will assign
        # the default one.
        if data["workspace"] is None:
            del data["workspace"]

        return self._debusine_http_client.post(
            "/workflow-template/",
            WorkflowTemplateResponse,
            data=data,
            expected_statuses=[requests.codes.created],
        )

    def workflow_create(
        self, create_workflow: CreateWorkflowRequest
    ) -> WorkRequestResponse:
        """
        Create a workflow (via POST /workflow/).

        :return: Workflow WorkRequest returned by the server.
        :raises: see _api_request method documentation.
        """
        data = create_workflow.dict()

        # If workspace is None: the client does not want to specify a
        # workspace. Do not send it to the server: the server will assign
        # the default one.
        if data["workspace"] is None:
            del data["workspace"]

        return self._debusine_http_client.post(
            "/workflow/",
            WorkRequestResponse,
            data=data,
            expected_statuses=[requests.codes.created],
        )

    def artifact_get(self, artifact_id: int) -> ArtifactResponse:
        """
        Get artifact information.

        Use download_artifact() to download the artifact.
        """
        return self._debusine_http_client.get(
            f"/artifact/{artifact_id}/",
            ArtifactResponse,
        )

    def artifact_create(
        self,
        artifact: LocalArtifact[Any],
        *,
        workspace: str | None,
        work_request: int | None = None,
        expire_at: datetime.datetime | None = None,
    ) -> ArtifactResponse:
        """Create artifact in the debusine server."""
        return self._debusine_http_client.post(
            "/artifact/",
            ArtifactResponse,
            data=serialize_local_artifact(
                artifact=artifact,
                workspace=workspace,
                work_request=work_request,
                expire_at=expire_at,
            ),
            expected_statuses=[requests.codes.created],
        )

    def _url_for_file_in_artifact(
        self, artifact_id: int, path_in_artifact: str
    ) -> str:
        """Return URL to upload a file in the artifact."""
        quoted_path = urllib.parse.quote(path_in_artifact)

        return f"{self.api_url}/artifact/{artifact_id}/files/{quoted_path}/"

    def upload_files(
        self,
        artifact_id: int,
        upload_files: dict[str, Path],
        base_directory: Path | None = None,
    ) -> None:
        """
        Upload into artifact the files.

        :param artifact_id: artifact_id to upload files to.
        :param upload_files: list of files to upload.
        :param base_directory: base directory for relative path's files
         to upload.
        """
        # The file uploader won't work as an anonymous user.  If this
        # assertion is optimized out then the file upload will fail
        # harmlessly; this just helps with type-checking.
        assert self.token is not None

        file_uploader = FileUploader(self.token, logger=self._logger)

        for artifact_path, local_path in upload_files.items():
            if not local_path.is_absolute():
                if base_directory is None:
                    raise ValueError(
                        f"{local_path} is relative: base_directory "
                        "parameter cannot be None"
                    )

                local_path = base_directory.joinpath(local_path)

            url = self._url_for_file_in_artifact(artifact_id, artifact_path)

            file_uploader.upload(local_path, url)

    def upload_artifact(
        self,
        local_artifact: LocalArtifact[Any],
        *,
        workspace: str | None,
        work_request: int | None = None,
        expire_at: datetime.datetime | None = None,
    ) -> RemoteArtifact:
        """Upload (create and upload files) the local_artifact to the server."""
        artifact_response = self.artifact_create(
            local_artifact,
            workspace=workspace,
            work_request=work_request,
            expire_at=expire_at,
        )

        files_to_upload: dict[str, Path] = {}
        for file_path_to_upload in artifact_response.files_to_upload:
            files_to_upload[file_path_to_upload] = local_artifact.files[
                file_path_to_upload
            ]

        self.upload_files(
            artifact_response.id,
            files_to_upload,
        )

        return RemoteArtifact(
            id=artifact_response.id,
            url=artifact_response.url,
            workspace=artifact_response.workspace,
        )

    def relation_create(
        self,
        artifact_id: int,
        target_id: int,
        relation_type: RelationType,
    ) -> RelationResponse:
        """
        Create a new relation between artifacts.

        :param artifact_id: relation from
        :param target_id: relation to
        :param relation_type: type of relation such as extends, relates-to,
          built-using
        :return: True if the relation already existed/has been created,
          False if it could not be created
        """
        relation_request = RelationCreateRequest(
            artifact=artifact_id, target=target_id, type=relation_type
        )
        return self._debusine_http_client.post(
            "/artifact-relation/",
            RelationResponse,
            data=relation_request.dict(include={"artifact", "target", "type"}),
            expected_statuses=[requests.codes.ok, requests.codes.created],
        )

    def relation_list(
        self, *, artifact_id: int | None = None, target_id: int | None = None
    ) -> RelationsResponse:
        """
        List relations associated with an artifact.

        Exactly one of artifact_id or target_id must be set.

        :param artifact_id: search for relations from this artifact
        :param target_id: search for relations to this artifact
        :return: a list of relations
        :raises ValueError: if exactly one of artifact_id or target_id is not
          set
        """
        if (artifact_id is None) == (target_id is None):
            raise ValueError(
                "Exactly one of artifact_id or target_id must be set"
            )
        params = {}
        if artifact_id is not None:
            params["artifact"] = str(artifact_id)
        else:
            assert target_id is not None
            params["target_artifact"] = str(target_id)
        return self._debusine_http_client.get(
            "/artifact-relation/?" + urlencode(params), RelationsResponse
        )

    @staticmethod
    def _should_download_as_tarball(artifact: ArtifactResponse) -> bool:
        """
        Decide whether to download artifact as a tarball or individually.

        Apply some heuristics to decide whether it's worth having the server
        build a tarball or not.
        """
        if len(artifact.files) == 1:
            return False
        total_size = 0
        for file in artifact.files.values():
            total_size += file.size
            if file.size > 20 * 1024 * 1024:
                return False
        return total_size < 100 * 1024 * 1024

    def _streaming_download(
        self,
        url: str,
    ) -> requests.Response:
        """Streaming download of url."""
        headers = {"Token": self.token}
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        return response

    def download_artifact(
        self,
        artifact_id: int,
        destination: Path,
        *,
        tarball: bool = False,
    ) -> ArtifactResponse:
        """
        Download artifact_id into destination directory.

        :param artifact_id: artifact id to download
        :param destination: destination directory to download/uncompress
        :param tarball: True to only download the tarball (artifact-id.tar.gz),
          False to uncompress it
        """
        artifact = self._debusine_http_client.get(
            f"/artifact/{artifact_id}",
            ArtifactResponse,
        )

        if not tarball and not self._should_download_as_tarball(artifact):
            self._logger.info("Downloading artifact files into %s", destination)
            for name, file in artifact.files.items():
                self._logger.debug(
                    "Downloading artifact file %s from %s", name, file.url
                )
                path = destination / name
                if path.parent != destination:
                    if not path.is_relative_to(destination):
                        raise AssertionError(f"Invalid filename {name}")
                    path.parent.mkdir(parents=True, exist_ok=True)
                stream_response_to_file(
                    self._streaming_download(file.url), path
                )
            return artifact

        url = artifact.download_tar_gz_url
        if tarball:
            destination_file = destination / f"artifact-{artifact_id}.tar.gz"
            stream_response_to_file(
                self._streaming_download(url),
                destination_file,
            )
            self._logger.info("Artifact downloaded: %s", destination_file)
            return artifact

        self._logger.info(
            "Downloading artifact and uncompressing into %s", destination
        )
        raw_response = self._streaming_download(url).raw
        raw_response.decode_content = True
        self._uncompress(raw_response, destination, url)

        return artifact

    def _uncompress(
        self,
        raw_response: io.IOBase,
        destination: Path,
        url: str,
    ) -> None:
        with (
            tempfile.TemporaryFile() as stderr_file,
            tempfile.TemporaryFile() as list_file,
        ):
            proc = subprocess.Popen(
                ["tar", "-xzv"],
                stdin=subprocess.PIPE,
                stderr=stderr_file,
                stdout=list_file,
                cwd=destination,
            )

            assert proc.stdin is not None
            shutil.copyfileobj(raw_response, proc.stdin)

            proc.stdin.close()

            returncode = proc.wait()

            list_file.seek(0)
            self._logger.info(
                list_file.read().decode(errors="ignore").rstrip("\n")
            )

            if returncode != 0:
                stderr_file.seek(0)
                stderr_contents = stderr_file.read().decode(errors="ignore")
                raise RuntimeError(
                    f"Error untarring {url}. "
                    f"Returncode: {returncode} stderr:\n{stderr_contents}"
                )

    def download_artifact_file(
        self,
        artifact_id: int | ArtifactResponse,
        path_in_artifact: str,
        destination: Path,
    ) -> ArtifactResponse:
        """
        Download a single file from artifact into destination directory.

        :param artifact_id: artifact id to download, or an
          :py:class:`ArtifactResponse` representing the artifact
        :param path_in_artifact: download the file from the artifact with
          this name
        :param destination: destination file to download to
        """
        if isinstance(artifact_id, ArtifactResponse):
            artifact = artifact_id
        else:
            artifact = self._debusine_http_client.get(
                f"/artifact/{artifact_id}",
                ArtifactResponse,
            )

        if path_in_artifact not in artifact.files:
            raise ValueError(
                f"No file '{path_in_artifact}' in artifact {artifact.id}"
            )
        url = artifact.files[path_in_artifact].url
        response = self._streaming_download(url)
        stream_response_to_file(response, destination)
        self._logger.info("Artifact file downloaded: %s", destination)

        return artifact

    def asset_create(
        self,
        category: AssetCategory,
        data: BaseAssetDataModel,
        *,
        workspace: str,
        work_request: int | None = None,
    ) -> AssetResponse:
        """Create asset in the debusine server."""
        return self._debusine_http_client.post(
            "/asset/",
            AssetResponse,
            data=json.loads(
                AssetCreateRequest(
                    category=category,
                    workspace=workspace,
                    work_request=work_request,
                    data=data,
                ).json()
            ),
            expected_statuses=[requests.codes.created],
        )

    def asset_list(
        self,
        *,
        asset_id: int | None = None,
        work_request: int | None = None,
        workspace: str | None = None,
    ) -> AssetsResponse:
        """Create asset in the debusine server."""
        params: dict[str, str | int] = {}
        if asset_id:
            params["asset"] = asset_id
        if work_request:
            params["work_request"] = work_request
        if workspace:
            params["workspace"] = workspace
        if not params:
            raise ValueError(
                "At least one of asset, work_request, and workspace must be "
                "specified"
            )
        query = urlencode(params)
        return self._debusine_http_client.get(
            f"/asset/?{query}",
            AssetsResponse,
        )

    def asset_permission_check(
        self,
        *,
        asset_category: str,
        asset_slug: str,
        permission_name: str,
        artifact_id: int,
        work_request_id: int,
        workspace: str,
    ) -> AssetPermissionCheckResponse:
        """Check work_request's permission to use an asset on an artifact."""
        return self._debusine_http_client.post(
            f"/asset/{asset_category}/{asset_slug}/{permission_name}/",
            AssetPermissionCheckResponse,
            data={
                "artifact_id": artifact_id,
                "work_request_id": work_request_id,
                "workspace": workspace,
            },
        )

    @overload
    def lookup_single(
        self,
        lookup: "LookupSingle",
        work_request: int,
        expect_type: Literal[LookupChildType.ARTIFACT],
        default_category: CollectionCategory | None = None,
    ) -> LookupSingleResponseArtifact: ...

    @overload
    def lookup_single(
        self,
        lookup: "LookupSingle",
        work_request: int,
        expect_type: Literal[LookupChildType.COLLECTION],
        default_category: CollectionCategory | None = None,
    ) -> LookupSingleResponseCollection: ...

    @overload
    def lookup_single(
        self,
        lookup: "LookupSingle",
        work_request: int,
        expect_type: LookupChildType,
        default_category: CollectionCategory | None = None,
    ) -> LookupSingleResponse: ...

    def lookup_single(
        self,
        lookup: "LookupSingle",
        work_request: int,
        expect_type: LookupChildType,
        default_category: CollectionCategory | None = None,
    ) -> LookupSingleResponse:
        """Look up a single collection item."""
        request = LookupSingleRequest(
            lookup=lookup,
            work_request=work_request,
            expect_type=expect_type,
            default_category=default_category,
        )
        return self._debusine_http_client.post(
            "/lookup/single/", LookupSingleResponse, data=request.dict()
        )

    @overload
    def lookup_multiple(
        self,
        lookup: "LookupMultiple",
        work_request: int,
        expect_type: Literal[LookupChildType.ARTIFACT],
        default_category: CollectionCategory | None = None,
    ) -> LookupMultipleResponse[LookupSingleResponseArtifact]: ...

    @overload
    def lookup_multiple(
        self,
        lookup: "LookupMultiple",
        work_request: int,
        expect_type: Literal[LookupChildType.COLLECTION],
        default_category: CollectionCategory | None = None,
    ) -> LookupMultipleResponse[LookupSingleResponseCollection]: ...

    @overload
    def lookup_multiple(
        self,
        lookup: "LookupMultiple",
        work_request: int,
        expect_type: LookupChildType,
        default_category: CollectionCategory | None = None,
    ) -> LookupMultipleResponse[LookupSingleResponse]: ...

    def lookup_multiple(
        self,
        lookup: "LookupMultiple",
        work_request: int,
        expect_type: LookupChildType,
        default_category: CollectionCategory | None = None,
    ) -> LookupMultipleResponse[LookupSingleResponse]:
        """Look up multiple collection items."""
        request = LookupMultipleRequest(
            lookup=lookup.dict()["__root__"],
            work_request=work_request,
            expect_type=expect_type,
            default_category=default_category,
        )
        return self._debusine_http_client.post(
            "/lookup/multiple/", LookupMultipleResponse, data=request.dict()
        )

    def fetch_task_configuration_collection(
        self, *, workspace: str, name: str
    ) -> "RemoteTaskConfigurationRepository":
        """Fetch a task configuration collection."""
        from debusine.client.task_configuration import (
            Manifest,
            RemoteTaskConfigurationRepository,
        )

        response = self._debusine_http_client.get(
            f"/task-configuration/{workspace}/{name}/",
            TaskConfigurationCollectionContents,
        )
        manifest = Manifest(workspace=workspace, collection=response.collection)
        return RemoteTaskConfigurationRepository.from_items(
            manifest, response.items
        )

    def push_task_configuration_collection(
        self,
        *,
        repo: "LocalTaskConfigurationRepository",
        dry_run: bool,
    ) -> TaskConfigurationCollectionUpdateResults:
        """Replace the contents of a task configuration collection on server."""
        from debusine.client.task_configuration import InvalidRepository

        if repo.manifest is None:
            raise InvalidRepository("repository has no manifest")

        data = {
            "collection": repo.manifest.collection.dict(),
            "items": [i.item.dict() for i in repo.entries.values()],
            "dry_run": dry_run,
        }

        return self._debusine_http_client.post(
            f"/task-configuration/{repo.manifest.workspace}"
            f"/{repo.manifest.collection.name}/",
            data=data,
            expected_class=TaskConfigurationCollectionUpdateResults,
        )

    def get_workspace_inheritance(
        self, workspace: str
    ) -> WorkspaceInheritanceChain:
        """Get a workspace inheritance chain."""
        return self._debusine_http_client.get(
            f"/workspace/{workspace}/inheritance/",
            expected_class=WorkspaceInheritanceChain,
        )

    def set_workspace_inheritance(
        self, workspace: str, *, chain: WorkspaceInheritanceChain
    ) -> WorkspaceInheritanceChain:
        """Set a workspace inheritance chain."""
        return self._debusine_http_client.post(
            f"/workspace/{workspace}/inheritance/",
            data=model_to_json_serializable_dict(chain),
            expected_class=WorkspaceInheritanceChain,
        )

    def _log_tenacity_exception(
        self, retry_state: tenacity.RetryCallState
    ) -> None:
        # outcome is always non-None in an "after" hook.
        assert retry_state.outcome is not None

        exception = retry_state.outcome.exception()
        self._logger.error("  Error: %s", exception)

    def _on_work_request_completed(
        self,
        *,
        command: Sequence[str | os.PathLike[str]],
        working_directory: Path,
        last_completed_at: Path | None,
        msg_content: dict[str, Any],
    ) -> None:
        on_work_request_completed = OnWorkRequestCompleted.parse_obj(
            msg_content
        )

        # Execute the command
        cmd = list(command) + [
            str(on_work_request_completed.work_request_id),
            on_work_request_completed.result,
        ]
        self._logger.info("Executing %s", cmd)
        p = subprocess.Popen(cmd, cwd=working_directory)
        p.wait()

        self.write_last_completed_at(
            last_completed_at,
            on_work_request_completed.completed_at,
        )

    async def _wait_and_execute(
        self,
        *,
        url: str,
        command: Sequence[str | os.PathLike[str]],
        working_directory: Path,
        last_completed_at: Path | None,
    ) -> None:
        """
        Connect using websockets to URL and wait for msg. Execute command.

        When a message is received (with "work_request_id" and "success")
        calls self._execute_command.
        """

        @tenacity.retry(
            sleep=self._tenacity_sleep,
            wait=tenacity.wait_random(min=1, max=6),
            retry=tenacity.retry_if_exception_type(
                aiohttp.client_exceptions.ClientError
            ),
            after=self._log_tenacity_exception,
        )
        async def do_wait_for_messages(session: aiohttp.ClientSession) -> None:
            headers = {}
            if self.token is not None:
                headers["Token"] = self.token

            self._logger.info("Requesting %s", url)

            def connected(msg_content: dict[Any, Any]) -> None:  # noqa: U100
                self._logger.info("Connected!")

            async with session.ws_connect(
                url, headers=headers, heartbeat=60
            ) as ws:
                async for msg in ws:  # pragma: no branch
                    await self.process_async_message(
                        msg,
                        {
                            "connected": connected,
                            "work_request_completed": functools.partial(
                                self._on_work_request_completed,
                                command=command,
                                working_directory=working_directory,
                                last_completed_at=last_completed_at,
                            ),
                        },
                    )

        async with aiohttp.ClientSession() as session:
            await do_wait_for_messages(session)

    @staticmethod
    def write_last_completed_at(
        completed_at_file: Path | None,
        completed_at: datetime.datetime | None,
    ) -> None:
        """Write to completed_at_file the completed_at datetime."""
        if completed_at_file is None:
            return

        completed_at_formatted = (
            completed_at.isoformat() if completed_at else None
        )

        completed_at_file.write_text(
            json.dumps({"last_completed_at": completed_at_formatted}, indent=2)
            + "\n"
        )

    @staticmethod
    def _read_last_completed_at(
        last_completed_at: Path | None,
    ) -> str | None:
        if last_completed_at is None:
            return None

        if not last_completed_at.exists():
            # The file does not exist, it will be created if a WorkRequest
            # is completed
            return None

        last_completed_at_json = json.loads(last_completed_at.read_bytes())
        completed_at = last_completed_at_json.get("last_completed_at")
        assert completed_at is None or isinstance(completed_at, str)
        return completed_at

    def on_work_request_completed(
        self,
        *,
        workspaces: list[str] | None = None,
        last_completed_at: Path | None = None,
        command: list[str],
        working_directory: Path,
    ) -> None:
        """Execute command when a work request is completed."""
        debusine_ws_url = self.base_api_url.replace("http", "ws", 1)
        url_work_request_completed = (
            f"{debusine_ws_url}/ws/1.0/work-request/on-completed/"
        )

        params = {}

        if workspaces:
            params["workspaces"] = ",".join(workspaces)

        completed_at_since = self._read_last_completed_at(last_completed_at)

        if completed_at_since:
            params["completed_at_since"] = completed_at_since

        if params:
            url_work_request_completed += "?" + urlencode(params)

        asyncio.run(
            self._wait_and_execute(
                url=url_work_request_completed,
                command=command,
                working_directory=working_directory,
                last_completed_at=last_completed_at,
            )
        )

    async def _tenacity_sleep(self, delay: float) -> None:
        # Used for the unit tests
        await asyncio.sleep(delay)

    async def process_async_message(
        self,
        msg: aiohttp.http_websocket.WSMessage,
        msg_text_to_callable: dict["str", _MessageProcessor],
    ) -> bool:
        """
        Process "msg": logs possible error, raise errors.

        If msg.type is aiohttp.WSMsgType.TEXT: decode msg.data (contains JSON),
        call callable msg_text_to_callable[msg.data["text"]](msg_content: dict).

        Return True a callable from msg_to_callable is called or False if not
        (invalid messages, etc.).

        :raise TokenDisabledError: if reason_code is TOKEN_DISABLED.
        """
        if not isinstance(msg, aiohttp.http_websocket.WSMessage):
            self._logger.debug(
                'Worker._process_message: unexpected type: %s '
                'is not an instance of %s',
                type(msg),
                aiohttp.http_websocket.WSMessage,
            )
            return False

        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                msg_content = json.loads(msg.data)
                self._logger.debug(
                    "Received from the server: '%s'", msg_content
                )
            except json.JSONDecodeError as exc:
                self._logger.info(  # noqa: G200
                    'Worker._process_message: JSONDecodeError on a message '
                    'received from the server: Received: "%s" Error: "%s"',
                    msg.data,
                    exc,
                )
                return False

            if (text := msg_content.get('text')) is not None:
                if (functor := msg_text_to_callable.get(text)) is not None:
                    if asyncio.iscoroutinefunction(functor):
                        await functor(msg_content=msg_content)
                    else:
                        functor(msg_content=msg_content)
                    return True
                else:
                    self._logger.debug(
                        'Debusine._process_async_message: invalid text in '
                        'msg_content received from the server: %s ',
                        text,
                    )
            elif (reason_code := msg_content.get('reason_code')) is not None:
                if reason_code == 'TOKEN_DISABLED':
                    reason = msg_content.get("reason", "Unknown")
                    raise TokenDisabledError(reason)
                else:
                    self._logger.error(
                        'Unknown reason code (ignoring it): "%s"', reason_code
                    )
            else:
                reason = msg_content.get('reason', 'unknown')
                self._logger.info("Disconnected. Reason: '%s'", reason)

        return False
