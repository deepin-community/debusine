# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Debusine checks."""

import os
import sys
from pathlib import Path

import pytest
from django.core.checks import Error
from django.core.management import call_command
from django.test import TestCase

from debusine.django import checks as common_checks
from debusine.signing import checks


@pytest.mark.xdist_group("serial-signing-checks")
class ChecksTests(TestCase):
    """Tests for functions in checks.py."""

    def test_all_migrations_applied(self) -> None:
        """all_migrations_applied() do not return any error."""
        self.assertEqual(
            checks.all_migrations_applied("debusine-signing", app_configs=None),
            [],
        )

    def _create_empty_migration(self, app_name: str) -> str:
        """Create empty migration file, return name of the migration."""
        temp_migration_name = "debusine_test_temporary_migration"
        call_command(
            "makemigrations",
            app_name,
            name=temp_migration_name,
            empty=True,
            verbosity=0,
        )

        migration_files = list(
            Path(f"debusine/{app_name}/migrations").glob(
                f"*_{temp_migration_name}.py"
            )
        )

        # Expect only one file with this wildcard
        self.assertEqual(len(migration_files), 1)

        migration_file = migration_files[0]

        self.addCleanup(migration_file.unlink)

        return migration_file.stem

    def test_all_migrations_applied_return_error(self) -> None:
        """all_migrations_applied() return a non-applied migration."""
        # Create and apply a temporary migration
        if "AUTOPKGTEST_TMP" in os.environ:  # pragma: no cover
            self.skipTest("Cannot create migration if running in autopkgtest")

        app_name = "signing"
        migration_name = self._create_empty_migration(app_name)

        expected_error = Error(
            f"Non-applied migration: {app_name} {migration_name}",
            hint='Run "debusine-signing migrate"',
        )

        self.assertEqual(
            checks.all_migrations_applied("debusine-signing", app_configs=None),
            [expected_error],
        )

    @staticmethod
    def restore_argv(to_restore: list[str]) -> None:
        """Set to_restore to sys.argv."""
        sys.argv = to_restore

    def test_all_migrations_applied_return_no_error_migrate_command(
        self,
    ) -> None:
        """
        all_migrations_applied() do not return error.

        There is a pending migration but a command "migration related" was
        used. E.g. the sysadmin / developer want to apply the migration.

        """
        if "AUTOPKGTEST_TMP" in os.environ:  # pragma: no cover
            self.skipTest("Cannot create migration if running in autopkgtest")

        app_name = "signing"
        self._create_empty_migration(app_name)

        # During the execution of the test sys.argv is modified.
        # Ensure that the original one is restored.
        self.addCleanup(self.restore_argv, sys.argv[:])

        for command in common_checks._migration_commands:
            with self.subTest(command=command):
                sys.argv[1:] = [command]
                self.assertEqual(
                    checks.all_migrations_applied(
                        "debusine-signing", app_configs=None
                    ),
                    [],
                )
