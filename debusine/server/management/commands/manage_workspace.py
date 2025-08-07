# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage workspaces."""

from datetime import timedelta
from typing import Any, NoReturn

from django.core.management import CommandError, CommandParser

from debusine.db.models import Scope, Workspace
from debusine.django.management.debusine_base_command import DebusineBaseCommand

# TODO: this is deprecated in favor of the 'workspace' command


class Command(DebusineBaseCommand):
    """Command to manage workspaces."""

    help = "Manage workspaces"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the manage_workspace command."""
        parser.add_argument(
            "scope_workspace",
            metavar="scope/name",
            help="scope/name of workspace to modify",
        )
        public = parser.add_mutually_exclusive_group()
        public.add_argument("--public", action="store_true", default=None)
        public.add_argument(
            "--private",
            action="store_false",
            dest="public",
            help='Public permissions (default: private)',
        )
        parser.add_argument(
            "--default-expiration-delay",
            action="store",
            type=int,
            metavar="N",
            help="Minimal time (in days) that a new artifact is kept in the"
            " workspace before being expired (default: 0)",
        )

    def handle(
        self, scope_workspace: str, *args: Any, **options: Any
    ) -> NoReturn:
        """Manage the workspace."""
        scope_name, workspace_name = scope_workspace.split("/", 1)
        try:
            scope = Scope.objects.get(name=scope_name)
        except Scope.DoesNotExist:
            raise CommandError(
                f'Scope "{scope_name}" not found',
                returncode=3,
            )
        try:
            workspace = Workspace.objects.get(name=workspace_name, scope=scope)
        except Workspace.DoesNotExist:
            raise CommandError(
                f'Workspace "{workspace_name}" not found', returncode=3
            )

        if options["public"] is not None:
            workspace.public = options["public"]
        if options["default_expiration_delay"] is not None:
            workspace.default_expiration_delay = timedelta(
                days=options["default_expiration_delay"]
            )
        workspace.save()

        raise SystemExit(0)
