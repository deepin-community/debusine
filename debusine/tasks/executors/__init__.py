# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Executors for Debusine Tasks.

Provides containment for tasks using a variety of backends.
"""

from debusine.tasks.executors.base import (
    ExecutorImageCategory,
    ExecutorInterface,
    ExecutorStatistics,
    InstanceInterface,
    analyze_worker_all_executors,
    executor_class,
)
from debusine.tasks.executors.incus import (
    IncusInstance,
    IncusLXCExecutor,
    IncusVMExecutor,
)
from debusine.tasks.executors.qemu import QemuExecutor
from debusine.tasks.executors.unshare import UnshareExecutor, UnshareInstance

__all__ = [
    "ExecutorImageCategory",
    "ExecutorInterface",
    "ExecutorStatistics",
    "IncusInstance",
    "IncusLXCExecutor",
    "IncusVMExecutor",
    "InstanceInterface",
    "QemuExecutor",
    "UnshareExecutor",
    "UnshareInstance",
    "analyze_worker_all_executors",
    "executor_class",
]
