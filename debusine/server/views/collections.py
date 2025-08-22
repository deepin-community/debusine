# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: collections."""

import heapq
import logging
import re
from functools import cached_property, total_ordering
from typing import Any

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineTaskConfiguration,
)
from debusine.db.context import context
from debusine.db.models import Collection, CollectionItem
from debusine.server.collections.debusine_task_configuration import (
    DebusineTaskConfigurationManager,
)
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    TaskConfigurationCollectionContents,
    TaskConfigurationCollectionContentsSerializer,
    TaskConfigurationCollectionUpdateResultsSerializer,
)
from debusine.server.views.base import BaseAPIView
from debusine.server.views.rest import IsTokenUserAuthenticated

logger = logging.getLogger(__name__)


@total_ordering
class ComparableItem:
    """Sortable container for DebusineTaskConfiguration entries."""

    def __init__(
        self, item: DebusineTaskConfiguration, name: str | None = None
    ) -> None:
        """Store an item together with its database name."""
        self.item = item
        # Store the database name together with the item, for those items that
        # are in the database
        self.name = name

    @cached_property
    def _sort_key(self) -> tuple[Any, ...]:
        item = self.item
        return (
            item.task_type or "",
            item.task_name or "",
            item.subject or "",
            item.context or "",
            item.template or "",
            item.use_templates or [],
            item.delete_values or [],
            sorted(item.default_values.items()) if item.default_values else [],
            (
                sorted(item.override_values.items())
                if item.override_values
                else []
            ),
            item.lock_values or [],
            item.comment or "",
        )

    def __eq__(self, other: Any) -> bool:
        """Delegate equality to contained items."""
        return bool(self.item == other.item)

    def __lt__(self, other: Any) -> bool:
        """
        Provide ordering.

        Ordering is arbitrary, and provided to allow to use
        DebusineTaskConfiguration items in heapq or similar structures.
        """
        if not isinstance(other, ComparableItem):
            return NotImplemented
        return bool(self._sort_key < other._sort_key)


class TaskConfigurationCollectionView(BaseAPIView):
    """View used to fetch and update a task configuration collection."""

    permission_classes = [IsTokenUserAuthenticated]
    parser_classes = [JSONParser]

    def _get_collection(self, workspace: str, name: str) -> Collection:
        """Lookup the task configuration collection."""
        self.set_current_workspace(workspace)
        try:
            collection = context.require_workspace().get_collection(
                user=context.require_user(),
                category=CollectionCategory.TASK_CONFIGURATION,
                name=name,
            )
        except Collection.DoesNotExist:
            raise DebusineAPIException(
                title="Collection not found",
                detail=(
                    f"Task configuration collection {name!r} does not exist"
                    f" in workspace {workspace!r}"
                ),
                status_code=status.HTTP_404_NOT_FOUND,
            )

        self.enforce(collection.can_display)
        return collection

    def get(
        self, request: Request, workspace: str, name: str  # noqa: U100
    ) -> Response:
        """Retrieve the contents of the collection."""
        collection = self._get_collection(workspace, name)

        items: list[DebusineTaskConfiguration] = []
        for child in collection.child_items.active().filter(
            child_type=CollectionItem.Types.BARE
        ):
            items.append(DebusineTaskConfiguration(**child.data))

        contents = TaskConfigurationCollectionContents(
            collection=collection, items=items
        )

        return Response(
            TaskConfigurationCollectionContentsSerializer(contents).data,
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _diff_items(
        collection: Collection, items: list[DebusineTaskConfiguration]
    ) -> tuple[list[str], list[DebusineTaskConfiguration], int]:
        """
        Compare the collection contents against a new list of items.

        :returns: a tuple (removed, added), where removed lists the item IDs to
                  remove, and added the DebusineTaskConfiguration items to add.
        """
        # Load the existing items
        old: list[ComparableItem] = []
        for child in collection.child_items.active().filter(
            child_type=CollectionItem.Types.BARE
        ):
            old.append(
                ComparableItem(
                    item=DebusineTaskConfiguration(**child.data),
                    name=child.name,
                )
            )

        # Wrap the new items for comparison
        new: list[ComparableItem] = [ComparableItem(item=i) for i in items]

        # DebusineTaskConfiguration objects are not hashable, so we cannot use
        # sets. We work around that by making them comparable, and using heapq
        heapq.heapify(old)
        heapq.heapify(new)

        removed: list[str] = []
        added: list[DebusineTaskConfiguration] = []
        unchanged: int = 0
        while old and new:
            if old[0] == new[0]:
                heapq.heappop(old)
                heapq.heappop(new)
                unchanged += 1
            elif old[0] < new[0]:
                assert old[0].name is not None
                removed.append(old[0].name)
                heapq.heappop(old)
            else:
                added.append(new[0].item)
                heapq.heappop(new)

        for i in old:
            assert i.name is not None
            removed.append(i.name)
        added += [i.item for i in new]

        return removed, added, unchanged

    def _validate_git_commit(self, value: Any, name: str) -> str:
        if not isinstance(value, str):
            raise DebusineAPIException(
                title="Invalid git commit",
                detail=f"{name} is not a string",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if not re.match(r"^[0-9a-f]{40,64}$", value):
            raise DebusineAPIException(
                title="Invalid git commit",
                detail=f"{name} is malformed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return value

    def post(self, request: Request, workspace: str, name: str) -> Response:
        """Update the contents of the collection."""
        collection = self._get_collection(workspace, name)
        self.enforce(collection.workspace.can_edit_task_configuration)
        dry_run = request.data.pop("dry_run", False)
        serializer = TaskConfigurationCollectionContentsSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)
        submitted_collection = serializer.validated_data["collection"]

        if submitted_collection["id"] != collection.pk:
            raise DebusineAPIException(
                title="Posted collection mismatch",
                detail=(
                    f"Data posted for collection {submitted_collection['id']}"
                    f" to update collection {collection.pk}"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if submitted_collection["name"] != collection.name:
            raise DebusineAPIException(
                title="Posted collection mismatch",
                detail=(
                    "Data posted for collection"
                    f" {submitted_collection['name']!r}"
                    f" to update collection {collection.name!r}"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Update git commit in collection data
        if (
            git_commit := submitted_collection["data"].get("git_commit")
        ) is not None:
            git_commit = self._validate_git_commit(git_commit, "git_commit")
            collection.data["git_commit"] = git_commit
        else:
            collection.data.pop("git_commit", None)
        collection.save()

        # We do not update existing items, as that would rewrite collection
        # history. If an item has been changed, we mark the old version as
        # deleted and create a new version
        removed, added, unchanged = self._diff_items(
            collection, serializer.validated_data["items"]
        )

        # Update collection
        manager = DebusineTaskConfigurationManager(collection=collection)
        user = context.require_user()
        assert user.is_authenticated
        if not dry_run:
            for name in removed:
                manager.remove_items_by_name(
                    name=name,
                    child_types=[CollectionItem.Types.BARE],
                    user=user,
                )

        # Count updates as the number of added names that are also in removed
        updated: int = 0

        for item in added:
            if not dry_run:
                name = manager.add_bare_data(
                    BareDataCategory.TASK_CONFIGURATION, user=user, data=item
                ).name
            else:
                name = item.name()
            if name in removed:
                updated += 1

        return Response(
            TaskConfigurationCollectionUpdateResultsSerializer(
                {
                    "added": len(added) - updated,
                    "removed": len(removed) - updated,
                    "updated": updated,
                    "unchanged": unchanged,
                }
            ).data,
            status=status.HTTP_200_OK,
        )
