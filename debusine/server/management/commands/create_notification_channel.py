# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to create a notification channel."""

import argparse
import sys
from typing import Any, NoReturn

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser

from debusine.db.models import NotificationChannel
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to create an enabled token."""

    help = "Create a new notification channel"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_notification_channel command."""
        methods = dict(NotificationChannel.Methods.choices).keys()
        parser.add_argument("name", help="Notification channel name")
        parser.add_argument(
            "method", help="Notification method", choices=methods
        )
        parser.add_argument(
            "--data",
            type=argparse.FileType("r"),
            help="File path (or - for stdin) to read the data for "
            "the notification. YAML format. Defaults to stdin.",
            default="-",
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if options["data"] != sys.stdin:
            options["data"].close()

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create NotificationChannel."""
        name = options["name"]
        method = options["method"]
        data = self.parse_yaml_data(options["data"].read())

        try:
            channel = NotificationChannel.objects.create(
                name=name, method=method, data=data
            )
            channel.full_clean()
            channel.save()
        except ValidationError as exc:
            raise CommandError(
                "Error creating notification channel: "
                + "\n".join(exc.messages),
                returncode=3,
            )

        raise SystemExit(0)
