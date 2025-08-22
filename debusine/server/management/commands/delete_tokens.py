# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to delete tokens. Allows filtering."""

from typing import Any

from django.core.management import CommandError, CommandParser

from debusine.db.models import Token
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import Tokens


class Command(DebusineBaseCommand):
    """Command to delete tokens."""

    help = (
        "Delete existing tokens. By default it requests confirmation "
        "before deletion"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the remove_tokens command."""
        parser.add_argument(
            '--yes', action='store_true', help='Skips confirmation of deletion'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Does not fail if trying to delete nonexistent tokens',
        )
        parser.add_argument('--token', help='Deletes only this token')
        parser.add_argument(
            '--username', help='Deletes tokens owned by USERNAME'
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Delete (if selected and confirmed) tokens."""
        tokens = Token.objects.get_tokens(
            username=options["username"],
            key=options["token"],
            include_expired=True,
        )

        if not tokens:
            error_message = 'There are no tokens to be deleted'
            if options['force']:
                self.stdout.write(error_message)
                return
            else:
                raise CommandError(error_message, returncode=3)

        Tokens(yaml=False).print(tokens, self.stdout)

        deletion_confirmed = False
        deletion_forced = options['yes']
        if not deletion_forced:
            deletion_answer = input("Would you like to delete them? [yN] ")
            deletion_confirmed = deletion_answer.strip() in ('y', 'Y')

        if deletion_forced or deletion_confirmed:
            tokens.delete()
