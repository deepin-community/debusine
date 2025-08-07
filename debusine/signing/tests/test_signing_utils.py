# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for functions in debusine.signing.signing_utils."""

import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase as DjangoTestCase
from nacl.public import PrivateKey

from debusine.signing.signing_utils import (
    SensitiveTemporaryDirectory,
    read_private_key,
)
from debusine.test import TestCase


class SensitiveTemporaryDirectoryTests(TestCase):
    """Unit tests for `SensitiveTemporaryDirectory`."""

    def test_cleanup_warnings_are_scary(self) -> None:
        """Warnings about lack of cleanup have a suitably alarming message."""
        temp_dir = SensitiveTemporaryDirectory()

        with warnings.catch_warnings(record=True) as warnings_log:
            warnings.simplefilter("always")
            del temp_dir

        self.assertEqual(len(warnings_log), 1)
        self.assertEqual(warnings_log[0].category, ResourceWarning)
        self.assertRegex(
            str(warnings_log[0].message),
            r"^Implicitly cleaning up <SensitiveTemporaryDirectory '.*'> "
            r"\(SENSITIVE\)",
        )


class ReadPrivateKeyTests(TestCase, DjangoTestCase):
    """Tests for `read_private_key`."""

    def create_private_key_file(
        self, contents: bytes, permissions: int = 0o600
    ) -> Path:
        """Create a temporary private key file."""
        file = self.create_temporary_file(contents=contents)
        file.chmod(permissions)
        return file

    def test_cannot_read_file(self) -> None:
        """Raise ImproperlyConfigured if we cannot read the file."""
        with self.assertRaisesRegex(ImproperlyConfigured, "Cannot read"):
            read_private_key(self.create_temporary_directory() / "nonexistent")

    def test_permissions_too_open(self) -> None:
        """Raise ImproperlyConfigured if file permissions are too open."""
        private_key_file = self.create_private_key_file(
            b"\0" * PrivateKey.SIZE, 0o640
        )

        with self.assertRaisesRegex(
            ImproperlyConfigured, "Permission too open"
        ):
            read_private_key(private_key_file)

    def test_wrong_size(self) -> None:
        """Raise ImproperlyConfigured if the file is the wrong size."""
        private_key_file = self.create_private_key_file(b"\0")

        with self.assertRaisesRegex(ImproperlyConfigured, "Cannot load key"):
            read_private_key(private_key_file)

    def test_good_key(self) -> None:
        """If the file contains a private key, return it."""
        private_key = PrivateKey.generate()
        private_key_file = self.create_private_key_file(bytes(private_key))

        self.assertEqual(read_private_key(private_key_file), private_key)
