# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Migrate debusine:signing-key artifacts to assets."""

import logging

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.assets import SigningKeyData

log = logging.getLogger(__name__)


def migrate_to_signing_key_assets(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate debusine:signing-key artifacts to assets."""
    Artifact = apps.get_model("db", "Artifact")
    Asset = apps.get_model("db", "Asset")

    for artifact in Artifact.objects.filter(
        category="debusine:signing-key",
    ).order_by("id"):
        if Asset.objects.filter(
            category=artifact.category,
            data__fingerprint=artifact.data["fingerprint"],
        ).exists():
            log.warning(
                "Asset for %s already exists, skipping conversion of "
                "artifact %i",
                artifact.data["fingerprint"],
                artifact.id,
            )
            continue

        Asset.objects.create(
            category=artifact.category,
            workspace=artifact.workspace,
            data=SigningKeyData(
                purpose=artifact.data["purpose"],
                fingerprint=artifact.data["fingerprint"],
                public_key=artifact.data["public_key"],
                description=None,
            ).dict(),
            created_at=artifact.created_at,
            created_by=artifact.created_by,
            created_by_work_request=artifact.created_by_work_request,
        )
        artifact.relations.all().delete()
        artifact.targeted_by.all().delete()
        artifact.delete()


def migrate_to_signing_key_artifacts(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate debusine:signing-key assets back to artifacts."""
    Artifact = apps.get_model("db", "Artifact")
    Asset = apps.get_model("db", "Asset")

    for asset in Asset.objects.filter(
        category="debusine:signing-key",
    ).order_by("id"):
        data_model = SigningKeyData.parse_obj(asset.data)

        Artifact.objects.create(
            category=asset.category,
            workspace=asset.workspace,
            data={
                "purpose": data_model.purpose,
                "fingerprint": data_model.fingerprint,
                "public_key": data_model.public_key,
            },
            created_at=asset.created_at,
            created_by=asset.created_by,
            created_by_work_request=asset.created_by_work_request,
        )
        asset.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0117_signing_work_request_fingerprints"),
    ]

    operations = [
        migrations.RunPython(
            migrate_to_signing_key_assets, migrate_to_signing_key_artifacts
        )
    ]
