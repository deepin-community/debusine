# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Debusine class."""

import contextlib
import datetime
import hashlib
import io
import json
import logging
import os
import stat
import tarfile
import textwrap
import types
import urllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn
from unittest import mock
from unittest.mock import MagicMock, call
from urllib.parse import urlencode

import aiohttp

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

import requests
import responses

from debusine.artifacts import WorkRequestDebugLogs
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    EmptyArtifactData,
)
from debusine.assets import AssetCategory, KeyPurpose, SigningKeyData
from debusine.client.debusine import Debusine
from debusine.client.models import (
    ArtifactResponse,
    AssetPermissionCheckResponse,
    AssetResponse,
    AssetsResponse,
    CreateWorkflowRequest,
    FileResponse,
    FilesResponseType,
    LookupChildType,
    LookupMultipleResponse,
    LookupResultType,
    LookupSingleResponse,
    RelationCreateRequest,
    RelationResponse,
    RelationType,
    RelationsResponse,
    RemoteArtifact,
    StrMaxLength255,
    WorkRequestExternalDebsignRequest,
    WorkRequestRequest,
    WorkflowTemplateRequest,
    model_to_json_serializable_dict,
)
from debusine.client.tests import server
from debusine.client.tests.server import DebusineAioHTTPTestCase
from debusine.tasks.models import LookupMultiple, OutputData
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_listing_response,
    create_work_request_response,
    create_workflow_template_response,
)


class DebusineTests(TestCase):
    """Tests for the Debusine class."""

    def setUp(self) -> None:
        """Initialize tests."""
        self.api_url = "https://debusine.debian.org/api"

        self.token = "token"
        self.logger = mock.create_autospec(spec=logging.Logger)
        # Add a trailing slash to the API URL to ensure that all parts of
        # the client strip it correctly.
        self.debusine = Debusine(
            self.api_url + "/", self.token, logger=self.logger
        )

    @responses.activate
    def test_work_request_iter(self) -> None:
        """Debusine.work_request_iter yields work requests."""
        work_requests = [
            create_work_request_response(id=1),
            create_work_request_response(id=2),
        ]
        listing_response = create_listing_response(*work_requests)

        responses.add(
            responses.GET,
            f'{self.debusine.api_url}/work-request/',
            json=model_to_json_serializable_dict(listing_response),
        )

        self.assertEqual(list(self.debusine.work_request_iter()), work_requests)
        self.assert_token_key_included_in_all_requests('token')

    @responses.activate
    def test_work_request_get(self) -> None:
        """Debusine.work_request_get returns the work request."""
        work_request_id = 10
        work_request_response = create_work_request_response(id=work_request_id)

        responses.add(
            responses.GET,
            f'{self.debusine.api_url}/work-request/{work_request_id}/',
            json=model_to_json_serializable_dict(work_request_response),
        )

        self.assertEqual(
            self.debusine.work_request_get(work_request_id),
            work_request_response,
        )
        self.assert_token_key_included_in_all_requests('token')

    @responses.activate
    def test_work_request_update(self) -> None:
        """Debusine.work_request_update patches the work request."""
        work_request_id = 10
        work_request_response = create_work_request_response(
            id=work_request_id, priority_adjustment=100
        )

        responses.add(
            responses.PATCH,
            f"{self.debusine.api_url}/work-request/{work_request_id}/",
            json=model_to_json_serializable_dict(work_request_response),
        )

        self.assertEqual(
            self.debusine.work_request_update(
                work_request_id, priority_adjustment=100
            ),
            work_request_response,
        )
        self.assert_token_key_included_in_all_requests("token")

    @responses.activate
    def test_work_request_retry(self) -> None:
        """Debusine.work_request_retry triggers a retry."""
        work_request_id = 10
        work_request_response = create_work_request_response(
            id=work_request_id + 1,
        )

        responses.add(
            responses.POST,
            f"{self.debusine.api_url}/work-request/{work_request_id}" "/retry/",
            json=model_to_json_serializable_dict(work_request_response),
        )

        self.assertEqual(
            self.debusine.work_request_retry(work_request_id),
            work_request_response,
        )
        self.assert_token_key_included_in_all_requests("token")

    def verify_work_request_create_success(
        self, workspace: str | None = None
    ) -> None:
        """
        Verify Debusine.work_request_create().

        :param workspace: if it's None: assert that the client's request
          does not contain "workspace" (it's assigned by the server). The
          server response contains workspace="System".

          If it's not None: the request contains "workspace" with the
          correct value.
        """
        expected_work_request = workspace if workspace is not None else "System"

        # response always contains "workspace". It's "system" if it was
        # no in the request
        expected_work_request_response = create_work_request_response(
            workspace=expected_work_request
        )

        responses.add(
            responses.POST,
            f'{self.debusine.api_url}/work-request/',
            json=model_to_json_serializable_dict(
                expected_work_request_response
            ),
        )

        work_request_fields: dict[str, Any] = {
            "task_name": "sbuild",
            "task_data": {},
            "event_reactions": {},
            "workspace": workspace,
        }
        # these fields are always posted
        fields_to_post = {"task_name", "task_data", "event_reactions"}

        # "workspace" is posted only if it's not None
        if workspace is not None:
            fields_to_post.add("workspace")

        # work_request_to_post can contain "workspace=None". The method
        # Debusine.work_request_create will remove this key if it's None
        work_request_to_post = WorkRequestRequest(**work_request_fields)

        actual_work_request_response = self.debusine.work_request_create(
            work_request_to_post
        )
        self.assertEqual(
            actual_work_request_response, expected_work_request_response
        )

        self.assert_token_key_included_in_all_requests(self.token)

        # The posted (over the wire) WorkRequestRequest does not contain
        # "workspace" if it was None
        self.assertEqual(
            json.loads(responses.calls[0].request.body or "null"),
            work_request_to_post.dict(include=fields_to_post),
        )

    @responses.activate
    def test_work_request_create_success(self) -> None:
        """
        Post a new work request for workspace "Testing".

        The server will assign it to "Testing".
        """
        self.verify_work_request_create_success(workspace="Testing")

    def test_work_request_completed_path(self) -> None:
        """Debusine.work_request_completed_path() return expected path."""
        work_request_id = 67
        self.assertEqual(
            Debusine.work_request_completed_path(work_request_id),
            f"/work-request/{work_request_id}/completed/",
        )

    @responses.activate
    def test_work_request_completed_update(self) -> None:
        """work_request_completed_path() make the expected HTTP request."""
        work_request_id = 68

        responses.add(
            responses.PUT,
            f"{self.api_url}/1.0"
            + self.debusine.work_request_completed_path(work_request_id),
            "",
        )

        self.debusine.work_request_completed_update(
            work_request_id, "success", OutputData()
        )

    @responses.activate
    def test_work_request_create_no_workspace_success(self) -> None:
        """
        Post a new work request without a workspace.

        Assert that the request does not contain "workspace" (not even
        None, as the server expects no workspace field and assigns it to the
        default).
        """
        self.verify_work_request_create_success(workspace=None)

    @responses.activate
    def test_work_request_external_debsign_get(self) -> None:
        """
        Debusine.work_request_external_debsign_get returns the work request.

        It uses a different endpoint from `Debusine.work_request_get` in
        order to ensure that dynamic task data is resolved.
        """
        work_request_id = 10
        work_request_response = create_work_request_response(
            id=work_request_id,
            task_type="Wait",
            task_name="externaldebsign",
            task_data={"unsigned": "lookup"},
            dynamic_task_data={"unsigned_id": 1},
        )

        responses.add(
            responses.GET,
            f'{self.debusine.api_url}/work-request/{work_request_id}/'
            f'external-debsign/',
            json=model_to_json_serializable_dict(work_request_response),
        )

        self.assertEqual(
            self.debusine.work_request_external_debsign_get(work_request_id),
            work_request_response,
        )
        self.assert_token_key_included_in_all_requests('token')

    @responses.activate
    def test_work_request_external_debsign_complete(self) -> None:
        """
        Debusine.work_request_external_debsign_complete sends the request.

        It provides external `debsign` data to the work request.
        """
        work_request_id = 10
        external_debsign_request = WorkRequestExternalDebsignRequest(
            signed_artifact=2
        )
        work_request_response = create_work_request_response(id=work_request_id)

        responses.add(
            responses.POST,
            f'{self.debusine.api_url}/work-request/{work_request_id}/'
            f'external-debsign/',
            json=model_to_json_serializable_dict(work_request_response),
        )

        self.assertEqual(
            self.debusine.work_request_external_debsign_complete(
                work_request_id, external_debsign_request
            ),
            work_request_response,
        )
        self.assert_token_key_included_in_all_requests('token')

    def verify_workflow_template_create_success(
        self, workspace: str | None = None
    ) -> None:
        """
        Verify Debusine.workflow_template_create().

        :param workspace: if it's None: assert that the client's request
          does not contain "workspace" (it's assigned by the server). The
          server response contains workspace="System".

          If it's not None: the request contains "workspace" with the
          correct value.
        """
        # Response always contains "workspace".  It's "system" if it was not
        # in the request.
        expected_workflow_template_response = create_workflow_template_response(
            workspace=(workspace if workspace is not None else "System")
        )

        responses.add(
            responses.POST,
            f'{self.debusine.api_url}/workflow-template/',
            json=model_to_json_serializable_dict(
                expected_workflow_template_response
            ),
            status=requests.codes.created,
        )

        workflow_template_fields: dict[str, Any] = {
            "name": "test",
            "task_name": "noop",
            "task_data": {},
            "workspace": workspace,
            "priority": 0,
        }
        # these fields are always posted
        fields_to_post = {"name", "task_name", "task_data", "priority"}

        # "workspace" is posted only if it's not None
        if workspace is not None:
            fields_to_post.add("workspace")

        # workflow_template_to_post can contain "workspace=None". The method
        # Debusine.workflow_template_create will remove this key if it's
        # None.
        workflow_template_to_post = WorkflowTemplateRequest(
            **workflow_template_fields
        )

        actual_workflow_template_response = (
            self.debusine.workflow_template_create(workflow_template_to_post)
        )
        self.assertEqual(
            actual_workflow_template_response,
            expected_workflow_template_response,
        )

        self.assert_token_key_included_in_all_requests(self.token)

        # The posted (over the wire) WorkflowTemplateRequest does not
        # contain "workspace" if it was None.
        self.assertEqual(
            json.loads(responses.calls[0].request.body or "null"),
            workflow_template_to_post.dict(include=fields_to_post),
        )

    @responses.activate
    def test_workflow_template_create_success(self) -> None:
        """Post a new workflow template."""
        self.verify_workflow_template_create_success(workspace="Testing")

    @responses.activate
    def test_workflow_template_create_no_workspace_success(self) -> None:
        """Post a new workflow template."""
        self.verify_workflow_template_create_success(workspace=None)

    def verify_workflow_create_success(
        self, workspace: str | None = None
    ) -> None:
        """
        Verify Debusine.workflow_create().

        :param workspace: if it's None: assert that the client's request
          does not contain "workspace" (it's assigned by the server). The
          server response contains workspace="System".

          If it's not None: the request contains "workspace" with the
          correct value.
        """
        # Response always contains "workspace".  It's "system" if it was not
        # in the request.
        expected_workflow_response = create_work_request_response(
            workspace=(workspace if workspace is not None else "System")
        )

        responses.add(
            responses.POST,
            f"{self.debusine.api_url}/workflow/",
            json=model_to_json_serializable_dict(expected_workflow_response),
            status=requests.codes.created,
        )

        workflow_fields: dict[str, Any] = {
            "template_name": "test",
            "task_data": {},
            "workspace": workspace,
        }
        # these fields are always posted
        fields_to_post = {"template_name", "task_data"}

        # "workspace" is posted only if it's not None
        if workspace is not None:
            fields_to_post.add("workspace")

        # workflow_to_post can contain "workspace=None". The method
        # Debusine.workflow_create will remove this key if it's None.
        workflow_to_post = CreateWorkflowRequest(**workflow_fields)

        actual_workflow_response = self.debusine.workflow_create(
            workflow_to_post
        )
        self.assertEqual(actual_workflow_response, expected_workflow_response)

        self.assert_token_key_included_in_all_requests(self.token)

        # The posted (over the wire) CreateWorkflowRequest does not contain
        # "workspace" if it was None.
        self.assertEqual(
            json.loads(responses.calls[0].request.body or "null"),
            workflow_to_post.dict(include=fields_to_post),
        )

    @responses.activate
    def test_workflow_create_success(self) -> None:
        """Post a new workflow."""
        self.verify_workflow_create_success(workspace="Testing")

    @responses.activate
    def test_workflow_create_no_workspace_success(self) -> None:
        """Post a new workflow with no workspace."""
        self.verify_workflow_create_success(workspace=None)

    def files_for_artifact(self) -> dict[str, Path]:
        """Return dictionary with files to upload in an artifact."""
        return {
            "README": self.create_temporary_file(),
            ".dirstamp": self.create_temporary_file(),
        }

    @responses.activate
    def test_artifact_get(self) -> None:
        """Test artifact_get() return ArtifactResponse."""
        artifact_id = 2
        workspace = "test"
        download_artifact_tar_gz_url = "https://example.com/path/"
        category = "Testing"
        created_at = datetime.datetime.utcnow()
        data: dict[str, Any] = {}

        get_artifact_response = create_artifact_response(
            id=artifact_id,
            workspace=workspace,
            category=category,
            created_at=created_at,
            data=data,
            download_tar_gz_url=download_artifact_tar_gz_url,
        )

        responses.add(
            responses.GET,
            f"{self.api_url}/1.0/artifact/{get_artifact_response.id}/",
            json=model_to_json_serializable_dict(get_artifact_response),
        )

        artifact = self.debusine.artifact_get(artifact_id)

        self.assert_token_key_included_in_all_requests(self.token)

        self.assertEqual(
            artifact,
            create_artifact_response(
                id=artifact_id,
                category=category,
                data=data,
                created_at=created_at,
                workspace=workspace,
                download_tar_gz_url=download_artifact_tar_gz_url,
            ),
        )

    @responses.activate
    def test_artifact_create_success(self) -> None:
        """Test artifact_create succeeds."""
        files = self.files_for_artifact()

        workspace = "test"
        work_request = 5
        expire_at = datetime.datetime.now() + datetime.timedelta(days=1)

        artifact_response = create_artifact_response(
            id=2,
            workspace=workspace,
            category="Testing",
            created_at=datetime.datetime.utcnow(),
            data={},
            files_to_upload=list(files.keys()),
        )

        responses.add(
            responses.POST,
            f"{self.api_url}/1.0/artifact/",
            json=model_to_json_serializable_dict(artifact_response),
            status=requests.codes.created,
        )

        artifact_to_post = WorkRequestDebugLogs(
            category=ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
            files=files,
            data=EmptyArtifactData(),
        )

        actual_artifact_response = self.debusine.artifact_create(
            artifact_to_post,
            workspace=workspace,
            work_request=work_request,
            expire_at=expire_at,
        )

        self.assertEqual(actual_artifact_response, artifact_response)

        self.assert_token_key_included_in_all_requests(self.token)

        assert responses.calls[0].request.body is not None
        self.assertEqual(
            json.loads(responses.calls[0].request.body),
            artifact_to_post.serialize_for_create_artifact(
                workspace=workspace,
                work_request=work_request,
                expire_at=expire_at,
            ),
        )

    @responses.activate
    def test_upload_files(self) -> None:
        """Client upload files to the server (Debusine.upload_files)."""
        file_relative = self.create_temporary_file(prefix="main.c")

        file_paths = [
            self.create_temporary_file(prefix="README"),
            self.create_temporary_file(prefix="AUTHORS?"),
            file_relative,
        ]

        artifact_id = 2

        urls = []
        for file_path in file_paths:
            file_path_encoded = urllib.parse.quote(file_path.name)
            url = (
                f"{self.api_url}/{Debusine.API_VERSION}/artifact/"
                f"{artifact_id}/files/{file_path_encoded}/"
            )
            responses.add(responses.PUT, url)
            urls.append(url)

        files_to_upload = {}
        for file_path in file_paths:
            files_to_upload[file_path.name] = file_path

        files_to_upload[file_relative.name] = file_relative.relative_to(
            file_relative.parent
        )

        self.debusine.upload_files(
            artifact_id, files_to_upload, file_relative.parent
        )

        for url in urls:
            self.assertTrue(responses.assert_call_count(url, 1))
        self.assertEqual(
            self.logger.info.mock_calls,
            [
                call(
                    "Uploading %s to %s",
                    path,
                    self.debusine._url_for_file_in_artifact(
                        artifact_id, path.name
                    ),
                )
                for path in file_paths
            ],
        )

    def test_upload_files_invalid_base_directory(self) -> None:
        """Base directory is None but there is a relative file path."""
        file_path = "src/main.c"

        files_to_upload = {"some/file/main.c": Path(file_path)}

        artifact_id = 2

        with self.assertRaisesRegex(
            ValueError,
            f"{file_path} is relative: base_directory parameter cannot be None",
        ):
            self.debusine.upload_files(artifact_id, files_to_upload, None)

    @responses.activate
    def test_upload_artifact(self) -> None:
        """upload_artifact() uploads the artifact and associated files."""
        category = ArtifactCategory.WORK_REQUEST_DEBUG_LOGS
        workspace = "the-workspace"
        work_request = 6

        data = EmptyArtifactData()
        filename = "README.txt"
        file_path = self.create_temporary_file()
        files = {
            filename: file_path,
            "already-uploaded-file.txt": self.create_temporary_file(),
        }

        local_artifact = WorkRequestDebugLogs(
            category=category, data=data, files=files
        )

        remote_id = 2

        # Create the artifact request
        responses.add(
            "POST",
            f"{self.debusine.api_url}/artifact/",
            create_artifact_response(
                id=remote_id,
                workspace=workspace,
                files_to_upload=[filename],
            ).json(),
            status=requests.codes.created,
        )

        # Upload the file request
        responses.add(
            "PUT",
            f"{self.debusine.api_url}/artifact/{remote_id}/files/{filename}/",
        )
        remote_artifact = self.debusine.upload_artifact(
            local_artifact, workspace=workspace, work_request=work_request
        )
        self.assertEqual(
            remote_artifact,
            RemoteArtifact(id=remote_id, workspace=workspace),
        )

        self.assertEqual(len(responses.calls), 2)

    @responses.activate
    def test_relation_create(self) -> None:
        """relation_create() makes an HTTP post."""
        artifact_id = 1
        target_id = 2

        url = self.api_url + "/1.0/artifact-relation/"

        response_body = RelationResponse(
            id=7,
            artifact=artifact_id,
            target=target_id,
            type=RelationType.EXTENDS,
        )
        responses.add(
            responses.POST,
            url,
            json=dict(response_body),
        )

        self.debusine.relation_create(
            artifact_id, target_id, RelationType.EXTENDS
        )

        responses.assert_call_count(url, 1)

        assert responses.calls[0].request.body is not None
        request_body = json.loads(responses.calls[0].request.body)

        self.assertEqual(
            request_body,
            RelationCreateRequest(
                artifact=artifact_id,
                target=target_id,
                type=RelationType.EXTENDS,
            ),
        )

    def test_relation_list_no_ids(self) -> None:
        """relation_list() requires an artifact or target ID."""
        with self.assertRaisesRegex(
            ValueError, "Exactly one of artifact_id or target_id must be set"
        ):
            self.debusine.relation_list()

    def test_relation_list_both_ids(self) -> None:
        """relation_list() requires only one of artifact or target ID."""
        with self.assertRaisesRegex(
            ValueError, "Exactly one of artifact_id or target_id must be set"
        ):
            self.debusine.relation_list(artifact_id=1, target_id=2)

    @responses.activate
    def test_relation_list_artifact(self) -> None:
        """relation_list() with an artifact ID makes an HTTP GET."""
        url = self.api_url + "/1.0/artifact-relation/?artifact=1"
        relation_response = RelationResponse(
            id=7, artifact=1, target=2, type=RelationType.EXTENDS
        )
        responses.add(responses.GET, url, json=[dict(relation_response)])

        self.assertEqual(
            self.debusine.relation_list(artifact_id=1),
            RelationsResponse.parse_obj([relation_response]),
        )

        responses.assert_call_count(url, 1)

    @responses.activate
    def test_relation_list_target(self) -> None:
        """relation_list() with a target ID makes an HTTP GET."""
        url = self.api_url + "/1.0/artifact-relation/?target_artifact=2"
        relation_response = RelationResponse(
            id=7, artifact=1, target=2, type=RelationType.EXTENDS
        )
        responses.add(responses.GET, url, json=[dict(relation_response)])

        self.assertEqual(
            self.debusine.relation_list(target_id=2),
            RelationsResponse.parse_obj([relation_response]),
        )

        responses.assert_call_count(url, 1)

    def add_get_download_artifact_response(
        self, files: int = 0, file_size: int = 10240, directories: bool = False
    ) -> ArtifactResponse:
        """
        Call responses.add("GET", "/api/1.0/artifact) with an Artifact.

        Return the ArtifactResponse that has been created.
        """
        artifact_id = 17
        artifact_base_url = (
            f"https://debusine.example.com/artifact/{artifact_id}/"
        )
        url_download_artifact = f"{artifact_base_url}?archive=tar.gz"

        directory = "directory/sub-directory/" if directories else ""

        response_files = {
            f"{directory}file{i}": FileResponse(
                size=file_size,
                checksums={
                    "sha256": pydantic.parse_obj_as(StrMaxLength255, "abc123")
                },
                type="file",
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl, f"{artifact_base_url}{directory}file{i}"
                ),
            )
            for i in range(files)
        }

        # Set endpoint to download artifact's information
        url_artifact_information = self.api_url + f"/1.0/artifact/{artifact_id}"
        artifact_response = create_artifact_response(
            id=artifact_id,
            download_tar_gz_url=url_download_artifact,
            files=response_files,
        )
        responses.add(
            "GET", url_artifact_information, body=artifact_response.json()
        )

        return artifact_response

    @responses.activate
    def test_download_artifact_failed(self) -> None:
        """download_artifact() download an invalid tar file Raise exception."""
        artifact_response = self.add_get_download_artifact_response()
        responses.add(
            responses.GET,
            artifact_response.download_tar_gz_url,
            b"Invalid tar",
        )

        with self.assertRaisesRegex(
            RuntimeError, r"Error untarring https://.*\. Returncode: "
        ):
            self.debusine.download_artifact(
                artifact_response.id,
                self.create_temporary_directory(),
                tarball=False,
            )

    def create_tar(self) -> tuple[io.BytesIO, Path]:
        """
        Create a tar file with a file inside.

        :return: tuple [tarFile, Path of a file]
        """
        compressed = io.BytesIO()
        tar_file = tarfile.open(fileobj=compressed, mode="w|gz")

        file = self.create_temporary_file()
        tar_file.add(file, arcname=file.name)
        tar_file.close()

        compressed.seek(0)

        return compressed, file

    @responses.activate
    def test_download_artifact_untar(self) -> None:
        """download_artifact() download the artifact and untar it."""
        compressed, file = self.create_tar()
        downloaded_directory = self.create_temporary_directory()
        expected_artifact_response = self.add_get_download_artifact_response()
        responses.add(
            responses.GET,
            expected_artifact_response.download_tar_gz_url,
            compressed.getvalue(),
        )

        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            actual_artifact_response = self.debusine.download_artifact(
                expected_artifact_response.id,
                downloaded_directory,
                tarball=False,
            )

        self.assertEqual(
            file.read_bytes(),
            (downloaded_directory / file.name).read_bytes(),
        )

        self.assertEqual(actual_artifact_response, expected_artifact_response)

        self.logger.info.assert_has_calls(
            [
                call(
                    "Downloading artifact and uncompressing into %s",
                    downloaded_directory,
                ),
                call(file.name),
            ]
        )

    @responses.activate
    def test_download_artifact_tarball(self) -> None:
        """download_artifact() download the artifact as .tar.gz."""
        compressed, file = self.create_tar()
        downloaded_directory = self.create_temporary_directory()

        expected_artifact_response = self.add_get_download_artifact_response()
        artifact_id = expected_artifact_response.id
        responses.add(
            responses.GET,
            expected_artifact_response.download_tar_gz_url,
            compressed.getvalue(),
        )

        self.debusine.download_artifact(
            expected_artifact_response.id,
            downloaded_directory,
            tarball=True,
        )

        artifact_file = downloaded_directory / f"artifact-{artifact_id}.tar.gz"

        self.assertEqual(compressed.getvalue(), artifact_file.read_bytes())

        self.logger.info.assert_called_with(
            "Artifact downloaded: %s", artifact_file
        )

    @responses.activate
    def test_download_artifact_single_file_individually(self) -> None:
        """download_artifact() downloads single-file artifacts individually."""
        contents = b"file contents"
        downloaded_directory = self.create_temporary_directory()
        artifact_response = self.add_get_download_artifact_response(files=1)
        destination = downloaded_directory / "file0"

        responses.add(
            responses.GET,
            artifact_response.files["file0"].url,
            contents,
        )

        self.debusine.download_artifact(
            artifact_response.id, downloaded_directory
        )

        self.assertEqual(destination.read_bytes(), contents)
        self.logger.info.assert_called_with(
            "Downloading artifact files into %s", downloaded_directory
        )

    @responses.activate
    def test_download_artifact_small_files_as_tar(self) -> None:
        """download_artifact() downloads many small files as a tar."""
        compressed, file = self.create_tar()
        downloaded_directory = self.create_temporary_directory()
        artifact_response = self.add_get_download_artifact_response(files=3)

        responses.add(
            responses.GET,
            artifact_response.download_tar_gz_url,
            compressed.getvalue(),
        )

        self.debusine.download_artifact(
            artifact_response.id, downloaded_directory
        )

        self.assertEqual(
            file.read_bytes(),
            (downloaded_directory / file.name).read_bytes(),
        )

    @responses.activate
    def test_download_artifact_single_file_with_parent_directory(self) -> None:
        """download_artifact() handles directories in single-file downloads."""
        contents = b"file contents"
        downloaded_directory = self.create_temporary_directory()
        artifact_response = self.add_get_download_artifact_response(
            files=1, directories=True
        )
        destination = downloaded_directory / "directory/sub-directory/file0"

        responses.add(
            responses.GET,
            artifact_response.files["directory/sub-directory/file0"].url,
            contents,
        )

        self.debusine.download_artifact(
            artifact_response.id, downloaded_directory
        )

        self.assertEqual(destination.read_bytes(), contents)
        self.logger.info.assert_called_with(
            "Downloading artifact files into %s", downloaded_directory
        )

    def test_should_download_as_tarball_single_file(self) -> None:
        """_should_download_as_tarball() single file artifact."""
        artifact_response = self.add_get_download_artifact_response(files=1)
        self.assertFalse(
            self.debusine._should_download_as_tarball(artifact_response)
        )

    def test_should_download_as_tarball_small_files(self) -> None:
        """When artifact contains only small files, download tar."""
        artifact_response = self.add_get_download_artifact_response(files=3)
        self.assertTrue(
            self.debusine._should_download_as_tarball(artifact_response)
        )

    def test_should_download_as_tarball_large_files(self) -> None:
        """When artifact contains a large file, download separately."""
        artifact_response = self.add_get_download_artifact_response(files=2)
        artifact_response.files["file1"].size = 30 * 1024 * 1024
        self.assertFalse(
            self.debusine._should_download_as_tarball(artifact_response)
        )

    def test_should_download_as_tarball_large_overall_size(self) -> None:
        """When artifact would be a large tar, download separately."""
        artifact_response = self.add_get_download_artifact_response(
            files=20, file_size=5 * 1024 * 1024
        )
        self.assertFalse(
            self.debusine._should_download_as_tarball(artifact_response)
        )

    def add_get_download_artifact_file_response(
        self, contents: bytes
    ) -> ArtifactResponse:
        """
        Call responses.add("GET", "/api/1.0/artifact) with an Artifact.

        Return the ArtifactResponse that has been created.
        """
        artifact_id = 17
        url_system_tar_xz = (
            f"https://debusine.example.com/"
            f"artifact/{artifact_id}/system.tar.xz"
        )

        # Set endpoint to download artifact's information
        url_artifact_information = self.api_url + f"/1.0/artifact/{artifact_id}"
        artifact_response = create_artifact_response(
            id=artifact_id,
            files=FilesResponseType(
                {
                    "system.tar.xz": FileResponse(
                        size=len(contents),
                        checksums={
                            "sha256": pydantic.parse_obj_as(
                                StrMaxLength255,
                                hashlib.sha256(contents).hexdigest(),
                            )
                        },
                        type="file",
                        url=pydantic.parse_obj_as(
                            pydantic.AnyUrl, url_system_tar_xz
                        ),
                    )
                }
            ),
        )
        responses.add(
            "GET", url_artifact_information, body=artifact_response.json()
        )

        return artifact_response

    @responses.activate
    def test_download_artifact_file_missing_path(self) -> None:
        """download_artifact_file() requires path_in_artifact to exist."""
        artifact_response = self.add_get_download_artifact_file_response(b"")
        with self.assertRaisesRegex(
            ValueError,
            f"No file 'nonexistent' in artifact {artifact_response.id}",
        ):
            self.debusine.download_artifact_file(
                artifact_response.id,
                "nonexistent",
                self.create_temporary_directory() / "nonexistent",
            )

    @responses.activate
    def test_download_artifact_file(self) -> None:
        """download_artifact_file() downloads the requested file."""
        contents = b"File contents"
        downloaded_directory = self.create_temporary_directory()
        artifact_response = self.add_get_download_artifact_file_response(
            contents
        )
        responses.add(
            responses.GET,
            artifact_response.files["system.tar.xz"].url,
            contents,
        )
        destination = downloaded_directory / "system.tar.xz"

        self.debusine.download_artifact_file(
            artifact_response.id, "system.tar.xz", destination
        )

        self.assertEqual(destination.read_bytes(), contents)
        self.logger.info.assert_called_with(
            "Artifact file downloaded: %s", destination
        )

    @responses.activate
    def test_download_artifact_file_with_artifact_response(self) -> None:
        """download_artifact_file() accepts an existing ArtifactResponse."""
        contents = b"File contents"
        downloaded_directory = self.create_temporary_directory()
        artifact_response = self.add_get_download_artifact_file_response(
            contents
        )
        responses.add(
            responses.GET,
            artifact_response.files["system.tar.xz"].url,
            contents,
        )
        destination = downloaded_directory / "system.tar.xz"

        self.debusine.download_artifact_file(
            artifact_response, "system.tar.xz", destination
        )

        self.assertEqual(destination.read_bytes(), contents)
        self.logger.info.assert_called_with(
            "Artifact file downloaded: %s", destination
        )

    @responses.activate
    def test_asset_create_success(self) -> None:
        """Test asset_create succeeds."""
        workspace = "test"
        work_request = 5

        asset_data = SigningKeyData(
            purpose=KeyPurpose.OPENPGP,
            fingerprint="ABC123",
            public_key="PUBLIC KEY",
            description="Description",
        )
        asset_request = {
            "category": AssetCategory.SIGNING_KEY,
            "workspace": workspace,
            "data": asset_data.dict(),
            "work_request": work_request,
        }
        asset_response_json = {
            "id": 99,
            "category": AssetCategory.SIGNING_KEY,
            "workspace": workspace,
            "data": asset_data.dict(),
            "work_request": work_request,
        }
        asset_response = AssetResponse(
            id=99,
            category=AssetCategory.SIGNING_KEY,
            workspace=workspace,
            data=asset_data,
            work_request=work_request,
        )

        responses.add(
            responses.POST,
            f"{self.api_url}/1.0/asset/",
            json=asset_response_json,
            status=requests.codes.created,
        )

        actual_asset_response = self.debusine.asset_create(
            category=AssetCategory.SIGNING_KEY,
            workspace=workspace,
            data=asset_data,
            work_request=work_request,
        )

        self.assertEqual(actual_asset_response, asset_response)

        self.assert_token_key_included_in_all_requests(self.token)

        assert responses.calls[0].request.body is not None
        self.assertEqual(
            json.loads(responses.calls[0].request.body), asset_request
        )

    def assert_list_assets(
        self,
        url: str,
        asset_id: int | None = None,
        work_request: int | None = None,
        workspace: str | None = None,
    ) -> None:
        """Call client.asset_list with the specified options, assert results."""
        asset_data = SigningKeyData(
            purpose=KeyPurpose.OPENPGP,
            fingerprint="ABC123",
            public_key="PUBLIC KEY",
            description="Description",
        )
        assets_response = AssetsResponse.parse_obj(
            [
                AssetResponse(
                    id=99,
                    category=AssetCategory.SIGNING_KEY,
                    workspace="workspace",
                    data=asset_data,
                    work_request=12,
                ).dict()
            ]
        )
        assets_response_json = [
            {
                "id": 99,
                "category": AssetCategory.SIGNING_KEY,
                "workspace": "workspace",
                "data": asset_data.dict(),
                "work_request": 12,
            }
        ]
        responses.add(
            responses.GET,
            url,
            json=assets_response_json,
            status=requests.codes.ok,
        )

        actual_asset_response = self.debusine.asset_list(
            asset_id=asset_id, workspace=workspace, work_request=work_request
        )

        self.assertEqual(actual_asset_response, assets_response)

        self.assert_token_key_included_in_all_requests(self.token)

    @responses.activate
    def test_asset_list_by_id(self) -> None:
        """Test asset_list by asset_id."""
        self.assert_list_assets(
            url=f"{self.api_url}/1.0/asset/?asset=99", asset_id=99
        )

    @responses.activate
    def test_asset_list_by_workspace(self) -> None:
        """Test asset_list by workspace."""
        self.assert_list_assets(
            url=f"{self.api_url}/1.0/asset/?workspace=workspace",
            workspace="workspace",
        )

    @responses.activate
    def test_asset_list_by_work_request(self) -> None:
        """Test asset_list by work_request_id."""
        self.assert_list_assets(
            url=f"{self.api_url}/1.0/asset/?work_request=12",
            work_request=12,
        )

    @responses.activate
    def test_asset_list_without_filter(self) -> None:
        """Test asset_list without a filter."""
        with self.assertRaisesRegex(
            ValueError,
            r"At least one of asset, work_request, and workspace must be "
            r"specified",
        ):
            self.debusine.asset_list()

    @responses.activate
    def test_asset_permission_check(self) -> None:
        permission_check_response = AssetPermissionCheckResponse(
            has_permission=True,
            username="testuser",
            user_id=123,
            resource={"package": "foo"},
        )
        responses.add(
            responses.POST,
            (
                f"{self.debusine.api_url}/asset/debusine:signing-key/"
                f"openpgp:ABC123/sign_with/"
            ),
            json=permission_check_response.dict(),
        )
        response = self.debusine.asset_permission_check(
            asset_category="debusine:signing-key",
            asset_slug="openpgp:ABC123",
            permission_name="sign_with",
            artifact_id=12,
            work_request_id=13,
            workspace="workspace",
        )
        self.assertEqual(response, permission_check_response)
        self.assert_token_key_included_in_all_requests(self.token)

    @responses.activate
    def test_lookup_single(self) -> None:
        """lookup_single() performs the requested lookup."""
        responses.add(
            responses.POST,
            f"{self.debusine.api_url}/lookup/single/",
            json={
                "result_type": LookupResultType.ARTIFACT,
                "collection_item": None,
                "artifact": 1,
                "collection": None,
            },
        )

        response = self.debusine.lookup_single(
            lookup="collection/artifact",
            work_request=1,
            expect_type=LookupChildType.ARTIFACT,
            default_category=CollectionCategory.TEST,
        )

        self.assertEqual(
            response,
            LookupSingleResponse(
                result_type=LookupResultType.ARTIFACT, artifact=1
            ),
        )
        assert responses.calls[0].request.body is not None
        self.assertEqual(
            json.loads(responses.calls[0].request.body),
            {
                "lookup": "collection/artifact",
                "work_request": 1,
                "default_category": CollectionCategory.TEST,
                "expect_type": LookupChildType.ARTIFACT,
            },
        )

    @responses.activate
    def test_lookup_multiple(self) -> None:
        """lookup_multiple() performs the requested lookup."""
        responses.add(
            responses.POST,
            f"{self.debusine.api_url}/lookup/multiple/",
            json=[
                {
                    "result_type": LookupResultType.ARTIFACT,
                    "collection_item": artifact_id,
                    "artifact": artifact_id,
                    "collection": None,
                }
                for artifact_id in (1, 2)
            ],
        )

        response = self.debusine.lookup_multiple(
            lookup=LookupMultiple.parse_obj({"collection": "collection"}),
            work_request=1,
            expect_type=LookupChildType.ARTIFACT,
            default_category=CollectionCategory.TEST,
        )

        self.assertEqual(
            response,
            LookupMultipleResponse.parse_obj(
                [
                    LookupSingleResponse(
                        result_type=LookupResultType.ARTIFACT,
                        collection_item=artifact_id,
                        artifact=artifact_id,
                    )
                    for artifact_id in (1, 2)
                ]
            ),
        )
        assert responses.calls[0].request.body is not None
        self.assertEqual(
            json.loads(responses.calls[0].request.body),
            {
                # The lookup is normalized.
                "lookup": [
                    {
                        "category": None,
                        "child_type": "artifact",
                        "collection": "collection",
                        "data_matchers": [],
                        "name_matcher": None,
                        "lookup_filters": [],
                    }
                ],
                "work_request": 1,
                "default_category": CollectionCategory.TEST,
                "expect_type": LookupChildType.ARTIFACT,
            },
        )

    @responses.activate
    def test_streaming_download(self) -> None:
        """Test _streaming_download() sets headers and returns the response."""
        url = "https://example.net/stream"
        body = b"abc123"
        responses.add(responses.GET, url, body)
        r = self.debusine._streaming_download(url)

        self.assertEqual(r.content, body)
        self.assertEqual(r.request.headers["Token"], self.debusine.token)


class DebusineAsyncTests(server.DebusineAioHTTPTestCase, TestCase):
    """
    Tests for Debusine client asynchronous functionality.

    Use server.DebusineAioHTTPTestCase to mock the server (instead of the
    responses library used by DebusineTests).
    """

    async def setUpAsync(self) -> None:
        """Set up tests."""
        await super().setUpAsync()

        self.api_url = str(self.server.make_url("/api"))

        self.token = "token"
        self.logger = mock.create_autospec(spec=logging.Logger)
        self.debusine = Debusine(self.api_url, self.token, logger=self.logger)

    def on_complete_url(self, *, params: dict[str, str] | None = None) -> str:
        """Return the WebSocket on-completed-url."""
        url = self.api_url + "/ws/1.0/work-request/on-completed/"
        url = url.replace("http", "ws", 1)

        if params is not None:
            url += "?" + urlencode(params)

        return url

    def script_write_parameters_to_file(self) -> tuple[Path, Path]:
        """
        Create a script that save its parameters into a file.

        :return: tuple with Path to the script and Path to the output file.

        """
        output_file = self.create_temporary_file()

        command = self.create_temporary_file(
            contents=textwrap.dedent(
                f"""\
            #!/bin/bash
            echo $* >> {str(output_file)}
            """
            ).encode("utf-8")
        )

        os.chmod(command, stat.S_IXUSR | stat.S_IRUSR)

        return command, output_file

    async def test_wait_and_execute_command(self) -> None:
        """A WorkRequest finished and command is executed."""
        await self.execute_wait_and_execute(
            command_modifier=lambda cmd: cmd,
            working_directory=lambda _: Path.cwd(),
            on_completed_work_requests=1,
        )
        self.assertEqual(self.requests[-1].headers["Token"], self.token)

    async def test_wait_and_execute_two_commands(self) -> None:
        """Two WorkRequests finished: two commands are executed."""
        await self.execute_wait_and_execute(
            command_modifier=lambda cmd: cmd,
            working_directory=lambda _: Path.cwd(),
            on_completed_work_requests=2,
        )

    async def test_wait_and_execute_command_relative_to_working_directory(
        self,
    ) -> None:
        """A WorkRequest finished and command is executed."""
        await self.execute_wait_and_execute(
            command_modifier=lambda cmd: f"./{cmd.name}",
            working_directory=lambda cmd: cmd.parent,
            on_completed_work_requests=1,
        )

    async def execute_wait_and_execute(
        self,
        command_modifier: Callable[[Path], str | os.PathLike[str]],
        working_directory: Callable[[Path], Path],
        on_completed_work_requests: int,
    ) -> None:
        """
        Test self.debusine._wait_and_execute().

        Create a script that use as a command. Execute and assert that
        it was executed.

        :param command_modifier: how to modify the Path to the script before
          executing it (e.g. to convert it to a relative path)
        :param working_directory: which working directory to use (e.g.
          to use the script's working directory or Path.cwd().
        :param on_completed_work_requests: number of on completed work requests
          expected. The IDs will increment by one on each finished work request.
        """
        url = self.on_complete_url()
        command, output_file = self.script_write_parameters_to_file()

        modified_command = command_modifier(command)

        self.ON_COMPLETED_WORK_REQUESTS = on_completed_work_requests

        await self.debusine._wait_and_execute(
            url=url,
            last_completed_at=None,
            command=modified_command,
            working_directory=working_directory(command),
        )

        expected_in_file = ""
        expected_log_executing_calls = []

        for number in range(0, on_completed_work_requests):
            expected_in_file += (
                f"{server.DebusineAioHTTPTestCase.WORK_REQUEST_ID + number} "
                f"{server.DebusineAioHTTPTestCase.WORK_REQUEST_RESULT}\n"
            )

            cmd = [
                str(modified_command),
                str(server.DebusineAioHTTPTestCase.WORK_REQUEST_ID + number),
                str(server.DebusineAioHTTPTestCase.WORK_REQUEST_RESULT),
            ]

            expected_log_executing_calls.append(call("Executing %s", cmd))

        self.assertEqual(output_file.read_text(), expected_in_file)

        self.logger.info.assert_has_calls(
            [
                call("Requesting %s", url),
                call("Connected!"),
                *expected_log_executing_calls,
            ],
        )

    async def test_wait_and_execute_command_write_last_completed_file(
        self,
    ) -> None:
        """wait_and_execute_command update last_completed_at file."""
        last_completed_at = self.create_temporary_file()

        command, _ = self.script_write_parameters_to_file()

        url = self.on_complete_url()

        await self.debusine._wait_and_execute(
            url=url,
            last_completed_at=last_completed_at,
            command=str(command),
            working_directory=Path.cwd(),
        )

        completed_at = DebusineAioHTTPTestCase.COMPLETED_AT.isoformat()
        expected = (
            json.dumps({"last_completed_at": completed_at}, indent=2) + "\n"
        )

        self.assertEqual(last_completed_at.read_text(), expected)

    def patch_tenacity_sleep(self, attempts: int = 1) -> MagicMock:
        """Patch self.debusine._tenacity_sleep and return its mock."""
        patcher = mock.patch.object(
            self.debusine, "_tenacity_sleep", autospec=True
        )
        mocked = patcher.start()
        mocked.side_effect = [None] * attempts
        self.addCleanup(patcher.stop)

        return mocked

    async def test_wait_and_execute_command_retry(self) -> None:
        """Client get disconnected: try again."""
        attempts = 2
        tenacity_sleep_mocked = self.patch_tenacity_sleep(attempts)

        exc_message = "SSL error"

        exc = aiohttp.client_exceptions.ClientError(exc_message)

        # Mock ws_connect to raise the ClientError exception
        class MockWebSocketResponse:
            async def __aenter__(self) -> NoReturn:
                raise exc

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: types.TracebackType | None,
            ) -> None:
                pass  # pragma: no cover

        def mock_ws_connect(*args: Any, **kwargs: Any) -> MockWebSocketResponse:
            return MockWebSocketResponse()

        patcher = mock.patch.object(
            aiohttp.ClientSession, 'ws_connect', new=mock_ws_connect
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        with self.assertRaises(StopAsyncIteration):
            await self.debusine._wait_and_execute(
                url=self.on_complete_url(),
                command="/bin/echo",
                working_directory=Path.cwd(),
                last_completed_at=None,
            )

        # Called three times: twice returned None and once StopAsyncIteration
        self.assertEqual(tenacity_sleep_mocked.call_count, attempts + 1)

        self.logger.error.assert_has_calls([call("  Error: %s", exc)] * 3)

    async def test_wait_and_execute_command_fail(self) -> None:
        """Command cannot be executed: raise FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            await self.debusine._wait_and_execute(
                url=self.on_complete_url(),
                command="file-does-not-exist",
                working_directory=Path.cwd(),
                last_completed_at=None,
            )

    async def test_wait_and_execute_no_token(self) -> None:
        """_wait_and_execute works even if it has no token."""
        self.debusine = Debusine(self.api_url, None, logger=self.logger)
        await self.execute_wait_and_execute(
            command_modifier=lambda cmd: cmd,
            working_directory=lambda _: Path.cwd(),
            on_completed_work_requests=1,
        )
        self.assertNotIn("Token", self.requests[-1].headers)

    async def test_tenacity_sleep(self) -> None:
        """Debusine._tenacity_sleep call asyncio.sleep."""
        patched = mock.patch("asyncio.sleep", autospec=True)
        mocked = patched.start()
        self.addCleanup(patched.stop)

        delay = 1.5
        await self.debusine._tenacity_sleep(delay)

        mocked.assert_called_with(delay)

    def patch_wait_and_execute(self) -> MagicMock:
        """Patch self.debusine._wait_and_execute, return its mock."""
        patcher = mock.patch.object(
            self.debusine, "_wait_and_execute", return_value=None
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked

    def test_on_work_request_completed(self) -> None:
        """on_work_request_completed call _wait_and_executed. No workspaces."""
        command = "this-is-the-command"

        wait_and_executed_mocked = self.patch_wait_and_execute()

        self.debusine.on_work_request_completed(
            workspaces=None,
            command=command,
            working_directory=Path.cwd(),
            last_completed_at=None,
        )

        url = self.on_complete_url()

        wait_and_executed_mocked.assert_awaited_with(
            url=url,
            command=command,
            working_directory=Path.cwd(),
            last_completed_at=None,
        )

    def test_on_work_request_completed_with_workspaces(self) -> None:
        """on_work_request_completed call _wait_and_execute with workspaces."""
        workspace = "lts"
        command = "this-is-the-command"

        wait_and_executed_mocked = self.patch_wait_and_execute()

        self.debusine.on_work_request_completed(
            workspaces=[workspace],
            last_completed_at=None,
            command=command,
            working_directory=Path.cwd(),
        )

        url = self.on_complete_url(params={"workspaces": workspace})

        wait_and_executed_mocked.assert_awaited_with(
            url=url,
            command=command,
            working_directory=Path.cwd(),
            last_completed_at=None,
        )

    def test_on_work_request_completed_last_completed_at(self) -> None:
        """
        on_work_request_completed call _wait_and_execute with last_completed.

        The file in last_completed_at existed and its contents is read.
        """
        command = "this-is-the-command"
        last_completed_at = datetime.datetime.utcnow().isoformat()

        last_completed_at_file = self.create_temporary_file(
            contents=json.dumps(
                {"last_completed_at": last_completed_at}
            ).encode("utf-8")
        )

        wait_and_executed_mocked = self.patch_wait_and_execute()

        self.debusine.on_work_request_completed(
            last_completed_at=last_completed_at_file,
            command=command,
            working_directory=Path.cwd(),
        )

        url = self.on_complete_url(
            params={"completed_at_since": last_completed_at}
        )

        wait_and_executed_mocked.assert_called_with(
            url=url,
            command=command,
            working_directory=Path.cwd(),
            last_completed_at=last_completed_at_file,
        )

    def test_on_work_request_completed_last_completed_at_does_not_exist(
        self,
    ) -> None:
        """
        on_work_request_completed call _wait_and_execute with last_completed.

        The file in last_completed_at did not exist.
        """
        last_completed_at_file = self.create_temporary_file()
        last_completed_at_file.unlink()

        command = "this-is-the-command"

        wait_and_executed_mocked = self.patch_wait_and_execute()

        self.debusine.on_work_request_completed(
            last_completed_at=last_completed_at_file,
            command=command,
            working_directory=Path.cwd(),
        )

        url = self.on_complete_url()

        wait_and_executed_mocked.assert_called_with(
            url=url,
            command=command,
            working_directory=Path.cwd(),
            last_completed_at=last_completed_at_file,
        )
