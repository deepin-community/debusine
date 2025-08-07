# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db workspaces."""

import enum
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import (
    Any,
    Generic,
    Self,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    Union,
    assert_never,
    cast,
)

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.postgres.aggregates import ArrayAgg
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models import F, Max, Q, QuerySet, UniqueConstraint, Value
from django.db.models.functions import Greatest
from django.urls import reverse

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import permissions
from debusine.db.models.files import File
from debusine.db.models.permissions import (
    AllowWorkers,
    PartialCheckResult,
    PermissionUser,
    Role,
    enforce,
    permission_check,
    permission_filter,
)
from debusine.db.models.scopes import Scope, ScopeRoles
from debusine.utils.typing_utils import copy_signature_from

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.db.models import Collection, User
else:
    TypedModelMeta = object

A = TypeVar("A")

#: Workspace names reserved for use in toplevel URL path components
RESERVED_WORKSPACE_NAMES = frozenset(
    # TODO: trim after !1274
    (
        "accounts",
        "artifact",
        "task-status",
        "user",
        "workers",
        "work-request",
        "workspaces",
    )
)

#: Regexp matching the structure of workspace names
workspace_name_regex = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*$")


def is_valid_workspace_name(value: str) -> bool:
    """Check if value is a valid workspace name."""
    if value in RESERVED_WORKSPACE_NAMES:
        return False
    return bool(workspace_name_regex.match(value))


def validate_workspace_name(value: str) -> None:
    """Validate workspace names."""
    if not is_valid_workspace_name(value):
        raise ValidationError(
            "%(value)r is not a valid workspace name", params={"value": value}
        )


class WorkspaceRoleBase(permissions.RoleBase):
    """Workspace role implementation."""

    implied_by_scope_roles: frozenset["ScopeRoles"]
    implied_by_workspace_roles: frozenset["WorkspaceRoles"]

    def _setup(self) -> None:
        """Set up implications for a newly constructed role."""
        implied_by_scope_roles: set[ScopeRoles] = set()
        implied_by_workspace_roles: set[WorkspaceRoles] = {
            cast(WorkspaceRoles, self)
        }
        for i in self.implied_by:
            match i:
                case ScopeRoles():
                    implied_by_scope_roles |= i.implied_by_scope_roles
                case Role():
                    # Resolve a role passed during class definition into its
                    # enum instance
                    wr = self.__class__(i.value)
                    implied_by_scope_roles |= wr.implied_by_scope_roles
                    implied_by_workspace_roles |= wr.implied_by_workspace_roles
                case _:
                    raise ImproperlyConfigured(
                        "Workspace roles do not support"
                        f" implications by {type(i)}"
                    )
        self.implied_by_scope_roles = frozenset(implied_by_scope_roles)
        self.implied_by_workspace_roles = frozenset(implied_by_workspace_roles)

    def q(self, user: "User") -> Q:
        """Return a Q expression to select workspaces with this role."""
        q = Q(
            roles__group__users=user,
            roles__role__in=self.implied_by_workspace_roles,
        )
        if self.implied_by_scope_roles:
            q |= Q(
                scope__roles__group__users=user,
                scope__roles__role__in=self.implied_by_scope_roles,
            )
        return q


class WorkspaceRoles(permissions.Roles, WorkspaceRoleBase, enum.ReprEnum):
    """Available roles for a Scope."""

    OWNER = Role("owner", implied_by=[ScopeRoles.OWNER])
    CONTRIBUTOR = Role("contributor", implied_by=[OWNER])


WorkspaceRoles.setup()


class WorkspaceQuerySet(QuerySet["Workspace", A], Generic[A]):
    """Custom QuerySet for Workspace."""

    def with_expiration_time(self) -> "WorkspaceQuerySet[Any]":
        """Annotate the queryset with expiration times."""
        return self.annotate(
            expiration_time=F("expiration_delay")
            + Greatest("created_at", Max("workrequest__completed_at"))
        )

    def with_role(self, user: "User", role: WorkspaceRoles) -> Self:
        """Keep only resources where the user has the given role."""
        return self.filter(role.q(user))

    def in_current_scope(self) -> Self:
        """Filter to workspaces in the current scope."""
        if context.scope is None:
            raise ContextConsistencyError("scope is not set")
        return self.filter(scope=context.scope)

    @permission_filter(workers=AllowWorkers.ALWAYS)
    def can_display(self, user: PermissionUser) -> Self:
        """Keep only Workspaces that can be displayed."""
        assert user is not None  # Enforced by decorator
        constraints = Q(public=True)
        # This is the same check done in Workspace.set_current, and if changed
        # they need to be kept in sync
        if user.is_authenticated:
            constraints |= Workspace.Roles.CONTRIBUTOR.q(user)
        return self.filter(constraints).distinct()

    @permission_filter(workers=AllowWorkers.NEVER)
    def can_configure(self, user: PermissionUser) -> Self:
        """Keep only Workspaces that can be configured."""
        assert user is not None  # Enforced by decorator
        if not user.is_authenticated:
            return self.none()
        return self.filter(Workspace.Roles.OWNER.q(user)).distinct()

    @permission_filter(workers=AllowWorkers.ALWAYS)
    def can_create_artifacts(self, user: PermissionUser) -> Self:
        """Keep only Workspaces where the user can create artifacts."""
        assert user is not None  # Enforced by decorator
        if not user.is_authenticated:
            return self.none()
        # TODO: this allows writing to public workspaces. It reflects the
        # status quo, and it may now be what we want in the long term
        constraints = Q(public=True) | Workspace.Roles.CONTRIBUTOR.q(user)
        return self.filter(constraints).distinct()

    @permission_filter(workers=AllowWorkers.NEVER)
    def can_create_work_requests(self, user: PermissionUser) -> Self:
        """Workspaces where the user can create work requests."""
        assert user is not None  # Enforced by decorator
        # Anonymous users are not allowed
        if not user.is_authenticated:
            return self.none()
        return self.filter(Workspace.Roles.CONTRIBUTOR.q(user)).distinct()

    @permission_filter(workers=AllowWorkers.NEVER)
    def can_create_experiment_workspace(self, user: PermissionUser) -> Self:
        """Workspaces from which the user can create an experiment workspace."""
        assert user is not None  # Enforced by decorator
        # Anonymous users are not allowed
        if not user.is_authenticated:
            return self.none()
        constraints = Q(public=True) | Workspace.Roles.CONTRIBUTOR.q(user)
        return self.filter(constraints).distinct()


class WorkspaceManager(models.Manager["Workspace"]):
    """Manager for Workspace model."""

    def get_roles_model(self) -> type["WorkspaceRole"]:
        """Get the model used for role assignment."""
        return WorkspaceRole

    def get_queryset(self) -> WorkspaceQuerySet[Any]:
        """Use the custom QuerySet."""
        return WorkspaceQuerySet(self.model, using=self._db)

    def get_for_context(self, name: str) -> "Workspace":
        """
        Query a workspace for setting into the current context.

        This defaults to context.scope as the scope, and prefetches roles for
        the current user
        """
        # Note: user permissions are checked by Workspace.set_current
        scope = context.require_scope()
        user = context.require_user()
        queryset = self.get_queryset().select_related("scope")
        if user.is_authenticated:
            queryset = queryset.annotate(
                user_roles=ArrayAgg(
                    "roles__role",
                    filter=Q(roles__group__users=user),
                    distinct=True,
                    default=Value([]),
                )
            )
        workspace = queryset.get(scope=scope, name=name)
        assert isinstance(workspace, Workspace)
        return workspace


class DeleteWorkspaces:
    """Delete all workspaces in a queryset."""

    def __init__(self, workspaces: WorkspaceQuerySet["Workspace"]) -> None:
        """Store workspaces to delete and compute affected objects."""
        # Import here to prevent circular dependencies
        from debusine.db import models as dmodels

        self.workspaces = workspaces

        # Since we use on_delete=PROTECT on most models, there may be
        # elements in the model interdependency graphs that we are not
        # deleting yet.
        #
        # It is difficult to test this without having infrastructure to
        # simulate a fully populated database that is kept up to date as
        # new models get added, so for the moment we limit ourselves to
        # adding to this as the need arises.
        self.workflow_templates = dmodels.WorkflowTemplate.objects.filter(
            workspace__in=workspaces
        )
        self.work_requests = dmodels.WorkRequest.objects.filter(
            workspace__in=workspaces
        )
        self.collection_items = dmodels.CollectionItem.objects.filter(
            parent_collection__workspace__in=workspaces
        )
        self.collections = dmodels.Collection.objects.filter(
            workspace__in=workspaces
        )
        self.file_in_artifacts = dmodels.FileInArtifact.objects.filter(
            artifact__workspace__in=workspaces
        )
        self.artifact_relations = dmodels.ArtifactRelation.objects.filter(
            Q(artifact__workspace__in=workspaces)
            | Q(target__workspace__in=workspaces)
        )
        self.artifacts = dmodels.Artifact.objects.filter(
            workspace__in=workspaces
        )
        self.workspace_roles = WorkspaceRole.objects.filter(
            resource__in=workspaces
        )
        self.asset_usages = dmodels.AssetUsage.objects.filter(
            workspace__in=workspaces
        )
        self.assets = dmodels.Asset.objects.filter(workspace__in=workspaces)

    def perform_deletions(self) -> None:
        """Delete the workspaces and all their resources."""
        self.workflow_templates.delete()
        self.work_requests.delete()
        self.collection_items.delete()
        self.collections.delete()
        self.file_in_artifacts.delete()
        self.artifact_relations.delete()
        self.artifacts.delete()
        self.asset_usages.delete()
        self.assets.delete()
        self.workspace_roles.delete()
        self.workspaces.delete()


def default_workspace() -> "Workspace":
    """Return the default Workspace."""
    return Workspace.objects.get(
        scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
        name=settings.DEBUSINE_DEFAULT_WORKSPACE,
    )


class Workspace(models.Model):
    """Workspace model."""

    Roles: TypeAlias = WorkspaceRoles

    objects = WorkspaceManager.from_queryset(WorkspaceQuerySet)()

    name = models.CharField(
        max_length=255, validators=[validate_workspace_name]
    )
    public = models.BooleanField(default=False)
    default_expiration_delay = models.DurationField(
        default=timedelta(0),
        help_text="minimal time that a new artifact is kept in the"
        " workspace before being expired",
    )

    inherits = models.ManyToManyField(
        "db.Workspace",
        through="db.WorkspaceChain",
        through_fields=("child", "parent"),
        related_name="inherited_by",
    )

    scope = models.ForeignKey(
        Scope, on_delete=models.PROTECT, related_name="workspaces"
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="time the workspace was created"
    )
    expiration_delay = models.DurationField(
        blank=True,
        null=True,
        help_text=(
            "if set, time since the last task completion time"
            "after which the workspace can be deleted"
        ),
    )

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["scope", "name"],
                name="%(app_label)s_%(class)s_unique_scope_name",
            ),
        ]

    def get_absolute_url(self) -> str:
        """Return an absolute URL to this workspace."""
        return reverse("workspaces:detail", kwargs={"wname": self.name})

    def get_absolute_url_configure(self) -> str:
        """Return an absolute URL to configure this workspace."""
        return reverse("workspaces:update", kwargs={"wname": self.name})

    @copy_signature_from(models.Model.save)
    def save(self, **kwargs: Any) -> None:
        """Wrap save with permission checks."""
        if self._state.adding:
            # Create
            enforce(self.scope.can_create_workspace)
        else:
            # Update
            ...  # TODO: check for update permissions

        return super().save(**kwargs)

    def set_current(self) -> None:
        """
        Set this as the current workspace.

        This needs to be called after ``context.set_scope`` and
        ``context.set_user``.
        """
        if (old_workspace := context.workspace) is not None:
            raise ContextConsistencyError(
                f"Workspace was already set to {old_workspace}"
            )

        if (scope := context.scope) is None:
            raise ContextConsistencyError("Cannot set workspace before scope")

        if self.scope != scope:
            raise ContextConsistencyError(
                f"workspace scope {self.scope.name!r}"
                f" does not match current scope {scope.name!r}"
            )

        if (user := context.user) is None:
            if context.worker_token:
                user = AnonymousUser()
            else:
                raise ContextConsistencyError(
                    "Cannot set workspace before user"
                )

        workspace_roles: frozenset[Workspace.Roles]
        if not user.is_authenticated and context.worker_token:
            workspace_roles = frozenset()
        elif (user_roles := getattr(self, "user_roles", None)) is not None:
            # Use cached version if available
            workspace_roles = frozenset(user_roles)
        else:
            workspace_roles = frozenset(self.get_roles(user))

        # Check workspace visibility. This is the same as the can_display
        # predicate, and if changed they need to be kept in sync
        expected = Workspace.Roles.CONTRIBUTOR
        if (
            self.public
            or context.worker_token
            or not expected.implied_by_scope_roles.isdisjoint(
                context.scope_roles
            )
            or not expected.implied_by_workspace_roles.isdisjoint(
                workspace_roles
            )
        ):
            context._workspace.set(self)
            context._workspace_roles.set(workspace_roles)
        else:
            raise ContextConsistencyError(
                f"User {user} cannot access workspace {self}"
            )

    @permission_check(
        "{user} cannot display workspace {resource}",
        workers=AllowWorkers.ALWAYS,
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the workspace can be displayed."""
        assert user is not None  # enforced by decorator
        # Shortcuts to avoid hitting the database for common cases
        if self.public:
            return True
        if not user.is_authenticated:
            return False
        # Shortcut to avoid hitting the database for common cases
        match self.context_has_role(user, Workspace.Roles.CONTRIBUTOR):
            case PartialCheckResult.ALLOW:
                return True
            case PartialCheckResult.DENY:
                return False
            case PartialCheckResult.PASS:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        return Workspace.objects.can_display(user).filter(pk=self.pk).exists()

    @permission_check(
        "{user} cannot configure workspace {resource}",
        workers=AllowWorkers.NEVER,
    )
    def can_configure(self, user: PermissionUser) -> bool:
        """Check if the workspace can be configured."""
        assert user is not None  # enforced by decorator
        if not user.is_authenticated:
            return False
        # Shortcut to avoid hitting the database for common cases
        match self.context_has_role(user, Workspace.Roles.OWNER):
            case PartialCheckResult.ALLOW:
                return True
            case PartialCheckResult.DENY:
                return False
            case PartialCheckResult.PASS:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        return Workspace.objects.can_configure(user).filter(pk=self.pk).exists()

    @permission_check(
        "{user} cannot create artifacts in {resource}",
        workers=AllowWorkers.ALWAYS,
    )
    def can_create_artifacts(self, user: PermissionUser) -> bool:
        """Check if the user can create artifacts."""
        assert user is not None  # enforced by decorator
        # Anonymous users cannot create artifacts
        if not user.is_authenticated:
            return False
        # Shortcuts to avoid hitting the database for common cases
        if self.public:
            # TODO: this allows writing to public workspaces. It reflects the
            # status quo, and it may now be what we want in the long term
            return True
        match self.context_has_role(user, Workspace.Roles.CONTRIBUTOR):
            case PartialCheckResult.ALLOW:
                return True
            case PartialCheckResult.DENY:
                return False
            case PartialCheckResult.PASS:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        return (
            Workspace.objects.can_create_artifacts(user)
            .filter(pk=self.pk)
            .exists()
        )

    @permission_check(
        "{user} cannot create work requests in {resource}",
        workers=AllowWorkers.NEVER,
    )
    def can_create_work_requests(self, user: PermissionUser) -> bool:
        """Check if the user can create work requests."""
        # TODO: At some point we may disallow users with only
        # contributor permissions from creating work requests
        # directly rather than via workflows, but that depends on
        # some other things, such as exactly how we implement custom
        # workflows and making it easy for users to create their own
        # workspaces.
        assert user is not None  # enforced by decorator
        # Anonymous users are not allowed
        if not user.is_authenticated:
            return False
        # Shortcut to avoid hitting the database for common cases
        match self.context_has_role(user, Workspace.Roles.CONTRIBUTOR):
            case PartialCheckResult.ALLOW:
                return True
            case PartialCheckResult.DENY:
                return False
            case PartialCheckResult.PASS:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        return (
            Workspace.objects.can_create_work_requests(user)
            .filter(pk=self.pk)
            .exists()
        )

    @permission_check(
        "{user} cannot create an experiment workspace from {resource}",
        workers=AllowWorkers.NEVER,
    )
    def can_create_experiment_workspace(self, user: PermissionUser) -> bool:
        """Check if the user can create an experiment workspace from this."""
        assert user is not None  # enforced by decorator
        # Anonymous cannot create experiment workspaces
        if not user.is_authenticated:
            return False
        # Shortcut to avoid hitting the database for common cases
        if self.public:
            return True
        match self.context_has_role(user, Workspace.Roles.CONTRIBUTOR):
            case PartialCheckResult.ALLOW:
                return True
            case PartialCheckResult.DENY:
                return False
            case PartialCheckResult.PASS:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        return (
            Workspace.objects.can_create_experiment_workspace(user)
            .filter(pk=self.pk)
            .exists()
        )

    def context_has_role(
        self, user: "User", role: WorkspaceRoles
    ) -> PartialCheckResult:
        """
        Check the context to see if the user has a role on this Workspace.

        :returns:
          * ALLOW if the context has enough information to determine that the
            user has the given role
          * DENY if the context has enough information to determine that the
            user does not have the given role
          * PASS if the context does not have enough information to decide
        """
        if (
            context.user != user
            or context.scope != self.scope
            or context.workspace != self
        ):
            return PartialCheckResult.PASS

        # Allowed as implied by scope roles the user has
        if not role.implied_by_scope_roles.isdisjoint(context.scope_roles):
            return PartialCheckResult.ALLOW

        # Allowed as implied by workspace roles the user has
        if not role.implied_by_workspace_roles.isdisjoint(
            context.workspace_roles
        ):
            return PartialCheckResult.ALLOW

        # The context has enough information to know that the user does not
        # have the given role on this workspace
        return PartialCheckResult.DENY

    # See https://github.com/typeddjango/django-stubs/issues/1047 for the typing
    def get_roles(
        self, user: Union["User", "AnonymousUser"]
    ) -> QuerySet["WorkspaceRole", "WorkspaceRoles"]:
        """Get the roles of the user on this workspace."""
        if not user.is_authenticated:
            result = WorkspaceRole.objects.none().values_list("role", flat=True)
        else:
            result = (
                self.roles.filter(group__users=user)
                .values_list("role", flat=True)
                .distinct()
            )
        return cast(QuerySet["WorkspaceRole", "WorkspaceRoles"], result)

    @property
    def expire_at(self) -> datetime | None:
        """Return computed expiration date."""
        if self.expiration_delay is None:
            return None

        # Try to reuse a value precomputed by
        # WorkspaceQuerySet.with_expiration_time()
        # otherwise, compute it and set it

        if not hasattr(self, "expiration_time"):
            query = Workspace.objects.with_expiration_time().filter(pk=self.pk)
            # expiration_time is introduced by with_expiration_time but it's
            # currently hard to explain to the type system
            (expiration_time,) = query.values_list(
                "expiration_time", flat=True
            )  # type: ignore[misc]
            setattr(self, "expiration_time", expiration_time)

        return cast(
            datetime, self.expiration_time  # type: ignore[attr-defined]
        )

    def file_needs_upload(self, fileobj: File) -> bool:
        """
        Return True if fileobj needs to be uploaded to this workspace.

        Before an artifact can be considered complete, all its files must be
        part of the artifact's workspace.  This requires each file to be in
        one of its stores, and also to be in some artifact in the workspace.
        Otherwise, the file must be uploaded, even if it already exists
        somewhere else in debusine; this prevents users obtaining
        unauthorized access to existing file contents.
        """
        from debusine.db.models import FileInArtifact

        if not self.scope.download_file_stores(fileobj).exists():
            return True

        if not FileInArtifact.objects.filter(
            artifact__workspace=self, file=fileobj, complete=True
        ).exists():
            return True

        return False

    def set_inheritance(self, chain: Sequence["Workspace"]) -> None:
        """Set the inheritance chain for this workspace."""
        # Check for duplicates in the chain before altering the database
        seen: set[int] = set()
        for workspace in chain:
            if workspace.pk in seen:
                raise ValueError(
                    f"duplicate workspace {workspace.name!r}"
                    " in inheritance chain"
                )
            seen.add(workspace.pk)

        WorkspaceChain.objects.filter(child=self).delete()
        for idx, workspace in enumerate(chain):
            WorkspaceChain.objects.create(
                child=self, parent=workspace, order=idx
            )

    def get_collection(
        self,
        *,
        # TODO: allow user to be None to mean take it from context?
        user: Union["User", "AnonymousUser"],
        category: str,
        name: str,
        visited: set[int] | None = None,
    ) -> "Collection":
        """
        Lookup a collection by category and name.

        If the collection is not found in this workspace, it follows the
        workspace inheritance chain using a depth-first search.

        :param user: user to use for permission checking
        :param category: collection category
        :param name: collection name
        :param visited: for internal use only: state used during graph
                        traversal
        :raises Collection.DoesNotExist: if the collection was not found
        """
        from debusine.db.models import Collection

        # Ensure that the user can access this workspace
        if not self.can_display(user):
            raise Collection.DoesNotExist

        # Lookup in this workspace
        try:
            return Collection.objects.get(
                workspace=self, category=category, name=name
            )
        except Collection.DoesNotExist:
            pass

        if visited is None:
            visited = set()

        visited.add(self.pk)

        # Follow the inheritance chain
        for node in self.chain_parents.order_by("order").select_related(
            "parent"
        ):
            workspace = node.parent
            # Break inheritance loops
            if workspace.pk in visited:
                continue
            try:
                return workspace.get_collection(
                    user=user, category=category, name=name, visited=visited
                )
            except Collection.DoesNotExist:
                pass

        raise Collection.DoesNotExist

    def get_singleton_collection(
        self, *, user: Union["User", "AnonymousUser"], category: str
    ) -> "Collection":
        """
        Lookup a singleton collection by category.

        If the collection is not found in this workspace, it follows the
        workspace inheritance chain using a depth-first search.

        :param user: user to use for permission checking
        :param category: collection category
        :raises Collection.DoesNotExist: if the collection was not found
        """
        return self.get_collection(user=user, category=category, name="_")

    def __str__(self) -> str:
        """Return basic information of Workspace."""
        return f"{self.scope.name}/{self.name}"


class WorkspaceChain(models.Model):
    """Workspace chaining model."""

    child = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="chain_parents",
        help_text="Workspace that falls back on `parent` for lookups",
    )
    parent = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="chain_children",
        help_text="Workspace to be looked up if lookup in `child` fails",
    )
    order = models.IntegerField(
        help_text="Lookup order of this element in the chain",
    )

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["child", "parent"],
                name="%(app_label)s_%(class)s_unique_child_parent",
            ),
            UniqueConstraint(
                fields=["child", "order"],
                name="%(app_label)s_%(class)s_unique_child_order",
            ),
        ]

    def __str__(self) -> str:
        """Return basic information of Workspace."""
        return f"{self.order}:{self.child.name}→{self.parent.name}"


class WorkspaceRole(models.Model):
    """Role assignments for workspaces."""

    Roles: TypeAlias = WorkspaceRoles

    resource = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="roles",
    )

    group = models.ForeignKey(
        "Group",
        on_delete=models.CASCADE,
        related_name="workspace_roles",
    )

    role = models.CharField(max_length=16, choices=Roles.choices)

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["resource", "group", "role"],
                name="%(app_label)s_%(class)s_unique_resource_group_role",
            ),
        ]

    def __str__(self) -> str:
        """Return a description of the role assignment."""
        return f"{self.group}─{self.role}⟶{self.resource}"
