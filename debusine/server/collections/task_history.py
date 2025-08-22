# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Manager for debusine:task-history collections."""

import re
from datetime import datetime
from typing import Any, TypedDict
from urllib.parse import quote, unquote

from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.db.models import IntegerField, Q, QuerySet, TextField, Value, Window
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce, Rank
from django.db.models.lookups import In
from django.utils import timezone

from debusine.artifacts.models import BareDataCategory, CollectionCategory
from debusine.db.models import CollectionItem, User, WorkRequest, Workspace
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)
from debusine.server.collections.lookup import lookup_multiple
from debusine.tasks.models import LookupMultiple, LookupSingle


class TaskHistoryManager(CollectionManagerInterface):
    """Manage collection of category debusine:task-history."""

    COLLECTION_CATEGORY = CollectionCategory.TASK_HISTORY
    VALID_BARE_DATA_CATEGORIES = frozenset(
        {BareDataCategory.HISTORICAL_TASK_RUN}
    )

    def do_add_bare_data(
        self,
        category: BareDataCategory,
        *,
        user: User,
        workflow: WorkRequest | None,
        data: dict[str, Any] | None = None,
        name: str | None = None,  # noqa: U100
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """
        Add bare data into the managed collection.

        :param category: category of the bare data
        :param user: user adding the data to the collection
        :param workflow: workflow adding the data to the collection
        :param data: per-item data, used to compute the item name
        :param name: not used, must be None
        :param replace: if True, replace an existing similar item
        """
        if data is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.TASK_HISTORY} requires data"
            )
        if name is not None:
            raise ItemAdditionError(
                f"Cannot use an explicit item name when adding to "
                f"{CollectionCategory.TASK_HISTORY}"
            )

        work_request_id = data["work_request_id"]
        task_type = data["task_type"]
        task_name = data["task_name"]
        quoted_subject = quote(data.get("subject") or "")
        quoted_context = quote(data.get("context") or "")

        name_elements = [
            task_type,
            task_name,
            quoted_subject,
            quoted_context,
            str(work_request_id),
        ]
        name = ":".join(name_elements)

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
                data=data,
                created_at=created_at,
                created_by_user=user,
                created_by_workflow=workflow,
                replaced_by=replaced_by,
            )
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

        # For each subject/context combination, keep the last success, the last
        # failure, and a configured number of most recent entries.  Clean up
        # other matching items.
        old_items = CollectionItem.objects.filter(
            parent_collection=self.collection,
            child_type=CollectionItem.Types.BARE,
            category=category,
            data__task_type=task_type,
            data__task_name=task_name,
        )
        subject_context_rank = Window(
            Rank(),
            partition_by=(KT("data__subject"), KT("data__context")),
            order_by="-created_at",
        )
        last_success = (
            old_items.filter(data__result=WorkRequest.Results.SUCCESS)
            .annotate(subject_context_rank=subject_context_rank)
            .filter(subject_context_rank=1)
        )
        last_failure = (
            old_items.filter(
                data__result__in={
                    WorkRequest.Results.FAILURE,
                    WorkRequest.Results.ERROR,
                }
            )
            .annotate(subject_context_rank=subject_context_rank)
            .filter(subject_context_rank=1)
        )
        most_recent = old_items.annotate(
            subject_context_rank=subject_context_rank
        ).filter(
            subject_context_rank__lte=self.collection.data.get(
                "old_items_to_keep", 5
            )
        )
        for old_item in (
            old_items.exclude(id=item.id)
            .exclude(id__in=last_success.values("id"))
            .exclude(id__in=last_failure.values("id"))
            .exclude(id__in=most_recent.values("id"))
        ):
            self.remove_item(old_item, user=user, workflow=workflow)

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

        :param query: `last-entry:TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT`,
          `last-success:TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT`, or
          `last-failure:TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT`.
        :raise LookupError: the query contains an unknown lookup format.
        """
        objects = CollectionItem.objects.active()
        query_filter = Q(
            parent_collection=self.collection,
            child_type=CollectionItem.Types.BARE,
        )

        if m := re.match(
            r"^(last-entry|last-success|last-failure):(.+?):(.+?):(.*?):(.*)$",
            query,
        ):
            objects = objects.annotate(
                subject=Coalesce(
                    KT("data__subject"), Value("", output_field=TextField())
                ),
                context=Coalesce(
                    KT("data__context"), Value("", output_field=TextField())
                ),
            )
            query_filter &= Q(
                data__task_type=m.group(2),
                data__task_name=m.group(3),
                subject=unquote(m.group(4)),
                context=unquote(m.group(5)),
            )
            match m.group(1):
                case "last-entry":
                    pass
                case "last-success":
                    query_filter &= Q(data__result=WorkRequest.Results.SUCCESS)
                case "last-failure":
                    query_filter &= Q(
                        data__result__in={
                            WorkRequest.Results.FAILURE,
                            WorkRequest.Results.ERROR,
                        }
                    )
                case _ as unreachable:
                    raise AssertionError(
                        f'Unexpected lookup format: "{unreachable}" (expected '
                        f'"last-entry", "last-success", or "last-failure")'
                    )
            return objects.filter(query_filter).order_by("created_at").last()
        else:
            raise LookupError(
                f'Unexpected lookup format: "{query}" (expected '
                f'"last-entry", "last-success", or "last-failure")'
            )

    def do_lookup_filter(
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

        :param key: For `same_work_request`, return conditions matching task
          runs for the same work request as any of the resulting artifacts.
          For `same_workflow`, return conditions matching task runs for work
          requests from the same workflow as any of the resulting artifacts.
        :raise LookupError: the query contains an unknown lookup filter
          format.
        """
        if key in {"same_work_request", "same_workflow"}:
            if isinstance(value, LookupSingle):
                value = LookupMultiple.parse_obj([value])
            items = lookup_multiple(
                value, workspace, user=user, workflow_root=workflow_root
            )

            class IDValues(TypedDict):
                id: int

            work_request_ids: set[int] | QuerySet[WorkRequest, IDValues] = {
                item.artifact.created_by_work_request_id
                for item in items
                if (
                    item.artifact is not None
                    and item.artifact.created_by_work_request_id is not None
                )
            }
            if key == "same_workflow":
                work_request_ids = WorkRequest.objects.filter(
                    parent__children__in=work_request_ids
                ).values("id")
            return Q(
                In(
                    Cast("data__work_request_id", IntegerField()),
                    work_request_ids,
                )
            )
        else:
            raise LookupError(
                f'Unexpected lookup filter format: "{key}" (expected '
                f'"same_work_request" or "same_workflow")'
            )
