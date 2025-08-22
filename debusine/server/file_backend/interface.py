# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Define interface of the file backend."""

import contextlib
import logging
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Generator, Hashable
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

import requests
from django.db import transaction

from debusine.db.models import File, FileInStore, FileStore
from debusine.server.file_backend.models import FileBackendConfiguration
from debusine.utils import NotSupportedError, extract_generic_type_arguments

logger = logging.getLogger(__name__)

FBC = TypeVar("FBC", bound=FileBackendConfiguration)
FBI = TypeVar("FBI", bound="FileBackendInterface[Any]")
H = TypeVar("H", bound=Hashable)


class FileBackendStream(Protocol):
    """Returned by :py:meth:`FileBackendEntryInterface.get_stream`."""

    def read(self, length: int = ..., /) -> bytes:
        """Read bytes from the file."""

    def fileno(self) -> int:
        """Return the file descriptor number, if available."""


class FileBackendEntryInterface(Generic[FBI, H], metaclass=ABCMeta):
    """A representation of an entry in a file backend."""

    # The backend containing this entry.
    backend: FBI

    # An identifier for the entry.  Each entry in the backend has a
    # different identifier; each type of backend decides how to format its
    # identifiers.
    _identifier: H

    def __init__(self, backend: FBI, identifier: H) -> None:
        """Initialize the entry."""
        self.backend = backend
        self._identifier = identifier

    def __eq__(self, other: object) -> bool:
        """Check equality with another entry."""
        if not isinstance(other, FileBackendEntryInterface):
            return NotImplemented
        return bool(
            self.backend.db_store == other.backend.db_store
            and self._identifier == other._identifier
        )

    def __hash__(self) -> int:
        """Return a hash of the entry."""
        return hash((self.backend.db_store, self._identifier))

    @abstractmethod
    def __str__(self) -> str:
        """Return a string describing this entry, ideally some kind of path."""

    @abstractmethod
    def get_local_path(self) -> Path | None:
        """Return the path to a local copy of the file when possible or None."""

    @contextlib.contextmanager
    @abstractmethod
    def get_temporary_local_path(self) -> Generator[Path]:
        """Yield the path to a possibly-temporary local copy of the file."""

    @abstractmethod
    def get_url(self, content_type: str | None = None) -> str | None:
        """
        Return a URL pointing to the content when possible or None.

        :param content_type: if set, request that the HTTP response to this
          URL should include this `Content-Type` field.  This is
          best-effort: some backends may not support it.
        """

    @contextlib.contextmanager
    def get_stream(self) -> Generator[FileBackendStream, None, None]:
        """
        Return a file-like object that can be read.

        This returns a local copy of the file if available, but can also
        stream from a remote URL.
        """
        if (local_path := self.get_local_path()) is not None:
            with open(local_path, "rb") as f:
                yield f
        elif (url := self.get_url()) is not None:
            with requests.get(url, stream=True) as r:
                r.raw.decode_content = True
                yield r.raw
        else:
            raise NotSupportedError(
                "This FileBackend doesn't support streaming"
            )
            # Note: this could fit a "always-redirect" remote file backend

    @abstractmethod
    def add(self, local_path: Path) -> None:
        """
        Add the contents of `local_path` to the underlying storage.

        Most callers should use `FileBackendInterface.add_file` instead,
        which also adds the corresponding `FileInStore` row.
        """

    @abstractmethod
    def same_contents(self, local_path: Path) -> bool:
        """
        Check whether this entry has the same contents as `local_path`.

        This is used by `FileBackendInterface.add_file` when adding a file
        that already exists in the database.  It returns True if the
        existing file's contents are the same as those of `local_path`,
        otherwise False.
        """

    @abstractmethod
    def remove(self) -> None:
        """
        Remove the entry from the underlying storage.

        Most callers should use `FileBackendInterface.remove_file` instead,
        which also removes the corresponding `FileInStore` row.
        """


class FileBackendInterface(Generic[FBC], metaclass=ABCMeta):
    """Interface to operate files in a storage."""

    db_store: FileStore

    # Class used as the in-memory representation of file backend
    # configuration.
    _configuration_type: type[FBC]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register configuration type for a subclass."""
        super().__init_subclass__(**kwargs)

        # The configuration type, computed by introspecting the type
        # argument used to specialize this generic class.
        [cls._configuration_type] = extract_generic_type_arguments(
            cls, FileBackendInterface
        )

    @cached_property
    def configuration(self) -> FBC:
        """Return the configuration for this file backend."""
        return self._configuration_type(**self.db_store.configuration)

    @abstractmethod
    def get_entry(self, fileobj: File) -> FileBackendEntryInterface[Any, Any]:
        """Return the entry in this backend corresponding to this file."""

    def get_local_path(self, fileobj: File) -> Path | None:
        """Return the path to a local copy of the file when possible or None."""
        return self.get_entry(fileobj).get_local_path()

    @contextlib.contextmanager
    def get_temporary_local_path(self, fileobj: File) -> Generator[Path]:
        """
        Yield the path to a possibly-temporary local copy of the file.

        If the backend already has a local copy, it can just yield it
        directly.  Otherwise, it downloads it to a temporary file, which it
        removes after this context manager exits.
        """
        with self.get_entry(fileobj).get_temporary_local_path() as temp_path:
            yield temp_path

    def get_url(
        self, fileobj: File, content_type: str | None = None
    ) -> str | None:
        """Return a URL pointing to the content when possible or None."""
        return self.get_entry(fileobj).get_url(content_type=content_type)

    @contextlib.contextmanager
    def get_stream(
        self, fileobj: File
    ) -> Generator[FileBackendStream, None, None]:
        """
        Return a file-like object that can be read.

        This returns a local copy of the file if available, but can also
        stream from a remote URL.
        """
        with self.get_entry(fileobj).get_stream() as stream:
            yield stream

    @transaction.atomic
    def add_file(self, local_path: Path, fileobj: File | None = None) -> File:
        """
        Copy local_path to underlying storage and register it in the FileStore.

        The optional ``fileobj`` provides the File used to identify the content
        available in local_path.

        The file may already exist in the store.  In that case the upload is
        for the purpose of proving that the user has access to the file
        contents and not merely its hash; this method checks whether the
        file contents are the same as those already in the store, and
        otherwise raises an exception rather than overwriting the existing
        file.
        """
        # fileobj.hash is not compared to avoid recalculating the hash
        # of the file
        if not fileobj:
            fileobj = File.from_local_path(local_path)
        elif fileobj.size != (size_in_disk := local_path.stat().st_size):
            raise ValueError(
                f"add_file file size mismatch. Path: {local_path} "
                f"Size in disk: {size_in_disk} "
                f"fileobj.size: {fileobj.size}"
            )

        file_in_store, created = FileInStore.objects.get_or_create(
            store=self.db_store, file=fileobj, data={}
        )
        entry = self.get_entry(fileobj)

        if created:
            entry.add(local_path)
        elif not entry.same_contents(local_path):
            raise ValueError(
                f"add_file contents mismatch when trying to add "
                f"{fileobj.hash_digest.hex()}"
            )

        return fileobj

    @transaction.atomic
    def remove_file(self, fileobj: File) -> None:
        """
        Remove the file from the FileStore.

        This removes the copy of the file identified by ``fileobj`` from the
        underlying storage and also the FileInStore entry indicating the
        availability of said file in the FileStore.
        """
        FileInStore.objects.filter(store=self.db_store, file=fileobj).delete()
        self.get_entry(fileobj).remove()

    # TODO: The mtime_filter interface is subject to change; we may need
    # this to look somewhat different for remote backends.
    @abstractmethod
    def list_entries(
        self, mtime_filter: Callable[[datetime], bool] | None = None
    ) -> Generator[FileBackendEntryInterface[Any, Any], None, None]:
        """
        Yield entries in this backend.

        :param mtime_filter: If given, only yield entries where this
          callable returns True when given their modification time.  The
          callable may assume that its argument is an aware datetime.
        """
