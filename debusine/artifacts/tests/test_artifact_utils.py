# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for utils.py."""

from debian import deb822
from debusine.artifacts import artifact_utils
from debusine.test import TestCase


class ArtifactUtilsTests(TestCase):
    """Tests for functions in artifact_utils.py."""

    def test_files_in_meta_file_match_files_return_None(self) -> None:
        """Files in files match files in .dsc: return None."""
        file_1 = self.create_temporary_file()

        dsc_file = self.create_temporary_file(suffix=".dsc")

        self.write_dsc_file(dsc_file, files=[file_1])

        files = {
            file_1.name: file_1,
            dsc_file.name: dsc_file,
        }

        # Does not raise an exception.
        artifact_utils.files_in_meta_file_match_files(".dsc", deb822.Dsc, files)

    def test_files_in_meta_file_extra_file_in_files_raise_value_error(
        self,
    ) -> None:
        """Files in files contain extra file (not listed in .dsc)."""
        file_1 = self.create_temporary_file(prefix="b")
        file_2 = self.create_temporary_file(prefix="c")

        dsc_file = self.create_temporary_file(prefix="a", suffix=".dsc")

        self.write_dsc_file(dsc_file, files=[file_2])

        files = {
            dsc_file.name: dsc_file,
            file_1.name: file_1,
        }

        files_str = fr"'{dsc_file.name}', '{file_1.name}'"
        files_listed_in_dsc = fr"'{file_2.name}'"

        expected_msg = (
            fr"^Files in the package and listed in the \.dsc must match."
            fr"Files: \[{files_str}\] "
            fr"Listed in \.dsc: \[{files_listed_in_dsc}\]$"
        )
        with self.assertRaisesRegex(ValueError, expected_msg):
            artifact_utils.files_in_meta_file_match_files(
                ".dsc", deb822.Dsc, files
            )

    def test_files_in_meta_file_missing_file_in_files_raise_value_error(
        self,
    ) -> None:
        """Files in file misses a file listed in .dsc."""
        file_1 = self.create_temporary_file()

        dsc_file = self.create_temporary_file(suffix=".changes")

        self.write_changes_file(dsc_file, files=[file_1])

        files = {
            dsc_file.name: dsc_file,
        }

        files_str = fr"'{dsc_file.name}'"
        files_listed_in_dsc = fr"'{file_1.name}'"

        expected_msg = (
            fr"^Files in the package and listed in the \.changes must match."
            fr"Files: \[{files_str}\] "
            fr"Listed in \.changes: \[{files_listed_in_dsc}\]$"
        )

        with self.assertRaisesRegex(ValueError, expected_msg):
            artifact_utils.files_in_meta_file_match_files(
                ".changes", deb822.Changes, files
            )

    def test_file_contains_file_in_changes_make_loop_complete(self) -> None:
        """
        files_in_meta_file_match_files: no metadata file found.

        It cannot happen in normal circumstances: another validator for files
        is used before and raise a ValidationError for lack of metadata.

        Implemented here to have 100% branch coverage.
        """
        with self.assertRaises(AssertionError):
            artifact_utils.files_in_meta_file_match_files(
                "no_exist", deb822.Dsc, {}
            )
