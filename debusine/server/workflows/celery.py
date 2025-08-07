# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Celery integration for debusine workflow orchestrators."""

import logging

from celery import shared_task
from django.db import transaction

from debusine.db.models import WorkRequest
from debusine.server.workflows.base import (
    WorkflowRunError,
    orchestrate_workflow,
)
from debusine.tasks.models import OutputData, OutputDataError, TaskTypes

logger = logging.getLogger(__name__)


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task  # type: ignore[misc]
@transaction.atomic
def run_workflow_task(work_request_id: int) -> bool:
    """Run a workflow callback or workflow in Celery."""
    try:
        work_request = WorkRequest.objects.get(pk=work_request_id)
    except WorkRequest.DoesNotExist:
        logger.error("Work request %d does not exist", work_request_id)
        raise

    if (
        work_request.task_type == TaskTypes.WORKFLOW
        and not work_request.is_workflow_root
    ):
        # This task must only be called to populate root workflows.
        # Sub-workflows are populated by their parent workflows instead.
        message = "Must be populated by its parent workflow instead"
        logger.error(
            "Error running work request %s/%s (%s): %s",
            work_request.task_type,
            work_request.task_name,
            work_request.id,
            message,
        )
        work_request.mark_completed(
            WorkRequest.Results.ERROR,
            output_data=OutputData(
                errors=[
                    OutputDataError(message=message, code="non-root-workflow")
                ]
            ),
        )
        return False

    try:
        orchestrate_workflow(work_request)
    except WorkflowRunError:
        # The details have been recorded in the work request's output data,
        # so we don't need to fail the Celery task.
        return False
    else:
        return True
