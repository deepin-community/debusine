# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the collection models."""

from datetime import datetime, timedelta
from datetime import timezone as tz
from typing import ClassVar
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebusinePromise,
)
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import (
    Collection,
    CollectionItem,
    User,
    WorkRequest,
    Workspace,
)
from debusine.db.playground import scenarios
from debusine.server.collections import DebianSuiteManager
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    override_permission,
)


class CollectionManagerTests(TestCase):
    """Tests for CollectionManager class."""

    scenario = scenarios.DefaultContext()

    def test_create_singleton(self) -> None:
        """Create a singleton collection."""
        workspace = self.playground.create_workspace(name="test")
        collection, _ = Collection.objects.get_or_create_singleton(
            category=CollectionCategory.PACKAGE_BUILD_LOGS,
            workspace=workspace,
        )
        self.assertEqual(collection.name, "_")
        self.assertEqual(
            collection.category, CollectionCategory.PACKAGE_BUILD_LOGS
        )
        self.assertEqual(collection.workspace, workspace)
        self.assertEqual(collection.data, {})

    def test_create_singleton_bad_category(self) -> None:
        """Singleton collections may only be of certain categories."""
        with self.assertRaisesRegex(
            ValueError,
            f"'{CollectionCategory.TEST}' is not a singleton collection "
            f"category",
        ):
            Collection.objects.get_or_create_singleton(
                category=CollectionCategory.TEST,
                workspace=self.scenario.workspace,
            )

    def test_in_current_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter."""
        standard_collections = list(
            Collection.objects.filter(workspace=self.scenario.workspace)
        )
        collection = self.playground.create_collection(
            name="test", category=CollectionCategory.TEST
        )

        with context.local():
            context.set_scope(self.scenario.scope)
            self.assertQuerySetEqual(
                Collection.objects.in_current_scope(),
                [collection] + standard_collections,
                ordered=False,
            )

        scope1 = self.playground.get_or_create_scope(name="Scope1")
        with context.local():
            context.set_scope(scope1)
            self.assertQuerySetEqual(
                Collection.objects.in_current_scope(),
                [],
            )

    def test_in_current_scope_no_context_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter without scope set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "scope is not set"
        ):
            Collection.objects.in_current_scope()

    def test_in_current_workspace(self) -> None:
        """Test the in_current_workspace() QuerySet filter."""
        standard_collections = list(
            Collection.objects.filter(workspace=self.scenario.workspace)
        )

        collection = self.playground.create_collection(
            name="test", category=CollectionCategory.TEST
        )
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)

        with context.local():
            self.scenario.workspace.set_current()
            self.assertQuerySetEqual(
                Collection.objects.in_current_workspace(),
                [collection] + standard_collections,
                ordered=False,
            )

        workspace1 = self.playground.create_workspace(name="other", public=True)
        with context.local():
            workspace1.set_current()
            self.assertQuerySetEqual(
                Collection.objects.in_current_workspace(),
                [],
            )

    def test_in_current_workspace_no_context_workspace(self) -> None:
        """Test in_current_workspace() without workspace set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "workspace is not set"
        ):
            Collection.objects.in_current_workspace()


class CollectionTests(TestCase):
    """Tests for Collection class."""

    scenario = scenarios.DefaultContext()

    def test_str(self) -> None:
        """Test stringification."""
        name = "Environments"
        category = CollectionCategory.ENVIRONMENTS
        collection = Collection.objects.create(
            name=name, category=category, workspace=self.scenario.workspace
        )
        self.assertEqual(str(collection), f"{name}@{category}")

        singleton = Collection.objects.get(
            category=CollectionCategory.PACKAGE_BUILD_LOGS,
            workspace=self.scenario.workspace,
        )
        self.assertEqual(
            str(singleton), f"_@{CollectionCategory.PACKAGE_BUILD_LOGS}"
        )

    def test_get_absolute_url(self) -> None:
        """Test get_absolute_url results."""
        name = "Environments"
        category = CollectionCategory.ENVIRONMENTS
        collection = Collection.objects.create(
            name=name, category=category, workspace=self.scenario.workspace
        )

        self.assertEqual(
            collection.get_absolute_url(),
            f"/{self.scenario.scope.name}/{self.scenario.workspace.name}"
            f"/collection/{CollectionCategory.ENVIRONMENTS}/{name}/",
        )

    def test_get_absolute_url_different_scope(self) -> None:
        """get_absolute_url works in another scope."""
        other_scope = self.playground.get_or_create_scope(name="other")
        other_workspace = self.playground.create_workspace(scope=other_scope)
        name = "Environments"
        category = CollectionCategory.ENVIRONMENTS
        collection = Collection.objects.create(
            name=name, category=category, workspace=other_workspace
        )

        self.assertEqual(
            collection.get_absolute_url(),
            f"/{other_scope.name}/{other_workspace.name}"
            f"/collection/{CollectionCategory.ENVIRONMENTS}/{name}/",
        )

    def test_get_absolute_url_search(self) -> None:
        """Test get_absolute_url_search results."""
        name = "Environments"
        category = CollectionCategory.ENVIRONMENTS
        collection = Collection.objects.create(
            name=name, category=category, workspace=self.scenario.workspace
        )

        self.assertEqual(
            collection.get_absolute_url_search(),
            f"/{self.scenario.scope.name}/{self.scenario.workspace.name}"
            f"/collection/{CollectionCategory.ENVIRONMENTS}/{name}/search/",
        )

    def test_get_absolute_url_search_different_scope(self) -> None:
        """get_absolute_url_search works in another scope."""
        other_scope = self.playground.get_or_create_scope(name="other")
        other_workspace = self.playground.create_workspace(scope=other_scope)
        name = "Environments"
        category = CollectionCategory.ENVIRONMENTS
        collection = Collection.objects.create(
            name=name, category=category, workspace=other_workspace
        )

        self.assertEqual(
            collection.get_absolute_url_search(),
            f"/{other_scope.name}/{other_workspace.name}"
            f"/collection/{CollectionCategory.ENVIRONMENTS}/{name}/search/",
        )

    def test_constraint_name_category_workspace(self) -> None:
        """Raise integrity error: name, category, workspace must be unique."""
        name = "Environments"
        category = CollectionCategory.ENVIRONMENTS

        Collection.objects.create(
            name=name, category=category, workspace=self.scenario.workspace
        )

        with (
            transaction.atomic(),
            self.assertRaisesRegex(
                IntegrityError, "db_collection_unique_name_category_workspace"
            ),
        ):
            Collection.objects.create(
                name=name, category=category, workspace=self.scenario.workspace
            )

        other_workspace = self.playground.create_workspace(name="other")
        Collection.objects.create(
            name=name, category=category, workspace=other_workspace
        )

    def test_name_not_empty(self) -> None:
        """Cannot create Collection with an empty name."""
        with self.assertRaisesRegex(
            IntegrityError, "db_collection_name_not_empty"
        ):
            Collection.objects.create(
                name="",
                category=CollectionCategory.ENVIRONMENTS,
                workspace=self.scenario.workspace,
            )

    def test_category_not_empty(self) -> None:
        """Cannot create Collection with an empty category."""
        with self.assertRaisesRegex(
            IntegrityError, "db_collection_category_not_empty"
        ):
            Collection.objects.create(
                name="name", category="", workspace=self.scenario.workspace
            )

    def test_retains_artifacts(self) -> None:
        """By default, a new Collection stops its artifacts expiring."""
        collection = Collection.objects.create(
            name="Environments",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=self.scenario.workspace,
        )
        self.assertEqual(
            collection.retains_artifacts, Collection.RetainsArtifacts.ALWAYS
        )

    def test_manager(self) -> None:
        """Test getting specialized manager."""
        collection = Collection.objects.create(
            name="Testing",
            category=CollectionCategory.SUITE,
            workspace=self.scenario.workspace,
        )
        self.assertEqual(type(collection.manager), DebianSuiteManager)

    def test_child_items_artifacts_collections(self) -> None:
        """Child items returns expected CollectionItems, artifacts, etc."""
        collection_parent_1 = Collection.objects.create(
            name="collection-1",
            category="collection-1",
            workspace=self.scenario.workspace,
        )
        collection_parent_2 = Collection.objects.create(
            name="collection-2",
            category="collection-2",
            workspace=self.scenario.workspace,
        )

        collection = Collection.objects.create(
            name="collection-3",
            category="collection-3",
            workspace=self.scenario.workspace,
        )
        collection_item_collection_1 = CollectionItem.objects.create(
            name="test",
            category="test",
            parent_collection=collection_parent_1,
            child_type=CollectionItem.Types.COLLECTION,
            collection=collection,
            created_by_user=self.scenario.user,
        )

        artifact, _ = self.playground.create_artifact()
        artifact_item_collection_1 = CollectionItem.objects.create(
            name="test-2",
            category="test-2",
            parent_collection=collection_parent_1,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact,
            created_by_user=self.scenario.user,
        )

        # Used to see that is not returned when querying
        # collection_parent_1.child_items.all()
        CollectionItem.objects.create(
            name="test",
            category="test",
            parent_collection=collection_parent_2,
            child_type=CollectionItem.Types.COLLECTION,
            collection=Collection.objects.create(
                name="collection-4",
                category="collection-3",
                workspace=self.scenario.workspace,
            ),
            created_by_user=self.scenario.user,
        )

        # Collection.child_items returns all CollectionItems
        self.assertQuerySetEqual(
            collection_parent_1.child_items.all(),
            [collection_item_collection_1, artifact_item_collection_1],
            ordered=False,
        )

        # Collection.child_artifacts returns only the artifacts
        self.assertQuerySetEqual(
            collection_parent_1.child_artifacts.all(), [artifact]
        )

        # Collection.child_collections returns only the collections
        self.assertQuerySetEqual(
            collection_parent_1.child_collections.all(), [collection]
        )

        # Given the artifact: there is a reverse relationship
        # with the collections that belongs to
        self.assertQuerySetEqual(
            artifact.parent_collections.all(),
            [collection_parent_1],
        )

        # Given a collection: there is a reverse relationship
        # with the collections that belongs to
        self.assertQuerySetEqual(
            collection.parent_collections.all(),
            [collection_parent_1],
        )

        # Given a artifact: there is a reverse relationship
        # to the CollectionItems that belongs to
        self.assertQuerySetEqual(
            artifact.collection_items.all(),
            [artifact_item_collection_1],
        )

        # Given a collection: there is a reverse relationship
        # to the CollectionItems that belongs to
        self.assertQuerySetEqual(
            collection.collection_items.all(),
            [collection_item_collection_1],
        )

    def test_can_display_delegate_to_workspace(self) -> None:
        """Test the can_display predicate on public workspaces."""
        standard_collections = list(
            Collection.objects.filter(workspace=self.scenario.workspace)
        )
        collection = self.playground.create_collection(
            name="test", category=CollectionCategory.TEST
        )
        with override_permission(Workspace, "can_display", AllowAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                allowed=[collection] + standard_collections,
            )
        with override_permission(Workspace, "can_display", DenyAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                denied=[collection] + standard_collections,
            )


class CollectionItemManagerTests(TestCase):
    """Tests for CollectionItemManager class."""

    user: ClassVar[User]
    workspace: ClassVar[Workspace]
    collection: ClassVar[Collection]
    workflow: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.user = cls.playground.create_user(username="John")
        cls.workspace = cls.playground.create_workspace(name="System")
        cls.collection = Collection.objects.create(
            name="Name",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=cls.workspace,
        )
        cls.workflow = cls.playground.create_work_request(task_name="noop")

    def test_create_from_bare_data(self) -> None:
        """Verify create_from_bare_data method."""
        category = BareDataCategory.TEST
        name = "some-name"
        data = {"a": "b"}

        collection_item = CollectionItem.objects.create_from_bare_data(
            category,
            parent_collection=self.collection,
            name=name,
            data=data,
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )

        self.assertEqual(collection_item.parent_collection, self.collection)
        self.assertEqual(collection_item.name, name)
        self.assertEqual(collection_item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(collection_item.category, category)
        self.assertEqual(collection_item.data, data)
        self.assertEqual(collection_item.created_by_user, self.user)
        self.assertEqual(collection_item.created_by_workflow, self.workflow)

    def test_create_from_bare_data_debusine_promise(self) -> None:
        """Verify create_from_bare_data with debusine:promise category."""
        category = BareDataCategory.PROMISE
        name = "valid-promise"
        data = DebusinePromise(
            promise_work_request_id=1,
            promise_workflow_id=100,
            promise_category=ArtifactCategory.BINARY_PACKAGE,
        )

        # Create a CollectionItem with valid DebusinePromiseData
        collection_item = CollectionItem.objects.create_from_bare_data(
            category,
            parent_collection=self.collection,
            name=name,
            data=data,
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )

        # Assert that the collection item is created correctly
        self.assertEqual(collection_item.parent_collection, self.collection)
        self.assertEqual(collection_item.name, name)
        self.assertEqual(collection_item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(collection_item.category, category)
        self.assertEqual(collection_item.data, data)
        self.assertEqual(collection_item.created_by_user, self.user)

    def test_create_from_bare_data_debusine_promise_invalid_data(self) -> None:
        """
        Verify create_from_bare_data raises ValueError.

        Invalid debusine:promise data.
        """
        category = BareDataCategory.PROMISE
        name = "invalid-promise"
        # Invalid data (missing required fields like promise_work_request_id)
        data = {
            "promise_workflow_id": 100,
            "promise_category": "debian:binary-package",
        }

        # Expect a ValueError due to missing promise_work_request_id in the data
        with self.assertRaises(ValueError):
            CollectionItem.objects.create_from_bare_data(
                category,
                parent_collection=self.collection,
                name=name,
                data=data,
                created_by_user=self.user,
                created_by_workflow=self.workflow,
            )

    def test_create_from_bare_data_created_at(self) -> None:
        created_at = timezone.now() - timedelta(days=1)

        collection_item = CollectionItem.objects.create_from_bare_data(
            BareDataCategory.TEST,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
            created_at=created_at,
        )

        self.assertEqual(collection_item.created_at, created_at)
        self.assertIsNone(collection_item.removed_at)

    def test_create_from_bare_data_created_at_replaced_by(self) -> None:
        current_item = CollectionItem.objects.create_from_bare_data(
            BareDataCategory.TEST,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )
        created_at = timezone.now() - timedelta(days=1)

        old_item = CollectionItem.objects.create_from_bare_data(
            BareDataCategory.TEST,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
            created_at=created_at,
            replaced_by=current_item,
        )

        self.assertEqual(old_item.created_at, created_at)
        self.assertEqual(old_item.removed_at, current_item.created_at)
        self.assertEqual(old_item.removed_by_user, current_item.created_by_user)
        self.assertEqual(
            old_item.removed_by_workflow, current_item.created_by_workflow
        )

    def test_create_from_artifact(self) -> None:
        """Verify create_from_artifact method."""
        data = {"a": "b"}
        artifact, _ = self.playground.create_artifact(data=data)

        name = "some-name"
        collection_item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=self.collection,
            name=name,
            data=data,
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )

        self.assertEqual(collection_item.parent_collection, self.collection)
        self.assertEqual(collection_item.name, name)
        self.assertEqual(collection_item.artifact, artifact)
        self.assertEqual(
            collection_item.child_type, CollectionItem.Types.ARTIFACT
        )
        self.assertEqual(collection_item.category, artifact.category)
        self.assertEqual(collection_item.data, data)
        self.assertEqual(collection_item.created_by_user, self.user)
        self.assertEqual(collection_item.created_by_workflow, self.workflow)

    def test_create_from_artifact_created_at(self) -> None:
        artifact, _ = self.playground.create_artifact()
        created_at = timezone.now() - timedelta(days=1)

        collection_item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
            created_at=created_at,
        )

        self.assertEqual(collection_item.created_at, created_at)
        self.assertIsNone(collection_item.removed_at)

    def test_create_from_artifact_created_at_replaced_by(self) -> None:
        current_artifact, _ = self.playground.create_artifact()
        current_item = CollectionItem.objects.create_from_artifact(
            current_artifact,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )
        created_at = timezone.now() - timedelta(days=1)

        old_artifact, _ = self.playground.create_artifact()
        old_item = CollectionItem.objects.create_from_artifact(
            old_artifact,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
            created_at=created_at,
            replaced_by=current_item,
        )

        self.assertEqual(old_item.created_at, created_at)
        self.assertEqual(old_item.removed_at, current_item.created_at)
        self.assertEqual(old_item.removed_by_user, current_item.created_by_user)
        self.assertEqual(
            old_item.removed_by_workflow, current_item.created_by_workflow
        )

    def test_create_from_collection(self) -> None:
        """Verify create_from_collection method."""
        category = "some-category"
        name = "collection-name"
        data = {"a": "b"}

        collection = Collection.objects.create(
            name="collection",
            category=category,
            workspace=self.workspace,
        )

        collection_item = CollectionItem.objects.create_from_collection(
            collection,
            parent_collection=self.collection,
            name=name,
            data=data,
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )

        self.assertEqual(collection_item.parent_collection, self.collection)
        self.assertEqual(collection_item.name, name)
        self.assertEqual(collection_item.collection, collection)
        self.assertEqual(
            collection_item.child_type, CollectionItem.Types.COLLECTION
        )
        self.assertEqual(collection_item.category, category)
        self.assertEqual(collection_item.data, data)
        self.assertEqual(collection_item.created_by_user, self.user)
        self.assertEqual(collection_item.created_by_workflow, self.workflow)

    def test_create_from_collection_created_at(self) -> None:
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        created_at = timezone.now() - timedelta(days=1)

        collection_item = CollectionItem.objects.create_from_collection(
            collection,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
            created_at=created_at,
        )

        self.assertEqual(collection_item.created_at, created_at)
        self.assertIsNone(collection_item.removed_at)

    def test_create_from_collection_created_at_replaced_by(self) -> None:
        current_collection = self.playground.create_collection(
            "test-current", CollectionCategory.TEST
        )
        current_item = CollectionItem.objects.create_from_collection(
            current_collection,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
        )
        created_at = timezone.now() - timedelta(days=1)

        old_collection = self.playground.create_collection(
            "test-old", CollectionCategory.TEST
        )
        old_item = CollectionItem.objects.create_from_collection(
            old_collection,
            parent_collection=self.collection,
            name="test",
            data={},
            created_by_user=self.user,
            created_by_workflow=self.workflow,
            created_at=created_at,
            replaced_by=current_item,
        )

        self.assertEqual(old_item.created_at, created_at)
        self.assertEqual(old_item.removed_at, current_item.created_at)
        self.assertEqual(old_item.removed_by_user, current_item.created_by_user)
        self.assertEqual(
            old_item.removed_by_workflow, current_item.created_by_workflow
        )

    def test_drop_full_history(self) -> None:
        """Verify drop_full_history method."""
        data = {"a": "b"}
        artifact, _ = self.playground.create_artifact(data=data)

        self.collection.full_history_retention_period = timedelta(days=2)
        self.collection.save()

        collection_item_removed = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=3),
        )

        collection_item_young = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=1),
        )

        collection_item_keep = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
        )

        CollectionItem.objects.drop_full_history(timezone.now())

        collection_item_removed.refresh_from_db()
        self.assertIsNone(collection_item_removed.artifact)

        collection_item_young.refresh_from_db()
        self.assertIsNotNone(collection_item_young.artifact)

        collection_item_keep.refresh_from_db()
        self.assertIsNotNone(collection_item_keep.artifact)

    def test_drop_full_history_no_retention_period(self) -> None:
        """Verify drop_full_history method without retention_period."""
        data = {"a": "b"}
        artifact, _ = self.playground.create_artifact(data=data)

        collection_item_removed = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=3),
        )

        collection_item_young = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=1),
        )

        collection_item_keep = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
        )

        CollectionItem.objects.drop_full_history(timezone.now())

        collection_item_removed.refresh_from_db()
        self.assertIsNotNone(collection_item_removed.artifact)

        collection_item_young.refresh_from_db()
        self.assertIsNotNone(collection_item_young.artifact)

        collection_item_keep.refresh_from_db()
        self.assertIsNotNone(collection_item_keep.artifact)

    def test_drop_metadata(self) -> None:
        """Verify drop_metadata method."""
        data = {"a": "b"}
        artifact, _ = self.playground.create_artifact(data=data)

        self.collection.full_history_retention_period = timedelta(days=1)
        self.collection.metadata_only_retention_period = timedelta(days=1)
        self.collection.save()

        collection_item_removed = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=3),
        )

        collection_item_young = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=1),
        )

        collection_item_keep = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
        )

        CollectionItem.objects.drop_metadata(timezone.now())

        with self.assertRaises(CollectionItem.DoesNotExist):
            CollectionItem.objects.get(id=collection_item_removed.id)

        collection_item_young.refresh_from_db()
        self.assertIsNotNone(collection_item_young)

        collection_item_keep.refresh_from_db()
        self.assertIsNotNone(collection_item_keep)

    def test_drop_metadata_no_retention_period(self) -> None:
        """Verify drop_metadata method."""
        data = {"a": "b"}
        artifact, _ = self.playground.create_artifact(data=data)

        collection_item_removed = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=3),
        )

        collection_item_young = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
            removed_at=timezone.now() - timedelta(days=1),
        )

        collection_item_keep = CollectionItem.objects.create(
            parent_collection=self.collection,
            name="some-name",
            artifact=artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact.category,
            data=data,
            created_by_user=self.user,
        )

        CollectionItem.objects.drop_metadata(timezone.now())

        collection_item_removed.refresh_from_db()
        self.assertIsNotNone(collection_item_removed)

        collection_item_young.refresh_from_db()
        self.assertIsNotNone(collection_item_young)

        collection_item_keep.refresh_from_db()
        self.assertIsNotNone(collection_item_keep)

    def test_active_at(self) -> None:
        """``active_at`` filters to items active at that time."""
        items: list[CollectionItem] = []
        item = self.playground.create_debian_environment(
            collection=self.collection, codename="bookworm"
        )
        item.created_at = datetime(2024, 12, 1, tzinfo=tz.utc)
        item.save()
        items.append(item)
        for timestamp in (
            datetime(2025, 1, 1, tzinfo=tz.utc),
            datetime(2025, 2, 1, tzinfo=tz.utc),
            datetime(2025, 3, 1, tzinfo=tz.utc),
        ):
            CollectionItem.objects.active().filter(
                parent_collection=self.collection, data__codename="trixie"
            ).update(
                removed_by_user=self.playground.get_default_user(),
                removed_at=timestamp,
            )
            item = self.playground.create_debian_environment(
                collection=self.collection, codename="trixie"
            )
            item.created_at = timestamp
            item.save()
            items.append(item)
        CollectionItem.objects.active().filter(
            parent_collection=self.collection, data__codename="trixie"
        ).update(
            removed_by_user=self.playground.get_default_user(),
            removed_at=datetime(2025, 4, 1, tzinfo=tz.utc),
        )

        for timestamp, expected_items in (
            (datetime(2024, 11, 30, tzinfo=tz.utc), []),
            (datetime(2024, 12, 1, tzinfo=tz.utc), [items[0]]),
            (datetime(2024, 12, 15, tzinfo=tz.utc), [items[0]]),
            (datetime(2025, 1, 1, tzinfo=tz.utc), [items[0], items[1]]),
            (datetime(2025, 1, 15, tzinfo=tz.utc), [items[0], items[1]]),
            (datetime(2025, 2, 1, tzinfo=tz.utc), [items[0], items[2]]),
            (datetime(2025, 2, 15, tzinfo=tz.utc), [items[0], items[2]]),
            (datetime(2025, 3, 1, tzinfo=tz.utc), [items[0], items[3]]),
            (datetime(2025, 3, 15, tzinfo=tz.utc), [items[0], items[3]]),
            (datetime(2025, 4, 1, tzinfo=tz.utc), [items[0]]),
        ):
            self.assertQuerySetEqual(
                CollectionItem.objects.active_at(timestamp).filter(
                    parent_collection=self.collection
                ),
                expected_items,
                ordered=False,
            )


class CollectionItemTests(TestCase):
    """Tests for CollectionItem class."""

    def setUp(self) -> None:
        """Create objects for the tests."""
        super().setUp()
        self.artifact, _ = self.playground.create_artifact()

        self.workspace = self.playground.create_workspace(name="System")
        self.collection = Collection.objects.create(
            name="Name",
            category=CollectionCategory.TEST,
            workspace=self.workspace,
        )
        self.user = get_user_model().objects.create_user(
            username="John", email="john@example.org"
        )

    def test_artifact_collection_item_same_name(self) -> None:
        """Two CollectionItem: same name, category different child_type."""
        name = "name:duplicated"
        category = "category:duplicated"

        CollectionItem.objects.create(
            name=name,
            category=category,
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=self.artifact,
            created_by_user=self.user,
        )
        with self.assertRaisesRegex(
            IntegrityError, "db_collectionitem_unique_active_name"
        ):
            CollectionItem.objects.create(
                name=name,
                category=category,
                parent_collection=self.collection,
                child_type=CollectionItem.Types.COLLECTION,
                collection=Collection.objects.create(
                    name="Test", category="test", workspace=self.workspace
                ),
                created_by_user=self.user,
            )

    def test_collection_and_parent_collection_not_the_same(self) -> None:
        """Cannot create CollectionItem with collection == parent_collection."""
        with self.assertRaisesRegex(
            IntegrityError, "db_collectionitem_distinct_parent_collection"
        ):
            CollectionItem.objects.create(
                name="Test",
                category="Category",
                parent_collection=self.collection,
                child_type=CollectionItem.Types.COLLECTION,
                collection=self.collection,
                created_by_user=self.user,
            )

    def test_only_one_active_item_in_collection(self) -> None:
        """Cannot create duplicated CollectionItem (not removed)."""
        name = "Name of the item"
        category = ArtifactCategory.SOURCE_PACKAGE

        CollectionItem.objects.create(
            name=name,
            category=category,
            artifact=self.artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        with self.assertRaisesRegex(
            IntegrityError,
            "db_collectionitem_unique_active_name",
        ):
            CollectionItem.objects.create(
                name=name,
                category=category,
                artifact=self.artifact,
                child_type=CollectionItem.Types.ARTIFACT,
                parent_collection=self.collection,
                created_by_user=self.user,
            )

    def test_second_active_item_added_first_one_removed(self) -> None:
        """Can create "duplicated" CollectionItem if first one is removed."""
        name = "Name of the item"
        category = ArtifactCategory.SOURCE_PACKAGE

        CollectionItem.objects.create(
            name=name,
            category=category,
            child_type=CollectionItem.Types.ARTIFACT,
            parent_collection=self.collection,
            created_by_user=self.user,
            removed_at=timezone.now(),
        )

        # Collection item can be added (no exception raised) because
        # the first one is removed
        CollectionItem.objects.create(
            name=name,
            category=category,
            artifact=self.artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

    def test_artifact_related_name(self) -> None:
        """Test artifact model "related_name" to CollectionItem."""
        artifact_in_collection_1 = CollectionItem.objects.create(
            name="name-1",
            category="category",
            artifact=self.artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        artifact_in_collection_2 = CollectionItem.objects.create(
            name="name-2",
            category="category",
            artifact=self.artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        self.assertQuerySetEqual(
            self.artifact.collection_items.all(),
            {artifact_in_collection_1, artifact_in_collection_2},
            ordered=False,
        )

    def test_artifact_constraint_are_valid_or_raise_integrity_error(
        self,
    ) -> None:
        """Verify constraints specific to an ARTIFACT type CollectionItem."""
        # CollectionItem of type ARTIFACT with an artifact
        CollectionItem.objects.create(
            name="Name",
            category="Category",
            artifact=self.artifact,
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
            created_by_user=self.user,
        )

        # CollectionItem of type ARTIFACT without an artifact
        # (the artifact was removed)
        CollectionItem.objects.create(
            name="Name-2",
            category="Category-2",
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
            created_by_user=self.user,
            removed_at=timezone.now(),
        )

        collection = Collection.objects.create(
            name="Collection-2", category="Category-2", workspace=self.workspace
        )

        # CollectionItem of type ARTIFACT with a collection
        with self.assertRaisesRegex(
            IntegrityError, "db_collectionitem_childtype_removedat_consistent"
        ):
            CollectionItem.objects.create(
                name="Name-3",
                category="Category-3",
                parent_collection=self.collection,
                child_type=CollectionItem.Types.ARTIFACT,
                collection=collection,
                created_by_user=self.user,
            )

    def test_collection_constraint_are_valid_or_raise_integrity_error(
        self,
    ) -> None:
        """Verify constraints specific to a COLLECTION type CollectionItem."""
        CollectionItem.objects.create(
            name="Name",
            category="Category",
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=self.artifact,
            removed_at=timezone.now(),
            created_by_user=self.user,
        )

        collection = Collection.objects.create(
            name="Collection-2", category="Category-2", workspace=self.workspace
        )

        CollectionItem.objects.create(
            name="Name-2",
            category="Category-2",
            parent_collection=self.collection,
            child_type=CollectionItem.Types.COLLECTION,
            collection=collection,
            removed_at=timezone.now(),
            created_by_user=self.user,
        )

    def test_collection_type_item_constraints(self) -> None:
        """Verify constraints specific to a Collection type CollectionItem."""
        collection = Collection.objects.create(
            name="Collection-2", category="Category-2", workspace=self.workspace
        )

        # CollectionItem of type COLLECTION with a collection
        CollectionItem.objects.create(
            name="Name",
            category="Category",
            collection=collection,
            parent_collection=self.collection,
            child_type=CollectionItem.Types.COLLECTION,
            created_by_user=self.user,
        )

        # CollectionItem of type COLLECTION without a collection (collection
        # was removed)
        CollectionItem.objects.create(
            name="Name-2",
            category="Category-2",
            parent_collection=self.collection,
            child_type=CollectionItem.Types.COLLECTION,
            created_by_user=self.user,
            removed_at=timezone.now(),
        )

        # CollectionItem of type COLLECTION with an artifact
        with self.assertRaisesRegex(
            IntegrityError, "db_collectionitem_childtype_removedat_consistent"
        ):
            CollectionItem.objects.create(
                name="Name-3",
                category="Category-3",
                parent_collection=self.collection,
                child_type=CollectionItem.Types.COLLECTION,
                artifact=self.artifact,
                created_by_user=self.user,
            )

    def test_bare_collection_item_constraints(self) -> None:
        """Verify constraints specific to a Bare type CollectionItem."""
        CollectionItem.objects.create(
            name="Name",
            category="Bare",
            parent_collection=self.collection,
            child_type=CollectionItem.Types.BARE,
            created_by_user=self.user,
        )

        with self.assertRaisesRegex(
            IntegrityError, "db_collectionitem_childtype_removedat_consistent"
        ):
            CollectionItem.objects.create(
                name="Name",
                category="Category",
                artifact=self.artifact,
                parent_collection=self.collection,
                child_type=CollectionItem.Types.BARE,
                created_by_user=self.user,
            )

    def test_debian_environments_no_more_than_one_codename_architecture(
        self,
    ) -> None:
        """Cannot create more than one duplicated active debian:environments."""
        collection = Collection.objects.create(
            name="Name",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=self.workspace,
        )
        category = ArtifactCategory.SYSTEM_TARBALL
        data = {"codename": "bookworm", "architecture": "amd64"}

        artifact_1, _ = self.playground.create_artifact(category=category)
        CollectionItem.objects.create(
            name="bookworm-amd64",
            category=category,
            parent_collection=collection,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_1,
            created_by_user=self.user,
            data=data,
            removed_at=timezone.now(),
        )

        # Can be created because the previous one is removed
        artifact_2, _ = self.playground.create_artifact(category=category)
        CollectionItem.objects.create(
            name="bookworm-amd64-2",
            category=category,
            parent_collection=collection,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_2,
            created_by_user=self.user,
            data=data,
        )

        # Can be created because it's different variant
        artifact_3, _ = self.playground.create_artifact(category=category)
        CollectionItem.objects.create(
            name="bookworm-amd64-3",
            category=category,
            parent_collection=collection,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_3,
            created_by_user=self.user,
            data={**data, "variant": "buildd"},
        )

        # Can be created because it's different backend
        artifact_4, _ = self.playground.create_artifact(category=category)
        CollectionItem.objects.create(
            name="bookworm-amd64-4",
            category=category,
            parent_collection=collection,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_4,
            created_by_user=self.user,
            data={**data, "backend": "unshare"},
        )

        # Can be created: duplicated but in a different collection
        artifact_5, _ = self.playground.create_artifact(category=category)
        CollectionItem.objects.create(
            name="bookworm-amd64-5",
            category=category,
            parent_collection=Collection.objects.create(
                name="Testing",
                category=CollectionCategory.ENVIRONMENTS,
                workspace=self.workspace,
            ),
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_5,
            created_by_user=self.user,
            data={**data, "variant": "buildd"},
        )

        # Cannot be created because already one active artifact
        # with the same data in the same collection (from artifact_3).
        artifact_6, _ = self.playground.create_artifact(category=category)
        msg = "db_collectionitem_unique_debian_environment"
        with self.assertRaisesRegex(IntegrityError, msg):
            CollectionItem.objects.create(
                name="bookworm-amd64-6",
                category=category,
                parent_collection=collection,
                child_type=CollectionItem.Types.ARTIFACT,
                artifact=artifact_6,
                created_by_user=self.user,
                data={**data, "variant": "buildd"},
            )

    def test_str_collection_item_artifact(self) -> None:
        """Stringification is correct for CollectionItem for ARTIFACT."""
        name = "Name of the item"
        category = ArtifactCategory.SOURCE_PACKAGE
        artifact_item = CollectionItem.objects.create(
            name=name,
            category=category,
            artifact=self.artifact,
            child_type=CollectionItem.Types.ARTIFACT,
            parent_collection=self.collection,
            created_by_user=self.user,
        )
        self.assertEqual(
            str(artifact_item),
            f"Id: {artifact_item.id} Name: {name} "
            f"Parent collection id: {self.collection.id} "
            f"Child type: {artifact_item.child_type} "
            f"Artifact id: {self.artifact.id}",
        )

    def test_str_collection_item_collection(self) -> None:
        """Stringification contains collection id.."""
        collection = Collection.objects.create(
            name="Collection-2", category="Category-2", workspace=self.workspace
        )

        collection_item = CollectionItem.objects.create(
            name="Name of the item",
            category=ArtifactCategory.SOURCE_PACKAGE,
            collection=collection,
            child_type=CollectionItem.Types.COLLECTION,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        self.assertIn(f"Collection id: {collection.id}", str(collection_item))

    def test_str_collection_item_bare(self) -> None:
        """Stringification does not contain collection/artifact id."""
        collection_item = CollectionItem.objects.create(
            name="Name of the item",
            category=ArtifactCategory.SOURCE_PACKAGE,
            child_type=CollectionItem.Types.BARE,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        self.assertNotIn("Collection id:", str(collection_item))
        self.assertNotIn("Artifact id:", str(collection_item))

    def test_parent_category(self) -> None:
        """`parent_category` is copied from the parent collection."""
        collection_item = CollectionItem.objects.create(
            name="test",
            category=BareDataCategory.TEST,
            child_type=CollectionItem.Types.BARE,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        self.assertEqual(
            collection_item.parent_category, self.collection.category
        )

    def test_get_absolute_url(self) -> None:
        """Test get_absolute_url results."""
        item = CollectionItem.objects.create(
            name="Name of the item",
            category=ArtifactCategory.SOURCE_PACKAGE,
            child_type=CollectionItem.Types.BARE,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        item_url = item.get_absolute_url()
        self.assertEqual(
            item_url,
            f"/{self.workspace.scope.name}/{self.workspace.name}"
            f"/collection/{self.collection.category}/{self.collection.name}"
            f"/item/{item.pk}/{quote(item.name)}/",
        )
        self.assertTrue(item_url.endswith("/Name%20of%20the%20item/"))

    def test_get_absolute_url_slash(self) -> None:
        """Test get_absolute_url results when the item name contains `/`."""
        item = CollectionItem.objects.create(
            name="some/hierarchical/name",
            category=ArtifactCategory.SOURCE_PACKAGE,
            child_type=CollectionItem.Types.BARE,
            parent_collection=self.collection,
            created_by_user=self.user,
        )

        item_url = item.get_absolute_url()
        self.assertEqual(
            item_url,
            f"/{self.workspace.scope.name}/{self.workspace.name}"
            f"/collection/{self.collection.category}/{self.collection.name}"
            f"/item/{item.pk}/some/hierarchical/name/",
        )

    def test_get_absolute_url_different_scope(self) -> None:
        """get_absolute_url works in another scope."""
        other_scope = self.playground.get_or_create_scope(name="other")
        other_workspace = self.playground.create_workspace(scope=other_scope)
        collection = self.playground.create_collection(
            "Name", CollectionCategory.TEST, workspace=other_workspace
        )
        item = CollectionItem.objects.create(
            name="Name of the item",
            category=ArtifactCategory.SOURCE_PACKAGE,
            child_type=CollectionItem.Types.BARE,
            parent_collection=collection,
            created_by_user=self.user,
        )

        item_url = item.get_absolute_url()
        self.assertEqual(
            item_url,
            f"/{other_scope.name}/{other_workspace.name}"
            f"/collection/{collection.category}/{collection.name}"
            f"/item/{item.pk}/{quote(item.name)}/",
        )
        self.assertTrue(item_url.endswith("/Name%20of%20the%20item/"))

    def test_expand_variables(self) -> None:
        """Variables are correctly expanded, with error handling."""
        variables = {
            "$package": "deb_fields.Package",
            "$version": "deb_fields.Version",
            "constant": "value",
        }
        reference_data = {
            "deb_fields": {
                "Package": "hello",
                "Version": "2.10-3",
                "Architecture": "any",
            }
        }

        self.assertEqual(
            CollectionItem.expand_variables(variables, reference_data),
            {"package": "hello", "version": "2.10-3", "constant": "value"},
        )

        variables = {
            "$package": "deb_fields.*",
            "$version": "deb_fields.Version",
        }
        with self.assertRaisesRegex(ValueError, "Too many values expanding"):
            CollectionItem.expand_variables(variables, reference_data)

        variables = {
            "$package": "]",
        }
        with self.assertRaisesRegex(ValueError, "Parse error"):
            CollectionItem.expand_variables(variables, reference_data)

        variables = {"$package": "deb_fields.Package", "package": "constant"}
        with self.assertRaisesRegex(
            ValueError, r"Cannot set both '\$package' and 'package' variables"
        ):
            CollectionItem.expand_variables(variables, reference_data)

        variables = {"$version": "deb_fields.Version"}
        with self.assertRaises(KeyError):
            CollectionItem.expand_variables(variables, {})

    def test_expand_name(self) -> None:
        """Item name is correctly expanded."""
        item_template = "{package}_{version}"
        expanded_variables = {"package": "hello", "version": "2.10-3"}

        self.assertEqual(
            CollectionItem.expand_name(item_template, expanded_variables),
            "hello_2.10-3",
        )
