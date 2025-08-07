# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the file backend interface."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from unittest import mock

from debusine.db.models import File, FileInStore, FileStore
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.file_backend.interface import (
    FileBackendEntryInterface,
    FileBackendInterface,
)
from debusine.server.file_backend.models import FileBackendConfiguration
from debusine.test.django import TestCase
from debusine.utils import NotSupportedError


class FakeFileBackendEntry(FileBackendEntryInterface["FakeFileBackend", bytes]):
    """An entry in a fake file backend."""

    def __str__(self) -> str:
        """Return a string describing this entry."""
        raise NotImplementedError()

    def get_local_path(self) -> None:
        """Return local path of entry."""
        return None

    @contextmanager
    def get_temporary_local_path(self) -> Generator[Path]:
        """Yield the path to a possibly-temporary local copy of the file."""
        raise NotImplementedError()

    def get_url(self, content_type: str | None = None) -> None:  # noqa: U100
        """Return URL of entry."""
        return None

    def add(self, local_path: Path) -> None:
        """Record the addition."""
        self.backend.add_file_calls.append(local_path.read_bytes())

    def same_contents(self, local_path: Path) -> bool:  # noqa: U100
        """Check whether this entry has the same contents as `local_path`."""
        return True

    def remove(self) -> None:
        # Ensure that the file is NOT in the DB before remove_file is called
        # The FileBackendInterface.remove_file should have removed it from the
        # DB before deleting the file from disk
        fileobj = File.objects.get(sha256=self._identifier)
        if FileInStore.objects.filter(
            store=self.backend.db_store, file=fileobj
        ).exists():
            raise RuntimeError(
                "Attempted to remove file from disk but still is referenced "
                "by the FileInStore model"
            )  # pragma: no cover

        self.backend.remove_file_calls.append(fileobj)


class FakeFileBackend(FileBackendInterface[FileBackendConfiguration]):
    """Fake file backend to test methods in FileBackendInterface."""

    def __init__(self) -> None:
        """Initialize object."""
        super().__init__()

        self.db_store = FileStore.objects.create(
            name="Fake", backend="Fake", configuration={}
        )
        self.add_file_calls: list[bytes] = []
        self.remove_file_calls: list[File] = []

    def get_entry(self, fileobj: File) -> FakeFileBackendEntry:
        """Return the entry in this backend corresponding to this file."""
        return FakeFileBackendEntry(backend=self, identifier=fileobj.sha256)

    def list_entries(
        self,
        mtime_filter: Callable[[datetime], bool] | None = None,  # noqa: U100
    ) -> Generator[FakeFileBackendEntry, None, None]:
        """Yield entries in this backend."""
        raise NotImplementedError()


class FileBackendInterfaceTests(TestCase):
    """Tests for code of FileBackendInterface."""

    file_backend: ClassVar[FakeFileBackend]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize file backend to be tested."""
        super().setUpTestData()
        cls.file_backend = FakeFileBackend()

    def test_eq(self) -> None:
        """Entries are equal if their file stores and identifiers are equal."""
        other_file_store = FileStore.objects.create(
            name="memory",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "memory"},
        )
        other_file_backend = other_file_store.get_backend_object()
        fileobjs = [self.playground.create_file() for _ in range(2)]

        self.assertEqual(
            self.file_backend.get_entry(fileobjs[0]),
            self.file_backend.get_entry(fileobjs[0]),
        )
        self.assertNotEqual(
            self.file_backend.get_entry(fileobjs[0]),
            self.file_backend.get_entry(fileobjs[1]),
        )
        self.assertNotEqual(
            self.file_backend.get_entry(fileobjs[0]),
            other_file_backend.get_entry(fileobjs[0]),
        )
        self.assertNotEqual(self.file_backend.get_entry(fileobjs[0]), object())

    def test_hash(self) -> None:
        """An entry is hashed using its file store and its identifier."""
        fileobj = self.playground.create_file()
        entry = self.file_backend.get_entry(fileobj)

        self.assertEqual(
            hash(entry), hash((self.file_backend.db_store, entry._identifier))
        )

    def test_remove_file_not_in_file_backend(self) -> None:
        """
        remove_file() call _remove_file.

        Assert that when the file in disk is deleted from the backend
        the fileobj model is not deleted yet. Debusine can have
        orphaned files in disk (not referenced by the DB) but must not have
        files in DB that don't exist in disk.
        """
        fileobj = self.playground.create_file()

        self.file_backend.remove_file(fileobj)

        self.assertEqual(self.file_backend.remove_file_calls, [fileobj])

    def test_remove_file_cannot_remove(self) -> None:
        """remove_file() fails to remove from storage: File is left intact."""
        fileobj = self.playground.create_file_in_backend(self.file_backend)

        with (
            self.assertRaises(RuntimeError),
            mock.patch.object(
                FakeFileBackendEntry, "remove", side_effect=RuntimeError
            ),
        ):
            self.file_backend.remove_file(fileobj)

        self.assertTrue(
            FileInStore.objects.filter(
                store=self.file_backend.db_store, file=fileobj
            ).exists()
        )

    def test_add_file_from_local_path_reuse_fileobj(self) -> None:
        """add_file() can reuse the fileobj."""
        temp_file_path = self.create_temporary_file(contents=b"This is a test")

        fileobj = File.from_local_path(temp_file_path)

        fileobj_before_id = fileobj.id

        added_fileobj = self.file_backend.add_file(
            temp_file_path, fileobj=fileobj
        )

        self.assertEqual(added_fileobj.id, fileobj_before_id)
        self.assertTrue(
            FileInStore.objects.filter(
                store=self.file_backend.db_store, file=added_fileobj
            ).exists()
        )

    def test_add_file_from_local_path_fileobj_size_do_not_match(self) -> None:
        """
        add_file() with fileobj not reused: size mismatch.

        Exception is raised, file is not added in the backend.
        """
        original_contents = b"This is a test"
        new_contents = b"Different content length"

        temp_file_path = self.create_temporary_file(contents=original_contents)
        fileobj = File.from_local_path(temp_file_path)

        self.assertEqual(FileInStore.objects.count(), 0)

        temp_file_path.write_bytes(new_contents)

        expected_message = (
            fr"^add_file file size mismatch\. Path: "
            fr"{temp_file_path} Size in disk: {len(new_contents)} "
            fr"fileobj\.size: {len(original_contents)}$"
        )

        with self.assertRaisesRegex(ValueError, expected_message):
            self.file_backend.add_file(temp_file_path, fileobj=fileobj)

        # No file was added in the File table
        self.assertEqual(File.objects.count(), 1)

        # No file was added in the FileInStore (wouldn't be possible
        # since no file was added anyway)
        self.assertEqual(FileInStore.objects.count(), 0)

    def test_add_file_from_local_path_fileobj(self) -> None:
        """add_file() copies and returns the file to the backend."""
        contents = b"this is a test2"
        temp_file_path = self.create_temporary_file(contents=contents)

        file = self.file_backend.add_file(temp_file_path)

        self.assertEqual(file.size, len(contents))
        self.assertEqual(file.hash_digest, _calculate_hash_from_data(contents))
        self.assertEqual(self.file_backend.add_file_calls, [contents])
        self.assertTrue(
            FileInStore.objects.filter(
                store=self.file_backend.db_store, file=file
            ).exists()
        )

    def test_add_file_from_local_path_file_cannot_be_copied(self) -> None:
        """add_file() fails to add to storage: no File is created."""
        temp_file_path = self.create_temporary_file(contents=b"test")

        # Adding the file to the backend storage could fail for all sorts of
        # reasons.  FileNotFoundError is an example.
        with (
            self.assertRaises(FileNotFoundError),
            mock.patch.object(
                FakeFileBackendEntry, "add", side_effect=FileNotFoundError
            ),
        ):
            self.file_backend.add_file(temp_file_path)

        # The File in db was not created
        self.assertFalse(File.objects.all().exists())

    def test_add_file_for_an_added_file(self) -> None:
        """add_file() adds a file that is already in the backend."""
        temp_file_path = self.create_temporary_file(contents=b"test")
        fileobj = self.file_backend.add_file(temp_file_path)

        self.assertEqual(self.file_backend.add_file_calls, [b"test"])

        self.assertEqual(
            self.file_backend.add_file(temp_file_path, fileobj), fileobj
        )
        # FileBackendEntryInterface.add is not called a second time.
        self.assertEqual(self.file_backend.add_file_calls, [b"test"])

    def test_add_file_contents_mismatch(self) -> None:
        """File exists in the backend with same hash but different contents."""
        original_contents = b"This is a test"
        new_contents = b"Hash collision"  # same length as original_contents
        temp_file_path = self.create_temporary_file(contents=original_contents)
        fileobj = File.from_local_path(temp_file_path)
        self.file_backend.add_file(temp_file_path)

        self.assertEqual(File.objects.count(), 1)
        self.assertEqual(FileInStore.objects.count(), 1)

        temp_file_path.write_bytes(new_contents)

        expected_message = (
            f"add_file contents mismatch when trying to add "
            f"{fileobj.hash_digest.hex()}"
        )

        with (
            self.assertRaisesRegex(ValueError, expected_message),
            mock.patch.object(
                FakeFileBackendEntry, "same_contents", return_value=False
            ),
        ):
            self.file_backend.add_file(temp_file_path, fileobj=fileobj)

        self.assertEqual(File.objects.count(), 1)
        self.assertEqual(FileInStore.objects.count(), 1)

    def test_remove_file_in_file_backend(self) -> None:
        """remove_file() call _remove_file, delete file from FileInStore()."""
        fileobj = self.playground.create_file_in_backend(self.file_backend)

        self.assertTrue(
            FileInStore.objects.filter(
                file=fileobj, store=self.file_backend.db_store
            ).exists()
        )

        self.file_backend.remove_file(fileobj)

        self.assertFalse(
            FileInStore.objects.filter(
                file=fileobj, store=self.file_backend.db_store
            ).exists()
        )

    def test_get_stream_non_local(self) -> None:
        """get_stream() without local URL: RuntimeError."""
        file = File()

        with self.assertRaisesRegex(
            NotSupportedError,
            "^This FileBackend doesn't support streaming$",
        ):
            self.file_backend.get_stream(file).__enter__()
