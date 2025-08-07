# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the models."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError

from debusine.db.models import File, FileInStore, FileStore
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.file_backend.external import ExternalDebianSuiteFileBackend
from debusine.server.file_backend.local import LocalFileBackend
from debusine.server.file_backend.memory import MemoryFileBackend
from debusine.test.django import TestCase


class FileManagerTests(TestCase):
    """Unit tests for the ``FileManager`` class."""

    def setUp(self) -> None:
        """Set up File to be used in the tests."""
        self.file_contents = b"test"
        self.file_hash = _calculate_hash_from_data(self.file_contents)

        self.file = self.playground.create_file(self.file_contents)

    def test_get_or_create_not_created(self) -> None:
        """
        File.objects.get_or_create returns created=False.

        File.objects.get_or_create() tried to create a file with the same
        file_hash and size as one already existing.
        """
        fileobj, created = File.objects.get_or_create(
            hash_digest=self.file_hash, size=len(self.file_contents)
        )

        self.assertEqual(self.file, fileobj)
        self.assertFalse(created)

    def test_get_or_create_created(self) -> None:
        """
        File.objects.get_or_create returns created=True.

        File.objects.get_or_create() created a new File (file_hash and size
        did not exist).
        """
        file_contents = self.file_contents + b"-new-contents"
        hash_digest = _calculate_hash_from_data(file_contents)
        size = len(file_contents)

        fileobj, created = File.objects.get_or_create(
            hash_digest=hash_digest, size=size
        )

        self.assertIsInstance(fileobj, File)
        self.assertEqual(fileobj.hash_digest, hash_digest)
        self.assertEqual(fileobj.size, size)
        self.assertTrue(created)


class FileTests(TestCase):
    """Tests for the File class."""

    def setUp(self) -> None:
        """Set up File to be used in the tests."""
        self.file_contents = b"test"
        self.file_hash = _calculate_hash_from_data(self.file_contents)

        self.file = self.playground.create_file(self.file_contents)

    def test_sha256(self) -> None:
        """File.sha256 have the expected hash."""
        self.assertEqual(self.file.sha256, self.file_hash)
        self.assertEqual(self.file.sha256.hex(), self.file_hash.hex())

    def test_hash_digest_getter(self) -> None:
        """File.hash_digest return the expected hash digest."""
        self.assertIsInstance(self.file.hash_digest, bytes)
        self.assertEqual(self.file.hash_digest, self.file_hash)
        self.assertEqual(self.file.hash_digest.hex(), self.file_hash.hex())

    def test_hash_digest_setter(self) -> None:
        """File.hash_digest setter sets the hash digest."""
        the_hash = _calculate_hash_from_data(b"some data")
        self.file.hash_digest = the_hash

        self.file.save()
        self.file.refresh_from_db()

        self.assertEqual(self.file.hash_digest, the_hash)

    def test_calculate_hash(self) -> None:
        """calculate_hash returns the expected hash digest."""
        file_contents = b"testing"
        local_file = self.create_temporary_file(contents=file_contents)
        expected_hash = _calculate_hash_from_data(file_contents)

        self.assertEqual(
            self.file.calculate_hash(
                local_file,
            ),
            expected_hash,
        )

    def test_str(self) -> None:
        """__str__() return sha256 and size."""
        self.assertEqual(
            str(self.file),
            f"id: {self.file.id} "
            f"sha256: {self.file_hash.hex()} "
            f"size: {len(self.file_contents)}",
        )

    def test_unique_constraint_hash_size(self) -> None:
        """File with same hash_digest and size cannot be created."""
        with self.assertRaisesRegex(
            IntegrityError, "db_file_unique_sha256_size"
        ):
            File.objects.create(
                **{
                    File.current_hash_algorithm: self.file.hash_digest,
                    "size": self.file.size,
                }
            )

    def test_hash_can_be_duplicated(self) -> None:
        """File with the same hash_digest and different size can be created."""
        file_existing = File.objects.earliest("id")
        file_new, _ = File.objects.get_or_create(
            hash_digest=file_existing.hash_digest, size=file_existing.size + 10
        )
        self.assertIsNotNone(file_new.id)
        self.assertNotEqual(file_new.id, file_existing.id)

    def test_constraint_hash_digest_not_empty(self) -> None:
        """File.hash_digest cannot be empty."""
        with self.assertRaisesRegex(IntegrityError, "db_file_sha256_not_empty"):
            File.objects.get_or_create(hash_digest=b"", size=5)


class FileStoreTests(TestCase):
    """Tests for the FileStore class."""

    def setUp(self) -> None:
        """Set up FileStore to be used in the tests."""
        self.backend = FileStore.BackendChoices.LOCAL
        self.file_store_name = "nas-01"

        self.file_store = FileStore.objects.create(
            name=self.file_store_name,
            backend=self.backend,
        )

    def test_default_values(self) -> None:
        """Test default values."""
        file_store = FileStore.objects.create(
            name="nas-02", backend=FileStore.BackendChoices.LOCAL
        )
        self.assertEqual(file_store.configuration, {})
        file_store.clean_fields()
        file_store.save()

    def test_backend_choice(self) -> None:
        """Assert FileStore.BackendChoices is correctly accessed."""
        self.assertEqual(self.file_store.backend, self.backend)

    def test_configuration_validation_error(self) -> None:
        """`ValidationError` is raised if configuration is invalid."""
        file_store = FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration=[],
        )
        with self.assertRaises(ValidationError) as raised:
            file_store.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"configuration": ["configuration must be a dictionary"]},
        )

        file_store.configuration = {"nonexistent": ""}
        with self.assertRaises(ValidationError) as raised:
            file_store.full_clean()
        messages = raised.exception.message_dict
        self.assertCountEqual(messages.keys(), ["configuration"])
        self.assertRegex(
            messages["configuration"][0],
            r"(?s)invalid file store configuration:.*"
            r"extra fields not permitted",
        )

    def test_files_through(self) -> None:
        """FileStore.files() return the expected file."""
        # Create a new file
        file = self.playground.create_file()

        # Add the file in the store
        FileInStore.objects.create(file=file, store=self.file_store, data={})

        # Assert that self.file_store through model return the expected file
        self.assertEqual(self.file_store.files.first(), file)

    def test_total_size(self) -> None:
        """Adding/removing files to/from the store maintains `total_size`."""
        files = [
            self.playground.create_file(contents=b"\0" * length)
            for length in (0, 4, 10, 1024, 512)
        ]

        expected_total_size = 0
        self.file_store.refresh_from_db()
        self.assertEqual(self.file_store.total_size, expected_total_size)
        for file in files:
            FileInStore.objects.create(file=file, store=self.file_store)
            expected_total_size += file.size
            self.file_store.refresh_from_db()
            self.assertEqual(self.file_store.total_size, expected_total_size)
        for file in files:
            FileInStore.objects.filter(
                file=file, store=self.file_store
            ).delete()
            expected_total_size -= file.size
            self.file_store.refresh_from_db()
            self.assertEqual(self.file_store.total_size, expected_total_size)

    def test_max_size_constraint(self) -> None:
        """If both are set, `soft_max_size` must be <= `max_size`."""
        for soft_max_size, max_size, ok in (
            (None, None, True),
            (1024, None, True),
            (None, 1024, True),
            (1024, 1024, True),
            (1024, 2048, True),
            (2048, 1024, False),
        ):
            with transaction.atomic():
                self.file_store.soft_max_size = soft_max_size
                self.file_store.max_size = max_size
                if ok:
                    self.file_store.save()
                else:
                    with self.assertRaisesRegex(
                        IntegrityError, "db_filestore_consistent_max_sizes"
                    ):
                        self.file_store.save()

    def test_get_backend_object(self) -> None:
        """get_backend_object instantiates the right backend."""
        fs_local = FileStore(
            name="local",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": "/tmp"},
        )
        fs_memory = FileStore(
            name="memory",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "test"},
        )
        fs_external = FileStore(
            name="external",
            backend=FileStore.BackendChoices.EXTERNAL_DEBIAN_SUITE,
            configuration={
                "archive_root_url": "https://deb.debian.org/debian",
                "suite": "bookworm",
                "components": ["main"],
            },
        )

        self.assertIsInstance(fs_local.get_backend_object(), LocalFileBackend)
        self.assertIsInstance(fs_memory.get_backend_object(), MemoryFileBackend)
        self.assertIsInstance(
            fs_external.get_backend_object(), ExternalDebianSuiteFileBackend
        )

    def test_default(self) -> None:
        """default() returns FileStore with name=="Default"."""
        self.assertEqual(FileStore.default().name, "Default")

    def test_str(self) -> None:
        """__str__() return the correct information."""
        self.assertEqual(
            self.file_store.__str__(),
            f"Id: {self.file_store.id} "
            f"Name: {self.file_store.name} "
            f"Backend: {self.file_store.backend}",
        )


class FileInStoreTests(TestCase):
    """Tests for the FileInStore class."""

    def setUp(self) -> None:
        """Set up FileInStore to be used in the tests."""
        self.file_store_name = "nas-01"
        self.file_store = FileStore.objects.create(
            name="nas-01",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={},
        )

        file_contents = b"test"

        self.file = self.playground.create_file(file_contents)
        self.file_in_store = FileInStore(
            store=self.file_store,
            file=self.file,
        )

    def test_default_values(self) -> None:
        """Test default values."""
        # Delete all FileInStore to create a new one, in this test,
        # reusing self.file_store and self.file
        FileInStore.objects.all().delete()
        file_in_store = FileInStore(store=self.file_store, file=self.file)

        self.assertEqual(file_in_store.data, {})
        file_in_store.clean_fields()
        file_in_store.save()

    def test_str(self) -> None:
        """__str__() return the correct string."""
        self.assertEqual(
            self.file_in_store.__str__(),
            f"Id: {self.file_in_store.id} "
            f"Store: {self.file_in_store.store.name} "
            f"File: {self.file_in_store.file.hash_digest.hex()}",
        )
