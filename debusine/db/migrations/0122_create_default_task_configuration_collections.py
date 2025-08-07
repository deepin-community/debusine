# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to create a debusine:task-configuration collection in the default workspace."""

from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.artifacts.models import CollectionCategory


def create_default_task_configuration_collection(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Create the default task_configuration collection."""
    Workspace = apps.get_model("db", "Workspace")
    Collection = apps.get_model("db", "Collection")
    default_workspace = Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    Collection.objects.create(
        name="default",
        category=CollectionCategory.TASK_CONFIGURATION,
        workspace=default_workspace,
    )


def remove_default_task_configuration_collection(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Remove the default task_configuration collection."""
    Workspace = apps.get_model("db", "Workspace")
    Collection = apps.get_model("db", "Collection")
    default_workspace = Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    Collection.objects.filter(
        name="default",
        category=CollectionCategory.TASK_CONFIGURATION,
        workspace=default_workspace,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0121_remove_workspace_file_stores"),
    ]

    operations = [
        migrations.RunPython(
            create_default_task_configuration_collection,
            remove_default_task_configuration_collection,
        )
    ]
