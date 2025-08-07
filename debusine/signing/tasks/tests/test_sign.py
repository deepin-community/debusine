# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Sign task."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock, skipUnless

import gpg
from django.test import TestCase as DjangoTestCase
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from nacl.public import PrivateKey

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts import (
    LocalArtifact,
    SigningInputArtifact,
    SigningOutputArtifact,
    WorkRequestDebugLogs,
)
from debusine.artifacts.models import (
    ArtifactCategory,
    DebusineSigningInput,
    DebusineSigningOutput,
    EmptyArtifactData,
    SigningResult,
)
from debusine.assets import AssetCategory, KeyPurpose
from debusine.client.debusine import Debusine
from debusine.client.models import (
    ArtifactResponse,
    AssetPermissionCheckResponse,
    FileResponse,
    FilesResponseType,
    RemoteArtifact,
    StrMaxLength255,
)
from debusine.signing.db.models import Key
from debusine.signing.gnupg import gpg_ephemeral_context
from debusine.signing.models import SigningMode
from debusine.signing.tasks import BaseSigningTask, Sign
from debusine.signing.tasks.models import SignDynamicData
from debusine.tasks.models import LookupMultiple, WorkerType
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabase
from debusine.test import TestCase
from debusine.utils import calculate_hash, is_command_available

# Bare minimum that sbsigntool will recognize as a valid PE/COFF image.
# This is unlikely to work in any other context.
minimal_pecoff_image = (
    # DOS header:
    # DOS magic number
    b"MZ"
    # DOS header fields, ignored by sbsigntool
    + (b"\0" * 58)
    # Address of next header
    + b"\x80\0\0\0"
    # DOS message, ignored by sbsigntool
    + (b"\0" * 64)
    # PE header:
    # NT signature
    + b"PE\0\0"
    # AMD64 magic
    + b"\x64\x86"
    # Number of sections
    + b"\0\0"
    # PE header fields, ignored by sbsigntool
    + (b"\0" * 12)
    # Optional header size: 752
    + (b"\xf0\x02")
    # Flags, ignored by sbsigntool
    + b"\0\0"
    # Optional header:
    # PE32+ magic
    + b"\x0b\x02"
    # Optional header standard fields, ignored by sbsigntool
    + (b"\0" * 22)
    # Image base and section alignment, ignored by sbsigntool
    + (b"\0" * 12)
    # File alignment: 512
    + b"\0\2\0\0"
    # Optional header NT-specific fields, ignored by sbsigntool
    + (b"\0" * 20)
    # Size of headers: 1024
    + b"\0\4\0\0"
    # More optional header NT-specific fields, ignored by sbsigntool
    + (b"\0" * 48)
    # Space for five data directories (the fifth is the certificate table)
    + (b"\0" * 640)
    # Padding for alignment
    + (b"\0" * 120)
)


example_url = pydantic.parse_obj_as(pydantic.AnyUrl, "http://example.org/")


class SignTestMixin:
    """Helpers for signing tests."""

    def mock_debusine(self, task: BaseSigningTask[Any, Any]) -> Any:
        """Create a Debusine mock and configure a task for it."""
        debusine_mock = mock.create_autospec(spec=Debusine)
        task.configure_server_access(debusine_mock)
        return debusine_mock

    def make_files_response(self, files: dict[str, Path]) -> FilesResponseType:
        """Make a fake files response."""
        return FilesResponseType(
            {
                name: FileResponse(
                    size=path.stat().st_size,
                    checksums={
                        "sha256": pydantic.parse_obj_as(
                            StrMaxLength255,
                            calculate_hash(path, "sha256").hex(),
                        )
                    },
                    type="file",
                    url=example_url,
                )
                for name, path in files.items()
            }
        )

    def make_artifact_response(
        self, artifact: LocalArtifact[Any], artifact_id: int
    ) -> ArtifactResponse:
        """Make a fake artifact response."""
        return ArtifactResponse(
            id=artifact_id,
            workspace="System",
            category=artifact.category,
            created_at=timezone.now(),
            data=artifact.data.dict(),
            download_tar_gz_url=example_url,
            files_to_upload=[],
            files=self.make_files_response(artifact.files),
        )


class SignTests(SignTestMixin, TestCase, DjangoTestCase):
    """Unit tests for the :py:class:`Sign` task."""

    def test_analyze_worker(self) -> None:
        """Test the analyze_worker() method."""
        self.mock_is_command_available({"sbsign": True})
        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1}
        )
        metadata = task.analyze_worker()
        self.assertEqual(metadata["signing:sign:available:uefi"], True)

    def test_analyze_worker_sbsign_not_available(self) -> None:
        """analyze_worker() handles sbsign not being available."""
        self.mock_is_command_available({"sbsign": False})
        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1}
        )
        metadata = task.analyze_worker()
        self.assertEqual(metadata["signing:sign:available:uefi"], False)

    def test_can_run_on_uefi(self) -> None:
        """can_run_on returns True for UEFI if sbsign is available."""
        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1}
        )
        self.assertTrue(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:sign:available:uefi": True,
                    "signing:sign:version": task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_openpgp(self) -> None:
        """can_run_on always returns True for OpenPGP."""
        task = Sign(
            task_data={"purpose": KeyPurpose.OPENPGP, "unsigned": [2], "key": 1}
        )
        self.assertTrue(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:sign:version": task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_mismatched_task_version(self) -> None:
        """can_run_on returns False for mismatched task versions."""
        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1}
        )
        self.assertFalse(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:sign:available:uefi": True,
                    "signing:sign:version": task.TASK_VERSION + 1,
                }
            )
        )

    def test_can_run_on_uefi_missing_tool(self) -> None:
        """can_run_on returns False for UEFI if sbsign is not available."""
        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1}
        )
        self.assertFalse(
            task.can_run_on(
                {
                    "system:worker_type": WorkerType.SIGNING,
                    "signing:sign:available:uefi": False,
                    "signing:sign:version": task.TASK_VERSION,
                }
            )
        )

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        task_db = FakeTaskDatabase(
            multiple_lookups={
                # unsigned
                (LookupMultiple.parse_obj([2]), None): [
                    ArtifactInfo(
                        id=2,
                        category=ArtifactCategory.SIGNING_INPUT,
                        data=DebusineSigningInput(),
                    )
                ]
            },
        )

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": "ABC123",
            }
        )
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            SignDynamicData(
                unsigned_ids=[2], unsigned_binary_package_names=[None]
            ),
        )

    def test_compute_dynamic_data_copies_binary_package_name(self) -> None:
        """Dynamic data copies `binary_package_name` from `unsigned`."""
        task_db = FakeTaskDatabase(
            multiple_lookups={
                # unsigned
                (LookupMultiple.parse_obj([2]), None): [
                    ArtifactInfo(
                        id=2,
                        category=ArtifactCategory.SIGNING_INPUT,
                        data=DebusineSigningInput(binary_package_name="hello"),
                    )
                ]
            },
        )

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": "ABC123",
            }
        )
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            SignDynamicData(
                unsigned_ids=[2], unsigned_binary_package_names=["hello"]
            ),
        )

    def test_get_source_artifacts_ids(self) -> None:
        """Test get_source_artifacts_ids."""
        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": "ABC123",
            }
        )
        self.assertEqual(task.get_source_artifacts_ids(), [])

        task.dynamic_data = SignDynamicData(unsigned_ids=[2])
        self.assertEqual(task.get_source_artifacts_ids(), [2])

    def test_fetch_input_missing_trusted_cert(self) -> None:
        """fetch_input requires trusted_certs to match configuration."""
        trusted_cert = "0" * 64
        signing_input = SigningInputArtifact.create(
            [], self.create_temporary_directory(), trusted_certs=[trusted_cert]
        )

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            return self.make_artifact_response(signing_input, artifact_id)

        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1},
            dynamic_task_data={"unsigned_ids": [2]},
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.side_effect = download_artifact
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        with override_settings(DEBUSINE_SIGNING_TRUSTED_CERTS=[]):
            self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            f"This installation cannot sign objects that trust certificate "
            f"'{trusted_cert}'\n",
        )

    def test_fetch_input_trusted_cert(self) -> None:
        """fetch_input accepts trusted_certs if they match configuration."""
        trusted_cert = "0" * 64
        fingerprint = "1" * 64
        key = Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint=fingerprint,
            private_key={},
            public_key=b"",
        )
        signing_input = SigningInputArtifact.create(
            [], self.create_temporary_directory(), trusted_certs=[trusted_cert]
        )
        signing_input_id = 2
        signing_input_response = self.make_artifact_response(
            signing_input, signing_input_id
        )

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [signing_input_id],
                "key": fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": [signing_input_id],
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = signing_input_response
        destination = self.create_temporary_directory()

        with override_settings(DEBUSINE_SIGNING_TRUSTED_CERTS=[trusted_cert]):
            self.assertTrue(task.fetch_input(destination))

        self.assertEqual(
            task._files, {signing_input_id: signing_input_response.files}
        )
        self.assertEqual(task._key, key)
        debusine_mock.download_artifact.assert_called_once_with(
            signing_input_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_mismatched_purpose(self) -> None:
        """fetch_input requires purpose to match key.purpose."""
        fingerprint = "0" * 64
        Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint=fingerprint,
            private_key={},
        )

        signing_input = SigningInputArtifact.create(
            [], self.create_temporary_directory()
        )
        signing_input_id = 2
        signing_input_response = self.make_artifact_response(
            signing_input, signing_input_id
        )

        task = Sign(
            task_data={
                "purpose": KeyPurpose.OPENPGP,
                "unsigned": [signing_input_id],
                "key": fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": [signing_input_id],
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = signing_input_response
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
            signing_input_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_missing_key(self) -> None:
        """fetch_input requires the signing key to exist in the database."""
        fingerprint = "0" * 64
        signing_input = SigningInputArtifact.create(
            [], self.create_temporary_directory()
        )
        signing_input_id = 2
        signing_input_response = self.make_artifact_response(
            signing_input, signing_input_id
        )

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [signing_input_id],
                "key": fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": [signing_input_id],
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = signing_input_response
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            f"Signing key uefi:{fingerprint} does not exist\n",
        )
        debusine_mock.download_artifact.assert_called_once_with(
            signing_input_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_multiple_unsigned_ids(self) -> None:
        """fetch_input handles multiple unsigned artifacts."""
        fingerprint = "0" * 64
        key = Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint=fingerprint,
            private_key={},
            public_key=b"",
        )
        signing_inputs = [
            SigningInputArtifact.create([], self.create_temporary_directory())
            for _ in range(2)
        ]
        signing_input_ids = [2, 3]
        responses = {
            artifact_id: self.make_artifact_response(artifact, artifact_id)
            for artifact, artifact_id in (
                (signing_inputs[0], signing_input_ids[0]),
                (signing_inputs[1], signing_input_ids[1]),
            )
        }

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            return responses[artifact_id]

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": signing_input_ids,
                "key": fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": signing_input_ids,
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.side_effect = download_artifact
        destination = self.create_temporary_directory()

        with override_settings(DEBUSINE_SIGNING_TRUSTED_CERTS=[]):
            self.assertTrue(task.fetch_input(destination))

        self.assertEqual(
            task._files,
            {
                signing_input_id: responses[signing_input_id].files
                for signing_input_id in signing_input_ids
            },
        )
        self.assertEqual(task._key, key)

    def test_fetch_input_permission_denied(self) -> None:
        fingerprint = "0" * 64
        Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint=fingerprint,
            private_key={},
            public_key=b"",
        )
        signing_input = SigningInputArtifact.create(
            [], self.create_temporary_directory()
        )
        signing_input_id = 2
        signing_input_response = self.make_artifact_response(
            signing_input, signing_input_id
        )

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [signing_input_id],
                "key": fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": [signing_input_id],
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = signing_input_response
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
            f"{signing_input_id} with uefi:{fingerprint}.\n",
        )
        debusine_mock.asset_permission_check.assert_called_once_with(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=f"uefi:{fingerprint}",
            permission_name="sign_with",
            artifact_id=signing_input_id,
            work_request_id=task.work_request_id,
            workspace=task.workspace_name,
        )

    def test_run_error(self) -> None:
        """run() records an error result if signing fails."""
        key = Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        download_directory = self.create_temporary_directory()
        execute_directory = self.create_temporary_directory()
        (image := download_directory / "image").touch()
        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": key.fingerprint,
            }
        )
        task.work_request_id = 1
        task._files = {2: self.make_files_response({"image": image})}
        task._permission_checks = {
            2: AssetPermissionCheckResponse(
                has_permission=True,
                username="testuser",
                user_id=123,
                resource={},
            )
        }
        task._key = key
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with mock.patch.object(key, "sign", side_effect=Exception("Boom")):
            self.assertFalse(task.run(execute_directory))

        self.assertEqual(
            task._results,
            {2: [SigningResult(file="image", error_message="Boom")]},
        )

    def test_run_success(self) -> None:
        """run() records a successful result if signing succeeds."""
        key = Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        download_directory = self.create_temporary_directory()
        execute_directory = self.create_temporary_directory()
        (image := download_directory / "image").touch()
        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": key.fingerprint,
            }
        )
        task.work_request_id = 1
        task._files = {2: self.make_files_response({"image": image})}
        task._permission_checks = {
            2: AssetPermissionCheckResponse(
                has_permission=True,
                username="testuser",
                user_id=123,
                resource={},
            )
        }
        task._key = key
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with mock.patch.object(key, "sign") as mock_sign:
            self.assertTrue(task.run(execute_directory))

        self.assertEqual(
            task._results,
            {2: [SigningResult(file="image", output_file="image.sig")]},
        )
        mock_sign.assert_called_once_with(
            execute_directory / "input" / "image",
            execute_directory / "output" / "image.sig",
            SigningMode.DETACHED,
            1,
            username="testuser",
            user_id=123,
            resource={},
            log_file=mock.ANY,
        )

    def test_run_uefi_on_kmod(self) -> None:
        """run() skips kernel modules for UEFI signing."""
        key = Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        download_directory = self.create_temporary_directory()
        execute_directory = self.create_temporary_directory()
        (image := download_directory / "image").touch()
        (foo_ko := download_directory / "foo.ko").touch()
        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": key.fingerprint,
            }
        )
        task.work_request_id = 1
        task._files = {
            2: self.make_files_response({"image": image, "foo.ko": foo_ko})
        }
        task._permission_checks = {
            2: AssetPermissionCheckResponse(
                has_permission=True,
                username="testuser",
                user_id=123,
                resource={},
            )
        }
        task._key = key
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with mock.patch.object(key, "sign"):
            self.assertTrue(task.run(execute_directory))

        self.assertEqual(
            task._results,
            {
                2: [
                    SigningResult(file="image", output_file="image.sig"),
                    SigningResult(
                        file="foo.ko",
                        error_message=(
                            "Skipped kernel module (not yet supported)"
                        ),
                    ),
                ]
            },
        )

    def test_run_uefi_on_only_kmod(self) -> None:
        """run() fails if no files are signed."""
        key = Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint="0" * 64,
            private_key={},
            public_key=b"",
        )
        download_directory = self.create_temporary_directory()
        execute_directory = self.create_temporary_directory()
        (foo_ko := download_directory / "foo.ko").touch()
        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": key.fingerprint,
            }
        )
        task.work_request_id = 1
        task._files = {2: self.make_files_response({"foo.ko": foo_ko})}
        task._key = key
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with mock.patch.object(key, "sign"):
            self.assertFalse(task.run(execute_directory))

        self.assertEqual(
            task._results,
            {
                2: [
                    SigningResult(
                        file="foo.ko",
                        error_message=(
                            "Skipped kernel module (not yet supported)"
                        ),
                    ),
                ]
            },
        )


class SignIntegrationTests(SignTestMixin, TestCase, TransactionTestCase):
    """Integration tests for the :py:class:`Sign` task."""

    @skipUnless(is_command_available("sbsign"), "requires sbsign")
    def test_uefi(self) -> None:
        """Integration test: sign using a real UEFI key."""
        service_private_key = PrivateKey.generate()
        with (
            override_settings(
                DEBUSINE_SIGNING_PRIVATE_KEYS=[service_private_key]
            ),
            open(os.devnull, "wb") as log_file,
        ):
            key = Key.objects.generate(
                Key.Purpose.UEFI, "A UEFI key", 1, log_file
            )
        temp_path = self.create_temporary_directory()
        (minimal_pecoff_path := temp_path / "image").write_bytes(
            minimal_pecoff_image
        )
        (minimal_pecoff_path2 := temp_path / "image2").write_bytes(
            minimal_pecoff_image[:-1] + b"\0"
        )
        signing_inputs = [
            SigningInputArtifact.create([path], temp_path)
            for path in (minimal_pecoff_path, minimal_pecoff_path2)
        ]
        signing_input_ids = [2, 3]
        responses = {
            artifact_id: self.make_artifact_response(artifact, artifact_id)
            for artifact, artifact_id in (
                (signing_inputs[0], signing_input_ids[0]),
                (signing_inputs[1], signing_input_ids[1]),
            )
        }
        uploaded_paths: list[Path] = []
        output_path = self.create_temporary_directory()
        signing_output_ids = [4, 5]
        work_request_debug_logs_id = 6

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            if artifact_id == signing_input_ids[0]:
                shutil.copy(minimal_pecoff_path, destination)
            elif artifact_id == signing_input_ids[1]:
                shutil.copy(minimal_pecoff_path2, destination)
            else:  # pragma: no cover
                pass
            return responses[artifact_id]

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            nonlocal uploaded_paths
            for path in local_artifact.files.values():
                uploaded_paths.append(path)
                shutil.copy(path, output_path)
            match (local_artifact.category, list(local_artifact.files)[0]):
                case (ArtifactCategory.SIGNING_OUTPUT, "image.sig"):
                    artifact_id = signing_output_ids[0]
                case (ArtifactCategory.SIGNING_OUTPUT, "image2.sig"):
                    artifact_id = signing_output_ids[1]
                case (ArtifactCategory.WORK_REQUEST_DEBUG_LOGS, _):
                    artifact_id = work_request_debug_logs_id
                case _ as unreachable:
                    raise AssertionError(f"unexpected upload: {unreachable}")
            return RemoteArtifact(id=artifact_id, workspace="System")

        task = Sign(
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": signing_input_ids,
                "key": key.fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": signing_input_ids,
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

        # The task uploaded an artifact with the expected properties.
        self.assertEqual(len(uploaded_paths), 3)
        debusine_mock.upload_artifact.assert_has_calls(
            [
                mock.call(
                    SigningOutputArtifact(
                        category=SigningOutputArtifact._category,
                        files={"image.sig": uploaded_paths[0]},
                        data=DebusineSigningOutput(
                            purpose=KeyPurpose.UEFI,
                            fingerprint=key.fingerprint,
                            results=[
                                SigningResult(
                                    file="image", output_file="image.sig"
                                )
                            ],
                        ),
                    ),
                    workspace="System",
                    work_request=1,
                ),
                mock.call(
                    SigningOutputArtifact(
                        category=SigningOutputArtifact._category,
                        files={"image2.sig": uploaded_paths[1]},
                        data=DebusineSigningOutput(
                            purpose=KeyPurpose.UEFI,
                            fingerprint=key.fingerprint,
                            results=[
                                SigningResult(
                                    file="image2", output_file="image2.sig"
                                )
                            ],
                        ),
                    ),
                    workspace="System",
                    work_request=1,
                ),
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
                    ),
                    workspace="System",
                    work_request=1,
                ),
            ]
        )
        debusine_mock.relation_create.assert_has_calls(
            [
                mock.call(
                    signing_output_ids[0], signing_input_ids[0], "relates-to"
                ),
                mock.call(
                    signing_output_ids[1], signing_input_ids[1], "relates-to"
                ),
            ]
        )

        # The files were properly signed.
        (certificate := temp_path / "tmp.crt").write_bytes(key.public_key)
        for image_path, signature_path in (
            (minimal_pecoff_path, output_path / "image.sig"),
            (minimal_pecoff_path2, output_path / "image2.sig"),
        ):
            subprocess.run(
                [
                    "sbverify",
                    "--cert",
                    certificate,
                    "--detached",
                    signature_path,
                    image_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )

    def test_openpgp(self) -> None:
        """Integration test: sign using a real OpenPGP key."""
        service_private_key = PrivateKey.generate()
        with (
            override_settings(
                DEBUSINE_SIGNING_PRIVATE_KEYS=[service_private_key]
            ),
            open(os.devnull, "wb") as log_file,
        ):
            key = Key.objects.generate(
                Key.Purpose.OPENPGP, "An OpenPGP key", 1, log_file
            )
        temp_path = self.create_temporary_directory()
        (changes_path := temp_path / "foo.changes").write_text(
            "A changes file\n"
        )
        signing_input = SigningInputArtifact.create([changes_path], temp_path)
        signing_input_id = 2
        signing_input_response = self.make_artifact_response(
            signing_input, signing_input_id
        )
        uploaded_paths: list[Path] = []
        output_path = self.create_temporary_directory()
        signing_output_id = 3

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            assert artifact_id == signing_input_id
            shutil.copy(changes_path, destination)
            return signing_input_response

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            nonlocal uploaded_paths
            for path in local_artifact.files.values():
                uploaded_paths.append(path)
                shutil.copy(path, output_path)
            return RemoteArtifact(id=signing_output_id, workspace="System")

        task = Sign(
            task_data={
                "purpose": KeyPurpose.OPENPGP,
                "unsigned": [signing_input_id],
                "key": key.fingerprint,
            },
            dynamic_task_data={
                "unsigned_ids": [signing_input_id],
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
            artifact_id=signing_input_id,
            work_request_id=task.work_request_id,
            workspace=task.workspace_name,
        )

        # The task uploaded an artifact with the expected properties.
        self.assertEqual(len(uploaded_paths), 2)
        debusine_mock.upload_artifact.assert_has_calls(
            [
                mock.call(
                    SigningOutputArtifact(
                        category=SigningOutputArtifact._category,
                        files={"foo.changes.sig": uploaded_paths[0]},
                        data=DebusineSigningOutput(
                            purpose=KeyPurpose.OPENPGP,
                            fingerprint=key.fingerprint,
                            results=[
                                SigningResult(
                                    file="foo.changes",
                                    output_file="foo.changes.sig",
                                )
                            ],
                        ),
                    ),
                    workspace="System",
                    work_request=1,
                ),
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
                    ),
                    workspace="System",
                    work_request=1,
                ),
            ]
        )
        debusine_mock.relation_create.assert_any_call(
            signing_output_id,
            signing_input_id,
            "relates-to",
        )

        # The file was properly signed.
        with (
            TemporaryDirectory(prefix="debusine-tests-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            import_result = ctx.key_import(bytes(key.public_key))
            assert getattr(import_result, "imported", 0) == 1
            data, result = ctx.verify(
                (output_path / "foo.changes.sig").read_bytes()
            )
            self.assertEqual(data, b"A changes file\n")
            self.assertEqual(result.signatures[0].fpr, key.fingerprint)
            # We configure this in gpg_ephemeral_context, but make sure of
            # it.
            self.assertEqual(
                result.signatures[0].hash_algo, gpg.constants.MD_SHA512
            )

    def test_label(self) -> None:
        """Test get_label."""
        task = Sign(
            task_data={"purpose": KeyPurpose.UEFI, "unsigned": [2], "key": 1}
        )
        self.assertEqual(task.get_label(), "sign data for uefi")
