# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Backend infrastructure for provisioning cloud workers.

See docs/reference/devel-blueprints/dynamic-compute.rst
"""

import math
from functools import cached_property

from celery import shared_task
from django.contrib.postgres.functions import TransactionNow
from django.db.models import F, IntegerField, Sum
from django.db.models.functions import Cast, Extract, Greatest, Least
from django.utils import timezone

from debusine.db.models import (
    Scope,
    ScopeWorkerPool,
    WorkRequest,
    Worker,
    WorkerPool,
)
from debusine.db.models.worker_pools import (
    WorkerPoolStatistics,
    WorkerPoolTaskExecutionStatistics,
)

# TODO: Hardcoded estimation of duration for a task, until we have code to
#       estimate the duration of future tasks based on task-statistics
TASK_DURATION_SECONDS = 10 * 60

# TODO: Hardcoded target latency for a scope, until we can read it from Scope
TARGET_LATENCY_SECONDS = 3600


def terminate_idle(minutes: int = 10) -> bool:
    """
    Find and terminate idle workers.

    :returns: true if idle workers were found
    """
    # List cloud workers that have been idle for more than 10 minutes
    idle_workers = Worker.objects.with_idle_time().filter(
        idle_time__gt=minutes * 60,
        worker_pool__isnull=False,
    )
    has_idle = False
    for worker in idle_workers:
        assert worker.worker_pool is not None
        has_idle = True
        worker.worker_pool.terminate_worker(worker)
    return has_idle


class WorkerAllocator:
    """Allocate new workers needed to handle the pending load."""

    # NOTE: This is a minimum viable implementation of cloud compute
    # provisioning, intended to be useful until we implement tagged scheduling
    # (#326).
    #
    # The following simplifications are in place:
    # * assume a hardcoded running time for tasks, until we have code to
    #   estimate the duration of future tasks based on task-statistics
    # * assume all static workers can handle all tasks
    # * assume all workers take the same time to process a task
    # * limit cloud provisioning to amd64 workers

    def __init__(self) -> None:
        """Prefetch common information from the database."""
        #: Number of available static workers
        self.static_workers_count = (
            Worker.objects.connected().filter(worker_pool__isnull=True).count()
        )

        self.start_of_month = timezone.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

    def limit_by_max_active_instances(
        self, pool: WorkerPool, amount: int
    ) -> int:
        """Limit amount by the max_active_instances configured in the pool."""
        pool_limits = pool.limits_model
        if pool_limits.max_active_instances is None:
            return amount

        current_worker_count = Worker.objects.filter(worker_pool=pool).count()
        available = pool_limits.max_active_instances - current_worker_count
        if available <= 0:
            return 0
        return min(amount, available)

    def get_used_seconds(self, pool: WorkerPool) -> int:
        """Count the seconds of pool usage for this month."""
        completed = (
            WorkerPoolStatistics.objects.filter(
                worker_pool=pool, timestamp__gte=self.start_of_month
            )
            .annotate(
                runtime_this_month=Least(
                    "runtime",
                    Cast(
                        Extract(
                            F("timestamp") - self.start_of_month,  # type: ignore[operator] # noqa: E501
                            "epoch",
                        ),
                        IntegerField(),
                    ),
                )
            )
            .aggregate(seconds_per_month=Sum("runtime_this_month"))[
                "seconds_per_month"
            ]
            or 0
        )
        connected = (
            Worker.objects.connected()
            .filter(worker_pool=pool)
            .annotate(
                runtime=Extract(
                    TransactionNow()
                    - Greatest("instance_created_at", self.start_of_month),
                    "epoch",
                )
            )
            .aggregate(seconds_per_month=Sum("runtime"))["seconds_per_month"]
            or 0
        )
        return completed + connected

    def limit_by_pool_target_max_seconds_per_month(
        self, pool: WorkerPool, amount: int
    ) -> int:
        """Limit amount by the pool's target_max_seconds_per_month."""
        target_max_seconds_per_month: int | None = (
            pool.limits_model.target_max_seconds_per_month
        )
        if target_max_seconds_per_month is None:
            return amount

        used_seconds = self.get_used_seconds(pool)
        if used_seconds >= target_max_seconds_per_month:
            return 0

        return min(
            amount,
            int(
                (target_max_seconds_per_month - used_seconds)
                / TASK_DURATION_SECONDS
            ),
        )

    def process_scope(self, scope: Scope) -> None:
        """Perform allocation for a scope."""
        allocator = ScopeWorkerAllocator(self, scope)
        allocator.process_scope()


class ScopeWorkerAllocator:
    """Allocate new workers needed to handle the pending load of a scope."""

    def __init__(self, worker_allocator: WorkerAllocator, scope: Scope) -> None:
        """Prefetch common information from the database."""
        self.worker_allocator = worker_allocator
        self.scope = scope

    def get_used_seconds(self, pool: WorkerPool) -> int:
        """Count the seconds of pool usage for the current scope this month."""
        completed = (
            WorkerPoolTaskExecutionStatistics.objects.filter(
                worker_pool=pool,
                scope=self.scope,
                timestamp__gte=self.worker_allocator.start_of_month,
            )
            .annotate(
                runtime_this_month=Least(
                    "runtime",
                    Cast(
                        Extract(
                            F("timestamp")
                            - self.worker_allocator.start_of_month,  # type: ignore[operator] # noqa: E501
                            "epoch",
                        ),
                        IntegerField(),
                    ),
                )
            )
            .aggregate(seconds_per_month=Sum("runtime_this_month"))[
                "seconds_per_month"
            ]
            or 0
        )
        running = (
            WorkRequest.objects.running()
            .filter(
                worker__worker_pool=pool,
                workspace__scope=self.scope,
                started_at__isnull=False,
            )
            .annotate(
                runtime=Extract(
                    TransactionNow()
                    - Greatest(
                        "started_at", self.worker_allocator.start_of_month
                    ),
                    "epoch",
                )
            )
            .aggregate(seconds_per_month=Sum("runtime"))["seconds_per_month"]
            or 0
        )
        return completed + running

    def limit_by_scope_pool_target_max_seconds_per_month(
        self, pool: WorkerPool, amount: int
    ) -> int:
        """Limit amount by target_max_seconds_per_month for scope and pool."""
        target_max_seconds_per_month: int | None = None
        try:
            scope_worker_pool = ScopeWorkerPool.objects.get(
                worker_pool=pool, scope=self.scope
            )
        except ScopeWorkerPool.DoesNotExist:
            return amount

        target_max_seconds_per_month = (
            scope_worker_pool.limits_model.target_max_seconds_per_month
        )
        if target_max_seconds_per_month is None:
            return amount

        used_seconds = self.get_used_seconds(pool)
        if used_seconds >= target_max_seconds_per_month:
            return 0

        return min(
            amount,
            int(
                (target_max_seconds_per_month - used_seconds)
                / TASK_DURATION_SECONDS
            ),
        )

    def allocate_workers(
        self, pool: WorkerPool, amount: int  # noqa: U100
    ) -> int:
        """
        Allocate workers on pool for the given scope.

        The number of workers allocated can be less than amount, or even zero,
        depending on pool availability and quotas.

        :param scope: the scope requesting allocation
        :param pool: the pool used to allocate
        :param amount: the amount of new workers required
        :returns: the amount of workers allocated
        """
        # Enforce max_active_instances
        amount = self.worker_allocator.limit_by_max_active_instances(
            pool, amount
        )

        # Enforce target_max_seconds_per_month
        amount = (
            self.worker_allocator.limit_by_pool_target_max_seconds_per_month(
                pool, amount
            )
        )

        # Enforce target_max_seconds_per_month
        amount = self.limit_by_scope_pool_target_max_seconds_per_month(
            pool, amount
        )

        if amount > 0:
            pool.launch_workers(amount)
        return amount

    @cached_property
    def pending_task_count(self) -> int:
        """Return the number of pending tasks."""
        return WorkRequest.objects.filter(
            workspace__scope=self.scope,
            task_data__host_architecture="amd64",
        ).count()

    @cached_property
    def workers_required(self) -> int:
        """
        Compute how many workers can process the scope's pending load.

        This computes the absolute number of workers required to process the
        scope's pending load within the expected latency targets.
        """
        # If we have no pending tasks, we do not require workers
        if (pending_tasks := self.pending_task_count) == 0:
            return 0

        # Estimated number of worker-seconds needed to run all but the last
        # pending task.
        pending_seconds = TASK_DURATION_SECONDS * max(pending_tasks - 1, 0)

        # Each worker will contribute one second per second of work towards
        # draining the queue.  There's no point in allocating more workers
        # than the number of pending tasks.
        return max(
            1,
            min(
                int(math.ceil(pending_seconds / TARGET_LATENCY_SECONDS)),
                pending_tasks,
            ),
        )

    @cached_property
    def workers_available(self) -> int:
        """Compute the number of workers currently available to the scope."""
        # Number of workers already available that can process the queue
        count = self.worker_allocator.static_workers_count

        # Add instance-wide pool workers
        count += Worker.objects.filter(worker_pool__instance_wide=True).count()

        # Add scope-assigned pool workers
        count += Worker.objects.filter(
            worker_pool__scopeworkerpool__scope=self.scope
        ).count()

        return count

    @cached_property
    def allocation_pools(self) -> list[WorkerPool]:
        """
        Return the list of pools that can be used to allocate workers.

        The list is ordered by priority, so that allocation can proceed from
        the first element onwwards.
        """
        # Pools in which we can allocate them
        pools: list[WorkerPool] = []

        # Add scope pools by decreasing priority
        pools.extend(
            WorkerPool.objects.filter(
                scopeworkerpool__scope=self.scope
            ).order_by("-scopeworkerpool__priority")
        )

        # Add instance-wide pools at the end
        pools.extend(WorkerPool.objects.filter(instance_wide=True))

        # TODO: enforce that ScopeWorkerPool cannot link to instance-wide
        # pools? Otherwise we may need to remove duplicates here

        return pools

    def process_scope(self) -> None:
        """Perform allocation for this scope."""
        # Number of workers required to process the queue in time
        workers_required = self.workers_required

        # Number of workers currently available
        workers_available = self.workers_available

        # If we have enough workers already, we are done
        if workers_required <= workers_available:
            return

        # Number of workers that we need to allocate
        new_workers_required = workers_required - workers_available

        # Allocate from one pool at a time
        for pool in self.allocation_pools:
            if new_workers_required <= 0:
                break
            allocated = self.allocate_workers(pool, new_workers_required)
            new_workers_required -= allocated


def create_new_workers() -> None:
    """Provision new workers if needed to handle pending load."""
    # NOTE: This is a minimum viable implementation of cloud compute
    # provisioning, intended to be useful until we implement tagged scheduling
    # (#326).
    #
    # The following simplifications are in place:
    # * assume all workers take the same time to process a task
    # * assume scopes' load demands are isolated from each other, which allows
    #   to process each scope's work queue without taking other scopes into
    #   account.
    #   This is not correct with regards to static workers, instance-wide pools
    #   or pools shared by multiple scopes, but in the worst case we
    #   underestimate the number of new workers needed and end up provisioning
    #   some workers again later as queues drain more slowly than estimated.
    #
    #   i.e. if two scopes would each need 15 workers to meet targets and there
    #   are 10 static workers, they'd each provision 5 from pools in this MVP,
    #   while in reality they'd need more because they're both sharing the same
    #   static workers.
    # * see WorkerAllocator for more assumptions

    allocator = WorkerAllocator()
    for scope in Scope.objects.all():
        allocator.process_scope(scope)


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task()  # type: ignore[misc]
def provision() -> None:
    """Provision or terminate workers from pools to handle pending tasks."""
    # NOTE: This is a minimum viable implementation of cloud compute
    # provisioning, intended to be useful until we implement tagged scheduling
    # (#326).
    #
    # The following simplifications are in place:
    #  * do not consider per-scope quotas when terminating workers: only idle
    #    workers are terminated
    #  * see create_new_workers() comments for more assumptions

    if terminate_idle():
        # If workers have been idle for more than 10 minutes, we can assume
        # that we do not have a significant backlog of pending work requests.
        #
        # This may not be true: we may have a pool of idle arm64 workers and a
        # backlog of amd64 tasks. If that is the case, we will catch up with
        # the backlog in the next provision run.
        return

    create_new_workers()
