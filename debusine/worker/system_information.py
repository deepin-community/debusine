# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Functions to get system information."""
import subprocess
from functools import cache
from typing import Any

import psutil

from debusine.tasks.models import WorkerType


def total_physical_memory() -> int:
    """Return bytes of RAM memory in system."""
    return psutil.virtual_memory().total


def cpu_count() -> int:
    """Return number of CPUs in the system."""
    if (count := psutil.cpu_count()) is None:
        return 1
    return count


def cpu_time() -> float:
    """Return total CPU time (user + system) used so far."""
    cpu_times = psutil.cpu_times()
    return cpu_times.user + cpu_times.system


@cache
def host_architecture() -> str | None:
    """Return the architecture of the system or None if dpkg not available."""
    try:
        return subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def kernel_supported_architectures() -> list[str] | None:
    """
    Return the architectures supported by the kernel (using arch-test).

    If arch-test fails: return None.
    """
    try:
        return sorted(
            subprocess.check_output(["arch-test", "-n"], text=True)
            .strip()
            .split("\n")
        )
    except subprocess.CalledProcessError:
        return None


def system_metadata(worker_type: WorkerType) -> dict[str, Any]:
    """Return system metadata formatted as a dictionary."""
    metadata = {
        "system:cpu_count": cpu_count(),
        "system:total_physical_memory": total_physical_memory(),
        "system:worker_type": worker_type,
    }
    if host_arch := host_architecture():
        metadata.update(
            {
                "system:host_architecture": host_arch,
                "system:architectures": [host_arch],
            }
        )

    # More architectures might be supported by the kernel
    if (kernel_supported_archs := kernel_supported_architectures()) is not None:
        metadata["system:architectures"] = kernel_supported_archs

    return metadata
