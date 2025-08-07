# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_notification_channel."""
import io
from typing import ClassVar

import yaml
from django.core.management import CommandError

from debusine.db.models import NotificationChannel
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


class CreateNotificationChannelCommandTests(TestCase):
    """Tests for the create_notification_channel command."""

    yaml_data: ClassVar[str]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up test."""
        super().setUpTestData()
        cls.yaml_data = yaml.safe_dump(
            {"from": "debusine@debusine.com", "to": ["lts@debusine.com"]}
        )

    def test_create_notification_channel_from_file(self) -> None:
        """create_notification_channel create a notification (data in file)."""
        name = "lts"
        method = NotificationChannel.Methods.EMAIL
        data_file = self.create_temporary_file(
            contents=self.yaml_data.encode("utf-8")
        )
        stdout, stderr, exit_code = call_command(
            "create_notification_channel",
            name,
            method,
            "--data",
            str(data_file),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertTrue(
            NotificationChannel.objects.filter(
                name=name, method=method
            ).exists()
        )

    def test_create_notification_channel_from_stdin(self) -> None:
        """create_notification_channel create a notification (data in stdin)."""
        name = "lts"
        method = NotificationChannel.Methods.EMAIL

        stdout, stderr, exit_code = call_command(
            "create_notification_channel",
            name,
            method,
            stdin=io.StringIO(self.yaml_data),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertTrue(
            NotificationChannel.objects.filter(
                name=name, method=method
            ).exists()
        )

    def test_create_notification_channel_invalid_data_yaml(self) -> None:
        """create_notification_channel return error: cannot parse YAML data."""
        data_file = self.create_temporary_file(contents=b":")
        with self.assertRaisesRegex(
            CommandError, "^Error parsing YAML:"
        ) as exc:
            call_command(
                "create_notification_channel",
                "test",
                "email",
                "--data",
                str(data_file),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_notification_invalid_data(self) -> None:
        """create_notification_channel return error: data is invalid."""
        # it is invalid because does not have the required fields
        data_file = self.create_temporary_file(contents=b"")
        with self.assertRaisesRegex(CommandError, "^Error creating") as exc:
            call_command(
                "create_notification_channel",
                "test",
                "email",
                "--data",
                str(data_file),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_notification_invalid_name(self) -> None:
        """create_notification_channel return error: name is invalid."""
        # it is invalid because does not have the required fields
        data_file = self.create_temporary_file(contents=b"{}")
        with self.assertRaisesRegex(CommandError, "^Error creating") as exc:
            call_command(
                "create_notification_channel",
                "a: b",
                "email",
                "--data",
                str(data_file),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_duplicated_notification_raise_command_error(self) -> None:
        """create_notification_channel raise CommandError: duplicated name."""
        name = "lts"
        method = NotificationChannel.Methods.EMAIL
        data_file = self.create_temporary_file(
            contents=self.yaml_data.encode("utf-8")
        )

        call_command(
            "create_notification_channel",
            name,
            method,
            "--data",
            str(data_file),
        )

        with self.assertRaisesRegex(CommandError, "^Error creating") as exc:
            call_command(
                "create_notification_channel",
                name,
                method,
                "--data",
                str(data_file),
            )

        self.assertEqual(exc.exception.returncode, 3)
