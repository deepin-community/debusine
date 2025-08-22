# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for workflows."""

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest
from debusine.server.workflows.models import WorkRequestWorkflowData
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData
from debusine.web.templatetags.workflows import workflow_runtime_status_small

# templatetags loading code will attempt to import tests, debusine.test
# dependencies (such as lxml) may not be available at runtime
try:
    from debusine.test.django import TestCase
except ImportError:
    from unittest import TestCase  # type: ignore[assignment]


class WorkflowRuntimeStatusSmallTests(TestCase):
    """Tests for workflow_runtime_status_small tag."""

    def setUp(self) -> None:
        """Set up test."""
        super().setUp()
        self.workflow = self.playground.create_workflow()

    def test_status_is_completed(self) -> None:
        """Test workflow status is completed."""
        self.workflow.status = WorkRequest.Statuses.COMPLETED
        self.workflow.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.COMPLETED
        )

        actual = workflow_runtime_status_small(self.workflow)

        self.assertHTMLEqual(
            actual,
            '<span class="badge text-bg-primary" title="Completed">C</span>',
        )

    def test_status_is_running(self) -> None:
        """Test workflow status is running and runtime status is blocked."""
        self.workflow.status = WorkRequest.Statuses.RUNNING
        self.workflow.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.BLOCKED
        )

        actual = workflow_runtime_status_small(self.workflow)

        self.assertHTMLEqual(
            actual,
            '<span class="badge text-bg-secondary" title="Running">R</span>'
            '<span class="badge text-bg-dark" title="Blocked">B</span>',
        )

    def test_runtime_status_is_needs_input(self) -> None:
        """Test workflow runtime status is needs input."""
        # Import this late, since its dependencies (such as lxml) may not be
        # available when Django tries to import everything under
        # templatetags.
        from debusine.server.tasks.tests.helpers import SampleBaseWaitTask

        self.workflow.status = WorkRequest.Statuses.RUNNING
        self.workflow.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.NEEDS_INPUT
        )

        class WaitNoop(
            SampleBaseWaitTask[BaseTaskData, BaseDynamicTaskData],
        ):
            TASK_VERSION = 1

            def _execute(self) -> bool:
                raise NotImplementedError()

        self.workflow.create_child(
            "noop",
        )

        children_needs_input = self.workflow.create_child(
            "waitnoop",
            task_type=TaskTypes.WAIT,
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )

        self.workflow.create_child(
            "waitnoop",
            task_type=TaskTypes.WAIT,
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )

        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        actual = workflow_runtime_status_small(self.workflow)

        self.assertHTMLEqual(
            actual,
            '<span title="Running" class="badge text-bg-secondary">R</span> '
            f'<a href="{children_needs_input.get_absolute_url()}">'
            '<span title="Needs Input"class="badge text-bg-secondary">I</span>'
            '</a>',
        )

    def test_runtime_status_is_needs_input_no_work_request(self) -> None:
        """
        Test workflow runtime status is needs input but no work request waiting.

        The workflow was NEEDS_INPUT but no work request is waiting (finished
        before getting the URL).
        """
        self.workflow.status = WorkRequest.Statuses.RUNNING
        self.workflow.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.NEEDS_INPUT
        )

        actual = workflow_runtime_status_small(self.workflow)

        self.assertHTMLEqual(
            actual,
            '<span title="Running" class="badge text-bg-secondary">R</span> '
            '<span title="Needs Input" class="badge text-bg-secondary">I'
            '</span>',
        )
