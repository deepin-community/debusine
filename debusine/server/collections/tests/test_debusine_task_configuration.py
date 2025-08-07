# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebusineTaskConfiguration collections."""

import unittest
from typing import Any, ClassVar
from unittest import mock

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineTaskConfiguration,
)
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import Collection, CollectionItem
from debusine.db.playground import scenarios
from debusine.server.collections.base import ItemAdditionError
from debusine.server.collections.debusine_task_configuration import (
    DebusineTaskConfigurationManager,
    apply_configuration,
    build_configuration,
    list_configuration,
    lookup_config_by_name,
    lookup_templates,
)
from debusine.tasks import TaskConfigError
from debusine.tasks.models import TaskTypes
from debusine.test.django import TestCase


class TaskConfigTestCase(TestCase):
    """Base class for tests that apply task configuration."""

    scenario = scenarios.DefaultContext()
    collection: ClassVar[Collection]
    manager: ClassVar[DebusineTaskConfigurationManager]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.collection = cls.scenario.workspace.get_collection(
            name="default",
            category=CollectionCategory.TASK_CONFIGURATION,
            user=cls.scenario.user,
        )
        cls.manager = DebusineTaskConfigurationManager(
            collection=cls.collection
        )

    @classmethod
    def _add_config(cls, entry: DebusineTaskConfiguration) -> CollectionItem:
        """Add a config entry to config_collection."""
        return cls.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=cls.scenario.user,
            data=entry,
        )


class DebusineTaskConfigurationManagerTests(TaskConfigTestCase):
    """Tests for DebusineTaskConfigurationManager."""

    def test_add_bare_data_no_data(self) -> None:
        """`add_bare_data` requires item data."""
        with self.assertRaisesRegex(
            ItemAdditionError,
            "Adding to debusine:task-configuration requires data",
        ):
            self.manager.add_bare_data(
                BareDataCategory.TASK_CONFIGURATION,
                user=self.scenario.user,
            )

    def test_add_bare_data_refuses_name(self) -> None:
        """`add_bare_data` refuses an explicitly-specified name."""
        with self.assertRaisesRegex(
            ItemAdditionError,
            "Cannot use an explicit item name when adding to "
            "debusine:task-configuration",
        ):
            self.manager.add_bare_data(
                BareDataCategory.TASK_CONFIGURATION,
                user=self.scenario.user,
                data=DebusineTaskConfiguration(
                    task_type=TaskTypes.WORKER,
                    task_name="sbuild",
                    subject="base-files",
                    context="sid",
                ),
                name="override",
            )

    def test_add_bare_data_raise_item_addition_error(self) -> None:
        """`add_bare_data` raises an error for duplicate names."""
        data = DebusineTaskConfiguration(template="test")
        self.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.scenario.user,
            data=data,
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_bare_data(
                BareDataCategory.TASK_CONFIGURATION,
                user=self.scenario.user,
                data=data,
            )

    def test_add_bare_data_replace(self) -> None:
        """`add_bare_data` can replace an existing bare data item."""
        data = DebusineTaskConfiguration(template="test")

        item_old = self.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.scenario.user,
            data=data,
        )

        item_new = self.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.scenario.user,
            data=data,
            replace=True,
        )

        serialized_data = model_to_json_serializable_dict(
            data, exclude_unset=True
        )

        item_old.refresh_from_db()
        self.assertEqual(item_old.name, "template:test")
        self.assertEqual(item_old.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_old.data, serialized_data)
        self.assertEqual(item_old.removed_by_user, self.scenario.user)
        self.assertIsNotNone(item_old.removed_at)
        self.assertEqual(item_new.name, "template:test")
        self.assertEqual(item_new.child_type, CollectionItem.Types.BARE)
        self.assertEqual(item_new.data, serialized_data)
        self.assertIsNone(item_new.removed_at)

    def test_add_bare_data_replace_nonexistent(self) -> None:
        """Replacing a nonexistent bare data item is allowed."""
        data = DebusineTaskConfiguration(template="test")

        item = self.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.scenario.user,
            data=data,
            replace=True,
        )

        self.assertEqual(item.name, "template:test")
        self.assertEqual(item.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            item.data, model_to_json_serializable_dict(data, exclude_unset=True)
        )

    def test_remove_bare_data(self) -> None:
        """`remove_bare_data` removes the item."""
        data = DebusineTaskConfiguration(template="test")
        item = self.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.scenario.user,
            data=data,
        )

        self.manager.remove_bare_data(item.name, user=self.scenario.user)

        item.refresh_from_db()
        self.assertEqual(item.removed_by_user, self.scenario.user)
        self.assertIsNotNone(item.removed_at)

    def test_lookup_unexpected_format(self) -> None:
        """`lookup` raises `LookupError` for an unexpected format."""
        with self.assertRaisesRegex(
            LookupError, r'^Unexpected lookup format: "foo:bar"'
        ):
            self.manager.lookup("foo:bar")

    def test_lookup_return_none(self) -> None:
        """`lookup` returns None if there are no matches."""
        self.assertIsNone(self.manager.lookup("name:does-not-exist"))

    def test_lookup_by_name(self) -> None:
        """Lookup by name works."""
        data = DebusineTaskConfiguration(template="test")
        item = self.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.scenario.user,
            data=data,
        )
        self.assertEqual(self.manager.lookup("name:template:test"), item)


class ConfigLookupTests(TaskConfigTestCase):
    """Tests for the lookup_* methods."""

    def test_lookup_config_by_name_successful(self) -> None:
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self._add_config(entry)
        self.assertEqual(
            lookup_config_by_name(self.collection, entry.name()),
            entry,
        )

    def test_lookup_config_by_name_missing(self) -> None:
        """Lookup returns None if the named item does not exist."""
        self.assertIsNone(
            lookup_config_by_name(self.collection, "Worker:noop::")
        )

    def test_lookup_config_by_name_corrupted(self) -> None:
        """Lookup raises if the item contains invalid data."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        item = self._add_config(entry)
        item.data["invalid_field"] = "value"
        item.save()
        with self.assertRaisesRegex(
            TaskConfigError,
            r"(?s)debusine:task-configuration item does not validate"
            r".+extra fields not permitted",
        ):
            lookup_config_by_name(self.collection, entry.name())

    def test_lookup_templates_empty(self) -> None:
        """Lookup templates with use_templates is empty."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(
            list(lookup_templates(self.collection, entry)),
            [],
        )

    def test_lookup_templates_missing_item(self) -> None:
        """Lookup templates with use_templates is empty."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            use_templates=["missing"],
        )
        with self.assertRaisesRegex(
            TaskConfigError,
            r"Worker:noop:: references missing template 'missing'",
        ):
            list(lookup_templates(self.collection, entry))

    def test_lookup_templates_found(self) -> None:
        """Lookup templates with one template."""
        template = DebusineTaskConfiguration(
            template="template",
        )
        self._add_config(template)

        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            use_templates=["template"],
        )
        self.assertEqual(
            list(lookup_templates(self.collection, entry)),
            [template],
        )

    def test_lookup_templates_diamond(self) -> None:
        """Lookup templates with a diamond dependency."""
        base = DebusineTaskConfiguration(
            template="base",
        )
        self._add_config(base)
        a = DebusineTaskConfiguration(template="a", use_templates=["base"])
        self._add_config(a)
        b = DebusineTaskConfiguration(template="b", use_templates=["base"])
        self._add_config(b)
        ab = DebusineTaskConfiguration(template="ab", use_templates=["a", "b"])
        self._add_config(ab)

        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            use_templates=["ab"],
        )
        self.assertEqual(
            list(lookup_templates(self.collection, entry)),
            [base, a, base, b, ab],
        )

    def test_lookup_templates_cycle(self) -> None:
        """Lookup templates with a dependency cycle."""
        a = DebusineTaskConfiguration(template="a", use_templates=["b"])
        self._add_config(a)
        b = DebusineTaskConfiguration(template="b", use_templates=["a"])
        self._add_config(b)

        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            use_templates=["a"],
        )
        with self.assertRaisesRegex(
            TaskConfigError,
            r"template:b: template lookup cycle detected: a→b→a",
        ):
            list(lookup_templates(self.collection, entry))

    def test_list_configuration_no_templates(self) -> None:
        """list_configuration with no templates."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self._add_config(entry)
        self.assertEqual(
            list(list_configuration(self.collection, entry.name())),
            [entry],
        )

    def test_list_configuration_missing_item(self) -> None:
        """list_configuration with a missing names yields no entries."""
        self.assertEqual(
            list(list_configuration(self.collection, "Worker:noop::")),
            [],
        )

    def test_list_configuration_templates_first(self) -> None:
        """list_configuration yields templates before items."""
        a = DebusineTaskConfiguration(
            template="a",
        )
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            use_templates=["a"],
        )
        self._add_config(entry)
        with mock.patch(
            "debusine.server.collections"
            ".debusine_task_configuration.lookup_templates",
            return_value=iter((a, a, a)),
        ):
            self.assertEqual(
                list(list_configuration(self.collection, entry.name())),
                [a, a, a, entry],
            )


class BuildConfigurationTests(unittest.TestCase):
    """Test :py:func:build_configuration`."""

    def entry(
        self, name: str = "test", **kwargs: Any
    ) -> DebusineTaskConfiguration:
        return DebusineTaskConfiguration(template=name, **kwargs)

    def test_empty(self) -> None:
        """Run with no entries."""
        self.assertEqual(build_configuration(()), ({}, {}))

    def test_one(self) -> None:
        """Run with one entry."""
        entry = self.entry(
            delete_values=["val0"],
            default_values={"val1": 1},
            override_values={"val2": 2},
            lock_values=["val3"],
        )
        self.assertEqual(
            build_configuration([entry]),
            ({"val1": 1}, {"val2": 2}),
        )

    def test_default_and_override(self) -> None:
        """Run with one entry with same default and override keys."""
        entry = self.entry(
            default_values={"val1": 1},
            override_values={"val1": 11},
        )
        self.assertEqual(
            build_configuration([entry]),
            ({"val1": 1}, {"val1": 11}),
        )

    def test_stack(self) -> None:
        """Run with two entries."""
        entry1 = self.entry(
            default_values={"val1": 1, "base1": 1},
            override_values={"val1": 2, "base2": 2},
        )
        entry2 = self.entry(
            default_values={"val1": 11},
            override_values={"val1": 22},
        )
        self.assertEqual(
            build_configuration([entry1, entry2]),
            ({"val1": 11, "base1": 1}, {"val1": 22, "base2": 2}),
        )

    def test_delete_values(self) -> None:
        """Test delete_values."""
        entry1 = self.entry(
            default_values={"val1": 1, "val2": 2},
            override_values={"val1": 11, "val2": 22},
        )
        entry2 = self.entry(
            delete_values=["val1"],
        )
        self.assertEqual(
            build_configuration([entry1, entry2]),
            ({"val2": 2}, {"val2": 22}),
        )

    def test_delete_and_set_values(self) -> None:
        """Test deleting and then setting values."""
        entry1 = self.entry(
            default_values={"val1": 1, "val2": 2},
            override_values={"val1": 11, "val2": 22},
        )
        entry2 = self.entry(
            delete_values=["val1"],
            default_values={"val1": 111},
            override_values={"val1": 112},
        )
        self.assertEqual(
            build_configuration([entry1, entry2]),
            ({"val1": 111, "val2": 2}, {"val1": 112, "val2": 22}),
        )

    def test_lock_values(self) -> None:
        """Test locked_values."""
        entry1 = self.entry(
            default_values={"val1": 1, "val2": 2},
            override_values={"val1": 11, "val2": 22},
            lock_values=["val1"],
        )
        entry2 = self.entry(
            delete_values=["val1", "val2"],
            default_values={"val1": 111, "val3": 3},
            override_values={"val1": 112, "val3": 33},
        )
        self.assertEqual(
            build_configuration([entry1, entry2]),
            ({"val1": 1, "val3": 3}, {"val1": 11, "val3": 33}),
        )


class ApplyConfigurationTests(TaskConfigTestCase):
    """Test :py:func:apply_configuration`."""

    def test_no_config(self) -> None:
        """Test without configuration to apply."""
        task_data: dict[str, Any] = {}
        apply_configuration(
            task_data, self.collection, TaskTypes.WORKER, "noop", None, None
        )
        self.assertEqual(task_data, {})

    def test_with_config(self) -> None:
        """Test with overrides to apply."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            default_values={"default": "added"},
            override_values={"override": "overridden"},
        )
        self._add_config(entry)

        task_data: dict[str, Any] = {
            "key": "value",
            "override": "original",
        }
        apply_configuration(
            task_data, self.collection, TaskTypes.WORKER, "noop", None, None
        )
        self.assertEqual(
            task_data,
            {"key": "value", "override": "overridden", "default": "added"},
        )

    def test_defaults_with_none(self) -> None:
        """Test setting defaults on existing values set to None."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            default_values={"default": "added"},
        )
        self._add_config(entry)

        task_data: dict[str, Any] = {"default": None}
        apply_configuration(
            task_data, self.collection, TaskTypes.WORKER, "noop", None, None
        )
        self.assertEqual(task_data, {"default": "added"})
