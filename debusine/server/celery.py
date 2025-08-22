# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Celery integration for debusine tasks."""

import logging
from contextlib import ExitStack

from celery import shared_task
from django.db import transaction

from debusine.artifacts.models import TaskTypes
from debusine.db.context import context
from debusine.db.models import WorkRequest
from debusine.db.models.work_requests import (
    compute_workflow_last_activity,
    compute_workflow_runtime_status,
    workflow_ancestors,
)
from debusine.server.tasks import BaseServerTask
from debusine.tasks import TaskConfigError
from debusine.tasks.models import OutputData, OutputDataError

logger = logging.getLogger(__name__)


class WorkRequestNotPending(Exception):
    """We only run pending work requests."""


class ServerTaskRunError(Exception):
    """Running a server task failed."""

    def __init__(self, message: str, code: str) -> None:
        """Construct the exception."""
        self.message = message
        self.code = code


def _run_server_task_or_error(work_request: WorkRequest) -> bool:
    """Run a server task, raising :py:class:`ServerTaskRunError` on errors."""
    if work_request.task_type != TaskTypes.SERVER:
        raise ServerTaskRunError(
            "Cannot run on a Celery worker", "wrong-task-type"
        )

    task_name = work_request.task_name
    try:
        task = work_request.get_task()
    except ValueError as exc:
        raise ServerTaskRunError(str(exc), "setup-failed") from exc
    except TaskConfigError as exc:
        raise ServerTaskRunError(
            f"Failed to configure: {exc}", "configure-failed"
        ) from exc
    assert isinstance(task, BaseServerTask)

    task.set_work_request(work_request)

    try:
        with ExitStack() as stack:
            if not task.TASK_MANAGES_TRANSACTIONS:
                stack.enter_context(transaction.atomic())
            result = task.execute_logging_exceptions()
    except Exception as exc:
        raise ServerTaskRunError(
            f"Execution failed: {exc}", "execute-failed"
        ) from exc
    else:
        if task.aborted:
            logger.info("Task: %s has been aborted", task_name)
            # No need to update DB state
            return False
        else:
            work_request.mark_completed(
                WorkRequest.Results.SUCCESS
                if result
                else WorkRequest.Results.FAILURE
            )
            return result


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task  # type: ignore[misc]
def run_server_task(work_request_id: int) -> bool:
    """Run a :py:class:`BaseTask` via Celery."""
    try:
        work_request = WorkRequest.objects.get(pk=work_request_id)
    except WorkRequest.DoesNotExist:
        logger.error("Work request %d does not exist", work_request_id)
        raise

    if work_request.status != WorkRequest.Statuses.PENDING:
        logger.error(
            "Work request %d is in status %s, not pending",
            work_request_id,
            work_request.status,
        )
        raise WorkRequestNotPending

    work_request.mark_running()

    context.reset()
    work_request.set_current()

    try:
        return _run_server_task_or_error(work_request)
    except ServerTaskRunError as exc:
        logger.error(
            "Error running work request %s/%s (%s): %s",
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
        raise


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task  # type: ignore[misc]
@transaction.atomic
def update_workflows() -> None:
    """
    Update expensive properties of workflows.

    The ``workflow_last_activity_at`` and ``workflow_runtime_status`` fields
    of workflows are used in the web UI and need to be kept up to date.
    """
    need_update = WorkRequest.objects.filter(
        workflows_need_update=True
    ).select_for_update()
    workflow_ancestor_ids = workflow_ancestors(need_update).values_list(
        "id", flat=True
    )
    for workflow in WorkRequest.objects.filter(
        id__in=workflow_ancestor_ids
    ).select_for_update():
        assert workflow.task_type == TaskTypes.WORKFLOW
        workflow.workflow_last_activity_at = compute_workflow_last_activity(
            workflow
        )
        workflow.workflow_runtime_status = compute_workflow_runtime_status(
            workflow
        )
        workflow.save()
    need_update.update(workflows_need_update=False)
