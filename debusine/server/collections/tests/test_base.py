# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the CollectionManagerInterface."""
from unittest.mock import patch

from django.contrib.auth import get_user_model

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.db.models import Collection, CollectionItem, default_workspace
from debusine.server.collections import (
    CollectionManagerInterface,
    DebianSuiteManager,
    ItemAdditionError,
)
from debusine.test.django import TestCase


class DebusineTestManager(CollectionManagerInterface):
    """Collection manager used for testing."""

    COLLECTION_CATEGORY = CollectionCategory.TEST


class CollectionManagerInterfaceTests(TestCase):
    """Tests for CollectionManagerInterface."""

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="John", email="john@example.org"
        )
        template = self.playground.create_workflow_template(
            name="test",
            task_name="noop",
        )
        self.workflow = self.playground.create_workflow(
            template, task_data={}, created_by=self.user
        )

        self.workspace = default_workspace()

        self.collection = Collection.objects.create(
            name="Test Collection",
            category=CollectionCategory.TEST,
            workspace=self.workspace,
        )
        self.manager = DebusineTestManager(self.collection)

    def test_add_bare_data(self) -> None:
        """Test add_bare_data() call do_add_bare_data()."""
        category = BareDataCategory.TEST
        self.manager.VALID_BARE_DATA_CATEGORIES = frozenset({category})

        with patch.object(self.manager, "do_add_bare_data") as mocked:
            self.manager.add_bare_data(category, user=self.user)

        mocked.assert_called_with(
            category,
            user=self.user,
            data=None,
            workflow=None,
            name=None,
            replace=False,
            created_at=None,
            replaced_by=None,
        )

    def test_add_bare_data_invalid_category_raise_item_addition_error(
        self,
    ) -> None:
        """Test add_bare_data() raise ItemAdditionError(): invalid category."""
        category = "invalid"

        msg = (
            f'^Bare data category "{category}" not supported by the collection$'
        )
        self.assertRaisesRegex(
            ItemAdditionError,
            msg,
            self.manager.add_bare_data,
            category,
            user=self.user,
        )

    def test_add_bare_data_any_category_valid(self) -> None:
        """If VALID_BARE_DATA_CATEGORIES is None, any category is valid."""
        category = BareDataCategory.TEST
        self.manager.VALID_BARE_DATA_CATEGORIES = None

        with patch.object(self.manager, "do_add_bare_data") as mocked:
            self.manager.add_bare_data(category, user=self.user)

        mocked.assert_called_with(
            category,
            user=self.user,
            workflow=None,
            data=None,
            name=None,
            replace=False,
            created_at=None,
            replaced_by=None,
        )

    def test_add_artifact(self) -> None:
        """Test add_artifact() call do_add_artifact()."""
        category = ArtifactCategory.TEST
        artifact, _ = self.playground.create_artifact(category=category)

        self.manager.VALID_ARTIFACT_CATEGORIES = frozenset({category})

        with patch.object(self.manager, "do_add_artifact") as mocked:
            self.manager.add_artifact(artifact, user=self.user)

        mocked.assert_called_with(
            artifact,
            user=self.user,
            workflow=None,
            variables=None,
            name=None,
            replace=False,
            created_at=None,
            replaced_by=None,
        )

    def test_add_artifact_invalid_category_raise_item_addition_error(
        self,
    ) -> None:
        """Test add_artifact() raise ItemAdditionError(): invalid category."""
        category = "invalid"
        artifact, _ = self.playground.create_artifact(
            category=category,  # type: ignore[arg-type]
        )

        msg = (
            f'^Artifact category "{category}" '
            f'not supported by the collection$'
        )
        self.assertRaisesRegex(
            ItemAdditionError,
            msg,
            self.manager.add_artifact,
            artifact,
            user=self.user,
        )

    def test_add_artifact_any_category_valid(self) -> None:
        """If VALID_ARTIFACT_CATEGORIES is None, any category is valid."""
        category = ArtifactCategory.TEST
        artifact, _ = self.playground.create_artifact(category=category)
        self.manager.VALID_ARTIFACT_CATEGORIES = None

        with patch.object(self.manager, "do_add_artifact") as mocked:
            self.manager.add_artifact(artifact, user=self.user)

        mocked.assert_called_with(
            artifact,
            user=self.user,
            workflow=None,
            variables=None,
            name=None,
            replace=False,
            created_at=None,
            replaced_by=None,
        )

    def test_add_collection(self) -> None:
        """Test add_collection() call do_add_collection()."""
        category = CollectionCategory.TEST

        self.manager.VALID_COLLECTION_CATEGORIES = frozenset({category})

        collection = Collection.objects.create(
            name="Testing", category=category, workspace=self.workspace
        )

        with patch.object(self.manager, "do_add_collection") as mocked:
            self.manager.add_collection(collection, user=self.user)

        mocked.assert_called_with(
            collection,
            user=self.user,
            workflow=None,
            variables=None,
            name=None,
            replace=False,
            created_at=None,
            replaced_by=None,
        )

    def test_add_collection_invalid_category_raise_item_addition_error(
        self,
    ) -> None:
        """Test add_artifact() raise ItemAdditionError(): invalid category."""
        category = "invalid"
        collection = Collection.objects.create(
            name="Some name", category=category, workspace=self.workspace
        )

        msg = (
            f'^Collection category "{category}" '
            f'not supported by the collection$'
        )
        self.assertRaisesRegex(
            ItemAdditionError,
            msg,
            self.manager.add_collection,
            collection,
            user=self.user,
        )

    def test_add_collection_any_category_valid(self) -> None:
        """If VALID_COLLECTION_CATEGORIES is None, any category is valid."""
        category = "debusine:anything"
        collection = Collection.objects.create(
            name="Some name", category=category, workspace=self.workspace
        )
        self.manager.VALID_COLLECTION_CATEGORIES = None

        with patch.object(self.manager, "do_add_collection") as mocked:
            self.manager.add_collection(collection, user=self.user)

        mocked.assert_called_with(
            collection,
            user=self.user,
            workflow=None,
            variables=None,
            name=None,
            replace=False,
            created_at=None,
            replaced_by=None,
        )

    def test_remove_item_bare_data(self) -> None:
        """remove_item() calls do_remove_item() for bare data."""
        item = self.playground.create_bare_data_item(self.collection, "test")

        with patch.object(self.manager, "do_remove_item") as mocked:
            self.manager.remove_item(
                item, user=self.user, workflow=self.workflow
            )

        mocked.assert_called_with(item, user=self.user, workflow=self.workflow)

    def test_remove_item_artifact(self) -> None:
        """remove_item() calls do_remove_item() for an artifact."""
        artifact, _ = self.playground.create_artifact()
        item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.playground.get_default_user(),
        )

        with patch.object(self.manager, "do_remove_item") as mocked:
            self.manager.remove_item(item, user=self.user, workflow=None)

        mocked.assert_called_with(item, user=self.user, workflow=None)

    def test_remove_item_collection(self) -> None:
        """remove_item() calls do_remove_item() for a collection."""
        collection = Collection.objects.create(
            name="Testing", category="Testing", workspace=self.workspace
        )
        item = CollectionItem.objects.create_from_collection(
            collection,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.playground.get_default_user(),
        )

        with patch.object(self.manager, "do_remove_item") as mocked:
            self.manager.remove_item(
                item, user=self.user, workflow=self.workflow
            )

        mocked.assert_called_with(item, user=self.user, workflow=self.workflow)

    def test_remove_items_by_name_bare_data(self) -> None:
        """remove_items_by_name() calls do_remove_item() for bare data."""
        item = self.playground.create_bare_data_item(self.collection, "test")

        with patch.object(self.manager, "do_remove_item") as mocked:
            self.manager.remove_items_by_name(
                name="test",
                child_types=[CollectionItem.Types.BARE],
                user=self.user,
                workflow=self.workflow,
            )

        mocked.assert_called_with(item, user=self.user, workflow=self.workflow)

    def test_remove_items_by_name_artifact(self) -> None:
        """remove_items_by_name() calls do_remove_item() for an artifact."""
        artifact, _ = self.playground.create_artifact()
        item = CollectionItem.objects.create(
            name="test",
            artifact=artifact,
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
            created_by_user=self.user,
        )

        with patch.object(self.manager, "do_remove_item") as mocked:
            self.manager.remove_items_by_name(
                name="test",
                child_types=[CollectionItem.Types.ARTIFACT],
                user=self.user,
                workflow=self.workflow,
            )

        mocked.assert_called_with(item, user=self.user, workflow=self.workflow)

    def test_remove_items_by_name_collection(self) -> None:
        """remove_items_by_name() calls do_remove_item() for a collection."""
        collection = self.playground.create_collection(
            name="Collection Test", category=CollectionCategory.TEST
        )

        item = CollectionItem.objects.create(
            name="test",
            collection=collection,
            parent_collection=self.collection,
            child_type=CollectionItem.Types.COLLECTION,
            created_by_user=self.user,
        )

        with patch.object(self.manager, "do_remove_item") as mocked:
            self.manager.remove_items_by_name(
                name="test",
                child_types=[CollectionItem.Types.COLLECTION],
                user=self.user,
                workflow=self.workflow,
            )

        mocked.assert_called_with(item, user=self.user, workflow=self.workflow)

    def test_lookup_unexpected_format_raise_lookup_error(self) -> None:
        """`lookup` raises `LookupError`: invalid format."""
        with self.assertRaisesRegex(
            LookupError, '^Unexpected lookup format: "nonsense"$'
        ):
            self.manager.lookup("nonsense")

    def test_lookup_return_none(self) -> None:
        """`lookup` returns None if there are no matches."""
        self.assertIsNone(self.manager.lookup("name:nonexistent"))

    def test_lookup_filter_unexpected_format_raise_lookup_error(self) -> None:
        """`lookup_filter` raises `LookupError`: invalid format."""
        with self.assertRaisesRegex(
            LookupError, '^Unexpected lookup filter format: "nonsense"$'
        ):
            self.manager.lookup_filter(
                "nonsense",
                "foo",
                workspace=self.playground.get_default_workspace(),
                user=self.playground.get_default_user(),
            )

    def test_get_manager_for(self) -> None:
        """Test getting specialized manager."""
        collection = Collection.objects.create(
            name="Testing",
            category=CollectionCategory.SUITE,
            workspace=self.workspace,
        )
        manager = CollectionManagerInterface.get_manager_for(collection)
        self.assertEqual(type(manager), DebianSuiteManager)
