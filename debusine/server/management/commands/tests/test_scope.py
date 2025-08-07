# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command scope."""
from typing import ClassVar
from unittest import mock

from django.core.management import CommandError

from debusine.db.models import (
    DEFAULT_FILE_STORE_NAME,
    FileStore,
    Group,
    Scope,
    User,
)
from debusine.db.models.scopes import ScopeRole
from debusine.django.management.tests import call_command
from debusine.server.management.commands.scope import Command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class CreateScopeCommandTests(TestCase):
    """Tests for `scope create` management command."""

    def assertHasScope(
        self,
        name: str = "scope",
        group_name: str | None = None,
        users: list[User] | None = None,
        label: str | None = None,
        icon: str | None = None,
        file_store_name: str = DEFAULT_FILE_STORE_NAME,
    ) -> None:
        """Check that the given scope exists, with its Owners group."""
        if users is None:
            users = []
        scope = Scope.objects.get(name=name)
        if group_name is not None:
            admin_group = Group.objects.get(scope=scope, name=group_name)
            self.assertQuerySetEqual(admin_group.users.all(), users)
            role = ScopeRole.objects.get(resource=scope, group=admin_group)
            self.assertEqual(role.role, ScopeRole.Roles.OWNER)
        if label is None:
            self.assertEqual(scope.label, scope.name.capitalize())
        else:
            self.assertEqual(scope.label, label)
        if icon is None:
            self.assertEqual(scope.icon, "")
        else:
            self.assertEqual(scope.icon, icon)
        self.assertQuerySetEqual(
            scope.file_stores.values_list(
                "name",
                "filestoreinscope__upload_priority",
                "filestoreinscope__download_priority",
            ),
            [(file_store_name, 100, 100)],
        )

    def test_create(self) -> None:
        """Test a successful create."""
        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--with-owners-group"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(group_name="Owners")

    def test_create_with_label(self) -> None:
        """Test setting label on create."""
        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--label=Scope Name"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(label="Scope Name")

    def test_update_label(self) -> None:
        """Test updating a label with create."""
        stdout, stderr, exit_code = call_command("scope", "create", "scope")
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(label="Scope")

        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--label=Changed"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(label="Changed")

    def test_create_with_icon(self) -> None:
        """Test setting icon on create."""
        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--icon=web/icons/scope.svg"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(icon="web/icons/scope.svg")

    def test_update_icon(self) -> None:
        """Test updating a label with create."""
        stdout, stderr, exit_code = call_command("scope", "create", "scope")
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope()

        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--icon=web/icons/scope.svg"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(icon="web/icons/scope.svg")

    def test_create_custom_group_name(self) -> None:
        """Test a successful create with specified group name."""
        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--with-owners-group=Admin"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(group_name="Admin")

    def test_create_custom_file_store_name(self) -> None:
        """Test a successful create with specified file store name."""
        FileStore.objects.create(
            name="custom",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "custom"},
        )

        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--file-store=custom"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope(file_store_name="custom")

    def test_idempotent(self) -> None:
        """Test idempotence in creating twice."""
        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--with-owners-group"
        )
        scope = Scope.objects.get(name="scope")
        admin_group = Group.objects.get(scope=scope, name="Owners")
        admin_group.users.set([self.playground.get_default_user()])

        # Recreate: the existing database objects are maintained
        stdout, stderr, exit_code = call_command(
            "scope", "create", "scope", "--with-owners-group"
        )
        new_scope = Scope.objects.get(name="scope")
        self.assertEqual(new_scope.pk, scope.pk)
        new_group = Group.objects.get(scope=new_scope, name="Owners")
        self.assertEqual(new_group.pk, admin_group.pk)

        # The previous group membership is also maintained
        self.assertHasScope(users=[self.playground.get_default_user()])

    def test_no_admin_group(self) -> None:
        """Test a successful create with no admin group."""
        stdout, stderr, exit_code = call_command("scope", "create", "scope")
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertHasScope()
        self.assertFalse(Group.objects.filter(scope__name="scope").exists())

    def test_new_name_invalid(self) -> None:
        """Test creating with an invalid name."""
        stdout, stderr, exit_code = call_command("scope", "create", "a/b")
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr.splitlines(),
            [
                "New scope would be invalid:",
                "* name: 'a/b' is not a valid scope name",
            ],
        )
        self.assertEqual(exit_code, 3)
        self.assertFalse(Scope.objects.filter(name="a/b").exists())

    def test_file_store_does_not_exist(self) -> None:
        """Passing a nonexistent file store name results in an error."""
        with self.assertRaisesRegex(
            CommandError, r"File store 'nonexistent' not found"
        ) as exc:
            call_command("scope", "create", "scope", "--file-store=nonexistent")

        self.assertEqual(getattr(exc.exception, "returncode"), 3)


class RenameScopeCommandTests(TestCase):
    """Tests for `scope rename` management command."""

    scope1: ClassVar[Scope]
    scope2: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope1 = cls.playground.get_or_create_scope(name="scope1")
        cls.scope2 = cls.playground.get_or_create_scope(name="scope2")

    def test_rename(self) -> None:
        """Test a successful rename."""
        stdout, stderr, exit_code = call_command(
            "scope", "rename", "scope1", "newname"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.scope1.refresh_from_db()
        self.assertEqual(self.scope1.name, "newname")

    def test_noop(self) -> None:
        """Test renaming to current name."""
        with mock.patch("debusine.db.models.Scope.save") as save:
            stdout, stderr, exit_code = call_command(
                "scope", "rename", "scope1", "scope1"
            )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        save.assert_not_called()

    def test_source_does_not_exist(self) -> None:
        """Test renaming a nonexisting scope."""
        with self.assertRaisesRegex(
            CommandError, r"Scope 'does-not-exist' not found"
        ) as exc:
            call_command("scope", "rename", "does-not-exist", "newname")

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_target_exists(self) -> None:
        """Test renaming with a name already in use."""
        stdout, stderr, exit_code = call_command(
            "scope", "rename", "scope1", "scope2"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr.splitlines(),
            [
                "Renamed scope would be invalid:",
                "* name: Scope with this Name already exists.",
            ],
        )

        self.assertEqual(exit_code, 3)
        self.scope1.refresh_from_db()
        self.assertEqual(self.scope1.name, "scope1")

    def test_new_name_invalid(self) -> None:
        """Test renaming to an invalid name."""
        stdout, stderr, exit_code = call_command(
            "scope", "rename", "scope1", "api"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr.splitlines(),
            [
                "Renamed scope would be invalid:",
                "* name: 'api' is not a valid scope name",
            ],
        )
        self.assertEqual(exit_code, 3)
        self.scope1.refresh_from_db()
        self.assertEqual(self.scope1.name, "scope1")

    def test_invalid_action(self) -> None:
        """Test invoking an invalid subcommand."""
        with self.assertRaisesRegex(
            CommandError, r"invalid choice: 'does-not-exist'"
        ) as exc:
            call_command("scope", "does-not-exist")

        self.assertEqual(getattr(exc.exception, "returncode"), 1)

    def test_unexpected_action(self) -> None:
        """Test a subcommand with no implementation."""
        command = Command()

        with self.assertRaisesRegex(
            CommandError, r"Action 'does_not_exist' not found"
        ) as exc:
            command.handle(action="does_not_exist")

        self.assertEqual(getattr(exc.exception, "returncode"), 3)


class AddFileStoreScopeCommandTests(TestCase):
    """Tests for `scope add_file_store` management command."""

    scope: ClassVar[Scope]
    file_store: ClassVar[FileStore]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope(name="scope")
        cls.file_store = FileStore.objects.create(
            name="store",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "store"},
        )

    def test_defaults(self) -> None:
        """Add a file store to a scope, with default properties."""
        stdout, stderr, exit_code = call_command(
            "scope",
            "add_file_store",
            self.scope.name,
            self.file_store.name,
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertIsNone(file_store_extra.upload_priority)
        self.assertIsNone(file_store_extra.download_priority)
        self.assertFalse(file_store_extra.populate)
        self.assertFalse(file_store_extra.drain)
        self.assertIsNone(file_store_extra.drain_to)
        self.assertFalse(file_store_extra.read_only)
        self.assertFalse(file_store_extra.write_only)
        self.assertIsNone(file_store_extra.soft_max_size)

    def test_properties(self) -> None:
        """Add a file store to a scope, with non-default properties."""
        stdout, stderr, exit_code = call_command(
            "scope",
            "add_file_store",
            self.scope.name,
            self.file_store.name,
            "--upload-priority",
            "50",
            "--download-priority",
            "200",
            "--populate",
            "--no-drain",
            "--drain-to",
            "other_store",
            "--soft-max-size",
            "1048576",
            "--no-read-only",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertEqual(file_store_extra.upload_priority, 50)
        self.assertEqual(file_store_extra.download_priority, 200)
        self.assertTrue(file_store_extra.populate)
        self.assertFalse(file_store_extra.drain)
        self.assertEqual(file_store_extra.drain_to, "other_store")
        self.assertFalse(file_store_extra.read_only)
        self.assertFalse(file_store_extra.write_only)
        self.assertEqual(file_store_extra.soft_max_size, 1048576)

    def test_nonexistent_scope(self) -> None:
        """Adding a file store to a nonexistent scope fails."""
        with self.assertRaisesRegex(
            CommandError, r"Scope 'does-not-exist' not found"
        ) as exc:
            call_command(
                "scope",
                "add_file_store",
                "does-not-exist",
                self.file_store.name,
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_nonexistent_file_store(self) -> None:
        """Adding a nonexistent file store to a scope fails."""
        with self.assertRaisesRegex(
            CommandError, r"File store 'does-not-exist' not found"
        ) as exc:
            call_command(
                "scope", "add_file_store", self.scope.name, "does-not-exist"
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)


class EditFileStoreScopeCommandTests(TestCase):
    """Tests for `scope edit_file_store` management command."""

    scope: ClassVar[Scope]
    file_store: ClassVar[FileStore]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope(name="scope")
        cls.file_store = FileStore.objects.create(
            name="store",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "store"},
        )

    def test_set_all_properties(self) -> None:
        """Set all properties of a file store in a scope."""
        self.scope.file_stores.add(
            self.file_store,
            through_defaults={"populate": True, "write_only": True},
        )

        stdout, stderr, exit_code = call_command(
            "scope",
            "edit_file_store",
            self.scope.name,
            self.file_store.name,
            "--upload-priority",
            "50",
            "--download-priority",
            "200",
            "--no-populate",
            "--drain",
            "--drain-to",
            "other_store",
            "--read-only",
            "--no-write-only",
            "--soft-max-size",
            "1048576",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertEqual(file_store_extra.upload_priority, 50)
        self.assertEqual(file_store_extra.download_priority, 200)
        self.assertFalse(file_store_extra.populate)
        self.assertTrue(file_store_extra.drain)
        self.assertEqual(file_store_extra.drain_to, "other_store")
        self.assertTrue(file_store_extra.read_only)
        self.assertFalse(file_store_extra.write_only)
        self.assertEqual(file_store_extra.soft_max_size, 1048576)

    def test_unchanged(self) -> None:
        """
        Leave all properties of a file store in a scope unchanged.

        This is mainly here for ease of test coverage.
        """
        self.scope.file_stores.add(
            self.file_store,
            through_defaults={
                "upload_priority": 100,
                "download_priority": 100,
                "populate": True,
                "drain": False,
                "drain_to": "other_store",
                "read_only": False,
                "write_only": True,
                "soft_max_size": 1048576,
            },
        )
        stdout, stderr, exit_code = call_command(
            "scope", "edit_file_store", self.scope.name, self.file_store.name
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertEqual(file_store_extra.upload_priority, 100)
        self.assertEqual(file_store_extra.download_priority, 100)
        self.assertTrue(file_store_extra.populate)
        self.assertFalse(file_store_extra.drain)
        self.assertEqual(file_store_extra.drain_to, "other_store")
        self.assertFalse(file_store_extra.read_only)
        self.assertTrue(file_store_extra.write_only)
        self.assertEqual(file_store_extra.soft_max_size, 1048576)

    def test_unset_upload_priority(self) -> None:
        """Unset upload priority for a file store in a scope."""
        self.scope.file_stores.add(
            self.file_store,
            through_defaults={"upload_priority": 50},
        )

        stdout, stderr, exit_code = call_command(
            "scope",
            "edit_file_store",
            self.scope.name,
            self.file_store.name,
            "--upload-priority",
            "",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertIsNone(file_store_extra.upload_priority)

    def test_unset_download_priority(self) -> None:
        """Unset download priority for a file store in a scope."""
        self.scope.file_stores.add(
            self.file_store, through_defaults={"download_priority": 50}
        )

        stdout, stderr, exit_code = call_command(
            "scope",
            "edit_file_store",
            self.scope.name,
            self.file_store.name,
            "--download-priority",
            "",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertIsNone(file_store_extra.download_priority)

    def test_unset_soft_max_size(self) -> None:
        """Unset soft maximum size for a file store in a scope."""
        self.scope.file_stores.add(
            self.file_store, through_defaults={"soft_max_size": 1048576}
        )

        stdout, stderr, exit_code = call_command(
            "scope",
            "edit_file_store",
            self.scope.name,
            self.file_store.name,
            "--soft-max-size",
            "",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        file_store_extra = self.scope.filestoreinscope_set.get(
            file_store=self.file_store
        )
        self.assertIsNone(file_store_extra.soft_max_size)

    def test_nonexistent_scope(self) -> None:
        """Editing a file store in a nonexistent scope fails."""
        with self.assertRaisesRegex(
            CommandError, r"Scope 'does-not-exist' not found"
        ) as exc:
            call_command(
                "scope",
                "edit_file_store",
                "does-not-exist",
                self.file_store.name,
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_nonexistent_file_store(self) -> None:
        """Editing a nonexistent file store in a scope fails."""
        with self.assertRaisesRegex(
            CommandError,
            fr"Scope '{self.scope.name}' has no file store named "
            fr"'does-not-exist'",
        ) as exc:
            call_command(
                "scope", "edit_file_store", self.scope.name, "does-not-exist"
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_not_in_this_scope(self) -> None:
        """Editing a file store that is not in the given scope fails."""
        with self.assertRaisesRegex(
            CommandError,
            fr"Scope '{self.scope.name}' has no file store named "
            fr"'{self.file_store.name}'",
        ) as exc:
            call_command(
                "scope",
                "edit_file_store",
                self.scope.name,
                self.file_store.name,
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)


class RemoveFileStoreScopeCommandTests(TestCase):
    """Tests for `scope remove_file_store` management command."""

    scope: ClassVar[Scope]
    file_store: ClassVar[FileStore]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope(name="scope")
        cls.file_store = FileStore.objects.create(
            name="store",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "store"},
        )

    def test_success(self) -> None:
        """Remove a file store from a scope."""
        self.scope.file_stores.set([self.file_store])
        workspace = self.playground.create_workspace(scope=self.scope)
        artifact, _ = self.playground.create_artifact(
            paths=["test"], workspace=workspace, create_files=True
        )
        other_store = FileStore.objects.create(
            name="other",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "other"},
        )
        self.scope.file_stores.add(other_store)
        other_store.files.add(artifact.files.get())

        stdout, stderr, exit_code = call_command(
            "scope", "remove_file_store", self.scope.name, self.file_store.name
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertNotIn(self.file_store, self.scope.file_stores.all())

    def test_remove_file_store_with_unique_files(self) -> None:
        """Cannot remove a store if some files in this scope are only there."""
        self.scope.file_stores.set([self.file_store])
        workspace = self.playground.create_workspace(scope=self.scope)
        self.playground.create_artifact(
            paths=["test"], workspace=workspace, create_files=True
        )

        with self.assertRaisesRegex(
            CommandError,
            fr"Scope '{self.scope.name}' contains some files that are only in "
            fr"file store '{self.file_store.name}'; drain it first",
        ) as exc:
            call_command(
                "scope",
                "remove_file_store",
                self.scope.name,
                self.file_store.name,
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_remove_file_store_nonexistent_scope(self) -> None:
        """Removing a file store from a nonexistent scope fails."""
        with self.assertRaisesRegex(
            CommandError, r"Scope 'does-not-exist' not found"
        ) as exc:
            call_command(
                "scope",
                "remove_file_store",
                "does-not-exist",
                self.file_store.name,
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_remove_file_store_nonexistent_file_store(self) -> None:
        """Removing a nonexistent file store from a scope fails."""
        with self.assertRaisesRegex(
            CommandError,
            fr"Scope '{self.scope.name}' has no file store named "
            fr"'does-not-exist'",
        ) as exc:
            call_command(
                "scope", "remove_file_store", self.scope.name, "does-not-exist"
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_remove_file_store_not_in_this_scope(self) -> None:
        """Removing a file store that is not in the given scope fails."""
        with self.assertRaisesRegex(
            CommandError,
            fr"Scope '{self.scope.name}' has no file store named "
            fr"'{self.file_store.name}'",
        ) as exc:
            call_command(
                "scope",
                "remove_file_store",
                self.scope.name,
                self.file_store.name,
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)


class ShowScopeCommandTests(TabularOutputTests, TestCase):
    """Tests for `scope show` management command."""

    file_stores: ClassVar[list[FileStore]]
    scope: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.file_stores = [
            FileStore.objects.create(
                name=name, backend=backend, configuration=configuration
            )
            for name, backend, configuration in (
                (
                    "default",
                    FileStore.BackendChoices.MEMORY,
                    {"name": "default"},
                ),
                ("backup", FileStore.BackendChoices.MEMORY, {"name": "backup"}),
                ("fast_download", FileStore.BackendChoices.LOCAL, {}),
            )
        ]
        cls.scope = cls.playground.get_or_create_scope(
            name="scope", label="Label"
        )
        cls.scope.file_stores.clear()
        cls.scope.file_stores.add(
            cls.file_stores[0], through_defaults={"upload_priority": 100}
        )
        cls.scope.file_stores.add(cls.file_stores[1])
        cls.scope.file_stores.add(
            cls.file_stores[2], through_defaults={"download_priority": 200}
        )

    def test_show(self) -> None:
        """Show a scope."""
        workspace = self.playground.create_workspace(scope=self.scope)
        artifacts = [
            self.playground.create_artifact(
                paths=["test"], workspace=workspace, create_files=True
            )[0]
            for _ in range(5)
        ]
        for artifact in artifacts[:2]:
            self.file_stores[1].files.add(artifact.files.get())

        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command("scope", "show", self.scope.name)

        self.assertEqual(output.col(0), ["scope", "", ""])
        self.assertEqual(output.col(1), ["Label", "", ""])
        self.assertEqual(output.col(2), ["default", "fast_download", "backup"])
        self.assertEqual(output.col(3), ["Memory", "Local", "Memory"])
        self.assertEqual(output.col(4), ["100", "", ""])
        self.assertEqual(output.col(5), ["", "200", ""])
        self.assertEqual(output.col(6), ["5", "0", "2"])
