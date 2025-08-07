# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the GenerateKey task."""

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from cryptography.x509 import load_pem_x509_certificate
from django.test import TransactionTestCase, override_settings
from nacl.public import PrivateKey, SealedBox

from debusine.artifacts import WorkRequestDebugLogs
from debusine.artifacts.models import EmptyArtifactData
from debusine.assets import AssetCategory, KeyPurpose, SigningKeyData
from debusine.client.debusine import Debusine
from debusine.signing.db.models import Key
from debusine.signing.gnupg import gpg_ephemeral_context
from debusine.signing.tasks import GenerateKey
from debusine.test import TestCase


class GenerateKeyTests(TestCase, TransactionTestCase):
    """Test the :py:class:`GenerateKey` task."""

    def mock_debusine(self, task: GenerateKey) -> Any:
        """Create a Debusine mock and configure a task for it."""
        debusine_mock = mock.create_autospec(spec=Debusine)
        task.configure_server_access(debusine_mock)
        return debusine_mock

    def test_uefi(self) -> None:
        """Integration test: generate and store a real UEFI key."""
        service_private_key = PrivateKey.generate()
        task = GenerateKey(
            task_data={"purpose": "uefi", "description": "A UEFI key"},
            dynamic_task_data={},
        )
        debusine_mock = self.mock_debusine(task)
        task.work_request_id = 1
        task.workspace_name = "System"
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory

        with override_settings(
            DEBUSINE_SIGNING_PRIVATE_KEYS=[service_private_key]
        ):
            self.assertTrue(task.execute())

        # The private key was encrypted using the configured service-wide
        # private key, and the public key has the correct subject.
        key = Key.objects.get(purpose=Key.Purpose.UEFI)
        self.assertEqual(
            load_pem_x509_certificate(
                bytes(key.public_key)
            ).subject.rfc4514_string(),
            "CN=A UEFI key",
        )
        self.assertEqual(key.private_key["storage"], "nacl")
        self.assertEqual(
            base64.b64decode(key.private_key["public_key"].encode()),
            bytes(service_private_key.public_key),
        )
        SealedBox(service_private_key).decrypt(
            base64.b64decode(key.private_key["encrypted"].encode())
        )

        # The task created an asset with the expected properties.
        debusine_mock.asset_create.assert_called_once_with(
            category=AssetCategory.SIGNING_KEY,
            data=SigningKeyData(
                purpose=KeyPurpose.UEFI,
                fingerprint=key.fingerprint,
                public_key=base64.b64encode(key.public_key).decode(),
                description="A UEFI key",
            ),
            workspace="System",
            work_request=1,
        )

    def test_openpgp(self) -> None:
        """Integration test: generate and store a real OpenPGP key."""
        service_private_key = PrivateKey.generate()
        task = GenerateKey(
            task_data={"purpose": "openpgp", "description": "An OpenPGP key"},
            dynamic_task_data={},
        )
        debusine_mock = self.mock_debusine(task)
        task.work_request_id = 1
        task.workspace_name = "System"
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory

        with override_settings(
            DEBUSINE_SIGNING_PRIVATE_KEYS=[service_private_key]
        ):
            self.assertTrue(task.execute())

        # The private key was encrypted using the configured service-wide
        # private key, and the public key has the correct UID.
        key = Key.objects.get(purpose=Key.Purpose.OPENPGP)
        with (
            TemporaryDirectory(prefix="debusine-tests-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            result = ctx.key_import(bytes(key.public_key))
            assert getattr(result, "imported", 0) == 1
            gpg_key = ctx.get_key(result.imports[0].fpr)
            self.assertEqual(gpg_key.uids[0].name, "An OpenPGP key")
        self.assertEqual(key.private_key["storage"], "nacl")
        self.assertEqual(
            base64.b64decode(key.private_key["public_key"].encode()),
            bytes(service_private_key.public_key),
        )
        SealedBox(service_private_key).decrypt(
            base64.b64decode(key.private_key["encrypted"].encode())
        )

        # The task created a signing asset with the expected properties.
        debusine_mock.asset_create.assert_called_once_with(
            category=AssetCategory.SIGNING_KEY,
            data=SigningKeyData(
                purpose=KeyPurpose.OPENPGP,
                fingerprint=key.fingerprint,
                public_key=base64.b64encode(key.public_key).decode(),
                description="An OpenPGP key",
            ),
            workspace="System",
            work_request=1,
        )

    def test_generate_failure(self) -> None:
        """If key generation fails, the task does not upload a signing key."""
        task = GenerateKey(
            task_data={"purpose": "uefi", "description": "A UEFI key"},
            dynamic_task_data={},
        )
        debusine_mock = self.mock_debusine(task)
        task.work_request_id = 1
        task.workspace_name = "System"
        debug_log_files_directory = TemporaryDirectory(prefix="debusine-tests-")
        self.addCleanup(debug_log_files_directory.cleanup)
        task._debug_log_files_directory = debug_log_files_directory

        with mock.patch.object(
            Key.objects,
            "generate",
            side_effect=Exception("Key generation failed"),
        ):
            self.assertFalse(task.execute())

        # No key was generated.
        self.assertQuerySetEqual(Key.objects.all(), [])

        # No asset was created
        debusine_mock.asset_create.assert_not_called()

        # Debug logs artifact was uploaded
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
            ),
            workspace="System",
            work_request=1,
        )

    def test_label(self) -> None:
        """Test get_label."""
        task = GenerateKey(
            task_data={"purpose": "uefi", "description": "A UEFI key"},
            dynamic_task_data={},
        )
        self.assertEqual(task.get_label(), "generate uefi key")
