# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The qemu executor backend."""
import os
import platform
from pathlib import Path
from typing import NoReturn

from debusine import utils
from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse
from debusine.tasks.executors.base import (
    ExecutorImageCategory,
    ExecutorInterface,
    ExecutorStatistics,
    ImageImportError,
    ImageNotDownloadedError,
    InstanceInterface,
)
from debusine.tasks.executors.images import ImageCache


class QemuExecutor(ExecutorInterface, backend_name="qemu"):
    """Support the qemu(1) executor."""

    system_image: ArtifactResponse
    _image_cache: ImageCache
    _local_path: Path | None = None

    image_category = ExecutorImageCategory.IMAGE

    def __init__(self, debusine_api: Debusine, system_image_id: int):
        """
        Instantiate a QemuExecutor.

        :param debusine_api: The object to use the debusine client API.
        :param system_image_id: An artifact ID pointing to the system image.
        """
        self._image_cache = ImageCache(debusine_api, self.image_category)
        self.system_image = self._image_cache.image_artifact(system_image_id)

    @classmethod
    def available(cls) -> bool:
        """Determine whether this executor is available for operation."""
        arch = platform.machine()
        if not utils.is_command_available(f"qemu-system-{arch}"):
            return False
        return os.access("/dev/kvm", os.W_OK)

    def download_image(self) -> str:
        """
        Make the image available locally.

        Fetch the image from artifact storage, if it isn't already available,
        and make it available locally.

        Return a path to the image or name, as appropriate for the backend.
        """
        if "python3-minimal" not in self.system_image.data["pkglist"]:
            raise ImageImportError(
                "Image doesn't contain a python3-minimal, required by qemu "
                "backend."
            )
        self._local_path = self._image_cache.download_image(self.system_image)
        return self.image_name()

    def image_name(self) -> str:
        """Return the path to the downloaded image."""
        if self._local_path is None:
            raise ImageNotDownloadedError("download_image() not yet called")
        return str(self._local_path)

    def create(self) -> NoReturn:
        """
        Create an QemuInstance using the downloaded image.

        Returns a new, stopped QemuInstance.
        """
        raise NotImplementedError("No Instance implemented for Qemu")

    def autopkgtest_virt_server(self) -> str:
        """Return the name of the autopkgtest-virt-server for this backend."""
        return "qemu"

    def autopkgtest_virt_args(self) -> list[str]:
        """Generate the arguments to drive an autopkgtest-virt-server."""
        if self._local_path is None:
            raise ImageNotDownloadedError("download_image() not yet called")
        return [
            "--boot",
            self.system_image.data["boot_mechanism"],
            "--dpkg-architecture",
            self.system_image.data["architecture"],
            str(self._local_path),
        ]

    @classmethod
    def get_statistics(
        cls, instance: InstanceInterface | None  # noqa: U100
    ) -> ExecutorStatistics:
        """Return statistics for this executor."""
        # TODO: The autopkgtest-virt-server interface makes this very
        # difficult to implement.  See
        # https://salsa.debian.org/freexian-team/debusine/-/issues/671.
        return ExecutorStatistics()
