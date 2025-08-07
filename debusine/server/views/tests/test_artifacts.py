# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the artifact views."""

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock
from unittest.mock import MagicMock

from django.conf import settings
from django.http.response import HttpResponseBase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from debusine.artifacts.models import ArtifactCategory
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    File,
    FileInArtifact,
    FileInStore,
    FileStore,
    FileUpload,
    Token,
    Workspace,
)
from debusine.db.playground import scenarios
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.scopes import urlconf_scope
from debusine.server.serializers import ArtifactSerializerResponse
from debusine.server.views.artifacts import UploadFileView
from debusine.test.django import (
    JSONResponseProtocol,
    TestCase,
    TestResponseType,
)
from debusine.test.test_utils import data_generator


class ArtifactViewTests(TestCase):
    """Tests for ArtifactView."""

    scenario = scenarios.DefaultContextAPI()
    playground_memory_file_store = False

    default_workspace_name: ClassVar[str]
    workspace: ClassVar[Workspace]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.default_workspace_name = "test"
        cls.workspace = cls.playground.create_workspace(
            name=cls.default_workspace_name, public=True
        )
        cls.playground.create_group_role(
            cls.workspace, Workspace.Roles.OWNER, users=[cls.scenario.user]
        )

    @staticmethod
    def empty_store_directory() -> None:
        """Empty settings.DEBUSINE_STORE_DIRECTORY."""
        shutil.rmtree(settings.DEBUSINE_STORE_DIRECTORY, ignore_errors=True)
        Path(settings.DEBUSINE_STORE_DIRECTORY).mkdir()

    @staticmethod
    def create_file_object(hash_digest: bytes, size: int) -> File:
        """Create new file with hash_digest and size."""
        fileobj = File()
        fileobj.hash_digest = hash_digest
        fileobj.size = size
        fileobj.save()

        store = FileStore.default()

        FileInStore.objects.create(file=fileobj, store=store, data={})

        return fileobj

    def post_artifact_create(
        self,
        *,
        token_key: str,
        data: dict[str, Any],
        hostname: str | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """
        Call self.client.post() to api-artifact-create.

        Pass the token.key and content_type="application/json".

        :param token_key: value of the Token header for the request
        :param data: data to post
        :param hostname: if not None, use it for HTTP_HOST
        """
        headers: dict[str, str] = {}
        if hostname is not None:
            headers["Host"] = hostname
        if scope is not None:
            headers["X-Debusine-Scope"] = scope

        return self.client.post(
            reverse("api:artifact-create"),
            data=data,
            HTTP_TOKEN=token_key,
            content_type="application/json",
            headers=headers,
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact(self) -> None:
        """An artifact is created."""
        # file_already_uploaded_* is in the File and Store tables. The file
        # was already created and uploaded

        # Test will ensure that the empty file is created in the store.
        # Empty the store directory in case that the file existed from
        # a previous test
        self.empty_store_directory()

        file_already_uploaded_path = "AUTHORS"
        file_already_uploaded_contents = b"contents of AUTHORS"
        self.create_file_object(
            _calculate_hash_from_data(file_already_uploaded_contents),
            len(file_already_uploaded_contents),
        )

        # file_exists_not_uploaded_* exists in the File table but is not in
        # the Store. The client should upload it (we do not know if another
        # client would upload it or not)
        file_exists_not_uploaded_path = "src/dd.c"
        file_exists_not_uploaded_contents = b"contents of src/dd.c"
        self.playground.create_file(file_exists_not_uploaded_contents)

        path_in_artifact_empty_file = "src/.dirstamp"
        category = "testing"
        artifact_serialized = {
            "workspace": self.default_workspace_name,
            "category": category,
            "files": {
                file_already_uploaded_path: {
                    "type": "file",
                    "size": len(file_already_uploaded_contents),
                    "checksums": {
                        "sha256": hashlib.sha256(
                            file_already_uploaded_contents
                        ).hexdigest(),
                        "md5": hashlib.md5(
                            file_already_uploaded_contents
                        ).hexdigest(),
                    },
                },
                file_exists_not_uploaded_path: {
                    "type": "file",
                    "size": len(file_exists_not_uploaded_contents),
                    "checksums": {
                        "sha256": hashlib.sha256(
                            file_exists_not_uploaded_contents
                        ).hexdigest(),
                        "md5": hashlib.md5(
                            file_exists_not_uploaded_contents
                        ).hexdigest(),
                    },
                },
                "src/seq.c": {
                    "type": "file",
                    "size": 21750,
                    "checksums": {
                        "sha256": (
                            "acd6454390756911a464b426dcc49027055c"
                            "ddf9258112dfd7a52ee96e231352"
                        ),
                        "md5": "e13c3d5d2489a7ef6b697379f74c2fd4",
                    },
                },
                path_in_artifact_empty_file: {
                    "type": "file",
                    "size": 0,
                    "checksums": {
                        "sha256": hashlib.sha256().hexdigest(),
                        "md5": hashlib.md5().hexdigest(),
                    },
                },
            },
            "data": {"key1": "value1"},
        }
        self.assertEqual(Artifact.objects.all().count(), 0)

        hostname = "example.com"

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname=hostname,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response_json = response.json()
        artifact = Artifact.objects.get(id=response_json["id"])

        # Assert created artifact is as expected
        self.assertEqual(artifact.category, artifact_serialized["category"])
        self.assertEqual(
            artifact.workspace.name, artifact_serialized["workspace"]
        )
        self.assertEqual(artifact.data, artifact_serialized["data"])
        self.assertIsNone(artifact.created_by_work_request)

        # expected_files_to_upload:
        # "AUTHORS" file was already available in the debusine server, it does
        #   not need to be uploaded again.
        # "src/dd.c" file existed in the File table but not in FileInStorage.
        #   It might be uploaded by another client or not: so debusine
        #   server requests this client to upload it.
        # "src/seq.c" is a 100% new file in debusine.
        # "src/.dirstamp" is an empty file: debusine does not request
        #   to be uploaded again.

        self.assertEqual(
            response_json,
            self.artifact_as_dict_response(artifact, hostname),
        )

        self.assertEqual(
            artifact.files.all().count(), len(artifact_serialized["files"])
        )

        file_backend = FileStore.default().get_backend_object()

        # Assert that files have the correct size and hash_digest
        assert isinstance(artifact_serialized["files"], dict)
        for path, file_data in artifact_serialized["files"].items():
            file_in_artifact = FileInArtifact.objects.get(
                artifact=artifact, path=path
            )
            size = file_in_artifact.file.size
            self.assertEqual(size, file_data["size"])
            self.assertEqual(
                file_in_artifact.file.hash_digest.hex(),
                file_data["checksums"]["sha256"],
            )

            local_path = file_backend.get_local_path(file_in_artifact.file)
            assert local_path is not None
            if size == 0:
                # Empty files are created, and the FileInArtifact is complete
                empty_file_stat = local_path.stat()
                self.assertEqual(empty_file_stat.st_size, 0)
                self.assertTrue(file_in_artifact.complete)
            else:
                # Non-empty files are not created yet, and the
                # FileInArtifact is incomplete
                self.assertFalse(local_path.exists())
                self.assertFalse(file_in_artifact.complete)

        # Assert that ArtifactView.post() created an empty file for the
        # empty file. Other files would be created when the client makes
        # the PUT request
        file_upload = FileUpload.objects.get(
            file_in_artifact__path=path_in_artifact_empty_file
        )
        path_empty_file = Path(file_upload.absolute_file_path())
        self.assertTrue(path_empty_file.is_file())
        self.assertEqual(path_empty_file.stat().st_size, 0)

        with self.captureOnCommitCallbacks(execute=True):
            # Delete FileUpload to delete the associated file in
            # DEBUSINE_UPLOAD_DIRECTORY. Only for the empty file because
            # ArtifactView.post() created the file only for the empty one
            file_upload.delete()

    @staticmethod
    def artifact_as_dict_response(
        artifact: Artifact, hostname: str
    ) -> dict[str, Any]:
        """Return dict: server return to the client."""
        path = artifact.get_absolute_url_download()

        with mock.patch.object(
            ArtifactSerializerResponse,
            "_build_absolute_download_url",
            autospec=True,
            return_value=f"http://{hostname}{path}",
        ):
            unused_request = MagicMock()
            serialized = ArtifactSerializerResponse.from_artifact(
                artifact, unused_request
            ).data
            serialized["download_tar_gz_url"] = (
                f"http://{hostname}{path}?archive=tar.gz"
            )
            return serialized

    def artifact_as_dict_request(self, work_request_id: int) -> dict[str, Any]:
        """Return dict: client post to the server to create an artifact."""
        return {
            "workspace": self.default_workspace_name,
            "category": "testing",
            "files": {},
            "work_request": work_request_id,
            "data": {},
        }

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True, ALLOWED_HOSTS=["*"])
    def test_create_artifact_created_by_work_request(self) -> None:
        """Request include a work request id. Artifact get created with it."""
        work_request = self.playground.create_work_request()
        token = self.playground.create_worker_token()

        response = self.post_artifact_create(
            token_key=token.key,
            data=self.artifact_as_dict_request(work_request.id),
            hostname="example.com",
        )

        artifact = Artifact.objects.get(id=response.json()["id"])
        self.assertEqual(artifact.created_by_work_request, work_request)

    def test_create_artifact_created_by_work_request_no_permissions(
        self,
    ) -> None:
        """
        Request is client authenticated (not worker). Invalid permission.

        Only workers can set the Artifact.created_by_work_request().
        """
        work_request = self.playground.create_work_request()

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=self.artifact_as_dict_request(work_request.id),
        )

        self.assertResponseProblem(
            response,
            "work_request field can only be used by a request "
            "with a token from a Worker",
            status_code=status.HTTP_403_FORBIDDEN,
        )

        # No artifact is created
        self.assertEqual(Artifact.objects.count(), 0)

    def test_create_artifact_created_by_work_request_not_found(self) -> None:
        """
        Request include a non-existing work request id.

        Assert that the view return HTTP 400 error.
        """
        # Associated a Worker to the token
        work_request_id = -1

        response = self.post_artifact_create(
            token_key=self.playground.create_worker_token().key,
            data=self.artifact_as_dict_request(work_request_id),
        )
        self.assertResponseProblem(
            response, f"WorkRequest {work_request_id} does not exist"
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_no_work_request(self) -> None:
        """Assert artifact is created without a work request relation."""
        artifact_serialized = {
            "workspace": self.default_workspace_name,
            "category": "testing",
            "files": {},
            "data": {},
        }

        token = self.scenario.user_token

        response = self.post_artifact_create(
            token_key=token.key,
            data=artifact_serialized,
            hostname="example.com",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        assert isinstance(response, Response)
        artifact = Artifact.objects.get(id=response.data["id"])
        self.assertIsNone(artifact.created_by_work_request)
        self.assertEqual(artifact.created_by, token.user)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_fails_no_work_request_id_no_user_token(
        self,
    ) -> None:
        """
        Artifact cannot be created.

        The data does not contain a work_request_id and the token is not
        associated to a user.
        """
        artifact_serialized = {
            "workspace": self.default_workspace_name,
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.playground.create_bare_token().key,
            data=artifact_serialized,
            hostname="example.com",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.assertEqual(
            response.json(),
            {
                "title": "AnonymousUser cannot create artifacts"
                " in debusine/test",
            },
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_fails_no_work_request_id_and_worker_token(
        self,
    ) -> None:
        """
        Artifact cannot be created.

        The data does not contain a work_request_id but a worker token is used.
        """
        artifact_serialized = {
            "workspace": self.default_workspace_name,
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.playground.create_worker_token().key,
            data=artifact_serialized,
            hostname="example.com",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.assertEqual(
            response.json(),
            {
                'title': "If work_request field is empty the WorkRequest"
                " must be created using a Token associated to a user"
            },
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_no_workspace(self) -> None:
        """Assert artifact is serialized in the default workspace."""
        artifact_serialized = {
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assert isinstance(response, Response)
        artifact = Artifact.objects.get(id=response.data["id"])
        self.assertEqual(
            artifact.workspace.name, settings.DEBUSINE_DEFAULT_WORKSPACE
        )

    def test_create_artifact_when_workspace_does_not_exist(self) -> None:
        """No artifact is created: workspace not found."""
        workspace_name = "does-not-exist"

        artifact_serialized = {
            "workspace": "does-not-exist",
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
        )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern=f"Workspace {workspace_name} not found"
            " in scope debusine",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_create_artifact_deserialize_error(self) -> None:
        """Send invalid artifact (missing fields)."""
        artifact_serialized: dict[str, Any] = {}

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
        )

        self.assertResponseProblem(
            response,
            "Cannot deserialize artifact",
            validation_errors_pattern="This field is required",
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_do_not_reuse_invisible_files(self) -> None:
        """Files not in this workspace must be reuploaded."""
        with context.disable_permission_checks():
            other_workspace = self.playground.create_workspace(name="other")
            other_artifact, other_files_contents = (
                self.playground.create_artifact(
                    paths=["foo"], workspace=other_workspace, create_files=True
                )
            )
        artifact_serialized = {
            "workspace": self.default_workspace_name,
            "category": ArtifactCategory.TEST,
            "files": {
                "foo": {
                    "type": "file",
                    "size": len(other_files_contents["foo"]),
                    "checksums": {
                        "sha256": hashlib.sha256(
                            other_files_contents["foo"]
                        ).hexdigest()
                    },
                }
            },
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response_json = response.json()
        self.assertEqual(response_json["files_to_upload"], ["foo"])
        file_in_artifact = FileInArtifact.objects.get(
            artifact=response_json["id"], path="foo"
        )
        self.assertFalse(file_in_artifact.complete)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_reuse_visible_files(self) -> None:
        """Files in this workspace do not need to be reuploaded."""
        with context.disable_permission_checks():
            artifact, files_contents = self.playground.create_artifact(
                paths=["foo"], workspace=self.workspace, create_files=True
            )
        artifact_serialized = {
            "workspace": self.default_workspace_name,
            "category": ArtifactCategory.TEST,
            "files": {
                "foo": {
                    "type": "file",
                    "size": len(files_contents["foo"]),
                    "checksums": {
                        "sha256": hashlib.sha256(
                            files_contents["foo"]
                        ).hexdigest()
                    },
                }
            },
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response_json = response.json()
        self.assertEqual(response_json["files_to_upload"], [])
        file_in_artifact = FileInArtifact.objects.get(
            artifact=response_json["id"], path="foo"
        )
        self.assertTrue(file_in_artifact.complete)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_honours_scope(self) -> None:
        """POST checks that the workspace is in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        scope3 = self.playground.get_or_create_scope("scope3")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="common-name", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="common-name", public=True
        )
        artifact_serialized = {
            "workspace": "common-name",
            "category": "testing",
            "files": {},
            "data": {},
        }

        for workspace in (workspace1, workspace2):
            response = self.post_artifact_create(
                token_key=self.scenario.user_token.key,
                data=artifact_serialized,
                hostname="example.com",
                scope=workspace.scope.name,
            )

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            assert isinstance(response, Response)
            artifact = Artifact.objects.get(id=response.data["id"])
            self.assertEqual(artifact.workspace, workspace)

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
            scope=scope3.name,
        )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern="Workspace common-name not found in scope scope3",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_no_default_workspace(self) -> None:
        """POST with no workspace in a scope without a default workspace."""
        scope = self.playground.get_or_create_scope("empty-scope")
        artifact_serialized = {
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
            scope=scope.name,
        )

        self.assertResponseProblem(
            response,
            "Cannot deserialize artifact",
            validation_errors_pattern=(
                r"'workspace': \['This field is required\.'\]"
            ),
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_private_workspace_unauthorized(self) -> None:
        """Artifacts in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        artifact_serialized = {
            "workspace": private_workspace.name,
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
            scope=private_workspace.scope.name,
        )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern=(
                f"Workspace {private_workspace.name} not found in scope "
                f"{private_workspace.scope.name}"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_create_artifact_private_workspace_authorized(self) -> None:
        """Artifacts in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        artifact_serialized = {
            "workspace": private_workspace.name,
            "category": "testing",
            "files": {},
            "data": {},
        }

        response = self.post_artifact_create(
            token_key=self.scenario.user_token.key,
            data=artifact_serialized,
            hostname="example.com",
            scope=private_workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assert isinstance(response, Response)
        artifact = Artifact.objects.get(id=response.data["id"])
        self.assertEqual(artifact.workspace, private_workspace)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_get_information(self) -> None:
        """Get information for the artifact."""
        with context.disable_permission_checks():
            artifact, _ = self.playground.create_artifact()
            token_key = self.scenario.user_token.key

            # Add a file (without "uploading" it) to test that files_to_upload
            # returns it.
            fileobj = self.playground.create_file()
            path_in_artifact = "README.txt"
            FileInArtifact.objects.create(
                artifact=artifact, file=fileobj, path=path_in_artifact
            )

        hostname = "example.com"
        response = self.client.get(
            reverse("api:artifact", kwargs={"artifact_id": artifact.id}),
            headers={"token": token_key, "host": hostname},
        )
        # HTTP_HOST must be a real hostname. By default, it is "testserver",
        # The Serializer needs a valid hostname for the URLField: otherwise
        # it fails the validation on the serializer
        # (for the download_artifact_tar_gz_url).
        # URLField can be an ipv4 or ipv6 address, a domain
        # or localhost.
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected = self.artifact_as_dict_response(artifact, hostname)

        assert isinstance(response, Response)
        self.assertEqual(response.data, expected)

    def test_get_information_artifact_not_found(self) -> None:
        """Get information return 404: artifact not found."""
        artifact_id = 0
        response = self.client.get(
            reverse("api:artifact", kwargs={"artifact_id": artifact_id}),
            headers={"token": self.scenario.user_token.key},
        )

        self.assertResponseProblem(
            response,
            "No Artifact matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_get_information_honours_scope(self) -> None:
        """Getting artifact information looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        artifact, _ = self.playground.create_artifact(workspace=workspace)

        hostname = "example.com"
        response = self.client.get(
            reverse("api:artifact", kwargs={"artifact_id": artifact.id}),
            headers={
                "Host": hostname,
                "Token": self.scenario.user_token.key,
                "X-Debusine-Scope": artifact.workspace.scope.name,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        with urlconf_scope(artifact.workspace.scope.name):
            expected = self.artifact_as_dict_response(artifact, hostname)
        assert isinstance(response, Response)
        self.assertEqual(response.data, expected)

        response = self.client.get(
            reverse("api:artifact", kwargs={"artifact_id": artifact.id}),
            headers={
                "Host": hostname,
                "Token": self.scenario.user_token.key,
                "X-Debusine-Scope": scope2.name,
            },
        )

        self.assertResponseProblem(
            response,
            "No Artifact matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_get_information_private_workspace_unauthorized(self) -> None:
        """Artifacts in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        artifact, _ = self.playground.create_artifact(
            workspace=private_workspace
        )

        hostname = "example.com"
        response = self.client.get(
            reverse("api:artifact", kwargs={"artifact_id": artifact.id}),
            headers={
                "Host": hostname,
                "Token": self.scenario.user_token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )

        self.assertResponseProblem(
            response,
            "No Artifact matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_get_information_private_workspace_authorized(self) -> None:
        """Artifacts in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        artifact, _ = self.playground.create_artifact(
            workspace=private_workspace
        )

        hostname = "example.com"
        response = self.client.get(
            reverse("api:artifact", kwargs={"artifact_id": artifact.id}),
            headers={
                "Host": hostname,
                "Token": self.scenario.user_token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        with urlconf_scope(artifact.workspace.scope.name):
            expected = self.artifact_as_dict_response(artifact, hostname)
        assert isinstance(response, Response)
        self.assertEqual(response.data, expected)


class UploadFileViewTests(TestCase):
    """Tests for UploadFileView class."""

    @context.disable_permission_checks()
    def setUp(self) -> None:
        """Set up objects for the tests."""
        self.token: Token | None = self.playground.create_worker_token()
        self.path_in_artifact = "README"
        self.file_size = 100
        self.artifact, self.files_contents = self.playground.create_artifact(
            [self.path_in_artifact], files_size=self.file_size
        )
        self.file_contents = self.files_contents[self.path_in_artifact]

        self.file_hash = self.hash_data(self.file_contents)

        self.file = self.playground.create_file(self.file_contents)
        self.file_in_artifact = FileInArtifact.objects.create(
            artifact=self.artifact, path=self.path_in_artifact, file=self.file
        )

    def tearDown(self) -> None:
        """If FileUpload for the file exist: delete it."""
        try:
            file_upload = FileUpload.objects.get(
                file_in_artifact=self.file_in_artifact
            )
        except FileUpload.DoesNotExist:
            return

        with self.captureOnCommitCallbacks(execute=True):
            file_upload.delete()

    @staticmethod
    def hash_data(data: bytes) -> str:
        """Return hex digest in hex of data."""
        hasher = hashlib.new(File.current_hash_algorithm)
        hasher.update(data)
        return hasher.digest().hex()

    def put_file(
        self,
        range_start: int | None = None,
        range_end: int | None = None,
        extra_put_params: dict[str, str] | None = None,
        *,
        path_in_artifact: str | None = None,
        artifact_id: int | None = None,
        forced_data: bytes | None = None,
        include_data: bool = True,
        token: Token | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """
        Upload file to debusine using the API.

        Use data in self.test_data

        :param range_start: use it with range_end
        :param range_end: if specified, upload a range of data
          use Content-Range: bytes $range_start-$range_end and attaches only
          the correct part of the data
        :param extra_put_params: add parameters to self.client.put
          (e.g. HTTP headers)
        :param path_in_artifact: path in the artifact to upload the file
        :param artifact_id: if not None: artifact_id to use,
          else self.artifact.id
        :param forced_data: if not None: use this data. Else,
          use self.test_data
        :param include_data: if True includes the data (following specified
          range)
        :param token: if not None: token to authenticate with, else
          self.token
        :param scope: if not None: X-Debusine-Scope header to send
        """
        parameters: dict[str, Any] = {}

        if forced_data is not None:
            data = forced_data
        else:
            data = self.file_contents

        if range_start is not None:
            if range_end is None:
                raise ValueError(
                    "range_end must have an int if range_start is not None"
                )  # pragma: no cover
            data = data[range_start : range_end + 1]
            file_size = len(data)
            parameters.update(
                **{
                    "HTTP_CONTENT_LENGTH": range_end + 1 - range_start,
                    "HTTP_CONTENT_RANGE": f"bytes {range_start}-{range_end}"
                    f"/{file_size}",
                }
            )

        if token is not None:
            parameters["HTTP_TOKEN"] = token.key
        elif self.token is not None:
            parameters["HTTP_TOKEN"] = self.token.key

        if scope is not None:
            parameters["HTTP_X_DEBUSINE_SCOPE"] = scope

        if extra_put_params is not None:
            parameters.update(**extra_put_params)

        if include_data:
            parameters["data"] = data

        if path_in_artifact is None:
            path_in_artifact = self.path_in_artifact

        if artifact_id is not None:
            artifact_id = artifact_id
        else:
            artifact_id = self.artifact.id

        with self.captureOnCommitCallbacks(execute=True):
            # captureOnCommitCallbacks(execute=True) to force File.delete()
            # callback to delete the file to be executed
            return self.client.put(
                reverse(
                    "api:upload-file",
                    kwargs={
                        "artifact_id": artifact_id,
                        "file_path": path_in_artifact,
                    },
                ),
                content_type="application/octet-stream",
                **parameters,
            )

    def test_put_file_no_token(self) -> None:
        """A request with no token cannot PUT a file."""
        self.token = None

        response = self.put_file()

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Authentication credentials were not provided.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_put_file_user_token(self) -> None:
        """A request with a user token can PUT a file."""
        token = self.playground.create_user_token()

        response = self.put_file(token=token)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_put_file_worker_token(self) -> None:
        """A request with a worker token can PUT a file."""
        assert hasattr(self.token, "worker")

        response = self.put_file()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_put_file_worker_activation_token(self) -> None:
        """A worker activation token cannot PUT a file."""
        activation_token = self.playground.create_worker_activation_token()

        response = self.put_file(token=activation_token)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="You do not have permission to perform this action.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_put_file_in_subdirectory(self) -> None:
        """Client PUT a file that is in a subdirectory (src/README)."""
        data = b"test"
        path_in_artifact = "src/README"

        file = self.playground.create_file(data)
        file_in_artifact = FileInArtifact.objects.create(
            artifact=self.artifact, path=path_in_artifact, file=file
        )

        response = self.put_file(
            path_in_artifact=path_in_artifact,
            forced_data=b"test",
            include_data=True,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        file_in_artifact.refresh_from_db()
        self.assertTrue(file_in_artifact.complete)

    def test_put_whole_file(self) -> None:
        """Server receive a file without Content-Range. Whole file."""
        self.assertEqual(FileInStore.objects.count(), 0)

        response = self.put_file()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(FileInStore.objects.count(), 1)

        file = FileInStore.objects.all()[0].file

        self.assertEqual(file.hash_digest.hex(), self.file_hash)
        self.assertEqual(file.size, self.file_size)
        self.file_in_artifact.refresh_from_db()
        self.assertTrue(self.file_in_artifact.complete)

    def test_put_wrong_whole_file(self) -> None:
        """Server receive a file without Content-Range. Unexpected hash."""
        files_in_uploads_before = FileUpload.objects.all().count()

        self.assertEqual(FileUpload.objects.count(), 0)

        data = b"x" * self.file_size

        response = self.put_file(forced_data=data)

        self.assertEqual(FileUpload.objects.count(), 0)

        hash_digest_expected = self.file_hash
        hash_digest_received = self.hash_data(data)

        self.assertResponseProblem(
            response,
            f"Invalid file hash. Expected: {hash_digest_expected} "
            f"Received: {hash_digest_received}",
            status_code=status.HTTP_409_CONFLICT,
        )

        self.assertEqual(
            files_in_uploads_before, FileUpload.objects.all().count()
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_already_uploaded_file_return_created(self) -> None:
        """Server return 201 created for a file that is already uploaded."""
        # Upload the file once
        response = self.put_file()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.file_in_artifact.refresh_from_db()
        self.assertTrue(self.file_in_artifact.complete)

        # Upload the same file again (can happen if two clients are uploading
        # the same file)
        response = self.put_file()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.file_in_artifact.refresh_from_db()
        self.assertTrue(self.file_in_artifact.complete)

    def test_put_big_file(self) -> None:
        """
        Server receive a file bigger than DATA_UPLOAD_MAX_MEMORY_SIZE.

        Ensure to exercise code for bigger files (read in multiple chunks and
        Django streaming it from disk instead of memory).
        """
        self.assertEqual(FileInStore.objects.count(), 0)

        file_size = settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 1000
        data = next(data_generator(file_size))
        path_in_artifact = self.path_in_artifact + ".2"
        file = self.playground.create_file(data)
        file_in_artifact = FileInArtifact.objects.create(
            artifact=self.artifact, path=path_in_artifact, file=file
        )

        response = self.put_file(
            path_in_artifact=path_in_artifact,
            forced_data=data,
            include_data=True,
        )

        file = FileInStore.objects.all()[0].file

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(file.hash_digest.hex(), self.hash_data(data))
        self.assertEqual(file.size, file_size)
        file_in_artifact.refresh_from_db()
        self.assertTrue(file_in_artifact.complete)

    def test_put_last_activity_updated(self) -> None:
        """Server updates FileUpload.last_activity_at on each received part."""
        # Upload first part of the file
        start_range = 0
        end_range = 20

        self.put_file(start_range, end_range)

        file_upload = self.file_in_artifact.fileupload

        # Last activity got updated and is less than now
        last_activity_1 = file_upload.last_activity_at
        self.assertLess(last_activity_1, timezone.now())

        start_range = end_range + 1
        end_range = 30

        # Uploads another part of the file (but not all)
        self.put_file(start_range, end_range)

        file_upload.refresh_from_db()

        last_activity_2 = file_upload.last_activity_at

        # Last activity got updated: greater than earlier, less than now
        self.assertGreater(last_activity_2, last_activity_1)
        self.assertLess(last_activity_2, timezone.now())

    def test_put_two_parts_file_completed(self) -> None:
        """Server receive a file in two parts."""
        files_in_uploads_before = FileUpload.objects.all().count()

        # Upload first part of the file
        start_range = 0
        end_range = 44

        response = self.put_file(start_range, end_range)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers["range"], f"bytes=0-{end_range + 1}")

        file_upload_path = self.file_in_artifact.fileupload.path
        self.assertFalse(Path(file_upload_path).is_absolute())
        self.assertFalse(self.file_in_artifact.complete)

        # Upload second part of the file
        start_range = end_range + 1
        end_range = self.file_size = 100 - 1

        response = self.put_file(start_range, end_range)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Assert that debusine created the file as expected in the database
        self.assertEqual(self.artifact.files.all().count(), 1)

        self.file_in_artifact.refresh_from_db()
        file = self.file_in_artifact.file
        self.assertEqual(file.hash_digest.hex(), self.file_hash)
        self.assertEqual(self.artifact.files.all().count(), 1)
        self.assertEqual(FileInStore.objects.count(), 1)

        self.assertEqual(
            files_in_uploads_before,
            FileUpload.objects.all().count(),
        )
        self.assertTrue(self.file_in_artifact.complete)

    def test_put_two_parts_file_completed_wrong_hash(self) -> None:
        """Server receive a file in two parts. Hash mismatch with expected."""
        files_in_uploads_before = FileUpload.objects.all().count()
        self.assertEqual(FileUpload.objects.count(), 0)

        # Upload first part of the file
        start_range_1 = 0
        end_range_1 = 44
        data_1 = self.file_contents
        response = self.put_file(start_range_1, end_range_1, forced_data=data_1)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Upload second part of the file (wrong hash)
        start_range_2 = end_range_1 + 1
        end_range_2 = self.file_size - 1
        data_2 = b"x" * self.file_size

        response = self.put_file(start_range_2, end_range_2, forced_data=data_2)

        expected_hash = self.file_hash

        received_hash = self.hash_data(
            data_1[start_range_1 : end_range_1 + 1]
            + data_2[start_range_2 : end_range_2 + 1]
        )

        self.assertResponseProblem(
            response,
            f"Invalid file hash. Expected: {expected_hash} "
            f"Received: {received_hash}",
            status_code=status.HTTP_409_CONFLICT,
        )

        # Temporary uploaded data is removed so the client can try again:
        self.assertEqual(FileUpload.objects.all().count(), 0)
        self.assertEqual(
            files_in_uploads_before,
            FileUpload.objects.all().count(),
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_content_range_star_size_partial_content(self) -> None:
        """
        Client PUT Content-Range: */$SIZE server return HTTP 206: partial file.

        The file has not started to be uploaded: response Range == bytes 0-0
        """  # noqa: RST213
        size = self.file.size

        file_upload = self.create_file_uploaded()
        file_upload.absolute_file_path().unlink()

        response = self.put_file(
            extra_put_params={"HTTP_CONTENT_RANGE": f"bytes */{size}"},
            include_data=False,
        )

        self.assertEqual(response.status_code, status.HTTP_206_PARTIAL_CONTENT)

        # Nothing has been uploaded yet
        self.assertEqual(response.headers["range"], "bytes=0-0")
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_gap_in_file_return_http_400(self) -> None:
        """
        Client PUT Content-Range: 40/99 having previously uploaded 30 bytes.

        Server responds with HTTP 400 because the second range starts after
        the received data.
        """
        upload_to_position = 30
        self.create_file_uploaded(upload_to_position)

        start_range = 40
        end_range = 99
        response = self.put_file(start_range, end_range)

        self.assertResponseProblem(
            response,
            f"Server had received {upload_to_position} bytes of the file. "
            f"New upload starts at {start_range}. "
            f"Please continue from {upload_to_position}",
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_continue_uploaded_file_without_content_range(self) -> None:
        """
        Client PUT without Content-Range and previously uploaded part of a file.

        Server responds with completed (it will overwrite the existing
        uploaded part of the file).
        """
        self.create_file_uploaded(30)

        response = self.put_file()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.file_in_artifact.refresh_from_db()
        self.assertTrue(self.file_in_artifact.complete)

    def create_file_uploaded(
        self, upload_size: int | None = None
    ) -> FileUpload:
        """
        Create an uploaded file (FileUpload and write data).

        Write self.file_in_artifact.file.size bytes or upload_size if not None.
        """
        temporary_file = self.create_temporary_file()

        file_in_artifact = FileInArtifact.objects.get(
            path=self.path_in_artifact, artifact=self.artifact
        )

        file_upload = FileUpload.objects.create(
            file_in_artifact=file_in_artifact,
            path=str(temporary_file),
        )

        if upload_size is None:
            upload_size = file_in_artifact.file.size

        file_upload.absolute_file_path().write_bytes(
            self.file_contents[:upload_size]
        )

        return file_upload

    def test_put_content_range_star_size_file_not_uploaded_yet(self) -> None:
        """
        Client PUT Content-Range: */$SIZE. Receive HTTP 206: whole file needed.

        The client had not uploaded any part of the file.
        """  # noqa: RST213
        response = self.put_file(
            extra_put_params={
                "HTTP_CONTENT_RANGE": f"bytes */{self.file_size}"
            },
            include_data=False,
        )
        self.assertEqual(response.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(response.headers["range"], "bytes=0-0")
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_content_range_star_size_file_already_uploaded(self) -> None:
        """
        Client PUT Content-Range: */$SIZE. Receive HTTP 200.

        The file was already uploaded.
        """  # noqa: RST213
        self.create_file_uploaded()

        response = self.put_file(
            extra_put_params={
                "HTTP_CONTENT_RANGE": f"bytes */{self.file_size}"
            },
            include_data=False,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_content_range_file_size_star(self) -> None:
        """
        Client PUT Content-Range: */*. Receive HTTP 200.

        The file was already uploaded.
        """  # noqa: RST213
        self.create_file_uploaded()
        response = self.put_file(
            extra_put_params={"HTTP_CONTENT_RANGE": "bytes */*"},
            include_data=False,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_content_range_invalid_file_size(self) -> None:
        """
        Client PUT Content-Range: */$WRONG_SIZE, receive error from the server.

        The server expected a different size than $WRONG_SIZE.
        """  # noqa: RST213
        FileUpload.objects.create(
            file_in_artifact=self.file_in_artifact,
            path="not-used-in-this-test",
        )

        content_range_length = self.file_size + 10
        content_range = f"bytes */{content_range_length}"

        response = self.put_file(
            extra_put_params={"HTTP_CONTENT_RANGE": content_range}
        )

        self.assertResponseProblem(
            response,
            "Invalid file size in Content-Range header. "
            f"Expected {self.file_size} received {content_range_length}",
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_put_artifact_does_not_exist(self) -> None:
        """Client PUT to artifact_id that does not exist. Return HTTP 404."""
        FileInArtifact.objects.all().delete()
        Artifact.objects.all().delete()

        invalid_artifact_id = 1
        response = self.put_file(artifact_id=invalid_artifact_id)

        self.assertResponseProblem(
            response,
            f"Artifact {invalid_artifact_id} does not exist",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_put_honours_scope(self) -> None:
        """PUT checks that the artifact is in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.artifact.workspace = workspace
        self.artifact.save()
        self.assertEqual(FileInStore.objects.count(), 0)

        response = self.put_file(scope=scope2.name)

        self.assertResponseProblem(
            response,
            f"Artifact {self.artifact.id} does not exist",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.put_file(scope=scope1.name)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(FileInStore.objects.count(), 1)

    def test_put_private_workspace_unauthorized(self) -> None:
        """Artifacts in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.artifact.workspace = private_workspace
        self.artifact.save()
        token = self.playground.create_user_token()
        assert token.user is not None

        response = self.put_file(
            token=token, scope=private_workspace.scope.name
        )

        self.assertResponseProblem(
            response,
            f"Artifact {self.artifact.id} does not exist",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_put_private_workspace_authorized(self) -> None:
        """Artifacts in private workspaces succeed for the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.artifact.workspace = private_workspace
        self.artifact.save()
        token = self.playground.create_user_token()
        assert token.user is not None
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[token.user]
        )
        self.assertEqual(FileInStore.objects.count(), 0)

        response = self.put_file(
            token=token, scope=private_workspace.scope.name
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(FileInStore.objects.count(), 1)

    def test_put_artifact_file_path_does_not_exist(self) -> None:
        """
        Client upload a file_path that does not exist in artifact.

        Server return HTTP 404.
        """
        invalid_path = FileInArtifact.objects.all()[0].path + ".tmp"

        response = self.put_file(path_in_artifact=invalid_path)

        self.assertResponseProblem(
            response,
            f'No file_path "{invalid_path}" for artifact {self.artifact.id}',
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_put_too_big_data(self) -> None:
        """
        Client send more data than it was expected based on the range.

        Server return HTTP 400.
        """
        start_range = 5
        end_range = 10
        data = self.file_contents[start_range:end_range]
        data_length = len(data)
        content_range = f"bytes {start_range}-{end_range}/{data_length}"

        response = self.put_file(
            extra_put_params={
                'HTTP_CONTENT_LENGTH': str(data_length),
                'HTTP_CONTENT_RANGE': content_range,
            },
            forced_data=data,
        )

        self.assertResponseProblem(
            response,
            f"Expected {end_range - start_range + 1} bytes (based on range "
            f"header: end-start+1). Received: {data_length} bytes",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_end_less_than_start(self) -> None:
        """Client send an invalid range: start is greater than file size."""
        file_length = len(self.file_contents)
        start_range = file_length + 10
        end_range = start_range + file_length - 1

        response = self.put_file(
            range_start=start_range,
            range_end=end_range,
        )

        self.assertResponseProblem(
            response,
            f"Range start ({start_range}) is greater than "
            f"file size ({file_length})",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_range_upload_data_after_end_of_the_file(self) -> None:
        """Client send data that would be written after the end of the file."""
        chunk = self.file_contents[0:20]
        chunk_length = len(chunk)
        start_range = self.file_size - 10
        end_range = start_range + chunk_length - 1
        content_range = f"bytes {start_range}-{end_range}/{self.file_size}"

        response = self.put_file(
            extra_put_params={
                'HTTP_CONTENT_RANGE': content_range,
            },
            forced_data=chunk,
        )

        self.assertResponseProblem(
            response,
            "Cannot process range: attempted to write after end of the file. "
            f"Range start: {start_range} Received: {chunk_length} "
            f"File size: {self.file_size}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_invalid_content_range_header_bytes(self) -> None:
        """Client send invalid Content-Range header. Server return HTTP 400."""
        invalid_header = "bytes something-not-valid"

        response = self.put_file(
            extra_put_params={
                'HTTP_CONTENT_RANGE': invalid_header,
            }
        )

        self.assertResponseProblem(
            response, f'Invalid Content-Range header: "{invalid_header}"'
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_invalid_content_range_header_file_size_partial(self) -> None:
        """Client send invalid content-range header. Server return HTTP 400."""
        invalid_header = "a/b"

        response = self.put_file(
            extra_put_params={
                'HTTP_CONTENT_RANGE': invalid_header,
            },
        )

        self.assertResponseProblem(
            response, f'Invalid Content-Range header: "{invalid_header}"'
        )
        self.file_in_artifact.refresh_from_db()
        self.assertFalse(self.file_in_artifact.complete)

    def test_create_file_in_storage(self) -> None:
        """Test create_file_in_storage() creates in correct FileStore."""
        file_upload = self.create_file_uploaded()
        file_id = file_upload.file_in_artifact.file.id
        scope = file_upload.file_in_artifact.artifact.workspace.scope

        # Create the file in the storage
        UploadFileView.create_file_in_storage(file_upload)

        file_store_expected = scope.file_stores.get()

        # The file got created in the scope's only file store
        self.assertTrue(file_store_expected.files.filter(id=file_id).exists())

        # Create a new FileStore and add it as the scope's preferred upload
        # file store
        new_file_store = FileStore.objects.create(
            name="nas-01",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": settings.DEBUSINE_STORE_DIRECTORY},
        )
        scope.file_stores.add(
            new_file_store, through_defaults={"upload_priority": 200}
        )

        # Create the file in the storage
        UploadFileView.create_file_in_storage(file_upload)

        # It got created in the new FileStore
        self.assertTrue(new_file_store.files.filter(id=file_id).exists())

        # Mark the new FileStore as write-only; this means it will only be
        # written to by vacuum_storage when applying the `populate` policy,
        # not by ordinary file uploads.
        new_file_store_policies = scope.filestoreinscope_set.get(
            file_store=new_file_store
        )
        new_file_store_policies.write_only = True
        new_file_store_policies.save()

        # Create another file in the storage
        self.path_in_artifact = "AUTHORS"
        self.file_contents = b"An AUTHORS file\n"
        new_file = self.playground.create_file(self.file_contents)
        FileInArtifact.objects.create(
            artifact=self.artifact, path=self.path_in_artifact, file=new_file
        )
        new_file_upload = self.create_file_uploaded()
        new_file_id = new_file_upload.file_in_artifact.file.id
        self.assertNotEqual(new_file_id, file_id)
        UploadFileView.create_file_in_storage(new_file_upload)

        # It got created in the original FileStore
        self.assertTrue(
            file_store_expected.files.filter(id=new_file_id).exists()
        )


class ArtifactRelationsViewTests(TestCase):
    """Tests for ArtifactRelationsView class."""

    scenario = scenarios.DefaultContextAPI()

    @context.disable_permission_checks()
    def setUp(self) -> None:
        """Initialize object."""
        self.artifact = Artifact.objects.create(
            category="test", workspace=self.scenario.workspace
        )
        self.artifact_target = Artifact.objects.create(
            category="test", workspace=self.scenario.workspace
        )
        self.client = APIClient()

    @staticmethod
    def request_relations(
        method: Callable[..., HttpResponseBase],
        *,
        artifact_id: int | None,
        target_artifact_id: int | None,
        body: dict[str, Any] | None = None,
        token: Token | None = None,
        scope: str | None = None,
    ) -> HttpResponseBase:
        """
        Call method with reverse("api:artifact-relation-list").

        :param method: method to call (usually self.client.get/post/delete)
        :param artifact_id: used to create the URL (passed as artifact_id)
        :param target_artifact_id:
        :param body: if not None passed as method's data argument and
          make content_type="application/json".
        :param token: if not None: token to authenticate with.
        :param scope: if not None: X-Debusine-Scope header to send.
        """
        params = {}
        if artifact_id is not None:
            params["artifact"] = artifact_id

        if target_artifact_id is not None:
            params["target_artifact"] = target_artifact_id

        post_kwargs = {}
        post_args = []
        if body is not None:
            post_kwargs["format"] = "json"
            post_args.append(body)

        if token is not None:
            post_kwargs["HTTP_TOKEN"] = token.key

        if scope is not None:
            post_kwargs["HTTP_X_DEBUSINE_SCOPE"] = scope

        query_string = urlencode(params)

        response = method(
            reverse(
                "api:artifact-relation-list",
            )
            + f"?{query_string}",
            *post_args,
            **post_kwargs,
        )
        assert isinstance(response, HttpResponseBase)
        return response

    def get_relations(
        self,
        *,
        artifact_id: int | None = None,
        target_artifact_id: int | None = None,
        token: Token | None = None,
        scope: str | None = None,
    ) -> HttpResponseBase:
        """
        Make an HTTP GET request to api:artifact-relation-list URL.

        :param artifact_id: used in query parameter "artifact"
        :param target_artifact_id: used in query parameter "target_artifact"
        :param token: if not None: token to authenticate with.
        :param scope: if not None: X-Debusine-Scope header to send.
        """
        return self.request_relations(
            self.client.get,
            artifact_id=artifact_id,
            target_artifact_id=target_artifact_id,
            token=token,
            scope=scope,
        )

    def post_relation(
        self,
        body: dict[str, Any] | None = None,
        token: Token | None = None,
        scope: str | None = None,
    ) -> HttpResponseBase:
        """
        Make an HTTP POST request to api:artifact-relation-list URL.

        :param body: body of the request
        :param token: if not None: token to authenticate with.
        :param scope: if not None: X-Debusine-Scope header to send.
        """
        return self.request_relations(
            self.client.post,
            artifact_id=None,
            target_artifact_id=None,
            body=body,
            token=token,
            scope=scope,
        )

    def delete_relation(
        self,
        artifact_relation_id: int,
        token: Token | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """
        Make an HTTP DELETE request to api:artifact-relation-detail URL.

        :param artifact_relation_id: ID of relation to delete.
        :param token: if not None: token to authenticate with.
        :param scope: if not None: X-Debusine-Scope header to send.
        """
        headers = {}
        if token is not None:
            headers["Token"] = token.key
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.delete(
            reverse(
                "api:artifact-relation-detail",
                kwargs={"pk": artifact_relation_id},
            ),
            headers=headers,
        )

    def test_get_return_404_artifact_not_found(self) -> None:
        """Get relations for a non-existing artifact: return 404."""
        artifact_id = 0
        response = self.get_relations(artifact_id=artifact_id)

        self.assertResponseProblem(
            response,
            f"Artifact {artifact_id} not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_return_404_target_artifact_not_found(self) -> None:
        """Get target relations for a non-existing artifact: return 404."""
        artifact_id = 0
        response = self.get_relations(target_artifact_id=artifact_id)

        self.assertResponseProblem(
            response,
            f"Artifact {artifact_id} not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_return_400_mandatory_parameters_missing(self) -> None:
        """Get relations: fails because missing mandatory parameters."""
        response = self.get_relations()

        self.assertResponseProblem(
            response, '"artifact" or "target_artifact" parameter are mandatory'
        )

    def create_relations(self) -> None:
        """Create two relations to be used in the tests."""
        ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.BUILT_USING,
        )
        ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

    def test_get_200_target_artifact_relations(self) -> None:
        """Get return target artifact relations."""
        with context.disable_permission_checks():
            self.create_relations()

            another_artifact = Artifact.objects.create(
                category=self.artifact.category,
                workspace=self.artifact.workspace,
            )
            ArtifactRelation.objects.create(
                artifact=self.artifact,
                target=another_artifact,
                type=ArtifactRelation.Relations.RELATES_TO,
            )

        response = self.get_relations(
            target_artifact_id=self.artifact_target.id
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(
            len(response.json()), self.artifact_target.targeted_by.count()
        )

        for artifact_relation in response.json():
            ArtifactRelation.objects.get(
                artifact_id=artifact_relation["artifact"],
                target_id=artifact_relation["target"],
                type=artifact_relation["type"],
            )

    def test_get_200_artifact_relations(self) -> None:
        """Get return artifact relations."""
        self.create_relations()

        response = self.get_relations(artifact_id=self.artifact.id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(
            len(response.json()), ArtifactRelation.objects.all().count()
        )

        for artifact_relation in response.json():
            ArtifactRelation.objects.get(
                artifact_id=self.artifact.id,
                target_id=artifact_relation["target"],
                type=artifact_relation["type"],
            )

    def test_get_honours_scope(self) -> None:
        """Getting relations looks up the artifact in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.artifact.workspace = workspace
        self.artifact.save()
        self.artifact_target.workspace = workspace
        self.artifact_target.save()
        self.create_relations()

        response = self.get_relations(
            artifact_id=self.artifact.id,
            token=self.scenario.user_token,
            scope=workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(
            len(response.json()), ArtifactRelation.objects.all().count()
        )

        response = self.get_relations(
            artifact_id=self.artifact.id,
            token=self.scenario.user_token,
            scope=scope2.name,
        )

        self.assertResponseProblem(
            response,
            f"Artifact {self.artifact.id} not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_private_workspace_unauthorized(self) -> None:
        """Artifacts in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.artifact.workspace = private_workspace
        self.artifact.save()
        self.artifact_target.workspace = private_workspace
        self.artifact_target.save()

        response = self.get_relations(
            artifact_id=self.artifact.id,
            token=self.scenario.user_token,
            scope=private_workspace.scope.name,
        )

        self.assertResponseProblem(
            response,
            f"Artifact {self.artifact.id} not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_private_workspace_authorized(self) -> None:
        """Artifacts in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.artifact.workspace = private_workspace
        self.artifact.save()
        self.artifact_target.workspace = private_workspace
        self.artifact_target.save()

        response = self.get_relations(
            artifact_id=self.artifact.id,
            token=self.scenario.user_token,
            scope=private_workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(
            len(response.json()), ArtifactRelation.objects.all().count()
        )

    def test_post_400_artifact_id_does_not_exist(self) -> None:
        """Post the relations for a non-existing artifact: return 400."""
        artifact_id = 0
        artifact_relation = {
            "artifact": artifact_id,
            "target": self.artifact.id,
            "type": ArtifactRelation.Relations.RELATES_TO,
        }

        response = self.post_relation(body=artifact_relation)

        self.assertResponseProblem(
            response,
            "Cannot deserialize artifact relation",
            validation_errors_pattern="Invalid pk",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_post_400_cannot_deserialize(self) -> None:
        """Add a new relation for an artifact: HTTP 400 cannot deserialize."""
        response = self.post_relation(body={"foo": "bar"})

        self.assertResponseProblem(
            response,
            title="Cannot deserialize artifact relation",
            validation_errors_pattern="This field is required",
        )

    def test_post_201_created(self) -> None:
        """Add a new relation for an artifact."""
        self.assertEqual(self.artifact.relations.all().count(), 0)
        relation = {
            "artifact": self.artifact.id,
            "target": self.artifact_target.id,
            "type": ArtifactRelation.Relations.RELATES_TO,
        }

        response = self.post_relation(body=relation)

        assert isinstance(response, JSONResponseProtocol)
        response_data = response.json()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        relation["id"] = ArtifactRelation.objects.all()[0].id
        self.assertEqual(response_data, relation)

        ArtifactRelation.objects.get(
            id=response_data["id"],
            artifact_id=response_data["artifact"],
            target_id=response_data["target"],
            type=response_data["type"],
        )

    def test_post_200_duplicated(self) -> None:
        """Try to add a relation that already exists: return HTTP 200."""
        artifact_relation = ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.BUILT_USING,
        )

        relation = {
            "artifact": artifact_relation.artifact.id,
            "target": artifact_relation.target.id,
            "type": str(artifact_relation.type),
        }

        response = self.post_relation(body=relation)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        relation["id"] = artifact_relation.id
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(response.json(), relation)

    def test_post_400_invalid_target_id(self) -> None:
        """No relations added to the artifact: invalid target id."""
        self.assertEqual(self.artifact.relations.all().count(), 0)

        relation = {
            "artifact": self.artifact.id,
            "target": 0,
            "type": ArtifactRelation.Relations.RELATES_TO,
        }

        response = self.post_relation(body=relation)

        self.assertResponseProblem(
            response,
            title="Cannot deserialize artifact relation",
            validation_errors_pattern="Invalid pk",
        )

    def test_post_honours_scope(self) -> None:
        """Getting relations looks up the artifact in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.artifact.workspace = workspace
        self.artifact.save()
        self.artifact_target.workspace = workspace
        self.artifact_target.save()
        relation = {
            "artifact": self.artifact.id,
            "target": self.artifact_target.id,
            "type": ArtifactRelation.Relations.RELATES_TO,
        }

        response = self.post_relation(
            body=relation, token=self.scenario.user_token, scope=scope2.name
        )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern="Workspace workspace not found in scope scope2",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.post_relation(
            body=relation,
            token=self.scenario.user_token,
            scope=workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assert isinstance(response, JSONResponseProtocol)
        relation["id"] = ArtifactRelation.objects.all()[0].id
        self.assertEqual(response.json(), relation)

    def test_post_private_workspace_unauthorized(self) -> None:
        """Creating relations in private workspaces 404s to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.artifact.workspace = private_workspace
        self.artifact.save()
        self.artifact_target.workspace = private_workspace
        self.artifact_target.save()
        relation = {
            "artifact": self.artifact.id,
            "target": self.artifact_target.id,
            "type": ArtifactRelation.Relations.RELATES_TO,
        }

        response = self.post_relation(
            body=relation,
            token=self.scenario.user_token,
            scope=private_workspace.scope.name,
        )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern=(
                f"Workspace Private not found in scope "
                f"{private_workspace.scope.name}"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_private_workspace_authorized(self) -> None:
        """Creating relations in private workspaces 200s to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.artifact.workspace = private_workspace
        self.artifact.save()
        self.artifact_target.workspace = private_workspace
        self.artifact_target.save()
        relation = {
            "artifact": self.artifact.id,
            "target": self.artifact_target.id,
            "type": ArtifactRelation.Relations.RELATES_TO,
        }

        response = self.post_relation(
            body=relation,
            token=self.scenario.user_token,
            scope=private_workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assert isinstance(response, JSONResponseProtocol)
        relation["id"] = ArtifactRelation.objects.all()[0].id
        self.assertEqual(response.json(), relation)

    def test_delete_404_artifact_not_found(self) -> None:
        """Delete relation: cannot find artifact."""
        artifact_relation_id = 0
        response = self.delete_relation(artifact_relation_id)

        self.assertResponseProblem(
            response,
            "No ArtifactRelation matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_delete_204_success(self) -> None:
        """Delete relation."""
        artifact_relation = ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

        response = self.delete_relation(artifact_relation.id)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.artifact.relations.all().count(), 0)

    def test_delete_honours_scope(self) -> None:
        """Deleting relations looks up the artifact in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.artifact.workspace = workspace
        self.artifact.save()
        self.artifact_target.workspace = workspace
        self.artifact_target.save()
        artifact_relation = ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

        response = self.delete_relation(
            artifact_relation_id=artifact_relation.id,
            token=self.scenario.user_token,
            scope=scope2.name,
        )

        self.assertResponseProblem(
            response,
            "No ArtifactRelation matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.delete_relation(
            artifact_relation_id=artifact_relation.id,
            token=self.scenario.user_token,
            scope=workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.artifact.relations.all().count(), 0)

    def test_delete_private_workspace_unauthorized(self) -> None:
        """Deleting relations in private workspaces 404s to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.artifact.workspace = private_workspace
        self.artifact.save()
        self.artifact_target.workspace = private_workspace
        self.artifact_target.save()
        artifact_relation = ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

        response = self.delete_relation(
            artifact_relation_id=artifact_relation.id,
            token=self.scenario.user_token,
            scope=private_workspace.scope.name,
        )

        self.assertResponseProblem(
            response,
            "No ArtifactRelation matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_delete_private_workspace_authorized(self) -> None:
        """Deleting relations in private workspaces 200s to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.artifact.workspace = private_workspace
        self.artifact.save()
        self.artifact_target.workspace = private_workspace
        self.artifact_target.save()
        artifact_relation = ArtifactRelation.objects.create(
            artifact=self.artifact,
            target=self.artifact_target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

        response = self.delete_relation(
            artifact_relation_id=artifact_relation.id,
            token=self.scenario.user_token,
            scope=private_workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.artifact.relations.all().count(), 0)
