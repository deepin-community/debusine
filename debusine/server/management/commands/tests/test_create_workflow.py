# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_workflow."""

import io
from typing import ClassVar

import yaml
from django.core.management import CommandError

from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    WorkRequest,
    WorkflowTemplate,
    default_workspace,
    system_user,
)
from debusine.django.management.tests import call_command
from debusine.tasks.models import TaskTypes
from debusine.test.django import TestCase


class CreateWorkflowCommandTests(TestCase):
    """Tests for the create_workflow command."""

    artifact: ClassVar[Artifact]
    template: ClassVar[WorkflowTemplate]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up test fixture."""
        super().setUpTestData()
        cls.artifact = cls.playground.create_source_artifact()
        architectures = ["amd64", "arm64"]
        for architecture in architectures:
            cls.playground.create_debian_environment(
                codename="bookworm", architecture=architecture
            )
        cls.template = WorkflowTemplate.objects.create(
            name="test",
            workspace=cls.playground.get_default_workspace(),
            task_name="sbuild",
            task_data={
                "input": {"source_artifact": cls.artifact.id},
                "architectures": architectures,
            },
        )

    def test_data_from_file(self) -> None:
        """`create_workflow` accepts data from a file."""
        data = {"target_distribution": "debian:bookworm"}
        data_file = self.create_temporary_file(
            contents=yaml.safe_dump(data).encode()
        )
        stdout, stderr, exit_code = call_command(
            "create_workflow", "test", "--data", str(data_file)
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        )
        self.assertEqual(workflow.workspace, default_workspace())
        self.assertEqual(workflow.created_by, system_user())
        self.assertEqual(
            workflow.task_data, {**data, **self.template.task_data}
        )

    def test_data_from_stdin(self) -> None:
        """`create_workflow` accepts data from stdin."""
        data = {"target_distribution": "debian:bookworm"}
        stdout, stderr, exit_code = call_command(
            "create_workflow", "test", stdin=io.StringIO(yaml.safe_dump(data))
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        )
        self.assertEqual(workflow.workspace, default_workspace())
        self.assertEqual(workflow.created_by, system_user())
        self.assertEqual(
            workflow.task_data, {**data, **self.template.task_data}
        )

    def test_empty_data(self) -> None:
        """`create_workflow` defaults data to {}."""
        WorkflowTemplate.objects.create(
            name="noop", workspace=default_workspace(), task_name="noop"
        )
        stdout, stderr, exit_code = call_command(
            "create_workflow", "noop", stdin=io.StringIO()
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW, task_name="noop"
        )
        self.assertEqual(workflow.task_data, {})

    def test_different_created_by(self) -> None:
        """`create_workflow` can use a different created-by user."""
        user = self.playground.get_default_user()
        stdout, stderr, exit_code = call_command(
            "create_workflow",
            "test",
            "--created-by",
            user.username,
            stdin=io.StringIO(
                yaml.safe_dump({"target_distribution": "debian:bookworm"})
            ),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        )
        self.assertEqual(workflow.created_by, user)

    def test_different_workspace(self) -> None:
        """`create_workflow` can use a non-default workspace."""
        workspace_name = "test-workspace"
        workspace = self.playground.create_workspace(
            name=workspace_name, public=True
        )
        workspace.set_inheritance([self.playground.get_default_workspace()])
        self.template.workspace = workspace
        self.template.save()
        stdout, stderr, exit_code = call_command(
            "create_workflow",
            "test",
            "--workspace",
            workspace_name,
            stdin=io.StringIO(
                yaml.safe_dump({"target_distribution": "debian:bookworm"})
            ),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        )
        self.assertEqual(workflow.workspace.name, workspace_name)

    def test_invalid_data_yaml(self) -> None:
        """`create_workflow` returns error: cannot parse data."""
        with self.assertRaisesRegex(
            CommandError, r"^Error parsing YAML:"
        ) as exc:
            call_command("create_workflow", "test", stdin=io.StringIO(":"))

        self.assertEqual(exc.exception.returncode, 3)

    def test_user_not_found(self) -> None:
        """`create_workflow` returns error: user not found."""
        with self.assertRaisesRegex(
            CommandError, r'^User "nonexistent" not found'
        ) as exc:
            call_command(
                "create_workflow",
                "test",
                "--created-by",
                "nonexistent",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_workspace_not_found(self) -> None:
        """`create_workflow` returns error: workspace not found."""
        with self.assertRaisesRegex(
            CommandError, r"Workspace 'nonexistent' not found"
        ) as exc:
            call_command(
                "create_workflow",
                "test",
                "--workspace",
                "nonexistent",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_template_name_not_found(self) -> None:
        """`create_workflow` returns error: template name not found."""
        with self.assertRaisesRegex(
            CommandError, r'^Workflow template "nonexistent" not found'
        ) as exc:
            call_command("create_workflow", "nonexistent", stdin=io.StringIO())

        self.assertEqual(exc.exception.returncode, 3)

    def test_bad_workflow_data(self) -> None:
        """`create_workflow` returns error: bad workflow data."""
        with self.assertRaisesRegex(
            CommandError,
            r"foo\s+extra fields not permitted \(type=value_error\.extra\)",
        ) as exc:
            call_command(
                "create_workflow", "test", stdin=io.StringIO("foo: bar\n")
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(
            WorkRequest.objects.filter(
                task_type=TaskTypes.WORKFLOW, task_name="sbuild"
            ).exists()
        )
