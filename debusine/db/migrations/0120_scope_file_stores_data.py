# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration for `Scope.file_stores`."""

from operator import attrgetter

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F, Model


def move_workspace_file_stores_to_scope(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Move `Workspace.*_file_stores` to `Scope.file_stores`."""
    Scope = apps.get_model("db", "Scope")

    for scope in Scope.objects.all():
        default_file_store = None
        other_file_stores: list[Model] | None = None
        for workspace in scope.workspaces.all():
            if (
                default_file_store is not None
                and default_file_store != workspace.default_file_store
            ):
                raise RuntimeError(
                    f"Scope {scope.name!r} has workspaces with different "
                    f"default file stores; make them agree first"
                )
            default_file_store = workspace.default_file_store

            workspace_other_file_stores = sorted(
                workspace.other_file_stores.all(), key=attrgetter("id")
            )
            if (
                other_file_stores is not None
                and other_file_stores != workspace_other_file_stores
            ):
                raise RuntimeError(
                    f"Scope {scope.name!r} has workspaces with different "
                    f"other file stores; make them agree first"
                )
            other_file_stores = workspace_other_file_stores

        if default_file_store is not None:
            scope.file_stores.set(
                [default_file_store],
                through_defaults={
                    "upload_priority": 100,
                    "download_priority": 100,
                },
            )
        if other_file_stores:
            scope.file_stores.add(*other_file_stores)


def move_scope_file_stores_to_workspace(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Move `Scope.file_stores` to `Workspace.*_file_stores`."""
    Scope = apps.get_model("db", "Scope")
    FileStoreInScope = apps.get_model("db", "FileStoreInScope")

    for scope in Scope.objects.all():
        if scope.workspaces.exists():
            default_file_store = scope.filestoreinscope_set.order_by(
                F("upload_priority").desc(nulls_last=True)
            )[0].file_store
            other_file_stores = list(
                scope.file_stores.exclude(pk=default_file_store.pk)
            )
            for workspace in scope.workspaces.all():
                workspace.default_file_store = default_file_store
                workspace.other_file_stores.set(other_file_stores)
                workspace.save()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0119_scope_file_stores_initial"),
    ]

    operations = [
        migrations.RunPython(
            move_workspace_file_stores_to_scope,
            move_scope_file_stores_to_workspace,
        )
    ]
