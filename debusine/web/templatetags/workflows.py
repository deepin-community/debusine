# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Template tag library for workflows."""
from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest
from debusine.db.models.work_requests import workflow_flattened

register = template.Library()


@register.simple_tag
def workflow_runtime_status_small(workflow: WorkRequest) -> str:
    """
    Render workflow runtime status component.

    It displays a badge with one letter for the workflow.status. If the
    status is RUNNING, it displays a second batch with
    workflow.workflow_runtime_status.
    """
    statuses_formatting: dict[WorkRequest.Statuses, tuple[str, str]] = {
        WorkRequest.Statuses.PENDING: ("secondary", "P"),
        WorkRequest.Statuses.RUNNING: ("secondary", "R"),
        WorkRequest.Statuses.COMPLETED: ("primary", "C"),
        WorkRequest.Statuses.ABORTED: ("dark", "A"),
        WorkRequest.Statuses.BLOCKED: ("dark", "B"),
    }

    runtime_status_formatting: dict[
        WorkRequest.RuntimeStatuses, tuple[str, str]
    ] = {
        WorkRequest.RuntimeStatuses.NEEDS_INPUT: (
            "secondary",
            "I",
        ),
        WorkRequest.RuntimeStatuses.RUNNING: ("secondary", "R"),
        WorkRequest.RuntimeStatuses.WAITING: ("secondary", "W"),
        WorkRequest.RuntimeStatuses.PENDING: ("secondary", "P"),
        WorkRequest.RuntimeStatuses.ABORTED: ("dark", "A"),
        WorkRequest.RuntimeStatuses.COMPLETED: ("primary", "C"),
        WorkRequest.RuntimeStatuses.BLOCKED: ("dark", "B"),
    }

    if (
        workflow.status == WorkRequest.Statuses.RUNNING
        and workflow.workflow_runtime_status is not None
    ):
        # Return badge with the workflow.workflow_runtime_status only
        workflow_runtime_status = WorkRequest.RuntimeStatuses(
            workflow.workflow_runtime_status
        )

        runtime_status_color, runtime_status_text = runtime_status_formatting[
            workflow_runtime_status
        ]
        runtime_status_title = workflow_runtime_status.label

        runtime_status_html = format_html(
            '<span title="{runtime_status_title}" '
            'class="badge text-bg-{runtime_status_color}">'
            '{runtime_status_text}</span>',
            runtime_status_title=runtime_status_title,
            runtime_status_color=runtime_status_color,
            runtime_status_text=runtime_status_text,
        )

        if workflow_runtime_status == WorkRequest.RuntimeStatuses.NEEDS_INPUT:
            work_request_needs_input = _first_workrequest_needs_input(workflow)
            if work_request_needs_input is not None:
                runtime_status_html = format_html(
                    '<a href="{url}">{runtime_status_html}</a>',
                    url=work_request_needs_input.get_absolute_url(),
                    runtime_status_html=runtime_status_html,
                )

        runtime_status_html = SafeString(" " + runtime_status_html)

    else:
        runtime_status_html = SafeString("")

    status = WorkRequest.Statuses(workflow.status)

    status_color, status_text = statuses_formatting[status]

    status_title = status.label

    html = format_html(
        '<span title="{status_title}" class="badge text-bg-{status_color}">'
        '{status_text}</span>'
        '{runtime_status_html}',
        status_title=status_title,
        status_color=status_color,
        status_text=status_text,
        runtime_status_html=runtime_status_html,
    )

    return html


def _first_workrequest_needs_input(workflow: WorkRequest) -> WorkRequest | None:
    assert workflow.task_type == TaskTypes.WORKFLOW

    for work_request in sorted(
        workflow_flattened(workflow),
        key=lambda workrequest: workrequest.created_at,
    ):
        if (
            work_request.task_type == TaskTypes.WAIT
            and work_request.status == WorkRequest.Statuses.RUNNING
            and work_request.workflow_data_json.get("needs_input")
        ):
            return work_request

    return None
