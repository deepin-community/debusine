# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db collections."""

import re
from datetime import datetime
from functools import cached_property
from typing import (
    Any,
    ClassVar,
    Generic,
    Optional,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
)

import jsonpath_rw
from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import CheckConstraint, F, Q, QuerySet, UniqueConstraint
from django.db.models.fields.json import KT
from django.urls import reverse

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    BaseArtifactDataModel,
    CollectionCategory,
    DebusinePromise,
    SINGLETON_COLLECTION_CATEGORIES,
)
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.constraints import JsonDataUniqueConstraint
from debusine.db.context import context
from debusine.db.models.permissions import (
    AllowWorkers,
    PermissionUser,
    permission_check,
    permission_filter,
)
from debusine.db.models.workspaces import Workspace

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.db.models.artifacts import Artifact
    from debusine.db.models.auth import User
    from debusine.db.models.work_requests import WorkRequest
    from debusine.server.collections import CollectionManagerInterface
else:
    TypedModelMeta = object

A = TypeVar("A")

#: Regexp matching the structure of collection names
collection_name_regex = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*$")


def is_valid_collection_name(value: str) -> bool:
    """Check if value is a valid scope name."""
    return bool(collection_name_regex.match(value))


def validate_collection_name(value: str) -> None:
    """Validate collection names."""
    if not is_valid_collection_name(value):
        raise ValidationError(
            "%(value)r is not a valid collection name", params={"value": value}
        )


class _CollectionRetainsArtifacts(models.TextChoices):
    """Choices for Collection.retains_artifacts."""

    NEVER = "never", "Never"
    WORKFLOW = "workflow", "While workflow is running"
    ALWAYS = "always", "Always"


class CollectionQuerySet(QuerySet["Collection", A], Generic[A]):
    """Custom QuerySet for Collection."""

    def in_current_scope(self) -> "CollectionQuerySet[A]":
        """Filter to collections in the current scope."""
        return self.filter(workspace__scope=context.require_scope())

    def in_current_workspace(self) -> "CollectionQuerySet[A]":
        """Filter to collections in the current workspace."""
        return self.filter(workspace=context.require_workspace())

    @permission_filter(workers=AllowWorkers.ALWAYS)
    def can_display(self, user: PermissionUser) -> "CollectionQuerySet[A]":
        """Keep only Collections that can be displayed."""
        # Delegate to workspace can_display check
        return self.filter(workspace__in=Workspace.objects.can_display(user))


class CollectionManager(models.Manager["Collection"]):
    """Manager for Collection model."""

    def get_queryset(self) -> CollectionQuerySet[Any]:
        """Use the custom QuerySet."""
        return CollectionQuerySet(self.model, using=self._db)

    @staticmethod
    def get_or_create_singleton(
        category: CollectionCategory,
        workspace: Workspace,
        *,
        data: dict[str, Any] | None = None,
    ) -> "tuple[Collection, bool]":
        """Create a singleton collection."""
        if category not in SINGLETON_COLLECTION_CATEGORIES:
            raise ValueError(
                f"'{category}' is not a singleton collection category"
            )
        return Collection.objects.get_or_create(
            name="_", category=category, workspace=workspace, data=data or {}
        )


class Collection(models.Model):
    """Model representing a collection."""

    objects = CollectionManager.from_queryset(CollectionQuerySet)()

    RetainsArtifacts: TypeAlias = _CollectionRetainsArtifacts

    name = models.CharField(
        max_length=255, validators=[validate_collection_name]
    )
    category = models.CharField(max_length=255)
    full_history_retention_period = models.DurationField(null=True, blank=True)
    metadata_only_retention_period = models.DurationField(null=True, blank=True)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.PROTECT, related_name="collections"
    )
    retains_artifacts = models.CharField(
        max_length=8,
        choices=RetainsArtifacts.choices,
        default=RetainsArtifacts.ALWAYS,
    )

    child_artifacts = models.ManyToManyField(
        "db.Artifact",
        through="db.CollectionItem",
        through_fields=("parent_collection", "artifact"),
        related_name="parent_collections",
    )
    child_collections = models.ManyToManyField(
        "self",
        through="db.CollectionItem",
        through_fields=("parent_collection", "collection"),
        related_name="parent_collections",
        symmetrical=False,
    )

    data = models.JSONField(default=dict, blank=True)

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["name", "category", "workspace"],
                name="%(app_label)s_%(class)s_unique_name_category_workspace",
            ),
            CheckConstraint(
                check=~Q(name=""), name="%(app_label)s_%(class)s_name_not_empty"
            ),
            CheckConstraint(
                check=(
                    (
                        ~Q(category__in=SINGLETON_COLLECTION_CATEGORIES)
                        & ~Q(name__startswith="_")
                    )
                    | Q(category__in=SINGLETON_COLLECTION_CATEGORIES, name="_")
                ),
                name="%(app_label)s_%(class)s_name_not_reserved",
            ),
            CheckConstraint(
                check=~Q(category=""),
                name="%(app_label)s_%(class)s_category_not_empty",
            ),
        ]

    @cached_property
    def manager(self) -> "CollectionManagerInterface":
        """Get collection manager for this collection category."""
        # Local import to avoid circular dependency
        from debusine.server.collections import CollectionManagerInterface

        return CollectionManagerInterface.get_manager_for(self)

    def get_absolute_url(self) -> str:
        """Return the canonical URL to display the collection."""
        return reverse(
            "workspaces:collections:detail",
            kwargs={
                "wname": self.workspace.name,
                "ccat": self.category,
                "cname": self.name,
            },
        )

    def get_absolute_url_search(self) -> str:
        """Return the canonical URL to search inside the collection."""
        return reverse(
            "workspaces:collections:search",
            kwargs={
                "wname": self.workspace.name,
                "ccat": self.category,
                "cname": self.name,
            },
        )

    @permission_check(
        "{user} cannot display collection {resource}",
        workers=AllowWorkers.ALWAYS,
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the collection can be displayed."""
        return self.workspace.can_display(user)

    def __str__(self) -> str:
        """Return id, name, category."""
        # Stringify using a valid lookup syntax
        return f"{self.name}@{self.category}"


class CollectionItemQuerySet(QuerySet["CollectionItem"]):
    """Custom QuerySet for CollectionItem."""

    def artifacts_in_collection(
        self, parent_collection: Collection, category: ArtifactCategory
    ) -> "CollectionItemQuerySet":
        """
        Filter to artifacts in a given parent collection.

        This is tuned to use the indexes on :py:class:`CollectionItem`.
        """
        return self.filter(
            parent_collection=parent_collection,
            # Technically redundant with parent_collection, but clues
            # PostgreSQL into using the correct index.
            parent_category=parent_collection.category,
            child_type=CollectionItem.Types.ARTIFACT,
            category=category,
        )


class CollectionItemManager(models.Manager["CollectionItem"]):
    """Manager for CollectionItem model."""

    @staticmethod
    def create_from_bare_data(
        category: BareDataCategory,
        *,
        parent_collection: Collection,
        name: str,
        data: BaseArtifactDataModel | dict[str, Any],
        created_by_user: "User",
        created_by_workflow: Optional["WorkRequest"],
    ) -> "CollectionItem":
        """Create a CollectionItem from bare data."""
        if isinstance(data, BaseArtifactDataModel):
            data = model_to_json_serializable_dict(data, exclude_unset=True)

        match category:
            case BareDataCategory.PROMISE:
                # Raise ValueError if data is not valid
                DebusinePromise(**data)

        return CollectionItem.objects.create(
            parent_collection=parent_collection,
            name=name,
            child_type=CollectionItem.Types.BARE,
            category=category,
            data=data,
            created_by_user=created_by_user,
            created_by_workflow=created_by_workflow,
        )

    @staticmethod
    def create_from_artifact(
        artifact: "Artifact",
        *,
        parent_collection: Collection,
        name: str,
        data: dict[str, Any],
        created_by_user: "User",
        created_by_workflow: Optional["WorkRequest"] = None,
    ) -> "CollectionItem":
        """Create a CollectionItem from the artifact."""
        return CollectionItem.objects.create(
            parent_collection=parent_collection,
            name=name,
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=created_by_user,
            created_by_workflow=created_by_workflow,
        )

    @staticmethod
    def create_from_collection(
        collection: Collection,
        *,
        parent_collection: Collection,
        name: str,
        data: dict[str, Any],
        created_by_user: "User",
    ) -> "CollectionItem":
        """Create a CollectionItem from the collection."""
        return CollectionItem.objects.create(
            parent_collection=parent_collection,
            name=name,
            collection=collection,
            child_type=CollectionItem.Types.COLLECTION,
            category=collection.category,
            data=data,
            created_by_user=created_by_user,
        )

    def drop_full_history(self, at: datetime) -> None:
        """
        Drop artifacts from collections after full_history_retention_period.

        :param at: datetime to check if the artifacts are old enough.
        """
        self.get_queryset().exclude(removed_at__isnull=True).filter(
            # https://github.com/typeddjango/django-stubs/issues/1548
            removed_at__lt=(
                at - F("parent_collection__full_history_retention_period")  # type: ignore[operator] # noqa: E501
            )
        ).update(artifact=None)

    def drop_metadata(self, at: datetime) -> None:
        """
        Delete old collection items.

        After full_history_retention_period + metadata_only_retention_period.

        :param at: datetime to check if the collection item is old enough.
        """
        self.get_queryset().exclude(removed_at__isnull=True).filter(
            # https://github.com/typeddjango/django-stubs/issues/1548
            removed_at__lt=(
                at - F("parent_collection__full_history_retention_period") - F("parent_collection__metadata_only_retention_period")  # type: ignore[operator] # noqa: E501
            )
        ).delete()


class CollectionItemActiveManager(CollectionItemManager):
    """Manager for active collection items."""

    def get_queryset(self) -> QuerySet["CollectionItem"]:
        """Return only active collection items."""
        return super().get_queryset().filter(removed_at__isnull=True)


class _CollectionItemTypes(models.TextChoices):
    """Choices for the CollectionItem.type."""

    BARE = "b", "Bare"
    ARTIFACT = "a", "Artifact"
    COLLECTION = "c", "Collection"


class CollectionItem(models.Model):
    """CollectionItem model."""

    objects = CollectionItemManager.from_queryset(CollectionItemQuerySet)()
    active_objects = CollectionItemActiveManager.from_queryset(
        CollectionItemQuerySet
    )()

    name = models.CharField(max_length=255)

    Types: TypeAlias = _CollectionItemTypes

    child_type = models.CharField(max_length=1, choices=Types.choices)

    # category duplicates the category of the artifact or collection of this
    # item, so when the underlying artifact or collection is deleted the
    # category is retained
    category = models.CharField(max_length=255)

    parent_collection = models.ForeignKey(
        Collection,
        on_delete=models.PROTECT,
        related_name="child_items",
    )
    parent_category = models.CharField(max_length=255)

    collection = models.ForeignKey(
        Collection,
        on_delete=models.PROTECT,
        related_name="collection_items",
        null=True,
    )
    artifact = models.ForeignKey(
        "Artifact",
        on_delete=models.PROTECT,
        related_name="collection_items",
        null=True,
    )

    data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="user_created_%(class)s",
    )
    created_by_workflow = models.ForeignKey(
        "WorkRequest",
        on_delete=models.PROTECT,
        null=True,
        related_name="workflow_created_%(class)s",
    )

    removed_at = models.DateTimeField(blank=True, null=True)
    removed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="user_removed_%(class)s",
    )
    removed_by_workflow = models.ForeignKey(
        "WorkRequest",
        on_delete=models.PROTECT,
        null=True,
        related_name="workflow_removed_%(class)s",
    )

    @staticmethod
    def expand_variables(
        variables: dict[str, str], reference_data: dict[Any, Any]
    ) -> dict[str, str]:
        """
        Expand JSONPath variables against some data.

        The data will normally come from an Artifact.
        """
        jsonpaths = {}
        for name, path in variables.items():
            if name.startswith("$"):
                try:
                    jsonpaths[name[1:]] = jsonpath_rw.parse(path)
                except Exception as e:
                    raise ValueError(e)

        expanded_variables = {}
        for name, jsonpath in jsonpaths.items():
            matches = jsonpath.find(reference_data)
            if len(matches) == 1:
                expanded_variables[name] = matches[0].value
            elif len(matches) > 1:
                raise ValueError(
                    "Too many values expanding",
                    variables[f"${name}"],
                    reference_data,
                )
            else:
                raise KeyError(variables[f"${name}"], reference_data)

        for name, value in variables.items():
            if not name.startswith("$"):
                if name in expanded_variables:
                    raise ValueError(
                        f"Cannot set both '${name}' and '{name}' variables"
                    )
                expanded_variables[name] = value

        return expanded_variables

    @staticmethod
    def expand_name(
        name_template: str, expanded_variables: dict[str, str]
    ) -> str:
        """Format item name following item_template."""
        return name_template.format(**expanded_variables)

    def get_absolute_url(self) -> str:
        """Return the canonical URL to display the item."""
        return reverse(
            "workspaces:collections:item_detail",
            kwargs={
                "wname": self.parent_collection.workspace.name,
                "ccat": self.parent_collection.category,
                "cname": self.parent_collection.name,
                "iid": self.pk,
                "iname": self.name,
            },
        )

    def __str__(self) -> str:
        """Return id, name, collection_id, child_type."""
        item_info = (
            f" Artifact id: {self.artifact.id}"
            if self.artifact
            else (
                f" Collection id: {self.collection.id}"
                if self.collection
                else ""
            )
        )

        return (
            f"Id: {self.id} Name: {self.name} "
            f"Parent collection id: {self.parent_collection_id} "
            f"Child type: {self.child_type}"
            f"{item_info}"
        )

    class Meta(TypedModelMeta):
        constraints = [
            JsonDataUniqueConstraint(
                fields=[
                    "category",
                    "data->>'codename'",
                    "data->>'architecture'",
                    "data->>'variant'",
                    "data->>'backend'",
                    "parent_collection",
                ],
                condition=Q(
                    parent_category=CollectionCategory.ENVIRONMENTS,
                    removed_at__isnull=True,
                ),
                nulls_distinct=False,
                name="%(app_label)s_%(class)s_unique_debian_environments",
            ),
            UniqueConstraint(
                fields=["name", "parent_collection"],
                condition=Q(removed_at__isnull=True),
                name="%(app_label)s_%(class)s_unique_active_name",
            ),
            # Prevent direct way to add a collection to itself.
            # It is still possible to add loops of collections. The Manager
            # should avoid it
            CheckConstraint(
                check=~Q(collection=F("parent_collection")),
                name="%(app_label)s_%(class)s_distinct_parent_collection",
            ),
            CheckConstraint(
                name="%(app_label)s_%(class)s_childtype_removedat_consistent",
                check=(
                    Q(
                        child_type=_CollectionItemTypes.BARE,
                        collection__isnull=True,
                        artifact__isnull=True,
                    )
                    | (
                        Q(
                            child_type=_CollectionItemTypes.ARTIFACT,
                            collection__isnull=True,
                        )
                        & (
                            Q(artifact__isnull=False)
                            | Q(removed_at__isnull=False)
                        )
                    )
                    | (
                        Q(
                            child_type=_CollectionItemTypes.COLLECTION,
                            artifact__isnull=True,
                        )
                        & (
                            Q(collection__isnull=False)
                            | Q(removed_at__isnull=False)
                        )
                    )
                ),
            ),
        ]
        indexes = [
            models.Index(
                F("parent_collection"),
                KT("data__package"),
                KT("data__version"),
                name="%(app_label)s_ci_suite_source_idx",
                condition=Q(
                    parent_category=CollectionCategory.SUITE,
                    child_type=_CollectionItemTypes.ARTIFACT,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                ),
            ),
            models.Index(
                F("parent_collection"),
                KT("data__srcpkg_name"),
                KT("data__srcpkg_version"),
                name="%(app_label)s_ci_suite_binary_source_idx",
                condition=Q(
                    parent_category=CollectionCategory.SUITE,
                    child_type=_CollectionItemTypes.ARTIFACT,
                    category=ArtifactCategory.BINARY_PACKAGE,
                ),
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Populate `parent_category` on save."""
        if not self.parent_category:
            self.parent_category = self.parent_collection.category
        super().save(*args, **kwargs)


class CollectionItemMatchConstraint(models.Model):
    """
    Enforce matching-value constraints on collection items.

    All instances of this model with the same :py:attr:`collection`,
    :py:attr:`constraint_name`, and :py:attr:`key` must have the same
    :py:attr:`value`.
    """

    objects: ClassVar[models.Manager["CollectionItemMatchConstraint"]] = (
        models.Manager["CollectionItemMatchConstraint"]()
    )

    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name="item_match_constraints",
    )
    # This is deliberately a bare ID, not a foreign key: some constraints
    # take into account even items that no longer exist but were in a
    # collection in the past.
    collection_item_id = models.BigIntegerField()
    constraint_name = models.CharField(max_length=255)
    key = models.TextField()
    value = models.TextField()

    class Meta(TypedModelMeta):
        constraints = [
            ExclusionConstraint(
                name="%(app_label)s_%(class)s_match_value",
                expressions=(
                    (F("collection"), RangeOperators.EQUAL),
                    (F("constraint_name"), RangeOperators.EQUAL),
                    (F("key"), RangeOperators.EQUAL),
                    (F("value"), RangeOperators.NOT_EQUAL),
                ),
            )
        ]
        indexes = [
            models.Index(
                name="%(app_label)s_cimc_collection_item_idx",
                fields=["collection_item_id"],
            )
        ]
