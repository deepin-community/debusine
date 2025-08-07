# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data migration for FileInArtifact.complete."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def update_file_in_artifact(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Populate `FileInArtifact.complete`."""
    FileInArtifact = apps.get_model("db", "FileInArtifact")
    FileStore = apps.get_model("db", "FileStore")
    Workspace = apps.get_model("db", "Workspace")

    # If a file store is only used by one workspace, then we can reasonably
    # assume that all files in that store are visible to artifacts in that
    # workspace.
    for file_store in FileStore.objects.all():
        workspaces = (
            file_store.default_workspaces.all()
            | file_store.other_workspaces.all()
        )
        if workspaces.count() == 1:
            workspace = workspaces.earliest("id")
            FileInArtifact.objects.filter(
                artifact__workspace=workspace,
                file__filestore=file_store,
                complete__isnull=True,
            ).update(complete=True)

    # Everything that's left over must be considered incomplete.
    FileInArtifact.objects.filter(complete__isnull=True).update(complete=False)


def revert_file_in_artifact(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Unpopulate `FileInArtifact.complete`."""
    FileInArtifact = apps.get_model("db", "FileInArtifact")
    FileInArtifact.objects.filter(complete__isnull=False).update(complete=None)


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0074_fileinartifact_complete_initial'),
    ]

    operations = [
        migrations.RunPython(update_file_in_artifact, revert_file_in_artifact)
    ]
