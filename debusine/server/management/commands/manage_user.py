# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage users."""

from typing import Any, NoReturn

from django.contrib.auth import get_user_model
from django.core.management import CommandError, CommandParser

from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to manage users."""

    help = "Manage users"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the manage_user command."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        # change email
        change_email = subparsers.add_parser("change-email")
        change_email.add_argument("username", help="Username to modify")
        change_email.add_argument("email", help="New email to set")

    def handle(self, *args: Any, **options: Any) -> None:
        """Manage the user."""
        if options["action"] == "change-email":
            self._change_email(options["username"], options["email"])
        else:  # pragma: no cover
            pass  # pragma: no cover

    def _change_email(self, username: str, email: str) -> NoReturn:
        try:
            user = get_user_model().objects.get(
                username=username, is_system=False
            )
        except get_user_model().DoesNotExist:
            raise CommandError(f'Username "{username}" not found', returncode=3)

        user.email = email
        user.save()

        raise SystemExit(0)
