# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_token."""

from django.contrib.auth import get_user_model
from django.core.management import CommandError
from django.test import TestCase

from debusine.db.models import Token
from debusine.django.management.tests import call_command


class CreateTokenCommandTests(TestCase):
    """Tests for the create_token command."""

    def test_create_token(self) -> None:
        """create_token creates a new token and prints the key on stdout."""
        username = "james"
        get_user_model().objects.create_user(username=username, password="1234")
        stdout, stderr, exit_code = call_command('create_token', username)

        token = Token.objects.latest("id")

        # The token key is not stored in the db, so we need to hash the token
        # we got.
        token_hash = Token._generate_hash(stdout.strip())

        self.assertEqual(token_hash, token.hash)
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertTrue(token.enabled)

    def test_username_does_not_exist(self) -> None:
        """create_token cannot create a token: username does not exist."""
        username = "james"
        expected_error = f'Cannot create token: "{username}" does not exist'
        with self.assertRaisesRegex(CommandError, expected_error) as exc:
            call_command("create_token", username)

        self.assertEqual(exc.exception.returncode, 3)
        self.assertEqual(Token.objects.all().count(), 0)
