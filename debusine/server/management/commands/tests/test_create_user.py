# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_user."""

from django.contrib.auth import authenticate, get_user_model
from django.core.management import CommandError
from django.test import TestCase

from debusine.django.management.tests import call_command


class CreateUserCommandTests(TestCase):
    """Tests for the create_token command."""

    def test_create_user(self) -> None:
        """create_user creates a new token and prints the password to stdout."""
        username = "bob"
        email = "bob@example.com"
        password, stderr, exit_code = call_command(
            "create_user", username, email
        )

        password = password.rstrip()
        self.assertEqual(len(password), 16)
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        user = authenticate(request=None, username=username, password=password)

        self.assertIsInstance(user, get_user_model())

    def test_create_user_duplicated_username(self) -> None:
        """create_user raise CommandError."""
        username = "bob"
        email = "bob@example.com"
        call_command("create_user", username, email)

        expected_error = "A user with this username or email already exists"
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command("create_user", username, email)

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_user_invalid_username(self) -> None:
        """create_user raises CommandError."""
        username = "a/b"
        email = "bob@example.com"

        expected_error = "Enter a valid username. This value may contain"
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command("create_user", username, email)

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_user_duplicated_email(self) -> None:
        """create_user raise CommandError."""
        email = "email@example.com"
        call_command("create_user", "bob", email)

        expected_error = "A user with this username or email already exists"
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command("create_user", "bob2", email)

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_user_with_email(self) -> None:
        """create_user set the user's email."""
        username = "bob"
        email = "bob@bob.com"
        call_command("create_user", username, email)

        user = get_user_model().objects.get(username=username)
        self.assertEqual(user.email, email)
