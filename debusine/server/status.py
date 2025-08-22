# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Queue and worker pool summaries."""

import abc
import logging
from collections import defaultdict

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

from django.db.models import Count

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest, Worker
from debusine.tasks import TaskConfigError
from debusine.tasks.models import WorkerType

log = logging.getLogger(__name__)


class PydanticBase(pydantic.BaseModel, abc.ABC):
    """Stricter BaseModel."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid


class WorkerSummary(PydanticBase):
    """Worker pool count."""

    registered: int = 0
    connected: int = 0
    idle: int = 0
    busy: int = 0


class TaskQueueSummary(PydanticBase):
    """Work Request queue length."""

    pending: int = 0
    running: int = 0


class QueueStatus(PydanticBase):
    """Worker queue lengths and pool sizes."""

    tasks: dict[TaskTypes, TaskQueueSummary] = {}

    # by worker type
    workers: dict[WorkerType, WorkerSummary] = {}
    worker_tasks: dict[WorkerType, TaskQueueSummary] = {}

    # by architecture
    external_workers_arch: dict[str, WorkerSummary] = {}
    external_workers_host_arch: dict[str, WorkerSummary] = {}
    worker_tasks_arch: dict[str | None, TaskQueueSummary] = {}


class WorkerStatus:
    """Calculate the size of queues and the worker pool."""

    @classmethod
    def get_status(cls) -> QueueStatus:
        """Count number of tasks and workers by type and architecture."""
        result = QueueStatus()

        result.tasks = cls._count_queued_tasks_by_type()
        result.workers = cls._count_workers_by_type()
        result.external_workers_arch = (
            cls._count_external_workers_by_architecture()
        )
        result.external_workers_host_arch = (
            cls._count_external_workers_by_host_architecture()
        )
        result.worker_tasks = cls._count_task_by_worker_type(result.tasks)

        # Add architectures with queued work requests
        for (
            arch,
            queued_work_requests,
        ) in cls._count_queued_worker_work_requests_by_architecture().items():
            if arch and arch not in result.external_workers_arch:
                result.external_workers_arch[arch] = WorkerSummary()
            result.worker_tasks_arch[arch] = queued_work_requests

        # Add architectures with online workers, and no queued work requests
        # (those with queued work requests were already added)
        for arch in result.external_workers_arch.keys():
            if arch not in result.worker_tasks_arch:
                result.worker_tasks_arch[arch] = TaskQueueSummary()

        return result

    @classmethod
    def _count_queued_tasks_by_type(cls) -> dict[TaskTypes, TaskQueueSummary]:
        """Return summary of queued tasks by task type."""
        result: dict[TaskTypes, TaskQueueSummary] = {
            task_type: TaskQueueSummary() for task_type in TaskTypes
        }

        for row in (
            WorkRequest.objects.pending()
            .values("task_type")
            .annotate(count=Count("id"))
        ):
            result[row["task_type"]].pending = row["count"]

        for row in (
            WorkRequest.objects.running()
            .values("task_type")
            .annotate(count=Count("id"))
        ):
            result[row["task_type"]].running = row["count"]

        return result

    @staticmethod
    def _count_task_by_worker_type(
        tasks: dict[TaskTypes, TaskQueueSummary],
    ) -> dict[WorkerType, TaskQueueSummary]:
        """Re-count queued tasks by worker type."""
        result: dict[WorkerType, TaskQueueSummary] = {
            worker_type: TaskQueueSummary() for worker_type in WorkerType
        }
        for task_type, worker_type in (
            (TaskTypes.WORKER, WorkerType.EXTERNAL),
            (TaskTypes.SERVER, WorkerType.CELERY),
            (TaskTypes.SIGNING, WorkerType.SIGNING),
        ):
            for field_name in TaskQueueSummary.__fields__.keys():
                setattr(
                    result[worker_type],
                    field_name,
                    getattr(result[worker_type], field_name)
                    + getattr(tasks[task_type], field_name),
                )
        return result

    @classmethod
    def _count_queued_worker_work_requests_by_architecture(
        cls,
    ) -> dict[str | None, TaskQueueSummary]:
        """
        Return summary of queued WORKER work requests by architecture.

        Categorize all WORKER tasks that don't have a specified archiceture as
        None.
        """
        pending_work_requests = (
            WorkRequest.objects.pending()
            .filter(
                task_data__host_architecture__isnull=False,
                task_type=TaskTypes.WORKER,
            )
            .values("task_data__host_architecture")
            .annotate(count=Count("id"))
            .order_by()
        )

        result: defaultdict[str | None, TaskQueueSummary] = defaultdict(
            TaskQueueSummary
        )

        # Add architectures with pending work requests if their
        # "task_data" contains "host_architecture"

        # TODO: count work requests by architecture using WorkRequest tags
        # (when tags are implemented in Debusine) instead of using
        # task_data__host_architecture or instantiating Tasks and calling
        # host_architecture() method

        for pending_host_architecture in pending_work_requests:
            result[
                pending_host_architecture["task_data__host_architecture"]
            ].pending = pending_host_architecture["count"]

        # Add architectures with pending work requests if their
        # "task_data" does not contain "host_architecture".
        pending_work_requests_no_host_architecture = (
            WorkRequest.objects.pending()
            .filter(
                task_data__host_architecture__isnull=True,
                task_type=TaskTypes.WORKER,
            )
            .order_by()
        )

        for work_request in pending_work_requests_no_host_architecture:
            host_architecture = cls._task_host_architecture(work_request)
            result[host_architecture].pending += 1

        for work_request in WorkRequest.objects.running().filter(
            task_type=TaskTypes.WORKER
        ):
            host_architecture = cls._task_host_architecture(work_request)
            result[host_architecture].running += 1

        return dict(result)

    @staticmethod
    def _task_host_architecture(work_request: WorkRequest) -> str | None:
        """Determine the host_architecture of work_request."""
        if "host_architecture" in work_request.task_data:
            host_architecture = work_request.task_data["host_architecture"]
            assert isinstance(host_architecture, str)
            return host_architecture
        try:
            task = work_request.get_task()
        except TaskConfigError:
            # When the scheduler picks this work request: will mark it
            # as aborted
            return None
        return task.host_architecture()

    @staticmethod
    def _count_workers_by_type() -> dict[WorkerType, WorkerSummary]:
        """Return number of connected external workers by type."""
        result: dict[WorkerType, WorkerSummary] = {
            worker_type: WorkerSummary() for worker_type in WorkerType
        }

        for worker in Worker.objects.filter(token__enabled=True):
            worker_type = WorkerType[worker.worker_type.upper()]
            result[worker_type].registered += 1
            if worker.connected():
                result[worker_type].connected += 1
                if worker.is_busy():
                    result[worker_type].busy += 1
                else:
                    result[worker_type].idle += 1

        return result

    @staticmethod
    def _count_external_workers_by_architecture() -> dict[str, WorkerSummary]:
        """
        Return number of connected external workers by architecture.

        The same worker might be able to execute tasks for multiple
        architectures.
        """
        result: defaultdict[str, WorkerSummary] = defaultdict(WorkerSummary)

        for worker in Worker.objects.filter(
            token__enabled=True, worker_type=WorkerType.EXTERNAL
        ):
            for arch in worker.metadata().get("system:architectures", []):
                result[arch].registered += 1
                if worker.connected():
                    result[arch].connected += 1
                    if worker.is_busy():
                        result[arch].busy += 1
                    else:
                        result[arch].idle += 1

        return dict(result)

    @staticmethod
    def _count_external_workers_by_host_architecture() -> (
        dict[str, WorkerSummary]
    ):
        """
        Return number of connected external workers by system:host_architecture.

        The same worker might be able to execute tasks for multiple
        architectures, but this only returns the host's architecture.
        """
        result: defaultdict[str, WorkerSummary] = defaultdict(WorkerSummary)

        for worker in Worker.objects.filter(
            token__enabled=True,
            worker_type=WorkerType.EXTERNAL,
        ):
            arch = worker.metadata().get("system:host_architecture")
            if not arch:
                log.warning(
                    "Worker %s missing system:host_architecture", worker.name
                )
                continue
            result[arch].registered += 1
            if worker.connected():
                result[arch].connected += 1
                if worker.is_busy():
                    result[arch].busy += 1
                else:
                    result[arch].idle += 1

        return dict(result)
