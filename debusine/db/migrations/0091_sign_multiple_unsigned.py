# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

from django.db import migrations, models, transaction
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import StateApps
from django.db.models.fields.json import KT

from debusine.tasks.models import TaskTypes


def update_sign_multiple_unsigned(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Update Sign tasks to take multiple unsigned lookups."""
    WorkRequest = apps.get_model("db", "WorkRequest")
    while True:
        with transaction.atomic():
            work_requests = (
                WorkRequest.objects.select_for_update()
                .filter(task_type=TaskTypes.SIGNING, task_name="sign")
                .annotate(unsigned_text=KT("task_data__unsigned"))
                .exclude(unsigned_text__startswith="[")
                .exclude(unsigned_text__startswith="{")[:1000]
            )
            if not work_requests:
                break
            for work_request in work_requests:
                work_request.task_data["unsigned"] = [
                    work_request.task_data["unsigned"]
                ]
                if (
                    work_request.dynamic_task_data is not None
                    and "unsigned_id" in work_request.dynamic_task_data
                ):
                    work_request.dynamic_task_data["unsigned_ids"] = [
                        work_request.dynamic_task_data.pop("unsigned_id")
                    ]
                work_request.save()


def revert_sign_multiple_unsigned(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Revert Sign tasks to take a single unsigned lookup."""
    WorkRequest = apps.get_model("db", "WorkRequest")
    while True:
        with transaction.atomic():
            work_requests = (
                WorkRequest.objects.select_for_update()
                .filter(task_type=TaskTypes.SIGNING, task_name="sign")
                .annotate(unsigned_text=KT("task_data__unsigned"))
                .filter(
                    models.Q(unsigned_text__startswith="[")
                    | models.Q(unsigned_text__startswith="{")
                )[:1000]
            )
            if not work_requests:
                break
            for work_request in work_requests:
                unsigned = work_request.task_data["unsigned"]
                if (
                    not isinstance(unsigned, list)
                    or len(unsigned) != 1
                    or not isinstance(unsigned[0], (int, str))
                ):
                    raise IrreversibleError(
                        f"Work request {work_request.id}: {unsigned!r} cannot "
                        f"be converted to a single lookup"
                    )
                work_request.task_data["unsigned"] = unsigned[0]
                if (
                    work_request.dynamic_task_data is not None
                    and "unsigned_ids" in work_request.dynamic_task_data
                ):
                    unsigned_ids = work_request.dynamic_task_data.pop(
                        "unsigned_ids"
                    )
                    if len(unsigned_ids) != 1:
                        raise IrreversibleError(
                            f"Work request {work_request.id}: {unsigned_ids!r} "
                            f"does not have exactly one element"
                        )
                    work_request.dynamic_task_data["unsigned_id"] = (
                        unsigned_ids[0]
                    )
                work_request.save()


class Migration(migrations.Migration):
    dependencies = [
        ('db', '0090_workspacerole_and_more'),
    ]

    operations = [
        migrations.RunPython(
            update_sign_multiple_unsigned, revert_sign_multiple_unsigned
        )
    ]
