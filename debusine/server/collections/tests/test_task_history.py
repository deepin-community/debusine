# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for TaskHistoryManager."""

from datetime import datetime, timedelta

from django.db.models import IntegerField, Q
from django.db.models.functions import Cast
from django.db.models.lookups import In
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebusineHistoricalTaskRun,
    RuntimeStatistics,
    TaskTypes,
)
from debusine.client.models import (
    LookupChildType,
    model_to_json_serializable_dict,
)
from debusine.db.models import Collection, CollectionItem, WorkRequest
from debusine.db.playground import scenarios
from debusine.server.collections.base import ItemAdditionError
from debusine.server.collections.lookup import LookupResult, lookup_multiple
from debusine.server.collections.task_history import TaskHistoryManager
from debusine.tasks.models import LookupMultiple
from debusine.test.django import TestCase


class TaskHistoryManagerTests(TestCase):
    """Tests for TaskHistoryManager."""

    scenario = scenarios.DefaultContext()
    workflow: WorkRequest
    collection: Collection
    collection_lookup: str
    manager: TaskHistoryManager

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.workflow = cls.playground.create_workflow()
        cls.collection = cls.scenario.workspace.get_singleton_collection(
            user=cls.scenario.user, category=CollectionCategory.TASK_HISTORY
        )
        cls.collection_lookup = f"_@{CollectionCategory.TASK_HISTORY}"
        cls.manager = TaskHistoryManager(collection=cls.collection)

    def create_historical_task_run(
        self,
        task_type: TaskTypes,
        task_name: str,
        subject: str | None,
        context: str | None,
        work_request_id: int,
        result: WorkRequest.Results = WorkRequest.Results.SUCCESS,
        collection: Collection | None = None,
    ) -> CollectionItem:
        """Create and return a historical task run in a collection."""
        created_at = datetime(2020, 1, 1) + timedelta(seconds=work_request_id)
        return CollectionItem.objects.create_from_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            parent_collection=collection or self.collection,
            name=":".join(
                [
                    task_type,
                    task_name,
                    subject or "",
                    context or "",
                    str(work_request_id),
                ]
            ),
            data=DebusineHistoricalTaskRun(
                task_type=task_type,
                task_name=task_name,
                subject=subject,
                context=context,
                timestamp=int(created_at.timestamp()),
                work_request_id=work_request_id,
                result=result,
                runtime_statistics=RuntimeStatistics(duration=1),
            ),
            created_by_user=self.scenario.user,
            created_by_workflow=self.workflow,
        )

    def test_add_bare_data_no_data(self) -> None:
        """`add_bare_data` requires item data."""
        with self.assertRaisesRegex(
            ItemAdditionError, "Adding to debusine:task-history requires data"
        ):
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
            )

    def test_add_bare_data_refuses_name(self) -> None:
        """`add_bare_data` refuses an explicitly-specified name."""
        with self.assertRaisesRegex(
            ItemAdditionError,
            "Cannot use an explicit item name when adding to "
            "debusine:task-history",
        ):
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
                data=DebusineHistoricalTaskRun(
                    task_type=TaskTypes.WORKER,
                    task_name="sbuild",
                    subject="base-files",
                    context="sid/amd64",
                    timestamp=int(timezone.now().timestamp()),
                    work_request_id=1,
                    result=WorkRequest.Results.SUCCESS,
                    runtime_statistics=RuntimeStatistics(duration=1),
                ),
                name="override",
            )

    def test_add_bare_data_raise_item_addition_error(self) -> None:
        """`add_bare_data` raises an error for duplicate names."""
        data = DebusineHistoricalTaskRun(
            task_type=TaskTypes.WORKER,
            task_name="sbuild",
            subject="base-files",
            context="sid/amd64",
            timestamp=int(timezone.now().timestamp()),
            work_request_id=1,
            result=WorkRequest.Results.SUCCESS,
            runtime_statistics=RuntimeStatistics(duration=1),
        )
        self.manager.add_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
                data=data,
            )

    def test_add_bare_data_different_work_request_ids(self) -> None:
        """Adding bare data items with different work request IDs is OK."""
        items = [
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
                data=DebusineHistoricalTaskRun(
                    task_type=TaskTypes.WORKER,
                    task_name="sbuild",
                    subject="base-files",
                    context="sid/amd64",
                    timestamp=int(timezone.now().timestamp()),
                    work_request_id=work_request_id,
                    result=WorkRequest.Results.SUCCESS,
                    runtime_statistics=RuntimeStatistics(duration=1),
                ),
            )
            for work_request_id in (1, 2)
        ]

        self.assertEqual(
            [item.name for item in items],
            [
                "Worker:sbuild:base-files:sid/amd64:1",
                "Worker:sbuild:base-files:sid/amd64:2",
            ],
        )

    def test_add_bare_data_unset_subject_and_context(self) -> None:
        """`add_bare_data` handles subject=None and context=None."""
        item = self.manager.add_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            user=self.scenario.user,
            workflow=self.workflow,
            data=DebusineHistoricalTaskRun(
                task_type=TaskTypes.WORKER,
                task_name="sbuild",
                subject=None,
                context=None,
                timestamp=int(timezone.now().timestamp()),
                work_request_id=1,
                result=WorkRequest.Results.SUCCESS,
                runtime_statistics=RuntimeStatistics(duration=1),
            ),
        )

        self.assertEqual(item.name, "Worker:sbuild:::1")

    def test_add_bare_data_quotes_subject_and_context(self) -> None:
        """`add_bare_data` URL-encodes subject and context."""
        item = self.manager.add_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            user=self.scenario.user,
            workflow=self.workflow,
            data=DebusineHistoricalTaskRun(
                task_type=TaskTypes.WORKER,
                task_name="sbuild",
                subject="hello:world",
                context="any:amd64:amd64",
                timestamp=int(timezone.now().timestamp()),
                work_request_id=1,
                result=WorkRequest.Results.SUCCESS,
                runtime_statistics=RuntimeStatistics(duration=1),
            ),
        )

        self.assertEqual(
            item.name, "Worker:sbuild:hello%3Aworld:any%3Aamd64%3Aamd64:1"
        )

    def test_add_bare_data_replace(self) -> None:
        """`add_bare_data` can replace an existing bare data item."""
        data = DebusineHistoricalTaskRun(
            task_type=TaskTypes.WORKER,
            task_name="sbuild",
            subject="base-files",
            context="sid/amd64",
            timestamp=int(timezone.now().timestamp()),
            work_request_id=1,
            result=WorkRequest.Results.SUCCESS,
            runtime_statistics=RuntimeStatistics(duration=1),
        )
        item_old = self.manager.add_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
        )

        item_new = self.manager.add_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        serialized_data = model_to_json_serializable_dict(
            data, exclude_unset=True
        )
        item_old.refresh_from_db()
        self.assertEqual(item_old.name, "Worker:sbuild:base-files:sid/amd64:1")
        self.assertEqual(item_old.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_old.data, serialized_data)
        self.assertEqual(item_old.removed_by_user, self.scenario.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(item_new.name, "Worker:sbuild:base-files:sid/amd64:1")
        self.assertEqual(item_new.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_new.data, serialized_data)
        self.assertIsNone(item_new.removed_at)

    def test_add_bare_data_replace_nonexistent(self) -> None:
        """Replacing a nonexistent bare data item is allowed."""
        data = DebusineHistoricalTaskRun(
            task_type=TaskTypes.WORKER,
            task_name="sbuild",
            subject="base-files",
            context="sid/amd64",
            timestamp=int(timezone.now().timestamp()),
            work_request_id=1,
            result=WorkRequest.Results.SUCCESS,
            runtime_statistics=RuntimeStatistics(duration=1),
        )

        item = self.manager.add_bare_data(
            BareDataCategory.HISTORICAL_TASK_RUN,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        self.assertEqual(item.name, "Worker:sbuild:base-files:sid/amd64:1")
        self.assertEqual(item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            item.data, model_to_json_serializable_dict(data, exclude_unset=True)
        )

    def test_add_bare_data_keeps_last_success(self) -> None:
        """`add_bare_data` keeps the last successful run."""
        items: list[CollectionItem] = []
        for task_type, task_name, subject, context in (
            (TaskTypes.WORKER, "sbuild", "base-files", "sid/amd64"),
            (TaskTypes.WORKER, "sbuild", "base-files", "trixie/amd64"),
            (TaskTypes.WORKER, "sbuild", "hello", "sid/amd64"),
            (TaskTypes.SERVER, "aptmirror", "base-files", "sid/amd64"),
        ):
            for i in range(6):
                items.append(
                    self.create_historical_task_run(
                        task_type,
                        task_name,
                        subject,
                        context,
                        len(items) + 1,
                        result=(
                            WorkRequest.Results.SUCCESS
                            if i == 0
                            else WorkRequest.Results.FAILURE
                        ),
                    )
                )

        items.append(
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
                data=DebusineHistoricalTaskRun(
                    task_type=TaskTypes.WORKER,
                    task_name="sbuild",
                    subject="base-files",
                    context="sid/amd64",
                    timestamp=int(timezone.now().timestamp()),
                    work_request_id=len(items) + 1,
                    result=WorkRequest.Results.FAILURE,
                    runtime_statistics=RuntimeStatistics(duration=1),
                ),
            )
        )

        # Matching items are pruned, but the most recent success is kept
        # despite not being within the window of old items to keep.
        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .order_by("id"),
            [items[0], *items[2:]],
        )

    def test_add_bare_data_keeps_last_failure(self) -> None:
        """`add_bare_data` keeps the last failed run."""
        items: list[CollectionItem] = []
        for task_type, task_name, subject, context in (
            (TaskTypes.WORKER, "sbuild", "base-files", "sid/amd64"),
            (TaskTypes.WORKER, "sbuild", "base-files", "trixie/amd64"),
            (TaskTypes.WORKER, "sbuild", "hello", "sid/amd64"),
            (TaskTypes.SERVER, "aptmirror", "base-files", "sid/amd64"),
        ):
            for i in range(6):
                items.append(
                    self.create_historical_task_run(
                        task_type,
                        task_name,
                        subject,
                        context,
                        len(items) + 1,
                        result=(
                            WorkRequest.Results.FAILURE
                            if i == 0
                            else WorkRequest.Results.SUCCESS
                        ),
                    )
                )

        items.append(
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
                data=DebusineHistoricalTaskRun(
                    task_type=TaskTypes.WORKER,
                    task_name="sbuild",
                    subject="base-files",
                    context="sid/amd64",
                    timestamp=int(timezone.now().timestamp()),
                    work_request_id=len(items) + 1,
                    result=WorkRequest.Results.SUCCESS,
                    runtime_statistics=RuntimeStatistics(duration=1),
                ),
            )
        )

        # Matching items are pruned, but the most recent failure is kept
        # despite not being within the window of old items to keep.
        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .order_by("id"),
            [items[0], *items[2:]],
        )

    def test_add_bare_data_old_items_to_keep(self) -> None:
        """The number of old items to keep is configurable."""
        self.collection.data = {"old_items_to_keep": 3}
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER, "sbuild", "base-files", "sid/amd64", i + 1
            )
            for i in range(5)
        ]

        items.append(
            self.manager.add_bare_data(
                BareDataCategory.HISTORICAL_TASK_RUN,
                user=self.scenario.user,
                workflow=self.workflow,
                data=DebusineHistoricalTaskRun(
                    task_type=TaskTypes.WORKER,
                    task_name="sbuild",
                    subject="base-files",
                    context="sid/amd64",
                    timestamp=int(timezone.now().timestamp()),
                    work_request_id=len(items) + 1,
                    result=WorkRequest.Results.SUCCESS,
                    runtime_statistics=RuntimeStatistics(duration=1),
                ),
            )
        )

        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .order_by("id"),
            items[3:],
        )

    def test_remove_item_bare_data(self) -> None:
        """`remove_item` removes a bare data item."""
        item = self.create_historical_task_run(
            TaskTypes.WORKER, "sbuild", "base-files", "sid/amd64", 1
        )

        self.manager.remove_item(
            item, user=self.scenario.user, workflow=self.workflow
        )

        self.assertEqual(item.removed_by_user, self.scenario.user)
        self.assertEqual(item.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item.removed_at)

    def test_lookup_unexpected_format(self) -> None:
        """`lookup` raises `LookupError` for an unexpected format."""
        with self.assertRaisesRegex(
            LookupError,
            r'^Unexpected lookup format: "foo:bar" \(expected '
            r'"last-entry", "last-success", or "last-failure"\)$',
        ):
            self.manager.lookup("foo:bar")

    def test_lookup_return_none(self) -> None:
        """`lookup` returns None if there are no matches."""
        self.assertIsNone(
            self.manager.lookup("last-entry:Worker:sbuild:base-files:sid/amd64")
        )
        self.assertIsNone(
            self.manager.lookup(
                "last-success:Worker:sbuild:base-files:sid/amd64"
            )
        )
        self.assertIsNone(
            self.manager.lookup(
                "last-failure:Worker:sbuild:base-files:sid/amd64"
            )
        )
        self.assertIsNone(self.manager.lookup("name:nonexistent"))

    def test_lookup_last_entry(self) -> None:
        """`lookup` for `last-entry` returns the last matching entry."""
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER, "sbuild", subject, context, work_request_id
            )
            for subject, context, work_request_id in (
                (None, None, 1),
                ("base-files", "sid/amd64", 2),
                ("base-passwd", "sid/amd64", 3),
                ("base-files", "sid/amd64", 4),
                ("hello:world", "any:amd64:amd64", 5),
            )
        ]

        self.assertEqual(
            self.manager.lookup("last-entry:Worker:sbuild::"), items[0]
        )
        self.assertEqual(
            self.manager.lookup(
                "last-entry:Worker:sbuild:base-files:sid/amd64"
            ),
            items[3],
        )
        self.assertEqual(
            self.manager.lookup(
                "last-entry:Worker:sbuild:base-passwd:sid/amd64"
            ),
            items[2],
        )
        self.assertEqual(
            self.manager.lookup(
                "last-entry:Worker:sbuild:hello%3Aworld:any%3Aamd64%3Aamd64"
            ),
            items[4],
        )

    def test_lookup_last_success(self) -> None:
        """`lookup` for `last-success` returns the last matching success."""
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER,
                "sbuild",
                subject,
                context,
                work_request_id,
                result=result,
            )
            for subject, context, work_request_id, result in (
                (None, None, 1, WorkRequest.Results.SUCCESS),
                ("base-files", "sid/amd64", 2, WorkRequest.Results.SUCCESS),
                ("base-passwd", "sid/amd64", 3, WorkRequest.Results.SUCCESS),
                ("base-files", "sid/amd64", 4, WorkRequest.Results.FAILURE),
                (
                    "hello:world",
                    "any:amd64:amd64",
                    5,
                    WorkRequest.Results.SUCCESS,
                ),
            )
        ]

        self.assertEqual(
            self.manager.lookup("last-success:Worker:sbuild::"), items[0]
        )
        self.assertEqual(
            self.manager.lookup(
                "last-success:Worker:sbuild:base-files:sid/amd64"
            ),
            items[1],
        )
        self.assertEqual(
            self.manager.lookup(
                "last-success:Worker:sbuild:hello%3Aworld:any%3Aamd64%3Aamd64"
            ),
            items[4],
        )

    def test_lookup_last_failure(self) -> None:
        """`lookup` for `last-failure` returns the last matching failure."""
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER,
                "sbuild",
                subject,
                context,
                work_request_id,
                result=result,
            )
            for subject, context, work_request_id, result in (
                (None, None, 1, WorkRequest.Results.FAILURE),
                ("base-files", "sid/amd64", 2, WorkRequest.Results.FAILURE),
                ("base-passwd", "sid/amd64", 3, WorkRequest.Results.FAILURE),
                ("base-files", "sid/amd64", 4, WorkRequest.Results.SUCCESS),
                (
                    "hello:world",
                    "any:amd64:amd64",
                    5,
                    WorkRequest.Results.FAILURE,
                ),
            )
        ]

        self.assertEqual(
            self.manager.lookup("last-failure:Worker:sbuild::"), items[0]
        )
        self.assertEqual(
            self.manager.lookup(
                "last-failure:Worker:sbuild:base-files:sid/amd64"
            ),
            items[1],
        )
        self.assertEqual(
            self.manager.lookup(
                "last-failure:Worker:sbuild:hello%3Aworld:any%3Aamd64%3Aamd64"
            ),
            items[4],
        )

    def test_lookup_filter_unexpected_format(self) -> None:
        """`lookup_filter` raises LookupError for an unexpected format."""
        with self.assertRaisesRegex(
            LookupError,
            r'^Unexpected lookup filter format: "invalid" \(expected '
            r'"same_work_request" or "same_workflow"\)$',
        ):
            self.manager.lookup_filter(
                "invalid",
                "foo",
                workspace=self.scenario.workspace,
                user=self.scenario.user,
            )

    def test_lookup_filter_same_work_request_single(self) -> None:
        """`lookup_filter`: `same_work_request` with a single lookup."""
        work_requests = [
            self.playground.create_work_request() for _ in range(2)
        ]
        upload, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD, work_request=work_requests[0]
        )
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER, "noop", "subject", "context", work_request.id
            )
            for work_request in work_requests
        ]
        subordinate_lookup = f"{upload.id}@artifacts"

        condition = self.manager.lookup_filter(
            "same_work_request",
            subordinate_lookup,
            workspace=self.scenario.workspace,
            user=self.scenario.user,
        )
        self.assertEqual(
            condition,
            Q(
                In(
                    Cast("data__work_request_id", IntegerField()),
                    {work_requests[0].id},
                )
            ),
        )
        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .filter(condition),
            [items[0]],
        )
        self.assertCountEqual(
            lookup_multiple(
                LookupMultiple.parse_obj(
                    {
                        "collection": self.collection_lookup,
                        "child_type": LookupChildType.BARE,
                        "lookup__same_work_request": subordinate_lookup,
                    }
                ),
                self.scenario.workspace,
                user=self.scenario.user,
                expect_type=LookupChildType.BARE,
            ),
            [
                LookupResult(
                    result_type=CollectionItem.Types.BARE,
                    parent_collection_lookup=self.collection_lookup,
                    collection_item=items[0],
                )
            ],
        )

    def test_lookup_filter_same_work_request_multiple(self) -> None:
        """`lookup_filter`: `same_work_request` with a multiple lookup."""
        work_requests = [
            self.playground.create_work_request() for _ in range(3)
        ]
        uploads = [
            self.playground.create_artifact(
                category=ArtifactCategory.UPLOAD, work_request=work_request
            )[0]
            for work_request in work_requests[:2]
        ]
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER, "noop", "subject", "context", work_request.id
            )
            for work_request in work_requests
        ]
        subordinate_lookup = LookupMultiple.parse_obj(
            [f"{upload.id}@artifacts" for upload in uploads]
        )

        condition = self.manager.lookup_filter(
            "same_work_request",
            subordinate_lookup,
            workspace=self.scenario.workspace,
            user=self.scenario.user,
        )
        self.assertEqual(
            condition,
            Q(
                In(
                    Cast("data__work_request_id", IntegerField()),
                    {work_request.id for work_request in work_requests[:2]},
                )
            ),
        )
        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .filter(condition),
            items[:2],
            ordered=False,
        )
        self.assertCountEqual(
            lookup_multiple(
                LookupMultiple.parse_obj(
                    {
                        "collection": self.collection_lookup,
                        "child_type": LookupChildType.BARE,
                        "lookup__same_work_request": subordinate_lookup,
                    }
                ),
                self.scenario.workspace,
                user=self.scenario.user,
                expect_type=LookupChildType.BARE,
            ),
            [
                LookupResult(
                    result_type=CollectionItem.Types.BARE,
                    parent_collection_lookup=self.collection_lookup,
                    collection_item=item,
                )
                for item in items[:2]
            ],
        )

    def test_lookup_filter_same_workflow_single(self) -> None:
        """`lookup_filter`: `same_workflow` with a single lookup."""
        work_requests = [self.workflow.create_child("noop") for _ in range(2)]
        work_requests.append(self.playground.create_work_request())
        upload, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD, work_request=work_requests[0]
        )
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER, "noop", "subject", "context", work_request.id
            )
            for work_request in work_requests
        ]
        subordinate_lookup = f"{upload.id}@artifacts"

        condition = self.manager.lookup_filter(
            "same_workflow",
            subordinate_lookup,
            workspace=self.scenario.workspace,
            user=self.scenario.user,
        )
        # Ideally we'd check the query itself, but digging into the
        # WorkRequest subquery is hard, so we just check the results.
        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .filter(condition),
            items[:2],
            ordered=False,
        )
        self.assertCountEqual(
            lookup_multiple(
                LookupMultiple.parse_obj(
                    {
                        "collection": self.collection_lookup,
                        "child_type": LookupChildType.BARE,
                        "lookup__same_workflow": subordinate_lookup,
                    }
                ),
                self.scenario.workspace,
                user=self.scenario.user,
                expect_type=LookupChildType.BARE,
            ),
            [
                LookupResult(
                    result_type=CollectionItem.Types.BARE,
                    parent_collection_lookup=self.collection_lookup,
                    collection_item=item,
                )
                for item in items[:2]
            ],
        )

    def test_lookup_filter_same_workflow_multiple(self) -> None:
        """`lookup_filter`: `same_workflow` with a multiple lookup."""
        work_requests = [self.workflow.create_child("noop") for _ in range(2)]
        other_workflow = self.playground.create_workflow()
        work_requests.extend(
            [other_workflow.create_child("noop") for _ in range(2)]
        )
        work_requests.append(self.playground.create_work_request())
        uploads = [
            self.playground.create_artifact(
                category=ArtifactCategory.UPLOAD, work_request=work_request
            )[0]
            for work_request in (work_requests[0], work_requests[2])
        ]
        items = [
            self.create_historical_task_run(
                TaskTypes.WORKER, "noop", "subject", "context", work_request.id
            )
            for work_request in work_requests
        ]
        subordinate_lookup = LookupMultiple.parse_obj(
            [f"{upload.id}@artifacts" for upload in uploads]
        )

        condition = self.manager.lookup_filter(
            "same_workflow",
            subordinate_lookup,
            workspace=self.scenario.workspace,
            user=self.scenario.user,
        )
        # Ideally we'd check the query itself, but digging into the
        # WorkRequest subquery is hard, so we just check the results.
        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .filter(condition),
            items[:4],
            ordered=False,
        )
        self.assertCountEqual(
            lookup_multiple(
                LookupMultiple.parse_obj(
                    {
                        "collection": self.collection_lookup,
                        "child_type": LookupChildType.BARE,
                        "lookup__same_workflow": subordinate_lookup,
                    }
                ),
                self.scenario.workspace,
                user=self.scenario.user,
                expect_type=LookupChildType.BARE,
            ),
            [
                LookupResult(
                    result_type=CollectionItem.Types.BARE,
                    parent_collection_lookup=self.collection_lookup,
                    collection_item=item,
                )
                for item in items[:4]
            ],
        )
