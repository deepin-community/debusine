# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Rename UpdateSuiteLintianCollection task data fields to use lookups."""

from django.db import migrations

from debusine.db.migrations._utils import (
    make_work_request_task_data_field_renamer,
)


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0048_alter_collection_retains_artifacts"),
    ]

    operations = [
        make_work_request_task_data_field_renamer(
            "updatesuitelintiancollection",
            [
                ("base_collection_id", "base_collection"),
                ("derived_collection_id", "derived_collection"),
            ],
        )
    ]
