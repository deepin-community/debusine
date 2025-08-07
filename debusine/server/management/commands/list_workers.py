# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to list workers."""

from typing import Any

from django.core.management import CommandParser

from debusine.db.models import Worker
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import Workers


class Command(DebusineBaseCommand):
    """Command to list the workers."""

    help = "List all the workers"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the list_tokens command."""
        parser.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """List the workers."""
        workers = Worker.objects.all()
        Workers(options["yaml"]).print(workers, self.stdout)
        raise SystemExit(0)
