# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for serializers."""

from datetime import datetime, timezone
from typing import Any, ClassVar

from django.conf import settings
from django.test import RequestFactory, override_settings
from django.urls import reverse
from rest_framework.exceptions import ErrorDetail

from debusine.artifacts.models import RuntimeStatistics
from debusine.assets import AssetCategory, KeyPurpose, SigningKeyData
from debusine.client.models import (
    LookupChildType,
    model_to_json_serializable_dict,
)
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    File,
    Token,
    WorkRequest,
    Worker,
    Workspace,
    default_workspace,
)
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.serializers import (
    ArtifactSerializer,
    ArtifactSerializerResponse,
    AssetSerializer,
    FileSerializer,
    LookupMultipleSerializer,
    LookupSingleSerializer,
    WorkRequestCompletedSerializer,
    WorkRequestSerializer,
    WorkerRegisterSerializer,
    WorkflowTemplateSerializer,
)
from debusine.tasks.models import OutputData, SbuildData, SbuildInput
from debusine.test import test_utils
from debusine.test.django import TestCase


class WorkRequestSerializerTests(TestCase):
    """Tests for WorkRequestSerializer."""

    token: ClassVar[Token]
    work_request: ClassVar[WorkRequest]
    work_request_serializer: ClassVar[WorkRequestSerializer]
    workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize test."""
        super().setUpTestData()

        cls.token = cls.playground.create_user_token()

        worker = Worker.objects.create_with_fqdn(
            'worker.lan', token=cls.playground.create_bare_token()
        )

        cls.work_request = cls.playground.create_work_request(
            started_at=datetime(2022, 2, 20, 15, 19, 1, 158424, timezone.utc),
            completed_at=datetime(2022, 2, 20, 16, 2, 3, 558425, timezone.utc),
            status=WorkRequest.Statuses.COMPLETED,
            result=WorkRequest.Results.SUCCESS,
            worker=worker,
            task_name='sbuild',
            task_data=SbuildData(
                input=SbuildInput(source_artifact=1),
                host_architecture="x64",
                environment="debian/match:codename=sid",
            ),
            created_by=cls.token.user,
        )

        cls.work_request_serializer = WorkRequestSerializer(cls.work_request)

        cls.workspace = cls.work_request.workspace

    def test_expected_fields(self) -> None:
        """Serializer returns the expected fields."""
        self.assertCountEqual(
            self.work_request_serializer.data.keys(),
            {
                'artifacts',
                'completed_at',
                'created_at',
                'created_by',
                'duration',
                'dynamic_task_data',
                'event_reactions',
                'id',
                'priority_adjustment',
                'priority_base',
                'result',
                'started_at',
                'status',
                'task_data',
                'task_name',
                'task_type',
                'worker',
                'workflow_data',
                'workspace',
            },
        )

    @context.disable_permission_checks()
    def test_serialize_include_artifact_ids_created_by_work_request(
        self,
    ) -> None:
        """Assert serialized WorkRequest include artifact ids created by it."""
        artifact_1, _ = self.playground.create_artifact(
            work_request=self.work_request
        )
        artifact_2, _ = self.playground.create_artifact(
            work_request=self.work_request
        )
        serialized = self.work_request_serializer.data
        self.assertEqual(
            serialized["artifacts"], sorted([artifact_1.id, artifact_2.id])
        )

    def assert_is_valid(
        self,
        data: dict[str, Any],
        only_fields: list[str] | None,
        is_valid_expected: bool,
        errors: dict[str, Any] | None = None,
    ) -> WorkRequestSerializer:
        """Validate data using only_fields and expects is_valid_expected."""
        work_request_serializer = WorkRequestSerializer(
            data=data, only_fields=only_fields
        )
        self.assertEqual(work_request_serializer.is_valid(), is_valid_expected)

        if errors is not None:
            self.assertEqual(work_request_serializer.errors, errors)

        return work_request_serializer

    def test_validate_only_fields_invalid(self) -> None:
        """Use only_fields with an extra field."""
        context.set_scope(self.workspace.scope)
        data = {
            'task_name': 'sbuild',
            'task_data': {'foo': 'bar'},
            'unwanted_field': 'for testing',
            'unwanted_field2': 'for testing',
            'binnmu': {'changelog': 'not allowed here', 'suffix': '+b1'},
            'workspace': self.workspace.name,
            'created_by': self.token.user_id,
        }
        errors = {
            'non_field_errors': [
                ErrorDetail(
                    string='Invalid fields: binnmu,'
                    ' unwanted_field, unwanted_field2',
                    code='invalid',
                )
            ]
        }
        self.assert_is_valid(
            data,
            ['task_name', 'task_data', 'workspace', 'created_by'],
            False,
            errors,
        )

    def test_validate_only_fields_valid(self) -> None:
        """Use only_fields with an extra field in data."""
        context.set_scope(self.workspace.scope)
        data = {
            'task_name': 'sbuild',
            'task_data': {'foo': 'bar'},
            'workspace': self.workspace.name,
            'created_by': self.token.user_id,
        }
        self.assert_is_valid(
            data, ['task_name', 'task_data', 'workspace', 'created_by'], True
        )

    def test_validate_without_only_fields(self) -> None:
        """is_valid() return True: not using only_fields."""
        context.set_scope(self.workspace.scope)
        data = {
            'task_name': 'sbuild',
            'workspace': self.workspace.name,
            'created_by': self.token.user_id,
        }
        self.assert_is_valid(data, None, True)

    def test_serialized_workspace_name(self) -> None:
        """Serialized work request has workspace name."""
        work_request_serialized = WorkRequestSerializer(self.work_request).data

        self.assertEqual(
            work_request_serialized["workspace"], self.workspace.name
        )

    def test_workspace_name_resolved_in_current_scope(self) -> None:
        """The given workspace name is resolved in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        scope3 = self.playground.get_or_create_scope("scope3")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="common-name", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="common-name", public=True
        )
        data = {
            "task_name": "sbuild",
            "workspace": "common-name",
            "created_by": self.token.user_id,
        }
        only_fields = ["task_name", "workspace", "created_by"]

        for workspace in (workspace1, workspace2):
            with context.local():
                context.set_scope(workspace.scope)
                work_request_serializer = self.assert_is_valid(
                    data, only_fields, True
                )
                self.assertEqual(
                    work_request_serializer.validated_data["workspace"],
                    workspace,
                )

        with context.local():
            context.set_scope(scope3)
            self.assert_is_valid(data, only_fields, False)

    def test_default_workspace(self) -> None:
        """Use the default workspace name in the current scope, if it exists."""
        data = {"task_name": "noop", "created_by": self.token.user_id}
        only_fields = ["task_name", "workspace", "created_by"]

        with context.local():
            context.set_scope(self.playground.get_default_scope())
            work_request_serializer = self.assert_is_valid(
                data, only_fields, True
            )
            self.assertEqual(
                work_request_serializer.validated_data["workspace"],
                self.playground.get_default_workspace(),
            )

        with context.local():
            context.set_scope(
                self.playground.get_or_create_scope("empty-scope")
            )
            self.assert_is_valid(
                data,
                only_fields,
                False,
                {
                    "workspace": [
                        ErrorDetail(
                            string="This field is required.", code="required"
                        )
                    ]
                },
            )


class WorkerRegisterSerializerTests(TestCase):
    """Test for WorkerRegisterSerializer class."""

    def test_expected_fields(self) -> None:
        """Expected fields are defined in the serializer."""
        worker_register_serializer = WorkerRegisterSerializer()
        data = worker_register_serializer.data

        self.assertCountEqual(data.keys(), {'token', 'fqdn', 'worker_type'})


class WorkRequestCompletedSerializerTests(TestCase):
    """Test for WorkRequestCompletedSerializer class."""

    def test_accept_success(self) -> None:
        """Serializer accepts {"result": "success"}."""
        work_request_completed_serializer = WorkRequestCompletedSerializer(
            data={'result': 'success'}
        )
        self.assertTrue(work_request_completed_serializer.is_valid())
        self.assertEqual(
            work_request_completed_serializer.validated_data['result'],
            'success',
        )

    def test_accept_success_with_output_data(self) -> None:
        """Serializer accepts output data."""
        work_request_completed_serializer = WorkRequestCompletedSerializer(
            data={
                "result": "success",
                "output_data": model_to_json_serializable_dict(
                    OutputData(
                        runtime_statistics=RuntimeStatistics(duration=60)
                    ),
                    exclude_unset=True,
                ),
            }
        )
        self.assertTrue(work_request_completed_serializer.is_valid())
        self.assertEqual(
            work_request_completed_serializer.validated_data['result'],
            'success',
        )

    def test_not_accept_unknown_result(self) -> None:
        """Serializer does not accept unrecognised result."""
        work_request_completed_serializer = WorkRequestCompletedSerializer(
            data={'result': 'something'}
        )
        self.assertFalse(work_request_completed_serializer.is_valid())
        self.assertIn("result", work_request_completed_serializer.errors)

    def test_not_accept_invalid_output_data(self) -> None:
        """Serializer does not accept invalid output data."""
        work_request_completed_serializer = WorkRequestCompletedSerializer(
            data={"result": "success", "output_data": {"nonsense": True}}
        )
        self.assertFalse(work_request_completed_serializer.is_valid())
        self.assertIn("output_data", work_request_completed_serializer.errors)


def serialized_file() -> dict[str, Any]:
    """Return a file."""
    return {
        "type": "file",
        "size": 3827,
        "checksums": {
            "sha256": "164a3bc86c0fe9a7aa15cfa9156e9be1124aad69e"
            "a757a011be1f1a13502409f",
            "md5": "7d13cb5b2bee07003d2b69ccbd256e65",
        },
    }


class FileSerializerTests(TestCase):
    """Test for FileSerializer class."""

    def test_serializer_with_valid_data(self) -> None:
        """Test File serializer (valid data)."""
        data = serialized_file()

        file_serializer = FileSerializer(data=data)

        self.assertTrue(file_serializer.is_valid())
        self.assertEqual(file_serializer.validated_data, data)

    def test_is_valid_false_missing_file_type(self) -> None:
        """Test FileSerializer (invalid data, no file_type)."""
        data = serialized_file()

        del data["type"]

        file_serializer = FileSerializer(data=data)

        self.assertFalse(file_serializer.is_valid())

    def test_is_valid_false_invalid_file_type(self) -> None:
        """Test FileSerializer (invalid file type)."""
        file = serialized_file()

        file["type"] = "directory"

        file_serializer = FileSerializer(data=file)

        self.assertFalse(file_serializer.is_valid())

    def test_is_valid_false_invalid_file_size(self) -> None:
        """Test FileSerializer (size must be positive)."""
        file = serialized_file()

        file["size"] = -10

        file_serializer = FileSerializer(data=file)

        self.assertFalse(file_serializer.is_valid())


class FileSerializerResponseTests(TestCase):
    """Tests for FileSerializerResponse."""

    def test_url(self) -> None:
        """It has the URL of the file."""
        file = serialized_file()
        file["url"] = "https://example.com/some/URL"

        FileSerializer(data=file)


class ArtifactSerializerTests(TestCase):
    """Test for ArtifactSerializer class."""

    workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize test."""
        super().setUpTestData()
        cls.workspace = cls.playground.create_workspace(name="test")

    def test_is_valid_true(self) -> None:
        """Test Artifact serializer (valid data)."""
        context.set_scope(self.workspace.scope)
        category = "artifact-test"

        serialized_artifact = {
            "category": category,
            "workspace": self.workspace.name,
            "work_request": 5,
            "files": {"AUTHORS": serialized_file()},
            "data": {"key1": "value1", "key2": "value2"},
        }

        artifact_serializer = ArtifactSerializer(data=serialized_artifact)
        self.assertTrue(artifact_serializer.is_valid())
        self.assertEqual(
            artifact_serializer.validated_data,
            {**serialized_artifact, "workspace": self.workspace},
        )

    def test_is_valid_true_no_work_request(self) -> None:
        """Test Artifact serializer (valid data) with no work_request."""
        context.set_scope(self.workspace.scope)
        category = "artifact-test"

        serialized_artifact = {
            "category": category,
            "workspace": self.workspace.name,
            "files": {"AUTHORS": serialized_file()},
            "data": {"key1": "value1", "key2": "value2"},
        }
        artifact_serializer = ArtifactSerializer(data=serialized_artifact)
        self.assertTrue(artifact_serializer.is_valid())
        self.assertNotIn("work_request", artifact_serializer.validated_data)

    def test_is_valid_true_work_request_is_none(self) -> None:
        """Test Artifact serializer (valid data) with work_request=None."""
        context.set_scope(self.workspace.scope)
        category = "artifact-test"

        serialized_artifact = {
            "category": category,
            "workspace": self.workspace.name,
            "files": {"AUTHORS": serialized_file()},
            "data": {"key1": "value1", "key2": "value2"},
            "work_request": None,
        }
        artifact_serializer = ArtifactSerializer(data=serialized_artifact)
        self.assertTrue(artifact_serializer.is_valid())
        self.assertIsNone(artifact_serializer.validated_data["work_request"])


class ArtifactSerializerResponseTests(TestCase):
    """Tests for ArtifactSerializerResponse class."""

    files_to_add: list[str]
    artifact: ClassVar[Artifact]
    files: ClassVar[dict[str, bytes]]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up test."""
        super().setUpTestData()
        cls.files_to_add = ["Makefile", "README.txt"]
        cls.artifact, cls.files = cls.playground.create_artifact(
            cls.files_to_add, create_files=True, skip_add_files_in_store=True
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_from_artifact(self) -> None:
        """from_artifact() return the expected ArtifactSerializerResponse."""
        host = "example.com"
        request = RequestFactory().get("/test", HTTP_HOST=host)
        serializer = ArtifactSerializerResponse.from_artifact(
            self.artifact, request
        )

        download_path = self.artifact.get_absolute_url_download()
        download_url = f"http://{host}{download_path}?archive=tar.gz"
        created_at = test_utils.date_time_to_isoformat_rest_framework(
            self.artifact.created_at
        )

        files = {}

        for file_name, file_content in self.files.items():
            checksums = {
                File.current_hash_algorithm: _calculate_hash_from_data(
                    file_content
                ).hex()
            }
            path = reverse(
                "workspaces:artifacts:file-download",
                kwargs={
                    "wname": self.artifact.workspace.name,
                    "artifact_id": self.artifact.id,
                    "path": file_name,
                },
            )
            files[file_name] = {
                "size": len(file_content),
                "checksums": checksums,
                "type": "file",
                "url": f"http://{host}{path}",
            }

        self.assertEqual(
            serializer.data,
            {
                "id": self.artifact.id,
                "workspace": self.artifact.workspace.name,
                "category": str(self.artifact.category),
                "data": self.artifact.data,
                "created_at": created_at,
                "expire_at": self.artifact.expire_at,
                "download_tar_gz_url": download_url,
                "files_to_upload": self.files_to_add,
                "files": files,
            },
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_build_absolute_download_url(self) -> None:
        """_download_url() return the expected URL."""
        host = "example.com"

        request = RequestFactory().get("/test", HTTP_HOST=host)

        actual = ArtifactSerializerResponse._build_absolute_download_url(
            self.artifact, request
        )

        path = self.artifact.get_absolute_url_download()
        expected = f"{request.scheme}://{host}{path}"

        self.assertEqual(actual, expected)


class AssetSerializerTests(TestCase):
    """Tests for AssetSerializer."""

    def setUp(self) -> None:
        """Configure a context, required by the context."""
        super().setUp()
        self.scope = self.playground.get_default_scope()
        self.user = self.playground.get_default_user()
        self.workspace = self.playground.get_default_workspace()
        context.set_scope(self.scope)

    def test_unknown_asset_category(self) -> None:
        """Deserialize an asset of an unknown category."""
        serializer = AssetSerializer(
            data={
                "category": "unknown",
                "workspace": self.workspace.name,
                "data": {},
                "work_request": 12,
            },
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("category", serializer.errors)
        self.assertEqual(
            serializer.errors["category"][0], "unknown is not a known category."
        )

    def test_invalid_data(self) -> None:
        """Deserialize an asset with invalid data."""
        serializer = AssetSerializer(
            data={
                "category": AssetCategory.SIGNING_KEY,
                "workspace": self.workspace.name,
                "data": {},
                "work_request": 12,
            },
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("data", serializer.errors)
        self.assertTrue(
            serializer.errors["data"][0].startswith("invalid asset data:")
        )

    def test_valid_data_work_request(self) -> None:
        """Deserialize an asset with valid data, created by a work request."""
        work_request = self.playground.create_work_request()
        serializer = AssetSerializer(
            data={
                "category": AssetCategory.SIGNING_KEY,
                "workspace": self.workspace.name,
                "data": SigningKeyData(
                    description="A Test Key",
                    fingerprint="ABC123",
                    public_key="PUBLIC KEY",
                    purpose=KeyPurpose.OPENPGP,
                ).dict(),
                "work_request": work_request.id,
            },
        )
        self.assertTrue(serializer.is_valid())
        with context.disable_permission_checks():
            asset = serializer.create(serializer.validated_data)
        self.assertEqual(asset.created_by_work_request, work_request)
        self.assertIsNone(asset.created_by)

    def test_valid_data_user(self) -> None:
        """Deserialize an asset with valid data, created by a user."""
        context.set_user(self.user)
        serializer = AssetSerializer(
            data={
                "category": AssetCategory.SIGNING_KEY,
                "workspace": self.workspace.name,
                "data": SigningKeyData(
                    description="A Test Key",
                    fingerprint="ABC123",
                    public_key="PUBLIC KEY",
                    purpose=KeyPurpose.OPENPGP,
                ).dict(),
            },
        )
        self.assertTrue(serializer.is_valid())
        with context.disable_permission_checks():
            asset = serializer.create(serializer.validated_data)
        self.assertIsNone(asset.created_by_work_request)
        self.assertEqual(asset.created_by, self.user)


class LookupSingleSerializerTests(TestCase):
    """Tests for LookupSingleSerializer."""

    def test_expect_type(self) -> None:
        """Test possible values of `expect_type`."""
        work_request = self.playground.create_work_request()

        for expect_type, validated_expect_type in (
            # All the items in LookupChildType are valid.
            *((str(item), item) for item in LookupChildType),
            # These specific values are mapped to their corresponding items
            # in LookupChildType, for compatibility with old clients.
            ("b", LookupChildType.BARE),
            ("a", LookupChildType.ARTIFACT),
            ("c", LookupChildType.COLLECTION),
        ):
            with self.subTest(expect_type=expect_type):
                data = {
                    "lookup": {"collection": "test"},
                    "work_request": work_request.id,
                    "expect_type": expect_type,
                }

                serializer = LookupSingleSerializer(data=data)

                self.assertTrue(serializer.is_valid())
                self.assertEqual(
                    serializer.validated_data["expect_type"],
                    validated_expect_type,
                )


class LookupMultipleSerializerTests(TestCase):
    """Tests for LookupMultipleSerializer."""

    def test_expect_type(self) -> None:
        """Test possible values of `expect_type`."""
        work_request = self.playground.create_work_request()

        for expect_type, validated_expect_type in (
            # All the items in LookupChildType are valid.
            *((str(item), item) for item in LookupChildType),
            # These specific values are mapped to their corresponding items
            # in LookupChildType, for compatibility with old clients.
            ("b", LookupChildType.BARE),
            ("a", LookupChildType.ARTIFACT),
            ("c", LookupChildType.COLLECTION),
        ):
            with self.subTest(expect_type=expect_type):
                data = {
                    "lookup": "x",
                    "work_request": work_request.id,
                    "expect_type": expect_type,
                }

                serializer = LookupMultipleSerializer(data=data)

                self.assertTrue(serializer.is_valid())
                self.assertEqual(
                    serializer.validated_data["expect_type"],
                    validated_expect_type,
                )


class WorkflowTemplateSerializerTests(TestCase):
    """Tests for WorkflowTemplateSerializer."""

    def test_valid_data(self) -> None:
        """Test valid data."""
        context.set_scope(self.playground.get_default_scope())
        data = {
            "name": "wt",
            "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
            "task_name": "noop",
        }

        serializer = WorkflowTemplateSerializer(data=data)

        self.assertTrue(serializer.is_valid())
        self.assertEqual(
            serializer.validated_data,
            {**data, "workspace": default_workspace()},
        )

    def test_invalid_task_name(self) -> None:
        """The serializer rejects an invalid task name."""
        context.set_scope(self.playground.get_default_scope())
        data = {
            "name": "wt",
            "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
            "task_name": "nonexistent",
        }

        serializer = WorkflowTemplateSerializer(data=data)

        self.assertFalse(serializer.is_valid())

    def test_workspace_name_resolved_in_current_scope(self) -> None:
        """The given workspace name is resolved in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        scope3 = self.playground.get_or_create_scope("scope3")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="common-name", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="common-name", public=True
        )
        data = {
            "name": "wt",
            "workspace": "common-name",
            "task_name": "noop",
        }

        for workspace in (workspace1, workspace2):
            with context.local():
                context.set_scope(workspace.scope)
                serializer = WorkflowTemplateSerializer(data=data)
                self.assertTrue(serializer.is_valid())
                self.assertEqual(
                    serializer.validated_data["workspace"], workspace
                )

        with context.local():
            context.set_scope(scope3)
            serializer = WorkflowTemplateSerializer(data=data)
            self.assertFalse(serializer.is_valid())
