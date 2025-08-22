# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_tokens."""
from datetime import timedelta
from typing import ClassVar

from django.utils import timezone

from debusine.db.models import Token, User
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class ListTokensCommandTests(TabularOutputTests, TestCase):
    """Tests for the list_tokens command."""

    user: ClassVar[User]
    token: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.user = User.objects.create_user(
            username="John", email="john@example.com"
        )
        cls.token = cls.playground.create_user_token(user=cls.user)

    def test_list_tokens_no_filtering(self) -> None:
        """list_tokens print the token key."""
        with self.assertPrintsTable() as output:
            call_command('list_tokens')

        self.assertEqual(output.col(0), [self.token.hash])
        self.assertEqual(output.col(1), [self.user.username])
        self.assertEqual(output.col(2), [self.token.created_at.isoformat()])
        self.assertEqual(output.col(3), ["-"])
        self.assertEqual(output.col(4), ["True"])
        self.assertEqual(output.col(5), [self.token.comment])

    def test_list_tokens_expire_at(self) -> None:
        """list_tokens prints `expire_at` if set."""
        self.token.expire_at = timezone.now() + timedelta(hours=1)
        self.token.save()

        with self.assertPrintsTable() as output:
            call_command('list_tokens')

        self.assertEqual(output.col(0), [self.token.hash])
        self.assertEqual(output.col(1), [self.user.username])
        self.assertEqual(output.col(2), [self.token.created_at.isoformat()])
        self.assertEqual(output.col(3), [self.token.expire_at.isoformat()])
        self.assertEqual(output.col(4), ["True"])
        self.assertEqual(output.col(5), [self.token.comment])

    def test_list_tokens_filtered_by_username(self) -> None:
        """list_tokens print the correct filtered owners."""
        self.playground.create_bare_token()
        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command(
                'list_tokens', '--username', 'John'
            )
        self.assertEqual(output.col(0), [self.token.hash])

    def test_list_tokens_sorted_by_created_at(self) -> None:
        """Tokens are sorted by created_at."""
        token_2 = self.playground.create_user_token(user=self.user)

        with self.assertPrintsTable() as output:
            call_command('list_tokens')

        # token_1 is displayed before token2 (ordered by created_at)
        self.assertEqual(output.col(0), [self.token.hash, token_2.hash])

        # Make token_1.created_at later than token_2
        self.token.created_at = token_2.created_at + timedelta(minutes=1)
        self.token.save()

        with self.assertPrintsTable() as output:
            call_command('list_tokens')

        # Now token_1 appear after token_2
        self.assertEqual(output.col(0), [token_2.hash, self.token.hash])
