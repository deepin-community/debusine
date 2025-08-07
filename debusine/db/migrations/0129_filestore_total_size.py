# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to initialize `FileStore.total_size`."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Sum


def set_file_store_total_size(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Initialize `FileStore.total_size`."""
    FileStore = apps.get_model("db", "FileStore")
    for file_store in FileStore.objects.annotate(
        computed_total_size=Sum("files__size")
    ):
        file_store.total_size = file_store.computed_total_size
        file_store.save()


def clear_file_store_total_size(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Initialize `FileStore.total_size`."""
    FileStore = apps.get_model("db", "FileStore")
    FileStore.objects.update(total_size=None)


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0128_filestore_add_size_columns"),
    ]

    operations = [
        migrations.RunPython(
            set_file_store_total_size, clear_file_store_total_size
        )
    ]
