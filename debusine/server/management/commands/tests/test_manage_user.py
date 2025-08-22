# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command manage_user."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError
from django.test import TestCase

from debusine.django.management.tests import call_command


class ManageUserCommandTests(TestCase):
    """Tests for manage_user management command."""

    def setUp(self) -> None:
        """Create a default User."""
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="bob", password="123456"
        )

    def test_no_action_raise_error(self) -> None:
        """Assert manage_user raise CommandError if no action is passed."""
        with self.assertRaises(CommandError):
            call_command("manage_user")

    def test_change_email_no_username_email_raise_error(self) -> None:
        """
        Assert manage_user change-email return an error message.

        Without the workaround in change_email.called_from_command_line
        it raises CommandError.
        """
        patcher = mock.patch.object(BaseCommand, "_called_from_command_line")
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        mocked.return_value = True

        stdout, stderr, exit_code = call_command("manage_user", "change-email")

        self.assertEqual(exit_code, 2)

    def test_change_email(self) -> None:
        """manage_user change-email changes the email."""
        email = "bob@bob.com"
        stdout, stderr, exit_code = call_command(
            "manage_user", "change-email", self.user.username, email
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, email)

    def test_change_email_username_not_found(self) -> None:
        """manage_user change-email user not found."""
        username = "non-existing-user"

        expected_error = f'Username "{username}" not found'
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command(
                "manage_user",
                "change-email",
                username,
                "email@example.com",
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_change_email_username_system(self) -> None:
        """manage_user excludes system users."""
        expected_error = 'Username "_system" not found'
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command(
                "manage_user", "change-email", "_system", "email@example.com"
            )

        self.assertEqual(exc.exception.returncode, 3)
