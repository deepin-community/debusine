# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Workflow that creates an experiment workspace from the one it's run on."""

from debusine.server.tasks.models import CreateExperimentWorkspaceData
from debusine.server.workflows.base import Workflow
from debusine.server.workflows.models import (
    ExperimentWorkspaceData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import BaseDynamicTaskData, TaskTypes
from debusine.tasks.server import TaskDatabaseInterface


class CreateExperimentWorkspaceWorkflow(
    Workflow[ExperimentWorkspaceData, BaseDynamicTaskData]
):
    """
    Workflow that creates a new experiment workspace.

    This workflow is a simple interface for users to invoke the
    create_experiment_workspace server task.
    """

    TASK_NAME = "create_experiment_workspace"

    def populate(self) -> None:
        """Create the server task that creates the new workspace."""
        task_data = CreateExperimentWorkspaceData(**self.data.dict())
        self.work_request_ensure_child(
            task_type=TaskTypes.SERVER,
            task_name="create_experiment_workspace",
            task_data=task_data,
            workflow_data=WorkRequestWorkflowData(
                display_name="Create experiment workspace",
                step="create-experiment-workspace",
            ),
        )

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """Compute dynamic data for this workflow."""
        return BaseDynamicTaskData()

    def get_label(self) -> str:
        """Return the task label."""
        return f"create experiment workspace {self.data.experiment_name!r}"
