# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for base workflow code."""

import logging
from unittest import mock

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest
from debusine.server.workflows.base import (
    WorkflowRunError,
    orchestrate_workflow,
)
from debusine.server.workflows.models import BaseWorkflowData
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks.models import BaseDynamicTaskData
from debusine.tasks.server import TaskDatabaseInterface
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class OrchestrateWorkflowTests(TestCase):
    """Test orchestrate_workflow()."""

    def test_workflow_callback(self) -> None:
        """A workflow callback is run and marked as completed."""
        parent = self.playground.create_workflow(task_name="noop")
        parent.mark_running()
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )

        with mock.patch(
            "debusine.server.workflows.noop.NoopWorkflow.callback_test",
            create=True,
        ) as mock_noop_callback_test:
            orchestrate_workflow(wr)

        mock_noop_callback_test.assert_called_once_with()
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)

    def test_workflow_callback_step_not_implemented(self) -> None:
        """A workflow callback must have a matching ``callback_*`` method."""
        parent = self.playground.create_workflow(task_name="noop")
        parent.mark_running()
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.callback_other",
                create=True,
            ) as mock_noop_callback_other,
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_callback_other.assert_not_called()
        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(
            raised.exception.message,
            "Orchestrator failed: Unhandled workflow callback step: test",
        )
        self.assertEqual(raised.exception.code, "orchestrator-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    @preserve_task_registry()
    def test_workflow_callback_computes_dynamic_data(self) -> None:
        """Dynamic task data is computed for workflow callbacks."""

        class ExampleDynamicData(BaseDynamicTaskData):
            dynamic: str

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, ExampleDynamicData]
        ):
            TASK_NAME = "example"

            def compute_dynamic_data(
                self, task_database: TaskDatabaseInterface  # noqa: U100
            ) -> ExampleDynamicData:
                return ExampleDynamicData(dynamic="foo")

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        parent = self.playground.create_workflow(task_name="example")
        parent.mark_running()
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )

        with mock.patch.object(ExampleWorkflow, "callback") as mock_callback:
            orchestrate_workflow(wr)

        mock_callback.assert_called_once_with(wr)
        parent.refresh_from_db()
        self.assertEqual(parent.dynamic_task_data, {"dynamic": "foo"})
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)

    @preserve_task_registry()
    def test_workflow_callback_computes_dynamic_data_only_once(self) -> None:
        """If a workflow already has dynamic data, it is left alone."""

        class ExampleDynamicData(BaseDynamicTaskData):
            dynamic: str

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, ExampleDynamicData]
        ):
            TASK_NAME = "example"

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        WorkRequest.objects.all().delete()
        parent = self.playground.create_workflow(task_name="example")
        parent.dynamic_task_data = {"dynamic": "foo"}
        parent.save()
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )

        with (
            mock.patch.object(
                ExampleWorkflow, "compute_dynamic_data"
            ) as mock_compute_dynamic_data,
            mock.patch.object(ExampleWorkflow, "callback") as mock_callback,
        ):
            orchestrate_workflow(wr)

        mock_compute_dynamic_data.assert_not_called()
        mock_callback.assert_called_once_with(wr)
        parent.refresh_from_db()
        self.assertEqual(parent.dynamic_task_data, {"dynamic": "foo"})
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)

    def test_workflow_callback_outside_workflow(self) -> None:
        """A workflow callback outside a workflow is skipped."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.INTERNAL, task_name="workflow"
        )
        expected_message = "Workflow callback is not contained in a workflow"

        with (
            self.assertLogsContains(
                f"Error running work request Internal/workflow ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(raised.exception.message, expected_message)
        self.assertEqual(raised.exception.code, "configure-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflow_callback_bad_task_data(self) -> None:
        """A workflow callback with bad task data is skipped."""
        parent = self.playground.create_workflow(
            task_name="noop", task_data={"nonsense": ""}, validate=False
        )
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )
        expected_message = "Failed to configure: 1 validation error"

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.callback"
            ) as mock_noop_callback,
            self.assertLogsContains(
                f"Error running work request Internal/workflow ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_callback.assert_not_called()
        self.assertEqual(raised.exception.work_request, wr)
        self.assertTrue(raised.exception.message.startswith(expected_message))
        self.assertEqual(raised.exception.code, "configure-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflow_callback_with_failing_compute_dynamic_data(self) -> None:
        """A workflow callback where computing dynamic data fails is skipped."""
        parent = self.playground.create_workflow(task_name="noop")
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )
        expected_message = (
            f"Failed to compute dynamic data for Workflow/noop ({parent.id}): "
            f"Boom"
        )

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow"
                ".compute_dynamic_data",
                side_effect=Exception("Boom"),
            ),
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.callback"
            ) as mock_noop_callback,
            self.assertLogsContains(
                f"Error running work request Internal/workflow ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_callback.assert_not_called()
        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(raised.exception.message, expected_message)
        self.assertEqual(raised.exception.code, "dynamic-data-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflow_callback_fails(self) -> None:
        """A workflow callback that fails is logged."""
        parent = self.playground.create_workflow(task_name="noop")
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )
        expected_message = "Orchestrator failed: Boom"

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.callback",
                side_effect=ValueError("Boom"),
            ) as mock_noop_callback,
            self.assertLogsContains(
                f"Error running work request Internal/workflow ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_callback.assert_called_once_with(wr)
        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(raised.exception.message, expected_message)
        self.assertEqual(raised.exception.code, "orchestrator-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflow(self) -> None:
        """A workflow is populated and left running."""
        wr = self.playground.create_workflow(task_name="noop")
        wr.mark_running()

        def populate() -> None:
            wr.create_child("noop")

        with mock.patch(
            "debusine.server.workflows.noop.NoopWorkflow.populate",
            side_effect=populate,
        ) as mock_noop_populate:
            orchestrate_workflow(wr)

        mock_noop_populate.assert_called_once_with()
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)

    def test_workflow_empty(self) -> None:
        """An empty workflow is populated and marked as completed."""
        wr = self.playground.create_workflow(task_name="noop")
        wr.mark_running()

        with mock.patch(
            "debusine.server.workflows.noop.NoopWorkflow.populate"
        ) as mock_noop_populate:
            orchestrate_workflow(wr)

        mock_noop_populate.assert_called_once_with()
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)

    @preserve_task_registry()
    def test_workflow_computes_dynamic_data(self) -> None:
        """Dynamic task data is computed for workflows."""

        class ExampleDynamicData(BaseDynamicTaskData):
            dynamic: str

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, ExampleDynamicData]
        ):
            TASK_NAME = "example"

            def compute_dynamic_data(
                self, task_database: TaskDatabaseInterface  # noqa: U100
            ) -> ExampleDynamicData:
                return ExampleDynamicData(dynamic="foo")

            def populate(self) -> None:
                self.work_request.create_child("noop")

        wr = self.playground.create_workflow(task_name="example")
        wr.mark_running()

        orchestrate_workflow(wr)

        wr.refresh_from_db()
        self.assertEqual(wr.dynamic_task_data, {"dynamic": "foo"})
        self.assertEqual(wr.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)

    def test_workflow_bad_task_data(self) -> None:
        """A workflow with bad task data is skipped."""
        # Bad task data would normally be caught by create_workflow.  Force
        # it to happen here by changing the task data after initial
        # creation.  (A more realistic case might be one where the
        # definition of a workflow changes but existing data isn't
        # migrated.)
        wr = self.playground.create_workflow(task_name="noop")
        wr.task_data = {"nonsense": ""}
        wr.save()
        wr.mark_running()
        expected_message = "Failed to configure: 1 validation error"

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.populate"
            ) as mock_noop_populate,
            self.assertLogsContains(
                f"Error running work request Workflow/noop ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_populate.assert_not_called()
        self.assertEqual(raised.exception.work_request, wr)
        self.assertTrue(raised.exception.message.startswith(expected_message))
        self.assertEqual(raised.exception.code, "configure-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflows_with_failing_compute_dynamic_data(self) -> None:
        """A workflow where computing dynamic data fails is skipped."""
        wr = self.playground.create_workflow(task_name="noop")
        wr.mark_running()
        expected_message = "Failed to compute dynamic data: Boom"

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow"
                ".compute_dynamic_data",
                side_effect=Exception("Boom"),
            ),
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.populate"
            ) as mock_noop_populate,
            self.assertLogsContains(
                f"Error running work request Workflow/noop ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_populate.assert_not_called()
        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(raised.exception.message, expected_message)
        self.assertEqual(raised.exception.code, "dynamic-data-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflow_fails(self) -> None:
        """A workflow that fails is logged."""
        wr = self.playground.create_workflow(task_name="noop")
        wr.mark_running()
        expected_message = "Orchestrator failed: Boom"

        with (
            mock.patch(
                "debusine.server.workflows.noop.NoopWorkflow.populate",
                side_effect=ValueError("Boom"),
            ) as mock_noop_populate,
            self.assertLogsContains(
                f"Error running work request Workflow/noop ({wr.id}): "
                f"{expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        mock_noop_populate.assert_called_once_with()
        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(raised.exception.message, expected_message)
        self.assertEqual(raised.exception.code, "orchestrator-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)

    def test_workflow_unblocks_children(self) -> None:
        """Workflow children are unblocked if possible."""
        wr = self.playground.create_workflow(task_name="noop")
        wr.mark_running()

        def populate() -> None:
            children = [wr.create_child("noop") for _ in range(2)]
            children[1].add_dependency(children[0])

        with mock.patch(
            "debusine.server.workflows.noop.NoopWorkflow.populate",
            side_effect=populate,
        ) as mock_noop_populate:
            orchestrate_workflow(wr)

        mock_noop_populate.assert_called_once_with()
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)
        children = wr.children.order_by("id")
        self.assertEqual(children.count(), 2)
        self.assertEqual(children[0].status, WorkRequest.Statuses.PENDING)
        self.assertEqual(children[1].status, WorkRequest.Statuses.BLOCKED)

    def test_wrong_task_type(self) -> None:
        """Attempts to orchestrate non-workflows are logged."""
        wr = self.playground.create_work_request()
        expected_message = "Does not have a workflow orchestrator"

        with (
            self.assertLogsContains(
                f"Error running work request {wr.task_type}/{wr.task_name} "
                f"({wr.id}): {expected_message}",
                logger="debusine.server.workflows.base",
                level=logging.WARNING,
            ),
            self.assertRaises(WorkflowRunError) as raised,
        ):
            orchestrate_workflow(wr)

        self.assertEqual(raised.exception.work_request, wr)
        self.assertEqual(raised.exception.message, expected_message)
        self.assertEqual(raised.exception.code, "configure-failed")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)
