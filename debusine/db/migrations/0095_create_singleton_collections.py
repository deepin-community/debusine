# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to create singleton collections in the default workspace."""

from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.artifacts.models import CollectionCategory

# We only create the collections that were defined as singletons at the time
# of this migration.  If we add new singleton categories and want to create
# them in the default workspace as well, then that requires a new migration.
singleton_collection_categories = (
    CollectionCategory.PACKAGE_BUILD_LOGS,
    CollectionCategory.TASK_HISTORY,
)


def create_singleton_collections(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Create singleton collections in the default workspace."""
    Workspace = apps.get_model("db", "Workspace")
    Collection = apps.get_model("db", "Collection")
    default_workspace = Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    for category in singleton_collection_categories:
        Collection.objects.create(
            name="_", category=category, workspace=default_workspace
        )


def remove_singleton_collections(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Remove singleton collections in the default workspace."""
    Workspace = apps.get_model("db", "Workspace")
    Collection = apps.get_model("db", "Collection")
    default_workspace = Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    for category in singleton_collection_categories:
        Collection.objects.filter(
            name="_", category=category, workspace=default_workspace
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0094_collection_db_collection_name_not_reserved"),
    ]

    operations = [
        migrations.RunPython(
            create_singleton_collections, remove_singleton_collections
        )
    ]
