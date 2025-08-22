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
from collections.abc import Collection, Generator
from typing import Any, ClassVar, Protocol, Self

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import models

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Group, Scope, Token, User, Workspace
from debusine.db.models.permissions import (
    Allow,
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


class PredicateWrapper(Protocol):
    """Structural typing for check/filter predicate testers."""

    last_user: PermissionUser
    called: bool

    def test(self) -> bool: ...


class PredicateTester:
    """Encapsulate tests for check and filter predicates."""

    def __init__(
        self,
        test_case: TestCase,
        user: PermissionUser,
        tester: PredicateWrapper,
    ) -> None:
        """Test calling tester with user."""
        self.test_case = test_case
        self.user = user
        self.tester = tester

    def __repr__(self) -> str:
        """Representation for debugging, shown by subTest."""
        return f"{self.user=!r}, {self.tester=!r}"

    def assert_forced_false(self) -> None:
        """The predicate failed without calling its body."""
        self.test_case.assertFalse(self.tester.test())
        self.test_case.assertFalse(self.tester.called)

    def assert_forced_true(self) -> None:
        """The predicate succeeded without calling its body."""
        self.test_case.assertTrue(self.tester.test())
        self.test_case.assertFalse(self.tester.called)

    def assert_called(self) -> None:
        """The predicate body has been called with the user passed to it."""
        self.test_case.assertFalse(self.tester.test())
        self.test_case.assertTrue(self.tester.called)
        self.test_case.assertEqual(self.tester.last_user, self.user)

    def assert_passes_with_checks_disabled(self) -> None:
        """The predicate always succeeds with permission checks disabled."""
        with context.disable_permission_checks():
            self.test_case.assertTrue(self.tester.test())
            self.test_case.assertFalse(self.tester.called)


def mock_pred_check(
    test_case: TestCase,
    *,
    user: PermissionUser,
    workers: Allow,
    anonymous: Allow,
) -> PredicateTester:
    """Create a tester for check predicates."""

    class CheckContainer:
        """Mock resource used to test the permission_check decorator."""

        def __init__(self) -> None:
            """Make space to store a trace of checks."""
            self.last_user: PermissionUser = None
            self.called = False

        def __repr__(self) -> str:
            return f"permission_check({workers=!r}, {anonymous=!r}, {user=})"

        # permission_check wants a Model, but it's easier to have a mock class
        # that is not a model to avoid having to deal with Django's model
        # registry. Override the check instead
        @permission_check(  # type: ignore[type-var]
            "{user} cannot pred {resource}",
            workers=workers,
            anonymous=anonymous,
        )
        def _predicate(self, user: PermissionUser) -> bool:
            self.last_user = user
            self.called = True
            return False

        def test(self) -> bool:
            self.called = False
            return self._predicate(user)

    return PredicateTester(test_case, user, CheckContainer())


def mock_pred_filter(
    test_case: TestCase,
    *,
    user: PermissionUser,
    workers: Allow,
    anonymous: Allow,
) -> PredicateTester:
    """Create a tester for filter predicates."""

    class FilterContainer(models.QuerySet[User]):
        """Mock resource used to test the permission_filter decorator."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Make space to store a trace of checks."""
            super().__init__(*args, **kwargs)
            self.last_user: PermissionUser = None
            self.called = False

        def __repr__(self) -> str:
            return f"permission_filter({workers=!r}, {anonymous=!r}, {user=})"

        @permission_filter(workers=workers, anonymous=anonymous)
        def _predicate(self, user: PermissionUser) -> Self:
            self.last_user = user
            self.called = True
            return self.none()

        def test(self) -> bool:
            self.called = False
            return self._predicate(user).count() > 0

    return PredicateTester(test_case, user, FilterContainer(User))


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
        res = mock_pred_check(
            self,
            user=AnonymousUser(),
            workers=Allow.ALWAYS,
            anonymous=Allow.ALWAYS,
        )
        resource = res.tester
        resource_class = resource.__class__
        cls_predicate = resource_class._predicate  # type: ignore[attr-defined]
        self.assertEqual(
            cls_predicate.error_template,
            "{user} cannot pred {resource}",
        )

        self.assertEqual(
            resource._predicate.error_template,  # type: ignore[attr-defined]
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

    def predicate_testers(
        self,
        workers: Collection[Allow],
        anonymous: Collection[Allow],
        users: Collection[PermissionUser],
    ) -> Generator[PredicateTester]:
        for w in workers:
            for a in anonymous:
                for u in users:
                    yield mock_pred_check(self, workers=w, anonymous=a, user=u)
                    yield mock_pred_filter(self, workers=w, anonymous=a, user=u)

    def test_predicates_user_unset_no_worker(self) -> None:
        """Test predicates with neither user nor worker in context."""
        for tester in self.predicate_testers(
            workers=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            anonymous=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            users=[None],
        ):
            with self.subTest(tester=tester):
                with self.assertRaisesRegex(
                    ContextConsistencyError,
                    "user was not set in context",
                ):
                    tester.tester.test()
                self.assertFalse(tester.tester.called)
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_anonymous_always_worker_set(self) -> None:
        """Test predicates with anonymous, allowed always, and worker set."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS, Allow.PASS, Allow.NEVER],
            anonymous=[Allow.ALWAYS],
            users=[AnonymousUser()],
        ):
            with self.subTest(tester=tester):
                tester.assert_forced_true()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_anonymous_pass_worker_set(self) -> None:
        """Test predicates with anonymous, allowed pass, and worker set."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS, Allow.PASS, Allow.NEVER],
            anonymous=[Allow.PASS],
            users=[AnonymousUser()],
        ):
            with self.subTest(tester=tester):
                tester.assert_called()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_anonymous_never_worker_set(self) -> None:
        """Test predicates with both user and worker set."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS, Allow.PASS, Allow.NEVER],
            anonymous=[Allow.NEVER],
            users=[AnonymousUser()],
        ):
            with self.subTest(tester=tester):
                tester.assert_forced_false()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_and_worker_set(self) -> None:
        """Test predicates with both user and worker set."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS, Allow.PASS, Allow.NEVER],
            anonymous=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            users=[self.user],
        ):
            with self.subTest(tester=tester):
                tester.assert_called()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_unset_worker_always(self) -> None:
        """Test predicates with worker, workers=ALWAYS."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS],
            anonymous=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            users=[None],
        ):
            with self.subTest(tester=tester):
                tester.assert_forced_true()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_unset_worker_pass(self) -> None:
        """Test predicates with worker, workers=PASS."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.PASS],
            anonymous=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            users=[None],
        ):
            with self.subTest(tester=tester):
                tester.assert_called()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_unset_worker_never(self) -> None:
        """Test predicates with worker, workers=NEVER."""
        context.set_worker_token(self.worker_token)

        for tester in self.predicate_testers(
            workers=[Allow.NEVER],
            anonymous=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            users=[None],
        ):
            with self.subTest(tester=tester):
                tester.assert_forced_false()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_anonymous_never(self) -> None:
        """Test predicates with anonymous user, anonymous=NEVER."""
        for tester in self.predicate_testers(
            workers=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            anonymous=[Allow.NEVER],
            users=[AnonymousUser()],
        ):
            with self.subTest(tester=tester):
                tester.assert_forced_false()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_anonymous_pass(self) -> None:
        """Test predicates with anonymous user, anonymous=PASS."""
        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS, Allow.PASS, Allow.NEVER],
            anonymous=[Allow.PASS],
            users=[AnonymousUser()],
        ):
            with self.subTest(tester=tester):
                tester.assert_called()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user_anonymous_always(self) -> None:
        """Test predicates with anonymous user, anonymous=ALWAYS."""
        for tester in self.predicate_testers(
            workers=[Allow.ALWAYS, Allow.PASS, Allow.NEVER],
            anonymous=[Allow.ALWAYS],
            users=[AnonymousUser()],
        ):
            with self.subTest(tester=tester):
                tester.assert_forced_true()
                tester.assert_passes_with_checks_disabled()

    def test_predicates_user(self) -> None:
        """Test predicates with explicitly given user."""
        for tester in self.predicate_testers(
            workers=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            anonymous=(Allow.ALWAYS, Allow.PASS, Allow.NEVER),
            users=[self.user],
        ):
            with self.subTest(tester=tester):
                tester.assert_called()
                tester.assert_passes_with_checks_disabled()

    def test_get_resource_scope(self) -> None:
        """Test get_resource_scope."""
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
        workspace = self.playground.get_default_workspace()
        artifact, _ = self.playground.create_artifact(create_files=False)

        self.assertIsNone(get_resource_workspace(self.user))
        self.assertIsNone(get_resource_workspace(self.group))
        self.assertIsNone(get_resource_workspace(self.scope))

        self.assertEqual(get_resource_workspace(workspace), workspace)
        self.assertEqual(get_resource_workspace(artifact), workspace)
