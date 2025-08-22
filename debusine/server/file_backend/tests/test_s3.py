# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for S3FileBackend."""

from datetime import timedelta
from io import BytesIO
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

from botocore import stub
from django.utils import timezone

from debusine.assets.models import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
)
from debusine.db.context import context
from debusine.db.models import Asset, File, FileInStore, FileStore
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.file_backend.s3 import S3FileBackend, S3FileBackendEntry
from debusine.test.django import TestCase
from debusine.test.test_utils import data_generator


class S3FileBackendTests(TestCase):
    """Tests for S3FileBackend."""

    provider_account: ClassVar[Asset]
    file_store: ClassVar[FileStore]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize file backend to be tested."""
        super().setUpTestData()
        cls.provider_account = (
            cls.playground.create_cloud_provider_account_asset(
                data=AWSProviderAccountData(
                    name="test",
                    configuration=AWSProviderAccountConfiguration(
                        region_name="test-region"
                    ),
                    credentials=AWSProviderAccountCredentials(
                        access_key_id="access-key",
                        secret_access_key="secret-key",
                    ),
                )
            )
        )
        cls.file_store = FileStore.objects.create(
            name="s3",
            backend=FileStore.BackendChoices.S3,
            configuration={"bucket_name": "test-bucket"},
            provider_account=cls.provider_account,
        )

    def test_no_provider_account(self) -> None:
        """`S3FileBackend` requires a cloud provider account."""
        self.file_store.provider_account = None

        with self.assertRaisesRegex(
            RuntimeError, r"^S3 file backend requires a cloud provider account$"
        ):
            self.file_store.get_backend_object()

    def test_wrong_provider_account(self) -> None:
        """`S3FileBackend` requires an AWS cloud provider account."""
        assert self.file_store.provider_account is not None
        with context.disable_permission_checks():
            self.file_store.provider_account.data = {"provider_type": "hetzner"}
            self.file_store.provider_account.save()

        with self.assertRaisesRegex(
            RuntimeError,
            r"^S3 file backend requires an AWS cloud provider account$",
        ):
            self.file_store.get_backend_object()

    def test_attributes(self) -> None:
        """Initializing `S3FileBackend` sets suitable attributes."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        self.assertEqual(backend.db_store, self.file_store)
        self.assertEqual(backend.client.__class__.__name__, "S3")
        provider_account_data = self.provider_account.data_model
        assert isinstance(provider_account_data, AWSProviderAccountData)
        self.assertEqual(
            backend.client.meta.region_name,
            provider_account_data.configuration.region_name,
        )
        # Private attribute not exposed by type annotations.
        signer = getattr(backend.client, "_request_signer")
        self.assertEqual(
            signer._credentials.access_key,
            provider_account_data.credentials.access_key_id,
        )
        self.assertEqual(
            signer._credentials.secret_key,
            provider_account_data.credentials.secret_access_key,
        )

    def test_endpoint_url(self) -> None:
        """The provider account can set an explicit endpoint URL."""
        assert self.file_store.provider_account is not None
        with context.disable_permission_checks():
            provider_account_data = self.file_store.provider_account.data_model
            assert isinstance(provider_account_data, AWSProviderAccountData)
            provider_account_data.configuration.s3_endpoint_url = (
                "https://s3.eu-west-2.amazonaws.com/"
            )
            self.file_store.provider_account.data = provider_account_data.dict(
                exclude_unset=True
            )
            self.file_store.provider_account.save()

        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        self.assertEqual(
            backend.client.meta.endpoint_url,
            provider_account_data.configuration.s3_endpoint_url,
        )

    def test_entry_str(self) -> None:
        """`S3FileBackendEntry.__str__` returns the identifier."""
        backend = self.file_store.get_backend_object()
        file = self.playground.create_file()
        entry = backend.get_entry(file)
        self.assertEqual(str(entry), f"{file.sha256.hex()}-{file.size}")

    def test_get_local_path(self) -> None:
        """`S3FileBackend.get_local_path` returns None."""
        backend = self.file_store.get_backend_object()
        file = self.playground.create_file()
        FileInStore.objects.create(store=self.file_store, file=file)
        self.assertIsNone(backend.get_local_path(file))

    def test_get_temporary_local_path(self) -> None:
        """`S3FileBackendEntry.get_temporary_local_path` yields a local path."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        identifier = f"{file.sha256.hex()}-{len(contents)}"
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "head_object",
            {"ContentLength": len(contents)},
            {"Bucket": "test-bucket", "Key": identifier},
        )
        stubber.add_response(
            "get_object",
            {"Body": BytesIO(contents)},
            {"Bucket": "test-bucket", "Key": identifier},
        )

        with stubber, backend.get_temporary_local_path(file) as temp_path:
            self.assertEqual(temp_path.read_bytes(), contents)

        stubber.assert_no_pending_responses()

    def test_get_url(self) -> None:
        """`S3FileBackend.get_url` returns a presigned URL."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        file = self.playground.create_file()
        FileInStore.objects.create(store=self.file_store, file=file)

        url = backend.get_url(file)
        assert url is not None
        split_url = urlsplit(url)

        self.assertEqual(split_url.scheme, "https")
        self.assertEqual(split_url.netloc, "test-bucket.s3.amazonaws.com")
        self.assertEqual(split_url.path, f"/{file.sha256.hex()}-{file.size}")
        query = parse_qs(split_url.query)
        self.assertRegex(
            query["X-Amz-Credential"][-1], r"^access-key/.*/test-region/.*"
        )
        self.assertEqual(query["X-Amz-Expires"], ["60"])
        self.assertIn("X-Amz-Signature", query)
        self.assertNotIn("response-content-type", query)

    def test_get_url_content_type(self) -> None:
        """`S3FileBackend.get_url` can override the Content-Type."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        file = self.playground.create_file()
        FileInStore.objects.create(store=self.file_store, file=file)

        url = backend.get_url(file, content_type="text/plain")
        assert url is not None
        split_url = urlsplit(url)

        self.assertEqual(split_url.scheme, "https")
        self.assertEqual(split_url.netloc, "test-bucket.s3.amazonaws.com")
        self.assertEqual(split_url.path, f"/{file.sha256.hex()}-{file.size}")
        query = parse_qs(split_url.query)
        self.assertRegex(
            query["X-Amz-Credential"][-1], r"^access-key/.*/test-region/.*"
        )
        self.assertEqual(query["X-Amz-Expires"], ["60"])
        self.assertIn("X-Amz-Signature", query)
        self.assertEqual(query["response-content-type"], ["text/plain"])

    def test_entry_add(self) -> None:
        """`S3FileBackendEntry.add` uploads the file."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        entry = backend.get_entry(file)
        identifier = (
            f"{_calculate_hash_from_data(contents).hex()}-{len(contents)}"
        )
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "test-bucket",
                "Key": identifier,
                "Body": stub.ANY,
                "ChecksumAlgorithm": "SHA256",
            },
        )

        with stubber:
            entry.add(temp_file_path)

        stubber.assert_no_pending_responses()

    def test_entry_add_storage_class(self) -> None:
        """`S3FileBackendEntry.add` can specify a different storage class."""
        self.file_store.configuration["storage_class"] = "EXPRESS_ONEZONE"
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        entry = backend.get_entry(file)
        identifier = (
            f"{_calculate_hash_from_data(contents).hex()}-{len(contents)}"
        )
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "test-bucket",
                "Key": identifier,
                "Body": stub.ANY,
                "ChecksumAlgorithm": "SHA256",
                "StorageClass": "EXPRESS_ONEZONE",
            },
        )

        with stubber:
            entry.add(temp_file_path)

        stubber.assert_no_pending_responses()

    def test_entry_add_hetzner(self) -> None:
        """Skip ChecksumAlgorithm when uploading to Hetzner."""
        assert self.file_store.provider_account is not None
        with context.disable_permission_checks():
            provider_account_data = self.file_store.provider_account.data_model
            assert isinstance(provider_account_data, AWSProviderAccountData)
            provider_account_data.configuration.region_name = None
            provider_account_data.configuration.s3_endpoint_url = (
                "https://hel1.your-objectstorage.com/"
            )
            self.file_store.provider_account.data = provider_account_data.dict(
                exclude_unset=True
            )
            self.file_store.provider_account.save()

        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        entry = backend.get_entry(file)
        identifier = (
            f"{_calculate_hash_from_data(contents).hex()}-{len(contents)}"
        )
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "put_object",
            {},
            {"Bucket": "test-bucket", "Key": identifier, "Body": stub.ANY},
        )

        with stubber:
            entry.add(temp_file_path)

        stubber.assert_no_pending_responses()

    def test_entry_same_contents_match(self) -> None:
        """`S3FileBackendEntry.same_contents` accepts matching contents."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        entry = backend.get_entry(file)
        identifier = f"{file.sha256.hex()}-{len(contents)}"
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "head_object",
            {"ContentLength": len(contents)},
            {"Bucket": "test-bucket", "Key": identifier},
        )
        stubber.add_response(
            "get_object",
            {"Body": BytesIO(contents)},
            {"Bucket": "test-bucket", "Key": identifier},
        )

        with stubber:
            self.assertTrue(entry.same_contents(temp_file_path))

        stubber.assert_no_pending_responses()

    def test_entry_same_contents_mismatch(self) -> None:
        """`S3FileBackendEntry.same_contents` rejects mismatching contents."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        entry = backend.get_entry(file)
        identifier = f"{file.sha256.hex()}-{len(contents)}"
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "head_object",
            {"ContentLength": len(contents)},
            {"Bucket": "test-bucket", "Key": identifier},
        )
        stubber.add_response(
            "get_object",
            {"Body": BytesIO(contents.lower())},
            {"Bucket": "test-bucket", "Key": identifier},
        )

        with stubber:
            self.assertFalse(entry.same_contents(temp_file_path))

        stubber.assert_no_pending_responses()

    def test_entry_remove(self) -> None:
        """`S3FileBackendEntry.remove` deletes the file."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        contents = b"This is a test"
        temp_file_path = self.create_temporary_file(contents=contents)
        file = File.from_local_path(temp_file_path)
        entry = backend.get_entry(file)
        identifier = f"{file.sha256.hex()}-{len(contents)}"
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "delete_object", {}, {"Bucket": "test-bucket", "Key": identifier}
        )

        with stubber:
            entry.remove()

        stubber.assert_no_pending_responses()

    def test_list_entries(self) -> None:
        """`S3FileBackend.list_entries` returns all entries in the backend."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        data_gen = data_generator(16)
        files = [
            self.playground.create_file(contents=next(data_gen))
            for _ in range(10)
        ]
        expected_entries: list[S3FileBackendEntry] = []
        for file in files:
            hash_hex = file.sha256.hex()
            expected_entries.append(
                S3FileBackendEntry(
                    backend=backend, identifier=f"{hash_hex}-{file.size}"
                )
            )
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "list_objects_v2",
            {
                "Contents": [
                    {"Key": f"{file.sha256.hex()}-{file.size}"}
                    for file in files
                ]
            },
            {"Bucket": "test-bucket"},
        )

        with stubber:
            self.assertCountEqual(backend.list_entries(), expected_entries)

        stubber.assert_no_pending_responses()

    def test_list_entries_empty(self) -> None:
        """`S3FileBackend.list_entries` handles an empty backend."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        stubber = stub.Stubber(backend.client)
        stubber.add_response("list_objects_v2", {}, {"Bucket": "test-bucket"})

        with stubber:
            self.assertCountEqual(backend.list_entries(), [])

        stubber.assert_no_pending_responses()

    def test_list_entries_mtime_filter(self) -> None:
        """`S3FileBackend.list_entries` can filter by modification time."""
        backend = self.file_store.get_backend_object()
        assert isinstance(backend, S3FileBackend)
        data_gen = data_generator(16)
        files = [
            self.playground.create_file(contents=next(data_gen))
            for _ in range(10)
        ]
        expected_entries: list[S3FileBackendEntry] = []
        for file in files:
            hash_hex = file.sha256.hex()
            expected_entries.append(
                S3FileBackendEntry(
                    backend=backend, identifier=f"{hash_hex}-{file.size}"
                )
            )
        now = timezone.now()
        stubber = stub.Stubber(backend.client)
        stubber.add_response(
            "list_objects_v2",
            {
                "Contents": [
                    {
                        "Key": f"{file.sha256.hex()}-{file.size}",
                        "LastModified": now - timedelta(days=10 - i),
                    }
                    for i, file in enumerate(files)
                ]
            },
            {"Bucket": "test-bucket"},
        )

        with stubber:
            self.assertCountEqual(
                backend.list_entries(
                    mtime_filter=lambda mtime: mtime < now - timedelta(days=6)
                ),
                expected_entries[:4],
            )

        stubber.assert_no_pending_responses()
