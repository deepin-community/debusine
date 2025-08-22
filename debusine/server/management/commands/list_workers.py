# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to list workers."""

import warnings
from typing import Any, NoReturn

from django.core.management import CommandParser

from debusine.server.management.commands import worker


class Command(worker.Command):
    """Command to list the workers."""

    help = "List all the workers"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the list_tokens command."""
        parser.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Forward to `debusine-admin worker list`."""
        warnings.warn(
            "The `debusine-admin list_workers` command has been deprecated in "
            "favour of `debusine-admin worker list`",
            DeprecationWarning,
        )
        super().handle_list(*args, **options)
