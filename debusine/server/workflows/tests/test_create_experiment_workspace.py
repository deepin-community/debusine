# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the package_publish workflow."""

from typing import Any

from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import WorkRequest, Workspace
from debusine.db.playground import scenarios
from debusine.server.workflows import CreateExperimentWorkspaceWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import ExperimentWorkspaceData
from debusine.test.django import TestCase


class CreateExperimentWorkspaceWorkflowTests(TestCase):
    """Unit tests for :py:class:`CreateExperimentWorkspaceWorkflow`."""

    scenario = scenarios.DefaultContext()

    def create_workflow(
        self, task_data: dict[str, Any]
    ) -> CreateExperimentWorkspaceWorkflow:
        """Create an experiment workspace workflow."""
        wr = self.playground.create_workflow(
            task_name="create_experiment_workspace", task_data=task_data
        )
        return CreateExperimentWorkspaceWorkflow(wr)

    def test_get_label(self) -> None:
        """Test get_label."""
        workflow = self.create_workflow({"experiment_name": "test"})
        self.assertEqual(
            workflow.get_label(), "create experiment workspace 'test'"
        )

    def test_create_orchestrator(self) -> None:
        """Workflow can be instantiated."""
        workflow = self.create_workflow({"experiment_name": "test"})
        self.assertEqual(workflow.data.experiment_name, "test")
        self.assertTrue(workflow.data.public)
        self.assertIsNone(workflow.data.owner_group)
        self.assertEqual(workflow.data.workflow_template_names, [])
        self.assertEqual(workflow.data.expiration_delay, 60)

    def orchestrate(
        self,
        task_data: ExperimentWorkspaceData,
        *,
        workspace: Workspace | None = None,
    ) -> WorkRequest:
        """Create and orchestrate a CreateExperimentWorkspaceWorkflow."""
        template = self.playground.create_workflow_template(
            name="create_experiment_workspace-template",
            task_name="create_experiment_workspace",
            workspace=workspace,
        )
        root = self.playground.create_workflow(
            task_name="create_experiment_workspace",
            task_data=model_to_json_serializable_dict(task_data),
        )
        self.assertEqual(root.workspace, template.workspace)

        root.mark_running()
        orchestrate_workflow(root)

        return root

    def test_populate(self) -> None:
        """Test population with both source and binary artifacts."""
        root = self.orchestrate(
            task_data=ExperimentWorkspaceData(experiment_name="test")
        )
        # There is only one child task
        create_experiment_workspace = root.children.get()
        self.assertEqual(
            create_experiment_workspace.task_name, "create_experiment_workspace"
        )
        self.assertEqual(
            create_experiment_workspace.task_data,
            {
                "experiment_name": "test",
                "expiration_delay": 60,
                "owner_group": None,
                "public": True,
                "workflow_template_names": [],
                "task_configuration": None,
            },
        )
