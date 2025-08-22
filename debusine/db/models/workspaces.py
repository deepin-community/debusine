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
from collections.abc import Generator, Sequence
from datetime import datetime, timedelta
from typing import (
    Any,
    Generic,
    Self,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    Union,
    cast,
)

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models import (
    BigIntegerField,
    Count,
    Exists,
    F,
    Func,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    UniqueConstraint,
    Value,
)
from django.db.models.expressions import RawSQL
from django.db.models.functions import Greatest
from django.urls import reverse
from django_cte import CTEQuerySet, With

from debusine.artifacts.models import CollectionCategory
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import permissions
from debusine.db.models.files import File
from debusine.db.models.permissions import (
    Allow,
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
    implied_by_public: bool

    def _setup(self) -> None:
        """Set up implications for a newly constructed role."""
        implied_by_scope_roles: set[ScopeRoles] = set()
        implied_by_workspace_roles: set[WorkspaceRoles] = {
            cast(WorkspaceRoles, self)
        }
        implied_by_public: bool = False
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
                case "public":
                    implied_by_public = True
                case _:
                    raise ImproperlyConfigured(
                        "Workspace roles do not support"
                        f" implications by {type(i)}"
                    )
        self.implied_by_scope_roles = frozenset(implied_by_scope_roles)
        self.implied_by_workspace_roles = frozenset(implied_by_workspace_roles)
        self.implied_by_public = implied_by_public

    def q(self, user: PermissionUser) -> Q:
        """Return a Q expression to select workspaces with this role."""
        assert user is not None
        if not user.is_authenticated:
            if self.implied_by_public:
                return Q(public=True)
            else:
                return Q(pk__in=())

        q = Q(
            roles__group__users=user,
            roles__role__in=self.implied_by_workspace_roles,
        )
        if self.implied_by_scope_roles:
            q |= Q(
                scope__in=Scope.objects.filter(
                    roles__group__users=user,
                    roles__role__in=self.implied_by_scope_roles,
                )
            )
        if self.implied_by_public:
            q |= Q(public=True)

        return q

    def implies(self, role: "WorkspaceRoles") -> bool:
        """Check if this role implies the given one."""
        return self in role.implied_by_workspace_roles


class WorkspaceRoles(permissions.Roles, WorkspaceRoleBase, enum.ReprEnum):
    """Available roles for a Scope."""

    OWNER = Role("owner", implied_by=[ScopeRoles.OWNER])
    CONTRIBUTOR = Role("contributor", implied_by=[OWNER])
    VIEWER = Role("viewer", implied_by=[CONTRIBUTOR, "public"])


WorkspaceRoles.setup()


class WorkspaceQuerySet(QuerySet["Workspace", A], Generic[A]):
    """Custom QuerySet for Workspace."""

    def with_expiration_time(self) -> "WorkspaceQuerySet[Any]":
        """Annotate the queryset with expiration times."""
        return self.annotate(
            expiration_time=F("expiration_delay")
            + Greatest("created_at", Max("workrequest__completed_at"))
        )

    def with_role_annotations(self, user: PermissionUser) -> Self:
        """
        Annotate the queryset with a bool for each role, with its presence.

        The returned annotations are in the form ``"is_{role}"`` and are True
        if the user has the given role on the annotated resource.

        A further ``roles_for`` annotation marks the stringified public key of
        the user used to compute annotations, or the empty string if the
        annotations are for the anonymous user.
        """
        queryset = self.annotate(
            roles_for=Value(
                str(user.pk) if user and user.is_authenticated else ""
            )
        )
        for role in WorkspaceRoles:
            queryset = queryset.annotate(
                **{
                    f"is_{role}": Exists(
                        Workspace.objects.filter(pk=OuterRef("pk")).with_role(
                            user, role
                        )
                    )
                }
            )
        return queryset

    def with_role(self, user: PermissionUser, role: WorkspaceRoles) -> Self:
        """Keep only resources where the user has the given role."""
        return self.filter(role.q(user)).distinct()

    def in_current_scope(self) -> Self:
        """Filter to workspaces in the current scope."""
        if context.scope is None:
            raise ContextConsistencyError("scope is not set")
        return self.filter(scope=context.scope)

    @permission_filter(workers=Allow.ALWAYS, anonymous=Allow.PASS)
    def can_display(self, user: PermissionUser) -> Self:
        """Keep only Workspaces that can be displayed."""
        # This is the same check done in Workspace.set_current, and if changed
        # they need to be kept in sync
        return self.with_role(user, Workspace.Roles.VIEWER)

    @permission_filter()
    def can_configure(self, user: PermissionUser) -> Self:
        """Keep only Workspaces that can be configured."""
        return self.with_role(user, Workspace.Roles.OWNER)

    @permission_filter(workers=Allow.ALWAYS)
    def can_create_artifacts(self, user: PermissionUser) -> Self:
        """Keep only Workspaces where the user can create artifacts."""
        return self.with_role(user, Workspace.Roles.CONTRIBUTOR)

    @permission_filter()
    def can_create_work_requests(self, user: PermissionUser) -> Self:
        """Workspaces where the user can create work requests."""
        return self.with_role(user, Workspace.Roles.CONTRIBUTOR)

    @permission_filter()
    def can_create_experiment_workspace(self, user: PermissionUser) -> Self:
        """Workspaces from which the user can create an experiment workspace."""
        return self.with_role(user, Workspace.Roles.CONTRIBUTOR)

    @permission_filter()
    def can_edit_task_configuration(self, user: PermissionUser) -> Self:
        """Workspaces in which the user can edit task configuration."""
        return self.with_role(user, Workspace.Roles.OWNER)

    def get_for_context(self, name: str) -> "Workspace":
        """
        Query a workspace for setting into the current context.

        This defaults to context.scope as the scope, and prefetches roles for
        the current user
        """
        # Note: user permissions are checked by Workspace.set_current
        scope = context.require_scope()

        # Do not annotate with user roles if using a worker token
        if context.worker_token:
            return cast("Workspace", self.get(scope=scope, name=name))

        user = context.require_user()
        queryset = self.select_related("scope")
        if user.is_authenticated:
            queryset = cast(
                "WorkspaceQuerySet[A]",
                queryset.annotate(
                    user_roles=ArrayAgg(
                        "roles__role",
                        filter=Q(roles__group__users=user),
                        distinct=True,
                        default=Value([]),
                    )
                ),
            )
        workspace = queryset.get(scope=scope, name=name)
        assert isinstance(workspace, Workspace)
        return workspace

    def annotate_with_workflow_stats(self) -> "WorkspaceQuerySet[Any]":
        """
        Annotate with workflow statistics.

        * ``running``: number of running workflows
        * ``needs_input``: number of workflows needing input
        * ``completed``: number of completed workflows
        """
        # Prevent circular import loop
        from debusine.db.models.work_requests import WorkRequest

        # Using subqueries so we can use our custom QuerySet filter methods.
        #
        # See https://stackoverflow.com/a/43771738 for an explanation of how
        # the annotations below are constructed.
        return self.annotate(
            running=Subquery(
                WorkRequest.objects.running()
                .workflows()
                .filter(parent__isnull=True)
                .filter(workspace=OuterRef("pk"))
                .values("workspace")
                .annotate(count=Count("pk"))
                .values("count")
            ),
            needs_input=Subquery(
                WorkRequest.objects.needs_input()
                .workflows()
                .filter(parent__isnull=True)
                .filter(workspace=OuterRef("pk"))
                .values("workspace")
                .annotate(count=Count("pk"))
                .values("count")
            ),
            completed=Subquery(
                WorkRequest.objects.filter(
                    status__in=(
                        WorkRequest.Statuses.COMPLETED,
                        WorkRequest.Statuses.ABORTED,
                    )
                )
                .workflows()
                .filter(parent__isnull=True)
                .filter(workspace=OuterRef("pk"))
                .values("workspace")
                .annotate(count=Count("pk"))
                .values("count")
            ),
        )


class WorkspaceManager(models.Manager["Workspace"]):
    """Manager for Workspace model."""

    def get_roles_model(self) -> type["WorkspaceRole"]:
        """Get the model used for role assignment."""
        return WorkspaceRole

    def get_queryset(self) -> WorkspaceQuerySet[Any]:
        """Use the custom QuerySet."""
        return WorkspaceQuerySet(self.model, using=self._db)


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
            Q(parent_collection__workspace__in=workspaces)
            | Q(artifact__workspace__in=workspaces)
            | Q(collection__workspace__in=workspaces)
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
        self.collection_items.delete()
        self.work_requests.delete()
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
            " after which the workspace can be deleted"
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
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.scope.name):
            return reverse("workspaces:detail", kwargs={"wname": self.name})

    def get_absolute_url_configure(self) -> str:
        """Return an absolute URL to configure this workspace."""
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.scope.name):
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

        if context.worker_token:
            context._workspace.set(self)
            context._workspace_roles.set(frozenset())
        else:
            _workspace_roles: set[str] = set()

            if self.public:
                _workspace_roles.add(Workspace.Roles.VIEWER)

            if not user.is_authenticated:
                pass
            elif (user_roles := getattr(self, "user_roles", None)) is not None:
                # Use cached version if available
                _workspace_roles.update(user_roles)
            else:
                _workspace_roles.update(self.get_group_roles(user))

            workspace_roles = Workspace.Roles.from_iterable(_workspace_roles)

            # Check workspace visibility. This is the same as the can_display
            # predicate, and if changed they need to be kept in sync
            expected = Workspace.Roles.VIEWER
            if (
                context.worker_token
                or not expected.implied_by_scope_roles.isdisjoint(
                    context.scope_roles
                )
                or any(a.implies(expected) for a in workspace_roles)
            ):
                context._workspace.set(self)
                context._workspace_roles.set(workspace_roles)
            else:
                raise ContextConsistencyError(
                    f"User {user} cannot access workspace {self}"
                )

    @permission_check(
        "{user} cannot display workspace {resource}",
        workers=Allow.ALWAYS,
        anonymous=Allow.PASS,
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the workspace can be displayed."""
        return self.has_role(user, Workspace.Roles.VIEWER)

    @permission_check(
        "{user} cannot configure workspace {resource}",
    )
    def can_configure(self, user: PermissionUser) -> bool:
        """Check if the workspace can be configured."""
        return self.has_role(user, Workspace.Roles.OWNER)

    @permission_check(
        "{user} cannot create artifacts in {resource}",
        workers=Allow.ALWAYS,
    )
    def can_create_artifacts(self, user: PermissionUser) -> bool:
        """Check if the user can create artifacts."""
        return self.has_role(user, Workspace.Roles.CONTRIBUTOR)

    @permission_check(
        "{user} cannot create work requests in {resource}",
    )
    def can_create_work_requests(self, user: PermissionUser) -> bool:
        """Check if the user can create work requests."""
        # TODO: At some point we may disallow users with only
        # contributor permissions from creating work requests
        # directly rather than via workflows, but that depends on
        # some other things, such as exactly how we implement custom
        # workflows and making it easy for users to create their own
        # workspaces.
        return self.has_role(user, Workspace.Roles.CONTRIBUTOR)

    @permission_check(
        "{user} cannot create an experiment workspace from {resource}",
    )
    def can_create_experiment_workspace(self, user: PermissionUser) -> bool:
        """Check if the user can create an experiment workspace from this."""
        return self.has_role(user, Workspace.Roles.CONTRIBUTOR)

    @permission_check(
        "{user} cannot edit task configuration in {resource}",
    )
    def can_edit_task_configuration(self, user: PermissionUser) -> bool:
        """Check if the user can edit task configuration."""
        return self.has_role(user, Workspace.Roles.OWNER)

    def has_role(self, user: PermissionUser, role: WorkspaceRoles) -> bool:
        """Check if the user has the given role on this Workspace."""
        # Honor implied_by_public immediately as it's independent from the user
        if role.implied_by_public and self.public:
            return True

        if not user or not user.is_authenticated:
            return False

        if context.user == user and context.workspace == self:
            # Allowed as implied by scope roles the user has
            if not context.scope_roles.isdisjoint(role.implied_by_scope_roles):
                return True

            # Allowed as implied by workspace roles the user has
            if any(r.implies(role) for r in context.workspace_roles):
                return True

            # The context has enough information to know that the user does not
            # have the given role on this workspace
            return False
        return (
            Workspace.objects.with_role(user, role).filter(pk=self.pk).exists()
        )

    # See https://github.com/typeddjango/django-stubs/issues/1047 for the typing
    def get_group_roles(
        self, user: PermissionUser
    ) -> QuerySet["WorkspaceRole", str]:
        """Get the roles of the user on this workspace."""
        if not user or not user.is_authenticated:
            return WorkspaceRole.objects.none().values_list("role", flat=True)
        else:
            return (
                self.roles.filter(group__users=user)
                .values_list("role", flat=True)
                .distinct()
            )

    def get_roles(self, user: PermissionUser) -> frozenset[WorkspaceRoles]:
        """
        Get the effective roles of the user on this workspace.

        If you invoke this method on each result of a whole queryset, you
        should annotate the queryset using ``with_role_annotations`` to greatly
        reduce the number of queries.
        """
        roles: list[WorkspaceRoles] = []
        if ((roles_for := getattr(self, "roles_for", None)) is not None) and (
            (
                roles_for
                and user
                and user.is_authenticated
                and int(roles_for) == user.pk
            )
            # with_role_annotations uses the empty string to indicate that the
            # annotations are computed for the anonymous user
            or (roles_for == "" and (not user or not user.is_authenticated))
        ):
            # Use roles from annotations
            for role in WorkspaceRoles:
                if getattr(self, f"is_{role}", False):
                    roles.append(role)
        else:
            # Fall back on querying each role
            for role in WorkspaceRoles:
                if self.has_role(user, role):
                    roles.append(role)
        return WorkspaceRoles.from_iterable(roles)

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
            if workspace == self:
                raise ValueError(
                    "inheritance chain contains the workspace itself"
                )

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
    ) -> "Collection":
        """
        Lookup a collection by category and name.

        If the collection is not found in this workspace, it follows the
        workspace inheritance chain using a depth-first search.

        :param user: user to use for permission checking
        :param category: collection category
        :param name: collection name
        :raises Collection.DoesNotExist: if the collection was not found
        """
        from debusine.db.models import Collection

        visible = With(Workspace.objects.can_display(user), name="visible")

        def chain_cte(cte: With) -> QuerySet[Any]:
            """
            Follow a workspace's inheritance chain.

            The ``path_order`` field allows ordering the results as for a
            depth-first search.
            """
            path_order_field: ArrayField[int, int] = ArrayField(
                IntegerField(), name="path_order"
            )
            path_field: ArrayField[int, int] = ArrayField(
                BigIntegerField(), name="path"
            )
            recursive = (
                visible.join(Workspace, id=visible.col.id)
                .filter(id=self.id)
                .values(
                    chain_child=F("id"),
                    chain_parent=F("id"),
                    path_order=RawSQL(
                        "ARRAY[]::integer[]", (), output_field=path_order_field
                    ),
                    path=RawSQL(
                        "ARRAY[db_workspace.id]", (), output_field=path_field
                    ),
                )
                .union(
                    visible.join(
                        cte.join(WorkspaceChain, child=cte.col.chain_parent),
                        parent_id=visible.col.id,
                    )
                    # Break inheritance loops.
                    .annotate(
                        is_cycle=RawSQL(
                            "chain.path @> ARRAY[db_workspacechain.parent_id]",
                            (),
                        )
                    )
                    .filter(is_cycle=False)
                    .values(
                        chain_child=F("child_id"),
                        chain_parent=F("parent_id"),
                        path_order=Func(
                            cte.col.path_order,
                            F("order"),
                            function="array_append",
                            output_field=path_order_field,
                        ),
                        path=Func(
                            cte.col.path,
                            F("parent_id"),
                            function="array_append",
                            output_field=path_field,
                        ),
                    ),
                    all=True,
                )
            )
            assert isinstance(recursive, QuerySet)
            return recursive

        chain = With.recursive(chain_cte, name="chain")
        collection = (
            chain.join(
                CTEQuerySet(Collection, using=Collection.objects._db),
                workspace=chain.col.chain_parent,
            )
            .with_cte(visible)
            .with_cte(chain)
            .filter(category=category, name=name)
            # Select the first matching collection in depth-first search
            # order.  This raises Collection.DoesNotExist if there is none.
            .earliest(chain.col.path_order)
        )
        assert isinstance(collection, Collection)
        return collection

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

    def list_accessible_collections(
        self,
        user: Union["User", "AnonymousUser"],
        category: CollectionCategory,
        *,
        ws_seen: tuple[int, ...] = (),
        coll_seen: set[int] | None = None,
    ) -> Generator["Collection", None, None]:
        """
        List all collections of a given category accessible from a workspace.

        It recurses up the workspace inheritance chain, breaking loops.

        :param user: user to use for accessibility checks
        :param category: the category of collections to list
        :param ws_seen: IDs of workspaces already visited during recursion, used
          to break loops
        :param coll_seen: IDs of collections already produced, to remove
          duplicates when workspaces appear multiple times in
          the inheritance tree
        """
        if self.id in ws_seen:
            return
        if not self.can_display(user):
            return
        ws_seen = ws_seen + (self.id,)
        if coll_seen is None:
            coll_seen = set()
        for collection in (
            self.collections.filter(category=category)
            .order_by("name")
            .select_related("workspace")
        ):
            if collection.id not in coll_seen:
                yield collection
                coll_seen.add(collection.id)
        for chain in self.chain_parents.select_related("parent").order_by(
            "order"
        ):
            yield from chain.parent.list_accessible_collections(
                user, category, ws_seen=ws_seen, coll_seen=coll_seen
            )

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
