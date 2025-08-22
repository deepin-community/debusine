# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the base Task classes."""
import abc
import datetime
import itertools
import math
import os
import shlex
import signal
import subprocess
import sys
import textwrap
import time
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock
from unittest.mock import MagicMock

import psutil

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts import WorkRequestDebugLogs
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    EmptyArtifactData,
    TaskTypes,
)
from debusine.client.debusine import Debusine
from debusine.tasks import (
    BaseExternalTask,
    BaseTask,
    DefaultDynamicData,
    ExtraRepositoryMixin,
    RunCommandTask,
    Sbuild,
    TaskConfigError,
)
from debusine.tasks.executors import (
    ExecutorImageCategory,
    ExecutorInterface,
    InstanceInterface,
)
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    BaseDynamicTaskDataWithExecutor,
    BaseTaskData,
    BaseTaskDataWithExecutor,
    BaseTaskDataWithExtraRepositories,
    ExtraRepository,
    WorkerType,
)
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
    SampleBaseExternalTask,
    SampleBaseTask,
    SampleBaseTaskWithExecutor,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_system_tarball_data,
    preserve_task_registry,
)


class SampleBaseTask1(
    SampleBaseExternalTask[BaseTaskData, BaseDynamicTaskData],
):
    """Sample class to test BaseExternalTask class."""

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SampleBaseTaskWithExecutor1(
    SampleBaseTaskWithExecutor[
        BaseTaskDataWithExecutor, BaseDynamicTaskDataWithExecutor
    ],
):
    """Sample class to test BaseTaskWithExecutor class."""

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SampleBaseTask2Data(BaseTaskData):
    """Data representation for SampleBaseTask2."""

    foo: str
    bar: float | None = None
    host_architecture: str | None = None


class SampleBaseTask2(
    SampleBaseExternalTask[SampleBaseTask2Data, BaseDynamicTaskData],
):
    """Test BaseExternalTask class with data validation."""

    TASK_VERSION = 1

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SampleBaseTaskDataWithExecutorAndArchitecture(BaseTaskDataWithExecutor):
    """BaseTaskDataWithExecutor with a host architecture."""

    host_architecture: str | None = None


class SampleBaseTaskWithExecutorAndArchitecture(
    SampleBaseTaskWithExecutor[
        SampleBaseTaskDataWithExecutorAndArchitecture,
        BaseDynamicTaskDataWithExecutor,
    ],
):
    """Test BaseTaskWithExecutor with a host architecture."""

    TASK_VERSION = 1

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SampleExtraRepositoryMixin(
    SampleBaseTask[BaseTaskDataWithExtraRepositories, BaseDynamicTaskData],
    ExtraRepositoryMixin[
        BaseTaskDataWithExtraRepositories, BaseDynamicTaskData
    ],
):
    """Test ExtraRepositoryMixin."""

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SampleDefaultDynamicData(
    DefaultDynamicData[BaseTaskData],
    SampleBaseTask[BaseTaskData, BaseDynamicTaskData],
):
    """Test DefaultDynamicData."""

    TASK_TYPE = TaskTypes.WORKER

    def _execute(self) -> bool:
        raise NotImplementedError()

    def _upload_work_request_debug_logs(self) -> None:
        raise NotImplementedError()

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class BaseTaskTests(TestCase):
    """Unit tests for BaseTask class."""

    worker_metadata = {"system:worker_type": WorkerType.EXTERNAL}

    def setUp(self) -> None:
        """Create the shared attributes."""
        super().setUp()
        self.task = SampleBaseTask1({})
        self.task2 = SampleBaseTask2({"foo": "bar"})

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def test_task_initialize_member_variables(self) -> None:
        """Member variables expected used in other methods are initialized."""
        self.assertIsNone(self.task.workspace_name)
        self.assertEqual(self.task._source_artifacts_ids, [])

    def test_name_is_set(self) -> None:
        """task.name is built from class name."""
        self.assertEqual(self.task.name, "samplebasetask1")

    def test_logger_is_configured(self) -> None:
        """task.logger is available."""
        self.assertIsNotNone(self.task.logger)

    @preserve_task_registry()
    def test_prefix_with_task_name(self) -> None:
        """task.prefix_with_task_name does what it says."""
        self.assertEqual(
            self.task.prefix_with_task_name("foobar"),
            f"{self.task.name}:foobar",
        )

        # Cannot import ServerNoop or NoopWorkflow because sometimes these
        # tests are run without the django test runner

        class SampleServer(BaseTask[BaseTaskData, BaseDynamicTaskData]):
            TASK_TYPE = TaskTypes.SERVER

            def _execute(self) -> bool:
                raise NotImplementedError()

            def _upload_work_request_debug_logs(self) -> None:
                raise NotImplementedError()

        self.assertEqual(
            SampleServer.prefix_with_task_name("foo"), "server:sampleserver:foo"
        )

        class SampleInternal(BaseTask[BaseTaskData, BaseDynamicTaskData]):
            TASK_TYPE = TaskTypes.INTERNAL

            def _execute(self) -> bool:
                raise NotImplementedError()

            def _upload_work_request_debug_logs(self) -> None:
                raise NotImplementedError()

        self.assertEqual(
            SampleInternal.prefix_with_task_name("foo"),
            "internal:sampleinternal:foo",
        )

        class SampleWorkflow(BaseTask[BaseTaskData, BaseDynamicTaskData]):
            TASK_TYPE = TaskTypes.WORKFLOW

            def _execute(self) -> bool:
                raise NotImplementedError()

            def _upload_work_request_debug_logs(self) -> None:
                raise NotImplementedError()

        self.assertEqual(
            SampleWorkflow.prefix_with_task_name("foo"),
            "workflow:sampleworkflow:foo",
        )

        class SampleSigning(BaseTask[BaseTaskData, BaseDynamicTaskData]):
            TASK_TYPE = TaskTypes.SIGNING

            def _execute(self) -> bool:
                raise NotImplementedError()

            def _upload_work_request_debug_logs(self) -> None:
                raise NotImplementedError()

        self.assertEqual(
            SampleSigning.prefix_with_task_name("foo"),
            "signing:samplesigning:foo",
        )

        class SampleWait(BaseTask[BaseTaskData, BaseDynamicTaskData]):
            TASK_TYPE = TaskTypes.WAIT

            def _execute(self) -> bool:
                raise NotImplementedError()

            def _upload_work_request_debug_logs(self) -> None:
                raise NotImplementedError()

        self.assertEqual(
            SampleWait.prefix_with_task_name("foo"), "wait:samplewait:foo"
        )

    def test_get_subject(self) -> None:
        self.task.dynamic_data = BaseDynamicTaskDataWithExecutor(
            subject="hello"
        )

        self.assertEqual(self.task.get_subject(), "hello")

    def test_get_subject_dynamic_task_data_is_none(self) -> None:
        self.task.dynamic_data = None

        self.assertIsNone(self.task.get_subject())

    def test_configure_sets_data_attribute(self) -> None:
        """task.data is set with configure."""
        task_data = {
            "foo": "bar",
        }

        data = SampleBaseTask2Data.parse_obj(task_data)

        self.assertEqual(
            data,
            SampleBaseTask2Data(
                foo="bar",
            ),
        )

    def test_configure_task_data_notification_drop(self) -> None:
        """Raise TaskConfigError: "notifications" is not valid anymore."""
        task_data = SampleBaseTask2Data(foo="bar")
        task_data_notification = {
            "notifications": {"on_failure": [{"channel": "test"}]}
        }

        with self.assertRaisesRegex(TaskConfigError, "notifications"):
            SampleBaseTask2({**task_data.dict(), **task_data_notification})

    def test_configure_fails_with_bad_schema(self) -> None:
        """configure() raises TaskConfigError if schema is not respected."""
        task_data = {"nonexistent": "bar"}
        with self.assertRaises(TaskConfigError):
            SampleBaseTask2(task_data)

    def test_configure_works_with_good_schema(self) -> None:
        """configure() doesn't raise TaskConfigError."""
        task_data = SampleBaseTask2Data(foo="bar", bar=3.14)

        task2 = SampleBaseTask2(task_data.dict())

        self.assertEqual(task2.data.foo, "bar")
        self.assertEqual(task2.data.bar, 3.14)

    def test_analyze_worker_without_task_version(self) -> None:
        """analyze_worker() reports an unknown task version."""
        metadata = self.task.analyze_worker()
        self.assertIsNone(metadata["samplebasetask1:version"])

    def test_analyze_worker_with_task_version(self) -> None:
        """analyze_worker() reports a task version."""
        metadata = self.task2.analyze_worker()
        self.assertEqual(metadata["samplebasetask2:version"], 1)

    def test_analyze_worker_all_tasks(self) -> None:
        """analyze_worker_all_tasks() reports results for each task."""
        patcher = mock.patch.object(
            BaseTask,
            "_sub_tasks",
            new={
                TaskTypes.WORKER: {
                    "task": SampleBaseTask1,
                    "task2": SampleBaseTask2,
                }
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        metadata = self.task.analyze_worker_all_tasks()

        self.assertEqual(
            metadata,
            self.task2.analyze_worker() | self.task.analyze_worker(),
        )

        # Assert that metadata contains data
        self.assertNotEqual(metadata, {})

    def test_host_architecture_from_task(self) -> None:
        """host_architecture() passes through a value from task data if set."""
        self.task2.data.host_architecture = "i386"

        self.assertEqual(self.task2.host_architecture(), "i386")

    def test_host_architecture_from_task_and_worker(self) -> None:
        """host_architecture() prefers information from the task."""
        self.task2.data.host_architecture = "i386"
        self.task2.worker_host_architecture = "amd64"

        self.assertEqual(self.task2.host_architecture(), "i386")

    def test_host_architecture_from_worker(self) -> None:
        """host_architecture() falls back to a value from the worker."""
        self.task2.worker_host_architecture = "amd64"

        self.assertEqual(self.task2.host_architecture(), "amd64")

    def test_class_for_name_invalid_type(self) -> None:
        """class_from_name raises a ValueError exception."""
        with self.assertRaisesRegex(
            ValueError, "'not-existing-type' is not a registered task type"
        ):
            BaseTask.class_from_name(
                cast(TaskTypes, "not-existing-type"), "non-existing-class"
            )

    def test_class_for_name_sbuild(self) -> None:
        """class_from_name returns Sbuild (case-insensitive)."""
        self.assertEqual(
            BaseTask.class_from_name(TaskTypes.WORKER, 'sBuIlD'), Sbuild
        )

    def test_class_for_name_no_class(self) -> None:
        """class_from_name raises a ValueError exception."""
        with self.assertRaisesRegex(
            ValueError,
            "'non-existing-class' is not a registered Worker task_name",
        ):
            BaseTask.class_from_name(TaskTypes.WORKER, 'non-existing-class')

    @preserve_task_registry()
    def test_class_for_name_no_duplicates(self) -> None:
        """
        BaseTask.__init_subclass__ raises AssertionError for duplicated names.

        BaseTask.__init_subclass__ uses the class name in lowercase: it can
        cause unexpected duplicated names. Check that are detected.
        """
        msg = "Two Tasks with the same name: 'somesubclassname'"
        with self.assertRaisesRegex(AssertionError, msg):

            class Somesubclassname(
                SampleBaseExternalTask[BaseTaskData, BaseDynamicTaskData],
            ):
                def run(self, execute_directory: Path) -> bool:  # noqa: U100
                    raise NotImplementedError()

            class SomeSubclassName(
                SampleBaseExternalTask[BaseTaskData, BaseDynamicTaskData],
            ):
                def run(self, execute_directory: Path) -> bool:  # noqa: U100
                    raise NotImplementedError()

    @preserve_task_registry()
    def test_conflict_names_server_worker(self) -> None:
        """Server and Worker tasks namespaces are shared."""
        msg = "'somesubclassname' already registered as a Server task"
        with self.assertRaisesRegex(AssertionError, msg):

            class Somesubclassname(
                SampleBaseTask[BaseTaskData, BaseDynamicTaskData]
            ):
                TASK_TYPE = TaskTypes.SERVER

                def _execute(self) -> bool:
                    raise NotImplementedError()

                def _upload_work_request_debug_logs(self) -> None:
                    raise NotImplementedError()

            class SomeSubclassName(
                SampleBaseExternalTask[BaseTaskData, BaseDynamicTaskData],
            ):
                def run(self, execute_directory: Path) -> bool:  # noqa: U100
                    raise NotImplementedError()

        msg = "'somesubclassname1' already registered as a Worker task"
        with self.assertRaisesRegex(AssertionError, msg):

            class SomeSubclassName1(
                SampleBaseExternalTask[BaseTaskData, BaseDynamicTaskData],
            ):
                def run(self, execute_directory: Path) -> bool:  # noqa: U100
                    raise NotImplementedError()

            class Somesubclassname1(
                SampleBaseTask[BaseTaskData, BaseDynamicTaskData]
            ):
                TASK_TYPE = TaskTypes.SERVER

                def _execute(self) -> bool:
                    raise NotImplementedError()

                def _upload_work_request_debug_logs(self) -> None:
                    raise NotImplementedError()

    def test_init_subclass_no_register_abstract_class(self) -> None:
        """BaseTask.__init_subclass__ does not register abstract classes."""

        class Abstract(BaseTask[BaseTaskData, BaseDynamicTaskData], abc.ABC):
            @abc.abstractmethod
            def _do_something(self) -> None:
                raise NotImplementedError()

        for task_type, registry in BaseTask._sub_tasks.items():
            with self.subTest(type=task_type):
                self.assertNotIn("abstract", registry)
                self.assertNotIn("basetask", registry)
                self.assertNotIn("baseexternaltask", registry)
                self.assertNotIn("basetaskwithexecutor", registry)
                self.assertNotIn("runcommandtask", registry)

    def test_execute_logging_exceptions_execute(self) -> None:
        """BaseTask.execute_logging_exceptions() executes self.execute."""
        patcher = mock.patch.object(self.task, "execute", autospec=True)
        mocker = patcher.start()
        self.addCleanup(patcher.stop)

        return_values = [True, False]
        for return_value in return_values:
            mocker.return_value = return_value
            self.assertEqual(
                self.task.execute_logging_exceptions(), return_value
            )

        self.assertEqual(mocker.call_count, len(return_values))

    def test_execute_logging_exceptions_handle_exception(self) -> None:
        """BaseTask.execute_logging_exceptions() logs the exception."""
        patcher = mock.patch.object(self.task, "execute", autospec=True)
        mocker = patcher.start()
        msg = "Exception message"
        mocker.side_effect = ValueError(msg)
        self.addCleanup(patcher.stop)

        with (
            self.assertRaisesRegex(ValueError, msg),
            self.assertLogs(self.task.logger),
        ):
            self.task.execute_logging_exceptions()

    def test_append_to_log_file(self) -> None:
        """BaseTask.append_to_lot_file() appends data to a log file."""
        contents = ["a 1", "b 2"]
        contents_str = "\n".join(contents) + "\n"

        filename = "log.txt"

        # Add data into the file
        self.task.append_to_log_file(filename, contents)

        assert self.task._debug_log_files_directory
        file = Path(self.task._debug_log_files_directory.name) / filename

        self.assertEqual(file.read_text(), contents_str)

        # Add more data
        self.task.append_to_log_file(filename, contents)
        self.assertEqual(file.read_text(), contents_str * 2)

    def test_open_debug_log_file_default_is_append_mode(self) -> None:
        """
        BaseTask.open_debug_log_file() return a file in a temporary directory.

        self.task._debug_log_files_directory is set to the temporary directory.

        Calling it again: return the same file (append mode by default).
        """
        line1 = "First line\n"
        filename = "test.log"
        with self.task.open_debug_log_file(filename) as file:
            file.write(line1)

        # Verify BaseTask._debug_log_files_directory contains the file
        assert self.task._debug_log_files_directory
        directory = Path(self.task._debug_log_files_directory.name)

        self.assertTrue(directory.exists())
        log_file = Path(self.task._debug_log_files_directory.name) / filename

        self.assertEqual(log_file, directory / filename)
        self.assertEqual(log_file.read_text(), line1)

        # Verify by default is in append mode: we are adding to it
        line2 = "Second line\n"
        with self.task.open_debug_log_file(filename) as file:
            file.write(line2)

        self.assertEqual(log_file.read_text(), f"{line1}{line2}")

        self.task._debug_log_files_directory.cleanup()

    def test_open_debug_log_file_mode_is_used(self) -> None:
        """BaseTask.open_debug_log_file() use the specified mode."""
        filename = "test.log"

        with self.task.open_debug_log_file(filename) as f:
            f.write("something")

        line = "new line"
        with self.task.open_debug_log_file(filename, mode="w") as f:
            f.write(line)

        assert self.task._debug_log_files_directory
        file = Path(self.task._debug_log_files_directory.name) / filename
        self.assertEqual(file.read_text(), line)

    def patch_task__execute(self) -> MagicMock:
        """Return mocked object of self.task._execute()."""
        patcher = mock.patch.object(self.task, "_execute", autospec=True)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def patch_task__upload_work_request_debug_logs(self) -> MagicMock:
        """Return mocked object of self.task.upload_work_request_debug_logs."""
        patcher = mock.patch.object(
            self.task, "_upload_work_request_debug_logs", autospec=True
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def task_execute_and_assert(self, expected_value: bool) -> None:
        """
        Call task.execute() and assert return value is expected_value.

        Set up mocks and ensure that mocks are used as expected.
        """
        _execute_mocked = self.patch_task__execute()
        _execute_mocked.return_value = expected_value
        upload_work_request_debug_logs = (
            self.patch_task__upload_work_request_debug_logs()
        )

        self.assertEqual(self.task.execute(), expected_value)

        _execute_mocked.assert_called_with()
        upload_work_request_debug_logs.assert_called_with()

    def test_execute_return_true(self) -> None:
        """BaseTask.execute() returns True and calls method upload logs."""
        self.task_execute_and_assert(True)

    def test_execute_return_false(self) -> None:
        """BaseTask.execute() returns False and calls method upload logs."""
        self.task_execute_and_assert(False)

    def test_abortion_flag(self) -> None:
        """BaseTask.abort() sets the aborted flag."""
        self.assertFalse(self.task.aborted)
        self.task.abort()
        self.assertTrue(self.task.aborted)

    def test_is_valid_task_name(self) -> None:
        """BaseTask.is_valid_task_name() with a valid name."""
        self.assertTrue(
            self.task.is_valid_task_name(TaskTypes.WORKER, "sbuild")
        )

    def test_is_valid_task_name_lowercases(self) -> None:
        """BaseTask.is_valid_task_name() with a valid name with upper case."""
        self.assertTrue(
            self.task.is_valid_task_name(TaskTypes.WORKER, "Sbuild")
        )

    def test_is_valid_task_name_invalid_type(self) -> None:
        """BaseTask.is_valid_task_name() with an invalid type."""
        self.assertFalse(
            self.task.is_valid_task_name(
                cast(TaskTypes, "does-not-exist"), "sbuild"
            )
        )

    def test_is_valid_task_name_invalid(self) -> None:
        """BaseTask.is_valid_task_name() with an invalid name."""
        self.assertFalse(
            self.task.is_valid_task_name(TaskTypes.WORKER, "not_a_real_task")
        )

    def test_task_names_worker(self) -> None:
        """BaseTask.task_names() returns all available worker tasks."""
        task_names = self.task.task_names(TaskTypes.WORKER)
        self.assertGreater(len(task_names), 0)
        self.assertIn("noop", task_names)

    def test_worker_task_names(self) -> None:
        """BaseTask.worker_task_names() returns all available worker tasks."""
        task_names = self.task.worker_task_names()
        self.assertGreater(len(task_names), 0)
        self.assertIn("noop", task_names)

    def test_is_worker_task(self) -> None:
        """BaseTask.is_worker_task() knows that noop is a WORKER task."""
        self.assertTrue(self.task.is_worker_task("noop"))

    def test_is_worker_task_false(self) -> None:
        """BaseTask.is_worker_task(): nonexistent isn't a WORKER task."""
        self.assertFalse(self.task.is_worker_task("nonexistent"))

    def test_ensure_artifact_categories_artifact(self) -> None:
        BaseTask.ensure_artifact_categories(
            configuration_key="source_artifact",
            category=ArtifactCategory.TEST,
            expected=[ArtifactCategory.TEST, ArtifactCategory.SOURCE_PACKAGE],
        )

    def test_ensure_artifact_categories_raise(self) -> None:
        with self.assertRaisesRegex(
            TaskConfigError,
            r"^source_artifact: unexpected artifact category: 'debusine:test'. "
            r"Valid categories: "
            r"\['debian:binary-package', 'debian:binary-packages'\]$",
        ):
            BaseTask.ensure_artifact_categories(
                configuration_key="source_artifact",
                category=ArtifactCategory.TEST,
                expected=[
                    ArtifactCategory.BINARY_PACKAGE,
                    ArtifactCategory.BINARY_PACKAGES,
                ],
            )

    def test_ensure_collection_category_ok(self) -> None:
        BaseTask.ensure_collection_category(
            configuration_key="source_collection",
            category=CollectionCategory.TEST,
            expected=CollectionCategory.TEST,
        )

    def test_ensure_collection_category_raise(self) -> None:
        with self.assertRaisesRegex(
            TaskConfigError,
            fr"^source_collection: unexpected collection category: "
            fr"'{CollectionCategory.TEST}'\. "
            fr"Expected: '{CollectionCategory.SUITE}'$",
        ):
            BaseTask.ensure_collection_category(
                configuration_key="source_collection",
                category=CollectionCategory.TEST,
                expected=CollectionCategory.SUITE,
            )

    def test_instantiate_with_new_data(self) -> None:
        task = self.task2.instantiate_with_new_data({"foo": "baz"})
        self.assertEqual(self.task2.data.foo, "bar")
        self.assertEqual(task.data.foo, "baz")

        self.assertIsNone(task.work_request_id)
        self.assertIsNone(task.workspace_name)
        self.assertIsNone(task.worker_host_architecture)

    def test_instantiate_with_new_data_copy_attrs(self) -> None:
        """instantiate_with_new_data copies extra attributes."""
        self.task2.work_request_id = 42
        self.task2.workspace_name = "test_workspace"
        self.task2.worker_host_architecture = "arm64"

        task = self.task2.instantiate_with_new_data({"foo": "bar"})

        self.assertEqual(task.work_request_id, 42)
        self.assertEqual(task.workspace_name, "test_workspace")
        self.assertEqual(task.worker_host_architecture, "arm64")

    def test_instantiate_with_new_data_ignores_old_data(self) -> None:
        """instantiate_with_new_data ignores existing task data."""
        self.task2.data.bar = 1
        task = self.task2.instantiate_with_new_data({"foo": "bar"})
        self.assertEqual(task.data.foo, "bar")
        self.assertIsNone(task.data.bar)

    def test_compute_dynamic_data(self) -> None:
        task_db = FakeTaskDatabase()

        self.assertEqual(
            self.task.compute_dynamic_data(task_db), BaseDynamicTaskData()
        )


class SampleBaseExternalTask1(
    SampleBaseExternalTask[BaseTaskData, BaseDynamicTaskData]
):
    """Sample class to test BaseExternalTask class."""

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class SampleBaseExternalTask2(
    SampleBaseExternalTask[SampleBaseTask2Data, BaseDynamicTaskData]
):
    """Test BaseExternalTask class with data validation."""

    TASK_VERSION = 1

    def run(self, execute_directory: Path) -> bool:
        """Unused abstract method from BaseExternalTask."""
        raise NotImplementedError()


class BaseExternalTaskTests(TestCase):
    """Unit tests for :py:class:`BaseExternalTask`."""

    def setUp(self) -> None:
        """Create the shared attributes."""
        super().setUp()
        self.task = SampleBaseExternalTask1({})
        self.task2 = SampleBaseExternalTask2({"foo": "bar"})
        self.worker_metadata = {"system:worker_type": WorkerType.EXTERNAL}

    def tearDown(self) -> None:
        """Delete temporary directory, if it exists."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def test_can_run_on_no_version(self) -> None:
        """Ensure can_run_on returns True if no version is specified."""
        self.assertIsNone(self.task.TASK_VERSION)
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        self.assertTrue(self.task.can_run_on(metadata))

    def test_can_run_on_with_different_versions(self) -> None:
        """Ensure can_run_on returns False if versions differ."""
        self.assertIsNone(self.task.TASK_VERSION)
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        metadata["samplebaseexternaltask1:version"] = 1
        self.assertFalse(self.task.can_run_on(metadata))

    def test_can_run_on_type_mismatch(self) -> None:
        """Ensure can_run_on returns False if the task/worker types mismatch."""
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        metadata["system:worker_type"] = WorkerType.CELERY
        self.assertFalse(self.task.can_run_on(metadata))

    def test_can_run_on_architecture_not_available(self) -> None:
        """Task wants amd64, no amd64 in system:architectures."""
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        metadata["system:architectures"] = ["arm64"]
        self.task2.data.host_architecture = "amd64"
        self.assertFalse(self.task2.can_run_on(metadata))

    def test_temporary_directory(self) -> None:
        """
        _temporary_directory() return a directory.

        The directory is deleted when the context finishes.
        """
        with self.task._temporary_directory() as temp_dir:
            self.assertIsInstance(temp_dir, Path)
            self.assertTrue(temp_dir.is_dir())
            self.assertTrue(
                temp_dir.name.startswith("debusine-fetch-exec-upload-")
            )

        # Ensure the temporary directory is deleted after the context is exited
        self.assertFalse(temp_dir.exists())

    def test_fetch_artifact(self) -> None:
        """
        fetch_artifact() looks up and downloads a single artifact.

        Update: self._source_artifacts_ids
        """
        directory = self.create_temporary_directory()

        artifact_id = 5
        artifact_workspace = "test"

        self.task.work_request_id = 1
        self.task.debusine = mock.create_autospec(spec=Debusine)
        artifact_response = create_artifact_response(
            workspace=artifact_workspace,
            id=5,
            category=ArtifactCategory.TEST,
            created_at=datetime.datetime.now(),
            data={},
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com"
            ),
            files_to_upload=[],
        )
        assert isinstance(self.task.debusine, MagicMock)
        self.task.debusine.download_artifact.return_value = artifact_response

        self.assertEqual(
            self.task.fetch_artifact(artifact_id, directory), artifact_response
        )

        self.task.debusine.download_artifact.assert_called_once_with(
            artifact_id, directory, tarball=False
        )

        self.assertEqual(self.task._source_artifacts_ids, [artifact_id])

    def test_fetch_artifact_without_configure_server_access(self) -> None:
        """fetch_artifact() asserts that self.debusine is set."""
        with self.assertRaisesRegex(AssertionError, r"^self\.debusine not set"):
            self.task.fetch_artifact(1, self.create_temporary_directory())

    def test_fetch_input_default_returns_true(self) -> None:
        """Default implementation returns True."""
        self.assertTrue(
            self.task.fetch_input(self.create_temporary_directory())
        )

    def test_check_directory_for_consistency(self) -> None:
        """Default implementation returns an empty list."""
        self.assertEqual(
            self.task.check_directory_for_consistency_errors(
                self.create_temporary_directory()
            ),
            [],
        )

    def test_upload_artifacts_default_returns(self) -> None:
        """Default implementation returns without raising an exception."""
        self.task.upload_artifacts(
            self.create_temporary_directory(), execution_success=True
        )

    def test_execute_fetch_input_logs(self) -> None:
        """_execute(): if fetch_input() raises an exception: log it."""
        msg = "There is some exception"
        exc = Exception(msg)

        with mock.patch.object(
            self.task, "fetch_input", autospec=True, side_effect=exc
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name) / "fetch_input.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def test_execute_configure_for_execution_logs(self) -> None:
        """_execute(): if configure_for_execution() raises an exc: log it."""
        msg = "There is some exception"

        exc = Exception(msg)

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task,
                "configure_for_execution",
                autospec=True,
                side_effect=exc,
            ),
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def test_execute_cmdline_logs(self) -> None:
        """_execute(): if run() raises an exc: log it."""
        msg = "There is some exception"

        exc = Exception(msg)

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(self.task, "run", autospec=True, side_effect=exc),
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name) / "execution.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def test_execute_check_directory_for_consistency_errors_logs(self) -> None:
        """_execute(): if check_directory_for_consistency_errors() fails."""
        msg = "There is some exception"

        exc = Exception(msg)

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(self.task, "run", autospec=True),
            mock.patch.object(
                self.task,
                "check_directory_for_consistency_errors",
                autospec=True,
                side_effect=exc,
            ),
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name)
            / "post_execution.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def test_execute_check_directory_for_consistency_errors_non_empty(
        self,
    ) -> None:
        """_execute(): if the output directory has consistency errors."""
        msg = "Consistency error"

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(self.task, "run", autospec=True),
            mock.patch.object(
                self.task,
                "check_directory_for_consistency_errors",
                autospec=True,
                return_value=[msg],
            ),
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name) / "consistency.log"
        ).read_text()

        self.assertRegex(log, r"^Consistency error$")

    def test_execute_upload_artifacts_logs(self) -> None:
        """_execute(): if upload_artifacts() raises an exception: log it."""
        msg = "There is some exception"

        exc = Exception(msg)

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(self.task, "run", autospec=True),
            mock.patch.object(
                self.task, "upload_artifacts", autospec=True, side_effect=exc
            ),
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name)
            / "post_execution.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def test_execute_cleanup_logs(self) -> None:
        """_execute(): if cleanup() raises an exception: log it."""
        msg = "There is some exception"

        exc = Exception(msg)

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(self.task, "run", autospec=True),
            mock.patch.object(self.task, "upload_artifacts", autospec=True),
            mock.patch.object(
                self.task, "cleanup", autospec=True, side_effect=exc
            ),
        ):
            self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name)
            / "post_execution.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def test_execute_logs_progress_success(self) -> None:
        """
        _execute() logs each of its main steps: success.

        For a success, there is no stages.log file.
        """
        self.task.work_request_id = 1

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(self.task, "run", autospec=True),
            mock.patch.object(self.task, "upload_artifacts", autospec=True),
            mock.patch.object(self.task, "cleanup", autospec=True),
            self.assertLogs(self.task.logger) as log,
        ):
            self.assertTrue(self.task._execute())

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.tasks:Work request 1: Fetching input",
                "INFO:debusine.tasks:Work request 1: Configuring for execution",
                "INFO:debusine.tasks:Work request 1: Preparing to run",
                "INFO:debusine.tasks:Work request 1: Running",
                "INFO:debusine.tasks:Work request 1: Checking output",
                "INFO:debusine.tasks:Work request 1: Uploading artifacts",
                "INFO:debusine.tasks:Work request 1: Cleaning up",
            ],
        )
        assert self.task._debug_log_files_directory
        stages_log = (
            Path(self.task._debug_log_files_directory.name) / "stages.log"
        )
        self.assertFalse(stages_log.exists())

    def test_execute_logs_progress_failure(self) -> None:
        """
        _execute() logs each of its main steps: failure.

        For a failure, there is an additional stages.log file.
        """
        self.task.work_request_id = 1

        with (
            mock.patch.object(self.task, "fetch_input", autospec=True),
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(
                self.task, "run", autospec=True, return_value=False
            ),
            mock.patch.object(self.task, "upload_artifacts", autospec=True),
            mock.patch.object(self.task, "cleanup", autospec=True),
            self.assertLogs(self.task.logger) as log,
        ):
            self.assertFalse(self.task._execute())

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.tasks:Work request 1: Fetching input",
                "INFO:debusine.tasks:Work request 1: Configuring for execution",
                "INFO:debusine.tasks:Work request 1: Preparing to run",
                "INFO:debusine.tasks:Work request 1: Running",
                "INFO:debusine.tasks:Work request 1: Checking output",
                "INFO:debusine.tasks:Work request 1: Uploading artifacts",
                "INFO:debusine.tasks:Work request 1: Cleaning up",
            ],
        )
        assert self.task._debug_log_files_directory
        stages_log = (
            Path(self.task._debug_log_files_directory.name) / "stages.log"
        )
        stages_log_lines = []
        for line in stages_log.read_text().splitlines():
            dt, message = line.split(maxsplit=1)
            datetime.datetime.fromisoformat(dt)
            stages_log_lines.append(message)
        self.assertEqual(
            stages_log_lines,
            [
                "Fetching input",
                "Configuring for execution",
                "Preparing to run",
                "Running",
                "Checking output",
                "Uploading artifacts",
                "Cleaning up",
            ],
        )

    def setup_upload_work_request_debug_logs(
        self, source_artifacts_ids: list[int] | None = None
    ) -> None:
        """Setup for upload_work_request_debug_logs tests."""  # noqa: D401
        self.task.debusine = mock.create_autospec(spec=Debusine)

        # Add a file to be uploaded
        with self.task.open_debug_log_file("test.log") as file:
            file.write("log")

        self.task.workspace_name = "System"
        self.task.work_request_id = 5

        if source_artifacts_ids is None:
            self.task._source_artifacts_ids = []
        else:
            self.task._source_artifacts_ids = source_artifacts_ids

    def test_upload_work_request_debug_logs_with_relation(self) -> None:
        """
        Artifact is created and uploaded. Relation is created.

        The relation is from the debug logs artifact to the source_artifact_id.
        """
        remote_artifact_id = 10
        source_artifact_id = 2

        self.setup_upload_work_request_debug_logs([source_artifact_id])

        assert isinstance(self.task.debusine, MagicMock)
        self.task.debusine.upload_artifact.return_value = MagicMock(
            id=remote_artifact_id
        )

        assert self.task._debug_log_files_directory
        work_request_debug_logs_artifact = WorkRequestDebugLogs.create(
            files=Path(self.task._debug_log_files_directory.name).glob("*")
        )
        self.task._upload_work_request_debug_logs()

        self.task.debusine.upload_artifact.assert_called_with(
            work_request_debug_logs_artifact,
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )

        self.task.debusine.relation_create.assert_called_with(
            remote_artifact_id, source_artifact_id, "relates-to"
        )

    def test_upload_work_request_debug_logs(self) -> None:
        """Artifact is created and uploaded."""
        self.setup_upload_work_request_debug_logs()
        assert isinstance(self.task.debusine, MagicMock)

        assert self.task._debug_log_files_directory
        work_request_debug_logs_artifact = WorkRequestDebugLogs.create(
            files=Path(self.task._debug_log_files_directory.name).glob("*")
        )

        self.task._upload_work_request_debug_logs()

        self.task.debusine.upload_artifact.assert_called_with(
            work_request_debug_logs_artifact,
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )

    def test_upload_work_request_no_log_files(self) -> None:
        """No log files: no artifact created."""
        self.task.debusine = mock.create_autospec(spec=Debusine)

        self.task._upload_work_request_debug_logs()

        assert isinstance(self.task.debusine, MagicMock)
        self.task.debusine.upload_artifact.assert_not_called()


class TestsTaskConfigError(TestCase):
    """Tests for TaskConfigError."""

    def test_without_original_exception(self) -> None:
        """No original exception: pass through message."""
        msg = "This is an error message"

        error = TaskConfigError(msg)

        self.assertEqual(str(error), msg)
        self.assertIsNone(error.original_exception)

    def test_str_with_original_exception(self) -> None:
        """Message and original exception: combine both."""
        msg = "This is an error message"
        original_exc = Exception("something")

        error = TaskConfigError(msg, original_exception=original_exc)

        self.assertEqual(
            str(error), f"{msg} (Original exception: {original_exc})"
        )
        self.assertIs(error.original_exception, original_exc)

    def test_None_with_original_exception(self) -> None:
        """Original exception and no message: stringify exception."""
        original_exc = Exception("something")

        error = TaskConfigError(None, original_exception=original_exc)

        self.assertEqual(str(error), str(original_exc))
        self.assertIs(error.original_exception, original_exc)

    def test_add_parent_message(self) -> None:
        """Test add_parent_message."""
        for exc, expected in (
            (TaskConfigError(None), "test"),
            (TaskConfigError("msg"), "test: msg"),
        ):
            exc.add_parent_message("test")
            self.assertEqual(exc.args[0], expected)


class RunCommandTaskForTesting(
    SampleBaseTask[BaseTaskData, BaseDynamicTaskData],
    RunCommandTask[BaseTaskData, BaseDynamicTaskData],
):
    """Used in TaskTests."""

    __test__ = False
    _popen: subprocess.Popen[Any] | None = None
    _pids_directory: Path | None = None

    def __init__(
        self,
        abort_after_aborted_calls: int | float = math.inf,
        wait_popen_timeouts: Iterator[int | float] | None = None,
        wait_popen_sigkill: bool = False,
        pids_directory: Path | None = None,
        wait_for_sigusr1: bool = True,
        send_signal_to_cmd: signal.Signals | None = None,
    ) -> None:
        """
        Initialize object used in the tests.

        :param abort_after_aborted_calls: abort() method will return False
          until this number. Then will return True on each subsequent call
        :param wait_popen_timeouts: a sequence of timeouts to use for
          successive calls to _wait_popen
        :param wait_popen_sigkill: if True the first call to _wait_popen()
          will do super()._wait_popen() AND also kill the process group.
        :param pids_directory: the directory where the PID.log files are
          expected to appear.
        :param wait_for_sigusr1: if True on the first _wait_popen will
          block until SIGUSR1 is received
        :param send_signal_to_cmd: if it's a signal (SIGUSR1, SIGUSR2) will
          run the command normally. When self.aborted is accessed twice it
          will send the signal to the command
        """
        super().__init__({})

        self._abort_after_aborted_calls = abort_after_aborted_calls
        self._wait_popen_timeouts = wait_popen_timeouts or itertools.repeat(
            0.001
        )
        self._wait_popen_sigkill = wait_popen_sigkill
        self._pids_directory = pids_directory
        self._wait_for_sigusr1 = wait_for_sigusr1
        self._send_signal_to_cmd = send_signal_to_cmd

        self._children_initialized = False
        self._popen = None

        self.aborted_call_count = 0

    @property
    def aborted(self) -> bool:
        """
        Return False (not aborted) or True.

        True when has been called more times than abort_after_aborted_calls
        parameter used in the __init__().

        If send_signal_to_cmd it will send the signal to the PID.
        """
        self.aborted_call_count += 1

        if self.aborted_call_count == 2 and self._send_signal_to_cmd:
            assert self._popen
            os.kill(self._popen.pid, self._send_signal_to_cmd)

        return self.aborted_call_count > self._abort_after_aborted_calls

    def _do_wait_for_sigusr1(self) -> None:
        """
        Will wait up to 5 seconds to receive SIGUSR1.

        SIGUSR1 is sent by the signal-logger.sh when the last process
        is spawned.
        """
        if self._children_initialized:
            return

        result = signal.sigtimedwait({signal.SIGUSR1}, 5)

        if result is None:  # pragma: no cover
            # No signal received after the timeout. This should never
            # happen: signal-logger.sh should have sent on time.
            # (the timeout is to fail faster in case that signal-logger.sh
            # is not working)
            raise RuntimeError("SIGUSR1 not received")

        self._children_initialized = True

    def wait_cmd_zombie_process(self) -> None:
        """
        Wait and get the returncode for cmd.

        TaskMixins SIGKILLed cmd. cmd probably was in a non-interruptable
        call in the Kernel and TaskMixins did not wait and cmd is not
        left zombie. Here it collects the returncode, so it is
        not zombie and avoids a Popen.__del__ ResourceWarning
        """
        assert self._popen
        self._popen.wait(timeout=5)

    def _wait_popen(
        self, popen: subprocess.Popen[Any], timeout: float  # noqa: U100
    ) -> int:
        # Calls TaskMixins._wait_popen() with a short timeout

        # Depending on self._wait_popen_sigkill will send a SIGKILL
        # to the process group

        self._popen = popen  # Used by wait_cmd_zombie_process()

        if self._wait_for_sigusr1:
            self._do_wait_for_sigusr1()

        try:
            return super()._wait_popen(popen, next(self._wait_popen_timeouts))
        except subprocess.TimeoutExpired as exc:
            raise exc
        finally:
            if self._wait_popen_sigkill:
                assert self._pids_directory
                pid = int(next(self._pids_directory.glob("*.pid")).stem)

                process_group = os.getpgid(pid)

                os.killpg(process_group, signal.SIGKILL)

                # If _wait_popen is called again: no killing again
                self._wait_popen_sigkill = False

    def _cmdline(self) -> list[str]:
        """Unused abstract method from RunCommandTask."""
        raise NotImplementedError()


class RunCommandTaskTests(
    ExternalTaskHelperMixin[RunCommandTaskForTesting], TestCase
):
    """Tests for RunCommandTask methods in isolation."""

    stdout_output = "Something written to stdout"
    stderr_output = "Something written to stderr"

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.temp_directory = self.create_temporary_directory()
        self.task = RunCommandTaskForTesting()

    def tearDown(self) -> None:
        """Delete temporary directory, if it exists."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def test_cmdline_as_root(self) -> None:
        """_cmdline_as_root return False."""
        self.assertFalse(self.task._cmdline_as_root())

    def test_cmd_env_default(self) -> None:
        """_cmd_env defaults to None."""
        self.assertIsNone(self.task._cmd_env())

    def test_task_succeeded(self) -> None:
        """task_succeeded() in the Mixin return True by default."""
        directory = self.create_temporary_directory()
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_cmdline_raise_not_implemented(self) -> None:
        """Mixin raise NotImplementedError."""
        with self.assertRaises(NotImplementedError):
            self.task._cmdline()

    def test_run_cmd_run_as_root_raise_value_error(self) -> None:
        """run_cmd() raise ValueError: run_as_root requires executor."""
        with (
            self.create_temporary_file().open("wb") as cmd_log,
            self.create_temporary_file().open("wb") as out_file,
        ):
            msg = r"^run_as_root requires an executor$"
            self.assertRaisesRegex(
                ValueError,
                msg,
                self.task._run_cmd,
                ["true"],
                self.create_temporary_directory(),
                env=None,
                run_as_root=True,
                cmd_log=cmd_log,
                out_file=out_file,
            )

    def test_execute_cmdline_logs(self) -> None:
        """_execute(): if _cmdline() raise an exc: log it."""
        msg = "There is some exception"

        exc = Exception(msg)

        self.patch_task("fetch_input")
        self.patch_task("configure_for_execution")
        self.patch_task("_cmdline").side_effect = exc

        self.assertFalse(self.task._execute())

        assert self.task._debug_log_files_directory
        log = (
            Path(self.task._debug_log_files_directory.name) / "execution.log"
        ).read_text()

        self.assertRegex(log, f"^Exception type: Exception\nMessage: {msg}")

        traceback_str = "".join(traceback.format_exception(exc))
        self.assertIn(traceback_str, log)

    def patch_task(self, method: str) -> MagicMock:
        """Patch self.task.{method}, return its mock."""
        patcher = mock.patch.object(self.task, method, autospec=True)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def run_signal_logger(
        self, task: RunCommandTaskForTesting, options: list[str]
    ) -> int | None:
        """Run signal logger in task with options."""
        # Block SIGUSR1 signal in case that it is delivered before
        # _do_wait_for_sigusr1 collects it

        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})

        script_path = self.write_signal_logger()

        try:
            return task.run_cmd(
                [
                    str(script_path),
                    str(self.temp_directory),
                    str(os.getpid()),
                    *options,
                ],
                self.temp_directory,
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

    def run_success_failure(self, task: RunCommandTaskForTesting) -> int | None:
        """Run success/failure script in task."""
        # Block SIGUSR1 signal in case that it is delivered before
        # _do_wait_for_sigusr1 collects it

        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})

        script_path = self.write_success_failure()

        try:
            return task.run_cmd(
                [str(script_path), str(os.getpid())], self.temp_directory
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

    @staticmethod
    def pid_exist_not_zombie(pid: int) -> bool:
        """Return True if the pid exist and is not a zombie process."""
        try:
            status = psutil.Process(pid).status()
            # no cover: depends on the timings of the processes being terminated
            # this line in the test is never hit
            return status != "zombie"  # pragma: no cover
        except psutil.NoSuchProcess:
            return False

    def assert_processes_are_terminated(
        self, expected_number_files_to_check: int
    ) -> None:
        """
        Assert that the PIDs in self.temp_directory don't exist or a zombie.

        :param expected_number_files_to_check: number of expected processes
           (PID files) to check.
        """
        start_time = time.time()
        time_out = 2

        files_to_check = list(self.temp_directory.glob("*.pid"))
        number_of_files_to_check = len(files_to_check)

        self.assertEqual(
            expected_number_files_to_check,
            number_of_files_to_check,
            f"Expected {expected_number_files_to_check} "
            f"processes found {number_of_files_to_check}",
        )

        while True:
            alive_pids = False
            elapsed = time.time() - start_time

            if elapsed > time_out:  # pragma: no cover
                # "no cover" because if the test pass this branch
                # is not used. Not having it the Test would be waiting
                # endless for the processes to disappear
                break

            for file in files_to_check:
                pid = int(file.stem)

                if self.pid_exist_not_zombie(pid):  # pragma: no cover
                    # Usually not triggered: the processes are killed very fast
                    # hence the no cover statement.
                    alive_pids = True
                else:  # pragma: no cover
                    # Usually triggered, "no cover" needed because of the issue
                    # explained in
                    # https://github.com/nedbat/coveragepy/issues/1025
                    pass

            if alive_pids:  # pragma: no cover
                # Usually not triggered because processes are killed very fast
                # hence the no cover statement.
                pass
            else:  # pragma: no cover
                # Usually triggered, "no cover" needed because of the issue
                # explained in https://github.com/nedbat/coveragepy/issues/1025
                break  # All processes are gone

        self.assertLess(
            elapsed, time_out, "Timeout waiting for processes to disappear"
        )

    def test_execute_cmd_cancelled_killed_with_sigterm(self) -> None:
        """Execute script, kills with SIGTERM. No processes running after."""
        self.task = RunCommandTaskForTesting(abort_after_aborted_calls=2)

        # Create three processes
        result = self.run_signal_logger(self.task, ["3"])

        # The processes were killed in RunCommandTask.run_cmd (the task
        # was aborted after two aborted calls)
        self.assert_processes_are_terminated(3)

        self.assertIsNone(result)

    def test_execute_cmd_cancelled_killed_with_sigkill(self) -> None:
        """Execute script, two children, killed by SIGKILL (not by SIGTERM)."""
        self.task = RunCommandTaskForTesting(abort_after_aborted_calls=1)

        result = self.run_signal_logger(self.task, ["2", "TERM"])

        self.assert_processes_are_terminated(2)

        self.assertIsNone(result)

        self.task.wait_cmd_zombie_process()

    def test_execute_cmd_finished_before_killpg_sigterm(self) -> None:
        """Execute script. SIGTERM is called but processes were already dead."""
        self.task = RunCommandTaskForTesting(
            abort_after_aborted_calls=1,
            wait_popen_sigkill=True,
            pids_directory=self.temp_directory,
        )

        result = self.run_signal_logger(self.task, ["2"])

        self.assert_processes_are_terminated(2)

        self.assertIsNone(result)

    def test_execute_cmd_finished_before_killpg_sigkill(self) -> None:
        """Execute script. SIGKILL is called but processes were already dead."""
        self.task = RunCommandTaskForTesting(
            abort_after_aborted_calls=2,
            wait_popen_timeouts=iter([0, 1, 1]),
            wait_popen_sigkill=True,
            pids_directory=self.temp_directory,
        )

        result = self.run_signal_logger(self.task, ["2", "TERM"])

        self.assert_processes_are_terminated(2)

        self.assertIsNone(result)

        assert self.task._debug_log_files_directory
        output_text = (
            Path(self.task._debug_log_files_directory.name)
            / self.task.CMD_LOG_FILENAME
        ).read_text()

        self.assertIn(
            textwrap.dedent(
                """\
            output (contains stdout and stderr):

            aborted: True
            returncode: -9
            """
            ),
            output_text,
        )
        self.assertTrue(output_text.startswith("cmd: "))

    def test_command_finish_with_success(self) -> None:
        """Execute script. No abort, cmd return code is 0 (success)."""
        self.task = RunCommandTaskForTesting(
            abort_after_aborted_calls=math.inf,
            send_signal_to_cmd=signal.SIGHUP,
        )

        returncode = self.run_success_failure(self.task)

        self.assertEqual(returncode, 0)
        assert self.task._debug_log_files_directory
        self.assertTrue(
            (
                Path(self.task._debug_log_files_directory.name)
                / self.task.CMD_LOG_FILENAME
            )
            .read_text()
            .startswith("cmd: ")
        )

    def test_command_finish_with_failure(self) -> None:
        """Execute script. No abort, result and log file existence."""
        self.task = RunCommandTaskForTesting(
            abort_after_aborted_calls=math.inf,
            send_signal_to_cmd=signal.SIGUSR2,
        )

        returncode = self.run_success_failure(self.task)

        self.assertEqual(returncode, 1)
        assert self.task._debug_log_files_directory
        self.assertTrue(
            (
                Path(self.task._debug_log_files_directory.name)
                / self.task.CMD_LOG_FILENAME
            )
            .read_text()
            .startswith("cmd: ")
        )

    def write_success_failure(self) -> Path:
        """
        Write to self.temp_directory a script that traps SIGHUP and SIGUSR2.

        For SIGHUP exits with exitcode == 0, SIGUSR2 exits with exitcode == 1.

        :return: script path
        """
        script_file = Path(self.temp_directory) / "success-failure.sh"

        with script_file.open("w") as f:
            f.write(
                textwrap.dedent(
                    f"""\
                #!/bin/sh

                # Arguments:
                # $1: PID of the process to send a SIGUSR1 when the
                # busy-wait state is reached

                pid_to_notify="$1"

                trap "exit 0" HUP
                trap "exit 1" USR2

                echo "{self.stdout_output}"
                echo "{self.stderr_output}" >&2

                kill -s USR1 $pid_to_notify

                while true
                do
                    true
                    # with a sleep there is a delay until the script
                    # process the signals
                done
                """
                )
            )

        script_file.chmod(0o700)

        return script_file

    def write_signal_logger(self) -> Path:
        """
        Write to self.temp_directory a script to test finishing of processes.

        :return: script path
        """
        script_file = Path(self.temp_directory) / "signal-logger.sh"

        with script_file.open("w") as f:
            f.write(
                textwrap.dedent(
                    """\
            #!/bin/sh

            # Arguments:
            # $1: output directory for the log files. Must exist
            # $2: PID of the process to send a SIGUSR1 when all children
            #     has been created
            # $3: number of generation of processes to launch
            # $4: (optional) If "TERM": traps TERM signal (logs it) and do not
            #                die

            log() {
                echo "$1" >> "$output_directory/$$.pid"
            }

            output_directory="$1"
            pid_to_notify="$2"
            to_spawn="$3"
            term="$4"

            if [ "$term" = "TERM" ]
            then
                trap "log SIGTERM" TERM
            fi

            log "Started $$"

            if [ "$to_spawn" -gt 1 ]
            then
                to_spawn=$((to_spawn-1))
                cmd="$0 $output_directory $pid_to_notify $to_spawn $term"
                $cmd &
                log "$$ launched $!"
            else
                kill -s USR1 $pid_to_notify
            fi

            while true
            do
                sleep 1
            done
            """
                )
            )

        script_file.chmod(0o700)

        return script_file

    def test_run_cmd_uses_executor(self) -> None:
        """_run_cmd() uses executor_instance to run the command."""
        cmd = ["echo", "test"]
        temp_directory = self.create_temporary_directory()
        expected_returncode = 5

        self.task.executor_instance = MagicMock(spec=InstanceInterface)
        self.task.executor_instance.run.return_value.returncode = (
            expected_returncode
        )

        with (
            self.create_temporary_file().open("wb") as cmd_log,
            self.create_temporary_file().open("wb") as out_file,
        ):
            actual_returncode = self.task._run_cmd(
                cmd,
                temp_directory,
                env=None,
                run_as_root=False,
                cmd_log=cmd_log,
                out_file=out_file,
            )

            self.task.executor_instance.run.assert_called_with(
                cmd,
                cwd=temp_directory,
                env=None,
                run_as_root=self.task._cmdline_as_root(),
                stderr=cmd_log.fileno(),
                stdout=out_file.fileno(),
            )

            self.assertEqual(actual_returncode, expected_returncode)

    def test_run_cmd_large_stdout_stderr(self) -> None:
        """
        Ensure that a command generating large output is handled correctly.

        Generating more than 65536 bytes can be problematic depending on
        how subprocess.Popen's stdout/stderr is handed.
        """
        max_bytes = 70_000
        command = [
            sys.executable,
            "-u",
            "-c",
            textwrap.dedent(
                f"""\
                import sys
                for i in range({max_bytes}):
                    sys.stdout.write('o')
                    sys.stderr.write('e')
                """
            ),
        ]

        self.task = RunCommandTaskForTesting(wait_for_sigusr1=False)

        returncode = self.task.run_cmd(command, self.temp_directory)

        self.assertEqual(returncode, 0)
        assert self.task._debug_log_files_directory
        log_file = (
            Path(self.task._debug_log_files_directory.name)
            / self.task.CMD_LOG_FILENAME
        )
        self.assertGreater(log_file.stat().st_size, max_bytes * 2)

    def test_run_cmd_output_appended(self) -> None:
        """If two run commands are used: output log is appended."""
        self.task = RunCommandTaskForTesting(wait_for_sigusr1=False)

        self.task.run_cmd(["true"], self.temp_directory)
        self.task.run_cmd(["false"], self.temp_directory)

        assert self.task._debug_log_files_directory
        log_file = (
            Path(self.task._debug_log_files_directory.name)
            / self.task.CMD_LOG_FILENAME
        )
        contents = log_file.read_text()

        self.assertIn("cmd: true", contents)
        self.assertIn("cmd: false", contents)

    def test_run_cmd_capture_output(self) -> None:
        """When the parameter capture_output=True: stdout is in result.out."""
        stdout_output = "this is the output\nsecond line"
        stderr_output = "this is stderr\nsecond line"
        command = [
            "bash",
            "-c",
            f"echo -e '{stdout_output}'; echo -e '{stderr_output}' >&2",
        ]
        self.task = RunCommandTaskForTesting(
            wait_popen_timeouts=itertools.repeat(5), wait_for_sigusr1=False
        )

        stdout_filename = "stdout.txt"

        returncode = self.task.run_cmd(
            command,
            self.temp_directory,
            capture_stdout_filename=stdout_filename,
        )

        self.assertEqual(returncode, 0)

        stdout_file = self.temp_directory / stdout_filename

        self.assertEqual(stdout_file.read_text(), stdout_output + "\n")

        command_quoted = shlex.join(command)

        expected_log = (
            f"cmd: {command_quoted}\n"
            f"output (contains stderr only, stdout was captured):\n"
            f"{stderr_output}\n\n"
            f"aborted: False\n"
            f"returncode: 0\n"
            f"\n"
            f"Files in working directory:\n"
            f"stdout.txt\n"
            f"{RunCommandTask.CMD_LOG_SEPARATOR}\n"
            f"this is the output\n"
            f"second line\n"
            f"\n"
            f"{RunCommandTask.CMD_LOG_SEPARATOR}\n"
        )

        assert self.task._debug_log_files_directory
        log_file = (
            Path(self.task._debug_log_files_directory.name)
            / self.task.CMD_LOG_FILENAME
        )

        self.assertEqual(log_file.read_text(), expected_log)


class BaseTaskWithExecutorTests(TestCase):
    """Unit tests for BaseTaskWithExecutor class."""

    def setUp(self) -> None:
        """Create the shared attributes."""
        super().setUp()
        self.task = SampleBaseTaskWithExecutor1({})

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def patch_executor(
        self, backend: BackendType, image_category: ExecutorImageCategory
    ) -> MagicMock:
        """Patch executor for `backend` and return its mock."""
        executor = MagicMock(spec=ExecutorInterface)
        executor.image_category = image_category
        patcher = mock.patch.dict(
            "debusine.tasks.executors.base._backends", {backend: executor}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return executor

    def setup_task_with_mocked_executor(
        self,
        task: BaseExternalTask[Any, Any],
        image_category: ExecutorImageCategory,
    ) -> MagicMock:
        """
        Set up common task environment for preparing executor tests.

        Return mock of executor.
        """
        backend = BackendType.UNSHARE

        task.data.backend = backend
        task.debusine = MagicMock(spec=Debusine)
        return self.patch_executor(backend, image_category)

    def test_backend_auto_default_backend(self) -> None:
        """If backend is "auto": BaseTask.backend return DEFAULT_BACKEND."""
        self.task.data.backend = BackendType.AUTO

        self.assertEqual(self.task.backend, self.task.DEFAULT_BACKEND)

    def test_backend_return_backend(self) -> None:
        """BaseTask.backend return the backend."""
        backend = BackendType.INCUS_LXC
        self.task.data.backend = backend

        self.assertEqual(self.task.backend, backend)

    def test_get_environment_int(self) -> None:
        """`get_environment` passes through integer lookups."""
        artifact_info = ArtifactInfo(
            id=1,
            category=ArtifactCategory.TEST,
            data=EmptyArtifactData(),
        )
        task_db = FakeTaskDatabase(single_lookups={(1, None): artifact_info})

        self.assertEqual(self.task.get_environment(task_db, 1), artifact_info)

    def test_get_environment_with_architecture_and_backend(self) -> None:
        """`get_environment` fills in architecture/backend."""
        task = SampleBaseTaskWithExecutorAndArchitecture(
            {
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "amd64",
            }
        )
        assert task.data.environment
        self.patch_executor(BackendType.UNSHARE, ExecutorImageCategory.TARBALL)
        artifact_info = ArtifactInfo(
            id=2,
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(),
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=",
                    None,
                ): artifact_info
            }
        )

        self.assertEqual(
            task.get_environment(task_db, task.data.environment), artifact_info
        )

    def test_get_environment_with_architecture_from_worker(self) -> None:
        """`get_environment` falls back to the worker's host architecture."""
        task = SampleBaseTaskWithExecutorAndArchitecture(
            {
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=bookworm",
            }
        )
        assert task.data.environment
        task.worker_host_architecture = "amd64"
        self.patch_executor(BackendType.UNSHARE, ExecutorImageCategory.IMAGE)
        artifact_info = ArtifactInfo(
            id=3,
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(),
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=image:backend=unshare:variant=",
                    None,
                ): artifact_info
            }
        )

        self.assertEqual(
            task.get_environment(task_db, task.data.environment), artifact_info
        )

    def test_get_environment_with_architecture_missing(self) -> None:
        """`get_environment` skips architecture if it's missing."""
        task = SampleBaseTaskWithExecutorAndArchitecture(
            {
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=bookworm",
            }
        )
        assert task.data.environment
        self.patch_executor(BackendType.UNSHARE, ExecutorImageCategory.IMAGE)
        artifact_info = ArtifactInfo(
            id=3,
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(),
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                (
                    "debian/match:codename=bookworm:format=image:"
                    "backend=unshare:variant=",
                    None,
                ): artifact_info
            }
        )

        self.assertEqual(
            task.get_environment(task_db, task.data.environment), artifact_info
        )

    def test_get_environment_tries_variant(self) -> None:
        """`get_environment` tries the task name as a variant first."""
        task = SampleBaseTaskWithExecutorAndArchitecture(
            {
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "amd64",
            }
        )
        assert task.data.environment
        self.patch_executor(BackendType.UNSHARE, ExecutorImageCategory.TARBALL)
        artifact_info = ArtifactInfo(
            id=2,
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(),
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:"
                    "variant=samplebasetaskwithexecutorandarchitecture",
                    None,
                ): artifact_info,
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=",
                    None,
                ): None,
            }
        )

        self.assertEqual(
            task.get_environment(task_db, task.data.environment), artifact_info
        )

        task_db.single_lookups = {
            (
                "debian/match:codename=bookworm:architecture=amd64:"
                "format=tarball:backend=unshare:variant=",
                None,
            ): artifact_info
        }

        self.assertEqual(
            task.get_environment(task_db, task.data.environment), artifact_info
        )

    def test_get_environment_without_try_variant(self) -> None:
        """`get_environment` can be told not to try variants."""
        task = SampleBaseTaskWithExecutorAndArchitecture(
            {
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "amd64",
            }
        )
        assert task.data.environment
        self.patch_executor(BackendType.UNSHARE, ExecutorImageCategory.TARBALL)
        artifact_info = ArtifactInfo(
            id=2,
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(),
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
                    None,
                ): artifact_info,
            }
        )

        self.assertEqual(
            task.get_environment(
                task_db, task.data.environment, try_variant=False
            ),
            artifact_info,
        )

    def test_prepare_executor(self) -> None:
        """BaseTask._prepare_executor() create an executor, download image."""
        executor_mocked = self.setup_task_with_mocked_executor(
            self.task, ExecutorImageCategory.TARBALL
        )
        executor_instance_mock = executor_mocked.return_value
        self.task.dynamic_data = BaseDynamicTaskDataWithExecutor(
            environment_id=89
        )

        self.task._prepare_executor()

        # Executor is called with the right parameters
        executor_mocked.assert_called_with(self.task.debusine, 89)

        # Executor's download_image() is called
        executor_instance_mock.download_image.assert_called_with()

        # self.task.executor is the executor
        self.assertIs(self.task.executor, executor_instance_mock)

    def test_prepare_executor_instance_executor_is_none(self) -> None:
        """
        BaseTask._prepare_executor_instance() create an instance, set it.

        Task.executor was None: create the executor as well.
        """
        self.task.executor = None
        executor_mocked = self.setup_task_with_mocked_executor(
            self.task, ExecutorImageCategory.TARBALL
        )
        self.task.dynamic_data = BaseDynamicTaskDataWithExecutor(
            environment_id=89
        )

        # executor_mocked.create() will be called, set up mock to verify
        executor_instance_mock = MagicMock(spec=InstanceInterface)
        executor_mocked.return_value.create.return_value = (
            executor_instance_mock
        )

        self.task._prepare_executor_instance()

        executor_mocked.assert_called()
        self.assertIs(self.task.executor_instance, executor_instance_mock)
        executor_instance_mock.start.assert_called_with()

    def test_prepare_executor_instance(self) -> None:
        """BaseTask._prepare_executor_instance() create an instance, set it."""
        self.task.executor = MagicMock(spec=ExecutorInterface)
        executor_instance_mock = MagicMock(spec=InstanceInterface)
        self.task.executor.create.return_value = executor_instance_mock

        self.task._prepare_executor_instance()

        # In this case, executor_mocked should not be called
        self.task.executor.assert_not_called()

        self.assertIs(self.task.executor_instance, executor_instance_mock)
        executor_instance_mock.start.assert_called_with()

    def test_execute_call_directory_push(self) -> None:
        """_execute(): self.executor is not None: call directory_push()."""
        self.task.executor_instance = MagicMock(spec=InstanceInterface)

        with (
            mock.patch.object(
                self.task, "fetch_input", autospec=True, return_value=True
            ) as fetch_input_mocked,
            mock.patch.object(
                self.task, "configure_for_execution", autospec=True
            ),
            mock.patch.object(
                self.task, "run", autospec=True, return_value=True
            ),
            mock.patch.object(self.task, "upload_artifacts", autospec=True),
        ):
            self.task._execute()

        download_directory = fetch_input_mocked.call_args[0][0]
        self.task.executor_instance.directory_push.assert_called_once_with(
            download_directory,
            Path("/tmp"),
            user=self.task.executor_instance.non_root_user,
            group=self.task.executor_instance.non_root_user,
        )

    def test_execute_shuts_down_remaining_executors(self) -> None:
        """_execute() self.executor is not None: shut down executor."""
        self.task.executor_instance = MagicMock(spec=InstanceInterface)
        self.task.executor_instance.run.return_value.returncode = 0
        self.task.executor_instance.is_started.return_value = True

        with (
            mock.patch.object(
                self.task, "fetch_input", autospec=True, return_value=True
            ),
            mock.patch.object(self.task, "configure_for_execution"),
            mock.patch.object(
                self.task, "run", autospec=True, return_value=True
            ),
            mock.patch.object(self.task, "upload_artifacts", autospec=True),
        ):
            self.task._execute()

        self.task.executor_instance.stop.assert_called_once()

    def test_run_executor_command(self) -> None:
        """run_executor_command() runs the command."""
        self.task.executor_instance = MagicMock(spec=InstanceInterface)
        self.task.executor_instance.run.return_value.returncode = 0
        self.task.executor_instance.is_started.return_value = True

        self.task.run_executor_command(["true"], "prepare.log", check=True)

        self.task.executor_instance.run.assert_called_once_with(
            ["true"],
            run_as_root=False,
            stdout=mock.ANY,
            stderr=mock.ANY,
            check=True,
        )

        expected_log = "Executing: true\nExecution completed (exit code 0)\n"

        assert self.task._debug_log_files_directory
        log_file = (
            Path(self.task._debug_log_files_directory.name) / "prepare.log"
        )

        self.assertEqual(log_file.read_text(), expected_log)


class TestTestTaskMixin(TestCase):
    """Test TestTaskMixin."""

    def test_label(self) -> None:
        """Test get_label."""
        task = SampleBaseTask1({})
        self.assertEqual(task.get_label(), "test")


class DefaultDynamicDataTest(TestCase):
    """Test DefaultDynamicData."""

    def test_dynamic_data(self) -> None:
        """Test build_dynamic_data."""
        task = SampleDefaultDynamicData({})
        self.assertEqual(
            task.compute_dynamic_data(FakeTaskDatabase()), BaseDynamicTaskData()
        )


class ExtraRepositoryMixinTests(TestCase):
    """Unit tests for BaseTaskWithExecutor class."""

    def setUp(self) -> None:
        """Create the shared attributes."""
        super().setUp()
        self.task = SampleExtraRepositoryMixin({})
        self.directory = self.create_temporary_directory()

    def configure_extra_repositories(
        self, *extra_repositories: dict[str, Any]
    ) -> None:
        """Parse a list of dictionaries into ExtraRepository objects."""
        self.task.data.extra_repositories = []
        for repo in extra_repositories:
            self.task.data.extra_repositories.append(
                ExtraRepository.parse_obj(repo)
            )

    def test_supports_deb822_sources(self) -> None:
        """Test supports_deb822_sources with 4 sample distributions."""
        self.assertFalse(self.task.supports_deb822_sources("jessie"))
        self.assertFalse(self.task.supports_deb822_sources("trusty"))
        self.assertTrue(self.task.supports_deb822_sources("stretch"))
        self.assertTrue(self.task.supports_deb822_sources("xenial"))

    def test_supports_inline_signed_by(self) -> None:
        """Test supports_inline_signed_by with 4 sample distributions."""
        self.assertFalse(self.task.supports_deb822_sources("jessie"))
        self.assertFalse(self.task.supports_deb822_sources("trusty"))
        self.assertTrue(self.task.supports_deb822_sources("bookworm"))
        self.assertTrue(self.task.supports_deb822_sources("jammy"))

    def test_write_extra_repository_config_jessie_no_keys(self) -> None:
        """Test repositories without deb822 or keys."""
        self.configure_extra_repositories(
            {
                "url": "http://example.net",
                "suite": "bookworm",
                "components": ["main"],
            },
        )

        self.task.write_extra_repository_config("jessie", self.directory)

        self.assertEqual(self.task.extra_repository_keys, [])
        self.assertEqual(self.task.extra_repository_sources, [])

    def test_write_extra_repository_config_two_keys(self) -> None:
        """Test repositories with two keys."""
        self.configure_extra_repositories(
            {
                "url": "http://example.net/",
                "suite": "a",
                "components": ["a"],
                "signing_key": "KEY A",
            },
            {
                "url": "http://example.net/",
                "suite": "b/",
                "signing_key": "KEY B",
            },
        )

        self.task.write_extra_repository_config("jessie", self.directory)

        self.assertEqual(len(self.task.extra_repository_keys), 2)
        expected_keys = ["KEY A\n", "KEY B\n"]
        for key_file, expected_key in zip(
            self.task.extra_repository_keys, expected_keys
        ):
            self.assertEqual(key_file.read_text(), expected_key)

        self.assertEqual(self.task.extra_repository_sources, [])

    def test_write_extra_repository_config_deb822_signed_keyring(self) -> None:
        """Test repositories in deb822 format with keys in apt < 2.3.10."""
        self.configure_extra_repositories(
            {
                "url": "http://example.net",
                "suite": "bullseye",
                "components": ["main", "non-free"],
                "signing_key": "\n".join(
                    (
                        "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                        "",
                        "ABCDEFGHI",
                        "-----END PGP PUBLIC KEY BLOCK-----",
                    )
                ),
            },
        )

        self.task.write_extra_repository_config("bullseye", self.directory)

        self.assertEqual(len(self.task.extra_repository_keys), 1)
        self.assertEqual(
            self.task.extra_repository_keys[0].read_text(),
            textwrap.dedent(
                """\
                -----BEGIN PGP PUBLIC KEY BLOCK-----

                ABCDEFGHI
                -----END PGP PUBLIC KEY BLOCK-----
                """
            ),
        )
        self.assertEqual(
            self.task.extra_repository_keys[0].name,
            "extra_apt_key_0.asc",
        )
        self.assertEqual(len(self.task.extra_repository_sources), 1)
        self.assertEqual(
            self.task.extra_repository_sources[0].read_text(),
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.net
                Suites: bullseye
                Components: main non-free
                Signed-By: /etc/apt/keyrings/extra_apt_key_0.asc
                """
            ),
        )

    def test_write_extra_repository_config_deb822_signed_inline(self) -> None:
        """Test repositories in deb822 format with keys inline."""
        self.configure_extra_repositories(
            {
                "url": "http://example.net",
                "suite": "bookworm",
                "components": ["main", "non-free"],
                "signing_key": "\n".join(
                    (
                        "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                        "",
                        "ABCDEFGHI",
                        "-----END PGP PUBLIC KEY BLOCK-----",
                    )
                ),
            },
        )

        self.task.write_extra_repository_config("bookworm", self.directory)

        self.assertEqual(self.task.extra_repository_keys, [])
        self.assertEqual(len(self.task.extra_repository_sources), 1)
        self.assertEqual(
            self.task.extra_repository_sources[0].read_text(),
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.net
                Suites: bookworm
                Components: main non-free
                Signed-By:
                 -----BEGIN PGP PUBLIC KEY BLOCK-----
                 .
                 ABCDEFGHI
                 -----END PGP PUBLIC KEY BLOCK-----
                """
            ),
        )

    def test_iter_oneline_sources(self) -> None:
        """Test iter_oneline_sources with a variety of sources."""
        self.configure_extra_repositories(
            {
                "url": "http://example.net",
                "suite": "flat/",
            },
            {
                "url": "http://example.com",
                "suite": "bookworm",
                "components": ["main", "non-free"],
            },
            {
                "url": "http://example.org",
                "suite": "buster",
                "components": ["main", "non-free"],
                "signing_key": "\n".join(
                    (
                        "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                        "",
                        "ABCDEFGHI",
                        "-----END PGP PUBLIC KEY BLOCK-----",
                    )
                ),
            },
        )
        sources = list(self.task.iter_oneline_sources())
        self.assertEqual(
            sources,
            [
                "deb http://example.net flat/",
                "deb http://example.com bookworm main non-free",
                (
                    "deb [signed-by=/etc/apt/keyrings/extra_apt_key_2.asc] "
                    "http://example.org buster main non-free"
                ),
            ],
        )

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        self.assertEqual(self.task.get_input_artifacts_ids(), [])
