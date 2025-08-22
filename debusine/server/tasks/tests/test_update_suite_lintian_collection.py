# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the task to update a debian:suite-lintian collection."""

import re
from typing import ClassVar, cast

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Collection,
    CollectionItem,
    User,
    WorkRequest,
    Workspace,
)
from debusine.server.collections import (
    DebianSuiteLintianManager,
    DebianSuiteManager,
)
from debusine.server.collections.tests.utils import CollectionTestMixin
from debusine.server.tasks import UpdateSuiteLintianCollection
from debusine.server.tasks.update_derived_collection import (
    DerivedCollectionChange,
    DerivedCollectionChangeKind,
)
from debusine.tasks.models import LintianData, LintianInput, LookupMultiple
from debusine.test.django import TestCase


class UpdateSuiteLintianCollectionTests(CollectionTestMixin, TestCase):
    """Test the `UpdateSuiteLintianCollection` task."""

    user: ClassVar[User]
    workspace: ClassVar[Workspace]
    work_request: ClassVar[WorkRequest]
    base_collection: ClassVar[Collection]
    base_manager: ClassVar[DebianSuiteManager]
    derived_collection: ClassVar[Collection]
    derived_manager: ClassVar[DebianSuiteLintianManager]
    task: ClassVar[UpdateSuiteLintianCollection]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.workspace = cls.playground.get_default_workspace()
        cls.work_request = cls.playground.create_work_request(
            workspace=cls.workspace, created_by=cls.user
        )
        cls.base_collection = Collection.objects.create(
            name="bookworm",
            category=CollectionCategory.SUITE,
            workspace=cls.workspace,
        )
        cls.base_collection.data["may_reuse_versions"] = True
        cls.base_collection.save()
        cls.base_manager = cast(DebianSuiteManager, cls.base_collection.manager)
        cls.derived_collection = Collection.objects.create(
            name="bookworm",
            category=CollectionCategory.SUITE_LINTIAN,
            workspace=cls.workspace,
        )
        cls.derived_manager = cast(
            DebianSuiteLintianManager, cls.derived_collection.manager
        )
        cls.task = UpdateSuiteLintianCollection(
            {
                "base_collection": f"bookworm@{CollectionCategory.SUITE}",
                "derived_collection": (
                    f"bookworm@{CollectionCategory.SUITE_LINTIAN}"
                ),
            }
        )
        cls.task.set_work_request(cls.work_request)

    def test_base_collection_uses_default_category(self) -> None:
        """`base_collection` uses a reasonable default category."""
        self.assertEqual(self.task.base_collection, self.base_collection)
        self.task.data.base_collection = "bookworm"
        del self.task.base_collection
        self.assertEqual(self.task.base_collection, self.base_collection)

    def test_derived_collection_uses_default_category(self) -> None:
        """`derived_collection` uses a reasonable default category."""
        self.assertEqual(self.task.derived_collection, self.derived_collection)
        self.task.data.derived_collection = "bookworm"
        del self.task.derived_collection
        self.assertEqual(self.task.derived_collection, self.derived_collection)

    def test_find_relevant_base_items(self) -> None:
        """Only active items in the correct base collection are relevant."""
        collection_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("a", "b", "c")
        ]
        self.base_manager.remove_item(collection_items[2])
        other_collection = Collection.objects.create(
            name="trixie",
            category=CollectionCategory.SUITE,
            workspace=self.workspace,
        )
        self.create_source_package_item(
            cast(DebianSuiteManager, other_collection.manager), "hello", "1.0"
        )

        self.assertQuerySetEqual(
            self.task.find_relevant_base_items(),
            collection_items[:2],
            ordered=False,
        )

    def test_find_relevant_derived_items(self) -> None:
        """Only active items in the correct derived collection are relevant."""
        source_package_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("a", "b", "c", "d")
        ]
        lintian_items = [
            self.create_lintian_item(
                self.derived_manager, related_item=source_package_item
            )
            for source_package_item in source_package_items[:3]
        ]
        self.derived_manager.remove_item(lintian_items[2])
        other_collection = Collection.objects.create(
            name="trixie",
            category=CollectionCategory.SUITE_LINTIAN,
            workspace=self.workspace,
        )
        self.create_lintian_item(
            cast(DebianSuiteLintianManager, other_collection.manager),
            related_item=source_package_items[3],
        )

        self.assertQuerySetEqual(
            self.task.find_relevant_derived_items(),
            lintian_items[:2],
            ordered=False,
        )

    def test_find_expected_derived_items(self) -> None:
        """
        Derived item names are mapped to sets of base item IDs.

        `*_source` items are only included if there are no corresponding
        binaries, since Lintian is less capable when it doesn't also have
        some binaries to work with.

        Binary packages with no corresponding source packages are omitted.
        """
        source_package_item_a = self.create_source_package_item(
            self.base_manager, "a", "1.0"
        )
        binary_package_items_a_any = [
            self.create_binary_package_item(
                self.base_manager, "a", "1.0", package, "1.0", "amd64"
            )
            for package in ("liba1", "liba-dev")
        ]
        binary_package_item_a_all = self.create_binary_package_item(
            self.base_manager, "a", "1.0", "a-doc", "1.0", "all"
        )
        binary_package_item_b = self.create_binary_package_item(
            self.base_manager, "b", "1:1.1", "b-doc", "1.1", "all"
        )
        source_package_item_c = self.create_source_package_item(
            self.base_manager, "c", "1.0"
        )

        self.assertEqual(
            self.task.find_expected_derived_items(
                self.task.find_relevant_base_items()
            ),
            {
                "a_1.0_all": {
                    source_package_item_a.id,
                    binary_package_item_a_all.id,
                },
                "a_1.0_amd64": {
                    item.id
                    for item in [source_package_item_a]
                    + binary_package_items_a_any
                },
                "b_1:1.1_all": {binary_package_item_b.id},
                "c_1.0_source": {source_package_item_c.id},
            },
        )

    def test_categorize_derived_items(self) -> None:
        """Items are categorized into add/replace/remove."""
        source_package_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("a", "b", "c")
        ]
        lintian_items: list[CollectionItem] = []
        for source_package_item in source_package_items:
            lintian_items.append(
                self.create_lintian_item(
                    self.derived_manager, related_item=source_package_item
                )
            )

        for source_package_item in source_package_items[1:]:
            self.base_manager.remove_item(source_package_item)
        new_source_package_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("c", "d")
        ]

        base_items = self.task.find_relevant_base_items()
        derived_items = self.task.find_relevant_derived_items()
        self.assertCountEqual(
            list(
                self.task.categorize_derived_items(
                    relevant_base_items=base_items,
                    relevant_derived_items=derived_items,
                )
            ),
            [
                DerivedCollectionChange(
                    kind=DerivedCollectionChangeKind.ADD,
                    name="d_1.0_source",
                    base_item_ids={new_source_package_items[1].id},
                ),
                DerivedCollectionChange(
                    kind=DerivedCollectionChangeKind.REPLACE,
                    name="c_1.0_source",
                    base_item_ids={new_source_package_items[0].id},
                ),
                DerivedCollectionChange(
                    kind=DerivedCollectionChangeKind.REMOVE, name="b_1.0_source"
                ),
            ],
        )

    def test_make_child_work_request_source_only(self) -> None:
        """Make a child work request with no binary packages."""
        source_package_item = self.create_source_package_item(
            self.base_manager, "a", "1.0"
        )
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        child_work_request = self.task.make_child_work_request(
            base_item_ids={source_package_item.id},
            child_task_data=None,
            force=False,
        )

        assert child_work_request is not None
        self.assertEqual(child_work_request.task_name, "lintian")
        self.assertEqual(
            child_work_request.task_data,
            {
                "input": {
                    "source_artifact": source_package_item.artifact_id,
                    "binary_artifacts": [],
                }
            },
        )
        self.assertEqual(child_work_request.workspace, self.workspace)
        self.assertEqual(child_work_request.created_by, work_request.created_by)
        self.assertEqual(child_work_request.parent, work_request)
        self.assert_work_request_event_reactions(
            child_work_request,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": ArtifactCategory.LINTIAN},
                    "collection": (
                        f"bookworm@{CollectionCategory.SUITE_LINTIAN}"
                    ),
                }
            ],
        )

    def test_make_child_work_request_with_binaries(self) -> None:
        """Make a child work request with binary packages."""
        source_package_item = self.create_source_package_item(
            self.base_manager, "a", "1.0"
        )
        binary_package_items = [
            self.create_binary_package_item(
                self.base_manager, "a", "1.0", package, "1.0", "amd64"
            )
            for package in ("liba1", "liba-dev")
        ]
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        child_work_request = self.task.make_child_work_request(
            base_item_ids={
                item.id for item in (source_package_item, *binary_package_items)
            },
            child_task_data=None,
            force=False,
        )

        assert child_work_request is not None
        self.assertEqual(child_work_request.task_name, "lintian")
        self.assertEqual(
            child_work_request.task_data,
            {
                "input": {
                    "source_artifact": source_package_item.artifact_id,
                    "binary_artifacts": [
                        item.artifact_id for item in binary_package_items
                    ],
                }
            },
        )
        self.assertEqual(child_work_request.workspace, self.workspace)
        self.assertEqual(child_work_request.created_by, work_request.created_by)
        self.assertEqual(child_work_request.parent, work_request)
        self.assert_work_request_event_reactions(
            child_work_request,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": ArtifactCategory.LINTIAN},
                    "collection": (
                        f"bookworm@{CollectionCategory.SUITE_LINTIAN}"
                    ),
                }
            ],
        )

    def test_make_child_work_request_with_only_binaries(self) -> None:
        """Make a child work request with binary packages but no sources."""
        binary_package_items = [
            self.create_binary_package_item(
                self.base_manager, "a", "1.0", package, "1.0", "amd64"
            )
            for package in ("liba1", "liba-dev")
        ]
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        child_work_request = self.task.make_child_work_request(
            base_item_ids={item.id for item in binary_package_items},
            child_task_data=None,
            force=False,
        )

        assert child_work_request is not None
        self.assertEqual(child_work_request.task_name, "lintian")
        self.assertEqual(
            child_work_request.task_data,
            {
                "input": {
                    "binary_artifacts": [
                        item.artifact_id for item in binary_package_items
                    ]
                }
            },
        )
        self.assertEqual(child_work_request.workspace, self.workspace)
        self.assertEqual(child_work_request.created_by, work_request.created_by)
        self.assertEqual(child_work_request.parent, work_request)
        self.assert_work_request_event_reactions(
            child_work_request,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": ArtifactCategory.LINTIAN},
                    "collection": (
                        f"bookworm@{CollectionCategory.SUITE_LINTIAN}"
                    ),
                }
            ],
        )

    def test_make_child_work_request_with_no_packages(self) -> None:
        """`make_child_work_request` raises ValueError: no packages."""
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        with self.assertRaisesRegex(
            ValueError,
            r"Base items must include at least one debian:source-package "
            r"or debian:binary-package artifact",
        ):
            self.task.make_child_work_request(
                base_item_ids=set(), child_task_data=None, force=False
            )

    def test_make_child_work_request_with_multiple_sources(self) -> None:
        """`make_child_work_request` raises ValueError: multiple sources."""
        source_package_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("a", "b")
        ]
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                f"Base items must include at most one debian:source-package "
                f"artifact: "
                f"{[item.artifact_id for item in source_package_items]}"
            ),
        ):
            self.task.make_child_work_request(
                base_item_ids={item.id for item in source_package_items},
                child_task_data=None,
                force=False,
            )

    def test_make_child_work_request_extra_task_data(self) -> None:
        """A child work request can be supplied with extra task data."""
        source_package_item = self.create_source_package_item(
            self.base_manager, "a", "1.0"
        )
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)
        child_task_data = {
            "environment": "debian/match:codename=bookworm:architecture=amd64"
        }

        child_work_request = self.task.make_child_work_request(
            base_item_ids={source_package_item.id},
            child_task_data=child_task_data,
            force=False,
        )

        assert child_work_request is not None
        self.assertEqual(
            child_work_request.task_data,
            {
                "input": {
                    "source_artifact": source_package_item.artifact_id,
                    "binary_artifacts": [],
                },
                "environment": (
                    "debian/match:codename=bookworm:architecture=amd64"
                ),
            },
        )
        # The passed-in `child_task_data` has not been mutated.
        self.assertEqual(
            child_task_data,
            {
                "environment": (
                    "debian/match:codename=bookworm:architecture=amd64"
                )
            },
        )

    def test_make_child_work_request_force(self) -> None:
        """`force=True` is needed if a matching work request already exists."""
        source_package_item = self.create_source_package_item(
            self.base_manager, "a", "1.0"
        )
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)
        self.playground.create_work_request(
            task_name="lintian",
            task_data=LintianData(
                input=LintianInput(
                    source_artifact=source_package_item.artifact_id,
                    binary_artifacts=LookupMultiple.parse_obj([]),
                )
            ),
            workspace=self.workspace,
            created_by=work_request.created_by,
            parent=work_request,
            event_reactions_json={
                "on_success": [
                    {
                        "action": "update-collection-with-artifacts",
                        "artifact_filters": {
                            "category": ArtifactCategory.LINTIAN
                        },
                        "collection": (
                            f"bookworm@{CollectionCategory.SUITE_LINTIAN}"
                        ),
                    }
                ]
            },
        )

        self.assertIsNone(
            self.task.make_child_work_request(
                base_item_ids={source_package_item.id},
                child_task_data=None,
                force=False,
            )
        )
        self.assertIsNotNone(
            self.task.make_child_work_request(
                base_item_ids={source_package_item.id},
                child_task_data=None,
                force=True,
            )
        )

    def test_execute(self) -> None:
        """The task plans and executes the required changes."""
        source_package_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("a", "b", "c")
        ]
        lintian_items: list[CollectionItem] = []
        for source_package_item in source_package_items:
            lintian_items.append(
                self.create_lintian_item(
                    self.derived_manager, related_item=source_package_item
                )
            )

        for source_package_item in source_package_items[1:]:
            self.base_manager.remove_item(source_package_item)
        new_source_package_items = [
            self.create_source_package_item(self.base_manager, package, "1.0")
            for package in ("c", "d")
        ]

        self.task.data.child_task_data = {
            "environment": "debian/match:codename=bookworm:architecture=amd64"
        }
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        self.assertTrue(self.task.execute())

        # The task created work requests for items that were planned to be
        # added or replaced.
        child_work_requests = sorted(
            WorkRequest.objects.filter(parent=work_request),
            key=lambda wr: wr.task_data["input"]["source_artifact"],
        )
        self.assertEqual(len(child_work_requests), 2)
        for child_work_request, item in zip(
            child_work_requests, new_source_package_items
        ):
            self.assertEqual(child_work_request.task_name, "lintian")
            self.assertEqual(
                child_work_request.task_data,
                {
                    "input": {
                        "source_artifact": item.artifact_id,
                        "binary_artifacts": [],
                    },
                    "environment": (
                        "debian/match:codename=bookworm:architecture=amd64"
                    ),
                },
            )
            self.assertEqual(child_work_request.workspace, self.workspace)
            self.assertEqual(
                child_work_request.created_by, work_request.created_by
            )
            self.assertEqual(child_work_request.parent, work_request)
            self.assert_work_request_event_reactions(
                child_work_request,
                on_success=[
                    {
                        "action": "update-collection-with-artifacts",
                        "artifact_filters": {
                            "category": ArtifactCategory.LINTIAN
                        },
                        "collection": (
                            f"bookworm@{CollectionCategory.SUITE_LINTIAN}"
                        ),
                    }
                ],
            )
        # The task removed items that were planned to be removed.
        self.assertQuerySetEqual(
            CollectionItem.objects.active().filter(
                parent_collection=self.derived_collection
            ),
            [lintian_items[0], lintian_items[2]],
            ordered=False,
        )

    def test_execute_wrong_base_collection_category(self) -> None:
        """The task fails if the base collection has the wrong category."""
        Collection.objects.create(
            name="bookworm",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=self.workspace,
        )
        self.task.data.base_collection = (
            f"bookworm@{CollectionCategory.ENVIRONMENTS}"
        )
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        with self.assertRaisesRegex(
            ValueError,
            r"Base collection has category 'debian:environments'; expected "
            r"one of \['debian:suite'\]",
        ):
            self.task.execute()

    def test_execute_wrong_derived_collection_category(self) -> None:
        """The task fails if the derived collection has the wrong category."""
        Collection.objects.create(
            name="bookworm",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=self.workspace,
        )
        self.task.data.derived_collection = (
            f"bookworm@{CollectionCategory.ENVIRONMENTS}"
        )
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        with self.assertRaisesRegex(
            ValueError,
            r"Derived collection has category 'debian:environments'; expected "
            r"one of \['debian:suite-lintian'\]",
        ):
            self.task.execute()

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(
            self.task.get_label(),
            "sync derived lintian collection",
        )
