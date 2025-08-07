# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the SbuildValidatorMixin class."""

from pathlib import Path

from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData
from debusine.tasks.sbuild_validator_mixin import SbuildValidatorMixin
from debusine.tasks.tests.helper_mixin import TestBaseExternalTask
from debusine.test import TestCase
from debusine.utils import calculate_hash


class TaskWithSbuildValidatorMixin(
    SbuildValidatorMixin,
    TestBaseExternalTask[BaseTaskData, BaseDynamicTaskData],
):
    """Minimum class to test SbuildValidatorMixin."""

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SbuildValidatorMixinTests(TestCase):
    """Tests for SbuildValidatorMixin class."""

    def setUp(self) -> None:
        """Set up test."""
        self.sbuild_validator_mixin = TaskWithSbuildValidatorMixin({})

    def test_check_directory_for_consistency_errors_no_errors(self) -> None:
        """check_directory_for_consistency_errors() return no errors."""
        sbuild_directory = self.create_temporary_directory()

        (file1 := sbuild_directory / "file1.deb").write_text("First file")
        (file2 := sbuild_directory / "file2.deb").write_text("Second file")

        # build_file does not cause any error
        (
            build_file := sbuild_directory
            / "hello_2.10-2_amd64-2023-05-08T10:04:40Z.build"
        ).write_text("the build file")

        # build_directory does not cause any error
        (sbuild_directory / "hello_2.10-2_amd64.build").symlink_to(build_file)

        changes_file = sbuild_directory / "hello.changes"

        (sbuild_directory / "hello.dsc").write_text("")

        self.write_changes_file(changes_file, [file1, file2])

        self.assertEqual(
            self.sbuild_validator_mixin.check_directory_for_consistency_errors(
                sbuild_directory
            ),
            [],
        )

    def test_check_directory_for_consistency_errors_no_changes_file(
        self,
    ) -> None:
        """check_directory_for_consistency_errors() handles missing .changes."""
        sbuild_directory = self.create_temporary_directory()

        self.sbuild_validator_mixin.check_directory_for_consistency_errors(
            sbuild_directory
        )

    def test_check_directory_for_consistency_errors_missing_file(self) -> None:
        """check_directory_for_consistency_errors() return one file missing."""
        sbuild_directory = self.create_temporary_directory()

        (file1 := sbuild_directory / "file1.deb").write_text("First file")
        (file2 := sbuild_directory / "file2.deb").write_text("Second file")

        changes_file = sbuild_directory / "hello.changes"

        self.write_changes_file(changes_file, [file1, file2])

        file2.unlink()

        expected_errors = [
            'File in .changes section Checksums-Sha1 '
            'does not exist: "file2.deb"',
            'File in .changes section Checksums-Sha256 '
            'does not exist: "file2.deb"',
            'File in .changes section Files does not exist: "file2.deb"',
        ]

        self.assertEqual(
            self.sbuild_validator_mixin.check_directory_for_consistency_errors(
                sbuild_directory
            ),
            expected_errors,
        )

    def test_check_directory_for_consistency_errors_files_in_dsc_no_error(
        self,
    ) -> None:
        """
        check_directory_for_consistency_errors(): no error.

        File is in .dsc and in the directory.
        """
        sbuild_directory = self.create_temporary_directory()

        (file_in_dsc := sbuild_directory / "a_file.txt").write_text("Some file")
        dsc_file = sbuild_directory / "hello.dsc"
        self.write_dsc_file(dsc_file, [file_in_dsc])

        changes_file = sbuild_directory / "hello.changes"
        self.write_changes_file(changes_file, [dsc_file])

        self.assertEqual(
            self.sbuild_validator_mixin.check_directory_for_consistency_errors(
                sbuild_directory
            ),
            [],
        )

    def test_check_directory_for_consistency_errors_invalid_hash(self) -> None:
        """
        check_directory_for_consistency_errors() return an error.

        A file has an invalid hash.
        """
        sbuild_directory = self.create_temporary_directory()

        (file := sbuild_directory / "file1.deb").write_text("First file")
        expected_size = file.stat().st_size
        expected_sha256 = calculate_hash(file, "sha256").hex()
        expected_sha1 = calculate_hash(file, "sha1").hex()
        expected_md5 = calculate_hash(file, "md5").hex()

        changes_file = sbuild_directory / "hello.changes"

        self.write_changes_file(changes_file, [file])

        file.write_text("Something else")

        actual_size = file.stat().st_size
        actual_sha256 = calculate_hash(file, "sha256").hex()
        actual_sha1 = calculate_hash(file, "sha1").hex()
        actual_md5 = calculate_hash(file, "md5").hex()

        def format_error(
            hash_algo: str,
            filename: str,
            expected_hash: str,
            actual_hash: str,
            expected_size: int,
            actual_size: int,
        ) -> str:
            return (
                f'{hash_algo} for file {filename} does not match: '
                f'expected: {expected_hash} actual: {actual_hash}. '
                f'Size expected: {expected_size} actual: {actual_size}'
            )

        expected_errors = [
            format_error(
                "sha1",
                file.name,
                expected_sha1,
                actual_sha1,
                expected_size,
                actual_size,
            ),
            format_error(
                "sha256",
                file.name,
                expected_sha256,
                actual_sha256,
                expected_size,
                actual_size,
            ),
            format_error(
                "md5",
                file.name,
                expected_md5,
                actual_md5,
                expected_size,
                actual_size,
            ),
        ]

        self.assertEqual(
            self.sbuild_validator_mixin.check_directory_for_consistency_errors(
                sbuild_directory
            ),
            expected_errors,
        )

    def test_check_directory_for_consistency_errors_extra_file(self) -> None:
        """
        check_directory_for_consistency_errors() return an error.

        There is an extra file in the directory.
        """
        build_directory = self.create_temporary_directory()
        changes_file = build_directory / "file.changes"

        (file1 := build_directory / "file1.deb").write_text("First file")

        self.write_changes_file(changes_file, [file1])

        (file2 := build_directory / "file2.deb").write_text("Second file")

        self.assertEqual(
            self.sbuild_validator_mixin.check_directory_for_consistency_errors(
                build_directory
            ),
            [
                "Files in directory not referenced in "
                f".changes or .dsc: [\'{str(file2)}\']"
            ],
        )
