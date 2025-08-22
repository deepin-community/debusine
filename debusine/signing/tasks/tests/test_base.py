# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for base signing task classes."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.db import connections
from django.test import TestCase, TransactionTestCase

from debusine.signing.tasks import BaseSigningTask
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData, WorkerType
from debusine.tasks.tests.helper_mixin import SampleBaseTask


class SampleBaseSigningTask(
    SampleBaseTask[BaseTaskData, BaseDynamicTaskData],
    BaseSigningTask[BaseTaskData, BaseDynamicTaskData],
):
    """Sample class to test BaseSigningTask class."""

    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Succeed if this task is running in a transaction."""
        return connections["default"].in_atomic_block


class SampleBaseSigningTask2Data(BaseTaskData):
    """Data representation for SampleBaseSigningTask2."""

    foo: str


class SampleBaseSigningTask2(
    SampleBaseTask[SampleBaseSigningTask2Data, BaseDynamicTaskData],
    BaseSigningTask[SampleBaseSigningTask2Data, BaseDynamicTaskData],
):
    """Test BaseSigningTask class with jsonschema validation."""

    TASK_VERSION = 1

    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class BaseSigningTaskTests(TestCase):
    """Unit tests for :py:class:`BaseSigningTask`."""

    def setUp(self) -> None:
        """Create the shared attributes."""
        super().setUp()
        self.task = SampleBaseSigningTask({})
        self.task2 = SampleBaseSigningTask2({"foo": "bar"})
        self.worker_metadata = {"system:worker_type": WorkerType.SIGNING}

    def test_can_run_on_no_version(self) -> None:
        """Ensure can_run_on returns True if no version is specified."""
        self.assertIsNone(self.task.TASK_VERSION)
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        self.assertEqual(self.task.can_run_on(metadata), True)

    def test_can_run_on_with_different_versions(self) -> None:
        """Ensure can_run_on returns False if versions differ."""
        self.assertIsNone(self.task.TASK_VERSION)
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        metadata["signing:samplebasesigningtask:version"] = 1
        self.assertEqual(self.task.can_run_on(metadata), False)


class BaseSigningTaskTransactionTests(TransactionTestCase):
    """Test transactional behaviour of :py:class:`BaseSigningTask`."""

    def test_runs_in_transaction(self) -> None:
        """Tasks are executed in a transaction."""
        task = SampleBaseSigningTask({}, {})
        task._debug_log_files_directory = TemporaryDirectory(
            prefix="debusine-tests-"
        )
        self.addCleanup(task._debug_log_files_directory.cleanup)
        with mock.patch.object(
            task, "_upload_work_request_debug_logs", autospec=True
        ):
            self.assertTrue(task.execute())
