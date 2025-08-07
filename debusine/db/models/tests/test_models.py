# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the models."""

from django.core.exceptions import ValidationError

from debusine.db.models import NotificationChannel
from debusine.test.django import TestCase


class NotificationChannelTests(TestCase):
    """Tests for NotificationChannel class."""

    def setUp(self) -> None:
        """Set up test."""
        self.email_data = {
            "from": "sender@debusine.example.org",
            "to": ["recipient@example.com"],
        }

    def test_create_email(self) -> None:
        """Create email NotificationChannel."""
        name = "deblts-email"
        method = NotificationChannel.Methods.EMAIL

        notification_channel = NotificationChannel.objects.create(
            name=name, method=method, data=self.email_data
        )

        notification_channel.refresh_from_db()

        self.assertEqual(notification_channel.name, name)
        self.assertEqual(notification_channel.method, method)
        self.assertEqual(notification_channel.data, self.email_data)

    def test_create_email_invalid_data(self) -> None:
        """Create email NotificationChannel: invalid data, failure."""
        with self.assertRaises(ValidationError):
            NotificationChannel.objects.create(
                name="deblts-email",
                method=NotificationChannel.Methods.EMAIL,
                data={"something": "something"},
            )

    def test_name_unique(self) -> None:
        """Field name is unique."""
        name = "deblts-email"
        NotificationChannel.objects.create(
            name=name,
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        with self.assertRaises(ValidationError):
            NotificationChannel.objects.create(
                name=name,
                method=NotificationChannel.Methods.EMAIL,
                data=self.email_data,
            )

    def test_str(self) -> None:
        """Assert __str__ return the expected string."""
        name = "deblts-email"
        notification_channel = NotificationChannel.objects.create(
            name=name,
            method=NotificationChannel.Methods.EMAIL,
            data=self.email_data,
        )

        self.assertEqual(str(notification_channel), name)
