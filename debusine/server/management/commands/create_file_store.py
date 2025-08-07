# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to create file stores."""

import argparse
import warnings
from typing import Any, IO, NoReturn

from django.core.management import CommandParser

from debusine.db.models import FileStore
from debusine.server.management.commands import file_store


class Command(file_store.Command):
    """Command to create a file store."""

    help = """
    Create a file store.

    Deprecated; use `debusine-admin file_store create` instead.
    """

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_file_store command."""
        parser.add_argument("name", help="Name")
        parser.add_argument(
            "backend",
            help="Backend type",
            choices=dict(FileStore.BackendChoices.choices).keys(),
        )
        parser.add_argument(
            "--configuration",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the configuration for the "
                "file store. YAML format. Defaults to stdin."
            ),
            default="-",
        )

    def handle(
        self, *, name: str, backend: str, configuration: IO[Any], **options: Any
    ) -> NoReturn:
        """Forward to `debusine-admin file_store create`."""
        warnings.warn(
            "The `debusine-admin create_file_store` command has been "
            "deprecated in favour of `debusine-admin file_store create`",
            DeprecationWarning,
        )
        super().handle_create(
            name=name, backend=backend, configuration=configuration, **options
        )
