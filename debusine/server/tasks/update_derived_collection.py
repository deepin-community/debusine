# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Server-side task to update a derived collection."""

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum, auto
from functools import cached_property
from typing import Any, TypeVar, assert_never

from django.db.models import QuerySet

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import CollectionCategory
from debusine.client.models import LookupChildType
from debusine.db.models import Collection, CollectionItem, WorkRequest
from debusine.server.collections.lookup import lookup_single
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import UpdateDerivedCollectionData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData

TD = TypeVar("TD", bound=UpdateDerivedCollectionData)


class DerivedCollectionChangeKind(Enum):
    """A kind of change needed to a derived collection."""

    ADD = auto()
    REPLACE = auto()
    REMOVE = auto()


@dataclass
class DerivedCollectionChange:
    """A change needed to a derived collection."""

    kind: DerivedCollectionChangeKind
    name: str
    base_item_ids: set[int] = pydantic.Field(default_factory=set)


class UpdateDerivedCollection(
    ABC, BaseServerTask[TD, BaseDynamicTaskData], DefaultDynamicData[TD]
):
    """Task that updates a derived collection from a base collection."""

    TASK_VERSION = 1

    BASE_COLLECTION_CATEGORIES: tuple[CollectionCategory]
    DERIVED_COLLECTION_CATEGORIES: tuple[CollectionCategory]

    @cached_property
    def base_collection(self) -> Collection:
        """Return the base collection."""
        assert self.workspace is not None
        assert self.work_request is not None

        base_collection = lookup_single(
            self.data.base_collection,
            self.workspace,
            user=self.work_request.created_by,
            default_category=(
                self.BASE_COLLECTION_CATEGORIES[0]
                if len(self.BASE_COLLECTION_CATEGORIES) == 1
                else None
            ),
            expect_type=LookupChildType.COLLECTION,
        ).collection
        if base_collection.category not in self.BASE_COLLECTION_CATEGORIES:
            raise ValueError(
                f"Base collection has category '{base_collection.category}'; "
                f"expected one of "
                f"{[str(c) for c in self.BASE_COLLECTION_CATEGORIES]}"
            )
        return base_collection

    @cached_property
    def derived_collection(self) -> Collection:
        """Return the derived collection."""
        assert self.workspace is not None
        assert self.work_request is not None

        derived_collection = lookup_single(
            self.data.derived_collection,
            self.workspace,
            user=self.work_request.created_by,
            default_category=(
                self.DERIVED_COLLECTION_CATEGORIES[0]
                if len(self.DERIVED_COLLECTION_CATEGORIES) == 1
                else None
            ),
            expect_type=LookupChildType.COLLECTION,
        ).collection
        if (
            derived_collection.category
            not in self.DERIVED_COLLECTION_CATEGORIES
        ):
            raise ValueError(
                f"Derived collection has category "
                f"'{derived_collection.category}'; expected one of "
                f"{[str(c) for c in self.DERIVED_COLLECTION_CATEGORIES]}"
            )
        return derived_collection

    def find_relevant_base_items(self) -> QuerySet[CollectionItem]:
        """
        Find the relevant active items in the base collection.

        Concrete subclasses may filter the returned query set further.
        """
        return CollectionItem.active_objects.filter(
            parent_collection=self.base_collection
        )

    def find_relevant_derived_items(self) -> QuerySet[CollectionItem]:
        """
        Find the relevant active items in the derived collection.

        Concrete subclasses may filter the returned query set further.
        """
        return CollectionItem.active_objects.filter(
            parent_collection=self.derived_collection
        )

    @abstractmethod
    def find_expected_derived_items(
        self, relevant_base_items: QuerySet[CollectionItem]
    ) -> dict[str, set[int]]:
        """
        Find the derived item names that should exist given these base items.

        Concrete subclasses must implement this.  The returned dictionary
        maps derived item names to sets of base item IDs.
        """

    def categorize_derived_items(
        self,
        relevant_base_items: QuerySet[CollectionItem],
        relevant_derived_items: QuerySet[CollectionItem],
    ) -> Generator[DerivedCollectionChange, None, None]:
        """Yield changes that need to be made to the derived collection."""
        # Build a set of base item IDs for each currently-existing derived
        # item.
        current_derived_items = {
            item.name: set(item.data.get("derived_from", []))
            for item in relevant_derived_items
        }
        # Build a set of base item IDs for each expected derived item.
        expected_derived_items = self.find_expected_derived_items(
            relevant_base_items
        )
        # Process expected items that don't already exist.
        for name in sorted(
            expected_derived_items.keys() - current_derived_items.keys()
        ):
            yield DerivedCollectionChange(
                kind=DerivedCollectionChangeKind.ADD,
                name=name,
                base_item_ids=expected_derived_items[name],
            )
        # Check if existing derived items need refreshing.
        for name in sorted(
            current_derived_items.keys() & expected_derived_items.keys()
        ):
            if (
                current_derived_items[name] != expected_derived_items[name]
                or self.data.force
            ):
                yield DerivedCollectionChange(
                    kind=DerivedCollectionChangeKind.REPLACE,
                    name=name,
                    base_item_ids=expected_derived_items[name],
                )
        # Process existing derived items that should be deleted.
        for name in sorted(
            current_derived_items.keys() - expected_derived_items.keys()
        ):
            yield DerivedCollectionChange(
                kind=DerivedCollectionChangeKind.REMOVE, name=name
            )

    @abstractmethod
    def make_child_work_request(
        self,
        base_item_ids: set[int],
        child_task_data: dict[str, Any] | None,
        force: bool,
    ) -> WorkRequest | None:
        """
        Make a work request to create a new derived item.

        Concrete subclasses must implement this.  They must do nothing if a
        work request with the same parameters already exists, unless `force`
        is True.
        """

    def _execute(self) -> bool:
        """Update the derived collection."""
        relevant_base_items = self.find_relevant_base_items()
        relevant_derived_items = self.find_relevant_derived_items()
        for change in self.categorize_derived_items(
            relevant_base_items=relevant_base_items,
            relevant_derived_items=relevant_derived_items,
        ):
            match change.kind:
                case (
                    DerivedCollectionChangeKind.ADD
                    | DerivedCollectionChangeKind.REPLACE
                ):
                    self.make_child_work_request(
                        base_item_ids=change.base_item_ids,
                        child_task_data=self.data.child_task_data,
                        force=self.data.force,
                    )
                case DerivedCollectionChangeKind.REMOVE:
                    relevant_derived_items.filter(name=change.name).delete()
                case _ as unreachable:
                    assert_never(unreachable)

        return True
