# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Dummy WorkerPool implementation, for testing."""

from typing import TYPE_CHECKING

from django.utils import timezone

from debusine.assets import CloudProvidersType
from debusine.server.worker_pools import WorkerPoolInterface

if TYPE_CHECKING:
    from debusine.db.models import Worker


class DummyWorkerPool(
    WorkerPoolInterface, cloud_provider=CloudProvidersType.DUMMY
):
    """Dummy implementation of WorkerPoolInterface for testing."""

    def launch_worker(self, worker: "Worker") -> None:
        """Launch a cloud worker using worker."""
        worker.instance_created_at = timezone.now()
        worker.worker_pool_data = {"launched": True}
        worker.save()

    def terminate_worker(self, worker: "Worker") -> None:
        """Terminate a cloud worker using worker."""
        worker.instance_created_at = None
        worker.worker_pool_data = None
        worker.save()
