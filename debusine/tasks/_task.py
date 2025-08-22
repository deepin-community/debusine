# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
r"""
Collection of tasks.

The debusine.tasks module hierarchy hosts a collection of
:py:class:`BaseTask` that are used by workers to fulfill
:py:class:`debusine.db.models.WorkRequest`\ s sent by the debusine
scheduler.

Creating a new task requires adding a new file containing a class inheriting
from the :py:class:`BaseTask` or :py:class:`RunCommandTask` base class. The
name of the class must be unique among all child classes.

A child class must, at the very least, override the :py:meth:`BaseTask.execute`
method.
"""
import inspect
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import traceback
from abc import ABCMeta, abstractmethod
from collections import defaultdict
from collections.abc import Collection, Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    AnyStr,
    BinaryIO,
    ClassVar,
    Generic,
    IO,
    Literal,
    Self,
    TYPE_CHECKING,
    TextIO,
    TypeVar,
    Union,
    overload,
)

if TYPE_CHECKING:
    from _typeshed import OpenBinaryModeWriting, OpenTextModeWriting

from debusine.artifacts import WorkRequestDebugLogs
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse, RelationType
from debusine.tasks.executors import (
    ExecutorImageCategory,
    ExecutorInterface,
    InstanceInterface,
    executor_class,
)
from debusine.tasks.models import (
    ActionRecordInTaskHistory,
    BackendType,
    BaseDynamicTaskData,
    BaseDynamicTaskDataWithExecutor,
    BaseTaskData,
    BaseTaskDataWithExecutor,
    BaseTaskDataWithExtraRepositories,
    EventReaction,
    LookupSingle,
    WorkerType,
    build_key_value_lookup_segment,
    build_lookup_string_segments,
    parse_key_value_lookup_segment,
    parse_lookup_string_segments,
)
from debusine.tasks.server import ArtifactInfo, TaskDatabaseInterface
from debusine.utils import extract_generic_type_arguments


class TaskConfigError(Exception):
    """Exception raised when there is an issue with a task configuration."""

    def __init__(
        self, message: str | None, original_exception: Exception | None = None
    ):
        """
        Initialize the TaskConfigError.

        :param message: human-readable message describing the error.
        :param original_exception: the exception that triggered this error,
          if applicable. This is used to provide additional information.
        """
        super().__init__(message)
        self.original_exception = original_exception

    def add_parent_message(self, msg: str) -> None:
        """Prepend the error message with one from the containing scope."""
        if self.args[0]:
            self.args = (f"{msg}: {self.args[0]}",)
        else:
            self.args = (msg,)

    def __str__(self) -> str:
        """
        Return a string representation.

        If an original exception is present, its representation is appended
        to the message for additional context.
        """
        if self.original_exception:
            if self.args[0] is not None:
                return (
                    f"{self.args[0]} (Original exception: "
                    f"{self.original_exception})"
                )
            else:
                return str(self.original_exception)
        else:
            return str(self.args[0])


TD = TypeVar("TD", bound=BaseTaskData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class BaseTask(Generic[TD, DTD], metaclass=ABCMeta):
    """
    Base class for tasks.

    A BaseTask object serves two purpose: encapsulating the logic of what
    needs to be done to execute the task (cf :py:meth:`configure`
    and :py:meth:`execute` that are run on a worker), and supporting the
    scheduler by determining if a task is suitable for a given worker. That is
    done in a two-step process, collating metadata from each worker (with the
    :py:meth:`analyze_worker` method that is run on a worker) and then,
    based on this metadata, see if a task is suitable (with
    :py:meth:`can_run_on` that is executed on the scheduler).

    Most concrete task implementations should inherit from
    :py:class:`RunCommandTask` instead.
    """

    #: Class used as the in-memory representation of task data.
    task_data_type: type[TD]
    data: TD

    #: Class used as the in-memory representation of dynamic task data.
    dynamic_task_data_type: type[DTD]
    dynamic_data: DTD | None

    #: Must be overridden by child classes to document the current version of
    #: the task's code. A task will only be scheduled on a worker if its task
    #: version is the same as the one running on the scheduler.
    TASK_VERSION: int | None = None

    #: The worker type must be suitable for the task type.  TaskTypes.WORKER
    #: requires an external worker; TaskTypes.SERVER requires a Celery
    #: worker; TaskTypes.SIGNING requires a signing worker.
    TASK_TYPE: TaskTypes

    name: ClassVar[str]
    _sub_tasks: dict[TaskTypes, dict[str, type["BaseTask['Any', 'Any']"]]] = (
        defaultdict(dict)
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Register the subclass into BaseTask._sub_tasks.

        Used by BaseTask.class_from_name() to return the class given the name.
        """
        super().__init_subclass__(**kwargs)

        # The name of the task. It is computed by converting the class name
        # to lowercase.
        cls.name = getattr(cls, "TASK_NAME", cls.__name__.lower())

        # The task data types, computed by introspecting the type arguments
        # used to specialize this generic class.
        [
            cls.task_data_type,
            cls.dynamic_task_data_type,
        ] = extract_generic_type_arguments(cls, BaseTask)

        if inspect.isabstract(cls):
            # Don't list abstract base classes as tasks.
            return

        registry = cls._sub_tasks[cls.TASK_TYPE]

        # The same sub-task could register twice
        # (but assert that is the *same* class, not a different
        # subtask with a name with a different capitalisation)
        if cls.name in registry and registry[cls.name] != cls:
            raise AssertionError(f'Two Tasks with the same name: {cls.name!r}')

        # Make sure SERVER and WORKER do not have conflicting task names
        match cls.TASK_TYPE:
            case TaskTypes.SERVER:
                if cls.name in cls._sub_tasks[TaskTypes.WORKER]:
                    raise AssertionError(
                        f'{cls.name!r} already registered as a Worker task'
                    )
            case TaskTypes.WORKER:
                if cls.name in cls._sub_tasks[TaskTypes.SERVER]:
                    raise AssertionError(
                        f'{cls.name!r} already registered as a Server task'
                    )

        registry[cls.name] = cls

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the task."""
        #: Validated task data submitted through :py:meth:`configure` without
        # BaseTask generic data
        self._configure(task_data, dynamic_task_data)

        #: A :py:class:`logging.Logger` instance that can be used in child
        #: classes when you override methods to implement the task.
        self.logger = logging.getLogger("debusine.tasks")

        # Task is aborted: the task does not need to be executed, and can be
        # stopped if it is already running
        self._aborted = False

        self.work_request_id: int | None = None

        # Workspace is used when uploading artifacts.
        # If it's None: the artifacts are created in the default workspace.
        # When the worker instantiates the task it should set
        # self.workspace_name (see issue #186).
        self.workspace_name: str | None = None

        # The worker's host architecture.  If set, this is used as a
        # fallback for tasks that do not specify their own host
        # architecture.
        self.worker_host_architecture: str | None = None

        # fetch_input() add the downloaded artifacts. Used by
        # `BaseTask._upload_work_request_debug_logs()` and maybe by
        # required method `upload_artifacts()`.
        #
        # This is distinct from get_input_artifacts_ids, which is used to
        # extract IDs from dynamic_data for use by UI views
        self._source_artifacts_ids: list[int] = []

        self._debug_log_files_directory: None | (
            tempfile.TemporaryDirectory[str]
        ) = None

    def instantiate_with_new_data(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> Self:
        """Create a new task like this one, with different data."""
        new_task = self.__class__(task_data, dynamic_task_data)
        new_task.work_request_id = self.work_request_id
        new_task.workspace_name = self.workspace_name
        new_task.worker_host_architecture = self.worker_host_architecture
        return new_task

    def append_to_log_file(self, filename: str, lines: list[str]) -> None:
        """
        Open log file and write contents into it.

        :param filename: use self.open_debug_log_file(filename)
        :param lines: write contents to the logfile
        """
        with self.open_debug_log_file(filename) as file:
            file.writelines([line + "\n" for line in lines])

    @staticmethod
    def ensure_artifact_categories(
        *,
        configuration_key: str,
        category: str,
        expected: Collection[ArtifactCategory],
    ) -> None:
        """
        Validate that the artifact's category is one of the expected categories.

        :param configuration_key: Optional key to identify the source of the
          artifact. Provides additional context in error messages.
        :param category: The category to validate.
        :param expected: A collection of valid artifact categories.
        """
        valid_categories = [cat.value for cat in sorted(expected)]
        if category not in valid_categories:
            raise TaskConfigError(
                f"{configuration_key}: unexpected artifact "
                f"category: '{category}'. "
                f"Valid categories: {valid_categories}"
            )

    @staticmethod
    def ensure_collection_category(
        *, configuration_key: str, category: str, expected: CollectionCategory
    ) -> None:
        """
        Validate that the collection's category is as expected.

        :param configuration_key: Optional key to identify the source of the
          artifact. Provides additional context in error messages.
        :param category: The category to validate.
        :param expected: The expected collection category.
        """
        if category != expected:
            raise TaskConfigError(
                f"{configuration_key}: unexpected collection category: "
                f"'{category}'. Expected: '{expected}'"
            )

    @overload
    def open_debug_log_file(
        self, filename: str, *, mode: "OpenTextModeWriting" = "a"
    ) -> TextIO: ...

    @overload
    def open_debug_log_file(
        self, filename: str, *, mode: "OpenBinaryModeWriting"
    ) -> BinaryIO: ...

    def open_debug_log_file(
        self,
        filename: str,
        *,
        mode: Union["OpenTextModeWriting", "OpenBinaryModeWriting"] = "a",
    ) -> IO[Any]:
        """
        Open a temporary file and return it.

        The files are always for the same temporary directory, calling it twice
        with the same file name will open the same file.

        The caller must call .close() when finished writing.
        """
        if self._debug_log_files_directory is None:
            self._debug_log_files_directory = tempfile.TemporaryDirectory(
                prefix="debusine-task-debug-log-files-"
            )

        debug_file = Path(self._debug_log_files_directory.name) / filename
        return debug_file.open(mode)

    @classmethod
    def prefix_with_task_name(cls, text: str) -> str:
        """:return: the ``text`` prefixed with the task name and a colon."""
        if cls.TASK_TYPE is TaskTypes.WORKER:
            # Worker tasks are left unprefixed for compatibility
            return f"{cls.name}:{text}"
        else:
            return f"{cls.TASK_TYPE.lower()}:{cls.name}:{text}"

    @classmethod
    def analyze_worker(cls) -> dict[str, Any]:
        """
        Return dynamic metadata about the current worker.

        This method is called on the worker to collect information about the
        worker. The information is stored as a set of key-value pairs in a
        dictionary.

        That information is then reused on the scheduler to be fed to
        :py:meth:`can_run_on` and determine if a task is suitable to be
        executed on the worker.

        Derived objects can extend the behaviour by overriding
        the method, calling ``metadata = super().analyze_worker()``,
        and then adding supplementary data in the dictionary.

        To avoid conflicts on the names of the keys used by different tasks
        you should use key names obtained with
        ``self.prefix_with_task_name(...)``.

        :return: a dictionary describing the worker.
        :rtype: dict.
        """
        version_key_name = cls.prefix_with_task_name("version")
        return {
            version_key_name: cls.TASK_VERSION,
        }

    @classmethod
    def analyze_worker_all_tasks(cls) -> dict[str, Any]:
        """
        Return dictionary with metadata for each task in BaseTask._sub_tasks.

        Subclasses of BaseTask get registered in BaseTask._sub_tasks. Return
        a dictionary with the metadata of each of the subtasks.

        This method is executed in the worker when submitting the dynamic
        metadata.
        """
        metadata = {}

        for registry in cls._sub_tasks.values():
            for task_class in registry.values():
                metadata.update(task_class.analyze_worker())

        return metadata

    def host_architecture(self) -> str | None:
        """
        Return host_architecture.

        Tasks where host_architecture is not determined by
        self.data.host_architecture should re-implement this method.
        """
        task_architecture = getattr(self.data, "host_architecture", None)
        if task_architecture is not None:
            assert isinstance(task_architecture, str)
            return task_architecture
        return self.worker_host_architecture

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """
        Check if the specified worker can run the task.

        This method shall take its decision solely based on the supplied
        ``worker_metadata`` and on the configured task data (``self.data``).

        The default implementation always returns True unless
        :py:attr:`TASK_TYPE` doesn't match the worker type or there's a
        mismatch between the :py:attr:`TASK_VERSION` on the scheduler side
        and on the worker side.

        Derived objects can implement further checks by overriding the method
        in the following way::

            if not super().can_run_on(worker_metadata):
                return False

            if ...:
                return False

            return True

        :param dict worker_metadata: The metadata collected from the worker by
            running :py:meth:`analyze_worker` on all the tasks on the worker
            under consideration.
        :return: the boolean result of the check.
        :rtype: bool.
        """
        worker_type = worker_metadata.get("system:worker_type")
        if (self.TASK_TYPE, worker_type) not in {
            (TaskTypes.WORKER, WorkerType.EXTERNAL),
            (TaskTypes.SERVER, WorkerType.CELERY),
            (TaskTypes.SIGNING, WorkerType.SIGNING),
        }:
            return False

        version_key_name = self.prefix_with_task_name("version")

        if worker_metadata.get(version_key_name) != self.TASK_VERSION:
            return False

        # Some tasks might not have "host_architecture"
        task_architecture = self.host_architecture()

        if (
            task_architecture is not None
            and task_architecture
            not in worker_metadata.get("system:architectures", [])
        ):
            return False

        return True

    @abstractmethod
    def build_dynamic_data(self, task_database: TaskDatabaseInterface) -> DTD:
        """
        Build a dynamic task data structure for this task.

        :param task_database: TaskDatabaseInterface to use for lookups
        :returns: the newly created dynamic task data
        """

    def compute_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> DTD:
        """
        Compute dynamic data for this task.

        This may involve resolving artifact lookups.
        """
        return self.build_dynamic_data(task_database)

    def _configure(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Configure the task with the supplied ``task_data``.

        The supplied data is validated against the pydantic data model for the
        type of task being configured. If validation fails, a TaskConfigError
        is raised. Otherwise, the `data` attribute is filled with the supplied
        `task_data`.

        :param dict task_data: The supplied data describing the task.
        :raises TaskConfigError: if the dict does not validate.
        """
        try:
            self.data = self.task_data_type(**task_data)
            self.dynamic_data = (
                None
                if dynamic_task_data is None
                else self.dynamic_task_data_type(**dynamic_task_data)
            )
        except ValueError as exc:
            raise TaskConfigError(None, original_exception=exc)

    def execute_logging_exceptions(self) -> bool:
        """Execute self.execute() logging any raised exceptions."""
        try:
            return self.execute()
        except Exception as exc:
            self.logger.exception("Exception in Task %s", self.name)
            raise exc

    def execute(self) -> bool:
        """
        Call the _execute() method, upload debug artifacts.

        See _execute() for more information.

        :return: result of the _execute() method.
        """  # noqa: D402
        result = self._execute()

        self._upload_work_request_debug_logs()

        return result

    @abstractmethod
    def _execute(self) -> bool:
        """
        Execute the requested task.

        The task must first have been configured. It is allowed to take
        as much time as required. This method will only be run on a worker. It
        is thus allowed to access resources local to the worker.

        It is recommended to fail early by raising a :py:exc:TaskConfigError if
        the parameters of the task let you anticipate that it has no chance of
        completing successfully.

        :return: True to indicate success, False for a failure.
        :rtype: bool.
        :raises TaskConfigError: if the parameters of the work request are
            incompatible with the worker.
        """

    def abort(self) -> None:
        """Task does not need to be executed. Once aborted cannot be changed."""
        self._aborted = True

    @property
    def aborted(self) -> bool:
        """
        Return if the task is aborted.

        Tasks cannot transition from aborted -> not-aborted.
        """
        return self._aborted

    @staticmethod
    def class_from_name(
        task_type: TaskTypes, task_name: str
    ) -> type["BaseTask['Any', 'Any']"]:
        """
        Return class for :param task_name (case-insensitive).

        :param task_type: type of task to look up

        __init_subclass__() registers BaseTask subclasses' into
        BaseTask._sub_tasks.
        """
        if (registry := BaseTask._sub_tasks.get(task_type)) is None:
            raise ValueError(f"{task_type!r} is not a registered task type")

        task_name_lowercase = task_name.lower()
        if (cls := registry.get(task_name_lowercase)) is None:
            raise ValueError(
                f"{task_name_lowercase!r} is not a registered"
                f" {task_type} task_name"
            )

        return cls

    @staticmethod
    def is_valid_task_name(task_type: TaskTypes, task_name: str) -> bool:
        """Return True if task_name is registered (its class is imported)."""
        if (registry := BaseTask._sub_tasks.get(task_type)) is None:
            return False
        return task_name.lower() in registry

    @staticmethod
    def task_names(task_type: TaskTypes) -> list[str]:
        """Return list of sub-task names."""
        return sorted(BaseTask._sub_tasks[task_type])

    @staticmethod
    def is_worker_task(task_name: str) -> bool:
        """Check if task_name is a task that can run on external workers."""
        return task_name.lower() in BaseTask._sub_tasks[TaskTypes.WORKER]

    @staticmethod
    def worker_task_names() -> list[str]:
        """Return list of sub-task names not of type TaskTypes.SERVER."""
        return sorted(BaseTask._sub_tasks[TaskTypes.WORKER].keys())

    @abstractmethod
    def _upload_work_request_debug_logs(self) -> None:
        """
        Create a WorkRequestDebugLogs artifact and upload the logs.

        The logs might exist in self._debug_log_files_directory and were
        added via self.open_debug_log_file() or self.create_debug_log_file().

        For each self._source_artifacts_ids: create a relation from
        WorkRequestDebugLogs to source_artifact_id.
        """

    @abstractmethod
    def get_input_artifacts_ids(self) -> list[int]:
        """
        Return the list of input artifact IDs used by this task.

        This refers to the artifacts actually used by the task. If
        dynamic_data is empty, this returns the empty list.

        This is used by views to show what artifacts were used by a task.
        `_source_artifacts_ids` cannot be used for this purpose because it is
        only set during task execution.
        """

    @abstractmethod
    def get_label(self) -> str:
        """
        Return a short human-readable label for the task.

        :return: None if no label could be computed from task data
        """

    def get_subject(self) -> str | None:
        """Return the subject if known or None."""
        if self.dynamic_data is not None:
            return self.dynamic_data.subject

        return None

    def get_event_reactions(
        self,
        event_name: Literal[  # noqa: U100
            "on_creation",
            "on_unblock",
            "on_assignment",
            "on_success",
            "on_failure",
        ],
    ) -> list[EventReaction]:
        """
        Return event reactions for this task.

        This allows tasks to provide actions that are processed by the
        server at various points in the lifecycle of the work request.
        """
        event_reactions: list[EventReaction] = []
        if event_name in {"on_success", "on_failure"}:
            event_reactions.append(ActionRecordInTaskHistory())
        return event_reactions


class BaseExternalTask(BaseTask[TD, DTD], Generic[TD, DTD], metaclass=ABCMeta):
    r"""
    A :py:class:`BaseTask` that runs on an external worker.

    Concrete subclasses must implement:

    * ``run(execute_directory: Path) -> bool``: Do the main work of the
      task.

    Most concrete subclasses should also implement:

    * ``fetch_input(self, destination) -> bool``. Download the needed
      artifacts into destination. Suggestion: can use
      ``fetch_artifact(artifact, dir)`` to download them.
      (default: return True)
    * ``configure_for_execution(self, download_directory: Path) -> bool``
      (default: return True)
    * ``check_directory_for_consistency_errors(self, build_directory: Path)
      -> list[str]``
      (default: return an empty list, indicating no errors)
    * ``upload_artifacts(self, directory: Path, \*, execution_success: bool)``.
      The member variable self._source_artifacts_ids is set by
      ``fetch_input()`` and can be used to create the relations between
      uploaded artifacts and downloaded artifacts.
      (default: return True)
    """

    TASK_TYPE = TaskTypes.WORKER

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the task."""
        super().__init__(task_data, dynamic_task_data)

        self.debusine: Debusine | None = None

        self.executor: ExecutorInterface | None = None
        self.executor_instance: InstanceInterface | None = None

    def configure_server_access(self, debusine: Debusine) -> None:
        """Set the object to access the server."""
        self.debusine = debusine

    @staticmethod
    @contextmanager
    def _temporary_directory() -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory(
            prefix="debusine-fetch-exec-upload-"
        ) as directory:
            yield Path(directory)

    def _log_exception(self, exc: Exception, stage: str) -> None:
        """Log an unexpected exception into the task log for stage."""
        exc_type = type(exc).__name__
        exc_traceback = traceback.format_exc()

        log_message = [
            f"Exception type: {exc_type}",
            f"Message: {exc}",
            "",
            exc_traceback,
        ]

        self.append_to_log_file(f"{stage}.log", log_message)

    def fetch_artifact(
        self, artifact_id: int, destination: Path
    ) -> ArtifactResponse:
        """
        Download artifact_id to destination.

        Add artifact_id to self._source_artifacts_ids.
        """
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        artifact_response = self.debusine.download_artifact(
            artifact_id, destination, tarball=False
        )
        self._source_artifacts_ids.append(artifact_id)
        return artifact_response

    def fetch_input(self, destination: Path) -> bool:  # noqa: U100
        """
        Download artifacts needed by the task, update self.source_artifacts_ids.

        Task might use self.data.input to download the relevant artifacts.

        The method self.fetch_artifact(artifact, destination) might be used
        to download the relevant artifacts and update
        self.source_artifacts_ids.
        """
        return True

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """
        Configure task: set variables needed for the self._cmdline().

        Called after the files are downloaded via `fetch_input()`.
        """
        return True

    def prepare_to_run(
        self, download_directory: Path, execute_directory: Path  # noqa: U100
    ) -> None:
        """Prepare the execution environment to do the main work of the task."""
        # Do nothing by default.

    @abstractmethod
    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Do the main work of the task."""

    def check_directory_for_consistency_errors(
        self, build_directory: Path  # noqa: U100
    ) -> list[str]:
        """Return list of errors after doing the main work of the task."""
        return []

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload the artifacts for the task."""
        # Do nothing by default.

    def cleanup(self) -> None:
        """Clean up after running the task."""
        # Do nothing by default.

    def _execute(self) -> bool:  # noqa: C901
        """
        Fetch the required input, execute the command and upload artifacts.

        Flow is:

        - Call ``self.fetch_input():`` download the artifacts
        - Call ``self.configure_for_execution()``: to set any member variables
          that might be used in ``self._cmdline()``
        - Call ``self.prepare_to_run()`` to prepare the execution
          environment to do the main work of the task
        - Call ``self.run()`` to do the main work of the task
        - Call ``self.check_directory_for_consistency_errors()``. If any
          errors are returned save them into consistency.log.
        - Call ``self.upload_artifacts(exec_dir, execution_success=succeeded)``.
        - Return ``execution_succeeded`` (the Worker will set the result as
          "Success" or "Failure")
        """
        with (
            self._temporary_directory() as execute_directory,
            self._temporary_directory() as download_directory,
            self.open_debug_log_file("stages.log") as stages_log,
        ):

            def log_stage(message: str) -> None:
                """Log a stage, both to the worker log and to a WR debug log."""
                self.logger.info(
                    "Work request %s: %s", self.work_request_id, message
                )
                print(
                    f"{datetime.now(timezone.utc).isoformat()} {message}",
                    file=stages_log,
                )

            try:
                log_stage("Fetching input")
                if not self.fetch_input(download_directory):
                    return False
            except Exception as exc:
                self._log_exception(exc, stage="fetch_input")
                return False

            try:
                log_stage("Configuring for execution")
                if not self.configure_for_execution(download_directory):
                    return False
            except Exception as exc:
                self._log_exception(exc, stage="configure_for_execution")
                return False

            try:
                try:
                    log_stage("Preparing to run")
                    self.prepare_to_run(download_directory, execute_directory)

                    log_stage("Running")

                    execution_succeeded = self.run(execute_directory)
                except Exception as exc:
                    self._log_exception(exc, stage="execution")
                    return False

                try:
                    log_stage("Checking output")
                    if errors := self.check_directory_for_consistency_errors(
                        execute_directory
                    ):
                        self.append_to_log_file(
                            "consistency.log", sorted(errors)
                        )
                        return False

                    log_stage("Uploading artifacts")
                    self.upload_artifacts(
                        execute_directory, execution_success=execution_succeeded
                    )
                except Exception as exc:
                    self._log_exception(exc, stage="post_execution")
                    return False
            finally:
                try:
                    log_stage("Cleaning up")
                    self.cleanup()
                except Exception as exc:
                    self._log_exception(exc, stage="post_execution")
                    return False

        if execution_succeeded:
            # We mainly care about stages.log for failures.  It's rather
            # boring for successes.
            assert self._debug_log_files_directory is not None
            (Path(self._debug_log_files_directory.name) / "stages.log").unlink()

        return execution_succeeded

    def _upload_work_request_debug_logs(self) -> None:
        """
        Create a WorkRequestDebugLogs artifact and upload the logs.

        The logs might exist in self._debug_log_files_directory and were
        added via self.open_debug_log_file() or self.create_debug_log_file().

        For each self._source_artifacts_ids: create a relation from
        WorkRequestDebugLogs to source_artifact_id.
        """
        if self._debug_log_files_directory is None:
            return

        work_request_debug_logs_artifact = WorkRequestDebugLogs.create(
            files=Path(self._debug_log_files_directory.name).glob("*")
        )

        assert self.debusine
        remote_artifact = self.debusine.upload_artifact(
            work_request_debug_logs_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

        for source_artifact_id in self._source_artifacts_ids:
            self.debusine.relation_create(
                remote_artifact.id,
                source_artifact_id,
                RelationType.RELATES_TO,
            )

        self._debug_log_files_directory.cleanup()
        self._debug_log_files_directory = None


def get_environment(
    task_database: TaskDatabaseInterface,
    lookup: LookupSingle,
    *,
    architecture: str | None = None,
    backend: str | None = None,
    default_category: CollectionCategory | None = None,
    image_category: ExecutorImageCategory | None = None,
    try_variant: str | None = None,
) -> ArtifactInfo:
    """
    Get an environment.

    This automatically fills in some additional constraints if needed.

    :param task_database: the :py:class:`TaskDatabaseInterface` used to
      perform the lookup
    :param lookup: the base lookup provided by the task data
    :param architecture: the task's host architecture, if available
    :param backend: the task's backend, or None if the environment lookup
      does not need to be constrained to a particular backend
    :param default_category: the default category to use for the first
      segment of the lookup
    :param image_category: try to use an environment with this image
      category; defaults to the image category needed by the executor for
      `self.backend`
    :param try_variant: None if the environment lookup does not need to be
      constrained to a particular variant; otherwise, and if `lookup` does
      not already specify a variant, then try looking up an environment with
      this variant first, and fall back to looking for an environment with
      no variant
    :return: the ArtifactInfo of a suitable environment artifact
    """
    lookups: list[LookupSingle] = []

    if (
        isinstance(lookup, str)
        and len(segments := parse_lookup_string_segments(lookup)) == 2
        and (parsed := parse_key_value_lookup_segment(segments[1]))[0]
        == "match"
    ):
        # Supplement the environment lookup with the task architecture,
        # if required.
        lookup_type, filters = parsed
        assert lookup_type == "match"

        # TODO: If filters already have values for architecture/format, then
        # check their consistency.

        if architecture is not None:
            filters.setdefault("architecture", architecture)

        if image_category is None and backend is not None:
            image_category = executor_class(backend).image_category
        match image_category:
            case ExecutorImageCategory.TARBALL:
                filters.setdefault("format", "tarball")
            case ExecutorImageCategory.IMAGE:
                filters.setdefault("format", "image")
            case _ as unreachable:
                raise AssertionError(
                    f"Unexpected image category: {unreachable}"
                )

        if backend is not None:
            filters.setdefault("backend", backend)

        if try_variant is not None and "variant" not in filters:
            for variant in (try_variant, ""):
                lookups.append(
                    build_lookup_string_segments(
                        segments[0],
                        build_key_value_lookup_segment(
                            lookup_type, {**filters, "variant": variant}
                        ),
                    )
                )
        else:
            lookups.append(
                build_lookup_string_segments(
                    segments[0],
                    build_key_value_lookup_segment(lookup_type, filters),
                )
            )
    else:
        lookups.append(lookup)

    for try_lookup in lookups[:-1]:
        try:
            return task_database.lookup_single_artifact(
                try_lookup, default_category=default_category
            )
        except KeyError:
            pass

    return task_database.lookup_single_artifact(
        lookups[-1], default_category=default_category
    )


TDE = TypeVar("TDE", bound=BaseTaskDataWithExecutor)
DTDE = TypeVar("DTDE", bound=BaseDynamicTaskDataWithExecutor)


# TODO: This should be an ABC.
class BaseTaskWithExecutor(BaseExternalTask[TDE, DTDE], Generic[TDE, DTDE]):
    r"""
    Base for tasks with executor capabilities.

    Concrete subclasses must implement ``fetch_input()``,
    ``configure_for_execution()``, ``run()``,
    ``check_directory_for_consistency_errors()``, and
    ``upload_artifacts()``, as documented by :py:class:`BaseExternalTask`.
    """

    DEFAULT_BACKEND = BackendType.UNSHARE

    def get_environment(
        self,
        task_database: TaskDatabaseInterface,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
        image_category: ExecutorImageCategory | None = None,
        set_backend: bool = True,
        try_variant: bool = True,
    ) -> ArtifactInfo:
        """
        Get an environment for an executor-capable task.

        This automatically fills in some additional constraints from the task
        data if needed.

        :param task_database: the :py:class:`TaskDatabaseInterface` used to
          perform the lookup
        :param lookup: the base lookup provided by the task data
        :param default_category: the default category to use for the first
          segment of the lookup
        :param image_category: try to use an environment with this image
          category; defaults to the image category needed by the executor
          for `self.backend`
        :param set_backend: if True (default), try to use an environment
          matching `self.backend`
        :param try_variant: if True (default), try to use an environment
          whose variant is `self.name`, but fall back to looking up an
          environment without a variant if the first lookup fails
        :return: the ArtifactInfo of a suitable environment artifact
        """
        return get_environment(
            task_database=task_database,
            lookup=lookup,
            architecture=self.host_architecture(),
            backend=self.backend if set_backend else None,
            default_category=default_category,
            image_category=image_category,
            try_variant=self.name if try_variant else None,
        )

    def _prepare_executor(self) -> None:
        """
        Prepare the executor.

        * Set self.executor to the new executor with self.backend and the
          appropriate environment ID (which must have been looked up by the
          task's compute_dynamic_data method)
        * Download the image
        """
        assert self.debusine
        assert self.dynamic_data
        assert self.dynamic_data.environment_id
        self.executor = executor_class(self.backend)(
            self.debusine, self.dynamic_data.environment_id
        )
        self.executor.download_image()

    def _prepare_executor_instance(self) -> None:
        """
        Create and start an executor instance.

        If self.executor is None: call self._prepare_executor()

        Set self.executor_instance to the new executor instance, starts
        the instance.
        """
        if self.executor is None:
            self._prepare_executor()

        assert self.executor
        self.executor_instance = self.executor.create()
        self.executor_instance.start()

    @property
    def backend(self) -> str:
        """Return the backend name to use."""
        backend = self.data.backend
        if backend == "auto":
            backend = self.DEFAULT_BACKEND
        return backend

    def prepare_to_run(
        self, download_directory: Path, execute_directory: Path
    ) -> None:
        """Copy the download and execution directories into the executor."""
        if self.executor_instance:
            self.executor_instance.create_user()
            non_root_user = self.executor_instance.non_root_user
            self.executor_instance.directory_push(
                download_directory,
                Path("/tmp"),
                user=non_root_user,
                group=non_root_user,
            )
            self.executor_instance.mkdir(
                execute_directory,
                user=non_root_user,
                group=non_root_user,
            )

    def run_executor_command(
        self,
        cmd: list[str],
        log_filename: str,
        run_as_root: bool = False,
        check: bool = True,
    ) -> None:
        """Run cmd within the executor, logging the output to log_name."""
        assert self.executor_instance
        with self.open_debug_log_file(log_filename, mode="ab") as cmd_log:
            msg = f"Executing: {shlex.join(cmd)}\n"
            cmd_log.write(msg.encode())
            cmd_log.flush()
            p = self.executor_instance.run(
                cmd,
                run_as_root=run_as_root,
                stdout=cmd_log,
                stderr=cmd_log,
                check=check,
            )
            msg = f"Execution completed (exit code {p.returncode})\n"
            cmd_log.write(msg.encode())

    def cleanup(self) -> None:
        """
        Clean up after running the task.

        Some tasks use the executor in upload_artifacts, so we clean up the
        executor here rather than in run().
        """
        if self.executor_instance and self.executor_instance.is_started():
            self.executor_instance.stop()


class RunCommandTask(
    BaseExternalTask[TD, DTD], Generic[TD, DTD], metaclass=ABCMeta
):
    r"""
    A :py:class:`BaseTask` that can execute commands and upload artifacts.

    Concrete subclasses must implement:

    * ``_cmdline(self) -> list[str]``
    * ``task_succeeded(self, returncode: Optional[int], execute_directory: Path)
      -> bool`` (defaults to True)

    They must also implement ``configure_for_execution()``,
    ``fetch_input()``, ``check_directory_for_consistency_errors()``, and
    ``upload_artifacts()``, as documented by :py:class:`BaseTaskWithExecutor`.
    (They do not need to implement ``run()``, but may do so if they need to
    run multiple commands rather than just one.)

    Use ``self.append_to_log_file()`` / ``self.open_debug_log_file()`` to
    provide information for the user (it will be available to the user as an
    artifact).

    Command execution uses process groups to make sure that the command and
    possible spawned commands are finished, and cancels the execution of the
    command if ``BaseTask.aborted()`` is True.

    Optionally: _cmdline_as_root() and _cmd_env() may be implemented, to
    customize behaviour.

    See the main entry point ``BaseTask._execute()`` for details of the
    flow.
    """

    # If CAPTURE_OUTPUT_FILENAME is not None: self.run_via_executor()
    # creates a file in the cwd of the command to save the stdout. The file
    # is available in self.upload_artifacts().
    CAPTURE_OUTPUT_FILENAME: str | None = None

    CMD_LOG_SEPARATOR = "--------------------"
    CMD_LOG_FILENAME = "cmd-output.log"

    def run(self, execute_directory: Path) -> bool:
        """
        Run a single command via the executor.

        .. note::

          If the member variable CAPTURE_OUTPUT_FILENAME is set:
          create a file with its name with the stdout of the command. Otherwise,
          the stdout of the command is saved in self.CMD_LOG_FILENAME).
        """
        cmd = self._cmdline()
        self.logger.info("Executing: %s", " ".join(cmd))

        returncode = self.run_cmd(
            cmd,
            execute_directory,
            env=self._cmd_env(),
            run_as_root=self._cmdline_as_root(),
            capture_stdout_filename=self.CAPTURE_OUTPUT_FILENAME,
        )

        self.logger.info("%s exited with code %s", cmd[0], returncode)

        return self.task_succeeded(returncode, execute_directory)

    def task_succeeded(
        self,
        returncode: int | None,  # noqa: U100
        execute_directory: Path,  # noqa: U100
    ) -> bool:
        """
        Sub-tasks can evaluate if the task was a success or failure.

        By default, return True (success). Sub-classes can re-implement it.

        :param returncode: return code of the command, or None if aborted
        :param execute_directory: directory with the output of the task
        :return: True (if success) or False (if failure).
        """
        return returncode == 0

    @abstractmethod
    def _cmdline(self) -> list[str]:
        """Return the command to execute, as a list of program arguments."""

    def _cmd_env(self) -> dict[str, str] | None:
        """Return the environment to execute the command under."""
        return None

    @staticmethod
    def _cmdline_as_root() -> bool:
        """Run _cmdline() as root."""
        return False

    @staticmethod
    def _write_utf8(file: BinaryIO, text: str) -> None:
        file.write(text.encode("utf-8", errors="replace") + b"\n")

    def _write_popen_result(
        self, file: BinaryIO, p: subprocess.Popen[AnyStr]
    ) -> None:
        self._write_utf8(file, f"\naborted: {self.aborted}")
        self._write_utf8(file, f"returncode: {p.returncode}")

    def run_cmd(
        self,
        cmd: list[str],
        working_directory: Path,
        *,
        env: dict[str, str] | None = None,
        run_as_root: bool = False,
        capture_stdout_filename: str | None = None,
    ) -> int | None:
        """
        Run cmd in working_directory. Create self.CMD_OUTPUT_FILE log file.

        If BaseTask.aborted == True terminates the process.

        :param cmd: command to execute with its arguments.
        :param working_directory: working directory where the command
          is executed.
        :param run_as_root: if True, run the command as root. Otherwise,
          the command runs as the worker's user
        :param capture_stdout_filename: for some commands the output of the
          command is the output of stdout (e.g. lintian) and not a set of files
          generated by the command (e.g. sbuild). If capture_stdout is not None,
          save the stdout into this file. The caller can then use it.
        :return: returncode of the process or None if aborted
        """
        with self.open_debug_log_file(
            self.CMD_LOG_FILENAME, mode="ab"
        ) as cmd_log:
            self._write_utf8(cmd_log, f"cmd: {shlex.join(cmd)}")

            out_file: BinaryIO
            if capture_stdout_filename:
                self._write_utf8(
                    cmd_log,
                    "output (contains stderr only, stdout was captured):",
                )
                capture_stdout = working_directory / capture_stdout_filename
                out_file = capture_stdout.open(mode="wb")
            else:
                self._write_utf8(
                    cmd_log, "output (contains stdout and stderr):"
                )
                out_file = cmd_log

            try:
                return self._run_cmd(
                    cmd,
                    working_directory,
                    env=env,
                    run_as_root=run_as_root,
                    cmd_log=cmd_log,
                    out_file=out_file,
                )
            finally:
                file_names = "\n".join(
                    str(file.relative_to(working_directory))
                    for file in sorted(working_directory.rglob("*"))
                )

                self._write_utf8(cmd_log, "\nFiles in working directory:")
                self._write_utf8(cmd_log, file_names)

                self._write_utf8(cmd_log, self.CMD_LOG_SEPARATOR)
                if capture_stdout_filename:
                    self._write_utf8(cmd_log, capture_stdout.read_text())
                    self._write_utf8(cmd_log, self.CMD_LOG_SEPARATOR)

                out_file.close()

    def _run_cmd(
        self,
        cmd: list[str],
        working_directory: Path,
        *,
        env: dict[str, str] | None,
        run_as_root: bool,
        cmd_log: BinaryIO,
        out_file: BinaryIO,
    ) -> int | None:
        """
        Execute cmd.

        :param run_as_root: if using an executor: run command as root or user.
          If not using an executor and run_as_root is True: raise ValueError().
        :param cmd_log: save the command log (parameters, stderr, return code,
          stdout)
        :param out_file: save the command stdout (might be the same as
          cmd_log or a different file)

        :return: returncode of the process or None if aborted
        """
        # Need to flush or subprocess.Popen() overwrites part of it
        cmd_log.flush()
        out_file.flush()

        run_kwargs: dict[str, Any] = {
            "cwd": working_directory,
            "env": env,
            "stderr": cmd_log.fileno(),
            "stdout": out_file.fileno(),
        }

        if self.executor_instance:
            return self.executor_instance.run(
                cmd, run_as_root=run_as_root, **run_kwargs
            ).returncode

        if run_as_root:
            raise ValueError("run_as_root requires an executor")

        p = subprocess.Popen(cmd, start_new_session=True, **run_kwargs)

        process_group = os.getpgid(p.pid)

        while True:
            if self.aborted:
                break

            try:
                self._wait_popen(p, timeout=1)
                break
            except subprocess.TimeoutExpired:
                pass

        if self.aborted:
            self.logger.debug("Task (cmd: %s PID %s) aborted", cmd, p.pid)
            try:
                if not self._send_signal_pid(p.pid, signal.SIGTERM):
                    # _send_signal_pid failed probably because cmd finished
                    # after aborting and before sending the signal
                    #
                    # p.poll() to read the returncode and avoid leaving cmd
                    # as zombie
                    p.poll()

                    # Kill possible processes launched by cmd
                    self._send_signal_group(process_group, signal.SIGKILL)
                    self.logger.debug("Could not send SIGTERM to %s", p.pid)

                    self._write_popen_result(cmd_log, p)
                    return None

                # _wait_popen with a timeout=5 to leave 5 seconds of grace
                # for the cmd to finish after sending SIGTERM
                self._wait_popen(p, timeout=5)
            except subprocess.TimeoutExpired:
                # SIGTERM was sent and 5 seconds later cmd
                # was still running. A SIGKILL to the process group will
                # be sent
                self.logger.debug(
                    "Task PID %s not finished after SIGTERM", p.pid
                )
                pass

            # debusine sends a SIGKILL if:
            # - SIGTERM was sent to cmd AND cmd was running 5 seconds later:
            #   SIGTERM was not enough so SIGKILL to the group is needed
            # - SIGTERM was sent to cmd AND cmd finished: SIGKILL to the
            #   group to make sure that there are not processes spawned
            #   by cmd running
            # (note that a cmd could launch processes in a new group
            # could be left running)
            self._send_signal_group(process_group, signal.SIGKILL)
            self.logger.debug("Sent SIGKILL to process group %s", process_group)

            # p.poll() to set p.returncode and avoid leaving cmd
            # as a zombie process.
            # But cmd might be left as a zombie process: if cmd was in a
            # non-interruptable kernel call p.returncode will be None even
            # after p.poll() and it will be left as a zombie process
            # (until debusine worker dies and the zombie is adopted by
            # init and waited on by init). If this happened there we might be a
            # ResourceWarning from Popen.__del__:
            # "subprocess %s is still running"
            #
            # A solution would e to wait (p.waitpid()) that the
            # process finished dying. This is implemented in the unit test
            # to avoid the warning but not implemented here to not delay
            # the possible shut down of debusine worker
            p.poll()
            self._write_popen_result(cmd_log, p)
            self.logger.debug("Returncode for PID %s: %s", p.pid, p.returncode)

            return None
        else:
            # The cmd has finished. The cmd might have spawned
            # other processes. debusine will kill any alive processes.
            #
            # If they existed they should have been finished by cmd:
            # run_cmd() should not leave processes behind.
            #
            # Since the parent died they are adopted by init and on
            # killing them they are not zombie.
            # (cmd might have spawned new processes in a different process
            # group: if this is the case they will be left running)
            self._send_signal_group(process_group, signal.SIGKILL)

        self._write_popen_result(cmd_log, p)

        return p.returncode

    def _wait_popen(
        self, popen: subprocess.Popen[AnyStr], timeout: float
    ) -> int:
        return popen.wait(timeout)

    @staticmethod
    def _send_signal_pid(pid: int, signal: signal.Signals) -> bool:
        try:
            os.kill(pid, signal)
        except ProcessLookupError:
            return False

        return True

    @staticmethod
    def _send_signal_group(process_group: int, signal: signal.Signals) -> None:
        """Send signal to the process group."""
        try:
            os.killpg(process_group, signal)
        except ProcessLookupError:
            pass


TDER = TypeVar("TDER", bound=BaseTaskDataWithExtraRepositories)


class ExtraRepositoryMixin(
    BaseExternalTask[TDER, DTD], Generic[TDER, DTD], metaclass=ABCMeta
):
    """Methods for configuring external APT repositories."""

    data: TDER

    # Deb822 style sources:
    extra_repository_sources: list[Path]
    # GPG keys (for apt < 2.3.10):
    extra_repository_keys: list[Path]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize class variables."""
        super().__init__(*args, **kwargs)
        self.extra_repository_sources = []
        self.extra_repository_keys = []

    def supports_deb822_sources(self, codename: str) -> bool:
        """Determine if codename supports deb822 sources."""
        return codename not in ("jessie", "trusty")

    def supports_inline_signed_by(self, codename: str) -> bool:
        """Determine if codename supports inline signatures."""
        # apt < 2.3.10 has no support for keys inline in Signed-By
        return codename not in (
            "jessie",
            "stretch",
            "buster",
            "bullseye",
            "trusty",
            "xenial",
            "bionic",
            "focal",
        )

    def write_extra_repository_config(
        self, codename: str, destination: Path
    ) -> None:
        """
        Write extra_repositories config to files in destination.

        extra_repository_keys will be populated with keys to install into
        /etc/apt/keyrings, if needed.

        extra_repository_sources will be populated with deb822 sources files,
        if supported.
        """
        inline_signed_by = self.supports_inline_signed_by(codename)
        for i, extra_repo in enumerate(self.data.extra_repositories or []):
            if not inline_signed_by:
                if extra_repo.signing_key:
                    path = destination / f"extra_apt_key_{i}.asc"
                    path.write_text(extra_repo.signing_key + "\n")
                    self.extra_repository_keys.append(path)

            # apt < 1.1 has no support for deb822 sources
            if self.supports_deb822_sources(codename):
                path = destination / f"extra_repository_{i}.sources"
                signed_by = None
                if not inline_signed_by:
                    signed_by = f"/etc/apt/keyrings/extra_apt_key_{i}.asc"
                path.write_text(
                    extra_repo.as_deb822_source(signed_by_filename=signed_by)
                )
                self.extra_repository_sources.append(path)

    def iter_oneline_sources(self) -> Generator[str]:
        """
        Generate apt one-line sources.list entries.

        Use this for releases that don't supports_deb822_sources.
        """
        for i, extra_repository in enumerate(
            self.data.extra_repositories or []
        ):
            yield extra_repository.as_oneline_source(
                signed_by_filename=f"/etc/apt/keyrings/extra_apt_key_{i}.asc"
            )


class DefaultDynamicData(BaseTask[TD, BaseDynamicTaskData], metaclass=ABCMeta):
    """Base class for tasks that do not add to dynamic task data."""

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """Return default dynamic data."""
        return BaseDynamicTaskData()

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of source artifact IDs used by this task."""
        return []
