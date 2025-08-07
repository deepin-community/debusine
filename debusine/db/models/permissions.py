# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Basic permission check infrastructure."""

import enum
import functools
from collections.abc import Callable, Collection
from typing import (
    Any,
    NamedTuple,
    Optional,
    Protocol,
    Self,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    Union,
    assert_never,
    cast,
    runtime_checkable,
)

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db.models import Model, Q, QuerySet
from django.utils.functional import classproperty

from debusine.db.context import ContextConsistencyError, context

if TYPE_CHECKING:
    from debusine.db.models import Scope, User, Workspace

ImpliedByCollection: TypeAlias = Collection[Any]


class Role(NamedTuple):
    """Annotated tuple used to define Roles."""

    value: str
    label: str | None = None
    implied_by: Collection[Union["Roles", "Role", Q]] = ()


class RoleBase(str):
    """
    Base for role value implementations.

    This is intended to be subclassed and used as value for a role enum. For
    example::

        class ScopeRoleBase(RoleBase):
            ...

        class ScopeRoles(Roles, ScopeRoleBase, enum.ReprEnum):
            ...
    """

    def __new__(
        cls,
        value: str,
        label: str | None = None,  # noqa: U100
        implied_by: ImpliedByCollection = (),  # noqa: U100
    ) -> Self:
        """Construct the string from value, ignoring other args."""
        return super().__new__(cls, value)

    def __init__(
        self,
        value: str,
        label: str | None = None,
        implied_by: ImpliedByCollection = (),
    ) -> None:
        """
        Set up RoleBase.

        :param value: string value for the role
        :param label: optional label (auto generated from value by default)
        :param implied_by: roles or other elements that can be used to imply
                           this role
        """
        self.label = label or value.capitalize()
        self.implied_by = implied_by

    def _setup(self) -> None:
        """Set up the role after the enum is fully built."""
        pass


# Mixin for role enums
class Roles:
    """Base for declaratively defining available roles for a resource."""

    @classmethod
    def setup(cls) -> None:
        """Finalize enum setup."""
        # Roles is used as a mixin for enum.ReprEnum, which is iterable,
        # but we cannot inherit from it without triggering the enum's setup.
        #
        # Overriding type checks until we have a better idea of how to type
        # this.
        for member in cls:  # type: ignore[attr-defined]
            member._setup()

    @classproperty
    def choices(cls) -> list[tuple[str, str]]:
        """Return labeled choices for use by Django fields."""
        res: list[tuple[str, str]] = []
        for entry in cls:  # type: ignore[attr-defined]
            role = cast("RoleBase", entry)
            res.append((str(role), role.label))
        return res


class PartialCheckResult(enum.StrEnum):
    """
    Result value enum for a partial check predicate.

    This is used by composable check methods, which can either report not
    having enough information to decide, or take an allow/deny decision.
    """

    ALLOW = "allow"
    DENY = "deny"
    PASS = "pass"


#: Type alias for the user variable used by permission predicates
PermissionUser: TypeAlias = Union["User", AnonymousUser, None]

M = TypeVar("M", bound=Model)
QS = TypeVar("QS", bound=QuerySet[Any, Any])


@runtime_checkable
class PermissionCheckPredicate(Protocol[M]):
    """Interface of a permission predicate on a resource."""

    __self__: M

    __func__: Callable[[M, "PermissionUser"], bool]

    def __call__(self, user: "PermissionUser") -> bool:
        """Test the predicate."""


def resolve_role(resource: Model | type[Model], role: str) -> str:
    """Convert a role in various forms into the right enum."""
    roles_enum = getattr(resource, "Roles")

    if isinstance(role, RoleBase):
        if not isinstance(role, roles_enum):
            raise TypeError(f"{role!r} cannot be converted to {roles_enum!r}")
        return cast(str, role)

    return cast(str, roles_enum(role))


def resolve_roles_list(
    resource: Model | type[Model], roles: str | Collection[str]
) -> list[str]:
    """Convert roles into a list of the right enum."""
    if not hasattr(resource, "Roles"):
        return []
    # Roles are string enums, which are instances of str, and therefore also of
    # Collection. This test intends to catch all roles that are not actual
    # collections of roles
    if isinstance(roles, str):
        return [resolve_role(resource, roles)]
    return [resolve_role(resource, r) for r in roles]


class AllowWorkers(enum.StrEnum):
    """Strategies for handling workers in permission predicates."""

    #: Predicate is always true for workers
    ALWAYS = "always"
    #: Predicate is always false for workers
    NEVER = "never"
    #: Predicate ignores the worker token
    PASS = "pass"


P: TypeAlias = Callable[[M, PermissionUser], bool]


def permission_check(
    msg: str, workers: AllowWorkers = AllowWorkers.PASS
) -> Callable[[P[M]], P[M]]:
    """
    Implement common elements of permission checking predicates.

    Predicates should normally also check permissions on any containing
    resources, relying on query caching as needed for performance.  This
    provides some defence in depth against omitted checks.
    """

    def wrap(f: P[M]) -> P[M]:
        @functools.wraps(f)
        def wrapper(self: M, user: PermissionUser) -> bool:
            if context.permission_checks_disabled:
                return True

            # User has not been set in the context: context.user is passed, but
            # it contains None
            if user is None:
                if context.worker_token is None:
                    raise ContextConsistencyError("user was not set in context")
                else:
                    user = AnonymousUser()

            # TODO: see #523
            if context.worker_token:
                match workers:
                    case AllowWorkers.ALWAYS:
                        return True
                    case AllowWorkers.NEVER:
                        return False
                    case AllowWorkers.PASS:
                        pass
                    case _ as unreachable:
                        assert_never(unreachable)

            return f(self, user)

        setattr(wrapper, "error_template", msg)
        return wrapper

    return wrap


PF: TypeAlias = Callable[[QS, PermissionUser], QS]


def permission_filter(
    workers: AllowWorkers = AllowWorkers.PASS,
) -> Callable[[PF[QS]], PF[QS]]:
    """
    Implement common elements of permission filtering predicates.

    Predicates should normally also check permissions on any containing
    resources, relying on query caching as needed for performance.  This
    provides some defence in depth against omitted checks.
    """

    def wrap(f: PF[QS]) -> PF[QS]:
        @functools.wraps(f)
        def wrapper(self: QS, user: PermissionUser) -> QS:
            if context.permission_checks_disabled:
                return self

            # User has not been set in the context: context.user is passed, but
            # it contains None
            if user is None:
                if context.worker_token is None:
                    raise ContextConsistencyError("user was not set in context")
                else:
                    user = AnonymousUser()

            # TODO: see #523
            if context.worker_token:
                match workers:
                    case AllowWorkers.ALWAYS:
                        return self
                    case AllowWorkers.NEVER:
                        return self.none()
                    case AllowWorkers.PASS:
                        pass
                    case _ as unreachable:
                        assert_never(unreachable)

            return f(self, user)

        return wrapper

    return wrap


def format_permission_check_error(
    predicate: Callable[[PermissionUser], bool], user: PermissionUser
) -> str:
    """Format a permission check error message."""
    assert hasattr(predicate, "error_template")
    error_template = predicate.error_template
    assert isinstance(error_template, str)
    assert hasattr(predicate, "__self__")
    return error_template.format(resource=predicate.__self__, user=user)


def enforce(predicate: Callable[[PermissionUser], bool]) -> None:
    """Enforce a permission predicate at the model level."""
    if predicate(context.user):
        return

    raise PermissionDenied(
        format_permission_check_error(predicate, context.user),
    )


def get_resource_scope(obj: Model) -> Optional["Scope"]:
    """
    Get the Scope object for a resource.

    :returns: None if the resource does not have a scope
    :raises NotImplementedError: for resources not currently supported
    """
    from debusine.db.models import ArtifactRelation, Scope, User

    match obj:
        case ArtifactRelation():
            return obj.artifact.workspace.scope
        case Scope():
            return obj
        case User():
            return None
        case _:
            if hasattr(obj, "scope"):
                assert isinstance(obj.scope, Scope)
                return obj.scope

            if hasattr(obj, "workspace"):
                assert isinstance(obj.workspace.scope, Scope)
                return obj.workspace.scope

            raise NotImplementedError(
                f"Cannot get scope for {obj.__class__.__name__} object"
            )


def get_resource_workspace(obj: M) -> Optional["Workspace"]:
    """
    Get the Workspace object for a resource.

    :returns: None if the resource does not have a workspace
    :raises NotImplementedError: for resources not currently supported
    """
    from debusine.db.models import (
        ArtifactRelation,
        Group,
        Scope,
        User,
        Workspace,
    )

    match obj:
        case ArtifactRelation():
            return obj.artifact.workspace
        case Scope() | User() | Group():
            return None
        case Workspace():
            return obj
        case _:
            if hasattr(obj, "workspace"):
                assert isinstance(obj.workspace, Workspace)
                return obj.workspace

            raise NotImplementedError(
                f"Cannot get workspace for {obj.__class__.__name__} object"
            )
