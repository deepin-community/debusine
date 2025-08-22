# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the WorkflowInternalManager."""
from unittest import mock

from django.db import IntegrityError

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.db.models import Collection, CollectionItem
from debusine.server.collections import (
    CollectionManagerInterface,
    ItemAdditionError,
    WorkflowInternalManager,
)
from debusine.test.django import TestCase


class WorkflowInternalManagerTests(TestCase):
    """Tests for WorkflowInternalManager."""

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.user = self.playground.get_default_user()

        self.workspace = self.playground.get_default_workspace()

        self.collection = Collection.objects.create(
            name="Test",
            category=CollectionCategory.WORKFLOW_INTERNAL,
            workspace=self.workspace,
        )

        self.manager = WorkflowInternalManager(collection=self.collection)

    def test_do_add_bare_data_no_name(self) -> None:
        """`do_add_bare_data` requires an item name."""
        with self.assertRaisesRegex(
            ItemAdditionError,
            "Adding to debusine:workflow-internal requires an item name",
        ):
            self.manager.add_bare_data(BareDataCategory.TEST, user=self.user)

    def test_do_add_bare_data_raise_item_addition_error(self) -> None:
        """Test do_add_bare_data raise error on Integrity issue."""
        with mock.patch(
            "debusine.db.models.CollectionItem.objects.create_from_bare_data"
        ) as mocked:
            mocked.side_effect = IntegrityError
            with self.assertRaises(ItemAdditionError):
                self.manager.add_bare_data(
                    BareDataCategory.TEST, user=self.user, name="actiontest"
                )

    def test_do_add_bare_data_replace(self) -> None:
        """`do_add_bare_data` can replace an existing artifact."""
        collection_item = self.manager.add_bare_data(
            BareDataCategory.TEST, user=self.user, name="test"
        )

        collection_item2 = self.manager.add_bare_data(
            BareDataCategory.TEST,
            user=self.user,
            data={"foo": "bar"},
            name="test",
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.name, "test")
        self.assertEqual(collection_item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(collection_item.data, {})
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "test")
        self.assertEqual(collection_item2.child_type, CollectionItem.Types.BARE)
        self.assertEqual(collection_item2.data, {"foo": "bar"})
        self.assertIsNone(collection_item2.removed_at)

    def test_do_add_bare_data_invalid_field_name(self) -> None:
        r"""Only debusine:promise can have data keys starting with ^promise_."""
        expected_message = (
            'Fields starting with "promise_" are not allowed'
            ' for category "debusine:test"'
        )
        with self.assertRaisesRegex(ItemAdditionError, expected_message):
            self.manager.add_bare_data(
                BareDataCategory.TEST,
                user=self.user,
                name="test",
                data={"promise_id": 100},
            )

    def test_do_add_bare_data_replace_nonexistent(self) -> None:
        """Replacing a nonexistent bare data item is allowed."""
        collection_item = self.manager.add_bare_data(
            BareDataCategory.TEST, user=self.user, name="test", replace=True
        )

        self.assertEqual(collection_item.name, "test")
        self.assertEqual(collection_item.child_type, CollectionItem.Types.BARE)

    def test_do_add_artifact_no_name(self) -> None:
        """`do_add_artifact` requires an item name."""
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            "Adding to debusine:workflow-internal requires an item name",
        ):
            self.manager.add_artifact(artifact_1, user=self.user)

    def test_do_add_artifact_raise_item_addition_error(self) -> None:
        """Test do_add_artifact raise error on Integrity issue."""
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )
        with mock.patch(
            "debusine.db.models.CollectionItem.objects.create_from_artifact"
        ) as mocked:
            mocked.side_effect = IntegrityError
            with self.assertRaises(ItemAdditionError):
                self.manager.add_artifact(
                    artifact_1, user=self.user, name="actiontest"
                )

    def test_do_add_artifact_replace(self) -> None:
        """`do_add_artifact` can replace an existing artifact."""
        workflow = self.playground.create_workflow()

        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )
        collection_item = self.manager.add_artifact(
            artifact_1, user=self.user, name="test"
        )
        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )

        collection_item2 = self.manager.add_artifact(
            artifact_2,
            user=self.user,
            workflow=workflow,
            name="test",
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, artifact_1)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertEqual(collection_item.removed_by_workflow, workflow)
        self.assertIsNotNone(collection_item.removed_at)

        self.assertEqual(collection_item2.name, "test")
        self.assertEqual(collection_item2.artifact, artifact_2)
        self.assertIsNone(collection_item2.removed_at)

    def test_do_add_artifact_with_promise_variable_raises_error(self) -> None:
        r"""
        Test adding artifact raises ItemAdditionError.

        The artifact's variables cannot contain keys starting with promise\\_.
        """
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )

        # Variables contain an invalid key starting with 'promise_'
        variables = {
            "promise_invalid_key": "some_value",
            "valid_key": "another_value",
        }

        with self.assertRaisesRegex(
            ItemAdditionError,
            'Keys starting with "promise_" are not allowed in variables',
        ):
            self.manager.add_artifact(
                artifact_1, user=self.user, variables=variables, name="test"
            )

    def test_do_add_artifact_with_variables_copied(self) -> None:
        """Test adding artifact copies the variables into CollectionItem."""
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )

        # Variables contain an invalid key starting with 'promise_'
        variables = {
            "valid_key_1": "value_1",
            "valid_key_2": "value_2",
        }

        collection_item = self.manager.add_artifact(
            artifact_1, user=self.user, variables=variables, name="test"
        )

        self.assertEqual(collection_item.data, variables)

    def test_do_add_artifact_replace_nonexistent(self) -> None:
        """Replacing a nonexistent artifact is allowed."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )

        collection_item = self.manager.add_artifact(
            artifact, user=self.user, name="test", replace=True
        )

        self.assertEqual(collection_item.name, "test")
        self.assertEqual(collection_item.artifact, artifact)

    def test_do_add_artifact_replace_bare_data(self) -> None:
        """Replacing bare data with an artifact is allowed."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST, data={}
        )
        self.manager.add_bare_data(
            category=BareDataCategory.TEST, user=self.user, name="test", data={}
        )

        self.manager.add_artifact(
            artifact, user=self.user, name="test", replace=True
        )

    def test_lookup_unexpected_format_raise_lookup_error(self) -> None:
        """Test lookup raise LookupError: invalid format."""
        msg = '^Unexpected lookup format: "foo:bar"$'

        with self.assertRaisesRegex(LookupError, msg):
            self.manager.lookup("foo:bar")

    def test_equal(self) -> None:
        """Test __eq__."""
        collection = Collection.objects.create(
            name="TestDebianSuite",
            category=CollectionCategory.SUITE,
            workspace=self.workspace,
        )
        manager_debian_suite = CollectionManagerInterface.get_manager_for(
            collection
        )
        manager_debian_suite2 = CollectionManagerInterface.get_manager_for(
            collection
        )
        self.assertEqual(manager_debian_suite, manager_debian_suite2)

        collection = Collection.objects.create(
            name="TestDebianSuiteLintian",
            category=CollectionCategory.SUITE_LINTIAN,
            workspace=self.workspace,
        )
        manager_debian_suite_lintian = (
            CollectionManagerInterface.get_manager_for(collection)
        )
        self.assertNotEqual(manager_debian_suite, manager_debian_suite_lintian)
