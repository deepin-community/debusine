# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Debsign task."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from django.test import TestCase as DjangoTestCase
from django.test import TransactionTestCase, override_settings
from nacl.public import PrivateKey

from debusine.artifacts import (
    LocalArtifact,
    SourcePackage,
    Upload,
    WorkRequestDebugLogs,
)
from debusine.artifacts.models import (
    ArtifactCategory,
    DebianUpload,
    EmptyArtifactData,
)
from debusine.assets.models import AssetCategory
from debusine.client.models import (
    ArtifactResponse,
    AssetPermissionCheckResponse,
    RemoteArtifact,
)
from debusine.signing.db.models import Key
from debusine.signing.gnupg import gpg_ephemeral_context
from debusine.signing.models import SigningMode
from debusine.signing.tasks import Debsign
from debusine.signing.tasks.models import DebsignDynamicData
from debusine.signing.tasks.tests.test_sign import SignTestMixin
from debusine.tasks.models import WorkerType
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabase
from debusine.test import TestCase
from debusine.test.test_utils import create_remote_artifact


class DebsignTests(SignTestMixin, TestCase, DjangoTestCase):
    """Unit tests for the :py:class:`Debsign` task."""

    def test_analyze_worker(self) -> None:
        """Test the analyze_worker() method."""
        self.mock_is_command_available({"debsign": True})
        task = Debsign(task_data={"unsigned": 2, "key": 1})
        metadata = task.analyze_worker()
        self.assertEqual(metadata["signing:debsign:available"], True)

    def test_analyze_worker_debsign_not_available(self) -> None:
        """analyze_worker() handles debsign not being available."""
        self.mock_is_command_available({"debsign": False})
        task = Debsign(task_data={"unsigned": 2, "key": 1})
        metadata = task.analyze_worker()
        self.assertEqual(metadata["signing:debsign:available"], False)

    def test_can_run_on(self) -> None:
        """can_run_on returns True if debsign is available."""
        task = Debsign(task_data={"unsigned": 2, "key": 1})
        self.assertTrue(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:debsign:available": True,
                    "signing:debsign:version": task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_mismatched_task_version(self) -> None:
        """can_run_on returns False for mismatched task versions."""
        task = Debsign(task_data={"unsigned": 2, "key": 1})
        self.assertFalse(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:debsign:available": True,
                    "signing:debsign:version": task.TASK_VERSION + 1,
                }
            )
        )

    def test_can_run_on_missing_tool(self) -> None:
        """can_run_on returns False if debsign is not available."""
        task = Debsign(task_data={"unsigned": 2, "key": 1})
        self.assertFalse(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:debsign:available": False,
                    "signing:debsign:version": task.TASK_VERSION,
                }
            )
        )

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        task_db = FakeTaskDatabase(
            single_lookups={
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.UPLOAD,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Architecture": "source",
                            "Files": [{"name": "hello_1.0.dsc"}],
                        },
                    ),
                ),
            }
        )

        task = Debsign(task_data={"unsigned": 2, "key": "ABC123"})
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            DebsignDynamicData(unsigned_id=2),
        )

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        task = Debsign(task_data={"unsigned": 1, "key": "ABC123"})
        self.assertEqual(task.get_input_artifacts_ids(), [])

        task.dynamic_data = DebsignDynamicData(unsigned_id=1)
        self.assertEqual(task.get_input_artifacts_ids(), [1])

    def test_fetch_input_wrong_category(self) -> None:
        """fetch_input checks the category of the unsigned artifact."""
        temp_path = self.create_temporary_directory()
        dsc_path = temp_path / "foo.dsc"
        self.write_dsc_file(dsc_path, [])
        source_package = SourcePackage.create(
            name="foo", version="1.0", files=[dsc_path]
        )
        source_package_id = 1
        source_package_response = self.make_artifact_response(
            source_package, source_package_id
        )

        task = Debsign(
            task_data={"unsigned": source_package_id, "key": "ABC123"},
            dynamic_task_data={"unsigned_id": source_package_id},
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = source_package_response
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            "Expected unsigned artifact of category debian:upload; got "
            "debian:source-package\n",
        )
        debusine_mock.download_artifact.assert_called_once_with(
            source_package_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_missing_key(self) -> None:
        """fetch_input requires the signing key to exist in the database."""
        fingerprint = "0" * 64
        directory = self.create_temporary_directory()
        changes_file = directory / "foo.changes"
        self.write_changes_file(changes_file, [])
        upload = Upload.create(changes_file=changes_file)
        upload_id = 2
        upload_response = self.make_artifact_response(upload, upload_id)

        task = Debsign(
            task_data={"unsigned": upload_id, "key": fingerprint},
            dynamic_task_data={"unsigned_id": upload_id},
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = upload_response
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            f"Signing key openpgp:{fingerprint} does not exist\n",
        )
        debusine_mock.download_artifact.assert_called_once_with(
            upload_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_permission_denied(self) -> None:
        key = Key.objects.create(
            purpose=Key.Purpose.OPENPGP,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        directory = self.create_temporary_directory()
        changes_file = directory / "foo.changes"
        self.write_changes_file(changes_file, [])
        upload = Upload.create(changes_file=changes_file)
        upload_id = 2
        upload_response = self.make_artifact_response(upload, upload_id)

        task = Debsign(
            task_data={"unsigned": upload_id, "key": key.fingerprint},
            dynamic_task_data={"unsigned_id": upload_id},
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = upload_response
        debusine_mock.asset_permission_check.return_value = (
            AssetPermissionCheckResponse(
                has_permission=False,
                username="testuser",
                user_id=123,
                resource={},
            )
        )
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            f"User testuser does not have permission to sign "
            f"{upload_id} with openpgp:{key.fingerprint}.\n",
        )
        debusine_mock.download_artifact.assert_called_once_with(
            upload_id, destination, tarball=mock.ANY
        )

    def test_run_error(self) -> None:
        """run() raises an exception if signing fails."""
        key = Key.objects.create(
            purpose=Key.Purpose.OPENPGP,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        download_directory = self.create_temporary_directory()
        execute_directory = self.create_temporary_directory()
        (changes_file := download_directory / "foo.changes").touch()
        task = Debsign(task_data={"unsigned": 2, "key": key.fingerprint})
        task.work_request_id = 1
        task._files = self.make_files_response({"foo.changes": changes_file})
        task._key = key
        task._permission_check = AssetPermissionCheckResponse(
            has_permission=True, username="testuser", user_id=123, resource={}
        )
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with (
            mock.patch(
                "debusine.signing.tasks.debsign.sign",
                side_effect=ValueError("Boom"),
            ),
            self.assertRaisesRegex(ValueError, "Boom"),
        ):
            task.run(execute_directory)

        self.assertIsNone(task._signed_changes_path)

    def test_run_success(self) -> None:
        """run() records a successful result if signing succeeds."""
        key = Key.objects.create(
            purpose=Key.Purpose.OPENPGP,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        download_directory = self.create_temporary_directory()
        execute_directory = self.create_temporary_directory()
        (changes_file := download_directory / "foo.changes").touch()
        task = Debsign(task_data={"unsigned": 2, "key": key.fingerprint})
        task.work_request_id = 1
        task._files = self.make_files_response({"foo.changes": changes_file})
        task._key = key
        task._permission_check = AssetPermissionCheckResponse(
            has_permission=True,
            username="testuser",
            user_id=123,
            resource={"package": "foo"},
        )
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with mock.patch("debusine.signing.tasks.debsign.sign") as mock_sign:
            self.assertTrue(task.run(execute_directory))

        self.assertEqual(
            task._signed_changes_path,
            execute_directory / "output" / "foo.changes",
        )
        mock_sign.assert_called_once_with(
            [key],
            execute_directory / "input" / "foo.changes",
            execute_directory / "output" / "foo.changes",
            SigningMode.DEBSIGN,
            1,
            username="testuser",
            user_id=123,
            resource={"package": "foo"},
            log_file=mock.ANY,
        )


class DebsignIntegrationTests(SignTestMixin, TestCase, TransactionTestCase):
    """Integration tests for the :py:class:`Debsign` task."""

    def generate_signing_key(self, service_private_key: PrivateKey) -> Key:
        """Generate a signing key."""
        with (
            override_settings(
                DEBUSINE_SIGNING_PRIVATE_KEYS=[service_private_key]
            ),
            open(os.devnull, "wb") as log_file,
        ):
            key = Key.objects.generate(
                Key.Purpose.OPENPGP, "An OpenPGP key", 1, log_file
            )
        return key

    def make_upload(self) -> tuple[Path, Path, Upload]:
        """Prepare a minimal upload; return (dsc, changes, artifact)."""
        temp_path = self.create_temporary_directory()
        dsc_path = temp_path / "foo.dsc"
        self.write_dsc_file(dsc_path, [])
        changes_path = temp_path / "foo.changes"
        self.write_changes_file(changes_path, [dsc_path])
        upload = Upload.create(changes_file=changes_path)
        return dsc_path, changes_path, upload

    def test_execute_success(self) -> None:
        """Integration test: sign using a real OpenPGP key."""
        service_private_key = PrivateKey.generate()
        key = self.generate_signing_key(service_private_key)
        dsc_path, changes_path, upload = self.make_upload()
        upload_id = 2
        upload_response = self.make_artifact_response(upload, upload_id)
        uploaded_paths: list[Path] = []
        uploaded_upload: Upload | None = None
        output_path = self.create_temporary_directory()
        uploaded_artifact_ids = {
            ArtifactCategory.UPLOAD: 3,
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: 4,
        }

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            assert artifact_id == upload_id
            shutil.copy(dsc_path, destination)
            shutil.copy(changes_path, destination)
            return upload_response

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            nonlocal uploaded_upload
            for path in local_artifact.files.values():
                uploaded_paths.append(path)
                shutil.copy(path, output_path)
            if local_artifact.category == ArtifactCategory.UPLOAD:
                [changes_path] = [
                    path
                    for path in local_artifact.files.values()
                    if path.name.endswith(".changes")
                ]
                uploaded_upload = Upload.create(changes_file=changes_path)
            return create_remote_artifact(
                id=uploaded_artifact_ids[local_artifact.category],
                workspace="System",
            )

        task = Debsign(
            task_data={"unsigned": upload_id, "key": key.fingerprint},
            dynamic_task_data={
                "unsigned_id": upload_id,
            },
        )
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.side_effect = download_artifact
        debusine_mock.upload_artifact.side_effect = upload_artifact
        debusine_mock.asset_permission_check.return_value = (
            AssetPermissionCheckResponse(
                has_permission=True,
                username="testuser",
                user_id=123,
                resource={},
            )
        )
        task.work_request_id = 1
        task.workspace_name = "System"
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory

        with override_settings(
            DEBUSINE_SIGNING_PRIVATE_KEYS=[service_private_key]
        ):
            self.assertTrue(task.execute())

        debusine_mock.asset_permission_check.assert_called_once_with(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=f"openpgp:{key.fingerprint}",
            permission_name="sign_with",
            artifact_id=upload_id,
            work_request_id=task.work_request_id,
            workspace=task.workspace_name,
        )

        # The task uploaded an artifact with the expected properties.
        self.assertEqual(len(uploaded_paths), 3)
        debusine_mock.upload_artifact.assert_has_calls(
            [
                mock.call(uploaded_upload, workspace="System", work_request=1),
                mock.call(
                    WorkRequestDebugLogs(
                        category=WorkRequestDebugLogs._category,
                        data=EmptyArtifactData(),
                        files={
                            "cmd-output.log": (
                                Path(debug_log_files_directory.name)
                                / "cmd-output.log"
                            )
                        },
                        content_types={
                            "cmd-output.log": "text/plain; charset=utf-8"
                        },
                    ),
                    workspace="System",
                    work_request=1,
                ),
            ]
        )
        debusine_mock.relation_create.assert_any_call(
            uploaded_artifact_ids[ArtifactCategory.UPLOAD],
            upload_id,
            "relates-to",
        )

        # The file was properly signed.
        with (
            TemporaryDirectory(prefix="debusine-tests-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            import_result = ctx.key_import(bytes(key.public_key))
            assert getattr(import_result, "imported", 0) == 1
            subprocess.run(
                [
                    "dscverify",
                    "--keyring",
                    Path(ctx.home_dir, "pubring.kbx"),
                    output_path / "foo.changes",
                ],
                env={**os.environ, "GNUPGHOME": ctx.home_dir},
                check=True,
                stdout=subprocess.DEVNULL,
            )

    def test_execute_failure(self) -> None:
        """If execution fails, the task does not upload an output artifact."""
        service_private_key = PrivateKey.generate()
        key = self.generate_signing_key(service_private_key)
        dsc_path, changes_path, upload = self.make_upload()
        upload_id = 2
        upload_response = self.make_artifact_response(upload, upload_id)

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            assert artifact_id == upload_id
            shutil.copy(dsc_path, destination)
            shutil.copy(changes_path, destination)
            return upload_response

        task = Debsign(
            task_data={
                "unsigned": upload_id,
                "key": key.fingerprint,
            },
            dynamic_task_data={
                "unsigned_id": upload_id,
            },
        )
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.side_effect = download_artifact
        task.work_request_id = 1
        task.workspace_name = "System"
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory

        with mock.patch(
            "debusine.signing.tasks.debsign.sign",
            side_effect=Exception("Signing failed"),
        ):
            self.assertFalse(task.execute())

        # Only a debug logs artifact was uploaded, not any signing output
        debusine_mock.upload_artifact.assert_called_once_with(
            WorkRequestDebugLogs(
                category=WorkRequestDebugLogs._category,
                data=EmptyArtifactData(),
                files={
                    "stages.log": (
                        Path(debug_log_files_directory.name) / "stages.log"
                    ),
                    "cmd-output.log": (
                        Path(debug_log_files_directory.name) / "cmd-output.log"
                    ),
                    "execution.log": (
                        Path(debug_log_files_directory.name) / "execution.log"
                    ),
                },
                content_types={
                    "stages.log": "text/plain; charset=utf-8",
                    "cmd-output.log": "text/plain; charset=utf-8",
                    "execution.log": "text/plain; charset=utf-8",
                },
            ),
            workspace="System",
            work_request=1,
        )

    def test_label(self) -> None:
        """Test get_label."""
        task = Debsign(task_data={"unsigned": 2, "key": 1})
        self.assertEqual(task.get_label(), "sign upload")
