# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the system_information."""
import subprocess
from unittest import mock
from unittest.mock import MagicMock

import psutil

from debusine.tasks.models import WorkerType
from debusine.test import TestCase
from debusine.worker import system_information


class TestSystemInformation(TestCase):
    """Test for system_information.py functions."""

    def setUp(self) -> None:
        """Ensure that host_architecture() is not cached."""
        system_information.host_architecture.cache_clear()
        self.addCleanup(system_information.host_architecture.cache_clear)

    def test_total_physical_memory(self) -> None:
        """Test total_physical_memory function."""
        total_physical_memory = system_information.total_physical_memory()

        # Assert that total_physical_memory "looks good": it is an integer
        # and at least 128 MB of memory
        self.assertIsInstance(total_physical_memory, int)
        self.assertGreaterEqual(total_physical_memory, 128 * 1024 * 1024)

        # Check that it matches the underlying implementation
        self.assertEqual(total_physical_memory, psutil.virtual_memory().total)

    def test_total_cpu_count(self) -> None:
        """Test cpu_count function."""
        cpu_count = system_information.cpu_count()

        # Assert that cpu_count "looks good": it is an integer
        # and at least 1 CPU
        self.assertIsInstance(cpu_count, int)
        self.assertGreaterEqual(cpu_count, 1)

        # Check that it matches the underlying implementation
        self.assertEqual(cpu_count, psutil.cpu_count())

    def test_total_cpu_count_undefined(self) -> None:
        """Test cpu_count function when undefined."""
        with mock.patch("psutil.cpu_count", return_value=None):
            self.assertEqual(system_information.cpu_count(), 1)

    def test_host_architecture(self) -> None:
        """Test host_architecture() function."""
        architecture = system_information.host_architecture()

        # Assert that architecture "looks good": type, length, no end of line
        assert isinstance(architecture, str)
        self.assertGreaterEqual(len(architecture), 1)
        self.assertEqual(architecture, architecture.strip())

        self.assertEqual(
            architecture,
            subprocess.check_output(
                ["dpkg", "--print-architecture"], text=True
            ).strip(),
        )

    def patch_subprocess_check_output(self) -> MagicMock:
        """Patch subprocess.check_output. Return its mock."""
        patcher = mock.patch("subprocess.check_output", autospec=True)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def test_host_architecture_dpkg_not_available(self) -> None:
        """
        Test host_architecture function: return None.

        dpkg is not available or returns an error.
        """
        mocked = self.patch_subprocess_check_output()
        mocked.side_effect = FileNotFoundError()
        self.assertIsNone(system_information.host_architecture())

        system_information.host_architecture.cache_clear()
        mocked.side_effect = subprocess.CalledProcessError(
            1, ["dpkg", "--print-architecture"]
        )
        self.assertIsNone(system_information.host_architecture())

    def test_kernel_supported_architectures_arch_test_fails(self) -> None:
        """
        Test kernel_supported_architectures return None.

        arch-test returns an error.
        """
        mocked = self.patch_subprocess_check_output()

        mocked.side_effect = subprocess.CalledProcessError(
            1, ["arch-test", "-n"]
        )
        self.assertIsNone(system_information.kernel_supported_architectures())

    def test_kernel_supported_architectures(self) -> None:
        """Test kernel_supported_architectures returns a list of archs."""
        mocked = self.patch_subprocess_check_output()

        architectures = ["x1", "x2"]
        mocked.side_effect = ["x2\nx1\n"]

        self.assertEqual(
            system_information.kernel_supported_architectures(), architectures
        )

    def test_system_metadata_without_host_kernel_supported_architectures(
        self,
    ) -> None:
        """
        system_metadata() does not include architectures related keys.

        Functions host_architecture and kernel_supported_architectures returned
        None.
        """
        with (
            mock.patch(
                "debusine.worker.system_information.host_architecture",
                return_value=None,
            ) as architecture_mocked,
            mock.patch(
                "debusine.worker.system_information"
                ".kernel_supported_architectures",
                return_value=None,
            ) as kernel_supported_architectures_mocked,
        ):
            metadata = system_information.system_metadata(WorkerType.EXTERNAL)

        architecture_mocked.assert_called()
        kernel_supported_architectures_mocked.assert_called()

        self.assertNotIn("system:host_architecture", metadata)
        self.assertNotIn("system:architectures", metadata)

    def test_system_metadata_architectures_same_as_host_architecture(
        self,
    ) -> None:
        """
        system_metadata() returns valid metadata for an external worker.

        Method system_information.kernel_supported_architectures() returns
        None: architectures is the same as host_architecture.
        """
        total_physical_memory = system_information.total_physical_memory()
        cpu_count = system_information.cpu_count()
        host_architecture = system_information.host_architecture()

        with mock.patch(
            "debusine.worker.system_information.kernel_supported_architectures",
            return_value=None,
        ):
            self.assertEqual(
                system_information.system_metadata(WorkerType.EXTERNAL),
                {
                    "system:cpu_count": cpu_count,
                    "system:total_physical_memory": total_physical_memory,
                    "system:worker_type": WorkerType.EXTERNAL,
                    "system:host_architecture": host_architecture,
                    "system:architectures": [host_architecture],
                },
            )

    def test_system_metadata_architectures_different_to_host_architecture(
        self,
    ) -> None:
        """system_metadata() populates architectures."""
        host_architecture = system_information.host_architecture()
        kernel_supported_architectures = ["x1", "x2"]
        with mock.patch(
            "debusine.worker.system_information.kernel_supported_architectures",
            return_value=kernel_supported_architectures,
        ):
            self.assertDictContainsAll(
                system_information.system_metadata(WorkerType.EXTERNAL),
                {
                    "system:host_architecture": host_architecture,
                    "system:architectures": kernel_supported_architectures,
                },
            )

    def test_system_metadata_celery(self) -> None:
        """system_metadata() returns valid metadata for a Celery worker."""
        total_physical_memory = system_information.total_physical_memory()
        cpu_count = system_information.cpu_count()
        host_architecture = system_information.host_architecture()

        system_metadata = system_information.system_metadata(WorkerType.CELERY)

        # system:architectures is tested in other unit tests
        system_metadata.pop("system:architectures")

        self.assertEqual(
            system_metadata,
            {
                "system:cpu_count": cpu_count,
                "system:total_physical_memory": total_physical_memory,
                "system:worker_type": WorkerType.CELERY,
                "system:host_architecture": host_architecture,
            },
        )

    def test_system_metadata_signing(self) -> None:
        """system_metadata() returns valid metadata for a signing worker."""
        total_physical_memory = system_information.total_physical_memory()
        cpu_count = system_information.cpu_count()
        host_architecture = system_information.host_architecture()

        system_metadata = system_information.system_metadata(WorkerType.SIGNING)

        # system:architectures is tested in other unit tests
        system_metadata.pop("system:architectures")

        self.assertEqual(
            system_metadata,
            {
                "system:cpu_count": cpu_count,
                "system:total_physical_memory": total_physical_memory,
                "system:worker_type": WorkerType.SIGNING,
                "system:host_architecture": host_architecture,
            },
        )
