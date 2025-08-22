# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the signing database models."""

import hashlib
import io
import os
import random
import re
import subprocess
from pathlib import Path
from typing import Any, BinaryIO
from unittest import mock

import gpg
from django.test import TestCase as DjangoTestCase
from gpg.results import GenkeyResult, ImportResult, ImportStatus
from nacl.public import PrivateKey

from debusine.signing.db.models import AuditLog, Key, sign
from debusine.signing.gnupg import GnuPGError, gpg_sign
from debusine.signing.models import (
    ProtectedKeyNaCl,
    ProtectedKeyPKCS11Static,
    SigningMode,
)
from debusine.signing.tests import make_fake_fingerprint
from debusine.test import TestCase


class KeyManagerTests(TestCase, DjangoTestCase):
    """Unit tests for `KeyManager`."""

    def create_nacl_key(
        self, *, storage_private_key: PrivateKey, purpose: Key.Purpose
    ) -> tuple[Key, bytes]:
        """
        Make a random NaCl-encrypted Key.

        :return: A tuple of the Key itself and its private key.
        """
        private_key = random.randbytes(64)
        public_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint()
        key = Key.objects.create(
            purpose=purpose,
            fingerprint=fingerprint,
            private_key=ProtectedKeyNaCl.encrypt(
                storage_private_key.public_key, private_key
            ).dict(),
            public_key=public_key,
        )
        return key, private_key

    def test_generate_uefi(self) -> None:
        """Generate a UEFI key."""
        # Generating keys is slow, so we just test that the correct
        # subprocesses are executed.
        private_key = random.randbytes(64)
        public_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint()
        storage_private_key = PrivateKey.generate()
        log_file = io.BytesIO()

        def run(
            args: list[str | os.PathLike[str]], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            match args[1]:
                case "req":
                    Path(args[args.index("-keyout") + 1]).write_bytes(
                        private_key
                    )
                    Path(args[args.index("-out") + 1]).write_bytes(public_key)
                    return subprocess.CompletedProcess(args=args, returncode=0)
                case "x509":
                    fp_colons = ":".join(re.findall("..", fingerprint))
                    return subprocess.CompletedProcess(
                        args=args,
                        returncode=0,
                        stdout=f"sha256 Fingerprint={fp_colons}\n".encode(),
                    )
                case _:  # pragma: no cover
                    return subprocess.CompletedProcess(args=args, returncode=1)

        with (
            mock.patch("subprocess.run", side_effect=run) as mock_run,
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
        ):
            key = Key.objects.generate(
                Key.Purpose.UEFI, "debian/bookworm test key", 123, log_file
            )

        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_has_calls(
            [
                mock.call(
                    [
                        "openssl",
                        "req",
                        "-new",
                        "-newkey",
                        "rsa:2048",
                        "-x509",
                        "-subj",
                        r"/CN=debian\/bookworm test key/",
                        "-days",
                        "5475",
                        "-noenc",
                        "-sha256",
                        "-keyout",
                        mock.ANY,
                        "-out",
                        mock.ANY,
                    ],
                    check=True,
                    stdout=log_file,
                    stderr=log_file,
                ),
                mock.call(
                    [
                        "openssl",
                        "x509",
                        "-inform",
                        "PEM",
                        "-noout",
                        "-fingerprint",
                        "-sha256",
                    ],
                    check=True,
                    input=mock.ANY,
                    stdout=subprocess.PIPE,
                    stderr=log_file,
                ),
            ]
        )
        self.assertEqual(key.purpose, Key.Purpose.UEFI)
        self.assertEqual(key.fingerprint, fingerprint)
        assert isinstance(key.stored_private_key.__root__, ProtectedKeyNaCl)
        self.assertEqual(
            key.stored_private_key.__root__.decrypt([storage_private_key]),
            private_key,
        )
        self.assertEqual(key.public_key, public_key)
        audit_log = AuditLog.objects.latest("created_at")
        self.assertEqual(audit_log.purpose, Key.Purpose.UEFI)
        self.assertEqual(audit_log.fingerprint, fingerprint)
        self.assertEqual(audit_log.event, AuditLog.Event.GENERATE)
        self.assertEqual(
            audit_log.data, {"description": "debian/bookworm test key"}
        )
        self.assertEqual(audit_log.created_by_work_request_id, 123)

    def test_generate_openpgp(self) -> None:
        """Generate an OpenPGP key."""
        # Generating keys is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        secret_key = random.randbytes(64)
        public_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint(length=40)
        create_result = GenkeyResult(None)
        create_result.primary = True
        create_result.sub = False
        create_result.fpr = fingerprint
        import_status = ImportStatus(None)
        import_status.fpr = fingerprint
        import_result = ImportResult(None)
        import_result.imported = 1
        import_result.imports = [import_status]
        storage_private_key = PrivateKey.generate()
        log_file = io.BytesIO()

        with (
            mock.patch(
                "gpg.Context.create_key", return_value=create_result
            ) as mock_create_key,
            mock.patch(
                "gpg.Context.key_export_secret", return_value=secret_key
            ) as mock_key_export_secret,
            mock.patch(
                "gpg.Context.key_export_minimal", return_value=public_key
            ) as mock_key_export_minimal,
            mock.patch(
                "gpg.Context.key_import", return_value=import_result
            ) as mock_key_import,
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
        ):
            key = Key.objects.generate(
                Key.Purpose.OPENPGP, "test key", 123, log_file
            )

        mock_create_key.assert_called_once_with(
            "test key", algorithm="ed25519", expires=False, sign=True
        )
        mock_key_export_secret.assert_called_once_with(fingerprint)
        mock_key_export_minimal.assert_called_once_with(fingerprint)
        mock_key_import.assert_called_once_with(public_key)
        self.assertEqual(key.purpose, Key.Purpose.OPENPGP)
        self.assertEqual(key.fingerprint, fingerprint)
        assert isinstance(key.stored_private_key.__root__, ProtectedKeyNaCl)
        self.assertEqual(
            key.stored_private_key.__root__.decrypt([storage_private_key]),
            secret_key,
        )
        self.assertEqual(key.public_key, public_key)
        audit_log = AuditLog.objects.latest("created_at")
        self.assertEqual(audit_log.purpose, Key.Purpose.OPENPGP)
        self.assertEqual(audit_log.fingerprint, fingerprint)
        self.assertEqual(audit_log.event, AuditLog.Event.GENERATE)
        self.assertEqual(audit_log.data, {"description": "test key"})
        self.assertEqual(audit_log.created_by_work_request_id, 123)

    def test_generate_openpgp_create_key_no_primary(self) -> None:
        """Key generation error: failed to generate an OpenPGP secret key."""
        create_result = GenkeyResult(None)
        create_result.primary = False
        log_file = io.BytesIO()

        with (
            mock.patch("gpg.Context.create_key", return_value=create_result),
            self.assertRaisesRegex(
                GnuPGError, "Failed to generate a secret key"
            ),
        ):
            Key.objects.generate(Key.Purpose.OPENPGP, "test key", 123, log_file)

    def test_generate_openpgp_create_key_unexpected_subkey(self) -> None:
        """Key generation error: unexpectedly got OpenPGP encryption subkey."""
        create_result = GenkeyResult(None)
        create_result.primary = True
        create_result.sub = True
        log_file = io.BytesIO()

        with (
            mock.patch("gpg.Context.create_key", return_value=create_result),
            self.assertRaisesRegex(
                GnuPGError, "Unexpectedly got an encryption subkey"
            ),
        ):
            Key.objects.generate(Key.Purpose.OPENPGP, "test key", 123, log_file)

    def test_generate_openpgp_key_export_secret_failed(self) -> None:
        """Key generation error: failed to export OpenPGP secret key."""
        fingerprint = make_fake_fingerprint(length=40)
        create_result = GenkeyResult(None)
        create_result.primary = True
        create_result.sub = False
        create_result.fpr = fingerprint
        log_file = io.BytesIO()

        with (
            mock.patch("gpg.Context.create_key", return_value=create_result),
            mock.patch("gpg.Context.key_export_secret", return_value=None),
            self.assertRaisesRegex(GnuPGError, "Failed to export secret key"),
        ):
            Key.objects.generate(Key.Purpose.OPENPGP, "test key", 123, log_file)

    def test_generate_openpgp_key_export_public_failed(self) -> None:
        """Key generation error: failed to export OpenPGP public key."""
        secret_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint(length=40)
        create_result = GenkeyResult(None)
        create_result.primary = True
        create_result.sub = False
        create_result.fpr = fingerprint
        log_file = io.BytesIO()

        with (
            mock.patch("gpg.Context.create_key", return_value=create_result),
            mock.patch(
                "gpg.Context.key_export_secret", return_value=secret_key
            ),
            mock.patch("gpg.Context.key_export_minimal", return_value=None),
            self.assertRaisesRegex(GnuPGError, "Failed to export public key"),
        ):
            Key.objects.generate(Key.Purpose.OPENPGP, "test key", 123, log_file)

    def test_generate_openpgp_key_import_failed(self) -> None:
        """Key generation error: failed to import OpenPGP public key."""
        secret_key = random.randbytes(64)
        public_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint(length=40)
        create_result = GenkeyResult(None)
        create_result.primary = True
        create_result.sub = False
        create_result.fpr = fingerprint
        import_result = ImportResult(None)
        import_result.not_imported = 1
        log_file = io.BytesIO()

        with (
            mock.patch("gpg.Context.create_key", return_value=create_result),
            mock.patch(
                "gpg.Context.key_export_secret", return_value=secret_key
            ),
            mock.patch(
                "gpg.Context.key_export_minimal", return_value=public_key
            ),
            mock.patch("gpg.Context.key_import", return_value=import_result),
            self.assertRaisesRegex(
                GnuPGError, "Failed to get fingerprint of new key"
            ),
        ):
            Key.objects.generate(Key.Purpose.OPENPGP, "test key", 123, log_file)

    def test_sign_uefi_attached(self) -> None:
        """Sign data using a UEFI key, returning an attached signature."""
        # Signing data is slow, so we just test that the correct
        # subprocesses are executed.
        storage_private_key = PrivateKey.generate()
        key, private_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.UEFI
        )
        temp_path = self.create_temporary_directory()
        raw_data = b"data to sign"
        (data_path := temp_path / "data").write_bytes(raw_data)
        signature_path = temp_path / "signed"
        signature_bytes = random.randbytes(64)
        log_file = io.BytesIO()
        key_bytes: bytes | None = None
        certificate_bytes: bytes | None = None

        def run(
            args: list[str | os.PathLike[str]], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            nonlocal key_bytes, certificate_bytes
            key_bytes = Path(args[args.index("--key") + 1]).read_bytes()
            certificate_bytes = Path(
                args[args.index("--cert") + 1]
            ).read_bytes()
            Path(args[args.index("--output") + 1]).write_bytes(signature_bytes)
            return subprocess.CompletedProcess(args=args, returncode=0)

        with (
            mock.patch("subprocess.run", side_effect=run) as mock_run,
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
        ):
            sign(
                [key],
                data_path,
                signature_path,
                SigningMode.ATTACHED,
                123,
                username="testuser",
                user_id=1234,
                resource={"package": "linux"},
                log_file=log_file,
            )

        mock_run.assert_called_once_with(
            [
                "sbsign",
                "--key",
                mock.ANY,
                "--cert",
                mock.ANY,
                "--output",
                signature_path,
                data_path,
            ],
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
        self.assertEqual(key_bytes, private_key)
        self.assertEqual(certificate_bytes, key.public_key)
        self.assertEqual(signature_path.read_bytes(), signature_bytes)

        audit_log = AuditLog.objects.latest("created_at")
        self.assertEqual(audit_log.purpose, Key.Purpose.UEFI)
        self.assertEqual(audit_log.fingerprint, key.fingerprint)
        self.assertEqual(audit_log.event, AuditLog.Event.SIGN)
        self.assertEqual(
            audit_log.data,
            {
                "data_sha256": hashlib.sha256(raw_data).hexdigest(),
                "username": "testuser",
                "user_id": 1234,
                "resource": {
                    "package": "linux",
                },
            },
        )
        self.assertEqual(audit_log.created_by_work_request_id, 123)

    def test_sign_uefi_detached(self) -> None:
        """Sign data using a UEFI key, returning a detached signature."""
        # Signing data is slow, so we just test that the correct
        # subprocesses are executed.
        storage_private_key = PrivateKey.generate()
        key, private_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.UEFI
        )
        temp_path = self.create_temporary_directory()
        (data_path := temp_path / "data").write_bytes(b"data to sign")
        signature_path = temp_path / "signed"
        signature_bytes = random.randbytes(64)
        log_file = io.BytesIO()
        key_bytes: bytes | None = None
        certificate_bytes: bytes | None = None

        def run(
            args: list[str | os.PathLike[str]], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            nonlocal key_bytes, certificate_bytes
            key_bytes = Path(args[args.index("--key") + 1]).read_bytes()
            certificate_bytes = Path(
                args[args.index("--cert") + 1]
            ).read_bytes()
            Path(args[args.index("--output") + 1]).write_bytes(signature_bytes)
            return subprocess.CompletedProcess(args=args, returncode=0)

        with (
            mock.patch("subprocess.run", side_effect=run) as mock_run,
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
        ):
            sign(
                [key],
                data_path,
                signature_path,
                SigningMode.DETACHED,
                123,
                log_file=log_file,
            )

        mock_run.assert_called_once_with(
            [
                "sbsign",
                "--key",
                mock.ANY,
                "--cert",
                mock.ANY,
                "--detached",
                "--output",
                signature_path,
                data_path,
            ],
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
        self.assertEqual(key_bytes, private_key)
        self.assertEqual(certificate_bytes, key.public_key)
        self.assertEqual(signature_path.read_bytes(), signature_bytes)

    def test_sign_openpgp_clear(self) -> None:
        """Sign data using an OpenPGP key, returning a clear signature."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        import_status = ImportStatus(None)
        import_status.fpr = key.fingerprint
        import_result = ImportResult(None)
        import_result.secret_imported = 1
        import_result.imports = [import_status]
        keylist = [object()]
        temp_path = self.create_temporary_directory()
        data_bytes = b"data to sign"
        (data_path := temp_path / "data").write_bytes(data_bytes)
        signature_path = temp_path / "data.asc"
        signature_bytes = random.randbytes(64)

        def gpg_sign(
            ctx: gpg.Context,
            data: gpg.Data,
            sink: BinaryIO | None = None,
            mode: int = gpg.constants.SIG_MODE_NORMAL,
        ) -> None:
            self.assertTrue(ctx.armor)
            self.assertEqual(data.read(), data_bytes)
            self.assertEqual(mode, gpg.constants.SIG_MODE_CLEAR)
            assert sink is not None
            sink.write(signature_bytes)

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch(
                "gpg.Context.key_import", return_value=import_result
            ) as mock_key_import,
            mock.patch(
                "gpg.Context.keylist", return_value=iter(keylist)
            ) as mock_keylist,
            mock.patch(
                "gpg.Context.signers", new_callable=mock.PropertyMock
            ) as mock_signers,
            mock.patch(
                "gpg.Context.sign", autospec=True, side_effect=gpg_sign
            ) as mock_sign,
        ):
            sign([key], data_path, signature_path, SigningMode.CLEAR, 123)

        mock_key_import.assert_called_once_with(secret_key)
        mock_keylist.assert_called_once_with(
            pattern=key.fingerprint, secret=True
        )
        mock_signers.assert_called_with(keylist)
        mock_sign.assert_called_once()
        self.assertEqual(signature_path.read_bytes(), signature_bytes)

    def test_sign_openpgp_detached(self) -> None:
        """Sign data using an OpenPGP key, returning a detached signature."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        import_status = ImportStatus(None)
        import_status.fpr = key.fingerprint
        import_result = ImportResult(None)
        import_result.secret_imported = 1
        import_result.imports = [import_status]
        keylist = [object()]
        temp_path = self.create_temporary_directory()
        data_bytes = b"data to sign"
        (data_path := temp_path / "data").write_bytes(data_bytes)
        signature_path = temp_path / "data.asc"
        signature_bytes = random.randbytes(64)

        def gpg_sign(
            ctx: gpg.Context,
            data: gpg.Data,
            sink: BinaryIO | None = None,
            mode: int = gpg.constants.SIG_MODE_NORMAL,
        ) -> None:
            self.assertTrue(ctx.armor)
            self.assertEqual(data.read(), data_bytes)
            self.assertEqual(mode, gpg.constants.SIG_MODE_DETACH)
            assert sink is not None
            sink.write(signature_bytes)

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch(
                "gpg.Context.key_import", return_value=import_result
            ) as mock_key_import,
            mock.patch(
                "gpg.Context.keylist", return_value=iter(keylist)
            ) as mock_keylist,
            mock.patch(
                "gpg.Context.signers", new_callable=mock.PropertyMock
            ) as mock_signers,
            mock.patch(
                "gpg.Context.sign", autospec=True, side_effect=gpg_sign
            ) as mock_sign,
        ):
            sign([key], data_path, signature_path, SigningMode.DETACHED, 123)

        mock_key_import.assert_called_once_with(secret_key)
        mock_keylist.assert_called_once_with(
            pattern=key.fingerprint, secret=True
        )
        mock_signers.assert_called_with(keylist)
        mock_sign.assert_called_once()
        self.assertEqual(signature_path.read_bytes(), signature_bytes)

    def test_sign_openpgp_multiple(self) -> None:
        """Sign data using multiple OpenPGP keys."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key_pairs = [
            self.create_nacl_key(
                storage_private_key=storage_private_key,
                purpose=Key.Purpose.OPENPGP,
            )
            for _ in range(2)
        ]
        import_results: list[ImportResult] = []
        for key, _ in key_pairs:
            import_status = ImportStatus(None)
            import_status.fpr = key.fingerprint
            import_result = ImportResult(None)
            import_result.secret_imported = 1
            import_result.imports = [import_status]
            import_results.append(import_result)
        keylist = [object() for _ in key_pairs]
        temp_path = self.create_temporary_directory()
        data_bytes = b"data to sign"
        (data_path := temp_path / "data").write_bytes(data_bytes)
        signature_path = temp_path / "data.asc"
        signature_bytes = random.randbytes(64)

        def gpg_sign(
            ctx: gpg.Context,
            data: gpg.Data,
            sink: BinaryIO | None = None,
            mode: int = gpg.constants.SIG_MODE_NORMAL,
        ) -> None:
            self.assertTrue(ctx.armor)
            self.assertEqual(data.read(), data_bytes)
            self.assertEqual(mode, gpg.constants.SIG_MODE_CLEAR)
            assert sink is not None
            sink.write(signature_bytes)

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch(
                "gpg.Context.key_import", side_effect=import_results
            ) as mock_key_import,
            mock.patch(
                "gpg.Context.keylist",
                side_effect=[iter([signer]) for signer in keylist],
            ) as mock_keylist,
            mock.patch(
                "gpg.Context.signers", new_callable=mock.PropertyMock
            ) as mock_signers,
            mock.patch(
                "gpg.Context.sign", autospec=True, side_effect=gpg_sign
            ) as mock_sign,
        ):
            sign(
                [key for key, _ in key_pairs],
                data_path,
                signature_path,
                SigningMode.CLEAR,
                123,
            )

        self.assertEqual(mock_key_import.call_count, len(key_pairs))
        mock_key_import.assert_has_calls(
            [mock.call(secret_key) for _, secret_key in key_pairs]
        )
        self.assertEqual(mock_keylist.call_count, len(key_pairs))
        mock_keylist.assert_has_calls(
            [
                mock.call(pattern=key.fingerprint, secret=True)
                for key, _ in key_pairs
            ]
        )
        mock_signers.assert_called_with(keylist)
        mock_sign.assert_called_once()
        self.assertEqual(signature_path.read_bytes(), signature_bytes)

    def test_sign_openpgp_not_detached_or_clear(self) -> None:
        """Key signing error: signing mode not detached or clear."""
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        temp_path = self.create_temporary_directory()
        (data_path := temp_path / "data").write_bytes(b"data to sign")
        signature_path = temp_path / "data.asc"

        with self.assertRaisesRegex(
            GnuPGError,
            "OpenPGP signing currently only supports detached or clear "
            "signatures",
        ):
            # Bypass debusine.signing.db.models.sign, since it also catches
            # this.
            gpg_sign(
                [(key.stored_private_key, key.public_key)],
                data_path,
                signature_path,
                SigningMode.ATTACHED,
            )

    def test_sign_openpgp_not_software_encrypted(self) -> None:
        """Key signing error: OpenPGP key is not software-encrypted."""
        public_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint()
        key = Key.objects.create(
            purpose=Key.Purpose.OPENPGP,
            fingerprint=fingerprint,
            private_key=ProtectedKeyPKCS11Static.create(
                pkcs11_uri="pkcs11:id=1"
            ).dict(),
            public_key=public_key,
        )
        temp_path = self.create_temporary_directory()
        (data_path := temp_path / "data").write_bytes(b"data to sign")
        signature_path = temp_path / "data.asc"

        with self.assertRaisesRegex(
            GnuPGError,
            "OpenPGP signing currently only supports software-encrypted keys",
        ):
            sign([key], data_path, signature_path, SigningMode.CLEAR, 123)

    def test_sign_openpgp_key_import_failed(self) -> None:
        """Key signing error: failed to import OpenPGP secret key."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        import_status = ImportStatus(None)
        import_status.fpr = key.fingerprint
        import_result = ImportResult(None)
        import_result.not_imported = 1
        temp_path = self.create_temporary_directory()
        (data_path := temp_path / "data").write_bytes(b"data to sign")
        signature_path = temp_path / "data.asc"

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch("gpg.Context.key_import", return_value=import_result),
            self.assertRaisesRegex(GnuPGError, "Failed to import secret key"),
        ):
            sign([key], data_path, signature_path, SigningMode.CLEAR, 123)

    def test_sign_openpgp_debsign(self) -> None:
        """Sign an upload using an OpenPGP key and debsign."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        import_status = ImportStatus(None)
        import_status.fpr = key.fingerprint
        import_result = ImportResult(None)
        import_result.secret_imported = 1
        import_result.imports = [import_status]
        unsigned_path = self.create_temporary_directory()
        (unsigned_path / "foo.dsc").write_text("dsc\n")
        (unsigned_changes_path := unsigned_path / "foo.changes").write_text(
            "changes\n"
        )
        signed_path = self.create_temporary_directory()
        signed_dsc_path = signed_path / "foo.dsc"
        signed_changes_path = signed_path / "foo.changes"
        signature_dsc = "signed dsc\n"
        signature_changes = "signed changes\n"
        log_file = io.BytesIO()

        def run(
            args: list[str | os.PathLike[str]], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            if args[0] == "debsign":
                changes_path = Path(args[-1])
                dsc_path = changes_path.parent / f"{changes_path.stem}.dsc"
                dsc_path.write_text(signature_dsc)
                changes_path.write_text(signature_changes)
            return subprocess.CompletedProcess(args=args, returncode=0)

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch(
                "gpg.Context.key_import", return_value=import_result
            ) as mock_key_import,
            mock.patch("subprocess.run", side_effect=run) as mock_run,
        ):
            sign(
                [key],
                unsigned_changes_path,
                signed_changes_path,
                SigningMode.DEBSIGN,
                123,
                log_file=log_file,
            )

        mock_key_import.assert_called_once_with(secret_key)
        mock_run.assert_any_call(
            [
                "debsign",
                f"-k{key.fingerprint}",
                "--re-sign",
                signed_changes_path,
            ],
            cwd=signed_changes_path.parent,
            env={**os.environ, "GNUPGHOME": mock.ANY},
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
        self.assertEqual(signed_dsc_path.read_text(), signature_dsc)
        self.assertEqual(signed_changes_path.read_text(), signature_changes)

    def test_sign_openpgp_debsign_not_software_encrypted(self) -> None:
        """Key signing error: OpenPGP key is not software-encrypted."""
        public_key = random.randbytes(64)
        fingerprint = make_fake_fingerprint()
        key = Key.objects.create(
            purpose=Key.Purpose.OPENPGP,
            fingerprint=fingerprint,
            private_key=ProtectedKeyPKCS11Static.create(
                pkcs11_uri="pkcs11:id=1"
            ).dict(),
            public_key=public_key,
        )
        unsigned_path = self.create_temporary_directory()
        (unsigned_changes_path := unsigned_path / "foo.changes").write_text(
            "changes\n"
        )
        signed_path = self.create_temporary_directory()
        signed_changes_path = signed_path / "foo.changes"

        with self.assertRaisesRegex(
            GnuPGError,
            "OpenPGP signing currently only supports software-encrypted keys",
        ):
            sign(
                [key],
                unsigned_changes_path,
                signed_changes_path,
                SigningMode.DEBSIGN,
                123,
            )

    def test_sign_openpgp_debsign_key_import_failed(self) -> None:
        """Key signing error: failed to import OpenPGP secret key."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        import_status = ImportStatus(None)
        import_status.fpr = key.fingerprint
        import_result = ImportResult(None)
        import_result.not_imported = 1
        unsigned_path = self.create_temporary_directory()
        (unsigned_changes_path := unsigned_path / "foo.changes").write_text(
            "changes\n"
        )
        signed_path = self.create_temporary_directory()
        signed_changes_path = signed_path / "foo.changes"

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch("gpg.Context.key_import", return_value=import_result),
            self.assertRaisesRegex(GnuPGError, "Failed to import secret key"),
        ):
            sign(
                [key],
                unsigned_changes_path,
                signed_changes_path,
                SigningMode.DEBSIGN,
                123,
            )

    def test_sign_openpgp_debsign_signature_directory_not_empty(self) -> None:
        """Key signing error: directory containing signature_path not empty."""
        # Signing data is slow, so we mock most of this and just test the
        # sequence of operations.  Integration tests will make sure that it
        # actually works.
        storage_private_key = PrivateKey.generate()
        key, secret_key = self.create_nacl_key(
            storage_private_key=storage_private_key, purpose=Key.Purpose.OPENPGP
        )
        import_status = ImportStatus(None)
        import_status.fpr = key.fingerprint
        import_result = ImportResult(None)
        import_result.secret_imported = 1
        import_result.imports = [import_status]
        unsigned_path = self.create_temporary_directory()
        (unsigned_changes_path := unsigned_path / "foo.changes").write_text(
            "changes\n"
        )
        signed_path = self.create_temporary_directory()
        signed_changes_path = signed_path / "foo.changes"
        (signed_path / "foo.dsc").touch()

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            mock.patch("gpg.Context.key_import", return_value=import_result),
            self.assertRaisesRegex(
                GnuPGError, "Directory containing signature_path is not empty"
            ),
        ):
            sign(
                [key],
                unsigned_changes_path,
                signed_changes_path,
                SigningMode.DEBSIGN,
                123,
            )
