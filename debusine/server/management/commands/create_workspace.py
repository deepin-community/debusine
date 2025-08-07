# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to create workspaces."""

from datetime import timedelta
from typing import Any, NoReturn

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser

from debusine.artifacts.models import SINGLETON_COLLECTION_CATEGORIES
from debusine.db.context import context
from debusine.db.models import Collection, Group, Scope, Workspace
from debusine.django.management.debusine_base_command import DebusineBaseCommand

# TODO: this is deprecated in favor of the 'workspace' command


class Command(DebusineBaseCommand):
    """Command to create a workspace."""

    help = "Create a new workspace"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_workspace command."""
        parser.add_argument(
            "scope_workspace",
            metavar="scope/name",
            help="scope/name for new workspace",
        )
        parser.add_argument(
            "--public",
            action="store_true",
            help="Public permissions (default: private)",
        )
        parser.add_argument(
            "--default-expiration-delay",
            action="store",
            type=int,
            metavar="N",
            help="Minimal time (in days) that a new artifact is kept in the"
            " workspace before being expired (default: 0)",
        )
        parser.add_argument(
            "--no-singleton-collections",
            dest="singleton_collections",
            action="store_false",
            help=(
                "Don't create the usual singleton collections for this "
                "workspace (default: create singleton collections)"
            ),
        )
        parser.add_argument(
            "--with-owners-group",
            type=str,
            nargs="?",
            metavar="name",
            help="Name of the owners groups for the workspace"
            " (optional name defaults to Owners-workspacename)",
        )

    @context.disable_permission_checks()
    def handle(
        self, scope_workspace: str, *args: Any, **options: Any
    ) -> NoReturn:
        """Create the workspace."""
        scope_name, workspace_name = scope_workspace.split("/", 1)
        try:
            scope = Scope.objects.get(name=scope_name)
            workspace, _ = Workspace.objects.get_or_create(
                name=workspace_name, scope=scope
            )
            if options["public"]:
                workspace.public = True
            if options["default_expiration_delay"]:
                workspace.default_expiration_delay = timedelta(
                    days=options["default_expiration_delay"]
                )
            workspace.full_clean()
            workspace.save()
            if options["singleton_collections"]:
                for category in SINGLETON_COLLECTION_CATEGORIES:
                    Collection.objects.get_or_create_singleton(
                        category, workspace
                    )

            # Make sure the workspace has an Owners group
            if options["with_owners_group"] is None:
                options["with_owners_group"] = "Owners-" + workspace_name
            admin_group = Group.objects.get(
                scope=scope, name=options["with_owners_group"]
            )

            admin_group.assign_role(workspace, "owner")
        except ValidationError as exc:
            raise CommandError(
                "Error creating workspace: " + "\n".join(exc.messages),
                returncode=3,
            )
        except Scope.DoesNotExist:
            raise CommandError(
                f'Scope "{scope_name}" not found',
                returncode=3,
            )
        except Group.DoesNotExist:
            raise CommandError(
                f'Group "{options["with_owners_group"]}" not found',
                returncode=3,
            )

        raise SystemExit(0)
