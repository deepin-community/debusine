# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration for Collection.{retains_artifacts,workflow}."""

import re

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.artifacts.models import CollectionCategory
from debusine.db.models.collections import _CollectionRetainsArtifacts


def update_collection(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate to `retains_artifacts` enum field and set `workflow`."""
    Collection = apps.get_model("db", "Collection")
    WorkRequest = apps.get_model("db", "WorkRequest")
    for collection in Collection.objects.all():
        if collection.retains_artifacts_bool:
            collection.retains_artifacts = _CollectionRetainsArtifacts.ALWAYS
        elif collection.category == CollectionCategory.WORKFLOW_INTERNAL and (
            m := re.match(r"^workflow-([0-9]+)$", collection.name)
        ):
            collection.workflow = WorkRequest.objects.get(id=m.group(1))
            collection.retains_artifacts = _CollectionRetainsArtifacts.WORKFLOW
        else:
            collection.retains_artifacts = _CollectionRetainsArtifacts.NEVER
        collection.save()


def revert_collection(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate back to `retains_artifacts` boolean field."""
    Collection = apps.get_model("db", "Collection")
    for collection in Collection.objects.all():
        collection.retains_artifacts_bool = (
            collection.retains_artifacts == _CollectionRetainsArtifacts.ALWAYS
        )
        collection.save()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0050_collection_workflow_initial"),
    ]

    operations = [migrations.RunPython(update_collection, revert_collection)]
