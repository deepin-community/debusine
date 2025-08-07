# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to delete a notification channel."""

from typing import Any, NoReturn

from django.core.management import CommandError, CommandParser

from debusine.db.models import NotificationChannel
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to delete a notification channel."""

    help = "Delete a notification channel"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_notification_channel command."""
        parser.add_argument("name", help="Notification channel name")

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create NotificationChannel."""
        name = options["name"]

        try:
            notification_channel = NotificationChannel.objects.get(name=name)
        except NotificationChannel.DoesNotExist:
            raise CommandError(
                f'NotificationChannel "{name}" does not exist', returncode=3
            )

        notification_channel.delete()

        raise SystemExit(0)
