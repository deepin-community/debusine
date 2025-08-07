# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db file management."""

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pgtrigger
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import CheckConstraint, F, Q, UniqueConstraint

from debusine.server.file_backend.models import (
    ExternalDebianSuiteBackendConfiguration,
    LocalFileBackendConfiguration,
    MemoryFileBackendConfiguration,
    S3FileBackendConfiguration,
)
from debusine.utils import calculate_hash

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.server.file_backend.interface import FileBackendInterface
else:
    TypedModelMeta = object


class FileManager(models.Manager["File"]):
    """Manager for File model."""

    def get_or_create(
        self,
        defaults: Mapping[str, Any] | None = None,
        hash_digest: bytes | None = None,
        **kwargs: Any,
    ) -> "tuple[File, bool]":
        """
        Get or create a File.

        If hash_digest is given, it must be computed using
        File.current_hash_algorithm.
        """
        kwargs = kwargs.copy()
        if hash_digest is not None:
            kwargs[File.current_hash_algorithm] = hash_digest
        return super().get_or_create(defaults, **kwargs)


class File(models.Model):
    """
    Database model of a file.

    Model different attributes of the file.

    From outside the class use the property ``hash`` and do not use ``sha256``
    field. This allows, if ever needed, to change the hash algorithm only
    modifying this class without changing the users of this class.
    """

    objects = FileManager()

    current_hash_algorithm = "sha256"

    sha256 = models.BinaryField(
        max_length=int(hashlib.new(current_hash_algorithm).digest_size / 8),
        help_text=f"{current_hash_algorithm} of the file",
    )
    size = models.PositiveBigIntegerField(help_text="Size in bytes of the file")

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["sha256", "size"],
                name="%(app_label)s_%(class)s_unique_sha256_size",
            ),
            CheckConstraint(
                name="%(app_label)s_%(class)s_sha256_not_empty",
                check=~Q(sha256=b""),
            ),
        ]

    def __str__(self) -> str:
        """Return basic information of File."""
        return (
            f"id: {self.id} "
            f"{self.current_hash_algorithm}: {self.hash_digest.hex()} "
            f"size: {self.size}"
        )

    @classmethod
    def from_local_path(cls, local_path: Path) -> "File":
        """Return a File with the fields."""
        file, created = cls.objects.get_or_create(
            hash_digest=cls.calculate_hash(local_path),
            size=os.stat(local_path).st_size,
        )
        return file

    @property
    def hash_digest(self) -> bytes:
        """
        Return the default hash digest of the File.

        Use this property instead of the field to allow the algorithm
        to be changed in the future.
        """
        return bytes(getattr(self, self.current_hash_algorithm))

    @hash_digest.setter
    def hash_digest(self, value: bytes) -> None:
        setattr(self, self.current_hash_algorithm, value)

    @classmethod
    def calculate_hash(cls, file_path: Path) -> bytes:
        """Return hash for the file."""
        return calculate_hash(file_path, cls.current_hash_algorithm)


class _FileStoreBackendChoices(models.TextChoices):
    """Enumerate all the Backend choices."""

    LOCAL = "Local", "Local"
    EXTERNAL_DEBIAN_SUITE = "ExternalDebianSuite", "ExternalDebianSuite"
    MEMORY = "Memory", "Memory"
    S3 = "S3", "S3"


DEFAULT_FILE_STORE_NAME = "Default"


def default_file_store() -> "FileStore":
    """Return the default file store."""
    return FileStore.objects.get(name=DEFAULT_FILE_STORE_NAME)


class FileStore(models.Model):
    """
    Database model of a FileStore.

    FileStore has files attached to it.
    """

    # BackendChoices not defined in FileStore to make it accessible
    # during the migrations. Historical models are used
    # (https://docs.djangoproject.com/en/3.2/topics/migrations/#historical-models)
    BackendChoices = _FileStoreBackendChoices

    configuration_validators = {
        BackendChoices.LOCAL: LocalFileBackendConfiguration,
        BackendChoices.EXTERNAL_DEBIAN_SUITE: (
            ExternalDebianSuiteBackendConfiguration
        ),
        BackendChoices.MEMORY: MemoryFileBackendConfiguration,
        BackendChoices.S3: S3FileBackendConfiguration,
    }

    name = models.CharField(max_length=255, unique=True)
    backend = models.CharField(max_length=255, choices=BackendChoices.choices)
    configuration = models.JSONField(default=dict, blank=True)
    files = models.ManyToManyField(File, through="db.FileInStore")
    instance_wide = models.BooleanField(default=True)
    total_size = models.BigIntegerField(default=0, editable=False)
    soft_max_size = models.BigIntegerField(blank=True, null=True)
    max_size = models.BigIntegerField(blank=True, null=True)
    provider_account = models.ForeignKey(
        "Asset", blank=True, null=True, on_delete=models.PROTECT
    )

    class Meta(TypedModelMeta):
        triggers = [
            pgtrigger.Trigger(
                name="db_filestore_sync_instance_wide",
                operation=pgtrigger.Insert | pgtrigger.Update,
                when=pgtrigger.After,
                func=" ".join(
                    """
                    UPDATE db_filestoreinscope
                        SET file_store_instance_wide = NEW.instance_wide
                        WHERE
                            db_filestoreinscope.file_store_id = NEW.id
                            AND db_filestoreinscope.file_store_instance_wide
                                != NEW.instance_wide;
                    RETURN NEW;
                    """.split()
                ),
            )
        ]
        constraints = [
            CheckConstraint(
                name="%(app_label)s_%(class)s_consistent_max_sizes",
                check=~Q(soft_max_size__gt=F("max_size")),
            )
        ]

    @staticmethod
    def default() -> "FileStore":
        """Return the default FileStore."""
        return default_file_store()

    def __str__(self) -> str:
        """Return basic information of FileStore."""
        return f"Id: {self.id} Name: {self.name} Backend: {self.backend}"

    def clean(self) -> None:
        """
        Ensure that data is valid for this backend type.

        :raise ValidationError: for invalid data.
        """
        if not isinstance(self.configuration, dict):
            raise ValidationError(
                {"configuration": "configuration must be a dictionary"}
            )

        try:
            self.configuration_validators[
                _FileStoreBackendChoices(self.backend)
            ](**self.configuration)
        except (TypeError, ValueError) as e:
            raise ValidationError(
                {"configuration": f"invalid file store configuration: {e}"}
            )

    def get_backend_object(self) -> "FileBackendInterface[Any]":
        """Instantiate the correct FileBackend and return it."""
        match self.backend:
            case _FileStoreBackendChoices.LOCAL:
                from debusine.server.file_backend.local import LocalFileBackend

                return LocalFileBackend(self)
            case _FileStoreBackendChoices.MEMORY:
                from debusine.server.file_backend.memory import (
                    MemoryFileBackend,
                )

                return MemoryFileBackend(self)
            case _FileStoreBackendChoices.EXTERNAL_DEBIAN_SUITE:
                from debusine.server.file_backend.external import (
                    ExternalDebianSuiteFileBackend,
                )

                return ExternalDebianSuiteFileBackend(self)
            case _FileStoreBackendChoices.S3:
                from debusine.server.file_backend.s3 import S3FileBackend

                return S3FileBackend(self)
            case _ as unreachable:
                raise NotImplementedError(
                    f"backend {unreachable!r} not supported"
                )


class FileInStore(models.Model):
    """
    Database model used as "through" from FileStore.

    Keeps the relationship between stores, files and attached data.
    """

    store = models.ForeignKey(FileStore, on_delete=models.PROTECT)
    file = models.ForeignKey(File, on_delete=models.PROTECT)
    data = models.JSONField(default=dict, blank=True)

    class Meta(TypedModelMeta):
        triggers = [
            # Maintain FileStore.total_size: adding a file to a store
            # increases it, and removing a file from a store decreases it.
            pgtrigger.Trigger(
                name="db_fileinstore_total_size_insert",
                operation=pgtrigger.Insert,
                when=pgtrigger.After,
                func=" ".join(
                    """
                    UPDATE db_filestore
                        SET total_size = total_size + db_file.size
                        FROM db_file
                        WHERE
                            db_filestore.id = NEW.store_id
                            AND db_file.id = NEW.file_id;
                    RETURN NULL;
                    """.split()
                ),
            ),
            pgtrigger.Trigger(
                name="db_fileinstore_total_size_delete",
                operation=pgtrigger.Delete,
                when=pgtrigger.After,
                func=" ".join(
                    """
                    UPDATE db_filestore
                        SET total_size = total_size - db_file.size
                        FROM db_file
                        WHERE
                            db_filestore.id = OLD.store_id
                            AND db_file.id = OLD.file_id;
                    RETURN NULL;
                    """.split()
                ),
            ),
        ]
        constraints = [
            UniqueConstraint(
                fields=["store", "file"],
                name="%(app_label)s_%(class)s_unique_store_file",
            )
        ]

    def __str__(self) -> str:
        """Return basic information of FileInStore."""
        return (
            f"Id: {self.id} "
            f"Store: {self.store.name} "
            f"File: {self.file.hash_digest.hex()}"
        )
