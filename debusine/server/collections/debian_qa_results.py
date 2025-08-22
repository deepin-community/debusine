# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debian:qa-results collections."""

import re
from datetime import datetime
from typing import Any

from django.db import IntegrityError
from django.db.models import Q, Window
from django.db.models.fields.json import KT
from django.db.models.functions import Rank
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianQAResult,
)
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import Artifact, CollectionItem, User, WorkRequest
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)


class DebianQAResultsManager(CollectionManagerInterface):
    """Manage collection of category debian:qa-results."""

    COLLECTION_CATEGORY = CollectionCategory.QA_RESULTS
    VALID_BARE_DATA_CATEGORIES = frozenset({BareDataCategory.QA_RESULT})
    VALID_ARTIFACT_CATEGORIES = frozenset(
        {
            ArtifactCategory.AUTOPKGTEST,
            ArtifactCategory.BLHC,
            ArtifactCategory.LINTIAN,
        }
    )

    def _expire(
        self,
        *,
        user: User,
        workflow: WorkRequest | None,
        exclude: set[CollectionItem],
    ) -> None:
        """Expire old items from the collection."""
        # For each task/package/architecture combination, keep a configured
        # number of most recent entries.  Clean up other matching items.
        old_items = self.collection.child_items.active()
        rank = Window(
            Rank(),
            partition_by=(
                KT("data__task_name"),
                KT("data__package"),
                KT("data__architecture"),
            ),
            order_by="-data__timestamp",
        )
        most_recent = old_items.annotate(rank=rank).filter(
            rank__lte=self.collection.data.get("old_items_to_keep", 5)
        )
        old_items.exclude(id__in={item.id for item in exclude}).exclude(
            id__in=most_recent.values("id")
        ).update(
            removed_by_user=user,
            removed_by_workflow=workflow,
            removed_at=timezone.now(),
        )

    def do_add_bare_data(
        self,
        category: BareDataCategory,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        data: dict[str, Any] | None = None,
        name: str | None = None,  # noqa: U100
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """Add bare data into the managed collection."""
        if data is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.QA_RESULTS} requires data"
            )
        if name is not None:
            raise ItemAdditionError(
                f"Cannot use an explicit item name when adding to "
                f"{CollectionCategory.QA_RESULTS}"
            )

        package = data["package"]
        version = data["version"]
        architecture = data["architecture"]
        timestamp = data["timestamp"]
        work_request_id = data["work_request_id"]
        work_request = WorkRequest.objects.get(id=work_request_id)

        qa_result = DebianQAResult(
            task_name=work_request.task_name,
            package=package,
            version=version,
            architecture=architecture,
            timestamp=timestamp,
            work_request_id=work_request_id,
            result=WorkRequest.Results(work_request.result),
        )

        name_elements = [
            work_request.task_name,
            package,
            version,
            architecture,
            str(work_request_id),
        ]
        name = "_".join(name_elements)

        if replace:
            self.remove_items_by_name(
                name=name,
                child_types=[CollectionItem.Types.BARE],
                user=user,
                workflow=workflow,
            )

        try:
            item = CollectionItem.objects.create_from_bare_data(
                category,
                parent_collection=self.collection,
                name=name,
                data=model_to_json_serializable_dict(qa_result),
                created_at=created_at,
                created_by_user=user,
                created_by_workflow=workflow,
                replaced_by=replaced_by,
            )
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

        # Run the expiry logic over the whole collection, by way of a
        # crash-only design: if something went wrong in the past, or if we
        # change the expiry rules, this will catch up.  If we need to
        # optimize this later, adding some good indexes (with the aid of
        # production-scale data) will probably be enough.
        self._expire(user=user, workflow=workflow, exclude={item})

        return item

    def do_add_artifact(
        self,
        artifact: Artifact,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,
        name: str | None = None,  # noqa: U100
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """Add the artifact into the managed collection."""
        if variables is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.QA_RESULTS} requires variables"
            )
        if name is not None:
            raise ItemAdditionError(
                f"Cannot use an explicit item name when adding to "
                f"{CollectionCategory.QA_RESULTS}"
            )

        package = variables["package"]
        version = variables["version"]
        architecture = variables["architecture"]
        timestamp = variables["timestamp"]
        work_request_id = variables["work_request_id"]
        work_request = WorkRequest.objects.get(id=work_request_id)

        qa_result = DebianQAResult(
            task_name=work_request.task_name,
            package=package,
            version=version,
            architecture=architecture,
            timestamp=timestamp,
            work_request_id=work_request_id,
            result=WorkRequest.Results(work_request.result),
        )

        name_elements = [
            work_request.task_name,
            package,
            version,
            architecture,
            str(work_request_id),
        ]
        name = "_".join(name_elements)

        if replace:
            self.remove_items_by_name(
                name=name,
                child_types=[
                    CollectionItem.Types.BARE,
                    CollectionItem.Types.ARTIFACT,
                ],
                user=user,
                workflow=workflow,
            )

        try:
            item = CollectionItem.objects.create_from_artifact(
                artifact,
                parent_collection=self.collection,
                name=name,
                data=model_to_json_serializable_dict(qa_result),
                created_at=created_at,
                created_by_user=user,
                created_by_workflow=workflow,
                replaced_by=replaced_by,
            )
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

        self._expire(user=user, workflow=workflow, exclude={item})

        return item

    def do_remove_item(
        self,
        item: CollectionItem,
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Remove an item from the collection."""
        item.removed_by_user = user
        item.removed_by_workflow = workflow
        item.removed_at = timezone.now()
        item.save()

    def do_lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem based on the query.

        :param query: `latest:TASKNAME_PACKAGE_ARCHITECTURE`.  If more than
          one possible CollectionItem matches the query: return the one with
          the highest timestamp, tie-breaking on the one that was most
          recently added to the collection.
        """
        query_filter = Q(parent_collection=self.collection)
        order_by = ["created_at"]

        if m := re.match(r"^latest:([^_]+)_([^_]+)_([^_]+)$", query):
            query_filter &= Q(
                data__task_name=m.group(1),
                data__package=m.group(2),
                data__architecture=m.group(3),
            )
            order_by.insert(0, "data__timestamp")
        else:
            raise LookupError(f'Unexpected lookup format: "{query}"')

        try:
            return (
                CollectionItem.objects.active()
                .filter(query_filter)
                .latest(*order_by)
            )
        except CollectionItem.DoesNotExist:
            return None
