# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Debusine checks."""

import getpass
import os
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

from django.conf import settings
from django.core.checks import Error
from django.core.management import call_command
from django.test import override_settings

from debusine.db.models import FileStore
from debusine.django import checks as common_checks
from debusine.project import checks
from debusine.test.django import TestCase


class ChecksTests(TestCase):
    """Tests for functions in checks.py."""

    playground_memory_file_store = False

    @override_settings()
    def test_check_directories_owners_mandatory_dirs_ok(self) -> None:
        """_check_directories_owners with mandatory dirs return empty list."""
        settings.PATH_TO_DIR_1 = self.create_temporary_directory()
        settings.PATH_TO_DIR_2 = self.create_temporary_directory()
        self.assertEqual(
            checks._check_directories_owners(
                ["PATH_TO_DIR_1", "PATH_TO_DIR_2"], missing_ok=False
            ),
            [],
        )

    @override_settings()
    def test_check_directories_owners_mandatory_dirs_not_exist(self) -> None:
        """_check_directories_owners return error: mandatory dir not exist."""
        settings.PATH_TO_DIR = path_to_dir = "/does/not/exist"
        self.assertEqual(
            checks._check_directories_owners(["PATH_TO_DIR"], missing_ok=False),
            [Error(f"{path_to_dir} must exist")],
        )

    @override_settings()
    def test_check_directories_owners_mandatory_owner_mismatch(self) -> None:
        """_check_directory_owners return error: mandatory dir owner wrong."""
        settings.PATH_TO_DIR = path_to_dir = self.create_temporary_directory()

        invalid_user = "an-invalid-user"

        patcher = mock.patch("pathlib.Path.owner", autospec=True)
        mocked_pathlib_owner = patcher.start()
        mocked_pathlib_owner.return_value = invalid_user
        self.addCleanup(patcher.stop)

        self.assertEqual(
            checks._check_directories_owners(["PATH_TO_DIR"], missing_ok=False),
            [
                Error(
                    f"{path_to_dir} owner ({invalid_user}) must match "
                    f"current user ({getpass.getuser()})"
                )
            ],
        )

    @override_settings()
    def test_check_directories_owners_optional_owner_mismatch(self) -> None:
        """_check_directory_owners return error: optional dir owner wrong."""
        settings.PATH_TO_DIR = path_to_dir = self.create_temporary_directory()

        invalid_user = "an-invalid-user"

        patcher = mock.patch("pathlib.Path.owner", autospec=True)
        mocked_pathlib_owner = patcher.start()
        mocked_pathlib_owner.return_value = invalid_user
        self.addCleanup(patcher.stop)

        self.assertEqual(
            checks._check_directories_owners(["PATH_TO_DIR"], missing_ok=True),
            [
                Error(
                    f"{path_to_dir} owner ({invalid_user}) must match "
                    f"current user ({getpass.getuser()})"
                )
            ],
        )

    @override_settings()
    def test_check_directories_owners_missing_optional_dirs_ok(self) -> None:
        """_check_directories_owners with optional dirs return empty list."""
        settings.PATH_TO_DIR_1 = "/does/not/exist"
        settings.PATH_TO_DIR_2 = "/does/not/exist"
        self.assertEqual(
            checks._check_directories_owners(
                ["PATH_TO_DIR_1", "PATH_TO_DIR_2"], missing_ok=True
            ),
            [],
        )

    def patch_directories_owners_check_error(self) -> MagicMock:
        """Patch checks.directories_owners_check to return an error."""
        patcher = mock.patch(
            "debusine.project.checks._check_directories_owners", autospec=True
        )
        mocked = patcher.start()
        mocked.return_value = [Error("Some Error")]
        self.addCleanup(patcher.stop)

        return mocked

    def test_directories_owners_check_check(self) -> None:
        """
        directories_owners_check uses check_directories_owners.

        directories_owners_check calls and returns
        _check_directories_owners()
        """
        check_directories_mocked = self.patch_directories_owners_check_error()

        self.assertEqual(
            checks.directories_owners_check(app_configs=None),
            check_directories_mocked.return_value,
        )

        check_directories_mocked.assert_called_with(
            [
                "DEBUSINE_DATA_PATH",
                "STATIC_ROOT",
                "MEDIA_ROOT",
                "DEBUSINE_CACHE_DIRECTORY",
                "DEBUSINE_TEMPLATE_DIRECTORY",
                "DEBUSINE_UPLOAD_DIRECTORY",
                "DEBUSINE_STORE_DIRECTORY",
            ],
            missing_ok=True,
        )

    def test_directories_owners_check_no_error_migrate_command(self) -> None:
        """
        directories_owners_check uses check_directories_owners.

        directories_owners_check calls and returns
        _check_directories_owners()
        """
        self.patch_directories_owners_check_error()

        # During the execution of the test sys.argv is modified.
        # Ensure that the original one is restored.
        self.addCleanup(self.restore_argv, sys.argv[:])

        for command in common_checks._migration_commands:
            with self.subTest(command=command):
                sys.argv[1] = command
                self.assertEqual(
                    checks.directories_owners_check(app_configs=None), []
                )

    @override_settings()
    def test_secret_key_not_default_in_debug_0(self) -> None:
        """secret_key_not_default_in_debug_0 return Errors or empty list."""
        failing_result = [
            Error(
                'Default SECRET_KEY cannot be used in DEBUG=False. Make sure '
                'to use "SECRET_KEY = read_secret_key(path)" from selected.py '
                'settings file',
                hint="Generate a secret key using the command: "
                "$ python3 -c 'from django.core.management.utils import "
                "get_random_secret_key; print(get_random_secret_key())'",
            )
        ]

        test_params = [
            {
                "TEST_MODE": False,
                "DEBUG": True,
                "SECRET_KEY": "some-key",
                "result": [],
            },
            {
                "TEST_MODE": False,
                "DEBUG": False,
                "SECRET_KEY": "some-key",
                "result": [],
            },
            {
                "TEST_MODE": False,
                "DEBUG": True,
                "SECRET_KEY": settings.SECRET_KEY,
                "result": [],
            },
            {
                "TEST_MODE": False,
                "DEBUG": False,
                "SECRET_KEY": settings.SECRET_KEY,
                "result": failing_result,
            },
            {
                "TEST_MODE": True,
                "DEBUG": False,
                "SECRET_KEY": settings.SECRET_KEY,
                "result": [],
            },
        ]

        for param in test_params:
            settings.TEST_MODE = param["TEST_MODE"]
            settings.DEBUG = param["DEBUG"]
            settings.SECRET_KEY = param["SECRET_KEY"]

            with self.subTest(params=param):
                self.assertEqual(
                    checks.secret_key_not_default_in_debug_0(app_configs=None),
                    param["result"],
                )

    @override_settings()
    def test_disable_automatic_scheduling_false_in_production(self) -> None:
        """Automatic scheduling is not disabled in production."""
        failing_result = [
            Error(
                "DISABLE_AUTOMATIC_SCHEDULING can only be enabled in TEST_MODE",
                hint="Remove DISABLE_AUTOMATIC_SCHEDULING=True from "
                "debusine-server settings",
            )
        ]

        for test_mode, disable_automatic_scheduling, result in (
            (True, True, []),
            (True, False, []),
            (True, None, []),
            (False, True, failing_result),
            (False, False, []),
            (False, None, []),
            (None, True, failing_result),
            (None, False, []),
            (None, None, []),
        ):
            with self.subTest(
                test_mode=test_mode,
                disable_automatic_scheduling=disable_automatic_scheduling,
            ):
                if test_mode is not None:
                    settings.TEST_MODE = test_mode

                if disable_automatic_scheduling is not None:
                    settings.DISABLE_AUTOMATIC_SCHEDULING = (
                        disable_automatic_scheduling
                    )

                self.assertEqual(
                    checks.disable_automatic_scheduling_false_in_production(
                        app_configs=None
                    ),
                    result,
                )

    def test_all_migrations_applied(self) -> None:
        """all_migrations_applied() do not return any error."""
        self.assertEqual(
            common_checks.all_migrations_applied(
                "debusine-admin", app_configs=None
            ),
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

        app_name = "db"
        migration_name = self._create_empty_migration(app_name)

        expected_error = Error(
            f"Non-applied migration: {app_name} {migration_name}",
            hint='Run "debusine-admin migrate"',
        )

        self.assertEqual(
            common_checks.all_migrations_applied(
                "debusine-admin", app_configs=None
            ),
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

        app_name = "db"
        self._create_empty_migration(app_name)

        # During the execution of the test sys.argv is modified.
        # Ensure that the original one is restored.
        self.addCleanup(self.restore_argv, sys.argv[:])

        for command in common_checks._migration_commands:
            with self.subTest(command=command):
                sys.argv[1] = command
                self.assertEqual(
                    common_checks.all_migrations_applied(
                        "debusine-admin", app_configs=None
                    ),
                    [],
                )

    def test_local_file_store_directories_exist(self) -> None:
        """local_file_store_directories_exist() does not return any error."""
        self.assertGreater(FileStore.objects.count(), 0)

        self.assertEqual(
            checks.local_file_store_directories_writable(app_configs=None), []
        )

    @staticmethod
    def create_file_store_base_directory_no_exist() -> tuple[FileStore, str]:
        """
        Create a FileStore with backend local and no-existing base_directory.

        :return: tuple[FileStore, base_directory]
        """
        name = "testing"
        base_directory = "/something/that/does/not/exist"
        file_store = FileStore.objects.create(
            name=name,
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": base_directory},
        )

        return file_store, base_directory

    def test_local_file_store_directories_no_exist_error(self) -> None:
        """local_file_store_directories_writable(): dir does not exist."""
        (
            file_store,
            base_directory,
        ) = self.create_file_store_base_directory_no_exist()

        self.assertEqual(
            checks.local_file_store_directories_writable(app_configs=None),
            [
                Error(
                    f'FileStore "{file_store.name}" has a non-existent '
                    f'or non-writable base_directory: "{base_directory}"',
                    hint="Create the directory, change the permissions "
                    "or change the FileStore configuration",
                )
            ],
        )

    def test_local_file_store_directories_no_writable_error(self) -> None:
        """local_file_store_directories_writable(): dir is not writable."""
        base_directory = self.create_temporary_directory()

        file_store = FileStore.objects.create(
            name="Testing",
            configuration={"base_directory": str(base_directory)},
            backend=FileStore.BackendChoices.LOCAL,
        )
        os.chmod(base_directory, 0)

        self.assertEqual(
            checks.local_file_store_directories_writable(app_configs=None),
            [
                Error(
                    f'FileStore "{file_store.name}" has a non-existent '
                    f'or non-writable base_directory: "{base_directory}"',
                    hint="Create the directory, change the permissions "
                    "or change the FileStore configuration",
                )
            ],
        )

    def test_local_file_store_directories_no_error_migrating(self) -> None:
        """local_file_store_directories_writable(): no error, migrating cmd."""
        self.create_file_store_base_directory_no_exist()

        self.addCleanup(self.restore_argv, sys.argv[:])

        for command in common_checks._migration_commands:
            with self.subTest(command=command):
                sys.argv[1] = command
                self.assertEqual(
                    checks.local_file_store_directories_writable(
                        app_configs=None
                    ),
                    [],
                )

    def test_local_file_store_directories_no_error(self) -> None:
        """local_file_store_directories_writable(): no error, dir exist."""
        # One FileStore with local backend exist: created by the migrations
        self.assertGreaterEqual(
            FileStore.objects.filter(
                backend=FileStore.BackendChoices.LOCAL
            ).count(),
            1,
        )

        self.assertEqual(
            checks.local_file_store_directories_writable(app_configs=None), []
        )
