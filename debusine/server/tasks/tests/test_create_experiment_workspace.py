# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the task to create experiment workspaces."""

from datetime import timedelta
from typing import Any, ClassVar

from django.utils import timezone

from debusine.db.context import context
from debusine.db.models import Group, WorkflowTemplate, Workspace
from debusine.db.playground import scenarios
from debusine.server.tasks import (
    CreateExperimentWorkspace,
    ServerTaskPermissionDenied,
)
from debusine.server.tasks.create_experiment_workspace import CannotCreate
from debusine.server.tasks.models import CreateExperimentWorkspaceData
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    override_permission,
)


class CreateExperimentWorkspaceTests(TestCase):
    """Tests for :py:class:`CreateExperimentWorkspace`."""

    scenario = scenarios.DefaultContext()
    workflow_template_1: ClassVar[WorkflowTemplate]
    workflow_template_2: ClassVar[WorkflowTemplate]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.workflow_template_1 = cls.playground.create_workflow_template(
            name="workflow_template_1", task_name="noop"
        )
        cls.workflow_template_2 = cls.playground.create_workflow_template(
            name="workflow_template_2", task_name="noop"
        )

    def _task(self, **kwargs: Any) -> CreateExperimentWorkspace:
        """Create a CreateExperimentWorkspace task."""
        workspace = self.scenario.workspace
        data = CreateExperimentWorkspaceData(**kwargs)
        work_request = self.playground.create_work_request(
            workspace=workspace,
            task_name="create_experiment_workspace",
            task_data=data,
        )
        task = CreateExperimentWorkspace(work_request.task_data)
        task.set_work_request(work_request)
        return task

    def test_get_label(self) -> None:
        """Test get_label."""
        task = self._task(experiment_name="test")
        self.assertEqual(
            task.get_label(), "create experiment workspace 'System-test'"
        )

    def test_validate_experiment_name(self) -> None:
        """Test experiment name validation."""
        for name in ["test-test", "0test", "test@test", ".test", "+test"]:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    ValueError, "experiment name contains invalid characters"
                ),
            ):
                CreateExperimentWorkspaceData(experiment_name="test-test")

    @override_permission(Workspace, "can_create_experiment_workspace", DenyAll)
    def test_execute_no_perms(self) -> None:
        """Test running the task with insufficient permissions."""
        task = self._task(experiment_name="test")
        with self.assertRaisesRegex(
            ServerTaskPermissionDenied,
            r"playground cannot create an experiment"
            r" workspace from debusine/System",
        ):
            task.execute()

    @override_permission(Workspace, "can_create_experiment_workspace", AllowAll)
    def test_execute_defaults(self) -> None:
        """Test running the task with default arguments."""
        test_start = timezone.now()
        task = self._task(experiment_name="test")
        self.assertTrue(task.execute())

        new_workspace = Workspace.objects.get(
            scope=self.scenario.scope, name="System-test"
        )
        self.assertTrue(new_workspace.public)
        self.assertEqual(new_workspace.default_expiration_delay, timedelta(0))
        self.assertQuerySetEqual(
            new_workspace.inherits.all(), [self.scenario.workspace]
        )
        self.assertGreaterEqual(new_workspace.created_at, test_start)
        self.assertEqual(new_workspace.expiration_delay, timedelta(days=60))

        group = Group.objects.get(
            scope=self.scenario.scope, name=new_workspace.name
        )
        self.assertTrue(group.ephemeral)
        self.assertQuerySetEqual(group.users.all(), [self.scenario.user])
        # TODO: assert that user is admin of the group (after !1611)

        role = new_workspace.roles.get(group=group)
        self.assertEqual(role.role, Workspace.Roles.OWNER)

        self.assertQuerySetEqual(
            WorkflowTemplate.objects.filter(workspace=new_workspace), []
        )

    @override_permission(Workspace, "can_create_experiment_workspace", AllowAll)
    def test_execute_all_set(self) -> None:
        """Test running the task with all arguments set."""
        test_start = timezone.now()
        task = self._task(
            experiment_name="test",
            public=False,
            owner_group=self.scenario.workspace_owners.name,
            workflow_template_names=["workflow_template_1"],
            expiration_delay=7,
        )
        self.assertTrue(task.execute())

        new_workspace = Workspace.objects.get(
            scope=self.scenario.scope, name="System-test"
        )
        self.assertFalse(new_workspace.public)
        self.assertEqual(new_workspace.default_expiration_delay, timedelta(0))
        self.assertQuerySetEqual(
            new_workspace.inherits.all(), [self.scenario.workspace]
        )
        self.assertGreaterEqual(new_workspace.created_at, test_start)
        self.assertEqual(new_workspace.expiration_delay, timedelta(days=7))

        role = new_workspace.roles.get(group=self.scenario.workspace_owners)
        self.assertEqual(role.role, Workspace.Roles.OWNER)

        new_workflow_templates = list(
            WorkflowTemplate.objects.filter(workspace=new_workspace)
        )
        self.assertEqual(len(new_workflow_templates), 1)

        self.assertEqual(
            new_workflow_templates[0].name, self.workflow_template_1.name
        )
        self.assertEqual(new_workflow_templates[0].workspace, new_workspace)
        self.assertEqual(
            new_workflow_templates[0].task_name,
            self.workflow_template_1.task_name,
        )
        self.assertEqual(
            new_workflow_templates[0].task_data,
            self.workflow_template_1.task_data,
        )
        self.assertEqual(
            new_workflow_templates[0].priority,
            self.workflow_template_1.priority,
        )

    @override_permission(Workspace, "can_create_experiment_workspace", AllowAll)
    def test_wrong_group_name(self) -> None:
        """Test running the task with a misspelled group name."""
        task = self._task(
            experiment_name="test",
            owner_group="misspelled-name",
        )
        with self.assertRaisesRegex(
            CannotCreate, "owner group misspelled-name not found"
        ):
            task.execute()

    @override_permission(Workspace, "can_create_experiment_workspace", AllowAll)
    def test_wrong_workflowtemplate_name(self) -> None:
        """Test running the task with a misspelled workflow template name."""
        task = self._task(
            experiment_name="test",
            workflow_template_names=["misspelled-name"],
        )
        with self.assertRaisesRegex(
            CannotCreate, "System/misspelled-name does not exist"
        ):
            task.execute()
