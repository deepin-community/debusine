# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to create users."""

import secrets
import string
from typing import Any, NoReturn

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.db import IntegrityError, transaction

from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to create a user."""

    help = "Create a new user. Output generated password on the stdout"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_user command."""
        parser.add_argument("username", help="Username")
        parser.add_argument("email", help="Email for the user")

    @staticmethod
    def _generate_password() -> str:
        characters = string.ascii_letters + string.digits + string.punctuation
        length = 16
        return "".join(secrets.choice(characters) for i in range(length))

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create the user."""
        password = self._generate_password()
        with transaction.atomic():
            try:
                user = get_user_model().objects.create_user(
                    username=options["username"],
                    email=options["email"],
                    password=password,
                )
                user.full_clean()
            except ValidationError as exc:
                raise CommandError(
                    "Error creating user: " + "\n".join(exc.messages),
                    returncode=3,
                )
            except IntegrityError:
                raise CommandError(
                    "A user with this username or email already exists",
                    returncode=3,
                )
            user.save()

        self.stdout.write(password)
        raise SystemExit(0)
