# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common code for Debusine checks."""

import sys
from typing import Any

from django.core.checks import Error
from django.db import connections
from django.db.migrations.loader import MigrationLoader

_migration_commands = (
    "makemigrations",
    "migrate",
    "showmigrations",
    "sqlmigrate",
    "squashmigrations",
)


def is_migration_command() -> bool:
    """Return True if a migration-related command is being run."""
    return sys.argv[1] in _migration_commands


def all_migrations_applied(admin_command: str, **kwargs: Any) -> list[Error]:
    """
    Check that all migrations have been applied.

    Avoid checking if any migration-related command is being run.

    Register this in a Django project using `django.core.checks.register`,
    passing the name of your management command as the first argument.
    """
    if is_migration_command():
        return []

    loader = MigrationLoader(connection=connections["default"], load=True)

    applied_migrations = loader.applied_migrations.keys()
    disk_migrations = loader.graph.nodes.keys()

    to_apply_migrations = sorted(disk_migrations - applied_migrations)

    errors = []

    for migration in to_apply_migrations:
        errors.append(
            Error(
                f"Non-applied migration: {migration[0]} {migration[1]}",
                hint=f'Run "{admin_command} migrate"',
            )
        )

    return errors
