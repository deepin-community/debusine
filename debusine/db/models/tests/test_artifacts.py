# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the models."""

from datetime import timedelta
from pathlib import Path

import django.db.utils
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from debusine.artifacts.local_artifact import WorkRequestDebugLogs
from debusine.artifacts.models import ArtifactCategory, EmptyArtifactData
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    File,
    FileInArtifact,
    FileUpload,
    Scope,
    Workspace,
)
from debusine.db.playground.scenarios import DefaultContext
from debusine.test.django import (
    AllowAll,
    DenyAll,
    ListFilter,
    TestCase,
    override_permission,
)
from debusine.test.test_utils import data_generator


class ArtifactManagerTests(TestCase):
    """Tests for the ArtifactManager class."""

    scenario = DefaultContext()

    def test_create_from_local_artifact(self) -> None:
        """create_from_local_artifact() creates a matching Artifact."""
        temp_dir = self.create_temporary_directory()
        paths = [temp_dir / "1.log", temp_dir / "2.log"]
        paths[0].write_text("1\n")
        paths[1].write_text("2\n")
        local_artifact = WorkRequestDebugLogs.create(files=paths)
        work_request = self.playground.create_work_request()

        with context.disable_permission_checks():
            artifact = Artifact.objects.create_from_local_artifact(
                local_artifact,
                self.scenario.workspace,
                created_by_work_request=work_request,
            )

        self.assertEqual(artifact.category, local_artifact.category)
        self.assertEqual(artifact.workspace, self.scenario.workspace)
        self.assertEqual(artifact.data, local_artifact.data)
        self.assertEqual(artifact.created_by_work_request, work_request)
        self.assertEqual(artifact.fileinartifact_set.count(), 2)
        for file_in_artifact, path in zip(
            artifact.fileinartifact_set.order_by("id"), paths
        ):
            self.assertEqual(file_in_artifact.path, path.name)
            fileobj = file_in_artifact.file
            file_backend = self.scenario.scope.download_file_backend(fileobj)
            with file_backend.get_stream(fileobj) as file:
                self.assertEqual(file.read(), path.read_bytes())

    @context.disable_permission_checks()
    def test_not_expired_return_not_expired_artifacts(self) -> None:
        """not_expired() return only the not expired artifacts."""
        artifact_1 = Artifact.objects.create(
            workspace=self.scenario.workspace,
            expiration_delay=timedelta(days=1),
        )
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()
        artifact_2 = Artifact.objects.create(
            workspace=self.scenario.workspace,
            expiration_delay=timedelta(days=1),
        )
        artifact_3 = Artifact.objects.create(
            workspace=self.scenario.workspace, expiration_delay=timedelta(0)
        )

        self.assertQuerySetEqual(
            Artifact.objects.not_expired(timezone.now()),
            {artifact_2, artifact_3},
            ordered=False,
        )

    @context.disable_permission_checks()
    def test_expired_return_expired_artifacts(self) -> None:
        """expired() return only the expired artifacts."""
        artifact_1 = Artifact.objects.create(
            workspace=self.scenario.workspace,
            expiration_delay=timedelta(days=1),
        )
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()

        Artifact.objects.create(
            workspace=self.scenario.workspace,
            expiration_delay=timedelta(days=1),
        )
        Artifact.objects.create(
            workspace=self.scenario.workspace, expiration_delay=timedelta(0)
        )

        self.assertQuerySetEqual(
            Artifact.objects.expired(timezone.now()), {artifact_1}
        )

    def test_in_current_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter."""
        with override_permission(Workspace, "can_create_artifacts", AllowAll):
            artifact = Artifact.objects.create(
                workspace=self.scenario.workspace
            )

        with context.local():
            context.set_scope(self.scenario.scope)
            self.assertQuerySetEqual(
                Artifact.objects.in_current_scope(),
                [artifact],
            )

        scope1 = Scope.objects.create(name="Scope1")
        with context.local():
            context.set_scope(scope1)
            self.assertQuerySetEqual(
                Artifact.objects.in_current_scope(),
                [],
            )

    def test_in_current_scope_no_context_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter without scope set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "scope is not set"
        ):
            Artifact.objects.in_current_scope()

    def test_in_current_workspace(self) -> None:
        """Test the in_current_workspace() QuerySet filter."""
        with override_permission(Workspace, "can_create_artifacts", AllowAll):
            artifact = Artifact.objects.create(
                workspace=self.scenario.workspace
            )
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)

        with context.local():
            self.scenario.workspace.set_current()
            self.assertQuerySetEqual(
                Artifact.objects.in_current_workspace(),
                [artifact],
            )

        with context.disable_permission_checks():
            workspace1 = self.playground.create_workspace(
                name="other", public=True
            )
        with context.local():
            workspace1.set_current()
            self.assertQuerySetEqual(
                Artifact.objects.in_current_workspace(),
                [],
            )

    def test_in_current_workspace_no_context_workspace(self) -> None:
        """Test in_current_workspace() without workspace set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "workspace is not set"
        ):
            Artifact.objects.in_current_workspace()


class ArtifactTests(TestCase):
    """Tests for the Artifact class."""

    scenario = DefaultContext()

    def test_save_permissions(self) -> None:
        """Test permission checks on save."""
        with override_permission(Workspace, "can_create_artifacts", AllowAll):
            Artifact.objects.create(workspace=self.scenario.workspace)

        with (
            override_permission(Workspace, "can_create_artifacts", DenyAll),
            self.assertRaisesRegex(
                PermissionDenied,
                r"None cannot create artifacts in debusine/System",
            ),
        ):
            Artifact.objects.create(workspace=self.scenario.workspace)

    @context.disable_permission_checks()
    def test_default_values_of_fields(self) -> None:
        """Define the fields of the models and test their default values."""
        artifact = Artifact()

        self.assertEqual(artifact.category, "")

        artifact.category = "sample-type"

        self.assertEqual(artifact.data, {})

        with context.disable_permission_checks():
            artifact.workspace = self.playground.create_workspace(name="test")

        artifact.clean_fields()

        date_time_before_save = timezone.now()

        artifact.save()

        # date_time_before_save <= artifact.created_at <= timezone.now()
        self.assertLessEqual(date_time_before_save, artifact.created_at)
        self.assertLessEqual(artifact.created_at, timezone.now())

        # By default, the expiration_delay is None
        self.assertIsNone(artifact.expiration_delay)

        # By default, the created_by_work_request is None
        self.assertIsNone(artifact.created_by_work_request)

        # By default, the created_by is None
        self.assertIsNone(artifact.created_by)

    @context.disable_permission_checks()
    def test_expired(self) -> None:
        """Test Artifact.expired() method."""
        artifact = Artifact()

        artifact.created_at = timezone.now() - timedelta(days=2)
        artifact.expiration_delay = timedelta(days=1)
        self.assertTrue(artifact.expired(timezone.now()))

        artifact.expiration_delay = timedelta(0)
        self.assertFalse(artifact.expired(timezone.now()))

        artifact.created_at = timezone.now()
        artifact.expiration_delay = timedelta(days=1)
        self.assertFalse(artifact.expired(timezone.now()))

    @context.disable_permission_checks()
    def test_default_expiration(self) -> None:
        """Test workspace's default_expiration_delay."""
        self.assertEqual(
            self.scenario.workspace.default_expiration_delay, timedelta(0)
        )
        artifact = Artifact.objects.create(workspace=self.scenario.workspace)
        self.assertIsNone(artifact.expiration_delay)

        with context.disable_permission_checks():
            expiring_ws = self.playground.create_workspace(name="test")
        expiring_ws.default_expiration_delay = timedelta(days=7)
        test_creation_time = timezone.now()
        artifact = Artifact.objects.create(workspace=expiring_ws)
        delta = expiring_ws.default_expiration_delay
        assert artifact.expire_at is not None
        self.assertEqual(
            (artifact.expire_at - test_creation_time).days, delta.days
        )

    @context.disable_permission_checks()
    def test_str(self) -> None:
        """Test for Artifact.__str__."""
        artifact = Artifact.objects.create(workspace=self.scenario.workspace)
        self.assertEqual(
            artifact.__str__(),
            f"Id: {artifact.id} "
            f"Category: {artifact.category} "
            f"Workspace: {artifact.workspace.id}",
        )

    @context.disable_permission_checks()
    def test_get_absolute_url(self) -> None:
        """Test for Artifact.get_absolute_url."""
        artifact = Artifact.objects.create(workspace=self.scenario.workspace)
        self.assertEqual(
            artifact.get_absolute_url(),
            reverse(
                "workspaces:artifacts:detail",
                kwargs={
                    "wname": artifact.workspace.name,
                    "artifact_id": artifact.pk,
                },
            ),
        )

    def test_data_validation_error(self) -> None:
        """Assert that ValidationError is raised if data is not valid."""
        with override_permission(Workspace, "can_create_artifacts", AllowAll):
            artifact = Artifact.objects.create(
                category=ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
                workspace=self.scenario.workspace,
                data=[],
            )
        with self.assertRaises(ValidationError) as raised:
            artifact.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"data": ["data must be a dictionary"]},
        )

        artifact.category = "missing:does-not-exist"
        artifact.data = {}
        with self.assertRaises(ValidationError) as raised:
            artifact.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"category": ["missing:does-not-exist: invalid artifact category"]},
        )

        artifact.category = ArtifactCategory.PACKAGE_BUILD_LOG
        with self.assertRaises(ValidationError) as raised:
            artifact.full_clean()
        messages = raised.exception.message_dict
        self.assertCountEqual(messages.keys(), ["data"])
        self.assertRegex(
            messages["data"][0],
            r"invalid artifact data:"
            r" 3 validation errors for DebianPackageBuildLog\n",
        )

    @context.disable_permission_checks()
    def test_get_label_from_data(self) -> None:
        """Test getting artifact label from its data."""
        artifact = Artifact.objects.create(
            category=ArtifactCategory.SOURCE_PACKAGE,
            workspace=self.scenario.workspace,
            data={
                "name": "test",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {},
            },
        )
        self.assertEqual(artifact.get_label(), "test_1.0-1")

    @context.disable_permission_checks()
    def test_get_label_from_category(self) -> None:
        """Test defaults for artifact label."""
        artifact = Artifact.objects.create(
            category=ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
            workspace=self.scenario.workspace,
            data={},
        )
        self.assertEqual(
            artifact.get_label(), "debusine:work-request-debug-logs"
        )

    @context.disable_permission_checks()
    def test_get_label_from_data_reuse_model(self) -> None:
        """Test getting artifact label reusing the model instance."""
        artifact = Artifact.objects.create(
            category=ArtifactCategory.SOURCE_PACKAGE,
            workspace=self.scenario.workspace,
            data={
                "name": "test",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {},
            },
        )
        self.assertEqual(
            artifact.get_label(EmptyArtifactData()), "debian:source-package"
        )

    def test_can_display_delegate_to_workspace(self) -> None:
        """Test the can_display predicate on public workspaces."""
        with override_permission(Workspace, "can_create_artifacts", AllowAll):
            artifact = Artifact.objects.create(
                workspace=self.scenario.workspace
            )
        with override_permission(Workspace, "can_display", AllowAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                allowed=artifact,
            )
        with override_permission(Workspace, "can_display", DenyAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                denied=artifact,
            )


class FileInArtifactTests(TestCase):
    """Tests for the FileInArtifact class."""

    def create_file_in_artifact(
        self,
        fileobj: File | None = None,
        artifact: Artifact | None = None,
        path: str | None = None,
    ) -> FileInArtifact:
        """Return FileInArtifact to be used in tests."""
        if fileobj is None:
            fileobj = self.playground.create_file()

        if artifact is None:
            artifact, _ = self.playground.create_artifact()

        if path is None:
            path = "/usr/bin/test"

        return FileInArtifact.objects.create(
            artifact=artifact, file=fileobj, path=path
        )

    @context.disable_permission_checks()
    def test_artifact_path_unique_constraint(self) -> None:
        """Test two FileInArtifact cannot have the same artifact and path."""
        artifact, _ = self.playground.create_artifact()

        file1 = self.playground.create_file(b"contents1")
        file2 = self.playground.create_file(b"contents2")

        self.create_file_in_artifact(file1, artifact, "/usr/bin/test")

        with self.assertRaisesRegex(
            django.db.utils.IntegrityError,
            "db_fileinartifact_unique_artifact_path",
        ):
            self.create_file_in_artifact(file2, artifact, "/usr/bin/test")

    @context.disable_permission_checks()
    def test_str(self) -> None:
        """Test FileInArtifact.__str__."""
        file_in_artifact = self.create_file_in_artifact()

        self.assertEqual(
            file_in_artifact.__str__(),
            f"Id: {file_in_artifact.id} "
            f"Artifact: {file_in_artifact.artifact.id} "
            f"Path: {file_in_artifact.path} "
            f"File: {file_in_artifact.file.id}",
        )

    @context.disable_permission_checks()
    def test_get_absolute_url(self) -> None:
        """Test FileInArtifact.get_absolute_url."""
        file_in_artifact = self.create_file_in_artifact()
        self.assertEqual(
            file_in_artifact.get_absolute_url(),
            f"/debusine/System/artifact/{file_in_artifact.artifact.id}/file/"
            f"/usr/bin/test",
        )

    @context.disable_permission_checks()
    def test_get_absolute_url_raw(self) -> None:
        """Test FileInArtifact.get_absolute_url_raw."""
        file_in_artifact = self.create_file_in_artifact()
        self.assertEqual(
            file_in_artifact.get_absolute_url_raw(),
            f"/debusine/System/artifact/{file_in_artifact.artifact.id}/raw/"
            f"/usr/bin/test",
        )


class FileUploadTests(TestCase):
    """Tests for FileUpload class."""

    @context.disable_permission_checks()
    def setUp(self) -> None:
        """Set up basic objects for the tests."""
        self.file_upload = self.playground.create_file_upload()
        self.artifact = self.file_upload.file_in_artifact.artifact
        self.workspace = self.artifact.workspace
        self.file_path = self.file_upload.file_in_artifact.path
        self.file_size = self.file_upload.file_in_artifact.file.size

    def test_current_size_raise_no_file_in_artifact(self) -> None:
        """Test current_size() raise ValueError (FileInArtifact not found)."""
        wrong_path = "no-exist"
        with self.assertRaisesRegex(
            ValueError,
            f'^No FileInArtifact for Artifact {self.artifact.id} '
            f'and path "{wrong_path}"$',
        ):
            FileUpload.current_size(self.artifact, wrong_path)

    @context.disable_permission_checks()
    def test_current_size_raise_no_fileupload_for_file_in_artifact(
        self,
    ) -> None:
        """Test current_size() raise ValueError (FileUpload not found)."""
        artifact = Artifact.objects.create(
            category="test", workspace=self.workspace
        )
        file_in_artifact = FileInArtifact.objects.create(
            artifact=artifact,
            path="something",
            file=self.playground.create_file(),
        )

        with self.assertRaisesRegex(
            ValueError,
            f"^No FileUpload for FileInArtifact {file_in_artifact.id}$",
        ):
            FileUpload.current_size(artifact, "something")

    def test_current_size_return_last_position_received(self) -> None:
        """Test current_size() method return size of the file."""
        write_to_position = 30
        self.file_upload.absolute_file_path().write_bytes(
            next(data_generator(write_to_position))
        )
        self.assertEqual(
            FileUpload.current_size(
                artifact=self.artifact, path_in_artifact=self.file_path
            ),
            write_to_position,
        )

    def test_no_two_fileobj_to_same_path(self) -> None:
        """Test cannot create two FileUpload with same path."""
        data = next(data_generator(self.file_size))
        file = self.playground.create_file(data)
        new_file_in_artifact = FileInArtifact.objects.create(
            artifact=self.artifact, path="README-2", file=file
        )

        with self.assertRaisesRegex(
            django.db.utils.IntegrityError, "db_fileupload_path_key"
        ):
            FileUpload.objects.create(
                file_in_artifact=new_file_in_artifact,
                path=self.file_upload.path,
            )

    def test_delete(self) -> None:
        """Test FileUpload delete() try to unlink file."""
        file_path = self.file_upload.absolute_file_path()
        Path(file_path).write_bytes(next(data_generator(self.file_size)))

        self.assertTrue(file_path.exists())

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.file_upload.delete()

        self.assertFalse(file_path.exists())

        self.assertEqual(len(callbacks), 1)

    def test_delete_file_does_not_exist(self) -> None:
        """Test FileUpload delete() try to unlink file but did not exist."""
        file_path = self.file_upload.absolute_file_path()

        self.assertFalse(Path(file_path).exists())

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.file_upload.delete()

        self.assertEqual(len(callbacks), 1)

    def test_absolute_file_path(self) -> None:
        """Test absolute_file_path() is in DEBUSINE_UPLOAD_DIRECTORY."""
        self.assertEqual(
            self.file_upload.absolute_file_path(),
            Path(settings.DEBUSINE_UPLOAD_DIRECTORY) / self.file_upload.path,
        )

    def test_str(self) -> None:
        """Test __str__."""
        self.assertEqual(self.file_upload.__str__(), f"{self.file_upload.id}")


class ArtifactRelationManagerTests(TestCase):
    """Tests for the ArtifactRelationManager class."""

    scenario = DefaultContext()

    def test_in_current_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter."""
        artifact, _ = self.playground.create_artifact(
            workspace=self.scenario.workspace
        )
        target, _ = self.playground.create_artifact(
            workspace=self.scenario.workspace
        )
        relation = self.playground.create_artifact_relation(
            artifact=artifact, target=target
        )

        with context.local():
            context.set_scope(self.scenario.scope)
            self.assertQuerySetEqual(
                ArtifactRelation.objects.in_current_scope(), [relation]
            )

        scope1 = Scope.objects.create(name="Scope1")
        with context.local():
            context.set_scope(scope1)
            self.assertQuerySetEqual(
                ArtifactRelation.objects.in_current_scope(), []
            )

    def test_in_current_scope_no_context_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter without scope set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "scope is not set"
        ):
            ArtifactRelation.objects.in_current_scope()


class ArtifactRelationTests(TestCase):
    """Implement ArtifactRelation tests."""

    scenario = DefaultContext()

    @context.disable_permission_checks()
    def setUp(self) -> None:
        """Initialize test object."""
        self.artifact_1, _ = self.playground.create_artifact()
        self.artifact_2 = Artifact.objects.create(
            workspace=self.artifact_1.workspace,
            category=self.artifact_1.category,
        )

    def test_type_not_valid_raise_validation_error(self) -> None:
        """Assert that ValidationError is raised if type is not valid."""
        artifact_relation = ArtifactRelation.objects.create(
            artifact=self.artifact_1,
            target=self.artifact_2,
            type="wrong-type",
        )
        with self.assertRaises(ValidationError):
            artifact_relation.full_clean()

    def test_str(self) -> None:
        """Assert __str__ return expected value."""
        artifact_relation = self.playground.create_artifact_relation(
            artifact=self.artifact_1, target=self.artifact_2
        )
        self.assertEqual(
            str(artifact_relation),
            f"{self.artifact_1.id} {ArtifactRelation.Relations.RELATES_TO} "
            f"{self.artifact_2.id}",
        )

    def test_can_display_delegate_to_workspace(self) -> None:
        """Test the can_display predicate on public workspaces."""
        artifact_relation = self.playground.create_artifact_relation(
            artifact=self.artifact_1, target=self.artifact_2
        )
        with override_permission(Workspace, "can_display", AllowAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                allowed=artifact_relation,
            )
        for allowed_artifact in (self.artifact_1, self.artifact_2):
            with override_permission(
                Workspace, "can_display", ListFilter, include=[allowed_artifact]
            ):
                self.assertPermission(
                    "can_display",
                    users=(AnonymousUser(), self.scenario.user),
                    denied=artifact_relation,
                )
        with override_permission(Workspace, "can_display", DenyAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                denied=artifact_relation,
            )
