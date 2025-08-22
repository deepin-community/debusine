# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Add components to debian:system-tarball and debian:system-image artifacts."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def add_components(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Add components to artifacts."""
    Artifact = apps.get_model("db", "Artifact")

    for artifact in Artifact.objects.filter(
        category__in=("debian:system-tarball", "debian:system-image"),
        data__components__isnull=True,
    ):
        components = []
        if work_request := artifact.created_by_work_request:
            if repos := work_request.task_data.get(
                "bootstrap_repositories", []
            ):
                repo = repos[0]
                if "components" in repo:
                    components = repo["components"]
        artifact.data["components"] = components
        artifact.save()


def remove_components(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Remove components from artifacts."""
    Artifact = apps.get_model("db", "Artifact")

    for artifact in Artifact.objects.filter(
        category__in=("debian:system-tarball", "debian:system-image"),
        data__components__isnull=False,
    ):
        artifact.data.pop("components")
        artifact.save()


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0003_add_workspace_role_viewer"),
    ]
    replaces = [
        ("db", "0145_system_image_components"),
    ]

    operations = [
        migrations.RunPython(add_components, remove_components),
    ]
