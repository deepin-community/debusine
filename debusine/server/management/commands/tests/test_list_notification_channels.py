# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_notification_channels."""
from django.test import TestCase
from rich.pretty import pretty_repr

from debusine.db.models import NotificationChannel
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.server.management.management_utils import sort_keys


class ListNotificationChannelsTests(TabularOutputTests, TestCase):
    """Tests for the list_notification_channels command."""

    def setUp(self) -> None:
        """Set up new notification channel for the test."""
        data = {
            "from": "sender@debusine.example.org",
            "to": ["recipient@example.com"],
        }
        self.notification_channel = NotificationChannel.objects.create(
            name="lts", method=NotificationChannel.Methods.EMAIL, data=data
        )

    def test_list_notification_channels(self) -> None:
        """list_notification_channels print the channels."""
        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "list_notification_channels"
            )
        self.assertEqual(output.col(0), [self.notification_channel.name])
        self.assertEqual(output.col(1), [self.notification_channel.method])
        self.assertEqual(
            output.col(2),
            [pretty_repr(sort_keys(self.notification_channel.data))],
        )

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
