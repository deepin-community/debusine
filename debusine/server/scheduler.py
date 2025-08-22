# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Schedule WorkRequests to Workers."""

import logging
from typing import Any
from uuid import uuid4

import django.db
from celery import shared_task, states
from django.conf import settings
from django.db import connection, transaction
from django.db.models import (
    DateTimeField,
    Exists,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django_celery_results.models import TaskResult

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest, Worker
from debusine.db.models.work_requests import SkipWorkRequest
from debusine.server.celery import run_server_task
from debusine.server.workflows.celery import run_workflow_task
from debusine.tasks import TaskConfigError
from debusine.tasks.models import OutputData, OutputDataError

logger = logging.getLogger(__name__)


class AssignToWorkerError(Exception):
    """Assigning a work request to a worker failed."""

    def __init__(self, message: str, code: str) -> None:
        """Construct the exception."""
        self.message = message
        self.code = code


def _possible_to_schedule_for_worker(worker: Worker) -> bool:
    if worker.is_at_capacity():
        # Worker already has enough work requests assigned; no scheduling
        # needed
        return False

    try:
        Worker.objects.select_for_update(nowait=True).get(id=worker.id)
    except django.db.DatabaseError:
        logger.debug("[SCHEDULER] Failed to lock Worker %s", worker)
        return False

    return True


@receiver(post_save, sender=Worker, dispatch_uid="ScheduleForWorker")
def _worker_changed(
    sender: type[Worker], instance: Worker, **kwargs: Any  # noqa: U100
) -> None:
    if not getattr(settings, "DISABLE_AUTOMATIC_SCHEDULING", False):
        schedule_for_worker(instance)


def _assign_work_request_to_worker(
    work_request: WorkRequest, worker: Worker
) -> WorkRequest | None:
    """
    Attempt to assign a particular work request to the worker.

    :param work_request: the work request to try to assign.
    :param worker: the worker that needs a new work request to be
      assigned.
    :return: The assigned work request, or None if it could not be assigned.
    :raises AssignToWorkerError: if the work request is broken.
    """
    try:
        task = work_request.get_task(worker=worker)
    except TaskConfigError as exc:
        raise AssignToWorkerError(
            f"Failed to configure: {exc}", "configure-failed"
        ) from exc

    if not task.can_run_on(worker.metadata()):
        return None

    try:
        work_request = WorkRequest.objects.select_for_update(nowait=True).get(
            id=work_request.id
        )
    except django.db.DatabaseError:
        logger.debug(
            "[SCHEDULER] Failed to lock WorkRequest %s",
            work_request,
        )
        return None

    if work_request.worker:  # pragma: no cover
        # work_request did not have a worker assigned on the
        # initial qs but has one now - nothing to do
        return None

    with work_request.scheduling_disabled():
        try:
            work_request.assign_worker(worker)
        except SkipWorkRequest:
            logger.debug(
                "Skipping work request %s due to on_assignment action",
                work_request,
            )
            return None
        except Exception as exc:
            raise AssignToWorkerError(
                f"Failed to compute dynamic data: {exc}", "dynamic-data-failed"
            ) from exc

    if task.TASK_TYPE is TaskTypes.SERVER:
        # Use a slightly more informative task ID than the default.
        task_id = f"{work_request.task_name}_{work_request.id}_{uuid4()}"
        transaction.on_commit(
            lambda: run_server_task.apply_async(
                args=(work_request.id,), task_id=task_id
            )
        )

    return work_request


@transaction.atomic
def schedule_for_worker(worker: Worker) -> WorkRequest | None:
    """
    Schedule a new work request for the worker.

    :param worker: the worker that needs a new work request to be
      assigned.
    :return: The assigned work request. None if no suitable work request could
      be found.
    :rtype: WorkRequest | None.
    """
    if not _possible_to_schedule_for_worker(worker):
        return None

    worker_metadata = worker.metadata()
    tasks_allowlist = worker_metadata.get("tasks_allowlist", None)
    tasks_denylist = worker_metadata.get("tasks_denylist", [])

    # TODO: Optimization: pre-filter on task type as appropriate.
    qs = WorkRequest.objects.pending(exclude_assigned=True).filter(
        task_type__in=(
            TaskTypes.WORKER,
            TaskTypes.SERVER,
            TaskTypes.SIGNING,
        )
    )
    if tasks_allowlist is not None:
        qs = qs.filter(task_name__in=tasks_allowlist)
    elif tasks_denylist:
        qs = qs.exclude(task_name__in=tasks_denylist)

    for work_request in qs:
        try:
            assigned_work_request = _assign_work_request_to_worker(
                work_request, worker
            )
        except AssignToWorkerError as exc:
            logger.error(
                "Error assigning work request %s/%s (%s): %s",
                work_request.task_type,
                work_request.task_name,
                work_request.id,
                exc.message,
            )
            work_request.mark_completed(
                WorkRequest.Results.ERROR,
                output_data=OutputData(
                    errors=[OutputDataError(message=exc.message, code=exc.code)]
                ),
            )
        else:
            if assigned_work_request is not None:
                return assigned_work_request

    return None


def run_workflow_task_via_celery(work_request: WorkRequest) -> None:
    """Run a workflow callback or workflow via Celery."""
    # Use a slightly more informative task ID than the default.
    task_id = (
        f"{work_request.task_type.lower()}_{work_request.task_name}_"
        f"{work_request.id}_{uuid4()}"
    )
    transaction.on_commit(
        lambda: run_workflow_task.apply_async(
            args=(work_request.id,), task_id=task_id
        )
    )


@transaction.atomic
def schedule_internal() -> list[WorkRequest]:
    """
    Schedule internal work requests.

    These don't require a worker, but need some kind of action from the
    scheduler.
    """
    in_progress_task_results = TaskResult.objects.filter(
        status__in={
            states.PENDING,
            states.RECEIVED,
            states.STARTED,
            states.RETRY,
        },
        task_name=("debusine.server.workflows.celery.run_workflow_task"),
    ).annotate(
        task_args_array=Cast("task_args", JSONField()),
        first_task_arg=Cast("task_args_array__0", IntegerField()),
    )
    qs = (
        WorkRequest.objects.with_workflow_root_id()
        .pending()
        .filter(
            Q(
                task_type=TaskTypes.INTERNAL,
                task_name__in={"synchronization_point", "workflow"},
            )
            | Q(task_type__in={TaskTypes.WORKFLOW, TaskTypes.WAIT})
        )
        # Only one workflow callback for a given workflow may run at once.
        .exclude(
            Q(task_type=TaskTypes.INTERNAL, task_name="workflow")
            & Exists(
                in_progress_task_results.filter(
                    first_task_arg__in=WorkRequest.objects.filter(
                        parent=OuterRef(OuterRef("parent"))
                    ),
                )
            )
        )
        # Only one sub-workflow within a given root workflow may run at once.
        .exclude(
            Q(task_type=TaskTypes.WORKFLOW)
            & Exists(
                in_progress_task_results.filter(
                    first_task_arg=OuterRef("workflow_root_id")
                )
            )
        )
        .order_by("id")
    )

    running_workflow_callback_parents: set[WorkRequest] = set()
    workflow_root_ids: set[int] = set()
    result: list[WorkRequest] = []
    for work_request in qs:
        match (work_request.task_type, work_request.task_name):
            case (TaskTypes.INTERNAL, "synchronization_point"):
                # Synchronization points do nothing; when pending, they are
                # immediately marked as completed, thus unblocking work
                # requests that depend on them.
                work_request.mark_completed(WorkRequest.Results.SUCCESS)
                result.append(work_request)

            case (TaskTypes.INTERNAL, "workflow"):
                # Only one workflow callback for a given workflow may run at
                # once.  (This duplicates the .exclude() above, because the
                # set of work requests that we need to skip may grow as we
                # schedule more workflow callbacks.)
                assert work_request.parent is not None
                if work_request.parent in running_workflow_callback_parents:
                    continue
                running_workflow_callback_parents.add(work_request.parent)

                # Workflow callbacks are handled by the corresponding
                # workflow orchestrator.  This may take a while, so we
                # delegate it to a Celery task.
                work_request.mark_running()
                run_workflow_task_via_celery(work_request)
                result.append(work_request)

            case (TaskTypes.WORKFLOW, _):
                # Workflow population always happens via the orchestrator
                # for the root workflow.  Sub-workflow orchestrators are not
                # called directly from the scheduler, although if a
                # sub-workflow is pending then its root workflow may deal
                # with populating it.
                # Workflows always have a root.
                assert work_request.workflow_root_id is not None
                workflow_root_ids.add(work_request.workflow_root_id)

            case (TaskTypes.WAIT, _):
                # Wait tasks are marked running immediately, but otherwise
                # we do nothing; some later event will mark them as
                # completed.
                work_request.mark_running()
                result.append(work_request)

            case _ as unreachable:
                raise AssertionError(
                    f"Unexpected internal task name: {unreachable}"
                )

    for workflow_root in (
        WorkRequest.objects.filter(id__in=workflow_root_ids)
        .order_by("id")
        .select_for_update()
    ):
        # Workflows are populated by the corresponding workflow
        # orchestrator.  This may take a while, so we delegate it to a
        # Celery task.
        workflow_root.mark_running()
        run_workflow_task_via_celery(workflow_root)
        result.append(workflow_root)

    return result


@transaction.atomic
def complete_delay_tasks() -> None:
    """Mark running delay tasks as completed once their delay has expired."""
    for work_request in (
        WorkRequest.objects.running()
        .filter(task_type=TaskTypes.WAIT, task_name="delay")
        .annotate(
            delay_until=Cast(
                KT("task_data__delay_until"),
                DateTimeField(blank=True, null=True),
            )
        )
        .exclude(delay_until__gt=timezone.now())
    ):
        work_request.mark_completed(WorkRequest.Results.SUCCESS)


@receiver(post_save, sender=WorkRequest, dispatch_uid="ScheduleForWorkRequest")
def _work_request_changed(
    sender: type[WorkRequest],  # noqa: U100
    instance: WorkRequest,
    **kwargs: Any,
) -> None:
    if not getattr(
        settings, "DISABLE_AUTOMATIC_SCHEDULING", False
    ) and not hasattr(instance, "_disable_signals"):
        # Run the scheduler on commit, but only once per savepoint.  This
        # uses undocumented Django internals, but if it goes wrong without
        # an obvious unit test failure, then the worst case should be some
        # extra calls to the scheduler, which is relatively harmless.
        if (set(connection.savepoint_ids), schedule_task.delay) not in [
            hook[:2] for hook in connection.run_on_commit
        ]:
            transaction.on_commit(schedule_task.delay)


def schedule() -> list[WorkRequest]:
    """
    Try to assign work requests to free workers.

    The function loops over workers that have no work request assigned
    and tries to assign them new work requests.

    :return: the list of work requests that got assigned.
    """
    available_workers = Worker.objects.waiting_for_work_request()

    result = list(schedule_internal())
    for available_worker in available_workers:
        work_request = schedule_for_worker(available_worker)
        if work_request:
            result.append(work_request)

    complete_delay_tasks()

    return result


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task()  # type: ignore[misc]
def schedule_task() -> None:
    """Run the scheduler as a celery task."""
    schedule()
