# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-admin command to manage workspaces.

Note: to make commands easier to be invoked from Ansible, we take care to make
them idempotent.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any, NoReturn, cast

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.db import transaction

from debusine.artifacts.models import SINGLETON_COLLECTION_CATEGORIES
from debusine.db.context import context
from debusine.db.models import (
    Collection,
    Group,
    Scope,
    Workspace,
    default_workspace,
)
from debusine.db.models.workspaces import DeleteWorkspaces, WorkspaceRole
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import (
    ResourceRoles,
    Workspaces,
    get_scope,
    get_workspace,
)


class Command(DebusineBaseCommand):
    """Command to manage workspaces."""

    help = "Manage workspaces"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        define = subparsers.add_parser(
            "define",
            aliases=["create"],
            help="Create a workspace with defaults"
            " if it doesn't exist. Modify it with the specified"
            " parameters if it doesn't.",
        )
        define.add_argument(
            "scope_workspace",
            metavar="scope/name",
            help="scope/name for new workspace",
        )
        define.add_argument(
            "owners_group",
            nargs="?",
            help="Name of the owners group for the new workspace,"
            " mandatory when creating workspace",
        )
        public = define.add_mutually_exclusive_group()
        public.add_argument("--public", action="store_true", default=None)
        public.add_argument(
            "--private",
            action="store_false",
            dest="public",
            help='Public permissions (default: private)',
        )
        define.add_argument(
            "--default-expiration-delay",
            action="store",
            type=int,
            metavar="N",
            default=None,
            help="Minimal time (in days) that a new artifact is kept in the"
            " workspace before being expired, 0 means no expiration"
            " (default: 30)",
        )
        define.add_argument(
            "--no-singleton-collections",
            dest="singleton_collections",
            action="store_false",
            help=(
                "Don't create the usual singleton collections for this "
                "workspace (default: create singleton collections)"
            ),
        )

        rename = subparsers.add_parser(
            "rename", help=self.handle_rename.__doc__
        )
        rename.add_argument(
            "scope_workspace", metavar="scope/name", help="Workspace to rename"
        )
        rename.add_argument("new_name", help="New name for the workspace")

        delete = subparsers.add_parser(
            "delete", help=self.handle_delete.__doc__
        )
        delete.add_argument(
            "scope_workspace", metavar="scope/name", help="Workspace to delete"
        )
        delete.add_argument(
            '--yes', action='store_true', help='Skips confirmation of deletion'
        )

        list_sp = subparsers.add_parser("list", help=self.handle_list.__doc__)
        list_sp.add_argument(
            "--yaml", action="store_true", help="Machine readable YAML output"
        )
        list_sp.add_argument(
            "scope",
            help="list workspaces for this scope",
            nargs='?',
            default=None,
        )

        list_roles = subparsers.add_parser(
            "list_roles", help=self.handle_list_roles.__doc__
        )
        list_roles.add_argument(
            "scope_workspace", metavar="scope/name", help="Workspace to inspect"
        )
        list_roles.add_argument(
            "--yaml", action="store_true", help="Machine readable YAML output"
        )

        grant_role = subparsers.add_parser(
            "grant_role", help=self.handle_grant_role.__doc__
        )
        grant_role.add_argument(
            "scope_workspace", metavar="scope/name", help="Workspace to edit"
        )
        grant_role.add_argument("role", help="Name of the role to assign")
        grant_role.add_argument(
            "groups",
            nargs='+',
            metavar="group",
            help="Group(s) getting the role",
        )

        revoke_role = subparsers.add_parser(
            "revoke_role", help=self.handle_revoke_role.__doc__
        )
        revoke_role.add_argument(
            "scope_workspace", metavar="scope/name", help="Workspace to edit"
        )
        revoke_role.add_argument("role", help="Name of the role to revoke")
        revoke_role.add_argument(
            "groups",
            nargs='+',
            metavar="group",
            help="Group(s) dropping the role",
        )

    def get_scope_and_workspace_name(
        self, scope_workspace: str
    ) -> tuple[Scope, str]:
        """Lookup a scopename/workspacename string."""
        if "/" not in scope_workspace:
            raise CommandError(
                f"scope_workspace {scope_workspace!r} should be in the form"
                " 'scopename/workspacename'",
                returncode=3,
            )
        scope_name, workspace_name = scope_workspace.split("/", 1)
        return get_scope(scope_name), workspace_name

    def get_group(self, scope: Scope, group_name: str) -> Group:
        """Lookup a group in a scope."""
        try:
            group = Group.objects.get(scope=scope, name=group_name)
        except Group.DoesNotExist:
            raise CommandError(
                f"Group {group_name!r} not found in scope {scope.name!r}",
                returncode=3,
            )
        return group

    @context.disable_permission_checks()
    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Dispatch the requested action."""
        func = cast(
            Callable[..., NoReturn],
            getattr(self, f"handle_{options['action']}", None),
        )
        func(*args, **options)

    def handle_define(
        self,
        *,
        scope_workspace: str,
        owners_group: str,
        **options: Any,
    ) -> NoReturn:
        """
        Ensure a workspace exists, with an Owners group.

        This is idempotent, and it makes sure the named workspace
        exists, it has an "Owners" group, and that group has the ADMIN
        role on the workspace.
        """
        scope, workspace_name = self.get_scope_and_workspace_name(
            scope_workspace
        )

        new_workspace = False
        with transaction.atomic():
            try:
                try:
                    workspace = Workspace.objects.get(
                        scope=scope, name=workspace_name
                    )
                except Workspace.DoesNotExist:
                    new_workspace = True
                    workspace = Workspace(
                        name=workspace_name,
                        scope=scope,
                        # defaults:
                        public=False,
                        default_expiration_delay=timedelta(30),
                    )
                if options["public"] is not None:
                    workspace.public = options["public"]
                if options["default_expiration_delay"] is not None:
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
                if new_workspace and owners_group is None:
                    raise CommandError(
                        "Error: owners_group is required"
                        " when creating workspace",
                        returncode=3,
                    )
                if owners_group is not None:
                    admin_group = self.get_group(scope, owners_group)
                    admin_group.assign_role(workspace, "owner")
            except ValidationError as exc:
                raise CommandError(
                    "Error creating workspace: " + "\n".join(exc.messages),
                    returncode=3,
                )

        raise SystemExit(0)

    handle_create = handle_define

    def handle_rename(
        self, *, scope_workspace: str, new_name: str, **options: Any
    ) -> NoReturn:
        """Rename a workspace."""
        workspace = get_workspace(scope_workspace)

        if new_name == workspace.name:
            raise SystemExit(0)

        workspace.name = new_name
        try:
            workspace.full_clean()
        except ValidationError as exc:
            self.stderr.write("Renamed workspace would be invalid:")
            for field, errors in exc.message_dict.items():
                for error in errors:
                    self.stderr.write(f"* {field}: {error}")
            raise SystemExit(3)

        workspace.save()

        raise SystemExit(0)

    def handle_list(self, **options: Any) -> NoReturn:
        """List workspaces in a scope."""
        workspaces = Workspace.objects.all()
        if options["scope"] is not None:
            scope = get_scope(options["scope"])
            workspaces = workspaces.filter(scope=scope)
        Workspaces(options["yaml"]).print(workspaces, self.stdout)
        raise SystemExit(0)

    def handle_list_roles(
        self, scope_workspace: str, **options: Any
    ) -> NoReturn:
        """List workspace's roles."""
        workspace = get_workspace(scope_workspace)

        workspace_roles = WorkspaceRole.objects.filter(resource=workspace)
        ResourceRoles(options["yaml"]).print(workspace_roles, self.stdout)
        raise SystemExit(0)

    def handle_grant_role(
        self, scope_workspace: str, role: str, groups: list[str], **options: Any
    ) -> NoReturn:
        """Assign workspace's role to groups."""
        workspace = get_workspace(scope_workspace)
        try:
            for group_name in groups:
                group = self.get_group(workspace.scope, group_name)
                group.assign_role(workspace, role)
        except ValueError as exc:
            raise CommandError(
                "Error assigning role: " + str(exc),
                returncode=3,
            )
        raise SystemExit(0)

    def handle_revoke_role(
        self, scope_workspace: str, role: str, groups: list[str], **options: Any
    ) -> NoReturn:
        """Revoke workspace's role from groups."""
        workspace = get_workspace(scope_workspace)
        for group_name in groups:
            group = self.get_group(workspace.scope, group_name)
            WorkspaceRole.objects.filter(
                resource=workspace, group=group, role=role
            ).delete()
        raise SystemExit(0)

    def handle_delete(
        self, *, scope_workspace: str, yes: bool, **options: Any
    ) -> NoReturn:
        """Delete a workspace with associated resources."""
        workspace = get_workspace(scope_workspace)

        # Prevent deletion of default workspace
        if workspace == default_workspace():
            raise CommandError(
                f"Workspace {scope_workspace} cannot be deleted",
                returncode=3,
            )

        deletion_confirmed = False
        if yes:
            deletion_confirmed = True
        else:
            deletion_answer = input(
                f"Would you like to delete workspace {scope_workspace}? [yN] "
            )
            deletion_confirmed = deletion_answer.strip() in ('y', 'Y')

        if not deletion_confirmed:
            raise SystemExit(0)

        with transaction.atomic():
            operation = DeleteWorkspaces(
                Workspace.objects.filter(pk=workspace.pk)
            )
            operation.perform_deletions()

        raise SystemExit(0)
