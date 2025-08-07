# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Migrate WorkRequest debusine:signing-key artifact lookups to fingerprints."""

import logging
import re
from dataclasses import dataclass

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.tasks.models import TaskTypes

log = logging.getLogger(__name__)


@dataclass
class TaskMigration:
    task_type: TaskTypes
    task_name: str
    data_key: str
    dynamic_data_key: str | None
    # For workflows, we have to find the dynamic data in a child
    child_task_type: TaskTypes | None
    child_task_name: str | None
    child_data_key: str | None


# Ordered bottom-up, topologically, so that we can determine workflow keys from
# immediate children
task_migrations = [
    # Leaf tasks
    TaskMigration(
        task_type=TaskTypes.SIGNING,
        task_name="sign",
        data_key="key",
        dynamic_data_key="key_id",
        child_task_type=None,
        child_task_name=None,
        child_data_key=None,
    ),
    TaskMigration(
        task_type=TaskTypes.SIGNING,
        task_name="debsign",
        data_key="key",
        dynamic_data_key="key_id",
        child_task_type=None,
        child_task_name=None,
        child_data_key=None,
    ),
    # Level 0 workflows
    TaskMigration(
        task_type=TaskTypes.WORKFLOW,
        task_name="make_signed_source",
        data_key="key",
        dynamic_data_key=None,
        child_task_type=TaskTypes.SIGNING,
        child_task_name="sign",
        child_data_key="key",
    ),
    TaskMigration(
        task_type=TaskTypes.WORKFLOW,
        task_name="package_upload",
        data_key="key",
        dynamic_data_key=None,
        child_task_type=TaskTypes.SIGNING,
        child_task_name="debsign",
        child_data_key="key",
    ),
    # Level 1 workflows
    TaskMigration(
        task_type=TaskTypes.WORKFLOW,
        task_name="debian_pipeline",
        data_key="make_signed_source_key",
        dynamic_data_key=None,
        child_task_type=TaskTypes.WORKFLOW,
        child_task_name="make_signed_source",
        child_data_key="key",
    ),
    TaskMigration(
        task_type=TaskTypes.WORKFLOW,
        task_name="debian_pipeline",
        data_key="upload_key",
        dynamic_data_key=None,
        child_task_type=TaskTypes.WORKFLOW,
        child_task_name="package_upload",
        child_data_key="key",
    ),
]

artifact_id_lookup_re = re.compile(r"^(\d+)@artifacts$")


def migrate_to_fingerprints(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate references to debusine:signing-key artifacts to fingerprints."""
    Artifact = apps.get_model("db", "Artifact")
    WorkRequest = apps.get_model("db", "WorkRequest")

    for step in task_migrations:
        for work_request in WorkRequest.objects.filter(
            task_type=step.task_type,
            task_name=step.task_name,
            task_data__has_key=step.data_key,
        ).order_by("id"):
            lookup: int | str | None = work_request.task_data[step.data_key]
            if lookup is None:
                continue
            artifact_id: int | None = None
            if isinstance(lookup, int):
                artifact_id = lookup
            elif lookup.isdigit():
                artifact_id = int(lookup)
            elif m := artifact_id_lookup_re.match(lookup):
                artifact_id = int(m.group(1))
            elif (
                step.dynamic_data_key
                and step.dynamic_data_key in work_request.dynamic_task_data
            ):
                artifact_id = work_request.dynamic_task_data[
                    step.dynamic_data_key
                ]

            if artifact_id is not None:
                try:
                    artifact = Artifact.objects.get(
                        id=artifact_id, category="debusine:signing-key"
                    )
                except Artifact.DoesNotExist:
                    log.warning(
                        (
                            "Unable to migrate '%s' to fingerprints in "
                            "work request %i, debusine:signing-key "
                            "artifact %i not found."
                        ),
                        step.data_key,
                        work_request.id,
                        artifact_id,
                    )
                    continue
                work_request.task_data[step.data_key] = artifact.data[
                    "fingerprint"
                ]
                if step.dynamic_data_key and work_request.dynamic_task_data:
                    work_request.dynamic_task_data.pop(step.dynamic_data_key)
                work_request.save()
                continue

            elif step.child_task_type:
                child = (
                    work_request.children.filter(
                        task_type=step.child_task_type,
                        task_name=step.child_task_name,
                        task_data__has_key=step.child_data_key,
                    )
                    .order_by("id")
                    .first()
                )
                if child is None:
                    log.warning(
                        (
                            "Unable to migrate '%s' to fingerprints in "
                            "work request %i, child work request with "
                            "task_name '%s' and fingerprints not found."
                        ),
                        step.data_key,
                        work_request.id,
                        step.child_task_name,
                    )
                    continue
                work_request.task_data[step.data_key] = child.task_data[
                    step.child_data_key
                ]
                # No workflow tasks have dynamic data, but included for
                # logical completeness:
                if step.dynamic_data_key:  # pragma: no cover
                    work_request.dynamic_task_data.pop(step.dynamic_data_key)
                work_request.save()
                continue

            log.warning(
                "Unable to migrate '%s' to fingerprints in work request %i.",
                step.data_key,
                work_request.id,
            )


def migrate_to_signing_key_artifacts(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate fingerprints back to debusine:signing-key asset lookups."""
    Artifact = apps.get_model("db", "Artifact")
    WorkRequest = apps.get_model("db", "WorkRequest")

    for step in task_migrations:
        for work_request in WorkRequest.objects.filter(
            task_type=step.task_type,
            task_name=step.task_name,
            task_data__has_key=step.data_key,
        ).order_by("id"):
            fingerprint = work_request.task_data[step.data_key]
            artifact = (
                Artifact.objects.filter(
                    category="debusine:signing-key",
                    data__fingerprint=fingerprint,
                )
                .order_by("id")
                .first()
            )
            if artifact is None:
                log.warning(
                    (
                        "Unable to migrate '%s' to artifact lookups in work "
                        "request %i, artifact not found"
                    ),
                    step.data_key,
                    work_request.id,
                )
                continue
            work_request.task_data[step.data_key] = f"{artifact.id}@artifacts"
            if step.dynamic_data_key:
                work_request.dynamic_task_data[step.dynamic_data_key] = (
                    artifact.id
                )
            work_request.save()


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0116_assets"),
    ]

    operations = [
        migrations.RunPython(
            migrate_to_fingerprints, migrate_to_signing_key_artifacts
        )
    ]
