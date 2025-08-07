# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the collections views."""

from typing import ClassVar
from unittest import mock

from django.db.models import Max
from django.urls import reverse
from rest_framework import status

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.db.models import Collection, CollectionItem
from debusine.db.playground import scenarios
from debusine.server.collections import (
    DebianPackageBuildLogsManager,
    DebianSuiteManager,
)
from debusine.test.django import TestCase
from debusine.web.templatetags.debusine import (
    ui_shortcuts as template_ui_shortcuts,
)
from debusine.web.views import sidebar, ui_shortcuts
from debusine.web.views.collections import (
    CollectionCategoryListView,
    CollectionItemDetailView,
    CollectionListView,
)
from debusine.web.views.tests.utils import ViewTestMixin


class CollectionViewsTestsBase(ViewTestMixin, TestCase):
    """Base fixture for testing Collection views."""

    scenario = scenarios.DefaultContext(set_current=True)
    default_collections: ClassVar[list[Collection]]
    bookworm: ClassVar[Collection]
    trixie: ClassVar[Collection]
    environments: ClassVar[Collection]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.default_collections = list(cls.scenario.workspace.collections.all())
        cls.bookworm = cls.playground.create_collection(
            "bookworm",
            CollectionCategory.SUITE,
        )
        cls.trixie = cls.playground.create_collection(
            "trixie",
            CollectionCategory.SUITE,
        )
        cls.environments = cls.playground.create_collection(
            "debian",
            CollectionCategory.ENVIRONMENTS,
        )


class CollectionListViewTests(CollectionViewsTestsBase):
    """Tests for :py:class:`CollectionListView`."""

    url: ClassVar[str]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.url = reverse(
            "workspaces:collections:list",
            kwargs={
                "wname": cls.scenario.workspace.name,
            },
        )

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = CollectionListView()
        self.assertEqual(
            view.get_title(), f"{self.scenario.workspace.name} collections"
        )

    def test_permissions(self) -> None:
        """Test permissions on the collection list view."""
        self.assertSetsCurrentWorkspace(self.scenario.workspace, self.url)

    def test_list(self) -> None:
        """Test collection list view."""
        response = self.client.get(self.url)
        self.assertResponseHTML(response)
        queryset = response.context["collection_list"]
        self.assertQuerySetEqual(
            queryset,
            self.default_collections
            + [self.bookworm, self.trixie, self.environments],
            ordered=False,
        )

    def test_hides_internal(self) -> None:
        """Internal collections are not shown."""
        internal = self.playground.create_collection(
            "internal",
            CollectionCategory.WORKFLOW_INTERNAL,
            workspace=self.scenario.workspace,
        )
        response = self.client.get(self.url)
        self.assertResponseHTML(response)
        queryset = response.context["collection_list"]
        self.assertFalse(queryset.filter(pk=internal.pk).exists())


class CollectionCategoryListViewTests(CollectionViewsTestsBase):
    """Tests for :py:class:`CollectionCategoryListView`."""

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = CollectionCategoryListView()
        view.kwargs = {"ccat": "testcategory"}
        self.assertEqual(
            view.get_title(),
            f"{self.scenario.workspace.name} testcategory collections",
        )

    def test_permissions(self) -> None:
        """Test permissions on the collection list view."""
        url = reverse(
            "workspaces:collections:category_list",
            kwargs={
                "wname": self.scenario.workspace.name,
                "ccat": CollectionCategory.SUITE,
            },
        )
        self.assertSetsCurrentWorkspace(self.scenario.workspace, url)

    def test_list_only_selected_category(self) -> None:
        """Test permissions on the collection list view."""
        url = reverse(
            "workspaces:collections:category_list",
            kwargs={
                "wname": self.scenario.workspace.name,
                "ccat": CollectionCategory.SUITE,
            },
        )
        response = self.client.get(url)
        self.assertResponseHTML(response)
        queryset = response.context["collection_list"]
        self.assertQuerySetEqual(
            queryset,
            [self.bookworm, self.trixie],
            ordered=False,
        )

    def test_hides_internal(self) -> None:
        """Internal collections are not shown."""
        self.playground.create_collection(
            "internal",
            CollectionCategory.WORKFLOW_INTERNAL,
            workspace=self.scenario.workspace,
        )
        url = reverse(
            "workspaces:collections:category_list",
            kwargs={
                "wname": self.scenario.workspace.name,
                "ccat": CollectionCategory.WORKFLOW_INTERNAL,
            },
        )
        response = self.client.get(url)
        self.assertResponseHTML(response)
        queryset = response.context["collection_list"]
        self.assertQuerySetEqual(queryset.all(), [])


class CollectionDetailViewTests(CollectionViewsTestsBase):
    """Tests for :py:class:`CollectionDetailView`."""

    def test_permissions(self) -> None:
        """Test permissions on the collection list view."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace, self.trixie.get_absolute_url()
        )

    def test_detail(self) -> None:
        """Test collection detail view."""
        response = self.client.get(self.trixie.get_absolute_url())
        tree = self.assertResponseHTML(response)

        # Check table
        table = self.assertHasElement(
            tree, "//table[@id='collection-details-table']"
        )
        # First row
        element = self.assertHasElement(table, '//tbody/tr[1]/th[1]')
        self.assertTextContentEqual(
            element, "Full history retention period (days)"
        )
        element = self.assertHasElement(table, '//tbody/tr[1]/td[1]')
        self.assertTextContentEqual(element, "Always")

        # Second row
        element = self.assertHasElement(table, '//tbody/tr[2]/th[1]')
        self.assertTextContentEqual(
            element, "Metadata only retention period (days)"
        )
        element = self.assertHasElement(table, '//tbody/tr[2]/td[1]')
        self.assertTextContentEqual(element, "Always")

        # Third row
        element = self.assertHasElement(table, '//tbody/tr[3]/th[1]')
        self.assertTextContentEqual(element, "Retains artifacts")
        element = self.assertHasElement(table, '//tbody/tr[3]/td[1]')
        self.assertTextContentEqual(element, "Always")

        self.assertFalse(tree.xpath("//div[@id='metadata']"))

    def test_metadata(self) -> None:
        """Test collection metadata view."""
        self.trixie.data = {"test": True}
        self.trixie.save()
        response = self.client.get(self.trixie.get_absolute_url())
        tree = self.assertResponseHTML(response)
        metadata = self.assertHasElement(tree, "//div[@id='metadata']")
        self.assertYAMLContentEqual(metadata.div[1], self.trixie.data)

    def test_not_found(self) -> None:
        """Test looking up an invalid collection."""
        response = self.client.get(
            reverse(
                "workspaces:collections:detail",
                kwargs={
                    "wname": self.scenario.workspace.name,
                    "ccat": self.trixie.category,
                    "cname": "sid",
                },
            )
        )
        self.assertResponseHTML(response, status_code=status.HTTP_404_NOT_FOUND)


class CollectionSearchViewTests(CollectionViewsTestsBase):
    """Tests for :py:class:`CollectionSearchView`."""

    items: ClassVar[dict[str, CollectionItem]]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.items = {}
        manager = DebianSuiteManager(collection=cls.trixie)
        for name in ("foo", "bar"):
            for ver in range(2):
                source = cls.playground.create_source_artifact(
                    name=f"lib{name}{ver}"
                )
                cls.items[f"lib{name}{ver}"] = manager.add_source_package(
                    source,
                    user=cls.scenario.user,
                    component="main",
                    section="devel",
                )
                binary, _ = cls.playground.create_artifact(
                    category=ArtifactCategory.BINARY_PACKAGE,
                    data={
                        "srcpkg_name": source.data["name"],
                        "srcpkg_version": source.data["version"],
                        "deb_fields": {
                            "Package": f"lib{name}{ver}-dev",
                            "Version": source.data["version"],
                            "Architecture": "amd64",
                        },
                        "deb_control_files": [],
                    },
                    paths=[f"lib{name}{ver}-dev_{ver}.0_amd64.deb"],
                    create_files=True,
                    skip_add_files_in_store=True,
                )
                cls.items[f"lib{name}{ver}-dev"] = manager.add_binary_package(
                    binary,
                    user=cls.scenario.user,
                    component="main",
                    section="devel",
                    priority="optional",
                )

        source = cls.playground.create_source_artifact(name="libold0")
        cls.items["libold0"] = manager.add_source_package(
            source,
            user=cls.scenario.user,
            component="main",
            section="devel",
        )
        manager.remove_artifact(source, user=cls.scenario.user)

    def test_permissions(self) -> None:
        """Test permissions on the collection list view."""
        url = reverse(
            "workspaces:collections:search",
            kwargs={
                "wname": self.scenario.workspace.name,
                "ccat": CollectionCategory.SUITE,
                "cname": "trixie",
            },
        )
        self.assertSetsCurrentWorkspace(self.scenario.workspace, url)

    def test_not_found(self) -> None:
        """Test looking up an invalid collection."""
        response = self.client.get(
            reverse(
                "workspaces:collections:search",
                kwargs={
                    "wname": self.scenario.workspace.name,
                    "ccat": self.trixie.category,
                    "cname": "sid",
                },
            )
        )
        self.assertResponseHTML(response, status_code=status.HTTP_404_NOT_FOUND)

    def test_defaults(self) -> None:
        """Test collection search view."""
        response = self.client.get(self.trixie.get_absolute_url_search())
        tree = self.assertResponseHTML(response)

        table = self.assertHasElement(
            tree, "//table[@id='collection-item-list']"
        )
        self.assertEqual(len(table.tbody.tr), 8)

        ctx = response.context

        items = ctx["paginator"].page_obj.object_list
        self.assertCountEqual(
            items, [v for k, v in self.items.items() if k != "libold0"]
        )

        self.assertEqual(ctx["collection"], self.trixie)

        sample_item = items[0]
        self.assertEqual(
            template_ui_shortcuts(sample_item),
            [
                ui_shortcuts.create_collection_item(sample_item),
                ui_shortcuts.create_artifact_view(sample_item.artifact),
                ui_shortcuts.create_artifact_download(sample_item.artifact),
            ],
        )

        paginator = ctx["paginator"]
        self.assertEqual(paginator.page_obj.number, 1)
        self.assertEqual(paginator.per_page, 50)
        self.assertEqual(paginator.count, 8)
        self.assertEqual(paginator.num_pages, 1)

    def test_ui_shortcuts(self) -> None:
        """Check generated ui shortcuts."""
        response = self.client.get(self.trixie.get_absolute_url_search())
        self.assertResponseHTML(response)
        item = response.context["paginator"].page_obj.object_list[0]
        self.assertEqual(
            item._ui_shortcuts,
            [
                ui_shortcuts.create_collection_item(item),
                ui_shortcuts.create_artifact_view(item.artifact),
                ui_shortcuts.create_artifact_download(item.artifact),
            ],
        )

    def test_ui_shortcuts_bare_items(self) -> None:
        """Check generated ui shortcuts for bare items."""
        collection = self.scenario.workspace.get_singleton_collection(
            user=self.scenario.user,
            category=CollectionCategory.PACKAGE_BUILD_LOGS,
        )
        data = {
            "work_request_id": 1,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
            "srcpkg_name": "hello",
            "srcpkg_version": "1.0-1",
        }
        manager = DebianPackageBuildLogsManager(collection=collection)
        manager.add_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            user=self.scenario.user,
            data=data,
        )

        response = self.client.get(collection.get_absolute_url_search())
        self.assertResponseHTML(response)
        item = response.context["paginator"].page_obj.object_list[0]
        self.assertEqual(
            item._ui_shortcuts,
            [
                ui_shortcuts.create_collection_item(item),
            ],
        )

    def test_search_name(self) -> None:
        """Test collection search view."""
        response = self.client.get(
            self.trixie.get_absolute_url_search(), data={"name": "libf"}
        )
        self.assertResponseHTML(response)
        ctx = response.context
        items = ctx["paginator"].page_obj.object_list
        self.assertCountEqual(
            items, [v for k, v in self.items.items() if k.startswith("libfoo")]
        )

    def test_search_category(self) -> None:
        """Test collection search view."""
        response = self.client.get(
            self.trixie.get_absolute_url_search(),
            data={"category": ArtifactCategory.BINARY_PACKAGE},
        )
        self.assertResponseHTML(response)
        ctx = response.context
        items = ctx["paginator"].page_obj.object_list
        self.assertCountEqual(
            items, [v for k, v in self.items.items() if k.endswith("-dev")]
        )

    def test_search_bad_category(self) -> None:
        """Test collection search with an invalid category."""
        response = self.client.get(
            self.trixie.get_absolute_url_search(),
            data={"category": "does-not-exist"},
        )
        self.assertResponseHTML(response)
        ctx = response.context
        items = ctx["paginator"].page_obj.object_list
        self.assertEqual(items, [])

    def test_search_historical(self) -> None:
        """Test collection search view."""
        response = self.client.get(
            self.trixie.get_absolute_url_search(),
            data={"historical": True},
        )
        self.assertResponseHTML(response)
        ctx = response.context
        items = ctx["paginator"].page_obj.object_list
        self.assertCountEqual(items, [self.items["libold0"]])

    def test_search_pagination(self) -> None:
        """Test collection search view pagination."""
        with mock.patch(
            "debusine.web.views.collections"
            ".CollectionSearchView.get_paginate_by",
            return_value=4,
        ):
            response = self.client.get(self.trixie.get_absolute_url_search())
        self.assertResponseHTML(response)

        ctx = response.context

        items = ctx["paginator"].page_obj.object_list
        self.assertEqual(len(items), 4)

        paginator = ctx["paginator"]
        self.assertEqual(paginator.page_obj.number, 1)
        self.assertEqual(paginator.per_page, 4)
        self.assertEqual(paginator.count, 8)
        self.assertEqual(paginator.num_pages, 2)

    def test_search_query_count(self) -> None:
        """Test that collection search makes a reasonable amount of queries."""
        with self.assertNumQueries(13):
            self.client.get(self.trixie.get_absolute_url_search())


class CollectionItemDetailViewTests(CollectionViewsTestsBase):
    """Tests for :py:class:`CollectionItemDetailView`."""

    item: ClassVar[CollectionItem]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        manager = DebianSuiteManager(collection=cls.trixie)
        artifact = cls.playground.create_source_artifact(name="libfoo0")
        cls.item = manager.add_source_package(
            artifact,
            user=cls.scenario.user,
            component="main",
            section="shlibs",
        )

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = CollectionItemDetailView()
        view.object = self.item
        self.assertEqual(view.get_title(), self.item.name)

    def test_permissions(self) -> None:
        """Test permissions on the collection list view."""
        url = self.item.get_absolute_url()
        self.assertSetsCurrentWorkspace(self.scenario.workspace, url)

    def test_defaults(self) -> None:
        """Test viewing an item."""
        url = self.item.get_absolute_url()
        response = self.client.get(url)
        tree = self.assertResponseHTML(response)
        metadata = self.assertHasElement(tree, "//div[@id='metadata']")
        self.assertYAMLContentEqual(metadata.div[1], self.item.data)

    def test_no_item_data(self) -> None:
        """Test viewing an item."""
        self.item.data = {}
        self.item.save()
        url = self.item.get_absolute_url()
        response = self.client.get(url)
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//div[@id='metadata']"))

    def test_sidebar(self) -> None:
        """Test sidebar generation."""
        url = self.item.get_absolute_url()
        response = self.client.get(url)
        self.assertResponseHTML(response)
        actual_items = response.context["sidebar_items"]
        expected_items = [
            sidebar.create_collection(self.trixie),
            sidebar.create_workspace(self.scenario.workspace),
            sidebar.create_user(self.scenario.user, context=self.item),
            sidebar.create_created_at(self.item.created_at),
        ]
        self.assertEqual(actual_items, expected_items)

    def test_wrong_item(self) -> None:
        """Test viewing an item."""
        max_id = CollectionItem.objects.aggregate(Max('id'))['id__max'] + 1
        response = self.client.get(
            reverse(
                "workspaces:collections:item_detail",
                kwargs={
                    "wname": self.scenario.workspace.name,
                    "ccat": self.trixie.category,
                    "cname": self.trixie.name,
                    "iid": str(max_id),
                    "iname": "test",
                },
            )
        )
        self.assertResponseHTML(response, status_code=status.HTTP_404_NOT_FOUND)
