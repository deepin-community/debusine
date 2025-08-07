# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Implementation of local file backend."""

import filecmp
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Self

from django.conf import settings

from debusine.db.models import File, FileStore
from debusine.server.file_backend.interface import (
    FileBackendEntryInterface,
    FileBackendInterface,
)
from debusine.server.file_backend.models import LocalFileBackendConfiguration

logger = logging.getLogger(__name__)


class LocalFileBackendEntry(
    FileBackendEntryInterface["LocalFileBackend", PurePath]
):
    """
    An entry in a local file backend.

    Local identifiers are the part of the file's path under the base
    directory.
    """

    @classmethod
    def _create_from_file(
        cls, backend: "LocalFileBackend", fileobj: File
    ) -> Self:
        """Make an entry based on a `File`."""
        hash_hex = fileobj.hash_digest.hex()
        identifier = (
            PurePath(hash_hex[0:2])
            / hash_hex[2:4]
            / hash_hex[4:6]
            / f"{hash_hex}-{fileobj.size}"
        )
        return cls(backend=backend, identifier=identifier)

    def __str__(self) -> str:
        """Return a string describing this entry."""
        return str(self.get_local_path())

    def get_local_path(self) -> Path:
        """Return the path to a local copy of the file."""
        return self.backend._base_directory / self._identifier

    @contextmanager
    def get_temporary_local_path(self) -> Generator[Path]:
        """Yield the path to a possibly-temporary local copy of the file."""
        yield self.get_local_path()

    def get_url(self, content_type: str | None = None) -> None:  # noqa: U100
        """Return None: no remote URL for a file in LocalFileBackend."""
        return None

    @staticmethod
    def _create_subdirectories(
        base_directory: Path, subdirectory: Path
    ) -> None:
        """
        Create the subdirectory in base_directory.

        :param base_directory: directory (must exist) to create the subdirectory
        :param subdirectory: such as "a/b/c", created in base_directory
        """
        accumulating = base_directory

        for part in subdirectory.parts:
            accumulating = accumulating / Path(part)
            accumulating.mkdir(exist_ok=True)

    @classmethod
    def _sync_file(cls, path: Path) -> None:
        """Flush `path` and its directory to disk."""
        # same approach (open mode) as sync from coreutils

        # coreutils include O_NONBLOCK to avoid blocking if the file
        # is a FIFO. Files in debusine are not FIFOs
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as exc:
            logger.debug(  # noqa: G200
                "Could not open %s for flushing (%s)", path, exc
            )
            return

        os.fsync(fd)
        os.close(fd)

        if path.is_file():
            cls._sync_file(path.parent)

    def add(self, local_path: Path) -> None:
        """Add the contents of `local_path` to the underlying storage."""
        destination_file = self.get_local_path()
        destination_directory = destination_file.parent

        self._create_subdirectories(
            self.backend._base_directory,
            destination_directory.relative_to(self.backend._base_directory),
        )

        # To make it easy to identify files that were not finished
        # copying: copy to a temp file + rename
        temporary_file = tempfile.NamedTemporaryFile(
            dir=destination_directory, suffix=".temp", delete=False
        )
        temporary_file.close()

        shutil.copy(local_path, temporary_file.name)
        os.rename(temporary_file.name, destination_file)

        self._sync_file(destination_file)

    def same_contents(self, local_path: Path) -> bool:
        """Check whether this entry has the same contents as `local_path`."""
        # This standard library function maintains a cache indexed by the
        # paths and stat signatures (type, size, mtime) of both files.  We
        # currently assume that the `local_path` argument is always a
        # recently-created file, so the cache will never be hit.  If this
        # assumption no longer holds, then we may want to inline something
        # like `filecmp._do_cmp` instead.
        return filecmp.cmp(local_path, self.get_local_path(), shallow=False)

    def remove(self) -> None:
        """Remove the entry from the underlying storage."""
        file_path = self.get_local_path()
        file_path.unlink(missing_ok=True)


class LocalFileBackend(FileBackendInterface[LocalFileBackendConfiguration]):
    """Local file backend (in settings.DEBUSINE_STORE_DIRECTORY)."""

    def __init__(self, file_store: FileStore) -> None:
        """Initialize LocalFileBackend."""
        super().__init__()
        self.db_store = file_store

        if (base_directory := self.configuration.base_directory) is None:
            if file_store.name == "Default":
                base_directory = settings.DEBUSINE_STORE_DIRECTORY
            else:
                raise RuntimeError(
                    f'LocalFileBackend {file_store.name} configuration '
                    'requires "base_directory" setting'
                )

        self._base_directory = Path(base_directory)

    def base_directory(self) -> Path:
        """Return the base_directory of this backend."""
        return self._base_directory

    def get_entry(self, fileobj: File) -> LocalFileBackendEntry:
        """Return the entry in this backend corresponding to this file."""
        return LocalFileBackendEntry._create_from_file(self, fileobj)

    def list_entries(
        self, mtime_filter: Callable[[datetime], bool] | None = None
    ) -> Generator[LocalFileBackendEntry, None, None]:
        """
        Yield entries in this backend.

        :param mtime_filter: If given, only yield entries where this
          callable returns True when given their modification time.  The
          callable may assume that its argument is an aware datetime.
        """
        for root, dirnames, filenames in os.walk(self._base_directory):
            reldirpath = PurePath(root).relative_to(self._base_directory)
            dirnames.sort()
            for filename in sorted(filenames):
                relpath = reldirpath / filename
                if mtime_filter is not None and not mtime_filter(
                    datetime.fromtimestamp(
                        (self._base_directory / relpath).stat().st_mtime,
                        # In general POSIX timestamps are inherently
                        # timezone-naive, but we can reasonably assume that
                        # a debusine store is on a sufficiently Unix-like
                        # file system to be UTC.
                        tz=timezone.utc,
                    )
                ):
                    continue
                yield LocalFileBackendEntry(
                    backend=self, identifier=reldirpath / filename
                )
