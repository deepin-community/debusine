# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration from Worker.internal to Worker.worker_type."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.tasks.models import WorkerType


def update_worker(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate to `worker_type` enum field."""
    Worker = apps.get_model("db", "Worker")
    for worker in Worker.objects.all():
        worker.worker_type = (
            WorkerType.CELERY if worker.internal else WorkerType.EXTERNAL
        )
        worker.save()


def revert_worker(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate back to `internal` boolean field."""
    Worker = apps.get_model("db", "Worker")
    for worker in Worker.objects.all():
        worker.internal = worker.worker_type == WorkerType.CELERY
        worker.save()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0063_worker_worker_type_initial"),
    ]

    operations = [migrations.RunPython(update_worker, revert_worker)]
