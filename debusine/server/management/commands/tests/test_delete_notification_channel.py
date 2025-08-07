# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command delete_channel_notification."""

from django.core.management import CommandError
from django.test import TestCase

from debusine.db.models import NotificationChannel
from debusine.django.management.tests import call_command


class DeleteNotificationChannelTests(TestCase):
    """Tests for the delete_notification_channel command."""

    def setUp(self) -> None:
        """Set up test."""
        self.email_data = {
            "from": "debusine@debusine.com",
            "to": ["user@example.com"],
        }

    def test_delete_notification_channel(self) -> None:
        """Delete notification channel."""
        name = "lts"
        NotificationChannel.objects.create(
            name=name,
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        stdout, stderr, exit_code = call_command(
            "delete_notification_channel", name
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertEqual(NotificationChannel.objects.count(), 0)

    def test_delete_notification_channel_name_does_not_exist(self) -> None:
        """Delete notification channel fails: name does not exist."""
        name = "does-not-exist"
        expected_error = f'NotificationChannel "{name}" does not exist'

        with self.assertRaisesMessage(CommandError, expected_error) as exc:
            call_command("delete_notification_channel", name)

        self.assertEqual(exc.exception.returncode, 3)
