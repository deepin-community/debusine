# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from debusine.db.migrations._utils import make_table_and_relations_renamer


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0135_workerpools'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                make_table_and_relations_renamer(
                    "db_group_users", "db_groupmembership"
                )
            ],
            state_operations=[
                migrations.CreateModel(
                    name='GroupMembership',
                    fields=[
                        (
                            'id',
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name='ID',
                            ),
                        ),
                        (
                            'group',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='membership',
                                to='db.group',
                            ),
                        ),
                        (
                            'user',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='group_memberships',
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                ),
                migrations.AlterField(
                    model_name='group',
                    name='users',
                    field=models.ManyToManyField(
                        blank=True,
                        related_name='debusine_groups',
                        through='db.GroupMembership',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        )
    ]
