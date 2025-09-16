# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Interface for collections."""

import re
from abc import ABC
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    BaseArtifactDataModel,
    CollectionCategory,
)
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    User,
    WorkRequest,
    Workspace,
)
from debusine.tasks.models import LookupMultiple, LookupSingle


class ItemAdditionError(Exception):
    """
    Raised if the object cannot be added to the collection.

    For example: the object's category is not valid for the collection,
    or adding this object breaks some collection constraints.
    """


class ItemRemovalError(Exception):
    """
    Raised if the object cannot be removed from the collection.

    For example: removing this Item breaks a collection constraint.
    """


class CollectionManagerInterface(ABC):
    """Interface to manage a collection."""

    # Category of the managed collection
    COLLECTION_CATEGORY: CollectionCategory

    # Valid categories of the bare data items added in the collection; if
    # None, any category is valid
    VALID_BARE_DATA_CATEGORIES: frozenset[BareDataCategory] | None = frozenset()

    # Valid categories of the artifacts added in the collection; if None,
    # any category is valid
    VALID_ARTIFACT_CATEGORIES: frozenset[ArtifactCategory] | None = frozenset()

    # Valid categories of the collections added in the collection; if None,
    # any category is valid
    VALID_COLLECTION_CATEGORIES: frozenset[CollectionCategory] | None = (
        frozenset()
    )

    MANAGERS: dict[str, type["CollectionManagerInterface"]] = {}

    def __init__(self, collection: Collection) -> None:
        """
        Instantiate collection manager.

        :param collection: collection to be managed
        """
        if collection.category != self.COLLECTION_CATEGORY:
            raise ValueError(
                f'{self.__class__.__name__} cannot manage '
                f'"{collection.category}" category'
            )

        # collection that the items will be added / removed / looked up
        self.collection = collection

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register the subclass."""
        super().__init_subclass__(**kwargs)
        cls.MANAGERS[cls.COLLECTION_CATEGORY] = cls

    def __eq__(self, other: Any) -> bool:
        """Two managers are equal if they manage the same collection."""
        if not isinstance(other, self.__class__):
            return False
        return self.collection == other.collection

    @classmethod
    def get_manager_for(
        cls, collection: Collection
    ) -> "CollectionManagerInterface":
        """Get specialized manager subclass."""
        manager_cls = cls.MANAGERS[collection.category]
        return manager_cls(collection)

    def add_bare_data(
        self,
        category: BareDataCategory,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        data: BaseArtifactDataModel | dict[str, Any] | None = None,
        name: str | None = None,
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """
        Add bare data to the managed collection.

        Verify that the item can be added (e.g. bare data category is valid)
        and call
        `self.do_add_bare_data(user=user, variables=variables)`.

        :param category: category of the bare data
        :param user: user adding the data to the collection
        :param workflow: workflow adding the data to the collection
        :param data: per-item data; the specific collection may also use
          this to compute the item name
        :param name: set name of new collection item (implementations may
          ignore this if they compute their own names)
        :param replace: if True, replace an existing similar item
        :param created_at: if set, mark the new item as having been created
          at this timestamp rather than now
        :param replaced_by: if set, mark the new item as having been
          replaced by this more-recently-created item
        """
        if (
            self.VALID_BARE_DATA_CATEGORIES is not None
            and category not in self.VALID_BARE_DATA_CATEGORIES
        ):
            raise ItemAdditionError(
                f'Bare data category "{category}" '
                f'not supported by the collection'
            )
        if isinstance(data, BaseArtifactDataModel):
            data = model_to_json_serializable_dict(data, exclude_unset=True)

        return self.do_add_bare_data(
            category,
            user=user,
            workflow=workflow,
            data=data,
            name=name,
            replace=replace,
            created_at=created_at,
            replaced_by=replaced_by,
        )

    def add_artifact(
        self,
        artifact: Artifact,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,
        name: str | None = None,
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """
        Add the artifact to the managed collection.

        Verify that the artifact can be added (e.g. artifact category is valid)
        and call
        `self.do_add_artifact(artifact, user=user, variables=variables)`.

        :param artifact: artifact to add
        :param user: user adding the artifact to the collection
        :param workflow: workflow adding the artifact to the collection
        :param variables: additional variables passed down to the specific
          collection's :py:meth:`do_add_artifact`, which may be used to
          compute per-item data
        :param name: set name of new collection item (implementations may
          ignore this if they compute their own names)
        :param replace: if True, replace an existing similar item
        :param created_at: if set, mark the new item as having been created
          at this timestamp rather than now
        :param replaced_by: if set, mark the new item as having been
          replaced by this more-recently-created item
        """
        if (
            self.VALID_ARTIFACT_CATEGORIES is not None
            and artifact.category not in self.VALID_ARTIFACT_CATEGORIES
        ):
            raise ItemAdditionError(
                f'Artifact category "{artifact.category}" '
                f'not supported by the collection'
            )

        return self.do_add_artifact(
            artifact,
            user=user,
            workflow=workflow,
            variables=variables,
            name=name,
            replace=replace,
            created_at=created_at,
            replaced_by=replaced_by,
        )

    def add_collection(
        self,
        collection: Collection,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,
        name: str | None = None,
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """
        Add the collection to the managed collection.

        Verify that the collection can be added (e.g. collection category
        is valid) and call
        `self.do_add_collection(collection, user=user, variables=variables)`.

        :param collection: collection to add
        :param user: user adding the collection
        :param workflow: workflow adding the collection to the collection
        :param variables: additional variables passed down to the specific
          collection's :py:meth:`do_add_collection`, which may be used to
          compute per-item data
        :param name: set name of new collection item (implementations may
          ignore this if they compute their own names)
        :param replace: if True, replace an existing similar item
        :param created_at: if set, mark the new item as having been created
          at this timestamp rather than now
        :param replaced_by: if set, mark the new item as having been
          replaced by this more-recently-created item
        """
        if (
            self.VALID_COLLECTION_CATEGORIES is not None
            and collection.category not in self.VALID_COLLECTION_CATEGORIES
        ):
            raise ItemAdditionError(
                f'Collection category "{collection.category}" '
                f'not supported by the collection'
            )

        return self.do_add_collection(
            collection,
            user=user,
            workflow=workflow,
            variables=variables,
            name=name,
            replace=replace,
            created_at=created_at,
            replaced_by=replaced_by,
        )

    def remove_item(
        self,
        item: CollectionItem,
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """
        Remove an item from the managed collection.

        Verify that the item can be removed and call
        self.do_remove_item(user=user).

        :param item: item to remove from the collection
        :param user: user removing the item from the collection
        :param workflow: workflow removing the item from the collection
        """
        # Check that the item can be removed
        # raise ItemRemovalError() if needed
        self.do_remove_item(item, user=user, workflow=workflow)

    def remove_items_by_name(
        self,
        name: str,
        child_types: Iterable[CollectionItem.Types],
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Remove items from the collection by name."""
        for item in CollectionItem.objects.active().filter(
            parent_collection=self.collection,
            child_type__in=child_types,
            name=name,
        ):
            self.remove_item(item, user=user, workflow=workflow)

    def lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem.

        For example, a caller could do::

          collection_manager.lookup(
              "match:codename=bookworm:architecture=amd64:variant=apt"
          )

        :param query: `name:NAME` to find an active item with the given
          name.  Specific collection implementations may define other valid
          queries.
        :return: a single CollectionItem, or None if the query is in a
          recognized format but does not match an item.
        :raise LookupError: the query contains an unknown lookup type.
        """
        if m := re.match(r"^name:(.+)$", query):
            try:
                return CollectionItem.objects.get(
                    parent_collection=self.collection,
                    removed_at__isnull=True,
                    name=m.group(1),
                )
            except CollectionItem.DoesNotExist:
                return None
        return self.do_lookup(query)

    def lookup_filter(
        self,
        key: str,
        value: LookupSingle | LookupMultiple,
        *,
        workspace: Workspace,
        user: User | AnonymousUser,
        workflow_root: WorkRequest | None = None,
    ) -> Q:
        """
        Return :py:class:`CollectionItem` conditions for a lookup filter.

        :param key: name of the lookup filter
        :param value: parameter passed to the specific lookup filter
        :param workspace: workspace to perform the lookup in
        :param user: user performing the lookup
        :param workflow_root: root of the workflow performing the lookup, if
          any
        :return: a Django :py:class:`Q` object with conditions for this lookup
          filter.
        :raise LookupError: the query contains an unknown lookup filter type.
        """
        return self.do_lookup_filter(
            key,
            value,
            workspace=workspace,
            user=user,
            workflow_root=workflow_root,
        )

    def do_add_bare_data(
        self,
        category: BareDataCategory,  # noqa: U100
        *,
        user: User,  # noqa: U100
        workflow: WorkRequest | None,  # noqa: U100
        data: dict[str, Any] | None = None,  # noqa: U100
        name: str | None = None,  # noqa: U100
        replace: bool = False,  # noqa: U100
        created_at: datetime | None = None,  # noqa: U100
        replaced_by: CollectionItem | None = None,  # noqa: U100
    ) -> CollectionItem:  # pragma: no cover
        """
        Add bare data into the managed collection.

        Called by add_bare_data().

        :raise ItemAdditionError: the item cannot be added: breaks
          collection's constraints.
        """
        raise ItemAdditionError(
            f'Cannot add bare data into "{self.COLLECTION_CATEGORY}"'
        )

    def do_add_artifact(
        self,
        artifact: Artifact,  # noqa: U100
        *,
        user: User,  # noqa: U100
        workflow: WorkRequest | None = None,  # noqa: U100
        variables: dict[str, Any] | None = None,  # noqa: U100
        name: str | None = None,  # noqa: U100
        replace: bool = False,  # noqa: U100
        created_at: datetime | None = None,  # noqa: U100
        replaced_by: CollectionItem | None = None,  # noqa: U100
    ) -> CollectionItem:  # pragma: no cover
        """
        Add the artifact into the managed collection.

        Called by add_artifact().

        :raise ItemAdditionError: the artifact cannot be added: breaks
          collection's constraints.
        """
        raise ItemAdditionError(
            f'Cannot add artifacts into "{self.COLLECTION_CATEGORY}"'
        )

    def do_add_collection(
        self,
        collection: Collection,  # noqa: U100
        *,
        user: User,  # noqa: U100
        workflow: WorkRequest | None = None,  # noqa: U100
        variables: dict[str, Any] | None = None,  # noqa: U100
        name: str | None = None,  # noqa: U100
        replace: bool = False,  # noqa: U100
        created_at: datetime | None = None,  # noqa: U100
        replaced_by: CollectionItem | None = None,  # noqa: U100
    ) -> CollectionItem:
        """
        Add the collection into the managed collection.

        Called by add_collection().

        :raise ItemAdditionError: the collection cannot be added: breaks
          collection's constraints.
        """
        raise ItemAdditionError(
            f'Cannot add collections into "{self.COLLECTION_CATEGORY}"'
        )

    def do_remove_item(
        self,
        item: CollectionItem,  # noqa: U100
        *,
        user: User | None = None,  # noqa: U100
        workflow: WorkRequest | None = None,  # noqa: U100
    ) -> None:  # pragma: no cover
        """
        Remove an item from the managed collection.

        Called by remove_item().

        :raise: ItemRemovalError: the item cannot be removed: breaks
          collection's constraints.
        """
        raise ItemRemovalError(
            f'Cannot remove bare data from "{self.COLLECTION_CATEGORY}"'
        )

    def do_lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem.

        Called by lookup().

        :raise LookupError: the query contains an unknown lookup type.
        """
        raise LookupError(f'Unexpected lookup format: "{query}"')

    def do_lookup_filter(
        self,
        key: str,
        value: LookupSingle | LookupMultiple,  # noqa: U100
        *,
        workspace: Workspace,  # noqa: U100
        user: User | AnonymousUser,  # noqa: U100
        workflow_root: WorkRequest | None = None,  # noqa: U100
    ) -> Q:
        """
        Return :py:class:`CollectionItem` conditions for a lookup filter.

        Called by lookup_filter().

        :raise LookupError: the query contains an unknown lookup filter type.
        """
        raise LookupError(f'Unexpected lookup filter format: "{key}"')
