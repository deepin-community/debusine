# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the QemuExecutor class."""
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

from debusine.client.debusine import Debusine
from debusine.tasks import Noop
from debusine.tasks.executors.base import (
    ExecutorStatistics,
    ImageImportError,
    ImageNotDownloadedError,
    executor_class,
)
from debusine.tasks.executors.qemu import QemuExecutor
from debusine.tasks.tests.helper_mixin import ExternalTaskHelperMixin
from debusine.test import TestCase


class QemuExecutorTests(ExternalTaskHelperMixin[Noop], TestCase):
    """Unit tests for QemuExecutor."""

    def setUp(self) -> None:
        """Mock the Debusine API for tests."""
        super().setUp()
        self.debusine_api = MagicMock(spec=Debusine)
        self.image_artifact = self.mock_image_download(
            self.debusine_api, system_image=True
        )
        self.image_artifact.data["pkglist"]["python3-minimal"] = "3.0"

        self.executor = QemuExecutor(self.debusine_api, 42)

    def test_backend_name(self) -> None:
        """Test that the backend_name attribute was set."""
        self.assertEqual(QemuExecutor.backend_name, "qemu")

    @mock.patch("platform.machine")
    @mock.patch("os.access")
    def test_available(self, access: MagicMock, machine: MagicMock) -> None:
        """available() returns True if qemu and /dev/kvm are available."""
        access.return_value = True
        machine.return_value = "x86_64"
        self.mock_is_command_available({"qemu-system-x86_64": True})
        self.assertTrue(QemuExecutor.available())

    @mock.patch("platform.machine")
    @mock.patch("os.access")
    def test_available_no_qemu(
        self, access: MagicMock, machine: MagicMock
    ) -> None:
        """Test that available() returns False if qemu is not available."""
        access.return_value = True
        machine.return_value = "x86_64"
        self.mock_is_command_available({"qemu-system-x86_64": False})
        self.assertFalse(QemuExecutor.available())

    @mock.patch("platform.machine")
    @mock.patch("os.access")
    def test_available_no_kvm(
        self, access: MagicMock, machine: MagicMock
    ) -> None:
        """Test that available() returns False if kvm is not available."""
        access.return_value = False
        machine.return_value = "x86_64"
        self.mock_is_command_available({"qemu-system-x86_64": True})
        self.assertFalse(QemuExecutor.available())

    def test_instantiation_fetches_artifact(self) -> None:
        """Test that instantiating QemuExecutor fetches the artifact."""
        self.assertEqual(self.executor.system_image, self.image_artifact)

    def test_download_image(self) -> None:
        """Test that download_image calls ImageCache.download_image."""
        response = self.executor.download_image()
        expected_path = self.image_cache_path / "42/image.qcow2"
        self.assertEqual(self.executor._local_path, expected_path)
        self.assertEqual(response, str(expected_path))

    def test_download_image_requires_python3(self) -> None:
        """Test that download_image requires python3-minimal in the image."""
        del self.image_artifact.data["pkglist"]["python3-minimal"]
        with self.assertRaisesRegex(
            ImageImportError,
            (
                "Image doesn't contain a python3-minimal, required by qemu "
                "backend."
            ),
        ):
            self.executor.download_image()

    def test_image_name(self) -> None:
        """Test that image_name returns the previously downloaded image path."""
        self.executor.download_image()
        response = self.executor.image_name()
        expected_path = self.image_cache_path / "42/image.qcow2"
        self.assertEqual(response, str(expected_path))

    def test_image_name_not_downloaded(self) -> None:
        """Test that image_name fails if there is no name."""
        with self.assertRaisesRegex(
            ImageNotDownloadedError, r"download_image\(\) not yet called"
        ):
            self.executor.image_name()

    def test_autopkgtest_virt_server(self) -> None:
        """Test that autopkgtest_virt_server returns qemu."""
        self.assertEqual(self.executor.autopkgtest_virt_server(), "qemu")

    def test_autopkgtest_virt_args(self) -> None:
        """Test that autopkgtest_virt_args returns sane arguments."""
        self.executor._local_path = Path("/not/used/path/image.qcow2")
        self.assertEqual(
            self.executor.autopkgtest_virt_args(),
            [
                "--boot",
                "efi",
                "--dpkg-architecture",
                "amd64",
                str(self.executor._local_path),
            ],
        )

    def test_autopkgtest_virt_args_before_download(self) -> None:
        """Test that autopkgtest_virt_args requires download."""
        with self.assertRaisesRegex(
            ImageNotDownloadedError, r"download_image\(\) not yet called"
        ):
            self.executor.autopkgtest_virt_args()

    def test_executor_class_finds_qemu(self) -> None:
        """Test that executor_class() supports qemu."""
        instance = executor_class("qemu")
        self.assertEqual(instance, QemuExecutor)

    def test_create(self) -> None:
        """Test create() raises NotImplemented."""
        with self.assertRaises(NotImplementedError):
            self.executor.create()

    def test_get_statistics(self) -> None:
        """`get_statistics` returns empty statistics."""
        self.assertEqual(
            QemuExecutor.get_statistics(None), ExecutorStatistics()
        )
