# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db scopes."""

import enum
import re
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

import pgtrigger
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models import CheckConstraint, F, Q, QuerySet, UniqueConstraint

from debusine.db.context import context
from debusine.db.models import permissions
from debusine.db.models.files import File, FileStore
from debusine.db.models.permissions import (
    AllowWorkers,
    PartialCheckResult,
    PermissionUser,
    permission_check,
    permission_filter,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.db.models.auth import User
    from debusine.server.file_backend.interface import FileBackendInterface
else:
    TypedModelMeta = object

#: Name of the fallback scope used for transitioning to scoped models
FALLBACK_SCOPE_NAME = "debusine"

#: Scope names reserved for use in toplevel URL path components
RESERVED_SCOPE_NAMES = frozenset(
    (
        "accounts",
        "admin",
        "api",
        "api-auth",
        "artifact",
        "task-status",
        "user",
        "workers",
        "work-request",
        "workspace",
    )
)

#: Regexp matching the structure of scope names
scope_name_regex = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*$")


def is_valid_scope_name(value: str) -> bool:
    """Check if value is a valid scope name."""
    if value in RESERVED_SCOPE_NAMES:
        return False
    return bool(scope_name_regex.match(value))


def validate_scope_name(value: str) -> None:
    """Validate scope names."""
    if not is_valid_scope_name(value):
        raise ValidationError(
            "%(value)r is not a valid scope name", params={"value": value}
        )


A = TypeVar("A")


class ScopeRoleBase(permissions.RoleBase):
    """Scope role implementation."""

    implied_by_scope_roles: frozenset["ScopeRoles"]

    def _setup(self) -> None:
        """Set up implications for a newly constructed role."""
        for i in self.implied_by:
            raise ImproperlyConfigured(
                f"Scope roles do not support implications by {type(i)}"
            )
        self.implied_by_scope_roles = frozenset((cast(ScopeRoles, self),))

    def q(self, user: "User") -> Q:
        """Return a Q expression to select scopes with this role."""
        return Q(
            roles__group__users=user,
            roles__role__in=self.implied_by_scope_roles,
        )


class ScopeRoles(permissions.Roles, ScopeRoleBase, enum.ReprEnum):
    """Available roles for a Scope."""

    OWNER = "owner"


ScopeRoles.setup()


class ScopeQuerySet(QuerySet["Scope", A], Generic[A]):
    """Custom QuerySet for Scope."""

    def with_role(self, user: "User", role: ScopeRoles) -> Self:
        """Keep only resources where the user has the given role."""
        return self.filter(role.q(user))

    @permission_filter(workers=AllowWorkers.ALWAYS)
    def can_display(self, user: PermissionUser) -> Self:  # noqa: U100
        """Keep only Scopes that can be displayed."""
        assert user is not None  # Enforced by decorator
        return self

    @permission_filter(workers=AllowWorkers.NEVER)
    def can_create_workspace(self, user: PermissionUser) -> Self:
        """Keep only Scopes where the user can create workspaces."""
        assert user is not None  # Enforced by decorator
        if not user.is_authenticated:
            return self.none()
        return self.with_role(user, Scope.Roles.OWNER)


class ScopeManager(models.Manager["Scope"]):
    """Manager for Scope model."""

    def get_roles_model(self) -> type["ScopeRole"]:
        """Get the model used for role assignment."""
        return ScopeRole

    def get_queryset(self) -> ScopeQuerySet[Any]:
        """Use the custom QuerySet."""
        return ScopeQuerySet(self.model, using=self._db)


class Scope(models.Model):
    """
    Scope model.

    This is used to create different distinct sets of groups and workspaces
    """

    Roles: TypeAlias = ScopeRoles

    objects = ScopeManager.from_queryset(ScopeQuerySet)()

    name = models.CharField(
        max_length=255,
        unique=True,
        validators=[validate_scope_name],
        help_text="internal name for the scope",
    )
    label = models.CharField(
        max_length=255,
        unique=True,
        help_text="User-visible name for the scope",
    )
    icon = models.CharField(
        max_length=255,
        default="",
        blank=True,
        help_text=(
            "Optional user-visible icon,"
            " resolved via ``{% static %}`` in templates"
        ),
    )
    file_stores = models.ManyToManyField(
        FileStore, related_name="scopes", through="db.FileStoreInScope"
    )

    def __str__(self) -> str:
        """Return basic information of Scope."""
        return self.name

    @permission_check(
        "{user} cannot display scope {resource}",
        workers=AllowWorkers.ALWAYS,
    )
    def can_display(self, user: PermissionUser) -> bool:  # noqa: U100
        """Check if the scope can be displayed."""
        assert user is not None  # enforced by decorator
        return True

    @permission_check(
        "{user} cannot create workspaces in {resource}",
        workers=AllowWorkers.NEVER,
    )
    def can_create_workspace(self, user: PermissionUser) -> bool:
        """Check if the user can create workspaces in this scope."""
        assert user is not None  # enforced by decorator
        # Token is not taken into account here
        if not user.is_authenticated:
            return False
        # Shortcut to avoid hitting the database for common cases
        match self.context_has_role(user, Scope.Roles.OWNER):
            case PartialCheckResult.ALLOW:
                return True
            case PartialCheckResult.DENY:
                return False
            case PartialCheckResult.PASS:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        return (
            Scope.objects.can_create_workspace(user).filter(pk=self.pk).exists()
        )

    def context_has_role(
        self, user: "User", role: ScopeRoles
    ) -> PartialCheckResult:
        """
        Check the context to see if the user has a role on this Scope.

        :returns:
          * ALLOW if the context has enough information to determine that the
            user has at least one of the given roles
          * DENY if the context has enough information to determine that the
            user does not have any of the given roles
          * PASS if the context does not have enough information to decide
        """
        if context.user != user or context.scope != self:
            return PartialCheckResult.PASS
        if role.implied_by_scope_roles.isdisjoint(context.scope_roles):
            return PartialCheckResult.DENY
        else:
            return PartialCheckResult.ALLOW

    # See https://github.com/typeddjango/django-stubs/issues/1047 for the typing
    def get_roles(
        self, user: Union["User", "AnonymousUser"]
    ) -> QuerySet["ScopeRole", "ScopeRoles"]:
        """Get the roles of the user on this scope."""
        if not user.is_authenticated:
            result = ScopeRole.objects.none().values_list("role", flat=True)
        else:
            result = (
                ScopeRole.objects.filter(resource=self, group__users=user)
                .values_list("role", flat=True)
                .distinct()
            )
        # QuerySet sees a CharField, but we know it's a ScopeRoles enum
        return cast(QuerySet["ScopeRole", "ScopeRoles"], result)

    def upload_file_stores(
        self,
        fileobj: File,
        *,
        enforce_soft_limits: bool = False,
        include_write_only: bool = False,
    ) -> QuerySet[FileStore]:
        """
        Find the file stores in this scope where `fileobj` can be uploaded.

        The returned query set is in descending order of upload priority.

        :param enforce_soft_limits: Enforce `soft_max_size` policies as well
          as `max_size`.
        :param include_write_only: Uploading a file to a write-only store
          will mean that debusine won't download it from there again, so
          that only makes sense in specialized situations such as populating
          a backup store.  Set this to True to include write-only stores in
          the returned query set.
        """
        file_stores = (
            self.file_stores.exclude(filestoreinscope__read_only=True)
            .exclude(max_size__lt=F("total_size") + fileobj.size)
            .order_by(
                F("filestoreinscope__upload_priority").desc(nulls_last=True)
            )
        )
        if enforce_soft_limits:
            # TODO: We should also enforce FileStoreInScope.soft_max_size.
            file_stores = file_stores.exclude(
                soft_max_size__lt=F("total_size") + fileobj.size
            )
        if not include_write_only:
            file_stores = file_stores.exclude(filestoreinscope__write_only=True)
        return file_stores

    def upload_file_backend(self, fileobj: File) -> "FileBackendInterface[Any]":
        """
        Find the best file backend for uploading `fileobj`.

        :raises IndexError: if there is no such file backend.
        """
        return self.upload_file_stores(fileobj)[0].get_backend_object()

    def download_file_stores(self, fileobj: File) -> QuerySet[FileStore]:
        """
        Find the file stores in this scope that have `fileobj`, if any.

        The returned query set is in descending order of download priority,
        breaking ties in descending order of upload priority.
        """
        return (
            self.file_stores.exclude(filestoreinscope__write_only=True)
            .filter(files=fileobj)
            .order_by(
                F("filestoreinscope__download_priority").desc(nulls_last=True),
                F("filestoreinscope__upload_priority").desc(nulls_last=True),
            )
        )

    def download_file_backend(
        self, fileobj: File
    ) -> "FileBackendInterface[Any]":
        """
        Find the best file backend for downloading `fileobj`.

        :raises IndexError: if there is no such file backend.
        """
        return self.download_file_stores(fileobj)[0].get_backend_object()


class ScopeRole(models.Model):
    """Role assignments for scopes."""

    Roles: TypeAlias = ScopeRoles

    resource = models.ForeignKey(
        Scope,
        on_delete=models.CASCADE,
        related_name="roles",
    )

    group = models.ForeignKey(
        "Group",
        on_delete=models.CASCADE,
        related_name="scope_roles",
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


class FileStoreInScope(models.Model):
    """Database model used for extra data on Scope/FileStore relations."""

    scope = models.ForeignKey(Scope, on_delete=models.PROTECT)
    file_store = models.ForeignKey(FileStore, on_delete=models.PROTECT)

    _file_store_instance_wide = models.BooleanField(
        default=True,
        editable=False,
        db_column="file_store_instance_wide",
        help_text="Synced from FileStore.instance_wide; do not update directly",
    )

    #: The priority of this store for the purpose of storing new files.
    #: When adding a new file, debusine tries stores whose policies allow
    #: adding new files in descending order of upload priority, counting
    #: null as the lowest.
    upload_priority = models.IntegerField(blank=True, null=True)

    #: The priority of this store for the purpose of serving files to
    #: clients.  When downloading a file, debusine tries stores in
    #: descending order of download priority, counting null as the lowest;
    #: it breaks ties in descending order of upload priority, again counting
    #: null as the lowest.  If there is still a tie, it picks one of the
    #: possibilities arbitrarily.
    download_priority = models.IntegerField(blank=True, null=True)

    #: If True, the storage maintenance job ensures that this store has a
    #: copy of all files in the scope.
    populate = models.BooleanField(default=False)

    #: If True, the storage maintenance job moves all files in this scope to
    #: some other store in the same scope, following the same rules for
    #: finding a target store as for uploads of new files.  It does not move
    #: into a store if that would take its total size over `soft_max_size`
    #: (either for the scope or the file store), and it logs an error if it
    #: cannot find any eligible target store.
    drain = models.BooleanField(default=False)

    #: If this field is set, then constrain `drain` to use the store with
    #: the given name in this scope.
    drain_to = models.TextField(blank=True, null=True)

    #: If True, debusine will not add new files to this store.  Use this in
    #: combination with `drain` to prepare for removing the file store.
    read_only = models.BooleanField(default=False)

    #: If True, debusine will not read files from this store.  This is
    #: suitable for provider storage classes that are designed for long-term
    #: archival rather than routine retrieval, such as S3 Glacier Deep
    #: Archive.
    write_only = models.BooleanField(default=False)

    #: An integer specifying the number of bytes that the file store can
    #: hold for this scope (accounting files that are in multiple scopes to
    #: all of the scopes in question).  This limit may be exceeded
    #: temporarily during uploads; the storage maintenance job will move the
    #: least-recently-used files to another file store to get back below the
    #: limit.
    soft_max_size = models.IntegerField(blank=True, null=True)

    class Meta(TypedModelMeta):
        triggers = [
            pgtrigger.Trigger(
                name="db_filestoreinscope_sync_instance_wide",
                operation=pgtrigger.Insert | pgtrigger.Update,
                when=pgtrigger.Before,
                func=" ".join(
                    """
                    NEW.file_store_instance_wide =
                        (SELECT db_filestore.instance_wide
                         FROM db_filestore
                         WHERE db_filestore.id = NEW.file_store_id);
                    RETURN NEW;
                    """.split()
                ),
            )
        ]
        constraints = [
            UniqueConstraint(
                fields=["scope", "file_store"],
                name="%(app_label)s_%(class)s_unique_scope_file_store",
            ),
            UniqueConstraint(
                fields=["file_store"],
                name=(
                    "%(app_label)s_%(class)s_"
                    "unique_file_store_not_instance_wide"
                ),
                condition=Q(_file_store_instance_wide=False),
            ),
            # It does not make sense to request a store to be populated
            # while also requesting it to be either drained or read-only.
            CheckConstraint(
                name="%(app_label)s_%(class)s_consistent_populate",
                check=Q(populate=False) | Q(drain=False, read_only=False),
            ),
        ]

    def __str__(self) -> str:
        """Return basic information of FileStoreInScope."""
        return f"{self.scope}/{self.file_store.name}"
