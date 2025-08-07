# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to create tokens."""

from typing import Any, NoReturn

from django.contrib.auth import get_user_model
from django.core.management import CommandError, CommandParser

from debusine.db.models import Token, User
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to create an enabled token."""

    help = "Create a new, enabled token to be used by a Debusine client"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_token command."""
        parser.add_argument(
            "username", help="Username of the user associated to the token"
        )
        parser.add_argument(
            '--comment',
            default='',
            help='Reason for the creation of this token',
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create the token."""
        username = options["username"]

        try:
            user = get_user_model().objects.get(username=username)
        except User.DoesNotExist:
            error = f'Cannot create token: "{username}" does not exist'
            raise CommandError(error, returncode=3)
        token = Token.objects.create(
            user=user, comment=options["comment"], enabled=True
        )
        self.stdout.write(token.key)
        raise SystemExit(0)
