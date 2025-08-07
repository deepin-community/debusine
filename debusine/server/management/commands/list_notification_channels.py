# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to list notification channels."""

from typing import Any, NoReturn

from django.core.management import CommandParser

from debusine.db.models import NotificationChannel
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import NotificationChannels


class Command(DebusineBaseCommand):
    """Command to list notification channels."""

    help = "List notification channels"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the list_tokens command."""
        parser.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """List notification channels."""
        notification_channels = NotificationChannel.objects.all()
        NotificationChannels(options["yaml"]).print(
            notification_channels, self.stdout
        )
        raise SystemExit(0)
