# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Implementation of in-memory file backend."""

import contextlib
import io
import logging
import tempfile
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Self

from debusine.db.models import File, FileStore
from debusine.server.file_backend.interface import (
    FileBackendEntryInterface,
    FileBackendInterface,
)
from debusine.server.file_backend.models import MemoryFileBackendConfiguration

logger = logging.getLogger(__name__)


class MemoryFileBackendEntry(
    FileBackendEntryInterface["MemoryFileBackend", str]
):
    """
    An entry in an in-memory file backend.

    In-memory identifiers are the hex digest of the file contents.
    """

    @classmethod
    def _create_from_file(
        cls, backend: "MemoryFileBackend", fileobj: File
    ) -> Self:
        """Make an entry based on a `File`."""
        hash_hex = fileobj.hash_digest.hex()
        return cls(backend=backend, identifier=hash_hex)

    def __str__(self) -> str:
        """Return a string describing this entry."""
        return self._identifier

    def get_local_path(self) -> None:
        """Return None: no local paths for in-memory files."""
        return None

    @contextmanager
    def get_temporary_local_path(self) -> Generator[Path]:
        """Yield the path to a possibly-temporary local copy of the file."""
        with tempfile.NamedTemporaryFile(
            prefix="debusine-memory-"
        ) as temp_file:
            temp_file.write(self.backend.storage[self._identifier])
            temp_file.flush()
            yield Path(temp_file.name)

    def get_url(self, content_type: str | None = None) -> None:  # noqa: U100
        """Return None: no remote URLs for in-memory files."""
        return None

    def add(self, local_path: Path) -> None:
        """Add the contents of `local_path` to the underlying storage."""
        self.backend.storage[File.calculate_hash(local_path).hex()] = (
            local_path.read_bytes()
        )

    def same_contents(self, local_path: Path) -> bool:
        """Check whether this entry has the same contents as `local_path`."""
        return self.backend.storage[self._identifier] == local_path.read_bytes()

    def remove(self) -> None:
        """Remove the entry from the underlying storage."""
        self.backend.storage.pop(self._identifier, None)


class MemoryFileBackend(FileBackendInterface[MemoryFileBackendConfiguration]):
    """In-memory file backend (used for tests)."""

    # In-memory file storages, indexed by configuration names
    storages: dict[str, dict[str, bytes]] = defaultdict(dict)

    def __init__(self, file_store: FileStore) -> None:
        """Initialize MemoryFileBackend."""
        super().__init__()
        self.db_store = file_store
        self.storage = self.storages[self.configuration.name]

    def get_entry(self, fileobj: File) -> MemoryFileBackendEntry:
        """Return the entry in this backend corresponding to this file."""
        return MemoryFileBackendEntry._create_from_file(self, fileobj)

    def _store_file(self, hash_hex: str, value: bytes) -> None:
        """Add/update a file in the storage."""
        # This is here mainly so it can be more easily mocked than a dict
        # assignment
        self.storage[hash_hex] = value

    @contextlib.contextmanager
    def get_stream(self, fileobj: File) -> Generator[io.BytesIO, None, None]:
        """Yield a file-like object that can be read."""
        hash_hex = fileobj.hash_digest.hex()
        with io.BytesIO(self.storage[hash_hex]) as out:
            yield out

    def list_entries(
        self,
        mtime_filter: Callable[[datetime], bool] | None = None,  # noqa: U100
    ) -> Generator[MemoryFileBackendEntry, None, None]:
        """
        Yield entries in this backend.

        The `mtime_filter` parameter from the interface is not currently
        implemented, because this backend does not record file modification
        times.
        """
        assert mtime_filter is None
        for hash_hex in self.storage:
            yield MemoryFileBackendEntry(backend=self, identifier=hash_hex)
