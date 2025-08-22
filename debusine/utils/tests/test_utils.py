# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for utils module."""

import hashlib
import stat
from pathlib import Path
from unittest import mock

from requests.structures import CaseInsensitiveDict

from debian import deb822
from debusine.test import TestCase
from debusine.utils import (
    CALCULATE_HASH_CHUNK_SIZE,
    DjangoChoicesEnum,
    atomic_writer,
    calculate_hash,
    find_file_suffixes,
    find_files_suffixes,
    is_command_available,
    parse_content_range_header,
    parse_range_header,
    read_changes,
    read_dsc,
)


class TestUtilsTests(TestCase):
    """Tests for the test_utils functions."""

    def test_calculate_hash_small_file(self) -> None:
        """calculate_hash return the correct sha256 for the file."""
        contents = b"debusine"
        sha256_expected = hashlib.sha256(contents).digest()

        temporary_file = self.create_temporary_file(contents=contents)

        self.assertEqual(
            calculate_hash(Path(temporary_file), "sha256"),
            sha256_expected,
        )

    def test_calculate_hash_big_file(self) -> None:
        """
        calculate_hash return the correct sha256 for a big file.

        A big file is a file bigger than CALCULATE_HASH_CHUNK_SIZE.
        """
        size_to_write = int(CALCULATE_HASH_CHUNK_SIZE * 2.5)

        contents = bytearray(size_to_write)

        temporary_file = self.create_temporary_file(contents=contents)

        self.assertEqual(
            calculate_hash(Path(temporary_file), "sha256"),
            hashlib.sha256(contents).digest(),
        )

    def test_parse_range_header_no_header(self) -> None:
        """parse_range_header return None: no "Range" header."""
        self.assertIsNone(parse_range_header(CaseInsensitiveDict({}), 30))

    def test_parse_range_header_invalid_header(self) -> None:
        """parse_range_header raise ValueError: invalid header."""
        invalid_header = "invalid-header"
        error = f'Invalid Range header: "{invalid_header}"'
        with self.assertRaisesRegex(ValueError, error):
            parse_range_header(
                CaseInsensitiveDict({"Range": invalid_header}), 30
            )

    def test_parse_range_header_no_positions(self) -> None:
        """parse_range_header raise ValueError: neither start nor end."""
        invalid_header = "bytes=-"
        error = f'Invalid Range header: "{invalid_header}"'
        with self.assertRaisesRegex(ValueError, error):
            parse_range_header(
                CaseInsensitiveDict({"Range": invalid_header}), 30
            )

    def test_parse_range_header(self) -> None:
        """parse_range_header return dictionary with start and end."""
        for range_header, expected in (
            ("bytes=10-24", {"start": 10, "end": 24}),
            ("bytes=10-", {"start": 10, "end": 29}),
            ("bytes=-10", {"start": 20, "end": 29}),
        ):
            with self.subTest(range_header=range_header):
                headers = CaseInsensitiveDict({"Range": range_header})
                self.assertEqual(parse_range_header(headers, 30), expected)

    def test_parse_content_range_header_no_header(self) -> None:
        """parse_content_range_header return None: no "Content-Range"."""
        self.assertIsNone(parse_content_range_header({}))

    def test_parse_content_range_header_start_end_size(self) -> None:
        """parse_content_range_header return dict with integers."""
        for header, expected in (
            ("bytes 10-20/30", {"start": 10, "end": 20, "size": 30}),
            ("bytes 10-20/*", {"start": 10, "end": 20, "size": "*"}),
            ("bytes */30", {"start": "*", "end": None, "size": 30}),
            ("bytes */*", {"start": "*", "end": None, "size": "*"}),
        ):
            with self.subTest(header=header):
                headers = {"Content-Range": header}

                self.assertEqual(parse_content_range_header(headers), expected)

    def test_parse_content_range_header_invalid_header(self) -> None:
        """parse_content_range_header raise ValueError: invalid header."""
        invalid_header = "invalid-header"
        error = f'Invalid Content-Range header: "{invalid_header}"'
        with self.assertRaisesRegex(ValueError, error):
            parse_content_range_header({"Content-Range": invalid_header})

    def test_read_dsc_file(self) -> None:
        """_read_dsc return a deb822.Dsc object: it was a valid Dsc file."""
        dsc_file = self.create_temporary_file()
        self.write_dsc_example_file(dsc_file)

        dsc = read_dsc(dsc_file)

        self.assertIsInstance(dsc, deb822.Dsc)

    def test_read_dsc_file_invalid(self) -> None:
        """_read_dsc return None: it was a not a valid Dsc file."""
        dsc_file = self.create_temporary_file(contents=b"invalid dsc file")

        dsc = read_dsc(dsc_file)

        self.assertIsNone(dsc)

    def test_read_changes_file(self) -> None:
        """read_changes returns deb822.Changes for a valid .changes file."""
        build_directory = self.create_temporary_directory()
        changes_file = build_directory / "foo.changes"
        self.write_changes_file(changes_file, [])

        changes = read_changes(build_directory)

        self.assertIsInstance(changes, deb822.Changes)

    def test_read_changes_file_missing(self) -> None:
        """read_changes returns None if the directory has no .changes file."""
        build_directory = self.create_temporary_directory()

        changes = read_changes(build_directory)

        self.assertIsNone(changes)

    def test_find_file_suffixes_multiple_files(self) -> None:
        """find_file_suffixes() return two files with different suffixes."""
        temporary_directory = self.create_temporary_directory()

        # Add a deb file
        (deb := temporary_directory / "file.deb").write_text("deb")

        # Add a udeb file
        (udeb := temporary_directory / "file.udeb").write_text("deb")

        # Add a file that is not returned
        (temporary_directory / "README.txt").write_text("README")

        # Check deb and udeb files
        self.assertEqual(
            find_files_suffixes(temporary_directory, [".deb", ".udeb"]),
            sorted([deb, udeb]),
        )

    def test_find_file_suffix_raise_runtime_error(self) -> None:
        """find_file() raise RuntimeError: two possible log files."""
        temporary_directory = self.create_temporary_directory()

        suffix = "build"

        (file1 := temporary_directory / f"log1.{suffix}").write_text("Log1")
        (file2 := temporary_directory / f"log2.{suffix}").write_text("Log2")

        # assertRaisesRegex must have the chars [] escaped
        files = (
            str(sorted([str(file1), str(file2)]))
            .replace("[", "")
            .replace("]", "")
        )

        with self.assertRaisesRegex(
            RuntimeError, rf"^More than one \['{suffix}'\] file: \[{files}\]$"
        ):
            find_file_suffixes(temporary_directory, [suffix])

    def test_find_files_suffix(self) -> None:
        """find_files_suffix() return multiple files."""
        temporary_directory = self.create_temporary_directory()

        # Create two files to be returned
        (deb1_file := temporary_directory / "hello.deb").write_text("First")
        (deb2_file := temporary_directory / "hello-2.deb").write_text("Second")

        # Create a non-relevant file
        (temporary_directory / "README.txt").write_text("a test")

        # Create a file that could match (*.deb) but is a symbolic link
        (temporary_directory / "temp.deb").symlink_to(deb1_file)

        # Create a file that could match (*.deb) but is a directory
        (temporary_directory / "debian.deb").mkdir()

        self.assertEqual(
            find_files_suffixes(temporary_directory, [".deb"]),
            sorted([deb1_file, deb2_file]),
        )

    def test_find_files_suffix_include_symbolic_links(self) -> None:
        """
        find_files_suffix() return files including symbolic links.

        The option include_symlinks is True.
        """
        temporary_directory = self.create_temporary_directory()

        # Create one file and one symbolic link to the file
        file_name = "file.txt"
        symbolic_link_name = "symbolic.txt"
        (file := temporary_directory / file_name).write_text("test")
        (symbolic_link := temporary_directory / symbolic_link_name).symlink_to(
            file
        )

        self.assertEqual(
            find_files_suffixes(
                temporary_directory, [".txt"], include_symlinks=True
            ),
            [file, symbolic_link],
        )

    def test_find_file_suffixes_one_file(self) -> None:
        """find_file_suffixes() return the expected file."""
        temporary_directory = self.create_temporary_directory()

        # Add a log file
        (log_file := temporary_directory / "log.build").write_text("log")

        # Add a file that is not returned
        (temporary_directory / "README.txt").write_text("README")

        # Check return the correct log file
        self.assertEqual(
            find_file_suffixes(temporary_directory, [".build"]),
            log_file,
        )

    def patch_shutil_which(self, available: bool) -> None:
        """Patch shutil.which to respond either positively or negatively."""
        patcher = mock.patch(
            "shutil.which", return_value="/usr/bin/test" if available else None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_is_command_available_yes(self) -> None:
        """is_command_available returns True if the command exists."""
        self.patch_shutil_which(True)
        self.assertTrue(is_command_available("test"))

    def test_is_command_available_no(self) -> None:
        """is_command_available returns False if the command does not exist."""
        self.patch_shutil_which(False)
        self.assertFalse(is_command_available("test"))

    def test_choices(self) -> None:
        """Test that .choices returns the values in Django choices pairs."""

        class TestChoices(DjangoChoicesEnum):
            FOO = "foo"
            BAR = "bar"

        self.assertEqual(
            list(TestChoices.choices), [("foo", "foo"), ("bar", "bar")]
        )

    def test_atomic_writer(self) -> None:
        """``atomic_writer`` writes to the target path."""
        temp_dir = self.create_temporary_directory()
        path = temp_dir / "file"

        with atomic_writer(path) as fd:
            fd.write(b"contents\n")
            self.assertFalse(path.exists())
            [temp_path] = temp_dir.iterdir()
            self.assertEqual(temp_path.parent, temp_dir)
            self.assertRegex(temp_path.name, r"^file.*\.new$")

        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), b"contents\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(temp_dir.iterdir()), [path])

    def test_atomic_writer_text(self) -> None:
        """``atomic_writer`` can write to the target path as text."""
        temp_dir = self.create_temporary_directory()
        path = temp_dir / "file"

        with atomic_writer(path, mode="w") as fd:
            fd.write("contents\n")
            self.assertFalse(path.exists())
            [temp_path] = temp_dir.iterdir()
            self.assertEqual(temp_path.parent, temp_dir)
            self.assertRegex(temp_path.name, r"^file.*\.new$")

        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(), "contents\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(temp_dir.iterdir()), [path])

    def test_atomic_writer_chmod(self) -> None:
        """``atomic_writer`` can set the target path's permissions."""
        temp_dir = self.create_temporary_directory()
        path = temp_dir / "file"

        with atomic_writer(path, chmod=0o644) as fd:
            fd.write(b"contents\n")
            self.assertFalse(path.exists())
            [temp_path] = temp_dir.iterdir()
            self.assertEqual(temp_path.parent, temp_dir)
            self.assertRegex(temp_path.name, r"^file.*\.new$")

        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), b"contents\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(list(temp_dir.iterdir()), [path])

    def test_atomic_writer_exceptions(self) -> None:
        """``atomic_writer`` cleans up after exceptions."""
        temp_dir = self.create_temporary_directory()
        path = temp_dir / "file"

        with self.assertRaisesRegex(RuntimeError, "Boom"), atomic_writer(path):
            raise RuntimeError("Boom")

        self.assertEqual(list(temp_dir.iterdir()), [])
