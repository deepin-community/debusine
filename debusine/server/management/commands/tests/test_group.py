# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command group."""
from typing import ClassVar
from unittest import mock

from django.core.management import CommandError

from debusine.db.models import Group, Scope, User
from debusine.db.playground import scenarios
from debusine.django.management.tests import call_command
from debusine.server.management.commands.group import Command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class GroupCommandTests(TabularOutputTests, TestCase):
    """Tests for group create management command."""

    scenario = scenarios.DefaultScopeUser()
    group: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("group")

    def assertGroup(
        self, scope: Scope, name: str, users: list[User] | None = None
    ) -> Group:
        """Check that the given scope exists, with its Admin group."""
        if users is None:
            users = []
        group = Group.objects.get(scope=scope, name=name)
        self.assertQuerySetEqual(group.users.all(), users, ordered=False)
        return group

    def test_invalid_action(self) -> None:
        """Test invoking an invalid subcommand."""
        with self.assertRaisesRegex(
            CommandError, r"invalid choice: 'does-not-exist'"
        ) as exc:
            call_command("group", "does-not-exist")

        self.assertEqual(getattr(exc.exception, "returncode"), 1)

    def test_unexpected_action(self) -> None:
        """Test a subcommand with no implementation."""
        command = Command()

        with self.assertRaisesRegex(
            CommandError, r"Action 'does_not_exist' not found"
        ) as exc:
            command.handle(action="does_not_exist")

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_get_scope_and_group_name(self) -> None:
        """Test get_scope_and_group_name."""
        command = Command()
        self.assertEqual(
            command.get_scope_and_group_name("debusine/group"),
            (self.scenario.scope, "group"),
        )
        self.assertEqual(
            command.get_scope_and_group_name("debusine/test"),
            (self.scenario.scope, "test"),
        )

        with self.assertRaisesRegex(
            CommandError,
            r"scope_group 'group' should be in the form 'scopename/groupname'",
        ) as exc:
            command.get_scope_and_group_name("group")
        self.assertEqual(getattr(exc.exception, "returncode"), 3)

        with self.assertRaisesRegex(
            CommandError, r"Scope 'test' not found"
        ) as exc:
            command.get_scope_and_group_name("test/group")
        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_get_group(self) -> None:
        """Test get_group."""
        command = Command()
        self.assertEqual(
            command.get_group(self.scenario.scope, "group"), self.group
        )

        with self.assertRaisesRegex(
            CommandError, r"Group 'test' not found in scope 'debusine'"
        ) as exc:
            command.get_group(self.scenario.scope, "test")
        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_get_users(self) -> None:
        """Test get_users."""
        user1 = self.playground.create_user("user1")
        user2 = self.playground.create_user("user2")

        command = Command()
        self.assertEqual(command.get_users(["user1", "user2"]), [user1, user2])

    def test_get_users_empty(self) -> None:
        """Test get_users with an empty username list."""
        command = Command()
        self.assertEqual(command.get_users([]), [])

    def test_get_users_nonexisting(self) -> None:
        """Test get_users with nonexisting usernames."""
        command = Command()
        with self.assertRaisesRegex(
            CommandError,
            r"User 'missing1' does not exist\n"
            r"User 'missing2' does not exist",
        ) as exc:
            command.get_users(["missing1", "missing2"])
        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_list_empty(self) -> None:
        """Test group list with no users."""
        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "group", "list", "debusine"
            )

        self.assertEqual(output.col(0), ["debusine"])
        self.assertEqual(output.col(1), ["group"])
        self.assertEqual(output.col(2), ["0"])

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_list(self) -> None:
        """Test group list."""
        self.playground.add_user(self.group, self.scenario.user)
        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "group", "list", "debusine"
            )

        self.assertEqual(output.col(0), ["debusine"])
        self.assertEqual(output.col(1), ["group"])
        self.assertEqual(output.col(2), ["1"])

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create(self) -> None:
        """Test a successful create."""
        stdout, stderr, exit_code = call_command(
            "group", "create", "debusine/group1"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        self.assertGroup(self.scenario.scope, "group1")

    def test_create_idempotent(self) -> None:
        """Test a idempotence in create twice."""
        stdout, stderr, exit_code = call_command(
            "group", "create", "debusine/group1"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        group = self.assertGroup(self.scenario.scope, "group1")
        self.playground.add_user(group, self.playground.get_default_user())

        stdout, stderr, exit_code = call_command(
            "group", "create", "debusine/group1"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        group1 = self.assertGroup(
            self.scenario.scope, "group1", [self.playground.get_default_user()]
        )

        self.assertEqual(group.pk, group1.pk)

    def test_create_invalid_name(self) -> None:
        """Test a successful create."""
        stdout, stderr, exit_code = call_command(
            "group", "create", "debusine/group/name"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "Created group would be invalid:\n"
            "* name: 'group/name' is not a valid group name\n",
        )
        self.assertEqual(exit_code, 3)
        self.assertQuerySetEqual(Group.objects.filter(name="group/name"), [])

    def test_rename(self) -> None:
        """Test a successful rename."""
        self.playground.add_user(self.group, self.playground.get_default_user())
        stdout, stderr, exit_code = call_command(
            "group", "rename", "debusine/group", "group1"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertGroup(
            self.scenario.scope, "group1", [self.playground.get_default_user()]
        )

    def test_rename_noop(self) -> None:
        """Test renaming to current name."""
        self.playground.add_user(self.group, self.playground.get_default_user())

        with mock.patch("debusine.db.models.Group.save") as save:
            stdout, stderr, exit_code = call_command(
                "group", "rename", "debusine/group", "group"
            )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertGroup(
            self.scenario.scope, "group", [self.playground.get_default_user()]
        )

        save.assert_not_called()

    def test_rename_group_does_not_exist(self) -> None:
        """Test renaming a nonexisting group."""
        with self.assertRaisesRegex(
            CommandError, r"Group 'does-not-exist' not found"
        ) as exc:
            call_command(
                "group", "rename", "debusine/does-not-exist", "newname"
            )

        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_rename_target_exists(self) -> None:
        """Test renaming with a name already in use."""
        group = self.playground.create_group("test")
        stdout, stderr, exit_code = call_command(
            "group", "rename", "debusine/test", "group"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr.splitlines(),
            [
                "Renamed group would be invalid:",
                "* Group with this Name and Scope already exists.",
            ],
        )

        self.assertEqual(exit_code, 3)
        group.refresh_from_db()
        self.assertEqual(group.name, "test")

    def test_rename_new_name_invalid(self) -> None:
        """Test renaming to an invalid name."""
        stdout, stderr, exit_code = call_command(
            "group", "rename", "debusine/group", "invalid/name"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr.splitlines(),
            [
                "Renamed group would be invalid:",
                "* name: 'invalid/name' is not a valid group name",
            ],
        )
        self.assertEqual(exit_code, 3)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "group")

    def test_delete(self) -> None:
        """Test a successful delete."""
        self.playground.add_user(self.group, self.playground.get_default_user())
        stdout, stderr, exit_code = call_command(
            "group",
            "delete",
            "debusine/group",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertQuerySetEqual(
            Group.objects.filter(scope=self.scenario.scope), []
        )

    def test_delete_missing(self) -> None:
        """Test idempotence in deleting a missing group."""
        self.playground.add_user(self.group, self.playground.get_default_user())
        stdout, stderr, exit_code = call_command(
            "group",
            "delete",
            "debusine/missing",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertQuerySetEqual(
            Group.objects.filter(scope=self.scenario.scope), [self.group]
        )

    def test_members_set(self) -> None:
        """Test members --set."""
        user1 = self.playground.create_user("user1")
        user2 = self.playground.create_user("user2")
        self.playground.add_user(self.group, self.playground.get_default_user())

        stdout, stderr, exit_code = call_command(
            "group", "members", "debusine/group", "--set", "user1", "user2"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertQuerySetEqual(
            self.group.users.all(), [user1, user2], ordered=False
        )

    def test_members_set_empty(self) -> None:
        """Test members --set to the empty set."""
        self.playground.add_user(self.group, self.playground.get_default_user())

        stdout, stderr, exit_code = call_command(
            "group", "members", "debusine/group", "--set"
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertQuerySetEqual(self.group.users.all(), [])

    def test_members_add(self) -> None:
        """Test members --add."""
        user1 = self.playground.create_user("user1")
        self.playground.add_user(self.group, self.playground.get_default_user())

        stdout, stderr, exit_code = call_command(
            "group",
            "members",
            "debusine/group",
            "--add",
            "user1",
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertQuerySetEqual(
            self.group.users.all(),
            [self.playground.get_default_user(), user1],
            ordered=False,
        )

    def test_members_remove(self) -> None:
        """Test members --remove."""
        user1 = self.playground.create_user("user1")
        self.playground.add_user(self.group, self.playground.get_default_user())
        self.playground.add_user(self.group, user1)

        stdout, stderr, exit_code = call_command(
            "group",
            "members",
            "debusine/group",
            "--remove",
            self.playground.get_default_user().username,
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertQuerySetEqual(
            self.group.users.all(),
            [user1],
        )

    def test_members_mix(self) -> None:
        """Test running multiple members actions."""
        with self.assertRaisesRegex(
            CommandError,
            r"Error: argument --add: not allowed with argument --remove",
        ) as exc:
            call_command(
                "group",
                "members",
                "debusine/group",
                "--remove",
                "1",
                "--add",
                "2",
            )
        self.assertEqual(getattr(exc.exception, "returncode"), 1)

    def test_members_list_empty(self) -> None:
        """Test group members with no users."""
        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "group", "members", "debusine/group"
            )

        self.assertEqual(output.col(0), [])
        self.assertEqual(output.col(1), [])
        self.assertEqual(output.col(2), [])

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_members_list(self) -> None:
        """Test group members."""
        self.playground.add_user(self.group, self.scenario.user)
        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "group", "members", "debusine/group"
            )

        self.assertEqual(output.col(0), [self.scenario.user.username])
        self.assertEqual(output.col(1), [self.scenario.user.email])
        self.assertEqual(
            output.col(2), [self.scenario.user.date_joined.isoformat()]
        )

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
