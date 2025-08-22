# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_users."""

from django.contrib.auth import get_user_model

from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class ListUsersCommandTests(TabularOutputTests, TestCase):
    """Tests for the list_users command."""

    def setUp(self) -> None:
        """Set up new user for the tests."""
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="bob", email="bob@bob.com", password="123456"
        )

    def test_list_tokens_no_filtering(self) -> None:
        """list_users print the user's information."""
        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command("list_users")

        self.assertEqual(output.col(0), [self.user.username])
        self.assertEqual(output.col(1), [self.user.email])
        self.assertEqual(output.col(2), [self.user.date_joined.isoformat()])

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
