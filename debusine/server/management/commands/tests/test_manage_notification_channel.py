# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command manage_channel_notification."""

import copy
import io
from typing import Any, ClassVar

import yaml
from django.core.management import CommandError

from debusine.db.models import NotificationChannel
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


class ManageNotificationChannelTests(TestCase):
    """Tests for the manage_notification_channel command."""

    email_data: ClassVar[dict[str, Any]]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up test."""
        super().setUpTestData()
        cls.email_data = {
            "from": "debusine@debusine.com",
            "to": ["user@example.com"],
        }

    def test_change_name(self) -> None:
        """Verify change name of a notification channel."""
        old_name = "lts"
        new_name = "new-lts"

        NotificationChannel.objects.create(
            name="lts",
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        stdout, stderr, exit_code = call_command(
            "manage_notification_channel",
            "change-name",
            old_name,
            new_name,
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertTrue(
            NotificationChannel.objects.filter(name=new_name).exists()
        )

    def test_change_data_from_file(self) -> None:
        """Verify change data of a notification channel from file."""
        new_data = copy.deepcopy(self.email_data)
        new_data["from"] = "a-new-email@example.com"

        name = "lts"

        NotificationChannel.objects.create(
            name=name,
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        new_data_file = self.create_temporary_file(
            contents=yaml.safe_dump(new_data).encode("utf-8")
        )

        stdout, stderr, exit_code = call_command(
            "manage_notification_channel",
            "change-data",
            name,
            "--data",
            str(new_data_file),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertEqual(
            NotificationChannel.objects.get(name=name).data, new_data
        )

    def test_change_data_from_stdin(self) -> None:
        """Verify change data of a notification channel from stdin."""
        new_data = copy.deepcopy(self.email_data)
        new_data["from"] = "a-new-email@example.com"

        name = "lts"

        NotificationChannel.objects.create(
            name=name,
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        stdout, stderr, exit_code = call_command(
            "manage_notification_channel",
            "change-data",
            name,
            stdin=io.StringIO(yaml.safe_dump(new_data)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertEqual(
            NotificationChannel.objects.get(name=name).data, new_data
        )

    def test_change_data_invalid_data(self) -> None:
        """Command raise CommandError: invalid data."""
        name = "deblts-email"
        NotificationChannel.objects.create(
            name=name,
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        new_data_file = self.create_temporary_file(
            contents=yaml.safe_dump({"invalid": "data"}).encode("utf-8")
        )

        with self.assertRaisesRegex(CommandError, "^Invalid data") as exc:
            call_command(
                "manage_notification_channel",
                "change-data",
                name,
                "--data",
                str(new_data_file),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_change_name_name_does_not_exist(self) -> None:
        """Name cannot be changed: NotificationChannel does not exist."""
        name = "does-not-exist"
        expected_error = f'NotificationChannel "{name}" does not exist'

        with self.assertRaisesMessage(CommandError, expected_error) as exc:
            call_command(
                "manage_notification_channel",
                "change-name",
                name,
                "new-name",
            )

        self.assertEqual(exc.exception.returncode, 3)
