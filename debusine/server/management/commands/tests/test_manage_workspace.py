# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command manage_workspace."""
from datetime import timedelta
from typing import ClassVar

from django.core.management import CommandError

from debusine.db.context import context
from debusine.db.models import Scope, Workspace
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


class ManageWorkspaceCommandTests(TestCase):
    """Tests for manage_workspace management command."""

    scope: ClassVar[Scope]
    workspace: ClassVar[Workspace]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up test fixture."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope(name="scope")
        cls.workspace = cls.playground.create_workspace(
            scope=cls.scope, name="test"
        )

    def test_no_default_reset(self) -> None:
        """Don't confuse no-option with reset-to-default."""
        # Non-default values
        default_expiration_delay = 7
        stdout, stderr, exit_code = call_command(
            "manage_workspace",
            str(self.workspace),
            "--public",
            f"--default-expiration-delay={default_expiration_delay}",
        )

        stdout, stderr, exit_code = call_command(
            "manage_workspace",
            str(self.workspace),
        )

        self.workspace.refresh_from_db()
        self.assertTrue(self.workspace.public)
        self.assertEqual(
            self.workspace.default_expiration_delay,
            timedelta(default_expiration_delay),
        )

    def test_change_public(self) -> None:
        """manage_workspace changes the public status."""
        for public in (True, False):
            stdout, stderr, exit_code = call_command(
                "manage_workspace",
                "--public" if public else "--private",
                str(self.workspace),
            )

            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")
            self.assertEqual(exit_code, 0)

            self.workspace.refresh_from_db()
            self.assertEqual(self.workspace.public, public)

    def test_change_public_not_found(self) -> None:
        """manage_workspace workspace not found."""
        name = "non-existing-workspace"

        expected_error = f'Workspace "{name}" not found'
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command(
                "manage_workspace",
                "--public",
                self.scope.name + "/" + name,
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_change_default_expiration_delay(self) -> None:
        """manage_workspace changes the default expiration delay."""
        default_expiration_delay = 123456

        with self.assertRaises(CommandError):
            call_command(
                "manage_workspace",
                "--default-expiration-delay",
                "letters",
                str(self.workspace),
            )

        stdout, stderr, exit_code = call_command(
            "manage_workspace",
            "--default-expiration-delay",
            str(default_expiration_delay),
            str(self.workspace),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.default_expiration_delay,
            timedelta(default_expiration_delay),
        )

        # Ensure "0" isn't confused with "no value given"
        default_expiration_delay = 0

        call_command(
            "manage_workspace",
            "--default-expiration-delay",
            str(default_expiration_delay),
            str(self.workspace),
        )

        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.default_expiration_delay,
            timedelta(default_expiration_delay),
        )

    def test_change_all(self) -> None:
        """manage_workspace changes multiple fields at once."""
        default_expiration_delay = 7

        stdout, stderr, exit_code = call_command(
            "manage_workspace",
            "--public",
            f"--default-expiration-delay={default_expiration_delay}",
            str(self.workspace),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.workspace.refresh_from_db()
        self.assertTrue(self.workspace.public)
        self.assertEqual(
            self.workspace.default_expiration_delay,
            timedelta(default_expiration_delay),
        )

    def test_delete_workspace_with_scope_not_found(self) -> None:
        """manage_workspace: scope not found."""
        with self.assertRaisesRegex(
            CommandError, 'Scope "nonexistent" not found'
        ) as exc:
            call_command("manage_workspace", "nonexistent/test")

        self.assertEqual(exc.exception.returncode, 3)
