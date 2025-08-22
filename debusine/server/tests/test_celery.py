# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Celery integration."""

import logging
from typing import Any
from unittest import mock

from django.db import connections, transaction

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest, Worker
from debusine.server.celery import (
    ServerTaskRunError,
    WorkRequestNotPending,
    run_server_task,
    update_workflows,
)
from debusine.server.tasks.models import ServerNoopData
from debusine.server.tasks.tests.helpers import SampleBaseServerTask
from debusine.tasks.models import (
    BaseDynamicTaskData,
    BaseTaskData,
    OutputData,
    OutputDataError,
)
from debusine.test.django import TestCase, TransactionTestCase


class SampleTaskNotManagingTransactions(
    SampleBaseServerTask[BaseTaskData, BaseDynamicTaskData]
):
    """A sample server task that does not manage its own transactions."""

    TASK_VERSION = 1

    def _execute(self) -> bool:
        return connections["default"].in_atomic_block


class SampleTaskManagingTransactions(
    SampleBaseServerTask[BaseTaskData, BaseDynamicTaskData]
):
    """A sample server task that manages its own transactions."""

    TASK_VERSION = 1
    TASK_MANAGES_TRANSACTIONS = True

    def _execute(self) -> bool:
        return not connections["default"].in_atomic_block


class RunServerTaskTests(TestCase):
    """Unit tests for :py:func:`run_server_task`."""

    def setUp(self) -> None:
        """Create common objects."""
        super().setUp()
        self.worker = Worker.objects.get_or_create_celery()

    def create_assigned_work_request(self, **kwargs: Any) -> WorkRequest:
        """Create a work request and assign it to self.worker."""
        work_request = self.playground.create_work_request(**kwargs)
        work_request.assign_worker(self.worker)
        return work_request

    def test_no_work_request(self) -> None:
        """The Celery task fails if the work request does not exist."""
        work_request_id = self.playground.create_work_request().id + 1
        with self.assertLogsContains(
            f"Work request {work_request_id} does not exist",
            logger="debusine.server.celery",
            level=logging.ERROR,
        ):
            result = run_server_task.apply(args=(work_request_id,))
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, WorkRequest.DoesNotExist)

    def test_not_pending(self) -> None:
        """The Celery task fails if the work request is not pending."""
        work_request = self.create_assigned_work_request()
        work_request.mark_completed(WorkRequest.Results.ERROR)
        with self.assertLogsContains(
            f"Work request {work_request.id} is in status completed, not "
            f"pending",
            logger="debusine.server.celery",
            level=logging.ERROR,
        ):
            result = run_server_task.apply(args=(work_request.id,))
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, WorkRequestNotPending)

    def test_bad_task_name(self) -> None:
        """The Celery task fails if the work request has a bad task name."""
        work_request = self.create_assigned_work_request(
            task_type=TaskTypes.SERVER, task_name="servernoop"
        )
        work_request.task_name = "nonexistent"
        work_request.save()
        expected_message = "'nonexistent' is not a registered Server task_name"
        with (
            self.assertLogsContains(
                f"Error running work request Server/nonexistent "
                f"({work_request.id}): {expected_message}",
                logger="debusine.server.celery",
                level=logging.ERROR,
            ),
            transaction.atomic(),
        ):
            result = run_server_task.apply(args=(work_request.id,))
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, ServerTaskRunError)
        self.assertEqual(result.result.message, expected_message)
        self.assertEqual(result.result.code, "setup-failed")
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNotNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.ERROR)
        self.assertEqual(
            work_request.output_data,
            OutputData(
                errors=[
                    OutputDataError(
                        message=expected_message, code="setup-failed"
                    )
                ]
            ),
        )

    def test_bad_task_data(self) -> None:
        """The Celery task fails if the work request has bad task data."""
        work_request = self.create_assigned_work_request(
            task_type=TaskTypes.SERVER, task_name="servernoop"
        )
        work_request.task_data = {"nonexistent": True}
        work_request.configured_task_data = {"nonexistent": True}
        work_request.save()
        expected_message = (
            "Failed to configure: 1 validation error for ServerNoopData"
        )
        with (
            self.assertLogsContains(
                f"Error running work request Server/servernoop "
                f"({work_request.id}): {expected_message}",
                logger="debusine.server.celery",
                level=logging.ERROR,
            ),
            transaction.atomic(),
        ):
            result = run_server_task.apply(args=(work_request.id,))
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, ServerTaskRunError)
        self.assertTrue(result.result.message.startswith(expected_message))
        self.assertEqual(result.result.code, "configure-failed")
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNotNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.ERROR)
        assert work_request.output_data is not None
        self.assertIsInstance(work_request.output_data, OutputData)
        assert work_request.output_data.errors is not None
        self.assertEqual(len(work_request.output_data.errors), 1)
        self.assertTrue(
            work_request.output_data.errors[0].message.startswith(
                expected_message
            )
        )
        self.assertEqual(
            work_request.output_data.errors[0].code, "configure-failed"
        )

    def test_external_task(self) -> None:
        """The Celery task fails if asked to run an external task."""
        work_request = self.create_assigned_work_request(
            task_name="noop", task_data={"result": True}
        )
        expected_message = "Cannot run on a Celery worker"
        with (
            self.assertLogsContains(
                f"Error running work request Worker/noop ({work_request.id}): "
                f"{expected_message}",
                logger="debusine.server.celery",
                level=logging.ERROR,
            ),
            transaction.atomic(),
        ):
            result = run_server_task.apply(args=(work_request.id,))
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, ServerTaskRunError)
        self.assertEqual(result.result.message, expected_message)
        self.assertEqual(result.result.code, "wrong-task-type")
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNotNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.ERROR)
        self.assertEqual(
            work_request.output_data,
            OutputData(
                errors=[
                    OutputDataError(
                        message=expected_message, code="wrong-task-type"
                    )
                ]
            ),
        )

    def test_task_raises_exception(self) -> None:
        """The Celery task fails if the task raises an exception."""
        work_request = self.create_assigned_work_request(
            task_type=TaskTypes.SERVER,
            task_name="servernoop",
            task_data=ServerNoopData(exception=True),
        )
        expected_message = "Execution failed: Client requested an exception"
        with (
            self.assertLogsContains(
                f"Error running work request Server/servernoop "
                f"({work_request.id}): {expected_message}",
                logger="debusine.server.celery",
                level=logging.ERROR,
            ),
            transaction.atomic(),
        ):
            result = run_server_task.apply(args=(work_request.id,))
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, ServerTaskRunError)
        self.assertEqual(
            result.result.message,
            "Execution failed: Client requested an exception",
        )
        self.assertEqual(result.result.code, "execute-failed")
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNotNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.ERROR)
        self.assertEqual(
            work_request.output_data,
            OutputData(
                errors=[
                    OutputDataError(
                        message=expected_message, code="execute-failed"
                    )
                ]
            ),
        )

    def test_task_aborted(self) -> None:
        """If the task is aborted, the Celery task logs but does not fail."""
        work_request = self.create_assigned_work_request(
            task_type=TaskTypes.SERVER, task_name="servernoop"
        )
        with (
            self.assertLogsContains(
                "Task: servernoop has been aborted",
                logger="debusine.server.celery",
                level=logging.INFO,
            ),
            mock.patch(
                "debusine.server.tasks.noop.ServerNoop.execute",
                lambda task: task.abort(),
            ),
            transaction.atomic(),
        ):
            result = run_server_task.apply(args=(work_request.id,))
        self.assertFalse(result.failed())
        self.assertFalse(result.result)
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(work_request.result, WorkRequest.Results.NONE)
        self.assertIsNone(work_request.output_data)

    def test_task_returns_false(self) -> None:
        """If the task returns False, the Celery task records that."""
        work_request = self.create_assigned_work_request(
            task_type=TaskTypes.SERVER,
            task_name="servernoop",
            task_data=ServerNoopData(result=False),
        )
        with transaction.atomic():
            result = run_server_task.apply(args=(work_request.id,))
        self.assertFalse(result.failed())
        self.assertFalse(result.result)
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNotNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.FAILURE)
        self.assertIsNone(work_request.output_data)

    def test_task_returns_true(self) -> None:
        """If the task returns True, the Celery task records that."""
        work_request = self.create_assigned_work_request(
            task_type=TaskTypes.SERVER,
            task_name="servernoop",
            task_data=ServerNoopData(result=True),
        )
        with transaction.atomic():
            result = run_server_task.apply(args=(work_request.id,))
        self.assertFalse(result.failed())
        self.assertTrue(result.result)
        work_request.refresh_from_db()
        self.assertIsNotNone(work_request.started_at)
        self.assertIsNotNone(work_request.completed_at)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.SUCCESS)
        self.assertIsNone(work_request.output_data)


class RunServerTaskTransactionTests(TransactionTestCase):
    """Unit tests for :py:func:`run_server_task`."""

    def test_task_not_managing_transactions(self) -> None:
        """An atomic block is used for tasks that do not manage transactions."""
        worker = Worker.objects.get_or_create_celery()
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.SERVER,
            task_name="sampletasknotmanagingtransactions",
            task_data={},
        )
        work_request.assign_worker(worker)
        result = run_server_task.apply(args=(work_request.id,))
        self.assertFalse(result.failed())
        self.assertTrue(result.result)

    def test_task_managing_transactions(self) -> None:
        """An atomic block is not used for tasks that manage transactions."""
        worker = Worker.objects.get_or_create_celery()
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.SERVER,
            task_name="sampletaskmanagingtransactions",
            task_data={},
        )
        work_request.assign_worker(worker)
        result = run_server_task.apply(args=(work_request.id,))
        self.assertFalse(result.failed())
        self.assertTrue(result.result)


class UpdateWorkflowTransactionTests(TransactionTestCase):
    """Unit tests for :py:func:`update_workflows`."""

    def test_updates_workflows(self) -> None:
        """The Celery task updates workflow properties."""
        with mock.patch("django.db.transaction.on_commit"):
            workflow = self.playground.create_workflow()
            work_request = workflow.create_child("noop")
            self.assertTrue(work_request.mark_aborted())
            self.assertTrue(work_request.workflows_need_update)

        result = update_workflows.apply()
        self.assertFalse(result.failed())
        self.assertIsNone(result.result)

        workflow.refresh_from_db()
        work_request.refresh_from_db()
        self.assertEqual(
            workflow.workflow_last_activity_at, work_request.completed_at
        )
        self.assertEqual(
            workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.ABORTED,
        )
        self.assertFalse(work_request.workflows_need_update)
