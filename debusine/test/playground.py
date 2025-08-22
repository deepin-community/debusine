# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Infrastructure to create test scenarios in the database."""

import contextlib
import copy
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Self

from debusine.db import playground
from debusine.db.models import File, FileStore
from debusine.server.file_backend.memory import MemoryFileBackend
from debusine.server.file_backend.models import (
    LocalFileBackendConfiguration,
    MemoryFileBackendConfiguration,
)


class Playground(playground.Playground, contextlib.ExitStack):
    """
    Generate test data scenarios for the database.

    It can be used with setUpTestData, in which case it supports the deepcopy
    protocol to preserve non-database assets across test methods
    """

    def __init__(self, memory_file_store: bool = True, **kwargs: Any) -> None:
        """Set default values."""
        super().__init__(**kwargs)
        self.file_store = self.get_default_file_store()
        self.memory_file_store = memory_file_store

    def __enter__(self) -> Self:
        """Set up the playground's file store."""
        super().__enter__()
        self.original_backend = self.file_store.backend
        self.original_configuration = self.file_store.configuration
        if self.memory_file_store:
            self.initial_storages: dict[str, dict[str, bytes]] | None = None
            self.file_store.backend = FileStore.BackendChoices.MEMORY
            mem_config = MemoryFileBackendConfiguration(
                name=self.file_store.name
            )
            self.file_store.configuration = mem_config.dict()
            self.file_store.save()
        else:
            self.stores_path = Path(
                self.enter_context(tempfile.TemporaryDirectory())
            )
            self.current_store_path = self.stores_path / "current"
            self.current_store_path.mkdir()
            self.saved_store_path = self.stores_path / "saved"
            local_config = LocalFileBackendConfiguration(
                base_directory=self.current_store_path.as_posix()
            )
            self.file_store.backend = FileStore.BackendChoices.LOCAL
            self.file_store.configuration = local_config.dict()
            self.file_store.save()
        return self

    def __exit__(self, *exc_details: Any) -> None:
        """Tear down the playground's file store."""
        self.file_store.backend = self.original_backend
        self.file_store.configuration = self.original_configuration
        self.file_store.save()
        for name in (
            "current_store_path",
            "initial_storages",
            "original_backend",
            "original_configuration",
            "saved_store_path",
            "stores_path",
        ):
            if hasattr(self, name):
                delattr(self, name)
        super().__exit__(*exc_details)

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        """Deep copy of Playground, as used by Django's TestCase."""
        # Standard deepcopy
        cls = self.__class__
        result = cls.__new__(cls)
        for k, v in self.__dict__.items():
            setattr(result, k, copy.deepcopy(v, memo))

        if hasattr(self, "original_backend"):
            match self.file_store.backend:
                case FileStore.BackendChoices.MEMORY:
                    self._deepcopy_memory(memo)
                case FileStore.BackendChoices.LOCAL:
                    self._deepcopy_files(memo)
                case _ as unreachable:
                    raise NotImplementedError(
                        f"backend {unreachable!r} not supported"
                    )

        return result

    def _deepcopy_memory(self, memo: dict[int, Any]) -> None:
        """Preserve the storage of MemoryFileBackend."""
        if self.initial_storages is None:
            self.initial_storages = copy.deepcopy(
                MemoryFileBackend.storages, memo
            )
        else:
            MemoryFileBackend.storages = copy.deepcopy(
                self.initial_storages, memo
            )

    def _deepcopy_files(self, memo: dict[int, Any]) -> None:  # noqa: U100
        """Preserve the storage of LocalFileBackend."""
        if not self.saved_store_path.exists():
            self.current_store_path.rename(self.saved_store_path)
        if self.current_store_path.exists():
            shutil.rmtree(self.current_store_path)
        # Hard link all files from saved_store to current_store
        subprocess.run(
            [
                "cp",
                "--link",
                "--recursive",
                self.saved_store_path.as_posix(),
                self.current_store_path,
            ],
            check=True,
        )

    def create_file_in_backend(self, *args: Any, **kwargs: Any) -> File:
        """
        Create a temporary file and adds it in the backend.

        :param backend: file backend to add the file in
        :param contents: contents of the file
        """
        file = super().create_file_in_backend(*args, **kwargs)
        if self.file_store.backend == FileStore.BackendChoices.LOCAL:
            backend = self.file_store.get_backend_object()
            self.callback(backend.remove_file, file)
        return file
