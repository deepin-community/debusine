# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for model permissions."""
import re
from typing import Any, ClassVar

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import models

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Group, Scope, Token, User, Workspace, system_user
from debusine.db.models.permissions import (
    PermissionUser,
    enforce,
    format_permission_check_error,
    get_resource_scope,
    get_resource_workspace,
    permission_check,
    permission_filter,
    resolve_role,
    resolve_roles_list,
)
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    override_permission,
)


@permission_filter()
def perm_filter(
    queryset: models.QuerySet[User],
    user: PermissionUser,
) -> models.QuerySet[User]:
    """Payload for testing the permission_filter decorator."""
    assert user is not None
    if user.is_authenticated:
        return queryset.filter(pk=user.pk)
    else:
        return queryset.none()


class MockResource(models.Model):
    """Mock resource used to test the permission_check decorator."""

    class Meta:
        managed = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Make space to store last user set."""
        super().__init__(*args, **kwargs)
        self.last_user: PermissionUser = None

    @permission_check("{user} cannot pred {resource}")
    def _pred(self, user: PermissionUser) -> bool:  # noqa: U100
        self.last_user = user
        return False


class PermissionsTests(TestCase):
    """Tests for permission infrastructure."""

    scope: ClassVar[Scope]
    scope1: ClassVar[Scope]
    user: ClassVar[User]
    user1: ClassVar[User]
    group: ClassVar[Group]
    worker_token: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_default_scope()
        cls.scope1 = cls.playground.get_or_create_scope("scope")
        cls.user = cls.playground.get_default_user()
        cls.user1 = cls.playground.create_user("user1")
        cls.group = cls.playground.create_group_role(
            cls.scope, Scope.Roles.OWNER
        )
        cls.worker_token = cls.playground.create_worker_token()

    def test_resolve_role(self) -> None:
        """Test passing a valid role type."""
        for val, result in (
            ("owner", Workspace.Roles.OWNER),
            (Workspace.Roles.OWNER, Workspace.Roles.OWNER),
        ):
            with self.subTest(val=val):
                self.assertEqual(resolve_role(Workspace, val), result)

    def test_resolve_role_invalid_type(self) -> None:
        """Test passing an invalid role type."""
        with self.assertRaisesRegex(
            ValueError, r"42 is not a valid ScopeRoles"
        ):
            resolve_role(Scope, 42)  # type: ignore[arg-type]

    def test_resolve_role_invalid_enum(self) -> None:
        """Test passing an invalid role enum."""
        with self.assertRaisesRegex(
            TypeError,
            re.escape(
                f"{Workspace.Roles.OWNER!r} cannot be converted to"
                " <enum 'ScopeRoles'>"
            ),
        ):
            resolve_role(Scope, Workspace.Roles.OWNER)

    def test_resolve_role_invalid_name(self) -> None:
        """Test passing an invalid role name."""
        with self.assertRaisesRegex(
            ValueError, r"'does_not_exist' is not a valid ScopeRoles"
        ):
            resolve_role(Scope, "does_not_exist")

    def test_resolve_roles_list(self) -> None:
        """Test resolve_roles_list function."""
        for roles_cls, arg, expected in (
            (Scope, "owner", [Scope.Roles.OWNER]),
            (Scope, Scope.Roles.OWNER, [Scope.Roles.OWNER]),
            (
                Workspace,
                ["owner", Workspace.Roles.OWNER],
                [Workspace.Roles.OWNER, Workspace.Roles.OWNER],
            ),
        ):
            with self.subTest(roles_cls=roles_cls, arg=arg):
                self.assertEqual(resolve_roles_list(roles_cls, arg), expected)

    def test_permission_error_template(self) -> None:
        """Test accessing the permission error template."""
        self.assertEqual(
            getattr(MockResource._pred, "error_template"),
            "{user} cannot pred {resource}",
        )

        res = MockResource()
        self.assertEqual(
            getattr(res._pred, "error_template"),
            "{user} cannot pred {resource}",
        )

    def test_format_permission_check_error(self) -> None:
        """Test format_permission_check_error."""
        self.assertEqual(
            format_permission_check_error(self.scope.can_display, self.user),
            "playground cannot display scope debusine",
        )

    def test_enforce(self) -> None:
        """Test the enforce method."""
        with override_permission(Scope, "can_display", AllowAll):
            enforce(self.scope.can_display)
        with (
            override_permission(Scope, "can_display", DenyAll),
            self.assertRaisesRegex(
                PermissionDenied, "None cannot display scope debusine"
            ),
        ):
            enforce(self.scope.can_display)

    def test_permission_check_user_unset(self) -> None:
        """Test the permission_check decorator with no user in context."""
        res = MockResource()

        with self.assertRaisesRegex(
            ContextConsistencyError, "user was not set in context"
        ):
            res._pred(context.user)

        with context.disable_permission_checks():
            self.assertTrue(res._pred(context.user))

    def test_permission_check_user_unset_with_token(self) -> None:
        """Test the permission_check decorator with no user but a token."""
        context.set_worker_token(self.worker_token)

        res = MockResource()
        self.assertFalse(res._pred(None))
        # User was defaulted to AnonymousUser()
        self.assertEqual(res.last_user, AnonymousUser())

        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assertFalse(res._pred(user))
                with context.disable_permission_checks():
                    self.assertTrue(res._pred(user))

    def test_permission_check_user_context(self) -> None:
        """Test the permission_check decorator with user from context."""
        res = MockResource()
        context.set_scope(self.scope)
        context.set_user(self.user)
        self.assertFalse(res._pred(context.user))
        with context.disable_permission_checks():
            self.assertTrue(res._pred(context.user))

    def test_permission_check_user_explicit(self) -> None:
        """Test the permission_check decorator with explicitly given user."""
        res = MockResource()
        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assertFalse(res._pred(user))
                with context.disable_permission_checks():
                    self.assertTrue(res._pred(user))

    def test_permission_filter_user_unset(self) -> None:
        """Test permission_filter with but no user in context."""
        # Default user without context raises ContextConsistencyError
        with self.assertRaisesRegex(
            ContextConsistencyError, r"user was not set in context"
        ):
            perm_filter(User.objects.all(), context.user)

        # But it works if permission checks are disabled
        with context.disable_permission_checks():
            self.assertQuerySetEqual(
                perm_filter(User.objects.all(), context.user),
                [system_user(), self.user, self.user1],
                ordered=False,
            )

    def test_permission_filter_user_unset_with_token(self) -> None:
        """Test permission_filter with the no user in context but a token."""
        context.set_worker_token(self.worker_token)

        # User unset defaults to AnonymousUser()
        self.assertQuerySetEqual(perm_filter(User.objects.all(), None), [])

    def test_permission_filter_user_context(self) -> None:
        """Test permission_filter with the context user."""
        context.set_scope(self.scope)
        context.set_user(self.user)

        self.assertQuerySetEqual(
            perm_filter(User.objects.all(), self.user), [self.user]
        )

        # If user is not passed, take it from context
        self.assertQuerySetEqual(
            perm_filter(User.objects.all(), context.user), [self.user]
        )

        # Disabling permission checks works for any user
        with context.disable_permission_checks():
            for user in (AnonymousUser(), self.user, self.user1):
                self.assertQuerySetEqual(
                    perm_filter(User.objects.all(), user),
                    [system_user(), self.user, self.user1],
                    ordered=False,
                )

    def test_permission_filter_user_explicit(self) -> None:
        """Test permission_filter with an explicit user."""
        for user, expected in (
            (self.user, [self.user]),
            (AnonymousUser(), []),
            (self.user1, [self.user1]),
        ):
            with self.subTest(user=user):
                self.assertQuerySetEqual(
                    perm_filter(User.objects.all(), user),
                    expected,
                    ordered=False,
                )

    def test_get_resource_scope(self) -> None:
        """Test get_resource_scope."""
        with context.disable_permission_checks():
            workspace = self.playground.get_default_workspace()
            artifact, _ = self.playground.create_artifact(create_files=False)

        self.assertIsNone(get_resource_scope(self.user))
        self.assertEqual(get_resource_scope(self.group), self.scope)

        self.assertEqual(get_resource_scope(self.scope), self.scope)
        self.assertEqual(get_resource_scope(self.scope1), self.scope1)

        self.assertEqual(get_resource_scope(workspace), self.scope)
        self.assertEqual(get_resource_scope(artifact), self.scope)

    def test_get_resource_workspace(self) -> None:
        """Test get_resource_workspace."""
        with context.disable_permission_checks():
            workspace = self.playground.get_default_workspace()
            artifact, _ = self.playground.create_artifact(create_files=False)

        self.assertIsNone(get_resource_workspace(self.user))
        self.assertIsNone(get_resource_workspace(self.group))
        self.assertIsNone(get_resource_workspace(self.scope))

        self.assertEqual(get_resource_workspace(workspace), workspace)
        self.assertEqual(get_resource_workspace(artifact), workspace)
