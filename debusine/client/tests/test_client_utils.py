# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the client_utils.py methods."""
import hashlib
import logging
from textwrap import dedent
from typing import Any
from unittest import mock

import requests
import responses
from debian.deb822 import Changes, Deb822

from debusine.artifacts.playground import ArtifactPlayground
from debusine.client.client_utils import (
    copy_file,
    dget,
    download_file,
    get_debian_package,
    get_url_contents_sha256sum,
    requests_put_or_connection_error,
)
from debusine.client.exceptions import (
    ClientConnectionError,
    ContentTooLargeError,
    ContentValidationError,
)
from debusine.test import TestCase


class RequestsPutOrConnectionErrorTests(TestCase):
    """Tests for requests_put_or_connection_error."""

    def test_requests_put_raise_exc_without_request(self) -> None:
        """
        requests_put_or_connection_error handles exceptions without a request.

        This is for 100% branch coverage; as far as we know,
        :py:meth:`requests.put` always sets the ``request`` attribute on
        exceptions.
        """
        patcher = mock.patch("debusine.client.client_utils.requests.put")
        mocked = patcher.start()
        mocked.side_effect = requests.exceptions.RequestException(request=None)
        self.addCleanup(patcher.stop)

        with self.assertRaisesRegex(
            ClientConnectionError, r"^Cannot connect\. Error: "
        ):
            requests_put_or_connection_error()


class DgetDownloadTests(TestCase):
    """Tests for our dget client."""

    def setUp(self) -> None:
        """Configure fake http responses."""
        self.workdir = self.create_temporary_directory()

        self.r_mock = responses.RequestsMock(assert_all_requests_are_fired=True)
        self.r_mock.start()
        self.addCleanup(self.r_mock.stop)
        self.addCleanup(self.r_mock.reset)

        # Swallow log messages
        log = logging.getLogger("debusine.client.client_utils")
        propagate_setting = log.propagate
        log.propagate = False
        self.addCleanup(lambda: setattr(log, "propagate", propagate_setting))

        def mock_get(
            *args: Any, **kwargs: Any
        ) -> responses.BaseResponse:  # pragma: no cover
            """Polyfill for .get() added in responses 0.19.0."""
            return self.r_mock.add(responses.GET, *args, **kwargs)

        def mock_upsert(
            *args: Any, **kwargs: Any
        ) -> responses.BaseResponse:  # pragma: no cover
            """Polyfill for .upsert() added in responses 0.13.0."""
            try:
                return self.r_mock.replace(*args, **kwargs)
            except ValueError:
                return self.r_mock.add(*args, **kwargs)

        if not hasattr(self.r_mock, "get"):  # pragma: no cover
            self.r_mock.get = mock_get
        if not hasattr(self.r_mock, "upsert"):  # pragma: no cover
            self.r_mock.upsert = mock_upsert  # type: ignore[method-assign]

        self.bodies = {
            "foo.deb": b"abc123",
            "foo_1.0.orig.tar.xz": b"test data",
            "foo_1.0-2.debian.tar.xz": b"more test data",
            "foo_1.0-2_source.buildinfo": b"Format: 1.0\nSource: foo\n",
        }
        for filename, body in self.bodies.items():
            self.r_mock.get(f"http://example.com/{filename}", body=body)
        self.set_dsc_response()
        self.set_changes_response()
        self.expected_stats = {
            "foo.deb": {
                "sha256": "6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d239259"
                "3af6a84118090",
                "sha1": "6367c48dd193d56ea7b0baad25b19455e529f5ee",
                "md5": "e99a18c428cb38d5f260853678922e03",
                "size": 6,
            },
        }

    def _set_response(
        self,
        filename: str,
        body: str,
        files: list[str],
        break_hashes: set[str] = set(),
        break_sizes: set[str] = set(),
    ) -> None:
        file_type: type[Deb822]
        if filename.endswith(".changes"):
            file_type = Changes
        else:
            file_type = Deb822
        full_body = (
            body
            + ArtifactPlayground.hash_deb822_files(
                file_type=file_type,
                file_contents={file: self.bodies[file] for file in files},
                break_hashes=break_hashes,
                break_sizes=break_sizes,
            )
        ).encode('utf-8')
        self.bodies[filename] = full_body
        self.r_mock.upsert(
            responses.GET, f"http://example.com/{filename}", body=full_body
        )

    def set_dsc_response(
        self,
        break_hashes: set[str] = set(),
        break_sizes: set[str] = set(),
    ) -> None:
        """Add / replace the .dsc response."""
        self._set_response(
            "foo_1.0-2.dsc",
            dedent(
                """
                Format: 3.0 (quilt)
                Source: foo
                Package-List:
                 foo deb unknown optional arch=any
                """
            ).lstrip(),
            ["foo_1.0.orig.tar.xz", "foo_1.0-2.debian.tar.xz"],
            break_hashes=break_hashes,
            break_sizes=break_sizes,
        )

    def set_changes_response(self) -> None:
        """Add / replace the .changes response."""
        self._set_response(
            "foo_1.0-2_source.changes",
            dedent(
                """
                Format: 1.8
                Source: foo
                """
            ).lstrip(),
            [
                "foo_1.0-2.dsc",
                "foo_1.0-2.debian.tar.xz",
                "foo_1.0-2_source.buildinfo",
            ],
        )

    def test_copy_file_stats(self) -> None:
        src = self.workdir / "src.deb"
        dest = self.workdir / "foo.deb"
        src.write_bytes(self.bodies["foo.deb"])
        stats = copy_file(src, dest)
        self.assertEqual(self.expected_stats["foo.deb"], stats)

    def test_download_file_downloads(self) -> None:
        """Check `download_file` writes the expected file."""
        dest = self.workdir / "foo.deb"
        download_file("http://example.com/foo.deb", dest)
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), b"abc123")

    def test_download_file_stats(self) -> None:
        """Check the return value of `download_file`."""
        dest = self.workdir / "foo.deb"
        stats = download_file("http://example.com/foo.deb", dest)
        self.assertEqual(self.expected_stats["foo.deb"], stats)

    def test_download_file_logging(self) -> None:
        """Ensure `download_file` logs its requests."""
        dest = self.workdir / "foo.deb"
        with self.assertLogsContains(
            message="Downloading http://example.com/foo.deb...",
            logger="debusine.client.client_utils",
        ):
            download_file("http://example.com/foo.deb", dest)

    def test_download_file_size(self) -> None:
        """Ensure `download_file` logs the size of files it's downloading."""
        dest = self.workdir / "big.deb"
        self.r_mock.get(
            "http://example.com/big.deb",
            body=b"1234567890" * 1000,
            headers={
                "Content-Length": "10000",
            },
        )
        with self.assertLogs(logger="debusine.client.client_utils") as cm:
            download_file("http://example.com/big.deb", dest)
        self.assertEqual(2, len(cm.records))
        messages = [record.message for record in cm.records]
        self.assertIn("Size: 0.01 MiB", messages)

    def test_dget_file(self) -> None:
        """Test that `dget` can download a .deb."""
        dget("http://example.com/foo.deb", self.workdir)
        dest = self.workdir / "foo.deb"
        self.assertTrue(dest.exists())

    def test_dget_subdir(self) -> None:
        """Ensure `dget` correctly handles sub-directories."""
        dest = self.workdir / "test.deb"
        self.r_mock.get(
            "http://example.com/subdir/test.deb",
            body=b"abc123",
        )
        dget("http://example.com/subdir/test.deb", self.workdir)
        self.assertTrue(dest.exists())

    def test_dget_dsc(self) -> None:
        """Test that `dget` can download a .dsc."""
        dget("http://example.com/foo_1.0-2.dsc", self.workdir)
        self.assertTrue((self.workdir / "foo_1.0-2.dsc").exists())
        self.assertTrue((self.workdir / "foo_1.0-2.debian.tar.xz").exists())
        self.assertTrue((self.workdir / "foo_1.0.orig.tar.xz").exists())

    def test_dget_source_changes(self) -> None:
        """Test that `dget` can download a _source.changes."""
        dget("http://example.com/foo_1.0-2_source.changes", self.workdir)
        self.assertTrue((self.workdir / "foo_1.0-2_source.changes").exists())
        self.assertTrue((self.workdir / "foo_1.0-2.dsc").exists())
        self.assertTrue((self.workdir / "foo_1.0-2.debian.tar.xz").exists())
        # Test that we recurse through the .dsc to find the orig tarball
        self.assertTrue((self.workdir / "foo_1.0.orig.tar.xz").exists())

    def test_dget_only_downloads_files_once(self) -> None:
        """Check that `dget` downloads aren't unnecessarily repeated."""
        with self.assertLogs(logger="debusine.client.client_utils") as cm:
            dget("http://example.com/foo_1.0-2_source.changes", self.workdir)
        records = [
            record.message
            for record in cm.records
            if record.message.startswith("Downloading")
        ]
        self.assertEqual(len(set(records)), len(records))
        self.assertTrue((self.workdir / "foo_1.0-2_source.changes").exists())
        self.assertTrue((self.workdir / "foo_1.0-2.dsc").exists())
        self.assertTrue((self.workdir / "foo_1.0-2.debian.tar.xz").exists())
        self.assertTrue((self.workdir / "foo_1.0.orig.tar.xz").exists())

    def test_dget_garbage_dsc(self) -> None:
        """If we can't parse it, `dget` simply downloads the .dsc."""
        self.r_mock.get(
            "http://example.com/garbage.dsc",
            body=b"\0:" * 100,
        )
        dget("http://example.com/garbage.dsc", self.workdir)
        self.assertTrue((self.workdir / "garbage.dsc").exists())

    def test_dget_garbage_changes(self) -> None:
        """If we can't parse it, `dget` simply downloads the .changes."""
        self.r_mock.get(
            "http://example.com/garbage.changes",
            body=b"\0:" * 100,
        )
        dget("http://example.com/garbage.changes", self.workdir)
        self.assertTrue((self.workdir / "garbage.changes").exists())

    def test_dget_verifies_sha256(self) -> None:
        """`dget` verifies SHA256 hashes."""
        self.set_dsc_response(break_hashes={"sha256"})
        with self.assertRaisesRegex(
            ContentValidationError,
            r"Downloaded file has mis-matching sha256 for "
            r"foo_1\.0\.orig\.tar\.xz \([0-9a-f]{64} != [0-9a-f]{64}\)",
        ):
            dget("http://example.com/foo_1.0-2.dsc", self.workdir)

    def test_dget_verifies_sha1(self) -> None:
        """`dget` verifies SHA1 hashes."""
        self.set_dsc_response(break_hashes={"sha1"})
        with self.assertRaisesRegex(
            ContentValidationError,
            r"Downloaded file has mis-matching sha1 for "
            r"foo_1\.0\.orig\.tar\.xz \([0-9a-f]{40} != [0-9a-f]{40}\)",
        ):
            dget("http://example.com/foo_1.0-2.dsc", self.workdir)

    def test_dget_verifies_md5(self) -> None:
        """`dget` verifies MD5 hashes."""
        self.set_dsc_response(break_hashes={"md5"})
        with self.assertRaisesRegex(
            ContentValidationError,
            r"Downloaded file has mis-matching md5 for "
            r"foo_1\.0\.orig\.tar\.xz \([0-9a-f]{32} != [0-9a-f]{32}\)",
        ):
            dget("http://example.com/foo_1.0-2.dsc", self.workdir)

    def test_dget_verifies_size(self) -> None:
        """`dget` will spot size mismatch between entries in dsc file."""
        self.set_dsc_response(break_sizes={"sha256"})
        with self.assertRaises(ContentValidationError) as cm:
            dget("http://example.com/foo_1.0-2.dsc", self.workdir)
        self.assertEqual(
            "foo_1.0-2.dsc has mis-matching size for foo_1.0.orig.tar.xz "
            "(9 != 10)",
            str(cm.exception),
        )

    def test_dget_verifies_previously_downloaded_hash(self) -> None:
        """`dget` spots files differing between .dsc and .changes files."""
        self.set_dsc_response(break_hashes={"sha256"})
        self.set_changes_response()
        with self.assertRaisesRegex(
            ContentValidationError,
            r"foo_1.0-2\.dsc has mis-matching sha256 for "
            r"foo_1\.0-2\.debian\.tar\.xz \([0-9a-f]{64} != [0-9a-f]{64}\)",
        ):
            dget("http://example.com/foo_1.0-2_source.changes", self.workdir)

    def test_dget_rejects_path_traversal(self) -> None:
        """`dget` will refuse to download a dsc file with / in filenames."""
        self.r_mock.get(
            "http://example.com/traversal.dsc",
            body=dedent(
                """
                Format: 3.0 (quilt)
                Source: foo
                Package-List:
                 foo deb unknown optional arch=any
                Checksums-Sha1:
                 f48dd853820860816c75d54d0f584dc863327a7c 9 foo/bar.tar.xz
                Files:
                 eb733a00c0c9d336e65691a37ab54293 9 foo/bar.tar.xz
                """
            )
            .lstrip()
            .encode('utf-8'),
        )
        with self.assertRaises(ContentValidationError) as cm:
            dget("http://example.com/traversal.dsc", self.workdir)
        self.assertEqual(
            "traversal.dsc contains invalid file name foo/bar.tar.xz",
            str(cm.exception),
        )

    def test_get_debian_package_local_file(self) -> None:
        """`get_debian_package` will return a Path to a local .deb file."""
        source = self.workdir / "foo.deb"
        source.write_bytes(b"test file")
        with get_debian_package(str(source)) as package:
            self.assertEqual(package, source)

    def test_get_debian_package_file_url(self) -> None:
        """`get_debian_package` will return a Path to a file://...deb URL."""
        source = self.workdir / "foo.deb"
        source.write_bytes(b"test file")
        with get_debian_package(f"file://{source}") as package:
            self.assertEqual(package, source)

    def test_get_debian_package_http_url(self) -> None:
        """`get_debian_package` will download a deb by http."""
        with get_debian_package("http://example.com/foo.deb") as package:
            self.assertEqual(package.read_bytes(), b"abc123")

    def test_get_debian_package_ftp_url(self) -> None:
        """`get_debian_package` can't take ftp:// URLs."""
        with self.assertRaisesRegex(ValueError, r"Not a supported URL scheme:"):
            with get_debian_package("ftp://example.com/foo.deb"):
                pass  # pragma: no cover


class GetUrlContentsSha256sumTests(TestCase):
    """Tests for get_url_contents_sha256sum function."""

    @responses.activate
    def test_get_url_contents_sha256sum(self) -> None:
        """get_url_contents_sha256sum function return contents and sha256."""
        url = "https://example.com"
        max_size = 100
        content = b"Test content"
        sha256sum = hashlib.sha256(content).hexdigest()

        responses.add(responses.GET, url, body=content)

        returned_content, returned_sha256sum = get_url_contents_sha256sum(
            url, max_size
        )

        self.assertEqual(returned_content, content)
        self.assertEqual(returned_sha256sum, sha256sum)

    @responses.activate
    def test_get_url_contents_sha256sum_too_large(self) -> None:
        """get_url_contents_sha256sum function raise ContentTooLargeError."""
        url = "https://example.com"
        max_size = 10
        content = b'Test content that is too large for the specified max_size'

        responses.add(responses.GET, url, body=content)

        message = "Content size exceeds maximum allowed size of 10 bytes."
        with self.assertRaisesRegex(ContentTooLargeError, message):
            get_url_contents_sha256sum(url, max_size)

    def test_file_url_without_allow_file(self) -> None:
        """Without allow_file=True, file:// URLs are rejected."""
        path = self.create_temporary_file()

        with self.assertRaises(requests.exceptions.InvalidSchema):
            get_url_contents_sha256sum(f"file://{path}", 10)

    def test_file_url_with_allow_file(self) -> None:
        """With allow_file=True, file:// URLs work."""
        max_size = 100
        content = b"Test content"
        sha256sum = hashlib.sha256(content).hexdigest()
        path = self.create_temporary_file(contents=content)

        returned_content, returned_sha256sum = get_url_contents_sha256sum(
            f"file://{path}", max_size, allow_file=True
        )

        self.assertEqual(returned_content, content)
        self.assertEqual(returned_sha256sum, sha256sum)
