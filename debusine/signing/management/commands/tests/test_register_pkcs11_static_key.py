# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command register_pkcs11_static_key."""

import os
import random
import re
import subprocess
from typing import Any
from unittest import mock

from django.core.management import CommandError
from django.test import TestCase as DjangoTestCase
from django.test import override_settings

from debusine.django.management.tests import call_command
from debusine.signing.db.models import AuditLog, Key
from debusine.signing.models import ProtectedKeyPKCS11Static
from debusine.signing.tests import make_fake_fingerprint
from debusine.test import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class RegisterPKCS11StaticKeyCommandTests(TestCase, DjangoTestCase):
    """Tests for the register_pkcs11_static_key command."""

    def test_register(self) -> None:
        """`register_pkcs11_static_key` registers a new key."""
        pkcs11_uri = "pkcs11:id=%00%01"
        public_key = random.randbytes(64)
        public_key_file = self.create_temporary_file()
        public_key_file.write_bytes(public_key)
        fingerprint = make_fake_fingerprint()

        def run(
            args: list[str | os.PathLike[str]], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            fp_colons = ":".join(re.findall("..", fingerprint))
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=f"sha256 Fingerprint={fp_colons}\n".encode(),
            )

        with mock.patch("subprocess.run", side_effect=run) as mock_run:
            stdout, stderr, exit_code = call_command(
                "register_pkcs11_static_key",
                "uefi",
                pkcs11_uri,
                str(public_key_file),
                "Test key",
            )

        mock_run.assert_called_once_with(
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
            stderr=None,
        )
        key = Key.objects.get(purpose=Key.Purpose.UEFI, fingerprint=fingerprint)
        assert isinstance(
            key.stored_private_key.__root__, ProtectedKeyPKCS11Static
        )
        self.assertEqual(key.stored_private_key.__root__.pkcs11_uri, pkcs11_uri)
        self.assertEqual(bytes(key.public_key), public_key)
        audit_log = AuditLog.objects.latest("created_at")
        self.assertEqual(audit_log.purpose, Key.Purpose.UEFI)
        self.assertEqual(audit_log.fingerprint, fingerprint)
        self.assertEqual(audit_log.event, AuditLog.Event.REGISTER)
        self.assertEqual(audit_log.data, {"description": "Test key"})
        self.assertIsNone(audit_log.created_by_work_request_id)

    def test_register_duplicated_purpose_and_fingerprint(self) -> None:
        """`register_pkcs11_static_key` returns error: duplicate purpose/fp."""
        pkcs11_uri = "pkcs11:id=%00%01"
        public_key = random.randbytes(64)
        public_key_file = self.create_temporary_file()
        public_key_file.write_bytes(public_key)
        fingerprint = make_fake_fingerprint()
        Key.objects.create(
            purpose=Key.Purpose.UEFI,
            fingerprint=fingerprint,
            private_key={},
            public_key=public_key,
        )

        def run(
            args: list[str | os.PathLike[str]], **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            fp_colons = ":".join(re.findall("..", fingerprint))
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=f"sha256 Fingerprint={fp_colons}\n".encode(),
            )

        with (
            self.assertRaisesRegex(
                CommandError,
                r"^Error creating key: "
                r"Key with this Purpose and Fingerprint already exists\.$",
            ) as exc,
            mock.patch("subprocess.run", side_effect=run),
        ):
            call_command(
                "register_pkcs11_static_key",
                "uefi",
                pkcs11_uri,
                str(public_key_file),
                "Test key",
            )

        self.assertEqual(exc.exception.returncode, 3)
