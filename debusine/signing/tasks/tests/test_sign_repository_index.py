# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the SignRepositoryIndex task."""

import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import gpg
from django.test import TestCase as DjangoTestCase
from django.test import TransactionTestCase, override_settings
from nacl.public import PrivateKey

from debusine.artifacts import (
    LocalArtifact,
    RepositoryIndex,
    WorkRequestDebugLogs,
)
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
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
from debusine.signing.tasks import SignRepositoryIndex
from debusine.signing.tasks.models import SignRepositoryIndexDynamicData
from debusine.signing.tasks.tests.test_sign import SignTestMixin
from debusine.tasks import TaskConfigError
from debusine.tasks.server import ArtifactInfo, CollectionInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabase
from debusine.test import TestCase
from debusine.test.test_utils import create_remote_artifact


class SignRepositoryIndexTests(SignTestMixin, TestCase, DjangoTestCase):
    """Unit tests for the :py:class:`SignRepositoryIndex` task."""

    def test_compute_dynamic_data(self) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.REPOSITORY_INDEX,
                    data=EmptyArtifactData(),
                ),
            },
            single_collection_lookups={
                (f"sid@{CollectionCategory.SUITE}", None): CollectionInfo(
                    id=1,
                    category=CollectionCategory.SUITE,
                    data={"signing_keys": ["ABC123"]},
                )
            },
        )

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            SignRepositoryIndexDynamicData(
                signing_keys=["ABC123"], unsigned_id=2
            ),
        )

    def test_compute_dynamic_data_wrong_suite_collection_category(self) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.REPOSITORY_INDEX,
                    data=EmptyArtifactData(),
                ),
            },
            single_collection_lookups={
                ("sid", None): CollectionInfo(
                    id=1, category=CollectionCategory.TEST, data={}
                )
            },
        )

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": "sid",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        with self.assertRaisesRegex(
            TaskConfigError,
            fr"^suite_collection: unexpected collection category: "
            fr"'{CollectionCategory.TEST}'\. "
            fr"Expected: '{CollectionCategory.SUITE}'$",
        ):
            task.compute_dynamic_data(task_db)

    def test_compute_dynamic_data_signing_keys_from_archive(self) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.REPOSITORY_INDEX,
                    data=EmptyArtifactData(),
                ),
            },
            single_collection_lookups={
                (f"_@{CollectionCategory.ARCHIVE}", None): CollectionInfo(
                    id=1,
                    category=CollectionCategory.ARCHIVE,
                    data={"signing_keys": ["ABC123"]},
                ),
                (f"sid@{CollectionCategory.SUITE}", None): CollectionInfo(
                    id=2, category=CollectionCategory.SUITE, data={}
                ),
            },
        )

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            SignRepositoryIndexDynamicData(
                signing_keys=["ABC123"], unsigned_id=2
            ),
        )

    def test_compute_dynamic_data_no_archive_or_signing_keys(self) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.REPOSITORY_INDEX,
                    data=EmptyArtifactData(),
                ),
            },
            single_collection_lookups={
                (f"sid@{CollectionCategory.SUITE}", None): CollectionInfo(
                    id=1, category=CollectionCategory.SUITE, data={}
                ),
            },
        )

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            SignRepositoryIndexDynamicData(signing_keys=[], unsigned_id=2),
        )

    def test_get_input_artifacts_ids(self) -> None:
        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 1,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        self.assertEqual(task.get_input_artifacts_ids(), [])

        task.dynamic_data = SignRepositoryIndexDynamicData(
            signing_keys=[], unsigned_id=1
        )
        self.assertEqual(task.get_input_artifacts_ids(), [1])

    def test_fetch_input_no_signing_keys(self) -> None:
        directory = self.create_temporary_directory()
        (release := directory / "Release").write_text("A Release file\n")
        unsigned = RepositoryIndex.create(file=release, path="Release")
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "clear",
                "signed_name": "InRelease",
            },
            dynamic_task_data={"signing_keys": [], "unsigned_id": unsigned_id},
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = unsigned_response
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            f"No signing keys are configured for "
            f"sid@{CollectionCategory.SUITE}.\n",
        )
        debusine_mock.download_artifact.assert_not_called()

    def test_fetch_input_permission_denied(self) -> None:
        fingerprint = "0" * 64
        directory = self.create_temporary_directory()
        (release := directory / "Release").write_text("A Release file\n")
        unsigned = RepositoryIndex.create(file=release, path="Release")
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "clear",
                "signed_name": "InRelease",
            },
            dynamic_task_data={
                "signing_keys": [fingerprint],
                "unsigned_id": unsigned_id,
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = unsigned_response
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
            f"{unsigned_id} with openpgp:{fingerprint}.\n",
        )
        debusine_mock.download_artifact.assert_called_once_with(
            unsigned_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_mismatching_permission_check_responses(self) -> None:
        keys = [
            Key.objects.create(
                purpose=Key.Purpose.OPENPGP,
                fingerprint=fingerprint,
                private_key={},
                public_key=b"",
            )
            for fingerprint in ("0" * 64, "1" * 64)
        ]
        directory = self.create_temporary_directory()
        (release := directory / "Release").write_text("A Release file\n")
        unsigned = RepositoryIndex.create(file=release, path="Release")
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "clear",
                "signed_name": "InRelease",
            },
            dynamic_task_data={
                "signing_keys": [key.fingerprint for key in keys],
                "unsigned_id": unsigned_id,
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = unsigned_response
        asset_permission_checks = [
            AssetPermissionCheckResponse(
                has_permission=True,
                username="testuser",
                user_id=123,
                resource={"path": "Release"},
            ),
            AssetPermissionCheckResponse(
                has_permission=True,
                username="somehow-different",
                user_id=123,
                resource={"path": "Release"},
            ),
        ]
        debusine_mock.asset_permission_check.side_effect = (
            asset_permission_checks
        )
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        destination = self.create_temporary_directory()

        self.assertFalse(task.fetch_input(destination))

        self.assertEqual(
            Path(debug_log_files_directory.name, "fetch_input.log").read_text(),
            f"Mismatching permission-check responses for different signing "
            f"keys: "
            f"{asset_permission_checks[1]} != {asset_permission_checks[0]}\n",
        )
        debusine_mock.download_artifact.assert_called_once_with(
            unsigned_id, destination, tarball=mock.ANY
        )

    def test_fetch_input_missing_key(self) -> None:
        """fetch_input requires the signing key to exist in the database."""
        fingerprint = "0" * 64
        directory = self.create_temporary_directory()
        (release := directory / "Release").write_text("A Release file\n")
        unsigned = RepositoryIndex.create(file=release, path="Release")
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "clear",
                "signed_name": "InRelease",
            },
            dynamic_task_data={
                "signing_keys": [fingerprint],
                "unsigned_id": unsigned_id,
            },
        )
        task.work_request_id = 1
        task.workspace_name = "workspace"
        debusine_mock = self.mock_debusine(task)
        debusine_mock.download_artifact.return_value = unsigned_response
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
            unsigned_id, destination, tarball=mock.ANY
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
        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        task.work_request_id = 1
        task._file_path = "Release"
        task._keys = [key]
        task._permission_check = AssetPermissionCheckResponse(
            has_permission=True,
            username="testuser",
            user_id=123,
            resource={"path": "Release"},
        )
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with (
            mock.patch(
                "debusine.signing.tasks.sign_repository_index.sign",
                side_effect=ValueError("Boom"),
            ),
            self.assertRaisesRegex(ValueError, "Boom"),
        ):
            task.run(execute_directory)

        self.assertIsNone(task._signed_path)

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
        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        task.work_request_id = 1
        task._file_path = "Release"
        task._keys = [key]
        task._permission_check = AssetPermissionCheckResponse(
            has_permission=True,
            username="testuser",
            user_id=123,
            resource={"path": "Release"},
        )
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory
        task.prepare_to_run(download_directory, execute_directory)

        with mock.patch(
            "debusine.signing.tasks.sign_repository_index.sign"
        ) as mock_sign:
            self.assertTrue(task.run(execute_directory))

        self.assertEqual(
            task._signed_path, execute_directory / "output" / "InRelease"
        )
        mock_sign.assert_called_once_with(
            [key],
            execute_directory / "input" / "Release",
            execute_directory / "output" / "InRelease",
            SigningMode.CLEAR,
            1,
            username="testuser",
            user_id=123,
            resource={"path": "Release"},
            log_file=mock.ANY,
        )


class SignRepositoryIndexIntegrationTests(
    SignTestMixin, TestCase, TransactionTestCase
):
    """Integration tests for the :py:class:`SignRepositoryIndex` task."""

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

    def make_release(self) -> tuple[Path, RepositoryIndex]:
        """Prepare a minimal repository index: return (path, artifact)."""
        temp_path = self.create_temporary_directory()
        (release_path := temp_path / "Release").write_text("A Release file\n")
        index = RepositoryIndex.create(file=release_path, path="Release")
        return release_path, index

    def test_execute_detached_success(self) -> None:
        """Integration test: detached-sign using real OpenPGP keys."""
        service_private_key = PrivateKey.generate()
        keys = [
            self.generate_signing_key(service_private_key) for _ in range(2)
        ]
        release_path, unsigned = self.make_release()
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)
        uploaded_paths: list[Path] = []
        uploaded_signed: RepositoryIndex | None = None
        output_path = self.create_temporary_directory()
        uploaded_artifact_ids = {
            ArtifactCategory.REPOSITORY_INDEX: 3,
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: 4,
        }

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            assert artifact_id == unsigned_id
            shutil.copy(release_path, destination)
            return unsigned_response

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            nonlocal uploaded_signed
            for path in local_artifact.files.values():
                uploaded_paths.append(path)
                shutil.copy(path, output_path)
            if local_artifact.category == ArtifactCategory.REPOSITORY_INDEX:
                [(signed_path_in_suite, signed_path)] = (
                    local_artifact.files.items()
                )
                uploaded_signed = RepositoryIndex.create(
                    file=signed_path, path=signed_path_in_suite
                )
            return create_remote_artifact(
                id=uploaded_artifact_ids[local_artifact.category],
                workspace="System",
            )

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "detached",
                "signed_name": "Release.gpg",
            },
            dynamic_task_data={
                "signing_keys": [key.fingerprint for key in keys],
                "unsigned_id": unsigned_id,
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
                resource={"path": "Release"},
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

        self.assertEqual(
            debusine_mock.asset_permission_check.call_count, len(keys)
        )
        debusine_mock.asset_permission_check.assert_has_calls(
            [
                mock.call(
                    asset_category=AssetCategory.SIGNING_KEY,
                    asset_slug=f"openpgp:{key.fingerprint}",
                    permission_name="sign_with",
                    artifact_id=unsigned_id,
                    work_request_id=task.work_request_id,
                    workspace=task.workspace_name,
                )
                for key in keys
            ]
        )

        # The task uploaded an artifact with the expected properties.
        self.assertEqual(len(uploaded_paths), 2)
        debusine_mock.upload_artifact.assert_has_calls(
            [
                mock.call(uploaded_signed, workspace="System", work_request=1),
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
            uploaded_artifact_ids[ArtifactCategory.REPOSITORY_INDEX],
            unsigned_id,
            "relates-to",
        )

        # The file was properly signed.
        with (
            TemporaryDirectory(prefix="debusine-tests-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            for key in keys:
                import_result = ctx.key_import(bytes(key.public_key))
                assert getattr(import_result, "imported", 0) == 1
            _, result = ctx.verify(
                release_path.read_bytes(),
                (output_path / "Release.gpg").read_bytes(),
            )
            self.assertEqual(len(result.signatures), len(keys))
            self.assertCountEqual(
                [signature.fpr for signature in result.signatures],
                [key.fingerprint for key in keys],
            )
            # We configure this in gpg_ephemeral_context, but make sure of
            # it.
            self.assertEqual(
                [signature.hash_algo for signature in result.signatures],
                [gpg.constants.MD_SHA512] * len(keys),
            )

    def test_execute_clear_success(self) -> None:
        """Integration test: clear-sign using real OpenPGP keys."""
        service_private_key = PrivateKey.generate()
        keys = [
            self.generate_signing_key(service_private_key) for _ in range(2)
        ]
        release_path, unsigned = self.make_release()
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)
        uploaded_paths: list[Path] = []
        uploaded_signed: RepositoryIndex | None = None
        output_path = self.create_temporary_directory()
        uploaded_artifact_ids = {
            ArtifactCategory.REPOSITORY_INDEX: 3,
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: 4,
        }

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            assert artifact_id == unsigned_id
            shutil.copy(release_path, destination)
            return unsigned_response

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            nonlocal uploaded_signed
            for path in local_artifact.files.values():
                uploaded_paths.append(path)
                shutil.copy(path, output_path)
            if local_artifact.category == ArtifactCategory.REPOSITORY_INDEX:
                [(signed_path_in_suite, signed_path)] = (
                    local_artifact.files.items()
                )
                uploaded_signed = RepositoryIndex.create(
                    file=signed_path, path=signed_path_in_suite
                )
            return create_remote_artifact(
                id=uploaded_artifact_ids[local_artifact.category],
                workspace="System",
            )

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "clear",
                "signed_name": "InRelease",
            },
            dynamic_task_data={
                "signing_keys": [key.fingerprint for key in keys],
                "unsigned_id": unsigned_id,
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
                resource={"path": "Release"},
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

        self.assertEqual(
            debusine_mock.asset_permission_check.call_count, len(keys)
        )
        debusine_mock.asset_permission_check.assert_has_calls(
            [
                mock.call(
                    asset_category=AssetCategory.SIGNING_KEY,
                    asset_slug=f"openpgp:{key.fingerprint}",
                    permission_name="sign_with",
                    artifact_id=unsigned_id,
                    work_request_id=task.work_request_id,
                    workspace=task.workspace_name,
                )
                for key in keys
            ]
        )

        # The task uploaded an artifact with the expected properties.
        self.assertEqual(len(uploaded_paths), 2)
        debusine_mock.upload_artifact.assert_has_calls(
            [
                mock.call(uploaded_signed, workspace="System", work_request=1),
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
            uploaded_artifact_ids[ArtifactCategory.REPOSITORY_INDEX],
            unsigned_id,
            "relates-to",
        )

        # The file was properly signed.
        with (
            TemporaryDirectory(prefix="debusine-tests-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            for key in keys:
                import_result = ctx.key_import(bytes(key.public_key))
                assert getattr(import_result, "imported", 0) == 1
            data, result = ctx.verify((output_path / "InRelease").read_bytes())
            self.assertEqual(data, b"A Release file\n")
            self.assertEqual(len(result.signatures), len(keys))
            self.assertCountEqual(
                [signature.fpr for signature in result.signatures],
                [key.fingerprint for key in keys],
            )
            # We configure this in gpg_ephemeral_context, but make sure of
            # it.
            self.assertEqual(
                [signature.hash_algo for signature in result.signatures],
                [gpg.constants.MD_SHA512] * len(keys),
            )

    def test_execute_failure(self) -> None:
        """If execution fails, the task does not upload an output artifact."""
        service_private_key = PrivateKey.generate()
        key = self.generate_signing_key(service_private_key)
        release_path, unsigned = self.make_release()
        unsigned_id = 2
        unsigned_response = self.make_artifact_response(unsigned, unsigned_id)

        def download_artifact(
            artifact_id: int, destination: Path, **kwargs: Any  # noqa: U100
        ) -> ArtifactResponse:
            assert artifact_id == unsigned_id
            shutil.copy(release_path, destination)
            return unsigned_response

        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": unsigned_id,
                "mode": "clear",
                "signed_name": "InRelease",
            },
            dynamic_task_data={
                "signing_keys": [key.fingerprint],
                "unsigned_id": unsigned_id,
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
            "debusine.signing.tasks.sign_repository_index.sign",
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
        task = SignRepositoryIndex(
            task_data={
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
                "unsigned": 2,
                "mode": "clear",
                "signed_name": "InRelease",
            }
        )
        self.assertEqual(
            task.get_label(),
            f"sign InRelease for sid@{CollectionCategory.SUITE}",
        )
