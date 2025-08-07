# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to create collections."""

import argparse
import sys
from typing import Any, NoReturn

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser

from debusine.db.models import Collection
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import get_workspace


class Command(DebusineBaseCommand):
    """Command to create a collection."""

    help = "Create a new collection"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_collection command."""
        parser.add_argument("name", help="Name")
        parser.add_argument("category", help="Category")
        parser.add_argument(
            "--workspace",
            metavar="scope/name",
            help="Workspace",
            default=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )
        parser.add_argument(
            "--data",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the data for the "
                "collection. YAML format. Defaults to stdin."
            ),
            default="-",
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if options["data"] != sys.stdin:
            options["data"].close()

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create the collection."""
        name = options["name"]
        category = options["category"]
        data = self.parse_yaml_data(options["data"].read()) or {}

        workspace = get_workspace(options["workspace"])

        try:
            collection = Collection(
                name=name, category=category, workspace=workspace, data=data
            )
            collection.full_clean()
            collection.save()
        except ValidationError as exc:
            raise CommandError(
                "Error creating collection: " + "\n".join(exc.messages),
                returncode=3,
            )

        raise SystemExit(0)
