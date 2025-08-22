# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebianPackageBuildLogsManager."""

from django.db.models import IntegerField, Q
from django.db.models.functions import Cast
from django.db.models.lookups import In

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.client.models import LookupChildType
from debusine.db.models import CollectionItem, default_workspace
from debusine.server.collections import (
    DebianPackageBuildLogsManager,
    ItemAdditionError,
)
from debusine.server.collections.lookup import LookupResult, lookup_multiple
from debusine.tasks.models import LookupMultiple
from debusine.test.django import TestCase


class DebianPackageBuildLogsManagerTests(TestCase):
    """Tests for DebianPackageBuildLogsManager."""

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.user = self.playground.get_default_user()
        self.workflow = self.playground.create_work_request(task_name="noop")
        self.workspace = default_workspace()
        self.collection = self.workspace.get_singleton_collection(
            user=self.user, category=CollectionCategory.PACKAGE_BUILD_LOGS
        )
        self.collection_lookup = f"_@{CollectionCategory.PACKAGE_BUILD_LOGS}"
        self.manager = DebianPackageBuildLogsManager(collection=self.collection)

    def test_do_add_bare_data_no_data(self) -> None:
        """`do_add_bare_data` requires item data."""
        with self.assertRaisesRegex(
            ItemAdditionError,
            "Adding to debian:package-build-logs requires data",
        ):
            self.manager.add_bare_data(
                BareDataCategory.PACKAGE_BUILD_LOG,
                user=self.user,
                workflow=self.workflow,
            )

    def test_do_add_bare_data_raise_item_addition_error(self) -> None:
        """`do_add_bare_data` raises an error for duplicate names."""
        data = {
            "work_request_id": 1,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        self.manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.user,
            workflow=self.workflow,
            data=data,
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_bare_data(
                BareDataCategory.PACKAGE_BUILD_LOG,
                user=self.user,
                workflow=self.workflow,
                data=data,
            )

    def test_do_add_bare_data_different_work_request_ids(self) -> None:
        """Adding bare data items with different work request IDs is OK."""
        data = {
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }

        items = [
            self.manager.add_bare_data(
                BareDataCategory.PACKAGE_BUILD_LOG,
                user=self.user,
                workflow=self.workflow,
                data={**data, "work_request_id": work_request_id},
            )
            for work_request_id in (1, 2)
        ]

        self.assertEqual(
            [item.name for item in items],
            [
                "debian_bookworm_amd64_hello_1.0-1_1",
                "debian_bookworm_amd64_hello_1.0-1_2",
            ],
        )

    def test_do_add_bare_data_replace(self) -> None:
        """`do_add_bare_data` can replace an existing bare data item."""
        data = {
            "work_request_id": 1,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        item_old = self.manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.user,
            workflow=self.workflow,
            data=data,
        )

        item_new = self.manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        item_old.refresh_from_db()
        self.assertEqual(item_old.name, "debian_bookworm_amd64_hello_1.0-1_1")
        self.assertEqual(item_old.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_old.data, data)
        self.assertEqual(item_old.removed_by_user, self.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(item_new.name, "debian_bookworm_amd64_hello_1.0-1_1")
        self.assertEqual(item_new.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_new.data, data)
        self.assertIsNone(item_new.removed_at)

    def test_do_add_bare_data_replace_nonexistent(self) -> None:
        """Replacing a nonexistent bare data item is allowed."""
        data = {
            "work_request_id": 1,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }

        item = self.manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        self.assertEqual(item.name, "debian_bookworm_amd64_hello_1.0-1_1")
        self.assertEqual(item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item.data, data)

    def test_do_add_artifact_no_variables(self) -> None:
        """`do_add_artifact` requires variables."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG, data={}
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            "Adding to debian:package-build-logs requires variables",
        ):
            self.manager.add_artifact(
                artifact, user=self.user, workflow=self.workflow
            )

    def test_do_add_artifact_raise_item_addition_error(self) -> None:
        """`do_add_artifact` raises an error for duplicate names."""
        work_request = self.playground.create_work_request()
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={"source": "hello", "version": "1.0-1"},
        )
        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={"source": "hello", "version": "1.0-1"},
        )
        data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
        }
        self.manager.add_artifact(
            artifact_1, user=self.user, workflow=self.workflow, variables=data
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_artifact(
                artifact_2,
                user=self.user,
                workflow=self.workflow,
                variables=data,
            )

    def test_do_add_artifact_override_srcpkg_name_version(self) -> None:
        """
        `do_add_artifact` can override the source package name/version.

        This isn't very useful in practice, but it allows its interface to
        be more like `do_add_bare_data`.
        """
        work_request = self.playground.create_work_request()
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={"source": "hello", "version": "1.0-1"},
        )
        data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello-2",
            "srcpkg_version": "1.0-2",
        }

        item = self.manager.add_artifact(
            artifact, user=self.user, workflow=self.workflow, variables=data
        )

        self.assertEqual(
            item.name, f"debian_bookworm_amd64_hello-2_1.0-2_{work_request.id}"
        )
        self.assertEqual(item.data, data)

    def test_do_add_artifact_different_work_request_ids(self) -> None:
        """Adding artifacts with different work request IDs is OK."""
        work_request_1 = self.playground.create_work_request()
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={"source": "hello", "version": "1.0-1"},
        )
        work_request_2 = self.playground.create_work_request()
        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={"source": "hello", "version": "1.0-1"},
        )
        data = {
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
        }

        items = [
            self.manager.add_artifact(
                artifact_1,
                user=self.user,
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
                f"debian_bookworm_amd64_hello_1.0-1_{work_request_1.id}",
                f"debian_bookworm_amd64_hello_1.0-1_{work_request_2.id}",
            ],
        )

    def test_do_add_artifact_replace_bare_data(self) -> None:
        """`do_add_artifact` can replace an existing bare data item."""
        work_request = self.playground.create_work_request()
        data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        item_old = self.manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.user,
            workflow=self.workflow,
            data=data,
        )
        artifact_new, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={"source": "hello", "version": "1.0-1"},
        )

        item_new = self.manager.add_artifact(
            artifact_new,
            user=self.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        item_old.refresh_from_db()
        self.assertEqual(
            item_old.name,
            f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
        )
        self.assertEqual(item_old.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_old.data, data)
        self.assertEqual(item_old.removed_by_user, self.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(
            item_new.name,
            f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
        )
        self.assertEqual(item_new.artifact, artifact_new)
        self.assertEqual(item_new.data, data)
        self.assertIsNone(item_new.removed_at)

    def test_do_add_artifact_replace_artifact(self) -> None:
        """`do_add_artifact` can replace an existing artifact."""
        worker_1 = self.playground.create_worker()
        worker_2 = self.playground.create_worker()
        work_request = self.playground.create_work_request(worker=worker_1)
        artifact_old, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG, data={}
        )
        artifact_new, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG, data={}
        )
        data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        item_old = self.manager.add_artifact(
            artifact_old, user=self.user, workflow=self.workflow, variables=data
        )

        work_request.assign_worker(worker_2)
        item_new = self.manager.add_artifact(
            artifact_new,
            user=self.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        item_old.refresh_from_db()
        self.assertEqual(
            item_old.name,
            f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
        )
        self.assertEqual(item_old.artifact, artifact_old)
        self.assertEqual(item_old.data, {**data, "worker": worker_1.name})
        self.assertEqual(item_old.removed_by_user, self.user)
        self.assertEqual(item_old.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(
            item_new.name,
            f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
        )
        self.assertEqual(item_new.artifact, artifact_new)
        self.assertEqual(item_new.data, {**data, "worker": worker_2.name})
        self.assertIsNone(item_new.removed_at)

    def test_do_add_artifact_replace_nonexistent(self) -> None:
        """Replacing a nonexistent artifact is allowed."""
        work_request = self.playground.create_work_request()
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG, data={}
        )
        data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }

        item = self.manager.add_artifact(
            artifact,
            user=self.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        self.assertEqual(
            item.name, f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}"
        )
        self.assertEqual(item.artifact, artifact)
        self.assertEqual(item.data, data)

    def test_do_remove_item_bare_data(self) -> None:
        """`do_remove_item` removes a bare data item."""
        data = {
            "work_request_id": 1,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        item = self.manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.user,
            workflow=self.workflow,
            data=data,
            replace=True,
        )

        self.manager.remove_item(item, user=self.user, workflow=self.workflow)

        self.assertEqual(item.removed_by_user, self.user)
        self.assertEqual(item.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item.removed_at)

    def test_do_remove_item_artifact(self) -> None:
        """`do_remove_item` removes an artifact item."""
        work_request = self.playground.create_work_request()
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG, data={}
        )
        data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        item = self.manager.add_artifact(
            artifact,
            user=self.user,
            workflow=self.workflow,
            variables=data,
            replace=True,
        )

        self.manager.remove_item(item, user=self.user, workflow=self.workflow)

        self.assertEqual(item.removed_by_user, self.user)
        self.assertEqual(item.removed_by_workflow, self.workflow)
        self.assertIsNotNone(item.removed_at)

    def test_do_lookup_filter_unexpected_format(self) -> None:
        """`do_lookup_filter` raises LookupError for an unexpected format."""
        with self.assertRaisesRegex(
            LookupError, r'^Unexpected lookup filter format: "invalid"'
        ):
            self.manager.lookup_filter(
                "invalid", "foo", workspace=self.workspace, user=self.user
            )

    def test_do_lookup_filter_same_work_request_single(self) -> None:
        """`do_lookup_filter`: `same_work_request` with a single lookup."""
        work_requests = [
            self.playground.create_work_request() for _ in range(2)
        ]
        upload, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD, work_request=work_requests[0]
        )
        build_logs = [
            self.playground.create_artifact(
                category=ArtifactCategory.PACKAGE_BUILD_LOG,
                work_request=work_request,
            )[0]
            for work_request in work_requests
        ]
        items = [
            self.manager.add_artifact(
                build_log,
                user=self.user,
                variables={
                    "work_request_id": build_log.created_by_work_request_id,
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "srcpkg_name": "hello",
                    "srcpkg_version": "1.0-1",
                },
            )
            for build_log in build_logs
        ]
        subordinate_lookup = f"{upload.id}@artifacts"

        condition = self.manager.lookup_filter(
            "same_work_request",
            subordinate_lookup,
            workspace=self.workspace,
            user=self.user,
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
                        "lookup__same_work_request": subordinate_lookup,
                    }
                ),
                self.workspace,
                user=self.user,
                expect_type=LookupChildType.ARTIFACT,
            ),
            [
                LookupResult(
                    result_type=CollectionItem.Types.ARTIFACT,
                    parent_collection_lookup=self.collection_lookup,
                    collection_item=items[0],
                    artifact=build_logs[0],
                )
            ],
        )

    def test_do_lookup_filter_same_work_request_multiple(self) -> None:
        """`do_lookup_filter`: `same_work_request` with a multiple lookup."""
        work_requests = [
            self.playground.create_work_request() for _ in range(3)
        ]
        uploads = [
            self.playground.create_artifact(
                category=ArtifactCategory.UPLOAD, work_request=work_request
            )[0]
            for work_request in work_requests[:2]
        ]
        build_logs = [
            self.playground.create_artifact(
                category=ArtifactCategory.PACKAGE_BUILD_LOG,
                work_request=work_request,
            )[0]
            for work_request in work_requests
        ]
        items = [
            self.manager.add_artifact(
                build_log,
                user=self.user,
                variables={
                    "work_request_id": build_log.created_by_work_request_id,
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "srcpkg_name": "hello",
                    "srcpkg_version": "1.0-1",
                },
            )
            for build_log in build_logs
        ]
        subordinate_lookup = LookupMultiple.parse_obj(
            [f"{upload.id}@artifacts" for upload in uploads]
        )

        condition = self.manager.lookup_filter(
            "same_work_request",
            subordinate_lookup,
            workspace=self.workspace,
            user=self.user,
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
                        "lookup__same_work_request": subordinate_lookup,
                    }
                ),
                self.workspace,
                user=self.user,
                expect_type=LookupChildType.ARTIFACT,
            ),
            [
                LookupResult(
                    result_type=CollectionItem.Types.ARTIFACT,
                    parent_collection_lookup=self.collection_lookup,
                    collection_item=item,
                    artifact=build_log,
                )
                for item, build_log in zip(items[:2], build_logs[:2])
            ],
        )
