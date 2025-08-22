# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tar-related classes: generate tar.gz for artifacts, etc."""

import logging
import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterator

from debusine.db.models import Artifact, File


class TarArtifact:
    """Create a .tar.gz file."""

    def __init__(
        self, artifact: Artifact, subdirectory: str | None = None
    ) -> None:
        """Initialize member variables."""
        self._artifact = artifact

        self._files = self._get_file_list(artifact, subdirectory)
        self._pos = 0
        self._max_chunk_size = 50 * 1024 * 1024

        self._temp_directory = tempfile.mkdtemp(
            prefix=f"debusine-artifact-download-{artifact.id}"
        )

        tar_file = os.path.join(
            self._temp_directory, f"artifact-{artifact.id}.tar.gz"
        )

        self._tar = tarfile.open(tar_file, mode="w|gz")
        self._tar_closed = False

        self._tar_file = open(tar_file, "rb")

        self._block_size = os.stat(tar_file).st_blksize
        self._bytes_deleted = 0

    @staticmethod
    def _get_file_list(
        artifact: Artifact, subdirectory: str | None
    ) -> dict[str, File]:
        """Return dictionary of file paths - file objects to be included."""
        files = {}

        if subdirectory is None:
            subdirectory = ""

        for file_in_artifact in (
            artifact.fileinartifact_set.select_related("file")
            .filter(path__startswith=subdirectory, complete=True)
            .order_by("-path")
        ):
            files[file_in_artifact.path] = file_in_artifact.file

        return files

    def __iter__(self) -> Iterator[bytes]:
        """Self is an iterator."""
        return self

    def __next__(self) -> bytes:
        """
        Return data or raise StopIteration if any exception happened.

        StopIteration if any exception occurred is needed to make debusine
        server to close the connection with the client and to avoid the
        client waiting for new data when no new data will be generated.

        The exception is logged for debugging purposes.
        """
        try:
            return self.next()
        except Exception as exc:
            if not isinstance(exc, StopIteration):
                logging.exception(exc)  # noqa: G200
            self._tar_file.close()
            shutil.rmtree(self._temp_directory)
            raise StopIteration

    def next(self) -> bytes:
        """Return pending data (from the last added file) and adds a file."""
        data = self._tar_file.read(self._max_chunk_size)

        if data != b"":
            # No need to add files, close, etc. if data can be
            # returned
            return data

        # No data read: add files, close Tar, StopIterating, etc.
        if len(self._files) > 0:
            # No data read and there are new files to add
            path, fileobj = self._files.popitem()

            file_backend = self._artifact.workspace.scope.download_file_backend(
                fileobj
            )
            with file_backend.get_stream(fileobj) as file:
                tarinfo = tarfile.TarInfo(path)
                tarinfo.size = fileobj.size
                mtime = self._artifact.created_at.timestamp()
                tarinfo.mtime = mtime
                self._tar.addfile(tarinfo, file)
        elif self._tar_closed:
            # No data read and the tar file is closed: stop iterating
            raise StopIteration
        elif len(self._files) == 0:
            # No data read: close the file. On closing the file
            # some more data might be read on the next iteration
            self._tar.close()
            self._tar_closed = True
        else:
            raise AssertionError  # pragma: no cover

        return b""
