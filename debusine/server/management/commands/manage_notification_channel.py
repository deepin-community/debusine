# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage a channel notification."""

import argparse
import sys
from typing import Any, NoReturn

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser

from debusine.db.models import NotificationChannel
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to manage channel notifications."""

    help = "Manage channel notification"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for manage_channel_notification."""
        subparsers = parser.add_subparsers(dest="action")

        # change name
        change_name = subparsers.add_parser("change-name")
        change_name.add_argument("name", help="Channel to modify")
        change_name.add_argument("new_name", help="New name")

        # change data
        change_data = subparsers.add_parser("change-data")
        change_data.add_argument("name", help="Channel to modify")
        change_data.add_argument(
            "--data",
            type=argparse.FileType("r"),
            help="New data. File path (or - for stdin) to read the data for "
            "the notification. YAML format. Defaults to stdin.",
            default="-",
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if options["action"] == "change-data" and options["data"] != sys.stdin:
            options["data"].close()

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Manage the notification."""
        name = options["name"]
        try:
            notification_channel = NotificationChannel.objects.get(name=name)
        except NotificationChannel.DoesNotExist:
            raise CommandError(
                f'NotificationChannel "{name}" does not exist', returncode=3
            )

        action = options["action"]

        if action == "change-name":
            notification_channel.name = options["new_name"]
            notification_channel.save()
        elif action == "change-data":  # pragma: no cover
            data = options["data"].read()
            notification_channel.data = self.parse_yaml_data(data)
            try:
                notification_channel.save()
            except ValidationError as exc:
                raise CommandError(f"Invalid data: {exc}", returncode=3)

        raise SystemExit(0)
