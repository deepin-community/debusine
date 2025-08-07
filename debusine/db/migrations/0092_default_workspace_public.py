# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration to make the default workspace public."""

from functools import partial

from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def set_default_workspace_public(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor, public: bool
) -> None:
    """Set the public field on the default workspace."""
    default_workspace = apps.get_model("db", "Workspace").objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )
    default_workspace.public = public
    default_workspace.save()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0091_sign_multiple_unsigned"),
    ]

    operations = [
        migrations.RunPython(
            partial(set_default_workspace_public, public=True),
            partial(set_default_workspace_public, public=False),
        )
    ]
