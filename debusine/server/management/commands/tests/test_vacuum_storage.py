# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command vacuum_storage."""

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, TypedDict
from unittest import mock

from botocore import stub
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from debusine.artifacts.models import CollectionCategory
from debusine.assets.models import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
)
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    CollectionItem,
    File,
    FileInArtifact,
    FileStore,
    FileUpload,
)
from debusine.django.management.tests import call_command
from debusine.server.file_backend.s3 import S3FileBackend
from debusine.server.management.commands.vacuum_storage import Command
from debusine.test.django import (
    BaseDjangoTestCase,
    TestCase,
    TransactionTestCase,
)


class _FilesAndDirectories(TypedDict):
    """Dictionary containing files and directories for testing."""

    root: Path
    files: set[Path]
    directories: set[Path]


class VacuumStorageTestMixin(BaseDjangoTestCase):
    """Shared setup and teardown for vacuum_storage tests."""

    playground_memory_file_store = False
    two_days_ago = timezone.now() - timedelta(days=2)

    def setUp(self) -> None:
        """Set common objects and settings."""
        # Using override_settings does not work (perhaps because it's a
        # lambda calculated setting)
        super().setUp()
        self.debusine_store_directory = str(self.create_temporary_directory())
        self.original_debusine_store_directory = (
            settings.DEBUSINE_STORE_DIRECTORY
        )
        settings.DEBUSINE_STORE_DIRECTORY = self.debusine_store_directory

        # Use the default FileStore using a new base_directory
        self.file_store = FileStore.default()
        self.file_store.configuration["base_directory"] = (
            settings.DEBUSINE_STORE_DIRECTORY
        )
        self.file_store.save()

        # Use a new directory for the uploads
        self.debusine_upload_directory = str(self.create_temporary_directory())
        self.original_upload_directory = settings.DEBUSINE_UPLOAD_DIRECTORY
        settings.DEBUSINE_UPLOAD_DIRECTORY = self.debusine_upload_directory

    def tearDown(self) -> None:
        """Restore settings."""
        settings.DEBUSINE_STORE_DIRECTORY = (
            self.original_debusine_store_directory
        )
        settings.DEBUSINE_UPLOAD_DIRECTORY = self.original_upload_directory
        super().tearDown()


class VacuumStorageCommandTransactionTests(
    VacuumStorageTestMixin, TransactionTestCase
):
    """Tests for vacuum_storage commands that require transactions."""

    def create_incomplete_old_artifact(self) -> tuple[Artifact, FileUpload]:
        """Return Artifact: incomplete and old, and its FileUpload."""
        artifact, _ = self.playground.create_artifact(
            paths=["README-partial-upload", "README-no-upload"],
            skip_add_files_in_store=True,
            create_files=True,
        )
        artifact.created_at = self.two_days_ago
        artifact.save()

        # The file is not completed (skip_add_files_in_store=True
        # in create_artifact)
        file_in_artifact = artifact.fileinartifact_set.get(
            path="README-partial-upload"
        )
        assert file_in_artifact is not None
        self.assertFalse(file_in_artifact.complete)

        # Create a partial upload.
        # The last_activity_at field is the default "now",
        # but it will be deleted because the Artifact was
        # creating and still incomplete more than one day ago
        temp_file = tempfile.NamedTemporaryFile(
            prefix="missing-file-",
            dir=settings.DEBUSINE_UPLOAD_DIRECTORY,
            # Can be deleted by FileUpload.delete via its on_commit hook.
            delete=False,
        )
        temp_file.close()
        file_upload = FileUpload.objects.create(
            file_in_artifact=file_in_artifact, path=Path(temp_file.name).name
        )

        return artifact, file_upload

    def test_delete_old_incomplete_artifacts(self) -> None:
        """Delete old and incomplete artifacts."""
        artifact_1, file_upload_1 = self.create_incomplete_old_artifact()
        file_upload_1_path = file_upload_1.absolute_file_path()

        # artifact_2 will not be deleted: incomplete but recent
        artifact_2, _ = self.playground.create_artifact(
            paths=["README"], create_files=True
        )
        file_in_artifact_2 = artifact_2.fileinartifact_set.all().first()
        assert file_in_artifact_2 is not None
        file_in_artifact_2.complete = False
        file_in_artifact_2.save()
        self.playground.create_artifact_relation(artifact_1, artifact_2)

        # artifact_3 will not be deleted: old but complete
        artifact_3, _ = self.playground.create_artifact(paths=["README"])
        artifact_3.created_at = self.two_days_ago
        artifact_3.save()
        self.playground.create_artifact_relation(artifact_2, artifact_3)

        # artifact_4 will be deleted: target of an artifact relation (this
        # cannot happen normally any more:
        # ArtifactRelationsView.perform_create forbids it)
        artifact_4, file_upload_4 = self.create_incomplete_old_artifact()
        file_upload_4_path = file_upload_4.absolute_file_path()
        self.playground.create_artifact_relation(artifact_3, artifact_4)

        # artifact_5 will not be deleted: in a collection
        artifact_5, _ = self.create_incomplete_old_artifact()
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        CollectionItem.objects.create_from_artifact(
            artifact_5,
            parent_collection=collection,
            name="test",
            data={},
            created_by_user=self.playground.get_default_user(),
        )

        stdout, stderr, exitcode = call_command("vacuum_storage", verbosity=2)

        # artifact_1 and its outbound relation are deleted, as are
        # artifact_4 and its inbound relation
        self.assertQuerySetEqual(
            Artifact.objects.all(),
            {artifact_2, artifact_3, artifact_5},
            ordered=False,
        )
        self.assertQuerySetEqual(
            ArtifactRelation.objects.values_list("artifact", "target"),
            [(artifact_2.id, artifact_3.id)],
            ordered=False,
        )

        # There is no file (deleted as part of FileUpload.delete())
        self.assertFalse(file_upload_1_path.exists())
        self.assertFalse(file_upload_4_path.exists())

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            "Checking incomplete artifacts\n"
            f"Deleted incomplete artifact {artifact_1.id}\n"
            f"Deleted incomplete artifact {artifact_4.id}\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

    def test_delete_old_incomplete_artifacts_dryrun(self) -> None:
        """Do not delete old and incomplete artifact: dry-run."""
        artifact, file_upload = self.create_incomplete_old_artifact()
        file_upload_path = file_upload.absolute_file_path()

        stdout, stderr, exitcode = call_command("vacuum_storage", "--dry-run")

        # artifact is not deleted
        self.assertQuerySetEqual(Artifact.objects.all(), {artifact})

        # file_upload and file in disk are not deleted
        self.assertQuerySetEqual(FileUpload.objects.all(), {file_upload})
        self.assertTrue(file_upload_path.exists())

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)


class VacuumStorageCommandTests(VacuumStorageTestMixin, TestCase):
    """Tests for vacuum_storage command."""

    def create_s3_file_store(self, **kwargs: Any) -> FileStore:
        """Create an S3 `FileStore`."""
        provider_account = self.playground.create_cloud_provider_account_asset(
            data=AWSProviderAccountData(
                name="test",
                configuration=AWSProviderAccountConfiguration(
                    region_name="test-region"
                ),
                credentials=AWSProviderAccountCredentials(
                    access_key_id="access-key",
                    secret_access_key="secret-key",
                ),
            )
        )
        return FileStore.objects.create(
            name="s3",
            backend=FileStore.BackendChoices.S3,
            configuration={"bucket_name": "test-bucket"},
            provider_account=provider_account,
            **kwargs,
        )

    @contextmanager
    def stub_s3_file_backend(self, store: FileStore) -> Generator[stub.Stubber]:
        """Make a stub for an S3 file store."""
        backend = store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        stubber = stub.Stubber(backend.client)
        with (
            mock.patch.object(
                S3FileBackend, "_make_client", return_value=backend.client
            ),
            stubber,
        ):
            yield stubber

    def get_store_contents(self, store: FileStore) -> set[bytes]:
        """
        Return a set of the contents of all the files in a store.

        Since file stores are content-addressable, we assume this is a
        sufficient representation for convenient testing.
        """
        store_contents = set()
        for entry in store.get_backend_object().list_entries():
            with entry.get_temporary_local_path() as path:
                store_contents.add(path.read_bytes())
        return store_contents

    def test_delete_file_in_disk_not_in_store_dry_run(self) -> None:
        """
        Test vacuum_storage find file in disk not in DB.

        Also test that is not finding a file part of an artifact.
        """
        artifact, _ = self.playground.create_artifact(
            paths=["README"], create_files=True
        )

        orphan_file = self.create_temporary_file(
            directory=self.debusine_store_directory
        )
        self._set_modified_time_two_days_ago(orphan_file)

        stdout, stderr, exitcode = call_command(
            "vacuum_storage", "--dry-run", verbosity=2
        )

        # The file was not deleted: dry-run was used
        self.assertTrue(orphan_file.exists())

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            f"Deleted {orphan_file}\n"
            "Checking empty directories\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

    @staticmethod
    def get_local_file_path(artifact: Artifact, path_in_artifact: str) -> Path:
        """Return the local file path for the path_in_artifact in Artifact."""
        fileobj = artifact.fileinartifact_set.get(path=path_in_artifact).file
        file_backend = FileStore.default().get_backend_object()
        path = file_backend.get_local_path(fileobj)
        assert path is not None
        return path

    def test_delete_file_in_disk_not_in_db(self) -> None:
        """
        Test vacuum_storage find and delete file that is in disk but not in DB.

        Also test that file part of an artifact is not deleted.
        """
        path_in_artifact = "README"
        artifact, _ = self.playground.create_artifact(
            paths=[path_in_artifact], create_files=True
        )

        temporary_file = self.create_temporary_file(
            directory=self.debusine_store_directory
        )
        self._set_modified_time_two_days_ago(temporary_file)
        stdout, stderr, exitcode = call_command("vacuum_storage")

        # The file was deleted
        self.assertFalse(temporary_file.exists())

        # The file part of an artifact was not deleted
        local_file_path = self.get_local_file_path(artifact, path_in_artifact)
        self.assertTrue(local_file_path.exists())

        # No verbosity, no output
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

    def test_file_not_deleted_too_new(self) -> None:
        """Do not delete the file: it's too new."""
        temporary_file = self.create_temporary_file(
            directory=self.debusine_store_directory
        )
        call_command("vacuum_storage")

        # The file was not deleted
        self.assertTrue(temporary_file.exists())

    def test_delete_empty_directory_dry_run(self) -> None:
        """Report delete an empty directory (but dry-run: does not delete)."""
        empty_directory = self.create_temporary_directory(
            directory=self.debusine_store_directory
        )
        self._set_modified_time_two_days_ago(empty_directory)
        self.playground.create_artifact(paths=["README"], create_files=True)

        stdout, stderr, exitcode = call_command(
            "vacuum_storage", "--dry-run", verbosity=2
        )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"Deleted empty directory {empty_directory}\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        # Not deleted: --dry-run was used
        self.assertTrue(empty_directory.is_dir())

    def test_delete_empty_directory(self) -> None:
        """Delete an empty directory (and leave a non-empty directory)."""
        empty_dir = self.create_temporary_directory(
            directory=self.debusine_store_directory
        )
        self._set_modified_time_two_days_ago(empty_dir)
        path_in_artifact = "README"
        artifact, _ = self.playground.create_artifact(
            paths=[path_in_artifact],
            create_files=True,
            skip_add_files_in_store=False,
        )

        stdout, stderr, exitcode = call_command("vacuum_storage")

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        self.assertFalse(empty_dir.is_dir())

        # Directory of the file in the artifact exists
        # The file part of an artifact was not deleted
        local_file_path = self.get_local_file_path(artifact, path_in_artifact)
        self.assertTrue(local_file_path.parent.is_dir())

    def test_delete_empty_directory_too_new(self) -> None:
        """Directory is not deleted: it's too new."""
        empty_dir = self.create_temporary_directory(
            directory=self.debusine_store_directory
        )
        call_command("vacuum_storage")

        self.assertTrue(empty_dir.is_dir())

    def test_delete_directory_fails(self) -> None:
        """directory.rmdir() fails (file was probably added after the check)."""
        empty_dir = self.create_temporary_directory(
            directory=self.debusine_store_directory
        )
        self._set_modified_time_two_days_ago(empty_dir)

        rmdir_patcher = mock.patch("pathlib.Path.rmdir", autospec=True)
        rmdir_mocked = rmdir_patcher.start()
        rmdir_mocked.side_effect = OSError()
        self.addCleanup(rmdir_patcher.stop)
        stdout, stderr, exitcode = call_command("vacuum_storage")

        rmdir_mocked.assert_called_once()

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        self.assertTrue(empty_dir.is_dir())

    def test_apply_populate_policy_dry_run(self) -> None:
        """Populate a store: dry run."""
        artifact, _ = self.playground.create_artifact(
            paths=["1", "2", "3"], create_files=True
        )
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(
            s3_file_store, through_defaults={"populate": True}
        )

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2", {}, {"Bucket": "test-bucket"}
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", "--dry-run", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Populating {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Would copy {file.hash_digest.hex()} from "
                f"{self.file_store.name} to {s3_file_store.name}\n"
                for file in artifact.files.order_by("sha256")
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_populate_policy(self) -> None:
        """Populate a store."""
        artifact, _ = self.playground.create_artifact(
            paths=["1", "2", "3"], create_files=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(
            s3_file_store,
            through_defaults={"populate": True, "write_only": True},
        )

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )
            # _apply_populate_policy
            for file in files:
                stubber.add_response(
                    "put_object",
                    {},
                    {
                        "Bucket": "test-bucket",
                        "Key": f"{file.hash_digest.hex()}-{file.size}",
                        "Body": stub.ANY,
                        "ChecksumAlgorithm": "SHA256",
                    },
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {"Key": f"{file.hash_digest.hex()}-{file.size}"}
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Populating {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Copied {file.hash_digest.hex()} from "
                f"{self.file_store.name} to {s3_file_store.name}\n"
                for file in files
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_populate_policy_nothing_to_do(self) -> None:
        """If all files are already in a `populate` store, do nothing."""
        artifact, _ = self.playground.create_artifact(
            paths=["1", "2", "3"], create_files=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(
            s3_file_store, through_defaults={"populate": True}
        )
        now = timezone.now()
        s3_file_store.files.add(*files)

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_populate_policy_file_deleted_in_parallel(self) -> None:
        """Populating a store ignores files that were deleted in parallel."""
        artifact, _ = self.playground.create_artifact(
            paths=["1", "2", "3"], create_files=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(
            s3_file_store, through_defaults={"populate": True}
        )
        original_refresh_from_db = File.refresh_from_db

        def mock_file_refresh_from_db(file: File) -> None:
            if file == files[1]:
                artifact.files.remove(file)
                self.file_store.files.remove(file)
                file.delete()
            return original_refresh_from_db(file)

        with (
            self.stub_s3_file_backend(s3_file_store) as stubber,
            mock.patch.object(
                File,
                "refresh_from_db",
                autospec=True,
                side_effect=mock_file_refresh_from_db,
            ),
        ):
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )
            # _apply_populate_policy
            for file in (files[0], files[2]):
                stubber.add_response(
                    "put_object",
                    {},
                    {
                        "Bucket": "test-bucket",
                        "Key": f"{file.hash_digest.hex()}-{file.size}",
                        "Body": stub.ANY,
                        "ChecksumAlgorithm": "SHA256",
                    },
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {"Key": f"{file.hash_digest.hex()}-{file.size}"}
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Populating {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Copied {file.hash_digest.hex()} from "
                f"{self.file_store.name} to {s3_file_store.name}\n"
                for file in (files[0], files[2])
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_populate_policy_cannot_download(self) -> None:
        """Cannot populate files that have no other download file stores."""
        artifact, _ = self.playground.create_artifact(
            paths=["1", "2", "3"], create_files=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        self.assertIn(self.file_store, scope.file_stores.all())
        local_policies = scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        local_policies.write_only = True
        local_policies.save()
        scope.file_stores.add(
            s3_file_store, through_defaults={"populate": True}
        )
        now = timezone.now()

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Populating {s3_file_store.name}\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(
            stderr,
            "".join(
                f"{scope}: Wanted to populate {s3_file_store.name} with "
                f"{file.hash_digest.hex()}, but it has no other download file "
                f"stores\n"
                for file in files
            ),
        )
        self.assertEqual(exitcode, 1)

        stubber.assert_no_pending_responses()

    def test_apply_populate_policy_size_exceeded(self) -> None:
        """We stop populating a store when its size limits are reached."""
        artifact, _ = self.playground.create_artifact(
            paths=["1", "2", "3"], files_size=100, create_files=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=150)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(
            s3_file_store, through_defaults={"populate": True}
        )

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2", {"Contents": []}, {"Bucket": "test-bucket"}
            )
            # _apply_populate_policy
            stubber.add_response(
                "put_object",
                {},
                {
                    "Bucket": "test-bucket",
                    "Key": f"{files[0].hash_digest.hex()}-{files[0].size}",
                    "Body": stub.ANY,
                    "ChecksumAlgorithm": "SHA256",
                },
            )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {"Key": f"{file.hash_digest.hex()}-{file.size}"}
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Populating {s3_file_store.name}\n"
            f"{scope}: Copied {files[0].hash_digest.hex()} from "
            f"{self.file_store.name} to {s3_file_store.name}\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(
            stderr,
            f"{scope}: Stopping population of {s3_file_store.name} to avoid "
            f"exceeding size limits\n",
        )
        self.assertEqual(exitcode, 1)

        stubber.assert_no_pending_responses()

    def test_apply_drain_policy_dry_run(self) -> None:
        """Drain a store: dry run."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=10)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store, through_defaults={"drain": True})
        drained_artifact, _ = self.playground.create_artifact(
            paths=["1"], create_files=True
        )
        undrained_paths = {
            path: f"Test file {path}".encode() for path in ("2", "3")
        }
        undrained_artifact, _ = self.playground.create_artifact(
            paths=undrained_paths,
            create_files=True,
            skip_add_files_in_store=True,
        )
        files = File.objects.filter(
            artifact__in=(drained_artifact, undrained_artifact)
        ).order_by("sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)

        expected_stdout = [
            "Checking orphan files from stores\n",
            "Checking empty directories\n",
            f"{scope}: Draining {s3_file_store.name}\n",
        ]
        for file in File.objects.filter(
            artifact__in=(drained_artifact, undrained_artifact)
        ).order_by("sha256"):
            if file in drained_artifact.files.all():
                expected_stdout.append(
                    f"{scope}: Would remove {file.hash_digest.hex()} from "
                    f"{s3_file_store.name}\n"
                )
            else:
                expected_stdout.append(
                    f"{scope}: Would move {file.hash_digest.hex()} from "
                    f"{s3_file_store.name} to {self.file_store.name}\n"
                )
        expected_stdout += [
            "Checking incomplete artifacts\n",
            "Checking orphan files in upload directory\n",
            "Checking missing files from stores\n",
            "Checking missing files from upload\n",
        ]

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", "--dry-run", verbosity=2
            )

        self.assertEqual(stdout, "".join(expected_stdout))
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_drain_policy(self) -> None:
        """Drain a store."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=150)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store, through_defaults={"drain": True})
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        drained_artifact, _ = self.playground.create_artifact(
            paths={"1": paths["1"]}, create_files=True
        )
        undrained_artifact, _ = self.playground.create_artifact(
            paths={"2": paths["2"], "3": paths["3"]},
            create_files=True,
            skip_add_files_in_store=True,
        )
        files = File.objects.filter(
            artifact__in=(drained_artifact, undrained_artifact)
        ).order_by("sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)

        expected_stdout = [
            "Checking orphan files from stores\n",
            "Checking empty directories\n",
            f"{scope}: Draining {s3_file_store.name}\n",
        ]
        for file in files:
            if file in drained_artifact.files.all():
                expected_stdout.append(
                    f"{scope}: Removed {file.hash_digest.hex()} from "
                    f"{s3_file_store.name}\n"
                )
            else:
                expected_stdout.append(
                    f"{scope}: Moved {file.hash_digest.hex()} from "
                    f"{s3_file_store.name} to {self.file_store.name}\n"
                )
        expected_stdout += [
            "Checking incomplete artifacts\n",
            "Checking orphan files in upload directory\n",
            "Checking missing files from stores\n",
            "Checking missing files from upload\n",
        ]

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": f"{file.hash_digest.hex()}-{file.size}",
                            "LastModified": now,
                        }
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )
            # _apply_drain_policy
            for file in files:
                path = file.fileinartifact_set.get().path
                identifier = f"{file.sha256.hex()}-{file.size}"
                if file not in drained_artifact.files.all():
                    stubber.add_response(
                        "head_object",
                        {"ContentLength": len(paths[path])},
                        {"Bucket": "test-bucket", "Key": identifier},
                    )
                    stubber.add_response(
                        "get_object",
                        {"Body": BytesIO(paths[path])},
                        {"Bucket": "test-bucket", "Key": identifier},
                    )
                stubber.add_response(
                    "delete_object",
                    {},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(stdout, "".join(expected_stdout))
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

        self.assertEqual(
            self.get_store_contents(self.file_store), set(paths.values())
        )

    def test_apply_drain_policy_nothing_to_do(self) -> None:
        """If a `drain` store is already empty, do nothing."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=150)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store, through_defaults={"drain": True})

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2", {}, {"Bucket": "test-bucket"}
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_drain_policy_file_deleted_in_parallel(self) -> None:
        """Draining a store ignores files that were deleted in parallel."""
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store, through_defaults={"drain": True})
        now = timezone.now()
        s3_file_store.files.add(*files)
        original_refresh_from_db = File.refresh_from_db

        def mock_file_refresh_from_db(file: File) -> None:
            if file == files[1]:
                artifact.files.remove(file)
                s3_file_store.files.remove(file)
                file.delete()
            return original_refresh_from_db(file)

        with (
            self.stub_s3_file_backend(s3_file_store) as stubber,
            mock.patch.object(
                File,
                "refresh_from_db",
                autospec=True,
                side_effect=mock_file_refresh_from_db,
            ),
        ):
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": f"{file.hash_digest.hex()}-{file.size}",
                            "LastModified": now,
                        }
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )
            # _apply_drain_policy
            for file in (files[0], files[2]):
                path = file.fileinartifact_set.get().path
                identifier = f"{file.sha256.hex()}-{file.size}"
                stubber.add_response(
                    "head_object",
                    {"ContentLength": len(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "get_object",
                    {"Body": BytesIO(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "delete_object",
                    {},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Moved {file.hash_digest.hex()} from "
                f"{s3_file_store.name} to {self.file_store.name}\n"
                for file in (files[0], files[2])
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_drain_policy_drain_to(self) -> None:
        """The `drain_to` policy selects a target store."""
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by("sha256")
        scope = self.playground.get_default_scope()
        memory_file_store = FileStore.objects.create(
            name="memory",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "memory"},
        )
        s3_file_store = self.create_s3_file_store()
        self.assertEqual(
            scope.filestoreinscope_set.get(
                file_store=self.file_store
            ).upload_priority,
            100,
        )
        scope.file_stores.add(
            memory_file_store, through_defaults={"upload_priority": 0}
        )
        scope.file_stores.add(
            s3_file_store,
            through_defaults={"drain": True, "drain_to": "memory"},
        )
        now = timezone.now()
        s3_file_store.files.add(*files)

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": f"{file.hash_digest.hex()}-{file.size}",
                            "LastModified": now,
                        }
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )
            # _apply_drain_policy
            for file in files:
                path = file.fileinartifact_set.get().path
                identifier = f"{file.sha256.hex()}-{file.size}"
                stubber.add_response(
                    "head_object",
                    {"ContentLength": len(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "get_object",
                    {"Body": BytesIO(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "delete_object",
                    {},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Moved {file.hash_digest.hex()} from "
                f"{s3_file_store.name} to {memory_file_store.name}\n"
                for file in files
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

        self.assertEqual(
            self.get_store_contents(memory_file_store), set(paths.values())
        )

    def test_apply_drain_policy_cannot_upload(self) -> None:
        """Cannot drain files that have no other upload file stores."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=150)
        local_policies = scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        local_policies.read_only = True
        local_policies.save()
        scope.file_stores.add(s3_file_store, through_defaults={"drain": True})
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by("sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining {s3_file_store.name}\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(
            stderr,
            "".join(
                f"{scope}: Wanted to drain {s3_file_store.name} of "
                f"{file.hash_digest.hex()}, but it has no other upload file "
                f"stores\n"
                for file in files
            ),
        )
        self.assertEqual(exitcode, 1)

        stubber.assert_no_pending_responses()

        self.assertEqual(self.get_store_contents(self.file_store), set())

    def test_apply_drain_policy_cannot_upload_drain_to(self) -> None:
        """Raise an explicit error if the `drain_to` store is ineligible."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store()
        local_policies = scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        local_policies.read_only = True
        local_policies.save()
        scope.file_stores.add(
            s3_file_store,
            through_defaults={"drain": True, "drain_to": self.file_store.name},
        )
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by("sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining {s3_file_store.name}\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(
            stderr,
            "".join(
                f"{scope}: Wanted to drain {s3_file_store.name} of "
                f"{file.hash_digest.hex()}, but {self.file_store.name} cannot "
                f"accept it\n"
                for file in files
            ),
        )
        self.assertEqual(exitcode, 1)

        stubber.assert_no_pending_responses()

        self.assertEqual(self.get_store_contents(self.file_store), set())

    def test_apply_max_size_limits_dry_run(self) -> None:
        """Apply maximum size limits: dry run."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=20)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store)
        drained_artifact, _ = self.playground.create_artifact(
            paths=["1"], files_size=10, create_files=True
        )
        undrained_paths = {
            path: f"Test file {path}".encode() for path in ("2", "3")
        }
        undrained_artifact, _ = self.playground.create_artifact(
            paths=undrained_paths,
            create_files=True,
            skip_add_files_in_store=True,
        )
        files = File.objects.filter(
            artifact__in=(drained_artifact, undrained_artifact)
        ).order_by("sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)
        s3_file_store.refresh_from_db()

        expected_stdout = [
            "Checking orphan files from stores\n",
            "Checking empty directories\n",
            f"{scope}: Draining up to {s3_file_store.total_size - 20} bytes "
            f"from {s3_file_store.name}\n",
        ]
        for file in File.objects.filter(
            artifact__in=(drained_artifact, undrained_artifact)
        ).order_by("artifact__created_at", F("size").desc(), "sha256")[:2]:
            if file in drained_artifact.files.all():
                expected_stdout.append(
                    f"{scope}: Would remove {file.hash_digest.hex()} from "
                    f"{s3_file_store.name}\n"
                )
            else:
                expected_stdout.append(
                    f"{scope}: Would move {file.hash_digest.hex()} from "
                    f"{s3_file_store.name} to {self.file_store.name}\n"
                )
        expected_stdout += [
            "Checking incomplete artifacts\n",
            "Checking orphan files in upload directory\n",
            "Checking missing files from stores\n",
            "Checking missing files from upload\n",
        ]

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", "--dry-run", verbosity=2
            )

        self.assertEqual(stdout, "".join(expected_stdout))
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_max_size_limits(self) -> None:
        """Apply maximum size limits."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(max_size=20)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store)
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        drained_artifact, _ = self.playground.create_artifact(
            paths={"1": paths["1"]}, create_files=True
        )
        undrained_artifact, _ = self.playground.create_artifact(
            paths={"2": paths["2"], "3": paths["3"]},
            create_files=True,
            skip_add_files_in_store=True,
        )
        files = File.objects.filter(
            artifact__in=(drained_artifact, undrained_artifact)
        ).order_by("artifact__created_at", F("size").desc(), "sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)
        s3_file_store.refresh_from_db()

        expected_stdout = [
            "Checking orphan files from stores\n",
            "Checking empty directories\n",
            f"{scope}: Draining up to {s3_file_store.total_size - 20} bytes "
            f"from {s3_file_store.name}\n",
        ]
        for file in files[:2]:
            if file in drained_artifact.files.all():
                expected_stdout.append(
                    f"{scope}: Removed {file.hash_digest.hex()} from "
                    f"{s3_file_store.name}\n"
                )
            else:
                expected_stdout.append(
                    f"{scope}: Moved {file.hash_digest.hex()} from "
                    f"{s3_file_store.name} to {self.file_store.name}\n"
                )
        expected_stdout += [
            "Checking incomplete artifacts\n",
            "Checking orphan files in upload directory\n",
            "Checking missing files from stores\n",
            "Checking missing files from upload\n",
        ]
        expected_local_contents = set()

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": f"{file.hash_digest.hex()}-{file.size}",
                            "LastModified": now,
                        }
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )
            # _apply_max_size_limits
            for file in files[:2]:
                path = file.fileinartifact_set.get().path
                identifier = f"{file.sha256.hex()}-{file.size}"
                if file not in drained_artifact.files.all():
                    stubber.add_response(
                        "head_object",
                        {"ContentLength": len(paths[path])},
                        {"Bucket": "test-bucket", "Key": identifier},
                    )
                    stubber.add_response(
                        "get_object",
                        {"Body": BytesIO(paths[path])},
                        {"Bucket": "test-bucket", "Key": identifier},
                    )
                stubber.add_response(
                    "delete_object",
                    {},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                expected_local_contents.add(paths[path])
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": (
                                f"{files[2].hash_digest.hex()}-{files[2].size}"
                            ),
                            "LastModified": now,
                        }
                    ]
                },
                {"Bucket": "test-bucket"},
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(stdout, "".join(expected_stdout))
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

        self.assertEqual(
            self.get_store_contents(self.file_store), expected_local_contents
        )

    def test_apply_max_size_limits_nothing_to_do(self) -> None:
        """If a store with a maximum size is already empty, do nothing."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=150)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store)

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2", {}, {"Bucket": "test-bucket"}
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_max_size_limits_file_deleted_in_parallel(self) -> None:
        """Applying `max_size` ignores files that were deleted in parallel."""
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by(F("size").desc(), "sha256")
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=5, max_size=20)
        self.assertIn(self.file_store, scope.file_stores.all())
        scope.file_stores.add(s3_file_store)
        now = timezone.now()
        s3_file_store.files.add(*files)
        s3_file_store.refresh_from_db()
        original_refresh_from_db = File.refresh_from_db

        def mock_file_refresh_from_db(file: File) -> None:
            if file == files[1]:
                artifact.files.remove(file)
                s3_file_store.files.remove(file)
                file.delete()
            return original_refresh_from_db(file)

        with (
            self.stub_s3_file_backend(s3_file_store) as stubber,
            mock.patch.object(
                File,
                "refresh_from_db",
                autospec=True,
                side_effect=mock_file_refresh_from_db,
            ),
        ):
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": f"{file.hash_digest.hex()}-{file.size}",
                            "LastModified": now,
                        }
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )
            # _apply_max_size_limits
            for file in (files[0], files[2]):
                path = file.fileinartifact_set.get().path
                identifier = f"{file.sha256.hex()}-{file.size}"
                stubber.add_response(
                    "head_object",
                    {"ContentLength": len(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "get_object",
                    {"Body": BytesIO(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "delete_object",
                    {},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining up to {s3_file_store.total_size - 5} bytes "
            f"from {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Moved {file.hash_digest.hex()} from "
                f"{s3_file_store.name} to {self.file_store.name}\n"
                for file in (files[0], files[2])
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

    def test_apply_max_size_limits_drain_to(self) -> None:
        """The `drain_to` policy selects a target store."""
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by(F("size").desc(), "sha256")
        scope = self.playground.get_default_scope()
        memory_file_store = FileStore.objects.create(
            name="memory",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "memory"},
        )
        s3_file_store = self.create_s3_file_store(max_size=5)
        self.assertEqual(
            scope.filestoreinscope_set.get(
                file_store=self.file_store
            ).upload_priority,
            100,
        )
        scope.file_stores.add(
            memory_file_store, through_defaults={"upload_priority": 0}
        )
        scope.file_stores.add(
            s3_file_store,
            through_defaults={"drain_to": "memory"},
        )
        now = timezone.now()
        s3_file_store.files.add(*files)
        s3_file_store.refresh_from_db()

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files
            stubber.add_response(
                "list_objects_v2",
                {
                    "Contents": [
                        {
                            "Key": f"{file.hash_digest.hex()}-{file.size}",
                            "LastModified": now,
                        }
                        for file in files
                    ]
                },
                {"Bucket": "test-bucket"},
            )
            # _apply_max_size_limits
            for file in files:
                path = file.fileinartifact_set.get().path
                identifier = f"{file.sha256.hex()}-{file.size}"
                stubber.add_response(
                    "head_object",
                    {"ContentLength": len(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "get_object",
                    {"Body": BytesIO(paths[path])},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
                stubber.add_response(
                    "delete_object",
                    {},
                    {"Bucket": "test-bucket", "Key": identifier},
                )
            # _report_missing_files_stores_referenced_by_the_db
            stubber.add_response(
                "list_objects_v2", {}, {"Bucket": "test-bucket"}
            )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining up to {s3_file_store.total_size - 5} bytes "
            f"from {s3_file_store.name}\n"
            + "".join(
                f"{scope}: Moved {file.hash_digest.hex()} from "
                f"{s3_file_store.name} to {memory_file_store.name}\n"
                for file in files
            )
            + "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

        stubber.assert_no_pending_responses()

        self.assertEqual(
            self.get_store_contents(memory_file_store), set(paths.values())
        )

    def test_apply_max_size_limits_cannot_upload(self) -> None:
        """Cannot drain files that have no other upload file stores."""
        scope = self.playground.get_default_scope()
        s3_file_store = self.create_s3_file_store(soft_max_size=20)
        self.assertIn(self.file_store, scope.file_stores.all())
        local_policies = scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        local_policies.read_only = True
        local_policies.save()
        scope.file_stores.add(s3_file_store)
        paths = {path: f"Test file {path}".encode() for path in ("1", "2", "3")}
        artifact, _ = self.playground.create_artifact(
            paths=paths, create_files=True, skip_add_files_in_store=True
        )
        files = artifact.files.order_by("sha256")
        now = timezone.now()
        s3_file_store.files.add(*files)
        s3_file_store.refresh_from_db()

        with self.stub_s3_file_backend(s3_file_store) as stubber:
            # _delete_unreferenced_files, then
            # _report_missing_files_stores_referenced_by_the_db
            for _ in range(2):
                stubber.add_response(
                    "list_objects_v2",
                    {
                        "Contents": [
                            {
                                "Key": f"{file.hash_digest.hex()}-{file.size}",
                                "LastModified": now,
                            }
                            for file in files
                        ]
                    },
                    {"Bucket": "test-bucket"},
                )

            stdout, stderr, exitcode = call_command(
                "vacuum_storage", verbosity=2
            )

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            f"{scope}: Draining up to {s3_file_store.total_size - 20} bytes "
            f"from {s3_file_store.name}\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(
            stderr,
            "".join(
                f"{scope}: Wanted to drain {s3_file_store.name} of "
                f"{file.hash_digest.hex()}, but it has no other upload file "
                f"stores\n"
                for file in files
            ),
        )
        self.assertEqual(exitcode, 1)

        stubber.assert_no_pending_responses()

        self.assertEqual(self.get_store_contents(self.file_store), set())

    def test_delete_orphan_file_in_upload_directory_no_delete_dry_run(
        self,
    ) -> None:
        """File is orphan but is not deleted: --dry-run."""
        orphan_file = self.create_temporary_file(
            directory=self.debusine_upload_directory
        )
        self._set_modified_time_two_days_ago(orphan_file)
        stdout, stderr, exitcode = call_command(
            "vacuum_storage", "--dry-run", verbosity=2
        )

        self.assertTrue(orphan_file.exists())

        self.assertEqual(
            stdout,
            "Checking orphan files from stores\n"
            "Checking empty directories\n"
            "Checking incomplete artifacts\n"
            "Checking orphan files in upload directory\n"
            f"Deleted {orphan_file}\n"
            "Checking missing files from stores\n"
            "Checking missing files from upload\n",
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

    def test_delete_orphan_file_too_new(self) -> None:
        """File is orphan but not deleted: too new."""
        orphan_file = self.create_temporary_file(
            directory=self.debusine_upload_directory
        )
        stdout, stderr, exitcode = call_command("vacuum_storage")

        self.assertTrue(orphan_file.exists())

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exitcode, 0)

    def test_delete_orphan_file(self) -> None:
        """File is orphan and deleted."""
        orphan_file = self.create_temporary_file(
            directory=self.debusine_upload_directory
        )
        self._set_modified_time_two_days_ago(orphan_file)
        call_command("vacuum_storage")

        self.assertFalse(orphan_file.exists())

    def test_file_in_store_db_missing_from_storage(self) -> None:
        """File is referenced by the DB but not in storage."""
        path_in_artifact = "README"
        artifact, _ = self.playground.create_artifact(
            paths=[path_in_artifact], create_files=True
        )

        file_path = self.get_local_file_path(artifact, path_in_artifact)
        hash_hex = File.calculate_hash(file_path).hex()

        memory_file_store = FileStore.objects.create(
            name="memory",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "memory"},
        )
        memory_file = memory_file_store.get_backend_object().add_file(file_path)

        file_path.unlink()
        memory_file_store.get_backend_object().get_entry(memory_file).remove()

        stdout, stderr, exitcode = call_command("vacuum_storage")

        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            f"File in FileStore {self.file_store.name} but not in storage: "
            f"{file_path}\n"
            f"File in FileStore {memory_file_store.name} but not in storage: "
            f"{hash_hex}\n",
        )
        self.assertEqual(exitcode, 1)

    def test_file_in_upload_db_missing_from_disk(self) -> None:
        """File is referenced by the DB but not in disk for uploads."""
        artifact, _ = self.playground.create_artifact(
            paths=["README"], create_files=True
        )
        file_in_artifact = FileInArtifact.objects.get(artifact=artifact)

        file_path = "missing-file"
        FileUpload.objects.create(
            file_in_artifact=file_in_artifact, path=file_path
        )

        stdout, stderr, exitcode = call_command("vacuum_storage")

        debusine_upload_directory = Path(self.debusine_upload_directory)

        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            f"File in FileUpload but not on disk: "
            f"{debusine_upload_directory / file_path}\n",
        )
        self.assertEqual(exitcode, 1)

    def _create_files_and_directories(self) -> _FilesAndDirectories:
        root = self.create_temporary_directory()

        files = {
            self.create_temporary_file(directory=root),
            self.create_temporary_file(directory=root),
        }
        directory = self.create_temporary_directory(directory=root)

        files.add(self.create_temporary_file(directory=directory))

        return {"root": root, "files": files, "directories": {directory}}

    def test_list_directory_files_and_directories_raise_error(self) -> None:
        """_list_directory raise ValueError for incompatible parameters."""
        with self.assertRaisesRegex(
            ValueError,
            'Parameters "files_only" and "directories_only" are incompatible',
        ):
            Command._list_directory("/", files_only=True, directories_only=True)

    def test_files_and_directories(self) -> None:
        """_list_directory without filters: return all files and directories."""
        files_and_directories = self._create_files_and_directories()

        self.assertEqual(
            Command._list_directory(files_and_directories["root"]),
            files_and_directories["files"]
            | files_and_directories["directories"],
        )

    def test_files_only(self) -> None:
        """_list_directory(files_only) return only files (no directories)."""
        files_and_directories = self._create_files_and_directories()

        self.assertEqual(
            Command._list_directory(
                files_and_directories["root"], files_only=True
            ),
            files_and_directories["files"],
        )

    def test_directories_only(self) -> None:
        """_list_directory(directories_only) return only directories."""
        files_and_directories = self._create_files_and_directories()

        self.assertEqual(
            Command._list_directory(
                files_and_directories["root"], directories_only=True
            ),
            files_and_directories["directories"],
        )

    def test_filter_nothing(self) -> None:
        """_list_directory with exclude all filter: return nothing."""
        files_and_directories = self._create_files_and_directories()

        self.assertEqual(
            Command._list_directory(
                files_and_directories["root"],
                filters=[lambda x: False],  # noqa: U100
            ),
            set(),
        )

    def test_filter_all(self) -> None:
        """_list_directory (only files, include all filter): return files."""
        files_and_directories = self._create_files_and_directories()

        self.assertEqual(
            Command._list_directory(
                files_and_directories["root"],
                files_only=True,
                filters=[lambda x: True],  # noqa: U100,
            ),
            files_and_directories["files"],
        )

    @classmethod
    def _set_modified_time_two_days_ago(cls, path: Path) -> None:
        two_days_ago = cls.two_days_ago.timestamp()
        os.utime(path, (two_days_ago, two_days_ago))
