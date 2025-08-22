# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db users and authentication."""

import abc
import enum
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    Generic,
    Literal,
    Optional,
    Self,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    Union,
    cast,
)

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.db.models import CheckConstraint, Q, QuerySet, UniqueConstraint
from django.urls import reverse
from django.utils import timezone

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import permissions
from debusine.db.models.permissions import (
    Allow,
    PermissionUser,
    Role,
    get_resource_scope,
    permission_check,
    permission_filter,
    resolve_role,
)
from debusine.db.models.scopes import Scope
from debusine.server import notifications
from debusine.utils.typing_utils import copy_signature_from

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    # https://github.com/typeddjango/django-stubs/issues/2112
    AbstractUserMeta = TypedModelMeta
else:
    TypedModelMeta = object
    AbstractUserMeta = AbstractUser.Meta

A = TypeVar("A")


class TokenManager(models.Manager["Token"]):
    """Manager for Token model."""

    def create_worker_activation(self) -> "Token":
        """Create a token suitable for use in worker activation."""
        # These are currently hardcoded to expire in 15 minutes, which
        # should be enough for most providers to be able to launch an
        # instance.
        return self.create(
            expire_at=timezone.now() + timedelta(minutes=15), enabled=True
        )

    def get_tokens(
        self,
        username: str | None = None,
        key: str | None = None,
        include_expired: bool = False,
    ) -> QuerySet["Token"]:
        """
        Return all the tokens filtered by a specific owner and/or token.

        To avoid filtering by owner or token set them to None
        """
        tokens = self.get_queryset()

        if username:
            tokens = tokens.filter(user__username=username)

        if key:
            token_hash = hashlib.sha256(key.encode()).hexdigest()
            tokens = tokens.filter(hash=token_hash)

        if not include_expired:
            tokens = tokens.exclude(expire_at__lt=timezone.now())

        return tokens

    def get_token_or_none(self, token_key: str) -> Optional["Token"]:
        """Return the token with token_key or None."""
        assert isinstance(token_key, str)

        token_hash = hashlib.sha256(token_key.encode()).hexdigest()

        try:
            return (
                self.select_related("worker", "activating_worker")
                .exclude(expire_at__lt=timezone.now())
                .get(hash=token_hash)
            )
        except Token.DoesNotExist:
            return None

    def expired(self, at: datetime) -> QuerySet["Token"]:
        """
        Return queryset with tokens that have expired.

        :param at: datetime to check if the tokens are expired.
        :return: tokens whose ``expire_at`` is before the given datetime.
        """
        return self.get_queryset().filter(expire_at__lt=at)


class Token(models.Model):
    """
    Database model of a token.

    A token contains a key and other related data. It's used as a shared
    key between debusine server and clients (workers).

    This token model is very similar to rest_framework.authtoken.models.Token.
    The bigger difference is that debusine's token's owner is a CharField,
    the rest_framework owner is a OneToOne foreign key to a user.

    Database-wise we don't store the token itself, but a hash of the token.
    :py:func:`TokenManager.get_token_or_none` can be used to check a provided
    token key against the database.
    """

    hash = models.CharField(
        max_length=64,
        unique=True,
        verbose_name='Hexadecimal hash, length is 64 chars',
        validators=[MaxLengthValidator(64), MinLengthValidator(64)],
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    comment = models.CharField(
        max_length=100,
        default='',
        verbose_name='Reason that this token was created',
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expire_at = models.DateTimeField(null=True, blank=True)

    enabled = models.BooleanField(default=False)

    last_seen_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Last time that the token was used",
    )

    key: str

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the token. If it's a new token it generates a key."""
        if not self.hash:
            self.key = self._generate_key()
            self.hash = self._generate_hash(self.key)
        super().save(*args, **kwargs)

    def enable(self) -> None:
        """Enable the token and save it."""
        self.enabled = True
        self.save()

    def disable(self) -> None:
        """Disable the token and save it."""
        self.enabled = False
        self.save()

        notifications.notify_worker_token_disabled(self)

    def __str__(self) -> str:
        """Return the hash of the Token."""
        return self.hash

    @classmethod
    def _generate_key(cls) -> str:
        """Create and return a key."""
        return secrets.token_hex(32)

    @classmethod
    def _generate_hash(cls, secret: str) -> str:
        """Hash the given secret."""
        return hashlib.sha256(secret.encode()).hexdigest()

    objects = TokenManager()


SYSTEM_USER_NAME = "_system"


def system_user() -> "User":
    """Return the `_system` user."""
    return User.objects.get(username=SYSTEM_USER_NAME)


class UserQuerySet(QuerySet["User", A], Generic[A]):
    """Custom QuerySet for User."""

    @permission_filter(workers=Allow.ALWAYS)
    def can_display(
        self, user: PermissionUser  # noqa: U100
    ) -> "UserQuerySet[A]":
        """Keep only Users that can be displayed."""
        assert user is not None  # Enforced by decorator
        return self

    @permission_filter()
    def can_manage(self, user: PermissionUser) -> "UserQuerySet[A]":
        """Keep only Users that the given user can manage."""
        assert user is not None  # Enforced by decorator
        return self.filter(pk=user.pk)


class UserManager(DjangoUserManager["User"]):
    """Manager for User model."""

    # We cannot use from_queryset or we hit this problem:
    # https://stackoverflow.com/questions/68367703/adding-custom-queryset-to-usermodel-causes-makemigrations-exception  # noqa: E501
    # Therefore we need to proxy all permission filters here

    def can_display(
        self, user: PermissionUser  # noqa: U100
    ) -> "UserQuerySet[A]":
        """Keep only Users that can be displayed."""
        return self.get_queryset().can_display(user)

    def can_manage(self, user: PermissionUser) -> "UserQuerySet[A]":
        """Keep only Users that the given user can manage."""
        return self.get_queryset().can_manage(user)

    def get_queryset(self) -> UserQuerySet[Any]:
        """Use the custom QuerySet."""
        return UserQuerySet(self.model, using=self._db)


class User(AbstractUser):
    """Debusine user."""

    is_system = models.BooleanField(default=False)

    # mypy seems to struggle changing the manager in a subclass
    objects = UserManager()  # type: ignore[misc]

    class Meta(AbstractUserMeta):
        constraints = [
            # System users must not have an email address.
            CheckConstraint(
                check=Q(is_system=False) | Q(email=""),
                name="%(app_label)s_%(class)s_non_system_email",
            ),
            # Email addresses of non-system users must be unique.
            UniqueConstraint(
                fields=["email"],
                condition=Q(is_system=False),
                name="%(app_label)s_%(class)s_unique_email",
            ),
        ]

    @permission_check(
        "{user} cannot display user {resource}", workers=Allow.ALWAYS
    )
    def can_display(self, user: PermissionUser) -> bool:  # noqa: U100
        """Check if the user can be displayed."""
        assert user is not None  # enforced by decorator
        # Disallow unauthenticated people to enumerate users in the system.
        #
        # In general, we do not assume that user information is automatically
        # public.
        #
        # This could help make life a bit harder, for example, for drive-by
        # spammers trying to enumerate users and their personal information.
        return True

    @permission_check("{user} cannot manage user {resource}")
    def can_manage(self, user: PermissionUser) -> bool:
        """Check if the user can manage this user."""
        assert user is not None  # enforced by decorator
        return self.pk == user.pk

    def get_absolute_url(self) -> str:
        """Return an absolute URL to this user."""
        return reverse("user:detail", kwargs={"username": self.username})


class Identity(models.Model):
    """
    Identity for a user in a remote user database.

    An Identity is bound if it's associated with a Django user, or unbound if
    no Django user is known for it.
    """

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["issuer", "subject"],
                name="%(app_label)s_%(class)s_unique_issuer_subject",
            ),
        ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="identities",
        null=True,
        on_delete=models.SET_NULL,
    )
    issuer = models.CharField(
        max_length=512,
        help_text="identifier of auhoritative system for this identity",
    )
    subject = models.CharField(
        max_length=512,
        help_text="identifier of the user in the issuer system",
    )
    last_used = models.DateTimeField(
        auto_now=True, help_text="last time this identity has been used"
    )
    claims = models.JSONField(default=dict)

    def __str__(self) -> str:
        """Return str for the object."""
        return f"{self.issuer}:{self.subject}"


#: Regexp matching the structure of group names
group_name_regex = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*$")


def is_valid_group_name(value: str) -> bool:
    """Check if value is a valid group name."""
    return bool(group_name_regex.match(value))


def validate_group_name(value: str) -> None:
    """Validate group names."""
    if not is_valid_group_name(value):
        raise ValidationError(
            "%(value)r is not a valid group name", params={"value": value}
        )


class GroupQuerySet(QuerySet["Group", A], Generic[A]):
    """Custom QuerySet for Group."""

    @permission_filter()
    def can_display(self, user: PermissionUser) -> "GroupQuerySet[A]":
        """Keep only groups that can be displayed."""
        assert user is not None  # Enforced by decorator
        return self.all()

    @permission_filter()
    def can_display_audit_log(self, user: PermissionUser) -> "GroupQuerySet[A]":
        """Keep only groups whose audit log can be displayed."""
        return self.can_display(user)

    @permission_filter()
    def can_manage(self, user: PermissionUser) -> "GroupQuerySet[A]":
        """Keep only groups that can be managed."""
        assert user is not None  # Enforced by decorator

        # Allow to scope owners
        constraints = Q(
            scope__in=Scope.objects.filter(
                roles__group__users=user,
                roles__role=Scope.Roles.OWNER,
            )
        )
        # Allow to group admins
        constraints |= Q(
            membership__user=user, membership__role=Group.Roles.ADMIN
        )
        return self.filter(constraints).distinct()

    def unused_ephemeral(self) -> "GroupQuerySet[Any]":
        """List ephemeral groups with no roles assigned."""
        constraints = Q()
        # Introspect reverse foreign keys to detect which resources have a
        # *Roles table
        for field in Group._meta.get_fields():
            if (
                field.auto_created
                and not field.concrete
                and field.name.endswith("_roles")
            ):
                # If this resource is currently assigning roles, then the group
                # is still in use
                constraints &= Q(**{f"{field.name}__isnull": True})
        return self.filter(ephemeral=True).filter(constraints)


class GroupManager(models.Manager["Group"]):
    """Manager for Group model."""

    def get_queryset(self) -> GroupQuerySet[Any]:
        """Use the custom QuerySet."""
        return GroupQuerySet(self.model, using=self._db)

    def from_scoped_name(self, scope_group: str) -> "Group":
        """Lookup a group from a scopename/groupname string."""
        scope_name, group_name = scope_group.split("/", 1)
        return Group.objects.get(scope__name=scope_name, name=group_name)

    def create_ephemeral(self, scope: Scope, name: str, owner: User) -> "Group":
        """Create an ephemeral group for the given user."""
        group = self.create(scope=scope, name=name, ephemeral=True)
        # Sets the user as an admin of the newly created group
        group.add_user(owner, Group.Roles.ADMIN)
        return group


class GroupRoleBase(permissions.RoleBase):
    """Group role implementation."""

    implied_by_scope_roles: frozenset["Scope.Roles"]
    implied_by_group_roles: frozenset["GroupRoles"]

    def _setup(self) -> None:
        """Set up implications for a newly constructed role."""
        implied_by_scope_roles: set[Scope.Roles] = set()
        implied_by_group_roles: set[GroupRoles] = {cast(GroupRoles, self)}
        for i in self.implied_by:
            match i:
                case Scope.Roles():
                    implied_by_scope_roles |= i.implied_by_scope_roles
                case Role():
                    # Resolve a role passed during class definition into its
                    # enum instance
                    gr = self.__class__(i.value)
                    implied_by_scope_roles |= gr.implied_by_scope_roles
                    implied_by_group_roles |= gr.implied_by_group_roles
                case _:
                    raise ImproperlyConfigured(
                        "Group roles do not support"
                        f" implications by {type(i)}"
                    )
        self.implied_by_scope_roles = frozenset(implied_by_scope_roles)
        self.implied_by_group_roles = frozenset(implied_by_group_roles)

    def implies(self, role: "GroupRoles") -> bool:
        """Check if this role implies the given one."""
        return self in role.implied_by_group_roles


class GroupRoles(permissions.Roles, GroupRoleBase, enum.ReprEnum):
    """Available roles for a Scope."""

    ADMIN = Role("admin", implied_by=[Scope.Roles.OWNER])
    MEMBER = Role("member", implied_by=[ADMIN])


GroupRoles.setup()


class Group(models.Model):
    """
    Connect users to roles.

    Group names are scoped: groups with the same name in different scopes are
    entirely distinct and unrelated.
    """

    Roles: TypeAlias = GroupRoles

    name = models.CharField(max_length=255, validators=[validate_group_name])
    scope = models.ForeignKey(Scope, null=False, on_delete=models.PROTECT)
    users = models.ManyToManyField(
        User,
        blank=True,
        through="db.GroupMembership",
        related_name="debusine_groups",
    )
    ephemeral = models.BooleanField(
        default=False, help_text="remove the group if it has no roles assigned"
    )

    objects = GroupManager.from_queryset(GroupQuerySet)()

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["name", "scope"],
                name="%(app_label)s_%(class)s_unique_name_scope",
            ),
        ]

    def __str__(self) -> str:
        """Return the scoped group name."""
        return f"{self.scope.name}/{self.name}"

    @copy_signature_from(models.Model.save)
    def save(self, *args: Any, **kwargs: Any) -> None:
        """Update audit log on group create/update."""
        old: Optional["Group"] = None
        if self.pk:
            old = Group.objects.get(pk=self.pk)
        super().save(*args, **kwargs)
        if old is None:
            self.add_audit_log(GroupAuditLogCreated.create(self))
        else:
            self.add_audit_log(GroupAuditLogUpdated.create_diff(old, self))

    def get_absolute_url(self) -> str:
        """Return an absolute URL to this user."""
        # Prevent circular import
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.scope.name):
            return reverse(
                "groups:detail",
                kwargs={"group": self.name},
            )

    @permission_check("{user} cannot display {resource}")
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the group can be displayed."""
        assert user is not None  # enforced by decorator
        return True

    @permission_check("{user} cannot display the audit log of {resource}")
    def can_display_audit_log(self, user: PermissionUser) -> bool:
        """Check if the group audit log can be displayed."""
        return self.can_display(user)

    @permission_check("{user} cannot manage {resource}")
    def can_manage(self, user: PermissionUser) -> bool:
        """Check if the group can be managed."""
        assert user is not None  # enforced by decorator
        # Shortcut to avoid hitting the database for common cases
        if (
            context.user == user
            and context.scope == self.scope
            and not context.scope_roles.isdisjoint(
                GroupRoles.ADMIN.implied_by_scope_roles
            )
        ):
            return True
        return Group.objects.can_manage(user).filter(pk=self.pk).exists()

    def assign_role(self, resource: models.Model, role: str) -> models.Model:
        """Assign a role on a resource."""
        if get_resource_scope(resource) != self.scope:
            raise ValueError(
                f"{resource.__class__.__name__} {str(resource)!r}"
                f" is not in scope {self.scope}"
            )

        role = resolve_role(resource, role)

        roles_model_cls: models.Model = getattr(
            resource.__class__, "objects"
        ).get_roles_model()
        assignment, _ = getattr(roles_model_cls, "objects").get_or_create(
            resource=resource, group=self, role=role
        )
        return cast(models.Model, assignment)

    def get_user_role(self, user: User) -> GroupRoles:
        """Get the role of an existing group member."""
        try:
            membership = GroupMembership.objects.get(group=self, user=user)
        except GroupMembership.DoesNotExist:
            raise ValueError(f"User {user} is not a member of group {self}")
        return cast(GroupRoles, membership.role)

    def set_user_role(self, user: User, role: Roles) -> "GroupMembership":
        """Set the role of an existing group member."""
        try:
            membership = GroupMembership.objects.get(group=self, user=user)
        except GroupMembership.DoesNotExist:
            raise ValueError(f"User {user} is not a member of group {self}")
        membership.role = role
        membership.save()
        self.add_audit_log(
            GroupAuditLogMemberRoleChanged(user=user.username, role=role)
        )
        return membership

    def add_user(
        self, user: User, role: Roles = Roles.MEMBER
    ) -> "GroupMembership":
        """Add a user to the group."""
        membership, created = GroupMembership.objects.get_or_create(
            group=self, user=user, defaults={"role": role}
        )
        if not created:
            raise ValueError(f"{user} is already a member of group {self}")
        self.add_audit_log(
            GroupAuditLogMemberAdded(user=user.username, role=role)
        )
        return membership

    def remove_user(self, user: User) -> None:
        """Remove an existing member from the group."""
        try:
            membership = GroupMembership.objects.get(group=self, user=user)
        except GroupMembership.DoesNotExist:
            raise ValueError(f"User {user} is not a member of group {self}")
        membership.delete()
        self.add_audit_log(GroupAuditLogMemberRemoved(user=user.username))

    def add_audit_log(
        self, changes: "GroupAuditLogEntry"
    ) -> Optional["GroupAuditLog"]:
        """Add an audit log entry."""
        if context.user is None or not context.user.is_authenticated:
            if context.permission_checks_disabled:
                return None
            raise ContextConsistencyError("add_audit_log requires a valid user")
        changes.actor = context.user.username
        return GroupAuditLog.objects.create_entry(
            group=self, actor=context.user, changes=changes
        )


class GroupMembership(models.Model):
    """Membership of a user in a group."""

    Roles: TypeAlias = GroupRoles

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="membership"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="group_memberships"
    )
    role = models.CharField(
        max_length=16, choices=Roles.choices, default=Roles.MEMBER
    )

    def __str__(self) -> str:
        """Return a string representation."""
        return f"{self.group}:{self.user}"

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["group", "user"],
                name="%(app_label)s_%(class)s_unique_group_user",
            ),
        ]


class GroupAuditLogEntryTypes(StrEnum):
    """Possible values for GroupAuditLogEntry types."""

    CREATED = "create"
    UPDATED = "update"
    ADD_USER = "add_user"
    REMOVE_USER = "del_user"
    SET_USER_ROLE = "set_role"


class GroupAuditLogEntryBase(pydantic.BaseModel, abc.ABC):
    """Base class for all audit log payloads."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    #: Username of the user performing the action
    actor: str = ""

    @abc.abstractmethod
    def description(self) -> str:
        """Format a description for this change."""


class GroupAuditLogCreated(GroupAuditLogEntryBase):
    """Group has been created."""

    type: Literal[GroupAuditLogEntryTypes.CREATED] = (
        GroupAuditLogEntryTypes.CREATED
    )
    name: str
    scope: str
    ephemeral: bool

    @classmethod
    def create(cls, group: Group) -> Self:
        """Create an entry populated with group's fields."""
        return cls(
            name=group.name,
            scope=str(group.scope),
            ephemeral=group.ephemeral,
        )

    def description(self) -> str:
        """Format a description for this change."""
        unknown = "<unknown>"
        group_desc = (
            f"group {self.name or unknown} in scope {self.scope or unknown}"
        )
        if self.ephemeral:
            return f"{self.actor} created ephemeral {group_desc}"
        else:
            return f"{self.actor} created {group_desc}"


class GroupAuditLogUpdated(GroupAuditLogEntryBase):
    """Group has been updated."""

    type: Literal[GroupAuditLogEntryTypes.UPDATED] = (
        GroupAuditLogEntryTypes.UPDATED
    )
    name: tuple[str, str] | None = None
    scope: tuple[str, str] | None = None
    ephemeral: tuple[bool, bool] | None = None

    @classmethod
    def create_diff(cls, old: Group, new: Group) -> Self:
        """
        Compute the changes between two Group objects.

        :returns: a dict mapping field names to tuples of ``(old, new)`` values
        """
        name: tuple[str, str] | None = None
        scope: tuple[str, str] | None = None
        ephemeral: tuple[bool, bool] | None = None
        if old.name != new.name:
            name = (old.name, new.name)
        if old.scope != new.scope:
            scope = (str(old.scope), str(new.scope))
        if old.ephemeral != new.ephemeral:
            ephemeral = (old.ephemeral, new.ephemeral)
        return cls(name=name, scope=scope, ephemeral=ephemeral)

    def description(self) -> str:
        """Format a description for this change."""
        changes: list[str] = []
        for field in "name", "scope", "ephemeral":
            change = getattr(self, field)
            if change is None:
                continue
            changes.append(f"{field}: {change[0]!r}→{change[1]!r}")
        if changes:
            return f"{self.actor} updated {'; '.join(changes)}"
        else:
            return f"{self.actor} updated group, no changes detected"


class GroupAuditLogMembershipBase(GroupAuditLogEntryBase):
    """Base for membership changes descriptions."""

    #: Username of the member affected
    user: str


class GroupAuditLogMemberAdded(GroupAuditLogMembershipBase):
    """Member added to the gruoup."""

    type: Literal[GroupAuditLogEntryTypes.ADD_USER] = (
        GroupAuditLogEntryTypes.ADD_USER
    )
    role: GroupRoles

    def description(self) -> str:
        """Format a description for this change."""
        return f"{self.actor} added {self.user} as {self.role}"


class GroupAuditLogMemberRoleChanged(GroupAuditLogMembershipBase):
    """Member role updated."""

    type: Literal[GroupAuditLogEntryTypes.SET_USER_ROLE] = (
        GroupAuditLogEntryTypes.SET_USER_ROLE
    )
    role: GroupRoles

    def description(self) -> str:
        """Format a description for this change."""
        return f"{self.actor} set {self.user}'s role as {self.role}"


class GroupAuditLogMemberRemoved(GroupAuditLogMembershipBase):
    """Member removed from the gruoup."""

    type: Literal[GroupAuditLogEntryTypes.REMOVE_USER] = (
        GroupAuditLogEntryTypes.REMOVE_USER
    )

    def description(self) -> str:
        """Format a description for this change."""
        return f"{self.actor} removed {self.user}"


GroupAuditLogEntry: TypeAlias = Annotated[
    Union[
        GroupAuditLogCreated,
        GroupAuditLogUpdated,
        GroupAuditLogMemberAdded,
        GroupAuditLogMemberRoleChanged,
        GroupAuditLogMemberRemoved,
    ],
    pydantic.Field(discriminator="type"),
]


class GroupAuditLogManager(models.Manager["GroupAuditLog"]):
    """Manager for :py:class:`GroupAuditLog`."""

    def create_entry(
        self,
        group: Group,
        actor: User,
        changes: GroupAuditLogEntry,
    ) -> "GroupAuditLog":
        """Create GroupAuditLog entry."""
        return self.create(group=group, actor=actor, changes=changes.dict())


class GroupAuditLog(models.Model):
    """Audit log entry for group changes."""

    created_at = models.DateTimeField(auto_now_add=True)
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="audit_log"
    )
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    changes = models.JSONField()

    objects = GroupAuditLogManager()

    @property
    def changes_model(self) -> GroupAuditLogEntry:
        """Return the pydantic model for changes."""
        # mypy complains that GroupAuditLogEntry is a "<typing special form>",
        # but the code works. I'm adding a mypy override for now, and hopefully
        # this can get cleaned in pydantic2.
        # See https://salsa.debian.org/freexian-team/debusine/-/merge_requests/1701#note_597300  # noqa: E501
        return pydantic.parse_obj_as(
            GroupAuditLogEntry, self.changes  # type: ignore[arg-type]
        )


class ClientEnroll(models.Model):
    """Information from a client wanting to enroll."""

    created_at = models.DateTimeField(auto_now_add=True)
    nonce = models.CharField(max_length=64, unique=True)
    payload = models.JSONField(default=dict)
