# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Mixin to validate a sbuild build."""
from pathlib import Path

from debian.deb822 import Deb822

import debusine.utils
from debusine.utils import calculate_hash, read_dsc


class SbuildValidatorMixin:
    """
    Implement check_directory_for_consistency_errors for a Sbuild build.

    Encapsulate related methods.
    """

    def check_directory_for_consistency_errors(
        self, build_directory: Path
    ) -> list[str]:
        """
        Validate build_directory.

        Verify that no unexpected files in the directory, all the files
        in .changes exist in the directory and have the correct hash, etc.

        :return: list of errors (empty if no errors).
        """
        errors: list[str] = []

        files_in_directory: set[Path] = set(build_directory.glob("*"))

        # Files ending in .dsc, .changes or .build: exist in the directory
        # but are not taken in account for the checks: remove them from
        # files_in_directory
        remove_from_files_in_directory = debusine.utils.find_files_suffixes(
            build_directory,
            [".dsc", ".changes", ".build"],
            include_symlinks=True,
        )

        for file in remove_from_files_in_directory:
            files_in_directory.discard(file)

        self._validate_changes_file_contents(
            build_directory, errors, files_in_directory
        )

        self._validate_dsc_file_contents(
            build_directory, errors, files_in_directory
        )

        # Check that no unexpected files exist in the directory
        # but not listed in the .changes or .dsc files
        if len(files_in_directory) > 0:
            files_in_directory_str = list(map(str, sorted(files_in_directory)))

            errors.append(
                f"Files in directory not referenced "
                f"in .changes or .dsc: {files_in_directory_str}"
            )

        return errors

    @classmethod
    def _validate_dsc_file_contents(
        cls,
        build_directory: Path,
        errors: list[str],
        files_in_directory: set[Path],
    ) -> None:
        dsc_file = debusine.utils.find_file_suffixes(build_directory, [".dsc"])

        dsc = read_dsc(dsc_file)

        if dsc is None:
            return

        cls._validate_deb822_file(
            dsc, build_directory, errors, files_in_directory
        )

    @classmethod
    def _validate_changes_file_contents(
        cls,
        build_directory: Path,
        errors: list[str],
        files_in_directory: set[Path],
    ) -> None:
        changes = debusine.utils.read_changes(build_directory)

        if changes is None:
            return

        cls._validate_deb822_file(
            changes, build_directory, errors, files_in_directory
        )

    @staticmethod
    def _validate_deb822_file(
        contents: Deb822,
        build_directory: Path,
        errors: list[str],
        files_in_directory: set[Path],
    ) -> None:
        # hash algorithm to section
        hash_algorithm_to_section = {
            "sha1": "Checksums-Sha1",
            "sha256": "Checksums-Sha256",
            "md5": "Files",
        }
        # Each algorithm has a different field name when reading from
        # the deb822 file
        hash_algorithm_to_field = {
            "sha1": "sha1",
            "sha256": "sha256",
            "md5": "md5sum",
        }

        # It checks the file hashes for the given algorithms
        for hash_algorithm in ["sha1", "sha256", "md5"]:
            section_name = hash_algorithm_to_section[hash_algorithm]

            for file_in_changes in contents[section_name]:
                file = build_directory / file_in_changes["name"]

                if not file.is_file():
                    errors.append(
                        f'File in .changes section {section_name} '
                        f'does not exist: "{file.name}"'
                    )
                    continue

                field_name = hash_algorithm_to_field[hash_algorithm]
                expected_hash = file_in_changes[field_name]
                actual_hash = calculate_hash(file, hash_algorithm).hex()

                if expected_hash != actual_hash:
                    expected_size = file_in_changes["size"]
                    actual_size = file.stat().st_size
                    errors.append(
                        f"{hash_algorithm} "
                        f"for file {file.name} does not match: "
                        f"expected: {expected_hash} actual: {actual_hash}. "
                        f"Size expected: {expected_size} actual: {actual_size}"
                    )

                if file in files_in_directory:
                    files_in_directory.remove(file)
