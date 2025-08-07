# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command delete_workspace."""

import io

from django.core.management import CommandError

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import (
    ArtifactRelation,
    WorkflowTemplate,
    Workspace,
    default_workspace,
)
from debusine.db.playground import Playground, scenarios
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


class DeleteWorkspaceCommandTests(TestCase):
    """Tests for the delete_workspace command."""

    @context.disable_permission_checks()
    def create_test_workspace(self) -> Workspace:
        """Create a test workspace."""
        scope = self.playground.get_or_create_scope("scope")
        workspace = self.playground.create_workspace(name="Test", scope=scope)
        self.playground.create_group_role(
            workspace,
            Workspace.Roles.CONTRIBUTOR,
            [self.playground.get_default_user()],
        )
        for architecture in ("amd64", "s390x"):
            self.playground.create_debian_environment(
                codename="bookworm",
                architecture=architecture,
                workspace=workspace,
            )
        work_request = self.playground.create_work_request(
            workspace=workspace, task_name="noop"
        )
        collection = self.playground.create_collection(
            "test", CollectionCategory.WORKFLOW_INTERNAL, workspace=workspace
        )
        artifact_hello, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": "hello",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {},
            },
            paths=[
                "hello_1.0-1.dsc",
                "hello_1.0-1.debian.tar.xz",
                "hello_1.0.orig.tar.xz",
            ],
            workspace=workspace,
            create_files=True,
            skip_add_files_in_store=True,
        )
        artifact_hello.created_by_work_request = work_request
        artifact_hello.save()

        artifact_hello_traditional, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": "hello-traditional",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {},
            },
            paths=[
                "hello-traditional.0-1.dsc",
                "hello-traditional.0-1.debian.tar.xz",
                "hello-traditional.0.orig.tar.xz",
            ],
            workspace=workspace,
            create_files=True,
            skip_add_files_in_store=True,
        )
        self.playground.create_artifact_relation(
            artifact_hello_traditional,
            artifact_hello,
            ArtifactRelation.Relations.RELATES_TO,
        )
        collection.manager.add_artifact(
            artifact_hello,
            user=self.playground.get_default_user(),
            name="hello",
        )
        collection.manager.add_artifact(
            artifact_hello_traditional,
            user=self.playground.get_default_user(),
            name="hello-traditional",
        )
        WorkflowTemplate.objects.create(
            name="test", workspace=workspace, task_name="noop"
        )

        sbuild_template = self.playground.create_workflow_template(
            name="Build package",
            task_name="sbuild",
            task_data={},
            workspace=workspace,
        )

        udev = self.playground.create_source_artifact(
            name="udev",
            version="252.26-1~deb12u2",
            create_files=True,
            workspace=workspace,
        )

        workflow = self.playground.create_workflow(
            sbuild_template,
            task_data={
                "input": {
                    "source_artifact": udev.pk,
                },
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64", "s390x"],
            },
        )
        workflow.mark_running()
        workflow.save()

        assert workflow.internal_collection is not None
        self.assertEqual(workflow.internal_collection.workflow, workflow)

        # TODO: add more kinds of elements to the workspace, to make
        # sure deletion catches them
        return workspace

    def test_delete_missing_workspace(self) -> None:
        """Workspace does not exist."""
        self.playground.get_or_create_scope("scope")
        with self.assertRaisesRegex(
            CommandError, r"^Workspace Test does not exist in scope scope$"
        ) as exc:
            call_command(
                'delete_workspace',
                "scope/Test",
            )
        self.assertEqual(exc.exception.returncode, 3)

        with self.assertRaisesRegex(
            CommandError, r"^Workspace Test does not exist in scope scope$"
        ) as exc:
            call_command(
                'delete_workspace',
                "scope/Test",
                '--yes',
            )
        self.assertEqual(exc.exception.returncode, 3)

        stdout, stderr, _ = call_command(
            'delete_workspace',
            "scope/Test",
            '--force',
        )
        self.assertEqual('', stdout)

        stdout, stderr, _ = call_command(
            'delete_workspace',
            "scope/Test",
            '--force',
            "--yes",
        )
        self.assertEqual('', stdout)

    def test_delete_workspace(self) -> None:
        """Delete an existing workspace."""
        workspace = self.create_test_workspace()

        stdout, stderr, _ = call_command(
            'delete_workspace',
            "scope/Test",
            "--yes",
        )
        self.assertEqual('', stdout)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(id=workspace.id)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(name="Test")

    def test_delete_workspace_playground_ui_scenario(self) -> None:
        """Delete a workspace with the playground UI scenario."""
        playground = Playground(default_workspace_name="Playground")
        scenario = scenarios.UIPlayground()
        playground.build_scenario(scenario)

        stdout, stderr, _ = call_command(
            'delete_workspace',
            str(scenario.workspace),
            "--yes",
        )
        self.assertEqual('', stdout)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(id=scenario.workspace.id)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(
                scope=scenario.scope, name=scenario.workspace.name
            )

    def test_delete_workspace_confirmation(self) -> None:
        """delete_workspace doesn't delete (user does not confirm)."""
        workspace = self.create_test_workspace()

        call_command(
            "delete_workspace",
            "scope/Test",
            stdin=io.StringIO("N\n"),
        )
        self.assertQuerySetEqual(
            Workspace.objects.filter(name="Test"), [workspace]
        )

        call_command(
            "delete_workspace",
            "scope/Test",
            stdin=io.StringIO("\n"),
        )
        self.assertQuerySetEqual(
            Workspace.objects.filter(name="Test"), [workspace]
        )

    def test_delete_default_workspace(self) -> None:
        """Default workspace does not get deleted."""
        workspace = default_workspace()
        with self.assertRaisesRegex(
            CommandError,
            fr"^Workspace {workspace.name} cannot be deleted$",
        ) as exc:
            call_command(
                "delete_workspace",
                str(workspace),
                "--yes",
                "--force",
            )
        self.assertEqual(exc.exception.returncode, 3)

    def test_delete_workspace_with_scope_not_found(self) -> None:
        """delete_workspace: scope not found."""
        with self.assertRaisesRegex(
            CommandError, 'Scope "nonexistent" not found'
        ) as exc:
            call_command("delete_workspace", "nonexistent/test")

        self.assertEqual(exc.exception.returncode, 3)
