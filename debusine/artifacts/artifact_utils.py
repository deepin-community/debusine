# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utils for the artifacts module."""

from collections.abc import Mapping
from pathlib import Path

from debian import deb822 as deb822
from debusine.client.models import FileRequest


def files_in_meta_file_match_files(
    metafile_extension: str,
    metafile_reader: type[deb822.Deb822],
    files: Mapping[str, Path | FileRequest],
) -> None:
    """
    Raise ValidationError if files in the metafile do not match files.

    It searches a file in files with the metafile_extension. Check that
    the files listed in the metafile (a .dsc or .changes) are in files. And
    that no extra files in files.

    :param metafile_extension: for example, .dsc or .changes.
    :param metafile_reader: for example, deb822.Dsc or deb822.Changes.
    :param files: files in the artifact.
    """
    metafile: Path | None = None

    # Find the metafile.
    for file_name, file_path in files.items():
        if file_name.endswith(metafile_extension):
            assert isinstance(file_path, Path)
            metafile = file_path
            break
    # Always available because a previous validator verified it.
    if metafile is None:
        raise AssertionError(
            f"{files} does not contain any file ending with "
            f"{metafile_extension}"
        )

    with metafile.open() as metafile_obj:
        metafile_files = metafile_reader(metafile_obj)

    files_in_metafile: set[str] = set()

    for file in metafile_files.get("Files", []):
        files_in_metafile.add(file["name"])

    names_in_files = {f for f in files if not f.endswith(metafile_extension)}

    if names_in_files != files_in_metafile:
        raise ValueError(
            f"Files in the package and listed in the {metafile_extension} "
            f"must match."
            f"Files: {sorted(files.keys())} "
            f"Listed in {metafile_extension}: {sorted(files_in_metafile)}"
        )
