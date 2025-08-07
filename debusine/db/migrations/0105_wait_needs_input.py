# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to add needs_input to WAIT tasks."""

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.tasks.models import TaskTypes


def add_needs_input(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Add needs_input to WAIT/delay and WAIT/externaldebsign tasks."""
    WorkRequest = apps.get_model("db", "WorkRequest")
    for work_request in WorkRequest.objects.select_for_update().filter(
        task_type=TaskTypes.WAIT,
        task_name__in={"delay", "externaldebsign"},
        workflow_data_json__needs_input__isnull=True,
    ):
        match work_request.task_name:
            case "delay":
                work_request.workflow_data_json["needs_input"] = False
            case "externaldebsign":
                work_request.workflow_data_json["needs_input"] = True
            case _ as unreachable:
                raise AssertionError(f"Unexpected task_name: {unreachable}")
        work_request.save()


def remove_needs_input(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Remove needs_input from WAIT/delay and WAIT/externaldebsign tasks."""
    WorkRequest = apps.get_model("db", "WorkRequest")
    for work_request in (
        WorkRequest.objects.select_for_update()
        .filter(task_type=TaskTypes.WAIT)
        .filter(
            models.Q(task_name="delay", workflow_data_json__needs_input=False)
            | models.Q(
                task_name="externaldebsign",
                workflow_data_json__needs_input=True,
            )
        )
    ):
        del work_request.workflow_data_json["needs_input"]
        work_request.save()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0104_workspacerole_contributor"),
    ]

    operations = [migrations.RunPython(add_needs_input, remove_needs_input)]
