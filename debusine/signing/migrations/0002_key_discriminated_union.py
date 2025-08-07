# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to fix layout of discriminated unions in Key.private_key."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Q


def update_key(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate `Key.private_key` to the new layout."""
    Key = apps.get_model("signing", "Key")
    for key in Key.objects.filter(private_key__has_key="data"):
        assert set(key.private_key) == {"storage", "data"}
        data = key.private_key["data"]
        assert "storage" not in data
        key.private_key = {"storage": key.private_key["storage"], **data}
        key.save()


def revert_key(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate `Key.private_key` back to the old layout."""
    Key = apps.get_model("signing", "Key")
    for key in Key.objects.filter(~Q(private_key__has_key="data")):
        data = dict(key.private_key)
        del data["storage"]
        key.private_key = {"storage": key.private_key["storage"], "data": data}
        key.save()


class Migration(migrations.Migration):

    dependencies = [
        ('signing', '0001_initial'),
    ]

    operations = [migrations.RunPython(update_key, revert_key)]
