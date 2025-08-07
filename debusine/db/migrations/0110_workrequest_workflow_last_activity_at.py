# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

from typing import TYPE_CHECKING

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.db.models.work_requests import compute_workflow_last_activity
from debusine.tasks.models import TaskTypes

if TYPE_CHECKING:
    from debusine.db.models import WorkRequest


def update_workflows_last_activity_at(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """
    Update workflow's ``workflow_last_activity_at``.

    The most recent activity of a workflow is the latest
    ``started_at``/``completed_at`` of its descendants.
    """
    WorkRequest = apps.get_model("db", "WorkRequest")

    for work_request in WorkRequest.objects.filter(
        task_type=TaskTypes.WORKFLOW
    ):
        work_request.workflow_last_activity_at = compute_workflow_last_activity(
            work_request
        )
        work_request.save()


class Migration(migrations.Migration):
    dependencies = [
        ('db', '0109_scope_label_required_and_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='workrequest',
            name='workflow_last_activity_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            update_workflows_last_activity_at, migrations.RunPython.noop
        ),
    ]
