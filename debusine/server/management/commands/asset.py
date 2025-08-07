# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-admin command to manage assets.

Note: to make commands easier to be invoked from Ansible, we take care to make
them idempotent.
"""

import argparse
import sys
from collections.abc import Callable
from functools import partial
from typing import Any, IO, NoReturn, cast

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.db import IntegrityError, transaction

from debusine.assets import AssetCategory
from debusine.db.context import context
from debusine.db.models import Asset, Group, Scope, Workspace
from debusine.db.models.assets import (
    AssetRole,
    AssetRoles,
    AssetUsage,
    AssetUsageRole,
    AssetUsageRoles,
)
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import (
    AssetUsageRoles as AssetUsageRolesPrinter,
)
from debusine.server.management.management_utils import (
    Assets,
    ResourceRoles,
    get_scope,
    get_workspace,
)


class Command(DebusineBaseCommand):
    """Command to manage assets."""

    help = "Manage assets"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        create = subparsers.add_parser(
            "create", help=self.handle_create.__doc__
        )
        create.add_argument("category", help="Asset category")
        create.add_argument(
            "--workspace",
            metavar="scope/name",
            type=partial(get_workspace, require_scope=True),
            help="Create asset in this workspace",
        )
        create.add_argument(
            "--data",
            dest="data_file",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the data for the asset. "
                "YAML format. Defaults to stdin."
            ),
            default="-",
        )

        delete = subparsers.add_parser(
            "delete", help=self.handle_delete.__doc__
        )
        delete.add_argument(
            "asset",
            metavar="id",
            type=self.get_asset_by_id,
            help="Asset to delete",
        )
        delete.add_argument(
            '--yes', action='store_true', help='Skips confirmation of deletion'
        )

        list_ = subparsers.add_parser("list", help=self.handle_list.__doc__)
        list_.add_argument(
            "--yaml", action="store_true", help="Machine readable YAML output"
        )
        list_.add_argument(
            "--scope",
            type=get_scope,
            help="list only assets in this scope",
        )
        list_.add_argument(
            "--workspace",
            metavar="scope/name",
            type=partial(get_workspace, require_scope=True),
            help="list only assets in this workspace",
        )

        list_roles = subparsers.add_parser(
            "list_roles", help=self.handle_list_roles.__doc__
        )
        list_roles.add_argument(
            "asset",
            type=self.get_asset_by_id,
            metavar="id",
            help="Asset to inspect",
        )
        list_roles.add_argument(
            "--yaml", action="store_true", help="Machine readable YAML output"
        )

        grant_role = subparsers.add_parser(
            "grant_role", help=self.handle_grant_role.__doc__
        )
        grant_role.add_argument(
            "--workspace",
            metavar="scope/name",
            type=partial(get_workspace, require_scope=True),
            help="Workspace to apply permissions in",
        )
        grant_role.add_argument(
            "asset",
            metavar="id",
            type=self.get_asset_by_id,
            help="Asset to grant permissions on",
        )
        grant_role.add_argument("role_name", help="Name of the role to assign")
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
            "--workspace",
            metavar="scope/name",
            type=partial(get_workspace, require_scope=True),
            help="Workspace to apply permissions in",
        )
        revoke_role.add_argument(
            "asset",
            type=self.get_asset_by_id,
            metavar="id",
            help="Asset to grant permissions on",
        )
        revoke_role.add_argument("role_name", help="Name of the role to revoke")
        revoke_role.add_argument(
            "groups",
            nargs='+',
            metavar="group",
            help="Group(s) dropping the role",
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if "data_file" in options and options["data_file"] != sys.stdin:
            options["data_file"].close()

    def get_asset_by_id(self, id_: int | str) -> Asset:
        """Lookup an asset by ID."""
        try:
            return Asset.objects.get(id=int(id_))
        except Asset.DoesNotExist:
            raise CommandError(f"Asset ID {id_!r} not found", returncode=3)

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

    def get_role_by_name(self, role_name: str, usage_role: bool) -> str:
        """Return the Roles object referring to role_name."""
        role_class = AssetUsageRoles if usage_role else AssetRoles
        try:
            return role_class(role_name)
        except ValueError as e:
            raise CommandError(e, returncode=3)

    def assert_workspace_in_scope(
        self, workspace: Workspace, scope: Scope
    ) -> None:
        """Ensure that workspace is within scope."""
        if scope != workspace.scope:
            raise CommandError(
                f"{workspace!r} is not in {scope!r}",
                returncode=3,
            )

    @context.disable_permission_checks()
    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Dispatch the requested action."""
        func = cast(
            Callable[..., NoReturn],
            getattr(self, f"handle_{options['action']}", None),
        )
        func(*args, **options)

    def handle_create(
        self,
        category: str,
        workspace: Workspace | None,
        data_file: IO[Any],
        **options: Any,
    ) -> NoReturn:
        """Create or update an asset."""
        data = self.parse_yaml_data(data_file.read()) or {}

        # For idempotency, we try to match the known unique constraints for
        # various asset categories.
        filters: dict[str, Any] = {}
        match category:
            case AssetCategory.CLOUD_PROVIDER_ACCOUNT:
                filters["data__name"] = data.get("name")
            case AssetCategory.SIGNING_KEY:
                filters["data__fingerprint"] = data.get("fingerprint")
            case _:  # pragma: no cover
                # No other asset categories are defined yet.
                pass

        with transaction.atomic():
            try:
                try:
                    asset = Asset.objects.get(
                        category=category, workspace=workspace, **filters
                    )
                except Asset.DoesNotExist:
                    asset = Asset.objects.create(
                        category=category, workspace=workspace, data=data
                    )
                if asset.data != data:
                    asset.data = data
                # TODO: We can't validate constraints here, because
                # JsonDataUniqueConstraint doesn't implement validate().  If
                # there's a constraint violation then we'll get an
                # IntegrityError instead.
                asset.full_clean(validate_constraints=False)
                asset.save()
            except ValidationError as exc:
                raise CommandError(
                    "Error creating asset: " + "\n".join(exc.messages),
                    returncode=3,
                )
            except IntegrityError as exc:
                raise CommandError(f"Error creating asset: {exc}", returncode=3)

        raise SystemExit(0)

    def handle_list(
        self,
        scope: Scope | None,
        workspace: Workspace | None,
        yaml: bool,
        **options: Any,
    ) -> NoReturn:
        """List assets in a scope."""
        if scope and workspace:
            self.assert_workspace_in_scope(workspace, scope)
        assets = Asset.objects.all()
        if scope is not None:
            assets = assets.filter(workspace__scope=scope)
        if workspace is not None:
            assets = assets.filter(workspace=workspace)
        Assets(yaml).print(assets, self.stdout)
        raise SystemExit(0)

    def handle_list_roles(
        self, asset: Asset, yaml: bool, **options: Any
    ) -> NoReturn:
        """List assets's roles."""
        ResourceRoles(yaml).print(asset.roles.all(), self.stdout)
        usage_roles = AssetUsageRole.objects.filter(resource__asset=asset).all()
        if yaml:
            print("---")
        AssetUsageRolesPrinter(yaml).print(usage_roles, self.stdout)
        raise SystemExit(0)

    def handle_grant_role(
        self,
        asset: Asset,
        role_name: str,
        groups: list[str],
        workspace: Workspace | None,
        **options: Any,
    ) -> NoReturn:
        """Assign role on asset to groups."""
        role = self.get_role_by_name(
            role_name, usage_role=workspace is not None
        )
        resource: Asset | AssetUsage = asset
        if workspace:
            if asset.workspace:
                self.assert_workspace_in_scope(workspace, asset.workspace.scope)
            resource, _ = AssetUsage.objects.get_or_create(
                asset=asset, workspace=workspace
            )
        if asset.workspace:
            scope = asset.workspace.scope
        elif workspace:
            scope = workspace.scope
        else:
            raise CommandError(
                "Cannot grant roles on an asset not assigned to a workspace"
            )
        for group_name in groups:
            group = self.get_group(scope, group_name)
            group.assign_role(resource, role)
        raise SystemExit(0)

    def handle_revoke_role(
        self,
        asset: Asset,
        workspace: Workspace | None,
        role_name: str,
        groups: list[str],
        **options: Any,
    ) -> NoReturn:
        """Revoke role on asset from groups."""
        if asset.workspace:
            scope = asset.workspace.scope
        elif workspace:
            scope = workspace.scope
        else:
            raise CommandError(
                "Cannot revoke roles on an asset not assigned to a workspace"
            )
        if workspace and asset.workspace:
            self.assert_workspace_in_scope(workspace, asset.workspace.scope)
        role = self.get_role_by_name(
            role_name, usage_role=workspace is not None
        )
        for group_name in groups:
            group = self.get_group(scope, group_name)
            if workspace:
                AssetUsageRole.objects.filter(
                    resource__asset=asset, group=group, role=role
                ).delete()
            else:
                AssetRole.objects.filter(
                    resource=asset, group=group, role=role
                ).delete()
        raise SystemExit(0)

    def handle_delete(
        self, *, asset: Asset, yes: bool, **options: Any
    ) -> NoReturn:
        """Delete an asset with associated resources."""
        deletion_confirmed = False
        if yes:
            deletion_confirmed = True
        else:
            deletion_answer = input(
                f"Would you like to delete asset {asset}? [yN] "
            )
            deletion_confirmed = deletion_answer.strip() in ('y', 'Y')

        if not deletion_confirmed:
            self.stderr.write("Not deleted.\n")
            raise SystemExit(1)

        asset.delete()
        raise SystemExit(0)
