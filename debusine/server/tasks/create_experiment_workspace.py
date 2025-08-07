# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Task to create a workspace for experiments.

See docs/reference/devel-blueprints/experiment-workspaces.rst
"""

from datetime import timedelta

from debusine.db.context import context
from debusine.db.models import Group, WorkflowTemplate, Workspace
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import CreateExperimentWorkspaceData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class CannotCreate(Exception):
    """Creation of the new workspace failed."""


class CreateExperimentWorkspace(
    BaseServerTask[CreateExperimentWorkspaceData, BaseDynamicTaskData],
    DefaultDynamicData[CreateExperimentWorkspaceData],
):
    """Task that creates a workspace for experiments."""

    TASK_VERSION = 1
    TASK_NAME = "create_experiment_workspace"

    def get_experiment_workspace_name(self) -> str:
        """Build the name of the experiment workspace."""
        assert self.work_request is not None
        base_workspace = self.work_request.workspace
        return f"{base_workspace.name}-{self.data.experiment_name}"

    def _execute(self) -> bool:
        """Execute the task."""
        assert self.work_request is not None
        experiment_workspace_name = self.get_experiment_workspace_name()
        base_workspace = self.work_request.workspace

        # We need to override permissions later, so we need to check here
        # that the user has the permissions needed to run this server task
        self.enforce(base_workspace.can_create_experiment_workspace)

        # Lookup workflow templates to copy
        workflow_templates: list[WorkflowTemplate] = []
        for template_name in self.data.workflow_template_names:
            try:
                workflow_templates.append(
                    WorkflowTemplate.objects.get(
                        workspace=base_workspace, name=template_name
                    )
                )
            except WorkflowTemplate.DoesNotExist:
                raise CannotCreate(
                    f"workflow template {base_workspace.name}/{template_name}"
                    " does not exist"
                )

        # Lookup or create the owner group
        if self.data.owner_group is None:
            owner_group = Group.objects.create_ephemeral(
                base_workspace.scope,
                name=experiment_workspace_name,
                owner=self.work_request.created_by,
            )
        else:
            try:
                owner_group = Group.objects.get(
                    name=self.data.owner_group, scope=base_workspace.scope
                )
            except Group.DoesNotExist:
                raise CannotCreate(
                    f"owner group {self.data.owner_group} not found"
                )

        # Create the new workspace
        with context.disable_permission_checks():
            workspace = Workspace.objects.create(
                scope=base_workspace.scope,
                name=experiment_workspace_name,
                expiration_delay=(
                    None
                    if self.data.expiration_delay is None
                    else timedelta(days=self.data.expiration_delay)
                ),
                public=self.data.public,
            )

        # Set the new workspace as inheriting an existing workspace
        workspace.set_inheritance([base_workspace])

        # Set the new group as OWNER of the new workspace
        owner_group.assign_role(workspace, Workspace.Roles.OWNER)

        # Copy WorkflowTemplates to the new workspace
        for workflow_template in workflow_templates:
            # See https://docs.djangoproject.com/en/5.1/topics/db/queries/#copying-model-instances  # noqa: E501
            workflow_template.pk = None
            workflow_template._state.adding = True
            workflow_template.workspace = workspace
            workflow_template.save()

        return True

    def get_label(self) -> str:
        """Return the task label."""
        return (
            "create experiment workspace"
            f" {self.get_experiment_workspace_name()!r}"
        )
