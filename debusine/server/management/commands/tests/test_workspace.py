# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command workspace."""
import io
from datetime import timedelta
from typing import ClassVar

import yaml
from django.core.management import CommandError

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    SINGLETON_COLLECTION_CATEGORIES,
)
from debusine.db.models import (
    ArtifactRelation,
    Scope,
    WorkflowTemplate,
    Workspace,
    default_workspace,
)
from debusine.db.models.workspaces import WorkspaceRole
from debusine.db.playground import Playground, scenarios
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class WorkspaceCommandsTests(TabularOutputTests, TestCase):
    """Tests for workspace management commands."""

    scope: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope(name="localtest")

    def test_define_scenario(self) -> None:
        """Test define command."""
        # define/create, with errors
        with self.assertRaisesRegex(
            CommandError,
            "Error: owners_group is required when creating workspace",
        ):
            call_command("workspace", "create", "localtest/myws")

        with self.assertRaisesRegex(
            CommandError,
            "scope_workspace 'myws' should be in the form"
            " 'scopename/workspacename'",
        ):
            call_command("workspace", "create", "myws", "Owners-myws")

        with self.assertRaisesRegex(
            CommandError,
            "Scope 'nonexistent' not found",
        ):
            call_command(
                "workspace", "create", "nonexistent/my ws", "Owners-myws"
            )

        with self.assertRaisesRegex(
            CommandError,
            "Error creating workspace: 'my ws' is not a valid workspace name",
        ):
            call_command(
                "workspace", "create", "localtest/my ws", "Owners-myws"
            )

        with self.assertRaisesRegex(
            CommandError, "Group 'Owners-myws' not found in scope 'localtest'"
        ):
            call_command("workspace", "create", "localtest/myws", "Owners-myws")

        self.playground.create_group("Owners-myws", scope=self.scope)
        self.playground.create_group("Owners-myws2", scope=self.scope)

        # create/define, idempotent
        for i in (1, 2):
            stdout, stderr, exit_code = call_command(
                "workspace", "define", "localtest/myws", "Owners-myws"
            )
            # assert workspace and role
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")
            self.assertEqual(exit_code, 0)
            workspace = Workspace.objects.get(scope=self.scope, name="myws")
            role = WorkspaceRole.objects.get(
                resource=workspace, group__name="Owners-myws"
            )
            self.assertEqual(role.role, WorkspaceRole.Roles.OWNER)
            self.assertFalse(workspace.public)
            self.assertEqual(workspace.default_expiration_delay, timedelta(30))
            self.assertQuerySetEqual(
                workspace.collections.values_list("category", flat=True),
                SINGLETON_COLLECTION_CATEGORIES,
                ordered=False,
            )

        # Set parameters
        call_command(
            "workspace",
            "define",
            "localtest/myws-new",
            "Owners-myws",
            "--public",
            "--default-expiration-delay",
            "0",
            "--no-singleton-collections",
        )
        workspace = Workspace.objects.get(scope=self.scope, name="myws-new")
        self.assertTrue(workspace.public)
        self.assertEqual(workspace.default_expiration_delay, timedelta(0))
        self.assertQuerySetEqual(
            workspace.collections.values_list("category", flat=True), []
        )

        # Change parameters
        call_command(
            "workspace",
            "define",
            "localtest/myws-new",
            "Owners-myws2",
            "--private",
            "--default-expiration-delay",
            "10",
        )
        workspace.refresh_from_db()
        self.assertFalse(workspace.public)
        self.assertEqual(workspace.default_expiration_delay, timedelta(10))
        role = WorkspaceRole.objects.get(
            resource=workspace, group__name="Owners-myws2"
        )
        self.assertEqual(role.role, WorkspaceRole.Roles.OWNER)

        # Don't reset parameters to defaults
        call_command(
            "workspace",
            "define",
            "localtest/myws-new",
        )
        self.assertFalse(workspace.public)
        self.assertEqual(workspace.default_expiration_delay, timedelta(10))

    def test_rename_scenario(self) -> None:
        """Test rename command."""
        self.playground.create_group("Owners-myws", scope=self.scope)
        call_command("workspace", "create", "localtest/myws", "Owners-myws")

        # rename, with errors
        with self.assertRaisesRegex(
            CommandError, "Workspace 'toto' not found in scope 'localtest'"
        ):
            call_command("workspace", "rename", "localtest/toto", "toto2")

        stdout, stderr, exit_code = call_command(
            "workspace", "rename", "localtest/myws", "my ws"
        )
        self.assertEqual(
            stderr,
            """Renamed workspace would be invalid:
* name: 'my ws' is not a valid workspace name
""",
        )
        self.assertEqual(exit_code, 3)
        self.assertTrue(
            Workspace.objects.filter(scope=self.scope, name="myws").exists()
        )

        call_command(
            "workspace", "create", "localtest/myws-exists", "Owners-myws"
        )
        stdout, stderr, exit_code = call_command(
            "workspace", "rename", "localtest/myws", "myws-exists"
        )
        self.assertEqual(
            stderr,
            """Renamed workspace would be invalid:
* __all__: Workspace with this Scope and Name already exists.
""",
        )
        self.assertEqual(exit_code, 3)

        # rename, idempotent
        stdout, stderr, exit_code = call_command(
            "workspace", "rename", "localtest/myws", "myws-renamed"
        )
        self.assertEqual(exit_code, 0)
        self.assertFalse(
            Workspace.objects.filter(scope=self.scope, name="myws").exists()
        )
        self.assertTrue(
            Workspace.objects.filter(
                scope=self.scope, name="myws-renamed"
            ).exists()
        )

        stdout, stderr, exit_code = call_command(
            "workspace", "rename", "localtest/myws-renamed", "myws-renamed"
        )
        self.assertEqual(exit_code, 0)

    def test_list(self) -> None:
        """Test list command."""
        # Setup
        self.playground.create_group("Owners-myws", scope=self.scope)

        call_command(
            "workspace", "create", "localtest/public", "Owners-myws", "--public"
        )
        call_command("workspace", "create", "localtest/otherfs", "Owners-myws")
        call_command(
            "workspace",
            "create",
            "localtest/noexpire",
            "Owners-myws",
            "--default-expiration-delay",
            "0",
        )

        # list
        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command("workspace", "list")

        self.assertEqual(
            output.col(0),
            [
                "debusine/System",
                "localtest/public",
                "localtest/otherfs",
                "localtest/noexpire",
            ],
        )
        self.assertEqual(output.col(1), ["True", "True", "False", "False"])
        self.assertEqual(output.col(2), ["Never", "30", "30", "Never"])

        # --yaml
        stdout, stderr, _ = call_command("workspace", "list", "--yaml")
        data = yaml.safe_load(stdout)
        expected_data = [
            {
                'expiration': 'Never',
                'name': 'debusine/System',
                'public': True,
            },
            {
                'expiration': 30,
                'name': 'localtest/public',
                'public': True,
            },
            {
                'expiration': 30,
                'name': 'localtest/otherfs',
                'public': False,
            },
            {
                'expiration': 'Never',
                'name': 'localtest/noexpire',
                'public': False,
            },
        ]
        self.assertEqual(data, expected_data)

        # scope
        stdout, stderr, _ = call_command(
            "workspace", "list", "localtest", "--yaml"
        )
        data = yaml.safe_load(stdout)
        self.assertEqual(data, expected_data[1:])

    def test_roles(self) -> None:
        """Test roles commands."""
        self.playground.create_group("Admins", scope=self.scope)
        self.playground.create_group("Employees", scope=self.scope)
        self.playground.create_group("Contractors", scope=self.scope)

        # fmt: off
        call_command("workspace", "create", "localtest/myws", "Admins")

        with self.assertRaisesRegex(
            CommandError,
            "Error assigning role: 'xxx' is not a valid WorkspaceRoles",
        ):
            call_command(
                "workspace", "grant_role", "localtest/myws",
                "xxx", "Employees",
            )

        call_command(
            "workspace", "grant_role", "localtest/myws",
            "contributor", "Employees", "Contractors",
        )

        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "workspace", "list_roles", "localtest/myws"
            )
        self.assertEqual(output.col(0), ["Admins", "Contractors", "Employees"])
        self.assertEqual(output.col(1), ["owner", "contributor", "contributor"])
        self.assertEqual(exit_code, 0)

        stdout, stderr, exit_code = call_command(
            "workspace", "list_roles", "localtest/myws", "--yaml"
        )
        data = yaml.safe_load(stdout)
        expected_data = [
            {'group': 'Admins', 'role': 'owner'},
            {'group': 'Contractors', 'role': 'contributor'},
            {'group': 'Employees', 'role': 'contributor'},
        ]
        self.assertEqual(data, expected_data)
        self.assertEqual(exit_code, 0)

        # idempotent
        call_command(
            "workspace", "grant_role", "localtest/myws",
            "contributor", "Employees", "Contractors",
        )
        stdout, stderr, exit_code = call_command(
            "workspace", "list_roles", "localtest/myws", "--yaml"
        )
        data = yaml.safe_load(stdout)
        self.assertEqual(data, expected_data)

        # revoke_role
        stdout, stderr, exit_code = call_command(
            "workspace", "revoke_role", "localtest/myws",
            "contributor", "Employees"
        )
        stdout, stderr, exit_code = call_command(
            "workspace", "list_roles", "localtest/myws", "--yaml"
        )
        data = yaml.safe_load(stdout)
        self.assertEqual(data, expected_data[0:-1])
        # fmt: on

    def create_workspace_for_delete(self) -> Workspace:
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

    def test_delete_workspace(self) -> None:
        """Delete an existing workspace."""
        workspace = self.create_workspace_for_delete()

        stdout, stderr, _ = call_command(
            "workspace",
            "delete",
            "scope/Test",
            "--yes",
        )
        self.assertEqual("", stdout)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(id=workspace.id)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(name="Test")

    def test_delete_workspace_missing(self) -> None:
        """Try to delete a workspace that does not exist."""
        self.playground.get_or_create_scope("scope")
        with self.assertRaisesRegex(
            CommandError, r"Workspace 'Test' not found in scope 'scope'$"
        ) as exc:
            call_command("workspace", "delete", "scope/Test", "--yes")
        self.assertEqual(exc.exception.returncode, 3)

        stdout, stderr, exit_code = call_command(
            "workspace",
            "delete",
            "scope/Test",
            "--force",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        stdout, stderr, exit_code = call_command(
            "workspace",
            "delete",
            "scope/Test",
            "--force",
            "--yes",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_delete_workspace_playground_ui_scenario(self) -> None:
        """Delete a workspace with the playground UI scenario."""
        playground = Playground(default_workspace_name="Playground")
        scenario = scenarios.UIPlayground()
        playground.build_scenario(scenario)

        stdout, stderr, _ = call_command(
            "workspace",
            "delete",
            str(scenario.workspace),
            "--yes",
        )
        self.assertEqual("", stdout)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(id=scenario.workspace.id)

        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get(
                scope=scenario.scope, name=scenario.workspace.name
            )

    def test_delete_workspace_confirmation(self) -> None:
        """delete_workspace doesn't delete (user does not confirm)."""
        workspace = self.create_workspace_for_delete()

        call_command(
            "workspace",
            "delete",
            "scope/Test",
            stdin=io.StringIO("N\n"),
        )
        self.assertQuerySetEqual(
            Workspace.objects.filter(name="Test"), [workspace]
        )

        call_command(
            "workspace",
            "delete",
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
            fr"^Workspace {workspace.scope.name}/{workspace.name}"
            " cannot be deleted$",
        ) as exc:
            call_command(
                "workspace",
                "delete",
                str(workspace),
                "--yes",
            )
        self.assertEqual(exc.exception.returncode, 3)
