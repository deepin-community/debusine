# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Rename Autopkgtest task data fields to use lookups."""

from django.db import migrations

from debusine.db.migrations._utils import (
    make_work_request_task_data_field_renamer,
)


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0044_collection_add_retains_artifacts"),
    ]

    operations = [
        make_work_request_task_data_field_renamer(
            "autopkgtest",
            [
                ("input__source_artifact_id", "input__source_artifact"),
                ("input__binary_artifacts_ids", "input__binary_artifacts"),
                ("input__context_artifacts_ids", "input__context_artifacts"),
            ],
        )
    ]
