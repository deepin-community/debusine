# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to edit workers metadata."""

import warnings
from typing import Any, NoReturn

from django.core.management import CommandParser

from debusine.server.management.commands import worker


class Command(worker.Command):
    """Command to edit the metadata of a Worker."""

    help = """
    Edit worker's metadata.

    Deprecated; use `debusine-admin worker edit_metadata` instead.
    """

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the edit_worker_metadata command."""
        parser.add_argument(
            '--set',
            help=(
                "Filename with the metadata in YAML"
                " (note: its contents will be displayed to users in the web UI)"
            ),
            metavar='PATH',
        )
        parser.add_argument(
            'worker_name',
            help='Name of the worker of which the metadata will be edited',
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Forward to `debusine-admin worker edit_metadata`."""
        warnings.warn(
            "The `debusine-admin edit_worker_metadata` command has been "
            "deprecated in favour of `debusine-admin worker edit_metadata`",
            DeprecationWarning,
        )
        options = dict(options)
        options["worker"] = options["worker_name"]
        del options["worker_name"]
        super().handle_edit_metadata(*args, **options)
