# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for functions in debusine.project_utils."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from debusine.project import project_utils
from debusine.test.django import TestCase


class ProjectUtilsTests(TestCase):
    """Tests for functions in defaults.py."""

    SECRET_KEY = "this-is-a-secret-key"

    def create_secret_key_file(self, permissions: int) -> Path:
        """Create a secret key file, schedules deletion after test."""
        file = self.create_temporary_file(
            contents=self.SECRET_KEY.encode("utf-8")
        )
        file.chmod(permissions)
        return file

    def test_read_secret_key_reads_key(self) -> None:
        """read_secret_key return the key in the file."""
        secret_key_file = self.create_secret_key_file(0o600)
        self.assertEqual(
            self.SECRET_KEY, project_utils.read_secret_key(secret_key_file)
        )

    def test_read_secret_key_cannot_read_file(self) -> None:
        """read_secret_key cannot read the file, raises ImproperlyConfigured."""
        with self.assertRaisesRegex(ImproperlyConfigured, "Cannot read"):
            project_utils.read_secret_key(
                "/tmp/a-file-that-does-not-exist-24241"
            )

    def test_read_secret_key_file_permissions_too_open(self) -> None:
        """read_secret_key file permissions too open, raises exception."""
        secret_key_file = self.create_secret_key_file(0o640)

        with self.assertRaisesRegex(ImproperlyConfigured, "too open"):
            project_utils.read_secret_key(secret_key_file)
