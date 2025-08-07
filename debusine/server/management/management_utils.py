# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utility methods used in management module."""

import abc
import datetime
import io
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, ClassVar, IO, cast

import rich
import yaml
from django.conf import settings
from django.core.management import CommandError
from django.db.models import QuerySet
from rich.table import Table

from debusine.assets.models import AssetCategory
from debusine.db.models import Asset, Scope, WorkerPool, Workspace
from debusine.db.models.assets import AssetUsageRole


@dataclass
class Column:
    """Represent a column in a tabular output."""

    #: Name in YAML
    name: str
    #: Label in tables
    label: str

    def to_data(self, value: Any) -> Any:
        """Return a value for YAML."""
        return value

    def to_rich(self, value: Any) -> str:
        """Return a value for human-readable output."""
        match value:
            case None:
                return "-"
            case datetime.datetime():
                return value.isoformat()
            case _:
                return str(value)


@dataclass
class AttrColumn(Column):
    """Column displaying an attribute of an object."""

    #: Attribute to use for value
    attr: str

    def to_data(self, value: Any) -> Any:
        """Return a value for YAML."""
        return getattr(value, self.attr) if value is not None else None

    def to_rich(self, value: Any) -> str:
        """Return a value for human-readable output."""
        if value is None:
            return "-"
        value = getattr(value, self.attr)
        return super().to_rich(value)


class Printer(abc.ABC):
    """Print tabular values in human or machine readable formats."""

    #: Description of tabular columns
    columns: ClassVar[list[Column]]

    def __init__(self, yaml: bool) -> None:
        """
        Choose the output type.

        :param yaml: use machine-readable YAML
        """
        self.yaml = yaml

    @abc.abstractmethod
    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""

    def print(
        self, qs: QuerySet[Any], file: IO[str] | io.TextIOBase | None = None
    ) -> None:
        """Generate and print output."""
        if self.yaml:
            self.print_yaml(qs, file)
        else:
            self.print_rich(qs, file)

    def print_yaml(
        self, qs: QuerySet[Any], file: IO[str] | io.TextIOBase | None = None
    ) -> None:
        """Generate machine-readable YAML."""
        rows: list[dict[str, Any]] = []
        for row in self.rows(qs):
            rows.append(
                {
                    col.name: col.to_data(val)
                    for col, val in zip(self.columns, row)
                }
            )
        print(yaml.dump(rows), file=file)

    def print_rich(
        self, qs: QuerySet[Any], file: IO[str] | io.TextIOBase | None = None
    ) -> None:
        """Generate human-readable output."""
        table = Table(box=rich.box.MINIMAL_DOUBLE_HEAD)
        for col in self.columns:
            table.add_column(col.label)
        for row in self.rows(qs):
            table.add_row(
                *(col.to_rich(val) for col, val in zip(self.columns, row))
            )
        rich.print(table, file=cast(IO[str], file))


class Tokens(Printer):
    """Display a queryset of tokens."""

    columns = [
        Column("hash", 'Token hash (do not copy)'),
        AttrColumn("user", 'User', "username"),
        Column("created", 'Created'),
        Column("expires", 'Expires'),
        Column("enabled", 'Enabled'),
        Column("comment", 'Comment'),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for token in qs.order_by('user__username', 'created_at'):
            yield [
                token.hash,
                token.user,
                token.created_at,
                token.expire_at,
                token.enabled,
                token.comment,
            ]


class Workers(Printer):
    """Display a queryset of workers."""

    columns = [
        Column("name", 'Name'),
        Column("type", 'Type'),
        Column("registered", 'Registered'),
        Column("connected", 'Connected'),
        AttrColumn("hash", 'Token hash (do not copy)', "hash"),
        AttrColumn("enabled", 'Enabled', "enabled"),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for worker in qs.order_by('registered_at'):
            token = worker.token
            yield [
                worker.name,
                worker.worker_type,
                worker.registered_at,
                worker.connected_at,
                token,
                token,
            ]


class Workspaces(Printer):
    """Display a queryset of workspaces."""

    columns = [
        Column("name", 'Name'),
        Column("public", 'Public'),
        Column("expiration", 'Default Expiration Delay (days)'),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for workspace in qs.order_by('id'):
            yield [
                str(workspace),
                workspace.public,
                workspace.default_expiration_delay.days or "Never",
            ]


class ResourceRoles(Printer):
    """Display a queryset of resource roles."""

    columns = [
        Column("group", 'Group'),
        Column("role", 'Role'),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for role in qs.order_by('group__name', 'role'):
            yield [
                role.group.name,
                role.role,
            ]


class WorkRequests(Printer):
    """Display a queryset of work requests."""

    columns = [
        Column("id", "ID"),
        AttrColumn("worker", "Worker", "name"),
        Column("created_at", "Created"),
        Column("started_at", "Started"),
        Column("completed_at", "Completed"),
        Column("status", "Status"),
        Column("result", "Result"),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for work_request in qs.order_by("created_at"):
            yield [
                work_request.id,
                work_request.worker,
                work_request.created_at,
                work_request.started_at,
                work_request.completed_at,
                work_request.status,
                work_request.result,
            ]


class Users(Printer):
    """Display a queryset of users."""

    columns = [
        Column("username", "User"),
        Column("email", "Email"),
        Column("date_joined", "Joined"),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for user in qs.order_by("date_joined"):
            yield [user.username, user.email, user.date_joined]


class Groups(Printer):
    """Display a queryset of groups."""

    columns = [
        Column("scope", "Scope"),
        Column("group", "Group"),
        Column("users", "Users"),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for group in qs.order_by("name"):
            yield [group.scope.name, group.name, group.users.count()]


def sort_keys(d: dict[str, Any]) -> dict[str, Any]:
    """Order keys of a dict."""
    return dict(sorted(d.items()))


class NotificationChannels(Printer):
    """Display a queryset of notification channels."""

    columns = [
        Column("name", "Name"),
        Column("method", "Method"),
        Column("data", "Data"),
    ]

    def rows(self, qs: QuerySet[Any]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for notification_channel in qs.order_by("name"):
            yield [
                notification_channel.name,
                notification_channel.method,
                sort_keys(notification_channel.data),
            ]


class Assets(Printer):
    """Display a queryset of assets."""

    columns = [
        Column("id", "ID"),
        Column("category", "Category"),
        Column("workspace", "Workspace"),
        Column("data", "Data"),
    ]

    def rows(self, qs: QuerySet[Asset]) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for asset in qs.order_by("id"):
            data: dict[str, Any] = {}
            match asset.category:
                case AssetCategory.SIGNING_KEY:
                    data = {
                        "fingerprint": asset.data["fingerprint"],
                        "purpose": asset.data["purpose"],
                    }
                case _:  # pragma: no cover
                    data = asset.data
            yield [asset.id, asset.category, str(asset.workspace), data]


class AssetUsageRoles(Printer):
    """Display a queryset of asset usage roles."""

    columns = [
        Column("workspace", "Workspace"),
        Column("group", "Group"),
        Column("role", "Role"),
    ]

    def rows(
        self, qs: QuerySet[AssetUsageRole]
    ) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for asset_usage_role in qs.order_by("id"):
            yield [
                asset_usage_role.resource.workspace.name,
                asset_usage_role.group.name,
                asset_usage_role.role,
            ]


class WorkerPools(Printer):
    """Display a queryset of worker_pools."""

    columns = [
        Column("name", "Name"),
        Column("provider_account", "Provider Account"),
        Column("enabled", "Enabled"),
        Column("specifications", "Specifications"),
        Column("limits", "Limits"),
    ]

    def rows(
        self, qs: QuerySet[WorkerPool]
    ) -> Generator[list[Any], None, None]:
        """Generate rows to display."""
        for worker_pool in qs.order_by("id"):
            yield [
                worker_pool.name,
                worker_pool.provider_account.data["name"],
                worker_pool.enabled,
                sort_keys(worker_pool.specifications),
                sort_keys(worker_pool.limits),
            ]


def get_scope(scope_name: str) -> Scope:
    """Look up a scope by name."""
    try:
        return Scope.objects.get(name=scope_name)
    except Scope.DoesNotExist:
        raise CommandError(f"Scope {scope_name!r} not found", returncode=3)


def get_workspace(name: str, require_scope: bool = False) -> Workspace:
    """Get a workspace from the user-provided name."""
    if "/" in name:
        scope_name, workspace_name = name.split("/", 1)
    elif require_scope:
        raise CommandError(
            (
                f"scope_workspace {name!r} should be in the form "
                f"'scopename/workspacename'"
            ),
            returncode=3,
        )
    else:
        scope_name = settings.DEBUSINE_DEFAULT_SCOPE
        workspace_name = name

    scope = get_scope(scope_name)
    try:
        return Workspace.objects.get(name=workspace_name, scope=scope)
    except Workspace.DoesNotExist:
        raise CommandError(
            f"Workspace {workspace_name!r} not found in scope {scope.name!r}",
            returncode=3,
        )
