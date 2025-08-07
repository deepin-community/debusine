# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Base support for tasks that run on server-side Celery workers."""

from abc import ABCMeta
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from debusine.artifacts import WorkRequestDebugLogs
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    WorkRequest,
    Workspace,
)
from debusine.db.models.permissions import (
    PermissionUser,
    format_permission_check_error,
)
from debusine.tasks import BaseTask
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData, TaskTypes
from debusine.worker.system_information import host_architecture

TD = TypeVar("TD", bound=BaseTaskData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class ServerTaskPermissionDenied(Exception):
    """Permission predicate checks failed on server tasks."""


class BaseServerTask(BaseTask[TD, DTD], metaclass=ABCMeta):
    """Base class for tasks that run on server-side Celery workers."""

    TASK_TYPE = TaskTypes.SERVER

    # If True, the task manages its own transactions.  If False, the task is
    # automatically run within a single transaction.
    TASK_MANAGES_TRANSACTIONS = False

    # Set by the Celery worker via :py:meth:`set_work_request`.
    work_request: WorkRequest | None = None
    workspace: Workspace | None = None

    def set_work_request(self, work_request: WorkRequest) -> None:
        """Set this task's work request and workspace."""
        self.work_request = work_request
        self.workspace = work_request.workspace

        # BaseTask attributes; work_request_id and workspace_name are less
        # useful for server tasks since we have the model instances, but we
        # set them anyway for compatibility.
        self.work_request_id = work_request.id
        self.workspace_name = work_request.workspace.name
        self.worker_host_architecture = host_architecture()

    def execute(self) -> bool:
        """Execute task, setting up a suitable context."""
        assert self.work_request is not None
        assert self.workspace is not None

        context.reset()
        context.set_scope(self.workspace.scope)
        context.set_user(self.work_request.created_by)
        self.workspace.set_current()
        try:
            return super().execute()
        finally:
            context.reset()

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

        work_request_debug_logs = WorkRequestDebugLogs.create(
            files=Path(self._debug_log_files_directory.name).glob("*")
        )

        assert self.workspace is not None
        artifact = Artifact.objects.create_from_local_artifact(
            work_request_debug_logs,
            self.workspace,
            created_by_work_request=self.work_request,
        )

        for source_artifact_id in self._source_artifacts_ids:
            ArtifactRelation.objects.create(
                artifact=artifact,
                target_id=source_artifact_id,
                type=ArtifactRelation.Relations.RELATES_TO,
            )

        self._debug_log_files_directory.cleanup()
        self._debug_log_files_directory = None

    def enforce(self, predicate: Callable[[PermissionUser], bool]) -> None:
        """Enforce a permission predicate."""
        assert self.work_request is not None

        if predicate(self.work_request.created_by):
            return

        raise ServerTaskPermissionDenied(
            format_permission_check_error(predicate, context.user)
        )
