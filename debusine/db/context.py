# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Context for database and permission operations.

This is currently based on ContextVar instead of asgiref.local.Local, because
the behaviour of Local is inconsistent across asgiref versions, and probably
buggy in asgiref 3.7+ (see https://github.com/django/asgiref/issues/473).

Ideally, this should eventually aligned with using the same
thread-local/task-local infrastructure that Django uses.

Note that when using threads, if a new thread is started, it will see the
application context reset to empty values: threads that need application
context values need to implement a way to inherit the caller's values.

Note that similar but more complicated gotchas would apply when
`django.urls.reverse` tries to check if `request.urlconf` was previously set to
a non-default value: threads and tasks that need reverse url resolution need to
be mindful of the inconsistencies in behaviour of the underlying
`asgiref.local.Local` implementation, and of asgiref issue #473.

See also https://code.djangoproject.com/ticket/35807
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING, Union

from django.contrib.auth.models import AnonymousUser

if TYPE_CHECKING:
    from debusine.db.models import Scope, Token, User, Workspace
    from debusine.db.models.scopes import ScopeRoles
    from debusine.db.models.workspaces import WorkspaceRoles


class ContextConsistencyError(Exception):
    """Raised if an inconsistency is found when setting application context."""


class Context:
    """
    Storage for Debusine application context.

    Setting the application context needs to be done in a specific order:

    1. ``context.set_scope()``
    2. ``context.set_user()``
    3. ``workspace.set_current()``

    This reflects the order in which information is available in the middleware
    chain, where scope is needed first in order to activate URL resolution,
    which is a prerequisite of the auth layer which will then populate the user.
    """

    # https://github.com/django/asgiref/issues/473

    # Use slots to catch typos in functions that set context variables
    __slots__ = [
        "_permission_checks_disabled",
        "_scope",
        "_scope_roles",
        "_user",
        "_worker_token",
        "_workspace",
        "_workspace_roles",
    ]

    def __init__(self) -> None:
        """Initialize the default values."""
        super().__init__()
        self._permission_checks_disabled: ContextVar[bool] = ContextVar(
            "permission_checks_disabled", default=False
        )

        self._scope: ContextVar[Optional["Scope"]] = ContextVar(
            "scope", default=None
        )

        self._scope_roles: ContextVar[frozenset["ScopeRoles"] | None] = (
            ContextVar("scope_roles", default=None)
        )

        self._user: ContextVar[Union["User", "AnonymousUser", None]] = (
            ContextVar("user", default=None)
        )

        self._worker_token: ContextVar[Optional["Token"]] = ContextVar(
            "worker_token", default=None
        )

        self._workspace: ContextVar[Optional["Workspace"]] = ContextVar(
            "workspace", default=None
        )
        self._workspace_roles: ContextVar[
            frozenset["WorkspaceRoles"] | None
        ] = ContextVar("workspace_roles", default=None)

    @property
    def permission_checks_disabled(self) -> bool:
        """
        Return True if in a management command.

        This is used to skip permission checks in situations like management
        commands or test fixture setup.
        """
        return self._permission_checks_disabled.get()

    @contextmanager
    def disable_permission_checks(self) -> Generator[None, None, None]:
        """Set admin mode for the duration of the context manager."""
        orig = self.permission_checks_disabled
        self._permission_checks_disabled.set(True)
        try:
            yield
        finally:
            self._permission_checks_disabled.set(orig)

    @property
    def scope(self) -> Optional["Scope"]:
        """Get the current scope."""
        return self._scope.get()

    def require_scope(self) -> "Scope":
        """
        Get the current scope.

        :raises ContextConsistencyError: if the scope is not set
        """
        if (scope := self.scope) is None:
            raise ContextConsistencyError("scope is not set in context")
        return scope

    def set_scope(self, new_scope: "Scope") -> None:
        """
        Set the current scope.

        This needs to be called *before* ``set_user``.

        :raises ContextConsistencyError: if the scope had already been set
        """
        if (scope := self.scope) is not None:
            raise ContextConsistencyError(f"Scope was already set to {scope}")

        self._scope.set(new_scope)
        self._user.set(None)
        self._workspace.set(None)

    @property
    def user(self) -> Union["User", "AnonymousUser", None]:
        """
        Get the current user.

        :return: the current user, or None if it has not been initialized yet
                 from the request
        """
        return self._user.get()

    def require_user(self) -> Union["User", "AnonymousUser"]:
        """
        Get the current user.

        :raises ContextConsistencyError: if the user is not set
        """
        if (user := self.user) is None:
            raise ContextConsistencyError("user is not set in context")
        return user

    def set_user(self, new_user: Union["User", "AnonymousUser"]) -> None:
        """
        Set the current user.

        This needs to be set *after* ``set_scope``.
        """
        from debusine.db.models.scopes import ScopeRoles

        if (scope := self.scope) is None:
            raise ContextConsistencyError("Cannot set user before scope")

        if (user := self.user) is not None:
            raise ContextConsistencyError(f"User was already set to {user}")

        # TODO: Scope visibility checks should happen here. Currently scopes
        # are always visible, so the only thing needed is this comment as a
        # placeholder

        self._user.set(new_user)
        self._scope_roles.set(
            ScopeRoles.from_iterable(scope.get_group_roles(new_user))
        )

    @property
    def worker_token(self) -> Optional["Token"]:
        """Get the current worker token."""
        return self._worker_token.get()

    def set_worker_token(self, new_token: "Token") -> None:
        """
        Set the current worker token.

        :raises ContextConsistencyError: if the token had already been set
        """
        if not new_token.enabled:
            raise ContextConsistencyError("Token is disabled")
        if self.worker_token is not None:
            raise ContextConsistencyError("Token was already set")
        self._worker_token.set(new_token)

    @property
    def workspace(self) -> Optional["Workspace"]:
        """Get the current workspace."""
        return self._workspace.get()

    def require_workspace(self) -> "Workspace":
        """
        Get the current workspace.

        :raises ContextConsistencyError: if the workspace is not set
        """
        if (workspace := self.workspace) is None:
            raise ContextConsistencyError("workspace is not set in context")
        return workspace

    @property
    def scope_roles(self) -> frozenset["ScopeRoles"]:
        """Get the roles the current user has on the current Scope."""
        if (roles := self._scope_roles.get()) is not None:
            return roles
        raise ContextConsistencyError("user is not set")

    @property
    def workspace_roles(self) -> frozenset["WorkspaceRoles"]:
        """Get the roles the current user has on the current Workspace."""
        if (roles := self._workspace_roles.get()) is not None:
            return roles
        raise ContextConsistencyError("workspace is not set")

    def reset(self) -> None:
        """Reset the application context to default values."""
        self._user.set(None)
        self._worker_token.set(None)
        self._scope.set(None)
        self._scope_roles.set(None)
        self._workspace.set(None)
        self._workspace_roles.set(None)
        self._permission_checks_disabled.set(False)

    @contextmanager
    def local(self) -> Generator[None, None, None]:
        """Restore application context when the context manager ends."""
        orig_user = self.user
        orig_worker_token = self.worker_token
        orig_scope = self.scope
        orig_scope_roles = self._scope_roles.get()
        orig_workspace = self.workspace
        orig_workspace_roles = self._workspace_roles.get()
        orig_permission_checks_disabled = self.permission_checks_disabled
        try:
            yield
        finally:
            self._user.set(orig_user)
            self._worker_token.set(orig_worker_token)
            self._scope.set(orig_scope)
            self._scope_roles.set(orig_scope_roles)
            self._workspace.set(orig_workspace)
            self._workspace_roles.set(orig_workspace_roles)
            self._permission_checks_disabled.set(
                orig_permission_checks_disabled
            )


context = Context()
