# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Fix WorkRequest.task_type."""

from functools import partial

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.tasks.models import TaskTypes


def fix_task_type(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
    *,
    reverse: bool = False,
) -> None:
    """
    Fix WorkRequest.task_type.

    From 0027_workrequest_task_type until 0030_move_task_type, this field
    used lower-case database values and a lower-case default.  These are no
    longer valid.
    """
    WorkRequest = apps.get_model("db", "WorkRequest")
    for work_request in WorkRequest.objects.all():
        if reverse:
            work_request.task_type = work_request.task_type.lower()
        else:
            work_request.task_type = TaskTypes[work_request.task_type.upper()]
        work_request.save()


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0035_autopkgtest_extra_environment"),
    ]

    operations = [
        migrations.RunPython(
            fix_task_type, partial(fix_task_type, reverse=True)
        )
    ]
