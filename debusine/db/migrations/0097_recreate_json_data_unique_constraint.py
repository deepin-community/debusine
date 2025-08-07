# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

from django.db import migrations, models

import debusine.artifacts.models
import debusine.db.constraints


class Migration(migrations.Migration):
    dependencies = [
        ('db', '0096_workspace_name_validation'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='collectionitem',
            name='db_collectionitem_unique_debian_environments',
        ),
        # Identical to 0071_collectionitem_parent_category_final; we
        # recreate it here to use NULLS NOT DISTINCT rather than COALESCE.
        # This is acceptably fast, since there aren't likely to be many
        # debian:environments collections to consider.
        migrations.AddConstraint(
            model_name='collectionitem',
            constraint=debusine.db.constraints.JsonDataUniqueConstraint(
                condition=models.Q(
                    (
                        'parent_category',
                        debusine.artifacts.models.CollectionCategory[
                            'ENVIRONMENTS'
                        ],
                    ),
                    ('removed_at__isnull', True),
                ),
                fields=(
                    'category',
                    "data->>'codename'",
                    "data->>'architecture'",
                    "data->>'variant'",
                    "data->>'backend'",
                    'parent_collection',
                ),
                name='db_collectionitem_unique_debian_environments',
                nulls_distinct=False,
            ),
        ),
    ]
