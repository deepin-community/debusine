# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for auth-related models."""

import contextlib
import re
from collections.abc import Generator
from datetime import timedelta
from typing import ClassVar
from unittest import mock

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.utils import timezone

import debusine.db.models.auth as auth
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import (
    Group,
    Identity,
    Scope,
    Token,
    User,
    Worker,
    Workspace,
)
from debusine.db.models.auth import (
    ClientEnroll,
    GroupAuditLog,
    GroupMembership,
    GroupRoleBase,
    GroupRoles,
    system_user,
)
from debusine.db.models.tests.utils import RolesTestCase
from debusine.db.playground import scenarios
from debusine.test.django import ChannelsHelpersMixin, TestCase


class TokenTests(ChannelsHelpersMixin, TestCase):
    """Unit tests of the ``Token`` model."""

    scenario = scenarios.DefaultScopeUser()

    def test_save(self) -> None:
        """The model creates a Token.key on save or keeps it if it existed."""
        token = Token.objects.create(user=self.scenario.user)

        self.assertIsNotNone(token.id)
        self.assertEqual(len(token.key), 64)

        key = token.key
        token.save()
        self.assertEqual(token.key, key)

    def test_str(self) -> None:
        """Test Token.__str__."""
        token = Token.objects.create(user=self.scenario.user)
        self.assertEqual(token.__str__(), token.hash)

    def test_user_field(self) -> None:
        """Test User field is None by default."""
        token = Token.objects.create()
        self.assertIsNone(token.user)

    def test_get_token_or_none_found(self) -> None:
        """get_token_or_none looks up a token and returns it."""
        token_hash = Token._generate_hash('some_key')
        token = Token.objects.create(hash=token_hash)

        self.assertEqual(token, Token.objects.get_token_or_none('some_key'))

        # Worker is supposed to be prefetched, but it doesn't work in this
        # case: django bug? This is important because hasattr looks like a
        # harmless thing to call from async code
        with self.assertNumQueries(1):
            self.assertFalse(hasattr(token, "worker"))
        with self.assertNumQueries(1):
            self.assertFalse(hasattr(token, "activating_worker"))

    def test_get_token_or_none_expires_soon(self) -> None:
        """get_token_or_none allows tokens with `expire_at` in the future."""
        token_hash = Token._generate_hash('some_key')
        token = Token.objects.create(
            hash=token_hash, expire_at=timezone.now() + timedelta(days=1)
        )

        self.assertEqual(token, Token.objects.get_token_or_none('some_key'))

    def test_get_token_or_none_expired(self) -> None:
        """get_token_or_none skips tokens with `expire_at` in the past."""
        token_hash = Token._generate_hash('some_key')
        Token.objects.create(
            hash=token_hash, expire_at=timezone.now() - timedelta(seconds=1)
        )

        self.assertIsNone(Token.objects.get_token_or_none('some_key'))

    def test_get_token_or_none_not_found(self) -> None:
        """get_token_or_none cannot find a token and returns None."""
        self.assertIsNone(Token.objects.get_token_or_none('a_non_existing_key'))

    def test_get_token_worker_prefetch(self) -> None:
        """get_token_or_none prefetches the worker attribute if present."""
        token_hash = Token._generate_hash('some_key')
        token = Token.objects.create(hash=token_hash)
        worker = self.playground.create_worker()
        worker.token = token

        self.assertEqual(token, Token.objects.get_token_or_none('some_key'))

        # worker is prefetched
        with self.assertNumQueries(0):
            self.assertEqual(token.worker, worker)

    def test_get_token_activating_worker_prefetch(self) -> None:
        """get_token_or_none prefetches activating_worker if present."""
        token_hash = Token._generate_hash('some_key')
        token = Token.objects.create(hash=token_hash)
        activating_worker = self.playground.create_worker()
        activating_worker.activation_token = token

        self.assertEqual(token, Token.objects.get_token_or_none('some_key'))

        # worker is prefetched
        with self.assertNumQueries(0):
            self.assertEqual(token.activating_worker, activating_worker)

    def test_enable(self) -> None:
        """enable() enables the token."""
        token = Token.objects.create()

        # Assert the default is disabled tokens
        self.assertFalse(token.enabled)

        token.enable()
        token.refresh_from_db()

        self.assertTrue(token.enabled)

    async def test_disable(self) -> None:
        """disable() disables the token."""
        token = await Token.objects.acreate(enabled=True)
        await Worker.objects.acreate(token=token, registered_at=timezone.now())

        channel = await self.create_channel(token.hash)

        await sync_to_async(token.disable)()

        await self.assert_channel_received(channel, {"type": "worker.disabled"})
        await token.arefresh_from_db()

        self.assertFalse(token.enabled)


class TokenManagerTests(TestCase):
    """Unit tests for the ``TokenManager`` class."""

    user_john: ClassVar[User]
    user_bev: ClassVar[User]
    token_john: ClassVar[Token]
    token_bev: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Test data used by all the tests."""
        super().setUpTestData()
        cls.user_john = get_user_model().objects.create_user(
            username="John", email="john@example.com"
        )
        cls.user_bev = get_user_model().objects.create_user(
            username="Bev", email="bev@example.com"
        )
        cls.token_john = Token.objects.create(user=cls.user_john)
        cls.token_bev = Token.objects.create(user=cls.user_bev)

    def test_get_tokens_all(self) -> None:
        """get_tokens returns all the tokens if no filter is applied."""
        self.assertQuerySetEqual(
            Token.objects.get_tokens(),
            {self.token_bev, self.token_john},
            ordered=False,
        )

    def test_get_tokens_by_owner(self) -> None:
        """get_tokens returns the correct tokens when filtering by owner."""
        self.assertQuerySetEqual(
            Token.objects.get_tokens(username='John'), [self.token_john]
        )
        self.assertQuerySetEqual(
            Token.objects.get_tokens(username='Bev'), [self.token_bev]
        )
        self.assertQuerySetEqual(
            Token.objects.get_tokens(username='Someone'), []
        )

    def test_get_tokens_by_key(self) -> None:
        """get_tokens returns the correct tokens when filtering by key."""
        self.assertQuerySetEqual(
            Token.objects.get_tokens(key=self.token_john.key),
            [self.token_john],
        )
        self.assertQuerySetEqual(
            Token.objects.get_tokens(key='non-existing-key'), []
        )

    def test_get_tokens_by_key_owner_empty(self) -> None:
        """
        get_tokens returns nothing if using a key and username without matches.

        Key for the key parameter or username for the user parameter exist
        but are for different tokens.
        """
        self.assertQuerySetEqual(
            Token.objects.get_tokens(
                key=self.token_john.key, username=self.user_bev.username
            ),
            [],
        )

    def test_get_tokens_include_expired(self) -> None:
        """get_tokens only includes expired tokens if requested."""
        self.token_john.expire_at = timezone.now() - timedelta(seconds=1)
        self.token_john.save()
        self.assertQuerySetEqual(Token.objects.get_tokens(username="John"), [])
        self.assertQuerySetEqual(
            Token.objects.get_tokens(username="John", include_expired=True),
            [self.token_john],
        )

    def test_expired(self) -> None:
        """``expired`` returns only the expired tokens."""
        now = timezone.now()
        tokens = [
            Token.objects.create(expire_at=expire_at)
            for expire_at in (
                None,
                now - timedelta(minutes=1),
                now - timedelta(seconds=1),
                now,
                now + timedelta(seconds=1),
            )
        ]

        self.assertQuerySetEqual(
            Token.objects.expired(at=now), tokens[1:3], ordered=False
        )
        self.assertQuerySetEqual(
            Token.objects.expired(at=now + timedelta(seconds=1)),
            tokens[1:4],
            ordered=False,
        )


class UserTests(TestCase):
    """Tests for the User class."""

    scenario = scenarios.DefaultScopeUser()
    user: ClassVar[User]
    system_user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.user = cls.playground.create_user("test")
        cls.system_user = system_user()

    def test_can_display_anonymous(self) -> None:
        """Test can_display with anonymous user."""
        self.assertPermission(
            "can_display",
            AnonymousUser(),
            denied=[self.scenario.user, self.user, self.system_user],
        )

    def test_can_display(self) -> None:
        """Test the can_display predicate."""
        self.assertPermission(
            "can_display",
            [self.scenario.user, self.user, self.system_user],
            allowed=[self.scenario.user, self.user, self.system_user],
        )

    def test_can_manage_anonymous(self) -> None:
        """Test can_manage with anonymous user."""
        self.assertPermission(
            "can_manage",
            AnonymousUser(),
            denied=[self.scenario.user, self.user, self.system_user],
        )

    def test_can_manage(self) -> None:
        """Test can_manage."""
        self.assertPermission(
            "can_manage",
            self.scenario.user,
            allowed=self.scenario.user,
            denied=self.user,
        )
        self.assertPermission(
            "can_manage",
            self.user,
            allowed=self.user,
            denied=self.scenario.user,
        )

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""
        self.assertEqual(
            self.scenario.user.get_absolute_url(),
            f"/-/user/{self.scenario.user.username}/",
        )


class IdentityTests(TestCase):
    """Tests for the Identity class."""

    def test_str(self) -> None:
        """Stringification should show the unique key."""
        ident = Identity(issuer="salsa", subject="test@debian.org")
        self.assertEqual(str(ident), "salsa:test@debian.org")


class GroupRolesTests(RolesTestCase[GroupRoles]):
    """Tests for the GroupRoles class."""

    scenario = scenarios.DefaultContext()
    roles_class = GroupRoles

    def test_invariants(self) -> None:
        self.assertRolesInvariants()

    def test_choices(self) -> None:
        self.assertChoices()

    def test_init(self) -> None:
        r = GroupRoleBase("test")
        r._setup()
        self.assertEqual(r.implied_by_scope_roles, frozenset())
        self.assertEqual(r.implied_by_group_roles, frozenset([r]))

    def test_init_invalid_implication(self) -> None:
        r = GroupRoleBase("test", implied_by=[Workspace.Roles.OWNER])
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"Group roles do not support implications"
            r" by <enum 'WorkspaceRoles'>",
        ):
            r._setup()

    def test_values(self) -> None:
        self.assertEqual(Group.Roles.ADMIN.label, "Admin")
        self.assertEqual(
            Group.Roles.ADMIN.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Group.Roles.ADMIN.implied_by_group_roles,
            frozenset([Group.Roles.ADMIN]),
        )

        self.assertEqual(Group.Roles.MEMBER.label, "Member")
        self.assertEqual(
            Group.Roles.MEMBER.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Group.Roles.MEMBER.implied_by_group_roles,
            frozenset([Group.Roles.ADMIN, Group.Roles.MEMBER]),
        )


class GroupManagerTests(TestCase):
    """Test for GroupManager class."""

    scenario = scenarios.DefaultScopeUser()
    scope1: ClassVar[Scope]
    scope2: ClassVar[Scope]
    group1: ClassVar[Group]
    group2: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope1 = cls.playground.get_or_create_scope("scope1")
        cls.group1 = cls.playground.create_group("group", scope=cls.scope1)
        cls.scope2 = cls.playground.get_or_create_scope("scope2")
        cls.group2 = cls.playground.create_group("group", scope=cls.scope2)

    def test_from_scoped_name(self) -> None:
        """Test from_scoped_name."""
        for arg, result in (
            ("scope1/group", self.group1),
            ("scope2/group", self.group2),
        ):
            with self.subTest(arg=arg):
                self.assertEqual(Group.objects.from_scoped_name(arg), result)

    def test_from_scoped_name_fail_lookup(self) -> None:
        """Test from_scoped_name."""
        for arg, exc_class in (
            ("scope1/fail", Group.DoesNotExist),
            ("scope2:group", ValueError),
        ):
            with self.subTest(arg=arg), self.assertRaises(exc_class):
                Group.objects.from_scoped_name(arg)

    def test_create_ephemeral(self) -> None:
        """Test create_ephemeral."""
        self.scenario.set_current()
        user = self.playground.get_default_user()
        group = Group.objects.create_ephemeral(
            scope=self.scope1, name="test", owner=user
        )
        self.assertEqual(group.scope, self.scope1)
        self.assertEqual(group.name, "test")
        self.assertQuerySetEqual(group.users.all(), [user])

        membership = GroupMembership.objects.get(group=group, user=user)
        self.assertEqual(membership.role, Group.Roles.ADMIN)

        audit_logs = list(GroupAuditLog.objects.order_by("created_at"))
        self.assertEqual(len(audit_logs), 2)
        # Group creation audit log
        audit_log = audit_logs[0]
        self.assertEqual(audit_log.group, group)
        self.assertEqual(audit_log.actor, self.scenario.user)
        changes_add = auth.GroupAuditLogCreated.create(group)
        changes_add.actor = self.scenario.user.username
        self.assertEqual(audit_log.changes, changes_add.dict())
        # Admin added audit log
        audit_log = audit_logs[1]
        self.assertEqual(audit_log.group, group)
        self.assertEqual(audit_log.actor, self.scenario.user)
        changes_memb = auth.GroupAuditLogMemberAdded(
            user=self.scenario.user.username,
            role=Group.Roles.ADMIN,
            actor=self.scenario.user.username,
        )
        self.assertEqual(audit_log.changes, changes_memb.dict())

    def test_unused_ephemeral_unused(self) -> None:
        """Test unused_ephemeral."""
        self.assertQuerySetEqual(Group.objects.unused_ephemeral(), [])

        with context.disable_permission_checks():
            self.group1.ephemeral = True
            self.group1.save()
        self.assertQuerySetEqual(
            Group.objects.unused_ephemeral(), [self.group1]
        )

    def test_unused_ephemeral_used_by_scope(self) -> None:
        """Test unused_ephemeral for a group used by a Scope."""
        with context.disable_permission_checks():
            self.group1.ephemeral = True
            self.group1.save()
        self.assertQuerySetEqual(
            Group.objects.unused_ephemeral(), [self.group1]
        )

        self.group1.assign_role(self.scope1, Scope.Roles.OWNER)
        self.assertQuerySetEqual(Group.objects.unused_ephemeral(), [])

    def test_unused_ephemeral_used_by_workspace(self) -> None:
        """Test unused_ephemeral for a group used by a Workspace."""
        with context.disable_permission_checks():
            self.group1.ephemeral = True
            self.group1.save()
        self.assertQuerySetEqual(
            Group.objects.unused_ephemeral(), [self.group1]
        )

        workspace = self.playground.create_workspace(scope=self.scope1)
        self.group1.assign_role(workspace, workspace.Roles.OWNER)
        self.assertQuerySetEqual(Group.objects.unused_ephemeral(), [])

    def test_unused_ephemeral_used_by_asset(self) -> None:
        """Test unused_ephemeral for a group used by an Asset."""
        with context.disable_permission_checks():
            self.group1.ephemeral = True
            self.group1.save()
        self.assertQuerySetEqual(
            Group.objects.unused_ephemeral(), [self.group1]
        )

        workspace = self.playground.create_workspace(scope=self.scope1)
        asset = self.playground.create_signing_key_asset(workspace=workspace)
        self.group1.assign_role(asset, asset.Roles.OWNER)
        self.assertQuerySetEqual(Group.objects.unused_ephemeral(), [])

    def test_unused_ephemeral_used_by_asset_usage(self) -> None:
        """Test unused_ephemeral for a group used by an AssetUsage."""
        with context.disable_permission_checks():
            self.group1.ephemeral = True
            self.group1.save()
        self.assertQuerySetEqual(
            Group.objects.unused_ephemeral(), [self.group1]
        )

        workspace = self.playground.create_workspace(scope=self.scope1)
        asset = self.playground.create_signing_key_asset(workspace=workspace)
        asset_usage = self.playground.create_asset_usage(
            asset, workspace=workspace
        )
        self.group1.assign_role(asset_usage, asset_usage.Roles.SIGNER)
        self.assertQuerySetEqual(Group.objects.unused_ephemeral(), [])


class GroupTests(TestCase):
    """Test for Group class."""

    scenario = scenarios.DefaultContext()

    group: ClassVar[Group]
    other_scope: ClassVar[Scope]
    other_group: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("group")
        cls.other_scope = cls.playground.get_or_create_scope(name="other")
        cls.other_group = cls.playground.create_group(
            "other", scope=cls.other_scope
        )

    def assertRole(
        self, group: Group, user: User, role: Group.Roles | None
    ) -> None:
        """
        Ensure the user has the given role in the group.

        Set role to None to ensure the user is not a member of the group.
        """
        membership: GroupMembership | None
        try:
            membership = GroupMembership.objects.get(user=user, group=group)
        except GroupMembership.DoesNotExist:
            membership = None
        if role is None:
            if membership is None:
                pass
            else:
                self.fail(
                    f"{user} unexpectedly has role {membership.role} in {group}"
                )
        else:
            if membership is None:
                self.fail(f"{user} is unexpectedly not a member of {group}")
            else:
                self.assertEqual(membership.role, role)

    @contextlib.contextmanager
    def assertGroupAuditLog(
        self,
    ) -> Generator[list[auth.GroupAuditLogEntry], None, None]:
        """
        Ensure add_audit_log is called.

        Generates a list populated with the messages logged.
        """
        changes: list[auth.GroupAuditLogEntry] = []
        with mock.patch(
            "debusine.db.models.auth.Group.add_audit_log"
        ) as add_audit_log:
            yield changes
        add_audit_log.assert_called()
        for call in add_audit_log.call_args_list:
            changes.append(call.args[0])

    def test_assertRole(self) -> None:
        """Test assertRole."""
        self.scenario.set_current()
        self.assertRole(self.group, self.scenario.user, None)
        with self.assertRaisesRegex(
            AssertionError, "is unexpectedly not a member of debusine/group"
        ):
            self.assertRole(self.group, self.scenario.user, Group.Roles.MEMBER)
        self.group.add_user(self.scenario.user, Group.Roles.ADMIN)
        self.assertRole(self.group, self.scenario.user, Group.Roles.ADMIN)
        with self.assertRaisesRegex(
            AssertionError, re.escape(f"'admin' != {Group.Roles.MEMBER!r}")
        ):
            self.assertRole(self.group, self.scenario.user, Group.Roles.MEMBER)
        with self.assertRaisesRegex(
            AssertionError,
            "playground unexpectedly has role admin in debusine/group",
        ):
            self.assertRole(self.group, self.scenario.user, None)

    def test_str(self) -> None:
        """Test stringification."""
        self.assertEqual(str(self.group), f"{self.scenario.scope.name}/group")

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""
        self.assertEqual(
            self.group.get_absolute_url(),
            "/debusine/-/groups/group/",
        )
        self.assertEqual(
            self.other_group.get_absolute_url(),
            "/other/-/groups/other/",
        )

    def test_defaults(self) -> None:
        """Test default values."""
        self.assertFalse(self.group.ephemeral)

    def test_unique(self) -> None:
        """Test scoped uniqueness."""
        scope2 = self.playground.get_or_create_scope("scope2")
        group2 = self.playground.create_group(self.group.name, scope=scope2)
        self.assertNotEqual(self.group.pk, group2.pk)

        with self.assertRaisesRegex(
            IntegrityError,
            # Only match constraint name to support non-english locales
            # r"duplicate key value violates unique constraint"
            r"db_group_unique_name_scope",
        ):
            Group.objects.create(name=self.group.name, scope=self.group.scope)

    def test_assign_user(self) -> None:
        """Test assigning users to groups."""
        user1 = User.objects.create_user(
            username="user1",
            email="user1@debian.org",
        )
        user2 = User.objects.create_user(
            username="user2",
            email="user2@debian.org",
        )

        self.playground.add_user(self.group, user1)

        self.assertQuerySetEqual(self.group.users.all(), [user1])
        self.assertQuerySetEqual(user1.groups.all(), [])
        self.assertQuerySetEqual(user1.debusine_groups.all(), [self.group])
        self.assertEqual(
            user1.group_memberships.get(group=self.group).role,
            Group.Roles.MEMBER,
        )

        self.playground.add_user(self.group, user2)

        self.assertQuerySetEqual(
            self.group.users.all(), [user1, user2], ordered=False
        )
        self.assertQuerySetEqual(user2.groups.all(), [])
        self.assertQuerySetEqual(user2.debusine_groups.all(), [self.group])

    def test_assign_role(self) -> None:
        """Test assign_role."""
        for resource, role in (
            (self.scenario.scope, Scope.Roles.OWNER),
            (self.scenario.scope, "owner"),
            (self.scenario.workspace, Workspace.Roles.OWNER),
            (self.scenario.workspace, "owner"),
        ):
            with self.subTest(resource=resource, role=role):
                assigned = self.group.assign_role(resource, role)
                # TODO: we can remove the type ignores once we find a way to
                # generically type the role assignment models
                self.assertEqual(
                    assigned.resource, resource  # type: ignore[attr-defined]
                )
                self.assertEqual(
                    assigned.group, self.group  # type: ignore[attr-defined]
                )
                self.assertEqual(
                    assigned.role, role  # type: ignore[attr-defined]
                )

    def test_assign_role_not_a_resource(self) -> None:
        """Try assigning role to something which is not a resource."""
        token = self.playground.create_bare_token()

        with self.assertRaisesRegex(
            NotImplementedError, r"Cannot get scope for Token object"
        ):
            self.group.assign_role(token, "owner")

    def test_assign_role_wrong_scope(self) -> None:
        """Try assigning role to a resource in a different scope."""
        scope2 = self.playground.get_or_create_scope("scope2")
        with self.assertRaisesRegex(
            ValueError, r"Scope 'scope2' is not in scope debusine"
        ):
            self.group.assign_role(scope2, "owner")

    def test_assign_role_wrong_role_enum(self) -> None:
        """Try assigning a role using the wrong enum."""
        with self.assertRaisesRegex(
            TypeError,
            re.escape(
                f"{Scope.Roles.OWNER!r} cannot be converted to"
                " <enum 'WorkspaceRoles'>"
            ),
        ):
            self.group.assign_role(self.scenario.workspace, Scope.Roles.OWNER)

    def test_assign_role_wrong_role_name(self) -> None:
        """Try assigning a role using the wrong enum."""
        with self.assertRaisesRegex(
            ValueError, r"'does-not-exist' is not a valid WorkspaceRoles"
        ):
            self.group.assign_role(self.scenario.workspace, "does-not-exist")

    def test_get_user_role(self) -> None:
        """Ensure get_user_role returns the user's role."""
        self.playground.add_user(self.group, self.scenario.user)
        self.assertEqual(
            self.group.get_user_role(self.scenario.user), Group.Roles.MEMBER
        )

        GroupMembership.objects.create(
            user=self.scenario.user,
            group=self.other_group,
            role=Group.Roles.ADMIN,
        )
        self.assertEqual(
            self.other_group.get_user_role(self.scenario.user),
            Group.Roles.ADMIN,
        )

    def test_get_user_role_not_member(self) -> None:
        """get_user_role raises ValueError if the user is not a member."""
        with self.assertRaisesRegex(
            ValueError,
            r"User playground is not a member of group debusine/group",
        ):
            self.group.get_user_role(self.scenario.user)

    def test_set_user_role(self) -> None:
        """Set the role of a user on a group."""
        self.scenario.set_current()
        self.group.add_user(self.scenario.user)
        self.assertEqual(
            self.group.get_user_role(self.scenario.user), Group.Roles.MEMBER
        )
        with self.assertGroupAuditLog() as changes:
            self.group.set_user_role(self.scenario.user, Group.Roles.ADMIN)
        self.assertEqual(
            self.group.get_user_role(self.scenario.user), Group.Roles.ADMIN
        )
        self.assertEqual(
            changes,
            [
                auth.GroupAuditLogMemberRoleChanged(
                    user=self.scenario.user.username, role=Group.Roles.ADMIN
                )
            ],
        )

        with self.assertGroupAuditLog() as changes:
            self.group.set_user_role(self.scenario.user, Group.Roles.MEMBER)
        self.assertEqual(
            self.group.get_user_role(self.scenario.user), Group.Roles.MEMBER
        )
        self.assertEqual(
            changes,
            [
                auth.GroupAuditLogMemberRoleChanged(
                    user=self.scenario.user.username, role=Group.Roles.MEMBER
                )
            ],
        )

    def test_set_user_role_not_a_member(self) -> None:
        """Set the role of a user not a member of the group."""
        with self.assertRaisesRegex(
            ValueError,
            r"User playground is not a member of group debusine/group",
        ):
            self.group.set_user_role(self.scenario.user, Group.Roles.ADMIN)

    def test_add_user(self) -> None:
        """Add a nonexisting user to a group."""
        self.assertRole(self.group, self.scenario.user, None)
        with self.assertGroupAuditLog() as changes:
            self.assertIsInstance(
                self.group.add_user(self.scenario.user), GroupMembership
            )
        self.assertRole(self.group, self.scenario.user, Group.Roles.MEMBER)
        self.assertEqual(
            changes,
            [
                auth.GroupAuditLogMemberAdded(
                    user=self.scenario.user.username, role=Group.Roles.MEMBER
                )
            ],
        )

    def test_add_user_role(self) -> None:
        """Add a user to a group with a custom role."""
        self.assertRole(self.group, self.scenario.user, None)
        with self.assertGroupAuditLog() as changes:
            self.group.add_user(self.scenario.user, Group.Roles.ADMIN)
        self.assertRole(self.group, self.scenario.user, Group.Roles.ADMIN)

        self.assertEqual(
            changes,
            [
                auth.GroupAuditLogMemberAdded(
                    user=self.scenario.user.username, role=Group.Roles.ADMIN
                )
            ],
        )

    def test_add_user_already_member(self) -> None:
        """Cannot add a user that is already a member."""
        self.scenario.set_current()
        self.group.add_user(self.scenario.user)
        self.assertRole(self.group, self.scenario.user, Group.Roles.MEMBER)
        with self.assertRaisesRegex(
            ValueError,
            r"playground is already a member of group debusine/group",
        ):
            self.group.add_user(self.scenario.user, Group.Roles.ADMIN)
        self.assertRole(self.group, self.scenario.user, Group.Roles.MEMBER)

    def test_remove_user(self) -> None:
        """Remove a user from a group."""
        self.playground.add_user(self.group, self.scenario.user)
        self.assertRole(self.group, self.scenario.user, Group.Roles.MEMBER)
        with self.assertGroupAuditLog() as changes:
            self.group.remove_user(self.scenario.user)
        self.assertRole(self.group, self.scenario.user, None)
        self.assertEqual(
            changes,
            [auth.GroupAuditLogMemberRemoved(user=self.scenario.user.username)],
        )

    def test_remove_user_not_member(self) -> None:
        """Removing a user not in the group fails."""
        self.assertRole(self.group, self.scenario.user, None)
        with self.assertRaisesRegex(
            ValueError,
            r"User playground is not a member of group debusine/group",
        ):
            self.group.remove_user(self.scenario.user)
        self.assertRole(self.group, self.scenario.user, None)

    def test_add_audit_log_no_user(self) -> None:
        """Ensure add_audit_log fails if there is no user in context."""
        with self.assertRaisesRegex(
            ContextConsistencyError, r"add_audit_log requires a valid user"
        ):
            self.group.add_audit_log(
                auth.GroupAuditLogMemberAdded(
                    user="test", role=Group.Roles.MEMBER
                )
            )

    def test_add_audit_log(self) -> None:
        """Ensure add_audit_log fails if there is no user in context."""
        self.scenario.set_current()
        changes = auth.GroupAuditLogMemberAdded(
            user=self.scenario.user.username, role=Group.Roles.ADMIN
        )
        entry = self.group.add_audit_log(changes.copy())
        assert entry is not None
        changes.actor = self.scenario.user.username
        self.assertEqual(entry.group, self.group)
        self.assertEqual(entry.actor, self.scenario.user)
        self.assertEqual(entry.changes, changes.dict())
        self.assertIsInstance(
            entry.changes_model, auth.GroupAuditLogMemberAdded
        )
        self.assertEqual(entry.changes_model, changes)

    def test_can_display_anonymous(self) -> None:
        """Deny can_display for anonymous users."""
        self.assertPermission(
            "can_display",
            users=AnonymousUser(),
            denied=[self.group, self.other_group],
        )

    def test_can_display_workers(self) -> None:
        """Deny can_display for workers."""
        GroupMembership.objects.create(
            group=self.group, user=self.scenario.user, role=Group.Roles.ADMIN
        )

        self.assertPermission(
            "can_display",
            users=[None],
            denied=[self.group, self.other_group],
            token=self.playground.create_worker_token(),
        )

    def test_can_display(self) -> None:
        """can_display is True for any user."""
        owner_group = self.playground.create_group_role(
            self.scenario.scope, "owner", [self.scenario.user]
        )
        admin = self.playground.create_user("admin")
        self.playground.add_user(self.group, admin, Group.Roles.ADMIN)

        member = self.playground.create_user("member")
        self.playground.add_user(self.group, member)

        other = self.playground.create_user("other")

        self.assertPermission(
            "can_display",
            users=[self.scenario.user, admin, member, other],
            allowed=[self.group, owner_group, self.other_group],
        )

    def test_can_display_audit_log_anonymous(self) -> None:
        """Deny can_display_audit_log for anonymous users."""
        self.assertPermission(
            "can_display_audit_log",
            users=AnonymousUser(),
            denied=[self.group, self.other_group],
        )

    def test_can_display_audit_log_workers(self) -> None:
        """Deny can_display_audit_log for workers."""
        GroupMembership.objects.create(
            group=self.group, user=self.scenario.user, role=Group.Roles.ADMIN
        )

        self.assertPermission(
            "can_display_audit_log",
            users=[None],
            denied=[self.group, self.other_group],
            token=self.playground.create_worker_token(),
        )

    def test_can_display_audit_log(self) -> None:
        """can_display_audit_log is True for any user."""
        owner_group = self.playground.create_group_role(
            self.scenario.scope, "owner", [self.scenario.user]
        )
        admin = self.playground.create_user("admin")
        GroupMembership.objects.create(
            group=self.group, user=admin, role=Group.Roles.ADMIN
        )

        member = self.playground.create_user("member")
        self.group.users.add(member)

        other = self.playground.create_user("other")

        self.assertPermission(
            "can_display_audit_log",
            users=[self.scenario.user, admin, member, other],
            allowed=[self.group, owner_group, self.other_group],
        )

    def test_can_manage_anonymous(self) -> None:
        """Deny can_manage for anonymous users."""
        self.assertPermission(
            "can_manage",
            users=AnonymousUser(),
            denied=[self.group, self.other_group],
        )

    def test_can_manage_workers(self) -> None:
        """Deny can_manage for workers."""
        GroupMembership.objects.create(
            group=self.group, user=self.scenario.user, role=Group.Roles.ADMIN
        )

        self.assertPermission(
            "can_manage",
            users=[None],
            denied=[self.group, self.other_group],
            token=self.playground.create_worker_token(),
        )

    def test_can_manage_scope_owner(self) -> None:
        """Test can_manage for scope owners."""
        owner_group = self.playground.create_group_role(
            self.scenario.scope, "owner", [self.scenario.user]
        )
        self.assertPermission(
            "can_manage",
            users=self.scenario.user,
            allowed=[self.group, owner_group],
            denied=[self.other_group],
        )

    def test_can_manage_group_member(self) -> None:
        """Test can_manage for group members."""
        self.playground.add_user(self.group, self.scenario.user)
        self.assertPermission(
            "can_manage",
            users=(None, AnonymousUser(), self.scenario.user),
            denied=[self.group, self.other_group],
            token=self.playground.create_worker_token(),
        )

    def test_can_manage_group_admin(self) -> None:
        """Test can_manage for group admins."""
        GroupMembership.objects.create(
            group=self.group, user=self.scenario.user, role=Group.Roles.ADMIN
        )
        self.assertPermission(
            "can_manage",
            users=self.scenario.user,
            allowed=self.group,
            denied=self.other_group,
        )


class GroupMembershipTests(TestCase):
    """Test for py:class:`GroupMembership`."""

    scenario = scenarios.DefaultScopeUser()

    membership: ClassVar[GroupMembership]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        group = cls.playground.create_group("group", users=[cls.scenario.user])
        cls.membership = GroupMembership.objects.get(
            user=cls.scenario.user, group=group
        )

    def test_str(self) -> None:
        """Test stringification."""
        self.assertEqual(str(self.membership), "debusine/group:playground")


class GroupAuditLogEntryTests(TestCase):
    """Test GroupAuditLogEntry models."""

    scenario = scenarios.DefaultScopeUser()

    def test_created_create(self) -> None:
        """Test GroupAuditLogCreated.create."""
        group = Group(name="test", scope=self.scenario.scope, ephemeral=True)
        entry = auth.GroupAuditLogCreated.create(group)
        self.assertEqual(
            entry,
            auth.GroupAuditLogCreated(
                name="test",
                scope="debusine",
                ephemeral=True,
            ),
        )

    def test_created_description(self) -> None:
        """Test GroupAuditLogCreated.description."""
        for group, description in (
            (
                Group(name="test", scope=self.scenario.scope, ephemeral=True),
                "actor created ephemeral group test in scope debusine",
            ),
            (
                Group(name="test", scope=self.scenario.scope, ephemeral=False),
                "actor created group test in scope debusine",
            ),
        ):
            with self.subTest(group=group):
                entry = auth.GroupAuditLogCreated.create(group)
                entry.actor = "actor"
                self.assertEqual(
                    entry.description(),
                    description,
                )

    def test_updated_create_diff(self) -> None:
        """Test GroupAuditLogUpdated.create_diff."""
        scope1 = self.playground.get_or_create_scope("scope1")
        group1 = Group(name="test", scope=self.scenario.scope, ephemeral=False)
        group2 = Group(name="test1", scope=scope1, ephemeral=True)
        entry = auth.GroupAuditLogUpdated.create_diff(group1, group2)
        self.assertEqual(
            entry,
            auth.GroupAuditLogUpdated(
                name=("test", "test1"),
                scope=("debusine", "scope1"),
                ephemeral=(False, True),
            ),
        )

    def test_updated_description(self) -> None:
        """Test GroupAuditLogUpdated.description."""
        group = Group(name="test", scope=self.scenario.scope, ephemeral=False)
        scope1 = self.playground.get_or_create_scope("scope1")
        for other_group, expected in (
            (
                Group(name="test1", scope=scope1, ephemeral=True),
                "actor updated name: 'test'→'test1';"
                " scope: 'debusine'→'scope1';"
                " ephemeral: False→True",
            ),
            (
                Group(name="test1", scope=self.scenario.scope, ephemeral=True),
                "actor updated name: 'test'→'test1'; ephemeral: False→True",
            ),
            (
                Group(name="test", scope=self.scenario.scope, ephemeral=True),
                "actor updated ephemeral: False→True",
            ),
            (
                group,
                "actor updated group, no changes detected",
            ),
        ):
            with self.subTest(other_group=other_group):
                entry = auth.GroupAuditLogUpdated.create_diff(
                    group, other_group
                )
                entry.actor = "actor"
                self.assertEqual(entry.description(), expected)

    def test_member_added_description(self) -> None:
        """Test GroupAuditLogMemberAdded.description."""
        entry = auth.GroupAuditLogMemberAdded(
            actor="actor", user="test", role=Group.Roles.ADMIN
        )
        self.assertEqual(entry.description(), "actor added test as admin")

    def test_role_changed_description(self) -> None:
        """Test GroupAuditLogMemberRoleChanged.description."""
        entry = auth.GroupAuditLogMemberRoleChanged(
            actor="actor", user="test", role=Group.Roles.ADMIN
        )
        self.assertEqual(entry.description(), "actor set test's role as admin")

    def test_removed_description(self) -> None:
        """Test GroupAuditLogMemberRemoved.description."""
        entry = auth.GroupAuditLogMemberRemoved(actor="actor", user="test")
        self.assertEqual(entry.description(), "actor removed test")


class ClientEnrollTests(TestCase):
    """Tests for :py:class:`ClientEnroll`."""

    def test_fields(self) -> None:
        """Test basic model fields."""
        ce = ClientEnroll.objects.create(
            nonce="12345678", payload={"foo": "bar"}
        )
        self.assertEqual(ce.nonce, "12345678")
        self.assertEqual(ce.payload, {"foo": "bar"})

    def test_nonce_unique(self) -> None:
        """Nonce must be unique."""
        ClientEnroll.objects.create(nonce="12345678", payload={"foo": "bar"})
        with self.assertRaisesRegex(
            IntegrityError, "db_clientenroll_nonce_key"
        ):
            ClientEnroll.objects.create(
                nonce="12345678", payload={"foo": "baz"}
            )
