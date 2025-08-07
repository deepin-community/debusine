# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for ExternalDebianSuiteFileBackend."""

from pathlib import Path
from typing import ClassVar

import responses

from debusine.db.models import FileStore
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.file_backend.external import ExternalDebianSuiteFileBackend
from debusine.test.django import TestCase
from debusine.utils import NotSupportedError


class ExternalDebianSuiteFileBackendTests(TestCase):
    """Tests for ExternalDebianSuiteFileBackend."""

    db_store: ClassVar[FileStore]
    file_backend: ClassVar[ExternalDebianSuiteFileBackend]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize file backend to be tested."""
        super().setUpTestData()

        cls.db_store = FileStore.objects.create(
            name="ExternalDebianSuite",
            backend=FileStore.BackendChoices.EXTERNAL_DEBIAN_SUITE,
            configuration={
                "archive_root_url": "http://ftp.debian.org/debian/",
                "suite": "bookworm",
                "components": ["main", "contrib"],
            },
        )

        cls.file_backend = ExternalDebianSuiteFileBackend(cls.db_store)

    def test_entry_str(self) -> None:
        """ExternalFileBackendEntry.__str__ returns the URL."""
        file = self.file_backend.add_remote_file(
            "pool/main/c/coreutils/coreutils_9.1-1_amd64.deb",
            _calculate_hash_from_data(b"test"),
            len(b"test"),
        )
        entry = self.file_backend.get_entry(file)
        expected_url = (
            "http://ftp.debian.org/debian/"
            "pool/main/c/coreutils/coreutils_9.1-1_amd64.deb"
        )
        self.assertEqual(str(entry), expected_url)

    @responses.activate
    def test_get_temporary_local_path(self) -> None:
        """`get_temporary_local_path` yields a temporary local path."""
        db_file = self.file_backend.add_remote_file(
            "pool/main/c/coreutils/coreutils_9.1-1_amd64.deb",
            bytes.fromhex(
                "61038f857e346e8500adf53a2a0a2085"
                "9f4d3a3b51570cc876b153a2d51a3091"
            ),
            2896560,
        )
        expected_url = (
            "http://ftp.debian.org/debian/"
            "pool/main/c/coreutils/coreutils_9.1-1_amd64.deb"
        )
        mocked_content = b"coreutils.deb_mocked_content"
        responses.add(responses.GET, expected_url, mocked_content)

        with self.file_backend.get_temporary_local_path(db_file) as temp_path:
            self.assertEqual(temp_path.read_bytes(), mocked_content)

    @responses.activate
    def test_remote_file(self) -> None:  # pragma: no cover
        """Create a remote file."""
        db_file = self.file_backend.add_remote_file(
            "pool/main/c/coreutils/coreutils_9.1-1_amd64.deb",
            bytes.fromhex(
                "61038f857e346e8500adf53a2a0a2085"
                "9f4d3a3b51570cc876b153a2d51a3091"
            ),
            2896560,
        )

        expected_url = (
            "http://ftp.debian.org/debian/"
            "pool/main/c/coreutils/coreutils_9.1-1_amd64.deb"
        )
        self.assertEqual(
            self.file_backend.get_url(db_file),
            expected_url,
        )

        self.assertIsNone(self.file_backend.get_local_path(db_file))

        mocked_content = b"coreutils.deb_mocked_content"
        responses.add(
            responses.GET,
            expected_url,
            mocked_content,
        )

        content = b''
        with self.file_backend.get_stream(db_file) as stream:
            content += stream.read()
        self.assertEqual(content, mocked_content)

        with self.assertRaises(NotSupportedError):
            self.file_backend.remove_file(db_file)

        with self.assertRaises(NotSupportedError):
            self.file_backend.add_file(Path('a/b/c.deb'), db_file)
