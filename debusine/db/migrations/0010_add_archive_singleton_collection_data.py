# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to create debian:archive in the default workspace."""

from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.artifacts.models import CollectionCategory
from debusine.db.models import SYSTEM_USER_NAME
from debusine.db.models.collections import _CollectionItemTypes


def create_archive(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Create the debian:archive collection in the default workspace."""
    Workspace = apps.get_model("db", "Workspace")
    User = apps.get_model("db", "User")
    Collection = apps.get_model("db", "Collection")
    CollectionItem = apps.get_model("db", "CollectionItem")
    default_workspace = Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    system_user = User.objects.get(username=SYSTEM_USER_NAME)
    archive = Collection.objects.create(
        name="_",
        category=CollectionCategory.ARCHIVE,
        workspace=default_workspace,
    )
    for suite in Collection.objects.filter(
        category=CollectionCategory.SUITE, workspace=default_workspace
    ):
        CollectionItem.objects.create(
            parent_collection=archive,
            parent_category=archive.category,
            name=suite.name,
            collection=suite,
            child_type=_CollectionItemTypes.COLLECTION,
            category=suite.category,
            data={},
            created_by_user=system_user,
        )


def remove_archive(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Remove the debian:archive collection in the default workspace."""
    Workspace = apps.get_model("db", "Workspace")
    Collection = apps.get_model("db", "Collection")
    CollectionItem = apps.get_model("db", "CollectionItem")
    default_workspace = Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    archive_qs = Collection.objects.filter(
        name="_",
        category=CollectionCategory.ARCHIVE,
        workspace=default_workspace,
    )
    CollectionItem.objects.filter(parent_collection__in=archive_qs).delete()
    archive_qs.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0009_add_archive_singleton_collection"),
    ]
    replaces = [
        ("db", "0151_add_archive_singleton_collection_data"),
    ]

    operations = [migrations.RunPython(create_archive, remove_archive)]
