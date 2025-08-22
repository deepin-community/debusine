# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebianQAResultsManager."""

from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianQAResult,
)
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import CollectionItem, WorkRequest
from debusine.db.playground import scenarios
from debusine.server.collections import (
    DebianQAResultsManager,
    ItemAdditionError,
)
from debusine.test.django import TestCase


class DebianQAResultsManagerTests(TestCase):
    """Tests for DebianQAResultsManager."""

    scenario = scenarios.DefaultContext()

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.workflow = self.playground.create_work_request(task_name="noop")
        self.collection = self.playground.create_collection(
            "results", CollectionCategory.QA_RESULTS
        )
        self.manager = DebianQAResultsManager(collection=self.collection)

    def test_add_bare_data_no_data(self) -> None:
        """``add_bare_data`` requires item data."""
        with self.assertRaisesRegex(
            ItemAdditionError, "Adding to debian:qa-results requires data"
        ):
            self.manager.add_bare_data(
                BareDataCategory.QA_RESULT,
                user=self.scenario.user,
                workflow=self.workflow,
            )

    def test_add_bare_data_refuses_name(self) -> None:
        """``add_bare_data`` refuses an explicitly-specified name."""
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": 1,
        }

        with self.assertRaisesRegex(
            ItemAdditionError,
            "Cannot use an explicit item name when adding to debian:qa-results",
        ):
            self.manager.add_bare_data(
                BareDataCategory.QA_RESULT,
                user=self.scenario.user,
                workflow=self.workflow,
                data=data,
                name="override",
            )

    def test_add_bare_data_raise_item_addition_error(self) -> None:
        """``add_bare_data`` raises an error for duplicate names."""
        work_request = self.playground.create_work_request(task_name="piuparts")
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }
        self.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_bare_data(
                BareDataCategory.QA_RESULT,
                user=self.scenario.user,
                workflow=self.workflow,
                data=data,
            )

    def test_add_bare_data_different_work_request_ids(self) -> None:
        """Adding bare data items with different work request IDs is OK."""
        now = timezone.now()
        work_requests = [
            self.playground.create_work_request(task_name="piuparts")
            for _ in range(2)
        ]

        items = [
            self.manager.add_bare_data(
                BareDataCategory.QA_RESULT,
                user=self.scenario.user,
                workflow=self.workflow,
                data={
                    "package": "hello",
                    "version": "1.0-1",
                    "architecture": "amd64",
                    "timestamp": int(now.timestamp()),
                    "work_request_id": work_request.id,
                },
            )
            for work_request in work_requests
        ]

        self.assertEqual(
            [item.name for item in items],
            [
                f"piuparts_hello_1.0-1_amd64_{work_request.id}"
                for work_request in work_requests
            ],
        )

    def test_add_bare_data_replace(self) -> None:
        """``add_bare_data`` can replace an existing bare data item."""
        work_request = self.playground.create_work_request(
            task_name="piuparts", result=WorkRequest.Results.FAILURE
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }
        item_old = self.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
        )

        work_request.result = WorkRequest.Results.SUCCESS
        work_request.save()
        item_new = self.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        item_old.refresh_from_db()
        self.assertEqual(
            item_old.name, f"piuparts_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item_old.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            item_old.data,
            {
                **data,
                "task_name": "piuparts",
                "result": WorkRequest.Results.FAILURE,
            },
        )
        self.assertEqual(item_old.removed_by_user, self.scenario.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(
            item_new.name, f"piuparts_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item_new.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            item_new.data,
            {
                **data,
                "task_name": "piuparts",
                "result": WorkRequest.Results.SUCCESS,
            },
        )
        self.assertIsNone(item_new.removed_at)

    def test_add_bare_data_replace_nonexistent(self) -> None:
        """Replacing a nonexistent bare data item is allowed."""
        work_request = self.playground.create_work_request(
            task_name="piuparts", result=WorkRequest.Results.SUCCESS
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }

        item = self.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        self.assertEqual(
            item.name, f"piuparts_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            item.data,
            {
                **data,
                "task_name": "piuparts",
                "result": WorkRequest.Results.SUCCESS,
            },
        )

    def test_add_bare_data_old_items_to_keep(self) -> None:
        """The number of old bare data items to keep is configurable."""
        self.collection.data = {"old_items_to_keep": 3}
        now = timezone.now()
        work_requests = [
            self.playground.create_work_request(task_name="piuparts")
            for _ in range(6)
        ]
        items = [
            CollectionItem.objects.create_from_bare_data(
                BareDataCategory.QA_RESULT,
                parent_collection=self.collection,
                name=f"piuparts_hello_1.0-1_amd64_{i + 1}",
                data=DebianQAResult(
                    task_name="piuparts",
                    package="hello",
                    version="1.0-1",
                    architecture="amd64",
                    timestamp=int(now.timestamp()) - 10 + i,
                    work_request_id=i + 1,
                    result=WorkRequest.Results.SUCCESS,
                ),
                created_by_user=self.scenario.user,
                created_by_workflow=self.workflow,
            )
            for i, work_request in enumerate(work_requests[:5])
        ]

        items.append(
            self.manager.add_bare_data(
                BareDataCategory.QA_RESULT,
                user=self.scenario.user,
                workflow=self.workflow,
                data={
                    "package": "hello",
                    "version": "1.0-1",
                    "architecture": "amd64",
                    "timestamp": int(now.timestamp()),
                    "work_request_id": work_requests[5].id,
                },
            )
        )

        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .order_by("id"),
            items[3:],
        )

    def test_add_artifact_no_variables(self) -> None:
        """``add_artifact`` requires variables."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "Adding to debian:qa-results requires variables"
        ):
            self.manager.add_artifact(
                artifact, user=self.scenario.user, workflow=self.workflow
            )

    def test_add_artifact_refuses_name(self) -> None:
        """``add_artifact`` refuses an explicitly-specified name."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": 1,
        }

        with self.assertRaisesRegex(
            ItemAdditionError,
            "Cannot use an explicit item name when adding to debian:qa-results",
        ):
            self.manager.add_artifact(
                artifact,
                user=self.scenario.user,
                workflow=self.workflow,
                variables=data,
                name="override",
            )

    def test_add_artifact_raise_item_addition_error(self) -> None:
        """``add_artifact`` raises an error for duplicate names."""
        now = timezone.now()
        work_request = self.playground.create_work_request(
            task_name="autopkgtest", result=WorkRequest.Results.FAILURE
        )
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "work_request_id": work_request.id,
        }
        self.manager.add_artifact(
            artifact_1,
            user=self.scenario.user,
            workflow=self.workflow,
            variables={**data, "timestamp": int(now.timestamp()) - 60},
        )

        work_request.result = WorkRequest.Results.SUCCESS
        work_request.save()
        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_artifact(
                artifact_2,
                user=self.scenario.user,
                workflow=self.workflow,
                variables={**data, "timestamp": int(now.timestamp())},
            )

    def test_add_artifact_different_work_request_ids(self) -> None:
        """Adding artifacts with different work request IDs is OK."""
        work_request_1 = self.playground.create_work_request(
            task_name="autopkgtest", result=WorkRequest.Results.SUCCESS
        )
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        work_request_2 = self.playground.create_work_request(
            task_name="autopkgtest", result=WorkRequest.Results.SUCCESS
        )
        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
        }

        items = [
            self.manager.add_artifact(
                artifact_1,
                user=self.scenario.user,
                workflow=self.workflow,
                variables={**data, "work_request_id": work_request.id},
            )
            for artifact, work_request in (
                (artifact_1, work_request_1),
                (artifact_2, work_request_2),
            )
        ]

        self.assertEqual(
            [item.name for item in items],
            [
                f"autopkgtest_hello_1.0-1_amd64_{work_request_1.id}",
                f"autopkgtest_hello_1.0-1_amd64_{work_request_2.id}",
            ],
        )

    def test_add_artifact_replace_bare_data(self) -> None:
        """``add_artifact`` can replace an existing bare data item."""
        work_request = self.playground.create_work_request(
            task_name="autopkgtest", result=WorkRequest.Results.FAILURE
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }
        item_old = self.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
        )
        artifact_new, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        work_request.result = WorkRequest.Results.SUCCESS
        work_request.save()

        item_new = self.manager.add_artifact(
            artifact_new,
            user=self.scenario.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        item_old.refresh_from_db()
        self.assertEqual(
            item_old.name, f"autopkgtest_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item_old.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            item_old.data,
            {
                **data,
                "task_name": "autopkgtest",
                "result": WorkRequest.Results.FAILURE,
            },
        )
        self.assertEqual(item_old.removed_by_user, self.scenario.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(
            item_new.name, f"autopkgtest_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item_new.artifact, artifact_new)
        self.assertEqual(
            item_new.data,
            {
                **data,
                "task_name": "autopkgtest",
                "result": WorkRequest.Results.SUCCESS,
            },
        )
        self.assertIsNone(item_new.removed_at)

    def test_add_artifact_replace_artifact(self) -> None:
        """``add_artifact`` can replace an existing artifact."""
        work_request = self.playground.create_work_request(
            task_name="autopkgtest", result=WorkRequest.Results.FAILURE
        )
        artifact_old, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        artifact_new, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }
        item_old = self.manager.add_artifact(
            artifact_old,
            user=self.scenario.user,
            workflow=self.workflow,
            variables=data,
        )

        work_request.result = WorkRequest.Results.SUCCESS
        work_request.save()
        item_new = self.manager.add_artifact(
            artifact_new,
            user=self.scenario.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        item_old.refresh_from_db()
        self.assertEqual(
            item_old.name, f"autopkgtest_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item_old.artifact, artifact_old)
        self.assertEqual(
            item_old.data,
            {
                **data,
                "task_name": "autopkgtest",
                "result": WorkRequest.Results.FAILURE,
            },
        )
        self.assertEqual(item_old.removed_by_user, self.scenario.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(
            item_new.name, f"autopkgtest_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item_new.artifact, artifact_new)
        self.assertEqual(
            item_new.data,
            {
                **data,
                "task_name": "autopkgtest",
                "result": WorkRequest.Results.SUCCESS,
            },
        )
        self.assertIsNone(item_new.removed_at)

    def test_add_artifact_replace_nonexistent(self) -> None:
        """Replacing a nonexistent artifact is allowed."""
        work_request = self.playground.create_work_request(
            task_name="autopkgtest", result=WorkRequest.Results.SUCCESS
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }

        item = self.manager.add_artifact(
            artifact,
            user=self.scenario.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        self.assertEqual(
            item.name, f"autopkgtest_hello_1.0-1_amd64_{work_request.id}"
        )
        self.assertEqual(item.artifact, artifact)
        self.assertEqual(
            item.data,
            {
                **data,
                "task_name": "autopkgtest",
                "result": WorkRequest.Results.SUCCESS,
            },
        )

    def test_add_artifact_old_items_to_keep(self) -> None:
        """The number of old artifact items to keep is configurable."""
        self.collection.data = {"old_items_to_keep": 3}
        now = timezone.now()
        work_requests = [
            self.playground.create_work_request(
                task_name="autopkgtest", result=WorkRequest.Results.SUCCESS
            )
            for _ in range(6)
        ]
        items = [
            CollectionItem.objects.create_from_artifact(
                self.playground.create_artifact(
                    category=ArtifactCategory.AUTOPKGTEST
                )[0],
                parent_collection=self.collection,
                name=f"autopkgtest_hello_1.0-1_amd64_{i + 1}",
                data=model_to_json_serializable_dict(
                    DebianQAResult(
                        task_name="autopkgtest",
                        package="hello",
                        version="1.0-1",
                        architecture="amd64",
                        timestamp=int(now.timestamp()) - 10 + i,
                        work_request_id=work_request.id,
                        result=WorkRequest.Results.SUCCESS,
                    )
                ),
                created_by_user=self.scenario.user,
                created_by_workflow=self.workflow,
            )
            for i, work_request in enumerate(work_requests[:5])
        ]

        items.append(
            self.manager.add_artifact(
                self.playground.create_artifact(
                    category=ArtifactCategory.AUTOPKGTEST
                )[0],
                user=self.scenario.user,
                workflow=self.workflow,
                variables={
                    "package": "hello",
                    "version": "1.0-1",
                    "architecture": "amd64",
                    "timestamp": int(now.timestamp()),
                    "work_request_id": work_requests[5].id,
                },
            )
        )

        self.assertQuerySetEqual(
            CollectionItem.objects.active()
            .filter(parent_collection=self.collection)
            .order_by("id"),
            items[3:],
        )

    def test_remove_item_bare_data(self) -> None:
        """``remove_item`` removes a bare data item."""
        work_request = self.playground.create_work_request(
            task_name="piuparts", result=WorkRequest.Results.SUCCESS
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }
        item = self.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.scenario.user,
            workflow=self.workflow,
            data=data,
        )

        self.manager.remove_item(
            item, user=self.scenario.user, workflow=self.workflow
        )

        self.assertEqual(item.removed_by_user, self.scenario.user)
        self.assertEqual(item.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item.removed_at)

    def test_remove_item_artifact(self) -> None:
        """``remove_item`` removes an artifact item."""
        work_request = self.playground.create_work_request(
            task_name="autopkgtest"
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST
        )
        data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(timezone.now().timestamp()),
            "work_request_id": work_request.id,
        }
        item = self.manager.add_artifact(
            artifact,
            user=self.scenario.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        self.manager.remove_item(
            item, user=self.scenario.user, workflow=self.workflow
        )

        self.assertEqual(item.removed_by_user, self.scenario.user)
        self.assertEqual(item.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item.removed_at)

    def test_lookup_unexpected_format_raise_lookup_error(self) -> None:
        """``lookup`` raises ``LookupError``: invalid format."""
        with self.assertRaisesRegex(
            LookupError, r'^Unexpected lookup format: "foo:bar"$'
        ):
            self.manager.lookup("foo:bar")

        with self.assertRaisesRegex(
            LookupError, r'^Unexpected lookup format: "latest:nonsense"$'
        ):
            self.manager.lookup("latest:nonsense")

    def test_lookup_return_none(self) -> None:
        """``lookup`` returns None if there are no matches."""
        self.assertIsNone(self.manager.lookup("latest:autopkgtest_hello_1.0-1"))
        self.assertIsNone(self.manager.lookup("name:nonexistent"))

    def test_lookup_latest(self) -> None:
        """``lookup`` returns a matching QA result."""
        items: list[CollectionItem] = []
        now = timezone.now()
        timestamps = [int(now.timestamp()), int(now.timestamp()) - 60]
        for category, task_name, package, architecture in (
            (ArtifactCategory.AUTOPKGTEST, "autopkgtest", "hello", "amd64"),
            (ArtifactCategory.AUTOPKGTEST, "autopkgtest", "hello", "i386"),
            (
                ArtifactCategory.AUTOPKGTEST,
                "autopkgtest",
                "base-files",
                "amd64",
            ),
            (ArtifactCategory.LINTIAN, "lintian", "hello", "amd64"),
        ):
            work_requests = [
                self.playground.create_work_request(
                    task_name=task_name, result=WorkRequest.Results.SUCCESS
                )
                for _ in range(2)
            ]
            artifact, _ = self.playground.create_artifact(category=category)
            data = {
                "package": package,
                "version": "1.0",
                "architecture": architecture,
            }
            items += [
                self.manager.add_artifact(
                    artifact,
                    user=self.scenario.user,
                    workflow=self.workflow,
                    variables={
                        **data,
                        "timestamp": timestamp,
                        "work_request_id": work_request.id,
                    },
                )
                for timestamp, work_request in zip(timestamps, work_requests)
            ]
        items[2].removed_at = timezone.now()
        items[2].save()
        work_requests = [
            self.playground.create_work_request(
                task_name="piuparts", result=WorkRequest.Results.SUCCESS
            )
            for _ in range(2)
        ]
        data = {"package": "hello", "version": "1.0", "architecture": "amd64"}
        items += [
            self.manager.add_bare_data(
                BareDataCategory.QA_RESULT,
                user=self.scenario.user,
                workflow=self.workflow,
                data={
                    **data,
                    "timestamp": timestamp,
                    "work_request_id": work_request.id,
                },
            )
            for timestamp, work_request in zip(timestamps, work_requests)
        ]

        self.assertEqual(
            self.manager.lookup("latest:autopkgtest_hello_amd64"), items[0]
        )
        # items[2] would match, but has been removed.
        self.assertEqual(
            self.manager.lookup("latest:autopkgtest_hello_i386"), items[3]
        )
        self.assertEqual(
            self.manager.lookup("latest:autopkgtest_base-files_amd64"), items[4]
        )
        self.assertEqual(
            self.manager.lookup("latest:lintian_hello_amd64"), items[6]
        )
        self.assertEqual(
            self.manager.lookup("latest:piuparts_hello_amd64"), items[8]
        )
        self.assertIsNone(
            self.manager.lookup("latest:piuparts_base-files_amd64")
        )
