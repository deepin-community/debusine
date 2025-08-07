# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for LocalFileBackend."""

import logging
from pathlib import Path, PurePath
from typing import ClassVar
from unittest import mock

from django.conf import settings

from debusine.db.models import (
    Collection,
    File,
    FileInStore,
    FileStore,
    Workspace,
)
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.file_backend.local import (
    LocalFileBackend,
    LocalFileBackendEntry,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import data_generator


class LocalFileBackendTests(TestCase):
    """Tests for LocalFileBackend."""

    default_file_store: ClassVar[FileStore]
    file_backend: ClassVar[LocalFileBackend]

    playground_memory_file_store = False

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize file backend to be tested."""
        super().setUpTestData()
        cls.default_file_store = FileStore.default()
        cls.file_backend = LocalFileBackend(cls.default_file_store)

    def test_init_set_base_directory_default(self) -> None:
        """Test LocalFileBackend use setting's directory for Default store."""
        Collection.objects.filter(name__in=("_", "default")).delete()
        Workspace.objects.all().delete()
        self.default_file_store.scopes.clear()
        self.default_file_store.delete()

        file_store = FileStore.objects.create(
            name=self.default_file_store.name, configuration={}
        )
        local_file_backend = LocalFileBackend(file_store)

        self.assertEqual(
            local_file_backend._base_directory,
            Path(settings.DEBUSINE_STORE_DIRECTORY),
        )

    def test_init_set_base_directory_from_config(self) -> None:
        """Test LocalFileBackend use the store configuration base_directory."""
        directory = "/tmp"
        file_store = FileStore.objects.create(
            name="tmp", configuration={"base_directory": directory}
        )
        local_file_backend = LocalFileBackend(file_store)

        self.assertEqual(local_file_backend._base_directory, Path(directory))

    def test_init_set_base_directory_raise_runtime_error(self) -> None:
        """
        Test LocalFileBackend raise RuntimeError: no base_directory setting.

        Only the store named "Default" use the
        settings.DEBUSINE_STORE_DIRECTORY by default.
        """
        store_name = "temporary"
        file_store = FileStore.objects.create(name=store_name, configuration={})

        with self.assertRaisesRegex(
            RuntimeError,
            f'LocalFileBackend {store_name} configuration requires '
            '"base_directory" setting',
        ):
            LocalFileBackend(file_store)

    def test_init_base_directory(self) -> None:
        """
        Test LocalFileBackend use base_directory from LocalStore configuration.

        Add a file into the backend and check that the correct path was used.
        """
        local_store_directory = self.create_temporary_directory()

        local_store = FileStore.default()
        local_store.configuration["base_directory"] = (
            local_store_directory.as_posix()
        )
        local_store.save()

        file_backend = LocalFileBackend(local_store)

        fileobj = self.playground.create_file_in_backend(file_backend)
        # No need to schedule deletion of the file because
        # the local_store_directory is cleaned up

        fileobj_path = file_backend.get_local_path(fileobj)
        assert fileobj_path is not None

        self.assertTrue(
            str(fileobj_path.parent).startswith(
                local_store_directory.as_posix()
            )
        )

    def test_entry_str(self) -> None:
        """LocalFileBackendEntry.__str__ returns the local path."""
        file = self.playground.create_file_in_backend(self.file_backend)
        hash_hex = file.sha256.hex()
        entry = self.file_backend.get_entry(file)
        self.assertEqual(
            str(entry),
            str(
                Path(self.default_file_store.configuration["base_directory"])
                / hash_hex[0:2]
                / hash_hex[2:4]
                / hash_hex[4:6]
                / f"{hash_hex}-{file.size}"
            ),
        )

    def test_get_local_path_for_added_file(self) -> None:
        """LocalFileBackend.from_local_path return the already existing file."""
        temp_file_path = self.create_temporary_file()

        fileobj = File.from_local_path(temp_file_path)

        self.assertEqual(File.from_local_path(temp_file_path), fileobj)

    def test_get_local_path(self) -> None:
        """LocalFileBackend.get_local_path for a file return the file path."""
        temp_file_path = self.create_temporary_file()

        hash_hex = File.calculate_hash(temp_file_path).hex()

        file = File.from_local_path(temp_file_path)

        self.assertEqual(
            self.file_backend.get_local_path(file),
            Path(self.default_file_store.configuration["base_directory"])
            / hash_hex[0:2]
            / hash_hex[2:4]
            / hash_hex[4:6]
            / f"{hash_hex}-{file.size}",
        )

    def test_get_temporary_local_path(self) -> None:
        """LocalFileBackend.get_temporary_local_path yields the file path."""
        temp_file_path = self.create_temporary_file()
        hash_hex = File.calculate_hash(temp_file_path).hex()
        file = File.from_local_path(temp_file_path)

        with self.file_backend.get_temporary_local_path(file) as temp_path:
            self.assertEqual(
                temp_path,
                Path(self.default_file_store.configuration["base_directory"])
                / hash_hex[0:2]
                / hash_hex[2:4]
                / hash_hex[4:6]
                / f"{hash_hex}-{file.size}",
            )

    def test_get_url(self) -> None:
        """LocalFileBackend.get_url return None."""
        fileobj = self.playground.create_file_in_backend(self.file_backend)

        url = self.file_backend.get_url(fileobj)
        self.assertIsNone(url)

    def test_add_file(self) -> None:
        """LocalFileBackend.add_file copy and return the file to the backend."""
        mocked_fsync = self.patch_os_fsync()
        contents = b"this is a test2"
        temp_file_path = self.create_temporary_file(contents=contents)

        # Create a file where the final file will be
        # This situation can happen if debusine copies a file into the
        # backend but the database is not updated (e.g. server crash)
        # Debusine, at a later point, might copy a file into the backend.
        # Let's test that the file is overwritten correctly
        fileobj_temp = File()
        fileobj_temp.hash_digest = _calculate_hash_from_data(contents)
        fileobj_temp.size = len(contents)
        fileobj_temp_path = self.file_backend.get_entry(
            fileobj_temp
        ).get_local_path()
        fileobj_temp_path.parent.mkdir(parents=True, exist_ok=True)

        # Writes the contents twice. To test that debusine will overwrite
        # the file
        fileobj_temp_path.write_bytes(contents * 2)
        self.addCleanup(fileobj_temp_path.unlink)

        # Add file in the backend
        file = self.file_backend.add_file(temp_file_path)

        # The fileobj returned has the expected size...
        self.assertEqual(file.size, len(contents))

        # ...and expected hash...
        self.assertEqual(file.hash_digest, _calculate_hash_from_data(contents))

        # The file has been properly copied
        file_in_file_backend_hash = File.calculate_hash(
            self.file_backend.get_entry(file).get_local_path()
        )

        self.assertEqual(file.hash_digest, file_in_file_backend_hash)
        self.assertTrue(
            FileInStore.objects.filter(
                store=self.file_backend.db_store, file=file
            ).exists()
        )
        mocked_fsync.assert_called()

    def test_add_file_already_in_backend(self) -> None:
        """
        LocalFileBackend.add_file add a file that is already in the backend.

        Assert that shutil.copy is not called.
        """
        fileobj = self.playground.create_file_in_backend(self.file_backend)

        # In this test we pretend to add the file from the backend into the
        # backend. Usually it would be from outside the backend directory.
        file_path = self.file_backend.get_entry(fileobj).get_local_path()

        with mock.patch("shutil.copy", autospec=True):
            self.assertEqual(
                self.file_backend.add_file(file_path, fileobj), fileobj
            )

    def test_directory_not_created_if_base_directory_do_not_exist(self) -> None:
        """
        A FileStore points to a base directory that does not exist.

        LocalFileBackend.add_file() does not create a directory: it raise
        FileNotFoundError: the base_directory must exist.
        """
        does_not_exist = self.create_temporary_directory()
        does_not_exist.rmdir()

        file_store = FileStore.objects.create(
            name="Testing",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": str(does_not_exist)},
        )
        local_file_backend = LocalFileBackend(file_store)

        file_to_add = self.create_temporary_file()

        with self.assertRaises(FileNotFoundError):
            local_file_backend.add_file(file_to_add)

        # The directory must not exist, it is not created
        self.assertFalse(does_not_exist.is_dir())

    def test_base_directory(self) -> None:
        """LocalFileBackend.base_directory() return the _base_directory."""
        self.assertEqual(
            self.file_backend.base_directory(),
            self.file_backend._base_directory,
        )

    def test_get_stream(self) -> None:
        """LocalFileBackend.get_stream return a stream with file contents."""
        contents = b"this is a test"
        file = self.playground.create_file_in_backend(
            self.file_backend, contents
        )

        with self.file_backend.get_stream(file) as f:
            self.assertEqual(f.read(), contents)

    def test_entry_remove(self) -> None:
        """LocalFileBackendEntry.remove deletes the file from the backend."""
        file = self.playground.create_file_in_backend(self.file_backend)
        entry = self.file_backend.get_entry(file)

        self.assertTrue(entry.get_local_path().exists())

        entry.remove()

        self.assertFalse(entry.get_local_path().exists())

    def test_entry_remove_did_not_exist(self) -> None:
        """
        LocalFileBackendEntry.remove tries to delete a file that did not exist.

        This could happen if remove() was called concurrently.
        """
        file = self.playground.create_file_in_backend(self.file_backend)
        entry = self.file_backend.get_entry(file)
        entry.get_local_path().unlink()

        entry.remove()
        self.assertTrue(True)  # No exception was raised

    def patch_os_fsync(self) -> mock.MagicMock:
        """Return mocked os.fsync."""
        patcher = mock.patch("os.fsync", autospec=True)
        mocked_fsync = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked_fsync

    def test_entry_sync_file(self) -> None:
        """LocalFileBackendEntry._sync_file sync the file and its directory."""
        mocked_fsync = self.patch_os_fsync()

        entry = self.file_backend.get_entry(self.playground.create_file())
        temporary_file = self.create_temporary_file()

        entry._sync_file(temporary_file)

        # Called twice: one to os.fsync the file and again for the directory
        self.assertEqual(mocked_fsync.call_count, 2)

    def test_entry_sync_file_does_not_exist(self) -> None:
        """LocalFileBackend._sync_file log warning: file does not exist."""
        entry = self.file_backend.get_entry(self.playground.create_file())
        temporary_file = self.create_temporary_file()
        temporary_file.unlink()

        expected_message = f"Could not open {temporary_file} for flushing"

        with self.assertLogsContains(
            expected_message, logger="debusine", level=logging.DEBUG
        ):
            entry._sync_file(temporary_file)

    def test_list_entries(self) -> None:
        """LocalFileBackend.list_entries returns all entries in the backend."""
        data_gen = data_generator(16)
        files = [
            self.playground.create_file_in_backend(
                self.file_backend, contents=next(data_gen)
            )
            for _ in range(10)
        ]
        expected_entries: list[LocalFileBackendEntry] = []
        for file in files:
            hash_hex = file.sha256.hex()
            expected_entries.append(
                LocalFileBackendEntry(
                    backend=self.file_backend,
                    identifier=PurePath(hash_hex[0:2])
                    / hash_hex[2:4]
                    / hash_hex[4:6]
                    / f"{hash_hex}-{file.size}",
                )
            )
        self.assertCountEqual(
            self.file_backend.list_entries(), expected_entries
        )
