# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Implementation of external file backends."""

import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Self

import requests
from requests_toolbelt.downloadutils.stream import stream_response_to_file

from debusine.db.models import File, FileInStore, FileStore
from debusine.server.file_backend.interface import (
    FileBackendEntryInterface,
    FileBackendInterface,
)
from debusine.server.file_backend.models import (
    ExternalDebianSuiteBackendConfiguration,
)
from debusine.utils import NotSupportedError


class ExternalDebianSuiteFileBackendEntry(
    FileBackendEntryInterface["ExternalDebianSuiteFileBackend", str]
):
    """
    An entry in an external repository.

    External repository identifiers are relative URLs under the archive root
    URL.
    """

    @classmethod
    def _create_from_file(
        cls, backend: "ExternalDebianSuiteFileBackend", fileobj: File
    ) -> Self:
        """Make an entry based on a `File`."""
        file_in_store = FileInStore.objects.get(
            file=fileobj, store=backend.db_store
        )
        relative_url = file_in_store.data["relative_url"]
        assert isinstance(relative_url, str)
        return cls(backend=backend, identifier=relative_url)

    def __str__(self) -> str:
        """Return a string describing this entry."""
        return self.get_url()

    def get_local_path(self) -> None:
        """
        Return None: no local path for now.

        This may change if we cache the remote file locally.
        """
        return None

    @contextmanager
    def get_temporary_local_path(self) -> Generator[Path]:
        """Yield a temporary local path for the file."""
        # TODO: Once we can assume Python >= 3.12, it would be better to use
        # delete_on_close=False here and call temp_file.close() instead of
        # temp_file.flush().
        with tempfile.NamedTemporaryFile(
            prefix="debusine-external-"
        ) as temp_file:
            with requests.get(self.get_url(), stream=True) as r:
                r.raise_for_status()
                stream_response_to_file(r, temp_file)
            temp_file.flush()
            yield Path(temp_file.name)

    def get_url(self, content_type: str | None = None) -> str:  # noqa: U100
        """
        Return a URL pointing to the content.

        We cannot override the Content-Type for external repositories.
        """
        return self.backend.archive_root_url + self._identifier

    def add(self, local_path: Path) -> None:
        """We cannot add files to external repositories."""
        raise NotImplementedError()

    def same_contents(self, local_path: Path) -> bool:
        """
        We cannot check file contents in external repositories.

        It would be possible, but there's no need since we cannot add files
        to them.
        """
        raise NotImplementedError()

    def remove(self) -> None:
        """
        Remove the entry from the underlying storage.

        This may change if we cache the remote file locally.
        """
        raise NotImplementedError()


class ExternalDebianSuiteFileBackend(
    FileBackendInterface[ExternalDebianSuiteBackendConfiguration]
):
    """Mirror an external repository in debusine."""

    def __init__(self, file_store: FileStore) -> None:
        """Initialize instance."""
        super().__init__()
        self.db_store = file_store
        # All files are relative to the archive root
        # https://wiki.debian.org/DebianRepository/Format
        self.archive_root_url = self.configuration.archive_root_url
        # For Collections to grab and parse the appropriate Packages files
        self.suite = self.configuration.suite
        self.components = self.configuration.components

    def get_entry(self, fileobj: File) -> ExternalDebianSuiteFileBackendEntry:
        """Return the entry in this backend corresponding to this file."""
        return ExternalDebianSuiteFileBackendEntry._create_from_file(
            self, fileobj
        )

    def remove_file(self, db_file: File) -> None:  # noqa: U100
        """
        Remove the file from the FileStore.

        This may change if we cache the remote file locally.
        """
        raise NotSupportedError(
            "ExternalDebianSuiteFileBackend is a read-only file backend"
        )

    def add_file(
        self, local_path: Path, db_file: File | None = None  # noqa: U100
    ) -> File:
        """
        Copy local_path to underlying storage and register it in the FileStore.

        The optional ``fileobj`` provides the File used to identify the content
        available in local_path.
        """
        raise NotSupportedError(
            "ExternalDebianSuiteFileBackend is a read-only file backend"
        )

    def add_remote_file(
        self,
        relative_url: str,
        hash_digest: bytes,
        size: int,
    ) -> File:
        """Register remote file in the DB."""
        db_file_kwargs: dict[str, Any] = {
            File.current_hash_algorithm: hash_digest,
            'size': size,
        }
        db_file, created = File.objects.get_or_create(**db_file_kwargs)
        FileInStore.objects.get_or_create(
            store=self.db_store,
            file=db_file,
            data={'relative_url': relative_url},
        )
        return db_file

    def list_entries(
        self,
        mtime_filter: Callable[[datetime], bool] | None = None,  # noqa: U100
    ) -> Generator[ExternalDebianSuiteFileBackendEntry, None, None]:
        """Yield entries in this backend."""
        # We cannot currently iterate over all files in an external suite
        # (though in principle it should be possible).
        raise NotImplementedError()
