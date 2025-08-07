# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command delete_tokens."""

import io

from django.core.management import CommandError

from debusine.db.models import Token
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class DeleteTokensCommandTests(TabularOutputTests, TestCase):
    """Tests for the delete_tokens command."""

    def test_delete_tokens(self) -> None:
        """delete_tokens deletes the token and prints the deleted key."""
        token = Token.objects.create()

        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command(
                'delete_tokens', '--yes', '--token', token.key
            )
        self.assertEqual(output.col(0), [token.hash])

    def test_delete_tokens_no_tokens(self) -> None:
        """delete_tokens does not exist: raise CommandError."""
        with self.assertRaisesRegex(
            CommandError, "^There are no tokens to be deleted$"
        ) as exc:
            call_command(
                'delete_tokens',
                '--token',
                '9deefd915ecd1009bf7598c1d4acf9a3bbfb8bd9e0c08b4bdc9',
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_delete_tokens_no_tokens_force(self) -> None:
        """delete_tokens returns exit code 0 and prints error message."""
        stdout, stderr, _ = call_command(
            'delete_tokens',
            '--force',
            '--token',
            '9deefd915ecd1009bf7598c1d4acf9a3bbfb8bd9e0c08b4bdc93d099b9a38aa2',
        )

        self.assertEqual('There are no tokens to be deleted\n', stdout)

    def test_delete_tokens_confirmation(self) -> None:
        """delete_tokens doesn't delete the token (user does not confirm)."""
        token = Token.objects.create()

        call_command(
            "delete_tokens", '--token', token.key, stdin=io.StringIO("N\n")
        )

        self.assertQuerySetEqual(Token.objects.filter(hash=token.hash), [token])

    def test_delete_tokens_confirmed(self) -> None:
        """delete_tokens delete the token (confirmed by the user)."""
        token = Token.objects.create()

        self.assertEqual(Token.objects.filter(hash=token.hash).count(), 1)

        call_command(
            "delete_tokens", '--token', token.key, stdin=io.StringIO("y\n")
        )

        self.assertEqual(Token.objects.filter(hash=token.hash).count(), 0)
