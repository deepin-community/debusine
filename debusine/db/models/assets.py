# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for assets."""

import enum
from typing import Any, Generic, TYPE_CHECKING, TypeAlias, TypeVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.constraints import CheckConstraint

from debusine.assets import (
    AssetCategory,
    BaseAssetDataModel,
    SigningKeyData,
    asset_data_model,
)
from debusine.db.constraints import JsonDataUniqueConstraint
from debusine.db.context import context
from debusine.db.models import permissions
from debusine.db.models.permissions import (
    AllowWorkers,
    PermissionUser,
    enforce,
    permission_check,
    permission_filter,
)
from debusine.db.models.scopes import ScopeRoles
from debusine.db.models.workspaces import Workspace
from debusine.tasks.models import WorkerType
from debusine.utils.typing_utils import copy_signature_from

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta
else:
    TypedModelMeta = object


class UnknownPermissionError(Exception):
    """A permission was requested, but does not exist."""


A = TypeVar("A")


class AssetQuerySet(models.QuerySet["Asset", A], Generic[A]):
    """Custom QuerySet for Asset."""

    def in_current_scope(self) -> "AssetQuerySet[A]":
        """Filter to assets in the current scope."""
        return self.filter(workspace__scope=context.require_scope())

    @permission_filter(workers=AllowWorkers.PASS)
    def can_display(self, user: PermissionUser) -> "AssetQuerySet[A]":
        """Keep only Assets that can be displayed."""
        # Delegate to workspace can_display check
        return self.filter(
            workspace__in=Workspace.objects.can_display(user)
        ).exclude(category=AssetCategory.CLOUD_PROVIDER_ACCOUNT)

    @permission_filter(workers=AllowWorkers.NEVER)
    def can_manage_permissions(
        self, user: PermissionUser
    ) -> "AssetQuerySet[A]":
        """Filter to Assets that can be managed by user."""
        return self.filter(
            roles__group__users=user, roles__role=AssetRoles.OWNER
        )


class AssetManager(models.Manager["Asset"]):
    """Manager for the Asset model."""

    def get_roles_model(self) -> type["AssetRole"]:
        """Get the model used for role assignment."""
        return AssetRole

    def get_queryset(self) -> AssetQuerySet[Any]:
        """Use the custom QuerySet."""
        return AssetQuerySet(self.model, using=self._db)

    def get_by_slug(self, category: str, slug: str) -> "Asset":
        """Return an asset with a matching slug."""
        match category:
            case AssetCategory.SIGNING_KEY:
                purpose, fingerprint = slug.split(":", 1)
                return self.get(
                    category=category,
                    data__purpose=purpose,
                    data__fingerprint=fingerprint,
                )
            case _:
                raise ValueError(f"No slug defined for category '{category}'")


class AssetUsageManager(models.Manager["AssetUsage"]):
    """Manager for the AssetUsage model."""

    def get_roles_model(self) -> type["AssetUsageRole"]:
        """Get the model used for role assignment."""
        return AssetUsageRole


class AssetRoles(permissions.Roles, permissions.RoleBase, enum.ReprEnum):
    """Available roles for an Asset."""

    OWNER = "owner"


AssetRoles.setup()


class AssetUsageRoles(permissions.Roles, permissions.RoleBase, enum.ReprEnum):
    """Available roles for an AssetUsage."""

    SIGNER = "signer"


AssetUsageRoles.setup()


class Asset(models.Model):
    """Asset model."""

    category = models.CharField(max_length=255, choices=AssetCategory.choices)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.PROTECT, blank=True, null=True
    )
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "User", blank=True, null=True, on_delete=models.PROTECT
    )
    created_by_work_request = models.ForeignKey(
        "WorkRequest", blank=True, null=True, on_delete=models.SET_NULL
    )

    Roles: TypeAlias = AssetRoles
    objects = AssetManager.from_queryset(AssetQuerySet)()

    class Meta(TypedModelMeta):
        constraints = [
            JsonDataUniqueConstraint(
                fields=["data->>'name'"],
                condition=models.Q(
                    category=AssetCategory.CLOUD_PROVIDER_ACCOUNT
                ),
                nulls_distinct=False,
                name="%(app_label)s_%(class)s_unique_cloud_provider_acct_name",
            ),
            JsonDataUniqueConstraint(
                fields=["data->>'fingerprint'"],
                condition=models.Q(category=AssetCategory.SIGNING_KEY),
                nulls_distinct=False,
                name="%(app_label)s_%(class)s_unique_signing_key_fingerprints",
            ),
            # Some categories of asset can have null workspaces, but not
            # signing keys
            CheckConstraint(
                check=~models.Q(category=AssetCategory.SIGNING_KEY)
                | models.Q(workspace__isnull=False),
                name="%(app_label)s_%(class)s_workspace_not_null",
            ),
        ]

    def clean(self) -> None:
        """
        Ensure that data is valid for this asset category.

        :raise ValidationError: for invalid data.
        """
        self.data_model

    @property
    def slug(self) -> str:
        """Return a string slug that uniquely identifies the asset."""
        match self.category:
            case AssetCategory.SIGNING_KEY:
                data_model = self.data_model
                assert isinstance(data_model, SigningKeyData)
                return f"{data_model.purpose}:{data_model.fingerprint}"
            case _:
                raise NotImplementedError(
                    f"No slug defined for category '{self.category}'"
                )

    @permission_check(
        "{user} cannot edit asset {resource}", workers=AllowWorkers.NEVER
    )
    def can_edit(self, user: PermissionUser) -> bool:
        """Can user edit this asset."""
        if user is None or not user.is_authenticated:
            return False
        return self.roles.filter(
            group__users=user, role=AssetRoles.OWNER
        ).exists()

    @permission_check(
        "{user} cannot create assets in {resource.workspace.scope}"
    )
    def can_create(self, user: PermissionUser) -> bool:
        """Can user create this asset."""
        from debusine.db.context import context

        # Allow signing workers to create Assets until we have delegated work
        # request permissions (#634)
        if context.worker_token and hasattr(context.worker_token, "worker"):
            if context.worker_token.worker.worker_type == WorkerType.SIGNING:
                return True
        if user is None or not user.is_authenticated:
            return False
        if not self.workspace:
            return False
        if self.category == AssetCategory.CLOUD_PROVIDER_ACCOUNT:
            # Not currently creatable through the API
            return False
        return ScopeRoles.OWNER in self.workspace.scope.get_roles(user)

    def has_permission(
        self,
        permission: str,
        user: PermissionUser,
        workspace: Workspace | None = None,
    ) -> bool:
        """Check user permissions on this asset (outside request context)."""
        if user is None or not user.is_authenticated:
            return False
        try:
            role = {
                'edit': AssetRoles.OWNER,
                'sign_with': AssetUsageRoles.SIGNER,
                'manage_permissions': AssetRoles.OWNER,
            }[permission]
        except KeyError:
            raise UnknownPermissionError()
        if workspace is not None:
            return self.usage.filter(
                workspace=workspace, roles__role=role, roles__group__users=user
            ).exists()
        else:
            return self.roles.filter(role=role, group__users=user).exists()

    @property
    def data_model(self) -> BaseAssetDataModel:
        """Instantiate AssetData from data."""
        if not isinstance(self.data, dict):
            raise ValidationError({"data": "data must be a dictionary"})

        try:
            return asset_data_model(self.category, self.data)
        except ValueError as e:
            raise ValidationError(
                {
                    "category": (
                        f"{self.category}: invalid asset category or data: {e}"
                    ),
                },
            ) from e

    @copy_signature_from(models.Model.save)
    def save(self, **kwargs: Any) -> None:
        """Wrap save with permission checks."""
        from debusine.db.context import context

        if context.permission_checks_disabled:
            pass
        elif self._state.adding:
            enforce(self.can_create)
        else:
            enforce(self.can_edit)

        return super().save(**kwargs)

    def __str__(self) -> str:
        """Return basic information of Asset."""
        return (
            f"Id: {self.id} "
            f"Category: {self.category} "
            f"Workspace: {self.workspace}"
        )


class AssetRole(models.Model):
    """Role assignment for assets."""

    resource = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="roles",
    )

    group = models.ForeignKey(
        "Group",
        on_delete=models.CASCADE,
        related_name="asset_roles",
    )

    role = models.CharField(max_length=16, choices=AssetRoles.choices)


class AssetUsage(models.Model):
    """Usage of an Asset within a workspace."""

    Roles: TypeAlias = AssetUsageRoles

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="usage",
    )

    workspace = models.ForeignKey(
        "Workspace",
        on_delete=models.CASCADE,
        related_name="asset_usage",
    )

    objects = AssetUsageManager()


class AssetUsageRole(models.Model):
    """Role assignment for assets within a workspace."""

    resource = models.ForeignKey(
        AssetUsage,
        on_delete=models.CASCADE,
        related_name="roles",
    )

    group = models.ForeignKey(
        "Group",
        on_delete=models.CASCADE,
        related_name="asset_usage_roles",
    )

    role = models.CharField(max_length=16, choices=AssetUsageRoles.choices)
