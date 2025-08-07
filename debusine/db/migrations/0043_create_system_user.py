# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Create a system user."""

from django.contrib.auth.hashers import make_password
from django.db import migrations, transaction
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.db.models import SYSTEM_USER_NAME


def create_system_user(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    User = apps.get_model("db", "User")
    unusable_password = make_password(None)
    with transaction.atomic():
        User.objects.create(
            username=SYSTEM_USER_NAME,
            password=unusable_password,
            first_name="Debusine",
            last_name="System",
            is_system=True,
        )


def remove_system_user(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    User = apps.get_model("db", "User")
    with transaction.atomic():
        User.objects.filter(username=SYSTEM_USER_NAME, is_system=True).delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("db", "0042_user_is_system"),
    ]

    operations = [
        migrations.RunPython(
            create_system_user, reverse_code=remove_system_user
        )
    ]
