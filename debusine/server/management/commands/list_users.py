# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to list work requests."""

from typing import Any, NoReturn

from django.contrib.auth import get_user_model
from django.core.management import CommandParser

from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import Users


class Command(DebusineBaseCommand):
    """Command to list work requests."""

    help = "List users sorted by date joined."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the list_tokens command."""
        parser.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """List users."""
        users = get_user_model().objects.filter(is_system=False)
        Users(options["yaml"]).print(users, self.stdout)
        raise SystemExit(0)
