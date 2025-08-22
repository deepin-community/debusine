# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Implementation of Amazon S3 file backend."""

import filecmp
import os
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Self, TYPE_CHECKING
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from packaging.version import Version

from debusine.assets.models import AWSProviderAccountData, CloudProvidersType
from debusine.db.models import File, FileStore
from debusine.server.file_backend.interface import (
    FileBackendEntryInterface,
    FileBackendInterface,
)
from debusine.server.file_backend.models import S3FileBackendConfiguration

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

    S3Client  # fake usage for vulture


class S3FileBackendEntry(FileBackendEntryInterface["S3FileBackend", str]):
    """
    An entry in an Amazon S3 file backend.

    S3 identifiers are "hash-size", where "hash" is the SHA-256 hex digest
    of the file contents and "size" is the file size in bytes.
    """

    @classmethod
    def _create_from_file(cls, backend: "S3FileBackend", fileobj: File) -> Self:
        """Make an entry based on a `File`."""
        return cls(
            backend=backend, identifier=f"{fileobj.sha256.hex()}-{fileobj.size}"
        )

    def __str__(self) -> str:
        """Return a string describing this entry."""
        return self._identifier

    def get_local_path(self) -> None:
        """Return None: no local paths for S3 files."""
        return None

    @contextmanager
    def get_temporary_local_path(self) -> Generator[Path]:
        """Yield a temporary local path for the file."""
        # TODO: Once we can assume Python >= 3.12, it would be better to use
        # delete_on_close=False here and call temp_file.close() instead of
        # temp_file.flush().
        with tempfile.NamedTemporaryFile(prefix="debusine-s3-") as temp_file:
            self.backend.client.download_fileobj(
                self.backend.configuration.bucket_name,
                self._identifier,
                temp_file,
            )
            temp_file.flush()
            yield Path(temp_file.name)

    def get_url(self, content_type: str | None = None) -> str:
        """Return a presigned URL for the file."""
        params = {
            "Bucket": self.backend.configuration.bucket_name,
            "Key": self._identifier,
        }
        if content_type is not None:
            params["ResponseContentType"] = content_type
        url = self.backend.client.generate_presigned_url(
            "get_object",
            Params=params,
            # Hardcoded to one minute for now.  These URLs are currently
            # returned in redirects which are presumably followed promptly
            # by clients.
            ExpiresIn=60,
        )
        assert isinstance(url, str)
        return url

    def add(self, local_path: Path) -> None:
        """Add the contents of `local_path` to the underlying storage."""
        extra_args = {}
        # Hetzner object storage doesn't currently support data integrity
        # protection for uploads.  See:
        #   https://status.hetzner.com/incident/b6382a74-0fd9-4789-b997-65249187fbc7
        #   https://github.com/boto/boto3/issues/4392
        if not self.backend._is_hetzner:
            extra_args["ChecksumAlgorithm"] = "SHA256"
        if self.backend.configuration.storage_class is not None:
            extra_args["StorageClass"] = (
                self.backend.configuration.storage_class
            )
        self.backend.client.upload_file(
            # TODO: Drop os.fspath() once we can rely on boto3 >= 1.26.32.
            os.fspath(local_path),
            self.backend.configuration.bucket_name,
            self._identifier,
            ExtraArgs=extra_args,
        )

    def same_contents(self, local_path: Path) -> bool:
        """Check whether this entry has the same contents as `local_path`."""
        with self.get_temporary_local_path() as temp_path:
            # This standard library function maintains a cache indexed by
            # the paths and stat signatures (type, size, mtime) of both
            # files.  Since `temp_path` was always created in this method,
            # the cache will never be hit.
            return filecmp.cmp(local_path, temp_path, shallow=False)

    def remove(self) -> None:
        """Remove the entry from the underlying storage."""
        self.backend.client.delete_object(
            Bucket=self.backend.configuration.bucket_name, Key=self._identifier
        )


class S3FileBackend(FileBackendInterface[S3FileBackendConfiguration]):
    """Amazon S3 file backend."""

    def __init__(self, file_store: FileStore) -> None:
        """Initialize `S3FileBackend`."""
        super().__init__()
        self.db_store = file_store
        if file_store.provider_account is None:
            raise RuntimeError(
                "S3 file backend requires a cloud provider account"
            )
        if (
            file_store.provider_account.data.get("provider_type")
            != CloudProvidersType.AWS
        ):
            raise RuntimeError(
                "S3 file backend requires an AWS cloud provider account"
            )
        provider_account_data = file_store.provider_account.data_model
        assert isinstance(provider_account_data, AWSProviderAccountData)
        s3_endpoint_url = provider_account_data.configuration.s3_endpoint_url
        self._is_hetzner = s3_endpoint_url is not None and urlsplit(
            s3_endpoint_url
        ).netloc.endswith(".your-objectstorage.com")
        self.client = self._make_client(provider_account_data)

    def _make_client(
        self, provider_account_data: AWSProviderAccountData
    ) -> "S3Client":
        """
        Make an S3 client for this provider account.

        This exists mainly so that it can be patched by tests.
        """
        config: Config | None = None
        # Hetzner object storage doesn't currently support data integrity
        # protection for uploads, so tell boto3 not to require it.  See:
        #   https://status.hetzner.com/incident/b6382a74-0fd9-4789-b997-65249187fbc7
        #   https://github.com/boto/boto3/issues/4392
        if self._is_hetzner and Version(version("boto3")) >= Version(
            "1.36.0"
        ):  # pragma: no cover
            config = Config(request_checksum_calculation="when_required")
        return boto3.client(
            "s3",
            region_name=provider_account_data.configuration.region_name,
            endpoint_url=provider_account_data.configuration.s3_endpoint_url,
            aws_access_key_id=provider_account_data.credentials.access_key_id,
            aws_secret_access_key=(
                provider_account_data.credentials.secret_access_key
            ),
            config=config,
        )

    def get_entry(self, fileobj: File) -> S3FileBackendEntry:
        """Return the entry in this backend corresponding to this file."""
        return S3FileBackendEntry._create_from_file(self, fileobj)

    def list_entries(
        self, mtime_filter: Callable[[datetime], bool] | None = None
    ) -> Generator[S3FileBackendEntry, None, None]:
        """
        Yield entries in this backend.

        :param mtime_before: If given, only yield entries where this
          callable returns True when given their modification time.  The
          callable may assume that its argument is an aware datetime.
        """
        paginator = self.client.get_paginator("list_objects_v2")
        iterator = paginator.paginate(Bucket=self.configuration.bucket_name)
        for page in iterator:
            for item in page.get("Contents", []):
                if mtime_filter is not None and not mtime_filter(
                    item["LastModified"]
                ):
                    continue
                yield S3FileBackendEntry(backend=self, identifier=item["Key"])
