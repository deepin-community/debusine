# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for application context."""

import asyncio
from collections.abc import Callable
from threading import Thread
from typing import Any, ClassVar

from django.db import connection

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Scope, Token, User, Workspace
from debusine.test.django import (
    BaseDjangoTestCase,
    TestCase,
    TransactionTestCase,
)


class TestContextBase(BaseDjangoTestCase):
    """Common base for context tests."""

    def assert_context_initial(self) -> None:
        """Check that the context has initial values."""
        self.assertIsNone(context.worker_token)
        self.assertIsNone(context.user)
        self.assertIsNone(context.scope)
        self.assertIsNone(context._scope_roles.get())
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())
        self.assertFalse(context.permission_checks_disabled)
        with self.assertRaisesRegex(
            ContextConsistencyError, r"scope is not set in context"
        ):
            context.require_scope()
        with self.assertRaisesRegex(
            ContextConsistencyError, r"user is not set in context"
        ):
            context.require_user()
        with self.assertRaisesRegex(
            ContextConsistencyError, r"workspace is not set in context"
        ):
            context.require_workspace()

    def assert_context_all_set(self) -> None:
        """Check that the context has been set to default test values."""
        self.assertEqual(
            context.worker_token,
            self.worker_token,  # type: ignore[attr-defined]
        )
        self.assertEqual(context.user, self.user)  # type: ignore[attr-defined]
        self.assertEqual(
            context.require_user(), self.user  # type: ignore[attr-defined]
        )
        self.assertEqual(
            context.scope, self.scope  # type: ignore[attr-defined]
        )
        self.assertEqual(
            context.require_scope(), self.scope  # type: ignore[attr-defined]
        )
        self.assertIsNotNone(context._scope_roles.get())
        self.assertEqual(
            context.workspace, self.workspace  # type: ignore[attr-defined]
        )
        self.assertIsNotNone(context._workspace_roles.get())
        self.assertEqual(
            context.require_workspace(),
            self.workspace,  # type: ignore[attr-defined]
        )
        self.assertTrue(context.permission_checks_disabled)


class TestAppContext(TestContextBase, TestCase):
    """Test application context variables."""

    worker_token: ClassVar[Token]
    user: ClassVar[User]
    scope: ClassVar[Scope]
    workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_default_scope()
        cls.workspace = cls.playground.get_default_workspace()
        cls.user = cls.playground.get_default_user()
        cls.worker_token = cls.playground.create_worker_token()

    def setUp(self) -> None:
        """Ensure context is not left dirty by a bug in a previous test."""
        super().setUp()
        context.reset()
        self.assertIsNone(context.scope)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context.user)

    def run_in_task(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Run a callable in an asyncio task."""

        async def _task_main() -> Any:
            return func(*args, **kwargs)

        async def _async_main() -> asyncio.Task[Any]:
            return await asyncio.create_task(_task_main())

        return asyncio.run(_async_main())

    def test_defaults(self) -> None:
        """Context variables are None by default."""
        self.assert_context_initial()

    def test_local_previously_none(self) -> None:
        """Using local() restores previously unset vars."""
        with context.local():
            context.set_scope(self.scope)
            context.set_user(self.user)
            context.set_worker_token(self.worker_token)
            self.workspace.set_current()
            context._permission_checks_disabled.set(True)
            self.assert_context_all_set()

        self.assert_context_initial()

    def test_local_previously_set(self) -> None:
        """Using local() restores previously set vars."""
        try:
            context.set_scope(self.scope)
            context.set_user(self.user)
            context.set_worker_token(self.worker_token)
            self.workspace.set_current()
            context._permission_checks_disabled.set(True)

            with context.local():
                context.reset()
                self.assert_context_initial()

            self.assert_context_all_set()
        finally:
            context.reset()

    def test_reset(self) -> None:
        """Calling reset() sets context to its initial values."""
        context.set_scope(self.scope)
        context.set_user(self.user)
        context.set_worker_token(self.worker_token)
        self.workspace.set_current()
        context._permission_checks_disabled.set(True)
        self.assert_context_all_set()

        context.reset()
        self.assert_context_initial()

    def test_visibility_task(self) -> None:
        """Check visibility with asyncio tasks."""
        context.set_scope(self.scope)
        context.set_user(self.user)
        context.set_worker_token(self.worker_token)
        self.workspace.set_current()
        context._permission_checks_disabled.set(True)

        def _test() -> None:
            # Application context is preserved when entering a Task
            self.assert_context_all_set()
            context.reset()
            self.assert_context_initial()

        self.run_in_task(_test)

        # Changes made in another Task are isolated
        self.assert_context_all_set()

    def test_set_scope(self) -> None:
        """Test setting scope."""
        self.assertIsNone(context.scope)
        context.set_scope(self.scope)
        self.assertEqual(context.scope, self.scope)
        self.assertIsNone(context._scope_roles.get())
        self.assertIsNone(context.user)
        self.assertIsNone(context.worker_token)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())
        self.assertFalse(context.permission_checks_disabled)

    def test_cannot_change_scope(self) -> None:
        """Changing scope is not allowed."""
        context.set_scope(self.scope)

        with self.assertRaisesRegex(
            ContextConsistencyError, "Scope was already set to debusine"
        ):
            context.set_scope(Scope(name="scope1"))

        self.assertEqual(context.scope, self.scope)
        self.assertIsNone(context._scope_roles.get())
        self.assertIsNone(context.user)
        self.assertIsNone(context.worker_token)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_set_user_without_scope(self) -> None:
        """Test setting user without scope."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "Cannot set user before scope"
        ):
            context.set_user(self.user)
        self.assertIsNone(context.scope)
        self.assertIsNone(context._scope_roles.get())
        self.assertIsNone(context.user)
        self.assertIsNone(context.worker_token)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_cannot_change_user(self) -> None:
        """Changing user is not allowed."""
        user1 = User.objects.create(username="test1", email="test1@example.org")

        context.set_scope(self.scope)
        context.set_user(self.user)

        with self.assertRaisesRegex(
            ContextConsistencyError, "User was already set to playground"
        ):
            context.set_user(user1)

        self.assertEqual(context.scope, self.scope)
        self.assertIsNotNone(context._scope_roles.get())
        self.assertEqual(context.user, self.user)
        self.assertIsNone(context.worker_token)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_scope_roles_unset(self) -> None:
        """Test scope_roles accessor."""
        with self.assertRaisesRegex(ContextConsistencyError, "user is not set"):
            context.scope_roles

    def test_scope_roles_empty(self) -> None:
        """Test scope_roles accessor with empty roleset."""
        context.set_scope(self.scope)
        context.set_user(self.user)
        self.assertEqual(context.scope_roles, frozenset())

    def test_scope_roles(self) -> None:
        """Test scope_roles accessor."""
        self.playground.create_group_role(
            self.scope, Scope.Roles.OWNER, users=[self.user]
        )

        context.set_scope(self.scope)
        context.set_user(self.user)
        self.assertEqual(context.scope_roles, frozenset((Scope.Roles.OWNER,)))

    def test_workspace_roles_unset(self) -> None:
        """Test workspace_roles accessor."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "workspace is not set"
        ):
            context.workspace_roles

    def test_workspace_roles_empty(self) -> None:
        """Test workspace_roles accessor with empty roleset."""
        context.set_scope(self.scope)
        context.set_user(self.user)
        self.workspace.set_current()
        self.assertEqual(context.workspace_roles, frozenset())

    def test_workspace_roles(self) -> None:
        """Test workspace_roles accessor."""
        self.playground.create_group_role(
            self.workspace, Workspace.Roles.OWNER, users=[self.user]
        )

        context.set_scope(self.scope)
        context.set_user(self.user)
        self.workspace.set_current()
        self.assertEqual(
            context.workspace_roles, frozenset((Scope.Roles.OWNER,))
        )

    def test_set_worker_token(self) -> None:
        """Test set_worker_token."""
        context.set_worker_token(self.worker_token)
        self.assertEqual(context.worker_token, self.worker_token)

    def test_set_worker_token_disabled(self) -> None:
        """Test set_worker_token with a disabled token."""
        self.worker_token.disable()
        with self.assertRaisesRegex(
            ContextConsistencyError, "Token is disabled"
        ):
            context.set_worker_token(self.worker_token)
        self.assert_context_initial()

    def test_cannot_change_worker_token(self) -> None:
        """Changing user is not allowed."""
        token1 = self.playground.create_worker_token()

        context.set_worker_token(self.worker_token)
        with self.assertRaisesRegex(
            ContextConsistencyError, "Token was already set"
        ):
            context.set_worker_token(token1)

        self.assertEqual(context.worker_token, self.worker_token)


class TestThread(TestContextBase, TransactionTestCase):
    """Test thread isolation for application context."""

    worker_token: Token
    user: User
    scope: Scope
    workspace: Workspace

    @context.disable_permission_checks()
    def setUp(self) -> None:
        """Set up common test data."""
        super().setUp()
        self.scope = self.playground.get_default_scope()
        self.workspace = self.playground.get_default_workspace()
        self.user = self.playground.get_default_user()
        self.worker_token = self.playground.create_worker_token()

    def run_in_thread(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Run a callable in a thread."""
        result: Any = None

        def _thread_main() -> None:
            nonlocal result
            try:
                result = func(*args, **kwargs)
            finally:
                connection.close()

        thread = Thread(target=_thread_main)
        thread.start()
        thread.join()

        return result

    def test_visibility_thread(self) -> None:
        """Check visibility with subthreads."""
        with context.disable_permission_checks():
            scope1 = self.playground.get_or_create_scope(name="scope1")
            workspace1 = self.playground.create_workspace(
                scope=scope1, name="test1", public=True
            )
            user1 = self.playground.create_user("test1")
            token1 = self.playground.create_worker_token()

        self.assert_context_initial()
        context.set_scope(self.scope)
        context.set_user(self.user)
        context.set_worker_token(self.worker_token)
        self.workspace.set_current()
        context._permission_checks_disabled.set(True)

        def _test() -> None:
            # Application context is cleared when changing Thread
            self.assert_context_initial()

            context.set_scope(scope1)
            context.set_user(user1)
            context.set_worker_token(token1)
            workspace1.set_current()
            context._permission_checks_disabled.set(True)

            self.assertEqual(context.scope, scope1)
            self.assertIsNotNone(context._scope_roles.get())
            self.assertEqual(context.user, user1)
            self.assertEqual(context.worker_token, token1)
            self.assertEqual(context.workspace, workspace1)
            self.assertIsNotNone(context._workspace_roles.get())
            self.assertTrue(context.permission_checks_disabled)

        self.run_in_thread(_test)

        self.assert_context_all_set()
