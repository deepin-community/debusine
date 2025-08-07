# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command file_store."""

import io

import yaml
from django.core.management import CommandError
from django.test import override_settings

from debusine.assets.models import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
)
from debusine.db.models import File, FileStore, Scope
from debusine.django.management.tests import call_command
from debusine.server.management.commands.file_store import Command
from debusine.test.django import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class FileStoreCommandTests(TestCase):
    """Tests for the `file_store` command."""

    def test_create_from_file(self) -> None:
        """`file_store create` creates a new file store (config in file)."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        configuration_file = self.create_temporary_file(
            contents=yaml.safe_dump(configuration).encode()
        )
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            "--configuration",
            str(configuration_file),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertTrue(file_store.instance_wide)
        self.assertIsNone(file_store.soft_max_size)
        self.assertIsNone(file_store.max_size)
        self.assertIsNone(file_store.provider_account)

    def test_create_from_stdin(self) -> None:
        """`file_store create` creates a new file store (config in stdin)."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertTrue(file_store.instance_wide)
        self.assertIsNone(file_store.soft_max_size)
        self.assertIsNone(file_store.max_size)
        self.assertIsNone(file_store.provider_account)

    def test_create_external(self) -> None:
        """`file_store create` creates a new external file store."""
        name = "test"
        backend = FileStore.BackendChoices.EXTERNAL_DEBIAN_SUITE
        configuration = {
            "archive_root_url": "https://deb.debian.org/debian",
            "suite": "bookworm",
            "components": ["main"],
        }
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertTrue(file_store.instance_wide)
        self.assertIsNone(file_store.soft_max_size)
        self.assertIsNone(file_store.max_size)
        self.assertIsNone(file_store.provider_account)

    def test_create_non_instance_wide(self) -> None:
        """`file_store create` creates a non-instance-wide file store."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--no-instance-wide",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertFalse(file_store.instance_wide)
        self.assertIsNone(file_store.soft_max_size)
        self.assertIsNone(file_store.max_size)
        self.assertIsNone(file_store.provider_account)

    def test_create_max_sizes(self) -> None:
        """`file_store create` creates a file store with maximum sizes."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--soft-max-size",
            "1024",
            "--max-size",
            "2048",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertTrue(file_store.instance_wide)
        self.assertEqual(file_store.soft_max_size, 1024)
        self.assertEqual(file_store.max_size, 2048)
        self.assertIsNone(file_store.provider_account)

    def test_create_provider_account(self) -> None:
        """`file_store create` creates a file store with a cloud provider."""
        asset = self.playground.create_cloud_provider_account_asset()
        name = "test"
        backend = FileStore.BackendChoices.S3
        configuration = {"bucket_name": "test-bucket"}
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--provider-account",
            asset.data["name"],
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertTrue(file_store.instance_wide)
        self.assertIsNone(file_store.soft_max_size)
        self.assertIsNone(file_store.max_size)
        self.assertEqual(file_store.provider_account, asset)

    def test_create_nonexistent_provider_account(self) -> None:
        """`file_store create` returns error: provider account not found."""
        self.playground.create_cloud_provider_account_asset()
        with self.assertRaisesRegex(
            CommandError,
            r"^Cloud provider account asset 'nonexistent' not found",
        ) as exc:
            stdout, stderr, exit_code = call_command(
                "file_store",
                "create",
                "--provider-account",
                "nonexistent",
                "test",
                FileStore.BackendChoices.S3,
                stdin=io.StringIO(
                    yaml.safe_dump({"bucket_name": "test-bucket"})
                ),
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(FileStore.objects.filter(name="test").exists())

    def test_create_invalid_data_yaml(self) -> None:
        """`file_store create` returns error: cannot parse YAML data."""
        with self.assertRaisesRegex(
            CommandError, r"^Error parsing YAML:"
        ) as exc:
            call_command(
                "file_store",
                "create",
                "test",
                FileStore.BackendChoices.LOCAL,
                stdin=io.StringIO(":"),
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(FileStore.objects.filter(name="test").exists())

    def test_create_invalid_data(self) -> None:
        """`file_store create` returns error: data is invalid."""
        with self.assertRaisesRegex(
            CommandError, r"^Error creating file store:"
        ) as exc:
            call_command(
                "file_store",
                "create",
                "test",
                FileStore.BackendChoices.EXTERNAL_DEBIAN_SUITE,
                stdin=io.StringIO(yaml.safe_dump({})),
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(FileStore.objects.filter(name="test").exists())

    def test_create_idempotent(self) -> None:
        """`file_store create` with matching parameters succeeds."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration=configuration,
        )
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
        self.assertTrue(file_store.instance_wide)

    def test_update_backend(self) -> None:
        """`file_store create` refuses to change the backend."""
        FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={},
        )
        with self.assertRaisesRegex(
            CommandError, r"^Cannot change backend of existing file store$"
        ) as exc:
            call_command(
                "file_store",
                "create",
                "test",
                FileStore.BackendChoices.EXTERNAL_DEBIAN_SUITE,
                stdin=io.StringIO(
                    yaml.safe_dump(
                        {
                            "archive_root_url": "https://deb.debian.org/debian",
                            "suite": "bookworm",
                            "components": ["main"],
                        }
                    )
                ),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_update_configuration(self) -> None:
        """`file_store create` refuses to change the backend."""
        FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": "/nonexistent"},
        )
        with self.assertRaisesRegex(
            CommandError,
            r"^Cannot change configuration of existing file store$",
        ) as exc:
            call_command(
                "file_store",
                "create",
                "test",
                FileStore.BackendChoices.LOCAL,
                stdin=io.StringIO(
                    yaml.safe_dump({"base_directory": "/nonexistent2"})
                ),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_update_instance_wide(self) -> None:
        """`file_store create` can change `instance_wide`."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        file_store = FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration=configuration,
        )
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--no-instance-wide",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertFalse(file_store.instance_wide)

        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertFalse(file_store.instance_wide)

        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--instance-wide",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertTrue(file_store.instance_wide)

    def test_update_max_sizes(self) -> None:
        """`file_store create` can change `soft_max_size` and `max_size`."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        file_store = FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration=configuration,
        )
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--soft-max-size",
            "1024",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertEqual(file_store.soft_max_size, 1024)
        self.assertIsNone(file_store.max_size)

        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--max-size",
            "2048",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertEqual(file_store.soft_max_size, 1024)
        self.assertEqual(file_store.max_size, 2048)

        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertEqual(file_store.soft_max_size, 1024)
        self.assertEqual(file_store.max_size, 2048)

    def test_update_provider_account(self) -> None:
        """`file_store create` can change `provider_account`."""
        provider_account_data_models = [
            AWSProviderAccountData(
                name=name,
                configuration=AWSProviderAccountConfiguration(
                    region_name="test-region"
                ),
                credentials=AWSProviderAccountCredentials(
                    access_key_id=f"{name}-access-key",
                    secret_access_key=f"{name}-secret-key",
                ),
            )
            for name in ("account1", "account2")
        ]
        provider_accounts = [
            self.playground.create_cloud_provider_account_asset(data=data)
            for data in provider_account_data_models
        ]
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        file_store = FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration=configuration,
            provider_account=provider_accounts[0],
        )
        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            "--provider-account",
            provider_account_data_models[1].name,
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertEqual(file_store.provider_account, provider_accounts[1])

        stdout, stderr, exit_code = call_command(
            "file_store",
            "create",
            name,
            backend,
            stdin=io.StringIO(yaml.safe_dump(configuration)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store.refresh_from_db()
        self.assertEqual(file_store.provider_account, provider_accounts[1])

    def test_update_instance_wide_violates_constraint(self) -> None:
        """`file_store create` reports failure to change `instance_wide`."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        file_store = FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration=configuration,
        )
        for scope_name in ("scope1", "scope2"):
            file_store.scopes.add(
                self.playground.get_or_create_scope(scope_name)
            )

        with self.assertRaisesRegex(
            CommandError,
            r'^Error creating file store: '
            r'duplicate key value violates unique constraint '
            r'"db_filestoreinscope_unique_file_store_not_instance_wide"',
        ):
            call_command(
                "file_store",
                "create",
                "--no-instance-wide",
                name,
                backend,
                stdin=io.StringIO(yaml.safe_dump(configuration)),
            )

    def create_memory_file_store(self, *, name: str) -> FileStore:
        """Create a MEMORY file store."""
        return FileStore.objects.create(
            name=name,
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": name},
        )

    def test_delete_missing_file_store(self) -> None:
        """A nonexistent file store cannot be deleted."""
        with self.assertRaisesRegex(
            CommandError, r"^File store 'nonexistent' does not exist"
        ) as exc:
            call_command("file_store", "delete", "nonexistent")

        self.assertEqual(exc.exception.returncode, 3)

    def test_delete_with_files(self) -> None:
        """A file store with files cannot be deleted without --force."""
        name = "test"
        file_store = self.create_memory_file_store(name=name)
        file_backend = file_store.get_backend_object()
        for contents in (b"abc", b"def", b"ghi"):
            self.playground.create_file_in_backend(file_backend, contents)

        with self.assertRaisesRegex(
            CommandError,
            fr"File store {name!r} still contains 3 files; drain it first",
        ) as exc:
            call_command("file_store", "delete", name)

        self.assertEqual(exc.exception.returncode, 3)

    def test_delete_with_files_and_force(self) -> None:
        """A file store with files can be deleted with --force."""
        name = "test"
        file_store = self.create_memory_file_store(name=name)
        file_backend = file_store.get_backend_object()
        files = [
            self.playground.create_file_in_backend(file_backend, contents)
            for contents in (b"abc", b"def", b"ghi")
        ]

        call_command("file_store", "delete", "--force", name)
        self.assertQuerySetEqual(FileStore.objects.filter(name=name), [])
        # The previously-related files still exist.
        self.assertQuerySetEqual(
            File.objects.filter(pk__in=[file.pk for file in files]),
            files,
            ordered=False,
        )

    def test_delete_with_scopes(self) -> None:
        """A file store with scopes cannot be deleted without --force."""
        name = "test"
        file_store = self.create_memory_file_store(name=name)
        for scope_name in ("scope1", "scope2"):
            file_store.scopes.add(
                self.playground.get_or_create_scope(scope_name)
            )

        with self.assertRaisesRegex(
            CommandError,
            fr"File store {name!r} is still used by 2 scopes; use "
            fr"'debusine-admin scope remove_file_store' first",
        ) as exc:
            call_command("file_store", "delete", name)

        self.assertEqual(exc.exception.returncode, 3)

    def test_delete_with_scopes_and_force(self) -> None:
        """A file store with scopes can be deleted with --force."""
        name = "test"
        file_store = self.create_memory_file_store(name=name)
        for scope_name in ("scope1", "scope2"):
            file_store.scopes.add(
                self.playground.get_or_create_scope(scope_name)
            )

        call_command("file_store", "delete", "--force", name)
        self.assertQuerySetEqual(FileStore.objects.filter(name=name), [])
        # The previously-related scopes still exist.
        self.assertEqual(
            Scope.objects.filter(name__in={"scope1", "scope2"}).count(), 2
        )

    def test_delete_without_scopes(self) -> None:
        """A file store without scopes can be deleted without --force."""
        name = "test"
        self.create_memory_file_store(name=name)

        call_command("file_store", "delete", name)
        self.assertQuerySetEqual(FileStore.objects.filter(name=name), [])

    def test_unexpected_action(self) -> None:
        """Test a subcommand with no implementation."""
        command = Command()

        with self.assertRaisesRegex(
            CommandError, r"Action 'does_not_exist' not found"
        ) as exc:
            command.handle(action="does_not_exist")

        self.assertEqual(getattr(exc.exception, "returncode"), 3)
