# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for MemoryFileBackend."""

from unittest import mock

from debusine.db.models import FileInStore, FileStore
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.file_backend.memory import (
    MemoryFileBackend,
    MemoryFileBackendEntry,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import data_generator


class MemoryFileBackendTests(TestCase):
    """Tests for MemoryFileBackend."""

    def setUp(self) -> None:
        """Initialize file backend to be tested."""
        super().setUp()
        self.store = FileStore.objects.create(
            name="memory",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "test_memory"},
        )
        self.backend = MemoryFileBackend(self.store)

    def tearDown(self) -> None:
        """Remove test memory storage."""
        MemoryFileBackend.storages.pop("test_memory", None)
        super().tearDown()

    def test_entry_str(self) -> None:
        """MemoryFileBackendEntry.__str__ returns the hex digest."""
        file = self.playground.create_file_in_backend(self.backend)
        entry = self.backend.get_entry(file)
        self.assertEqual(str(entry), file.sha256.hex())

    def test_get_local_path(self) -> None:
        """MemoryFileBackend.get_local_path returns None."""
        temp_file_path = self.create_temporary_file()
        file = self.backend.add_file(temp_file_path)
        local_path = self.backend.get_local_path(file)
        self.assertIsNone(local_path)

    def test_get_temporary_local_path(self) -> None:
        """MemoryFileBackend.get_temporary_local_path yields a local path."""
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = self.backend.add_file(temp_file_path)

        with self.backend.get_temporary_local_path(file) as temp_path:
            self.assertEqual(temp_path.read_bytes(), contents)

    def test_get_url(self) -> None:
        """MemoryFileBackend.get_url return None."""
        temp_file_path = self.create_temporary_file()
        file = self.backend.add_file(temp_file_path)
        url = self.backend.get_url(file)
        self.assertIsNone(url)

    def test_add_file(self) -> None:
        """MemoryFileBackend.add_file reuse the fileobj."""
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)

        file = self.backend.add_file(temp_file_path)

        self.assertEqual(file.size, len(contents))
        self.assertEqual(file.hash_digest, _calculate_hash_from_data(contents))
        self.assertTrue(
            FileInStore.objects.filter(
                store=self.backend.db_store, file=file
            ).exists()
        )
        self.assertEqual(
            self.backend.storages["test_memory"][file.hash_digest.hex()],
            contents,
        )

    def test_add_file_for_an_added_file(self) -> None:
        """add_file add a file that is already in the backend."""
        hexsum = (
            "c7be1ed902fb8dd4d48997c6452f5d7e509fbcdbe2808b16bcf4edce4c07d14e"
        )
        original_contents = b"This is a test"
        file_path = self.create_temporary_file(contents=original_contents)
        with mock.patch(
            "debusine.server.file_backend.memory.MemoryFileBackendEntry.add"
        ) as mock_add:
            self.backend.add_file(file_path)
        mock_add.assert_called_with(file_path)

        self.backend._store_file(hexsum, original_contents)
        with mock.patch(
            "debusine.server.file_backend.memory.MemoryFileBackendEntry.add"
        ) as mock_add:
            self.backend.add_file(file_path)
        mock_add.assert_not_called()

    def test_get_stream(self) -> None:
        """MemoryFileBackend.get_stream return a stream with file contents."""
        contents = b"this is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = self.backend.add_file(temp_file_path)

        with self.backend.get_stream(file) as f:
            self.assertEqual(f.read(), contents)

    def test_entry_remove(self) -> None:
        """MemoryFileBackendEntry.remove deletes the file from the backend."""
        temp_file_path = self.create_temporary_file()
        file = self.backend.add_file(temp_file_path)
        with self.backend.get_stream(file) as fd:
            fd.read()

        self.backend.get_entry(file).remove()

        with self.assertRaises(KeyError):
            with self.backend.get_stream(file) as fd:
                pass  # pragma: no cover

    def test_entry_removes_twice(self) -> None:
        """
        MemoryFileBackendEntry.remove tries to delete a file that did not exist.

        This could happen if remove() was called concurrently.
        """
        temp_file_path = self.create_temporary_file()
        file = self.backend.add_file(temp_file_path)
        entry = self.backend.get_entry(file)
        entry.remove()
        entry.remove()

    def test_list_entries(self) -> None:
        """MemoryFileBackend.list_entries returns all entries in the backend."""
        data_gen = data_generator(16)
        files = [
            self.playground.create_file_in_backend(
                self.backend, contents=next(data_gen)
            )
            for _ in range(10)
        ]
        self.assertCountEqual(
            self.backend.list_entries(),
            [
                MemoryFileBackendEntry(
                    backend=self.backend, identifier=file.sha256.hex()
                )
                for file in files
            ],
        )
