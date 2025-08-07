# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to delete workspaces."""

from typing import Any

from django.core.management import CommandError, CommandParser
from django.db import transaction

from debusine.db.models import Scope, Workspace, default_workspace
from debusine.db.models.workspaces import DeleteWorkspaces
from debusine.django.management.debusine_base_command import DebusineBaseCommand

# TODO: this is deprecated in favor of the 'workspace' command


class Command(DebusineBaseCommand):
    """Command to delete a workspace."""

    help = (
        "Delete a workspace. By default it requests confirmation"
        "before deletion"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_workspace command."""
        parser.add_argument(
            "scope_workspace",
            metavar="scope/name",
            help="scope/name of workspace to delete",
        )
        parser.add_argument(
            '--yes', action='store_true', help='Skips confirmation of deletion'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Does not fail if trying to delete nonexistent workspaces',
        )

    def handle(
        self, *, scope_workspace: str, yes: bool, force: bool, **options: Any
    ) -> None:
        """Delete the workspace."""
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
            if force:
                return
            else:
                raise CommandError(
                    f"Workspace {workspace_name} does not exist"
                    f" in scope {scope_name}",
                    returncode=3,
                )

        # Prevent deletion of default workspace
        if workspace == default_workspace():
            raise CommandError(
                f"Workspace {workspace_name} cannot be deleted",
                returncode=3,
            )

        deletion_confirmed = False
        if yes:
            deletion_confirmed = True
        else:
            deletion_answer = input(
                f"Would you like to delete workspace {workspace_name}? [yN] "
            )
            deletion_confirmed = deletion_answer.strip() in ('y', 'Y')

        if not deletion_confirmed:
            return

        with transaction.atomic():
            operation = DeleteWorkspaces(
                Workspace.objects.filter(pk=workspace.pk)
            )
            operation.perform_deletions()

        raise SystemExit(0)
