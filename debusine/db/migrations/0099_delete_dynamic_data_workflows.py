# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to delete non-supported dynamic_data from work requests."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import StateApps

from debusine.tasks.models import TaskTypes


def delete_dynamic_data_from_workflows(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Delete dynamic_task_data from WorkRequest of type workflow."""
    WorkRequest = apps.get_model("db", "WorkRequest")

    WorkRequest.objects.filter(task_type=TaskTypes.WORKFLOW).filter(
        dynamic_task_data__isnull=False
    ).update(dynamic_task_data=None)


def migration_not_reversible(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Raise error if exists any work request of type WORKFLOW."""
    WorkRequest = apps.get_model("db", "WorkRequest")

    if WorkRequest.objects.filter(task_type=TaskTypes.WORKFLOW).exists():
        raise IrreversibleError("can't revert dynamic_task_data removed")


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0098_artifact_original_artifact"),
    ]

    operations = [
        migrations.RunPython(
            delete_dynamic_data_from_workflows, migration_not_reversible
        )
    ]
