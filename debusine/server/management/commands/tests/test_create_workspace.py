# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_workspace."""
from datetime import timedelta
from typing import ClassVar

from django.core.management import CommandError
from django.test import override_settings

from debusine.artifacts.models import SINGLETON_COLLECTION_CATEGORIES
from debusine.db.models import Group, Scope, Workspace
from debusine.db.models.workspaces import WorkspaceRole
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class CreateWorkspaceCommandTests(TestCase):
    """Tests for the create_workspace command."""

    scope: ClassVar[Scope]
    admin_group: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope(name="scope")
        cls.admin_group = cls.playground.create_group(
            scope=cls.scope, name="Owners-test"
        )

    def test_create_workspace(self) -> None:
        """create_workspace creates a new workspace."""
        name = "test"
        stdout, stderr, exit_code = call_command(
            "create_workspace", self.scope.name + "/" + name
        )

        workspace = Workspace.objects.get(scope=self.scope, name=name)

        self.assertFalse(workspace.public)
        self.assertQuerySetEqual(
            workspace.collections.values_list("category", flat=True),
            SINGLETON_COLLECTION_CATEGORIES,
            ordered=False,
        )
        role = WorkspaceRole.objects.get(
            resource=workspace, group=self.admin_group
        )
        self.assertEqual(role.role, WorkspaceRole.Roles.OWNER)
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create_public_workspace(self) -> None:
        """create_workspace creates a new public workspace."""
        name = "test"
        stdout, stderr, exit_code = call_command(
            "create_workspace", "--public", self.scope.name + "/" + name
        )

        workspace = Workspace.objects.get(scope=self.scope, name=name)

        self.assertTrue(workspace.public)
        self.assertEqual(workspace.default_expiration_delay, timedelta(0))
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create_workspace_with_default_expiration_delay(self) -> None:
        """Creates a workspace with a default_expiration_delay."""
        name = "test"
        default_expiration_delay = timedelta(days=7)
        stdout, stderr, exit_code = call_command(
            "create_workspace",
            "--default-expiration-delay",
            str(default_expiration_delay.days),
            self.scope.name + "/" + name,
        )

        workspace = Workspace.objects.get(scope=self.scope, name=name)

        self.assertFalse(workspace.public)
        self.assertEqual(
            workspace.default_expiration_delay, default_expiration_delay
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create_workspace_with_invalid_name(self) -> None:
        """Create a workspace with an invalid name."""
        with self.assertRaisesRegex(
            CommandError,
            r"^Error creating workspace: "
            r"'invalid name' is not a valid workspace name$",
        ) as exc:
            call_command("create_workspace", "scope/invalid name")

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_workspace_with_different_scopes(self) -> None:
        """Create a workspace with a different scopes."""
        name = "test"
        stdout, stderr, exit_code = call_command(
            "create_workspace", self.scope.name + "/" + name
        )

        scope = Scope.objects.create(name="different")
        admin_group = self.playground.create_group(
            scope=scope, name="Owners-test"
        )
        stdout, stderr, exit_code = call_command(
            "create_workspace", "different/" + name
        )

        workspace = Workspace.objects.get(scope=scope, name=name)
        self.assertFalse(workspace.public)
        self.assertEqual(workspace.scope, scope)
        role = WorkspaceRole.objects.get(resource=workspace, group=admin_group)
        self.assertEqual(role.role, WorkspaceRole.Roles.OWNER)
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create_workspace_with_scope_not_found(self) -> None:
        """create_workspace: scope not found."""
        with self.assertRaisesRegex(
            CommandError, 'Scope "nonexistent" not found'
        ) as exc:
            call_command("create_workspace", "nonexistent/test")

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_workspace_no_singleton_collections(self) -> None:
        """Create a workspace with no singleton collections."""
        name = "test"
        stdout, stderr, exit_code = call_command(
            "create_workspace",
            "--no-singleton-collections",
            self.scope.name + "/" + name,
        )

        workspace = Workspace.objects.get(scope=self.scope, name=name)

        self.assertFalse(workspace.public)
        self.assertQuerySetEqual(
            workspace.collections.values_list("category", flat=True), []
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create_workspace_with_specific_owners_group(self) -> None:
        """Create a workspace with a specific owners group."""
        name = "test"
        admin_group = self.playground.create_group(
            scope=self.scope, name="MyOwners"
        )
        stdout, stderr, exit_code = call_command(
            "create_workspace",
            self.scope.name + "/" + name,
            "--with-owners-group",
            "MyOwners",
        )

        workspace = Workspace.objects.get(scope=self.scope, name=name)

        role = WorkspaceRole.objects.get(resource=workspace, group=admin_group)
        self.assertEqual(role.role, WorkspaceRole.Roles.OWNER)
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        with self.assertRaisesRegex(
            CommandError, 'Group "NonExistent" not found'
        ) as exc:
            call_command(
                "create_workspace",
                self.scope.name + "/" + name,
                "--with-owners-group",
                "NonExistent",
            )
        self.assertEqual(exc.exception.returncode, 3)
