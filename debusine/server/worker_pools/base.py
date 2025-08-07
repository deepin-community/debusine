# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""API for provisioning and managing cloud workers."""

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from debusine.assets import CloudProvidersType

if TYPE_CHECKING:
    from debusine.db.models import Worker, WorkerPool

from debusine.server.worker_pools.models import WorkerPoolSpecifications

providers: dict[CloudProvidersType, type["WorkerPoolInterface"]] = {}


class WorkerPoolInterface(ABC):
    """Interface for all cloud compute providers."""

    worker_pool: "WorkerPool"
    specifications: WorkerPoolSpecifications

    def __init__(self, worker_pool: "WorkerPool"):
        """Initialize WorkerPoolInterface."""
        self.worker_pool = worker_pool
        self.specifications = worker_pool.specifications_model

    def __init_subclass__(
        cls, cloud_provider: CloudProvidersType, **kwargs: Any
    ) -> None:
        """Register an instance of WorkerPoolInterface by cloud_provider."""
        super().__init_subclass__(**kwargs)
        providers[cloud_provider] = cls

    @abstractmethod
    def launch_worker(self, worker: "Worker") -> None:
        """
        Launch a cloud worker using worker.

        The implementation is expected to set worker.instance_created_at to the
        current timestamp, and store state in worker.worker_pool_data.

        This method is idempotent, repeatedly calling it on the same worker
        should not create additional cloud instances or restart existing ones.
        """

    @abstractmethod
    def terminate_worker(self, worker: "Worker") -> None:
        """
        Terminate the cloud worker specified.

        The implementation is expected to set worker.instance_created_at to
        None and clear worker.worker_pool_data if it was able to successfully
        initiate instance termination.

        This method is idempotent.
        """


def provider_interface(worker_pool: "WorkerPool") -> WorkerPoolInterface:
    """Return a WorkerPoolInterface for the specified worker_pool."""
    cloud_provider = worker_pool.provider_account.data["provider_type"]
    return providers[cloud_provider](worker_pool)
