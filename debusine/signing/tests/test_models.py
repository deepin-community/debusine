# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the signing models."""

import random

from django.test import TestCase
from nacl.public import PrivateKey

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.signing.models import (
    AvailableKeyFileSystem,
    AvailableKeyPKCS11,
    ProtectedKeyError,
    ProtectedKeyNaCl,
    ProtectedKeyPKCS11Static,
)


class ProtectedKeyNaClTests(TestCase):
    """Unit tests for `ProtectedKeyNaCl`."""

    def test_encrypt_error(self) -> None:
        """Encryption errors raise ProtectedKeyError."""
        # This doesn't fail in normal use unless libsodium is broken.  Force
        # it to fail using a type error.
        public_key = PrivateKey.generate().public_key
        with self.assertRaisesRegex(
            ProtectedKeyError, "input message must be bytes"
        ):
            ProtectedKeyNaCl.encrypt(
                public_key,
                "should be bytes",  # type: ignore[arg-type]
            )

    def test_decrypt_wrong_key(self) -> None:
        """Decrypting with the wrong key raises ProtectedKeyError."""
        private_keys = [PrivateKey.generate() for _ in range(2)]
        protected = ProtectedKeyNaCl.encrypt(
            private_keys[0].public_key, b"data"
        )
        with self.assertRaisesRegex(
            ProtectedKeyError,
            "Key not encrypted using any of the given private keys",
        ):
            protected.decrypt([private_keys[1]])

    def test_decrypt_error(self) -> None:
        """Other decryption errors raise ProtectedKeyError."""
        private_key = PrivateKey.generate()
        protected = ProtectedKeyNaCl.create(
            public_key=bytes(private_key.public_key), encrypted=b"nonsense" * 8
        )
        with self.assertRaisesRegex(
            ProtectedKeyError, "An error occurred trying to decrypt the message"
        ):
            protected.decrypt([private_key])

    def test_encrypt_decrypt(self) -> None:
        """Encrypting and decrypting some data works."""
        private_key = PrivateKey.generate()
        protected = ProtectedKeyNaCl.encrypt(private_key.public_key, b"data")
        self.assertEqual(protected.decrypt([private_key]), b"data")

    def test_encrypt_decrypt_rotated(self) -> None:
        """The key-encryption key can be rotated."""
        private_keys = [PrivateKey.generate() for _ in range(2)]
        protected = ProtectedKeyNaCl.encrypt(
            private_keys[1].public_key, b"data"
        )
        self.assertEqual(protected.decrypt(private_keys), b"data")

    def test_available(self) -> None:
        """The key can be made temporarily available."""
        storage_private_key = PrivateKey.generate()
        private_key = random.randbytes(64)
        public_key = random.randbytes(64)
        protected = ProtectedKeyNaCl.encrypt(
            storage_private_key.public_key, private_key
        )

        with (
            self.settings(DEBUSINE_SIGNING_PRIVATE_KEYS=[storage_private_key]),
            protected.available(public_key) as available,
        ):
            assert isinstance(available, AvailableKeyFileSystem)
            temp_dir = available.temp_dir
            key_path = temp_dir / "tmp.key"
            certificate_path = temp_dir / "tmp.crt"
            self.assertEqual(key_path.read_bytes(), private_key)
            self.assertEqual(certificate_path.read_bytes(), public_key)
            self.assertEqual(
                available.sbsign_args,
                ["--key", str(key_path), "--cert", str(certificate_path)],
            )

        # After the context manager exits, the key and certificate are no
        # longer on the file system.
        self.assertFalse(temp_dir.exists())


class ProtectedKeyPKCS11StaticTests(TestCase):
    """Unit tests for `ProtectedKeyPKCS11Static`."""

    def test_bad_pkcs11_uri(self) -> None:
        """The PKCS#11 URI must use the "pkcs11" scheme."""
        with self.assertRaisesRegex(
            pydantic.ValidationError, "pkcs11_uri must start with 'pkcs11:'"
        ):
            ProtectedKeyPKCS11Static.create(pkcs11_uri="https://example.com/")

    def test_available(self) -> None:
        """The key can be made temporarily available."""
        public_key = random.randbytes(64)
        protected = ProtectedKeyPKCS11Static.create(pkcs11_uri="pkcs11:id=1")

        with protected.available(public_key) as available:
            assert isinstance(available, AvailableKeyPKCS11)
            temp_dir = available.temp_dir
            certificate_path = temp_dir / "tmp.crt"
            self.assertEqual(certificate_path.read_bytes(), public_key)
            self.assertEqual(
                available.sbsign_args,
                [
                    "--engine",
                    "pkcs11",
                    "--key",
                    "pkcs11:id=1",
                    "--cert",
                    str(certificate_path),
                ],
            )

        # After the context manager exits, the certificate is no longer on
        # the file system.
        self.assertFalse(temp_dir.exists())
