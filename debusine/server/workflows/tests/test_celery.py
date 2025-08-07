# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for workflow Celery integration."""

import logging
from unittest import mock

from django.db import connections

from debusine.db.models import WorkRequest
from debusine.server.workflows.celery import run_workflow_task
from debusine.tasks.models import OutputData, OutputDataError
from debusine.test.django import TestCase, TransactionTestCase


class RunWorkflowTaskTests(TestCase):
    """Unit tests for :py:func:`run_workflow_task`."""

    def test_no_work_request(self) -> None:
        """The Celery task fails if the work request does not exist."""
        work_request_id = self.playground.create_work_request().id + 1

        with self.assertLogsContains(
            f"Work request {work_request_id} does not exist",
            logger="debusine.server.workflows.celery",
            level=logging.ERROR,
        ):
            result = run_workflow_task.apply(args=(work_request_id,))

        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, WorkRequest.DoesNotExist)

    def test_not_running(self) -> None:
        """The Celery task returns False if the work request is not running."""
        wr = self.playground.create_workflow(task_name="noop")

        with self.assertLogsContains(
            f"Error running work request Workflow/noop ({wr.id}): "
            f"Work request is in status pending, not running",
            logger="debusine.server.workflows.base",
            level=logging.WARNING,
        ):
            result = run_workflow_task.apply(args=(wr.id,))

        self.assertFalse(result.failed())
        self.assertFalse(result.result)
        wr.refresh_from_db()
        self.assertIsNotNone(wr.completed_at)
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.ERROR)
        self.assertEqual(
            wr.output_data,
            OutputData(
                errors=[
                    OutputDataError(
                        message=(
                            "Work request is in status pending, not running"
                        ),
                        code="wrong-status",
                    )
                ]
            ),
        )

    def test_sub_workflow(self) -> None:
        """The Celery task returns False if given a sub-workflow."""
        root_wr = self.playground.create_workflow(task_name="noop")
        sub_wr = self.playground.create_workflow(
            task_name="noop", parent=root_wr
        )
        sub_wr.mark_running()

        with self.assertLogsContains(
            f"Error running work request Workflow/noop ({sub_wr.id}): Must be "
            f"populated by its parent workflow instead",
            logger="debusine.server.workflows.celery",
            level=logging.ERROR,
        ):
            result = run_workflow_task.apply(args=(sub_wr.id,))

        self.assertFalse(result.failed())
        self.assertFalse(result.result)
        sub_wr.refresh_from_db()
        self.assertIsNotNone(sub_wr.started_at)
        self.assertIsNotNone(sub_wr.completed_at)
        self.assertEqual(sub_wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(sub_wr.result, WorkRequest.Results.ERROR)
        self.assertEqual(
            sub_wr.output_data,
            OutputData(
                errors=[
                    OutputDataError(
                        message=(
                            "Must be populated by its parent workflow instead"
                        ),
                        code="non-root-workflow",
                    )
                ]
            ),
        )

    def test_workflow_callback(self) -> None:
        """A workflow callback is run and marked as completed."""
        parent = self.playground.create_workflow(task_name="noop")
        parent.mark_running()
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )
        wr.mark_running()

        with mock.patch(
            "debusine.server.workflows.noop.NoopWorkflow.callback"
        ) as mock_noop_callback:
            result = run_workflow_task.apply(args=(wr.id,))

        self.assertFalse(result.failed())
        self.assertTrue(result.result)
        mock_noop_callback.assert_called_once_with(wr)
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)

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
            result = run_workflow_task.apply(args=(wr.id,))

        self.assertFalse(result.failed())
        self.assertTrue(result.result)
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
            result = run_workflow_task.apply(args=(wr.id,))

        self.assertFalse(result.failed())
        self.assertTrue(result.result)
        mock_noop_populate.assert_called_once_with()
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)


class RunWorkflowTaskTransactionTests(TransactionTestCase):
    """Test transactional behaviour of :py;func:`run_workflow_task`."""

    def test_runs_in_transaction(self) -> None:
        """The workflow orchestrator is run in a transaction."""
        wr = self.playground.create_workflow(task_name="noop")
        wr.mark_running()

        def fake_populate() -> None:
            self.assertTrue(connections["default"].in_atomic_block)

        with mock.patch(
            "debusine.server.workflows.noop.NoopWorkflow.populate",
            side_effect=fake_populate,
        ) as mock_noop_populate:
            result = run_workflow_task.apply(args=(wr.id,))

        self.assertFalse(result.failed())
        self.assertTrue(result.result)
        mock_noop_populate.assert_called_once_with()
