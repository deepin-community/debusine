# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration for CollectionItem.parent_category."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F, OuterRef, Subquery


def update_collection_item(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Populate `CollectionItem.parent_category`."""
    Collection = apps.get_model("db", "Collection")
    CollectionItem = apps.get_model("db", "CollectionItem")
    CollectionItem.objects.filter(parent_category__isnull=True).annotate(
        new_parent_category=Subquery(
            Collection.objects.filter(
                id=OuterRef("parent_collection_id")
            ).values("category")
        )
    ).update(parent_category=F("new_parent_category"))


def revert_collection_item(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Unpopulate `CollectionItem.parent_category`."""
    CollectionItem = apps.get_model("db", "CollectionItem")
    CollectionItem.objects.filter(parent_category__isnull=False).update(
        parent_category=None
    )


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0069_collectionitem_parent_category_initial'),
    ]

    operations = [
        migrations.RunPython(update_collection_item, revert_collection_item)
    ]
