# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for DB constraints."""
from typing import Any
from unittest import mock

from django.db import IntegrityError
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import CheckConstraint, Q

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.constraints import JsonDataUniqueConstraint
from debusine.db.models import Collection, CollectionItem, User
from debusine.test.django import TestCase


class JsonDataUniqueConstraintTests(TestCase):
    """Tests for JsonDataUniqueConstraint class."""

    def test_eq_true(self) -> None:
        """Test equality for equal JsonDataUniqueConstraint."""
        self.assertEqual(
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="unique",
            ),
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="unique",
            ),
        )

    def test_eq_false(self) -> None:
        """Test equality for not-equal JsonDataUniqueConstraint."""
        self.assertNotEqual(
            JsonDataUniqueConstraint(
                fields=["data->>architecture", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="unique",
            ),
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="unique",
            ),
        )

        # Different condition
        self.assertNotEqual(
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=True),
                nulls_distinct=False,
                name="unique",
            ),
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="unique",
            ),
        )

        # Different nulls_distinct
        self.assertNotEqual(
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=True,
                name="unique",
            ),
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="unique",
            ),
        )

        # Different name
        self.assertNotEqual(
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="name_1",
            ),
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="name_2",
            ),
        )

        # Comparing with a Constraint that is not a JsonDataUniqueConstraint
        self.assertNotEqual(
            JsonDataUniqueConstraint(
                fields=["data->>codename", "parent_collection"],
                condition=Q(category__isnull=False),
                nulls_distinct=False,
                name="name_1",
            ),
            CheckConstraint(check=Q(), name="price"),
        )

    def assert_constraint_deconstruction(
        self,
        constraint: JsonDataUniqueConstraint,
        expected_kwargs: dict[str, Any],
    ) -> None:
        """Assert constraint.deconstruct return expected path, args, kwargs."""
        path, args, kwargs = constraint.deconstruct()
        self.assertEqual(
            path, "debusine.db.constraints.JsonDataUniqueConstraint"
        )
        self.assertEqual(args, ())
        self.assertEqual(kwargs, expected_kwargs)

    def test_deconstruction(self) -> None:
        """Test deconstruction."""
        fields = ["foo", "bar"]
        name = "unique_fields"
        condition = Q(name__eq="name")

        constraint = JsonDataUniqueConstraint(
            fields=fields, name=name, nulls_distinct=True, condition=condition
        )
        expected_kwargs = {
            "fields": tuple(fields),
            "name": "unique_fields",
            "nulls_distinct": True,
            "condition": condition,
        }

        self.assert_constraint_deconstruction(constraint, expected_kwargs)

    def test_deconstruction_no_condition_no_nulls_distinct(self) -> None:
        """Test deconstruction without condition and without nulls distinct."""
        fields = ["foo", "bar"]
        name = "unique_fields"

        constraint = JsonDataUniqueConstraint(
            fields=fields, name=name, nulls_distinct=None, condition=Q()
        )
        expected_kwargs = {
            "fields": tuple(fields),
            "name": "unique_fields",
        }

        self.assert_constraint_deconstruction(constraint, expected_kwargs)

    def test_database_constraint(self) -> None:
        """
        Test cannot create two objects with duplicated data.

        Use CollectionItem debian:environments constraint to exercise relevant
        code:
        -The constraint works
        -Applies only on the relevant condition

        Complementary tests assert the SQL created and other code paths
        not used by CollectionItem debian:environments constraint.
        """
        self.workspace = self.playground.create_workspace()
        self.artifact, _ = self.playground.create_artifact()

        self.user = User.objects.create(
            username="John", email="john@example.com"
        )

        collection_test = Collection.objects.create(
            name="test",
            category=CollectionCategory.TEST,
            workspace=self.workspace,
        )
        collection_environments = Collection.objects.create(
            name="debian",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=self.workspace,
        )

        item_kwargs = {
            "child_type": CollectionItem.Types.ARTIFACT,
            "artifact": self.artifact,
            "created_by_user": self.user,
        }

        # Allowed by the condition: category does not have any unique constraint
        CollectionItem.objects.create(
            **item_kwargs,
            name="name1",
            category=ArtifactCategory.TEST,
            parent_collection=collection_test,
        )
        CollectionItem.objects.create(
            **item_kwargs,
            name="name2",
            category=ArtifactCategory.TEST,
            parent_collection=collection_test,
        )

        CollectionItem.objects.create(
            **item_kwargs,
            name="name3",
            category=ArtifactCategory.SYSTEM_TARBALL,
            parent_collection=collection_environments,
            data={"codename": "bookworm", "architecture": "amd64"},
        )

        # Having both a tarball and an image with the same properties is
        # allowed
        CollectionItem.objects.create(
            **item_kwargs,
            name="name5",
            category=ArtifactCategory.SYSTEM_IMAGE,
            parent_collection=collection_environments,
            data={"codename": "bookworm", "architecture": "amd64"},
        )

        # Not allowed: the category debian:environments does not allow
        # duplicated codename and architecture in the same collection
        msg_contents = "db_collectionitem_unique_debian_environments"
        with self.assertRaisesRegex(IntegrityError, msg_contents):
            CollectionItem.objects.create(
                **item_kwargs,
                name="name4",
                category=ArtifactCategory.SYSTEM_TARBALL,
                parent_collection=collection_environments,
                data={"codename": "bookworm", "architecture": "amd64"},
            )

    @staticmethod
    def run_create_sql(
        constraint: JsonDataUniqueConstraint, *, table_name: str
    ) -> str:
        """Run constraint.create_sql and return SQL."""
        schema_editor = mock.create_autospec(
            spec=BaseDatabaseSchemaEditor, instance=True
        )
        schema_editor.connection = mock.Mock()
        schema_editor.connection.vendor = "postgresql"
        schema_editor.connection.ops = mock.Mock()
        schema_editor.connection.ops.quote_name = mock.Mock(
            return_value=f'"{table_name}"'
        )

        sql = constraint.create_sql(CollectionItem, schema_editor)

        return str(sql)

    def test_create_sql(self) -> None:
        """
        Test create_sql method.

        Covers code path condition=None not covered by test_database_constraint.
        """
        fields = ["name", "category"]
        name = "index_name"
        table_name = "table"
        constraint = JsonDataUniqueConstraint(
            fields=fields,
            name=name,
            nulls_distinct=False,
            condition=None,
        )
        fields_str = ", ".join(fields)
        actual_sql = self.run_create_sql(constraint, table_name=table_name)
        expected_sql = (
            f'CREATE UNIQUE INDEX {name} ON "{table_name}" ({fields_str}) '
            f'NULLS NOT DISTINCT'
        )
        self.assertEqual(actual_sql, expected_sql)

    def test_create_sql_nulls_distinct_false(self) -> None:
        """Test create_sql adds NULLS NOT DISTINCT."""
        constraint = JsonDataUniqueConstraint(
            fields=["name", "data->>'variant'"],
            name="index_name",
            nulls_distinct=False,
            condition=None,
        )
        table_name = "table_name"
        actual_sql = self.run_create_sql(constraint, table_name=table_name)
        expected_sql = (
            f'CREATE UNIQUE INDEX index_name ON "{table_name}" '
            f"(name, (data->>'variant')) NULLS NOT DISTINCT"
        )
        self.assertEqual(actual_sql, expected_sql)

    def test_create_sql_nulls_distinct_true(self) -> None:
        """Test create_sql adds NULLS DISTINCT."""
        constraint = JsonDataUniqueConstraint(
            fields=["name", "data->>'variant'"],
            name="index_name",
            nulls_distinct=True,
            condition=None,
        )
        table_name = "table"
        actual_sql = self.run_create_sql(constraint, table_name=table_name)
        expected_sql = (
            f'CREATE UNIQUE INDEX index_name ON "{table_name}" '
            f"(name, (data->>'variant')) NULLS DISTINCT"
        )

        self.assertEqual(actual_sql, expected_sql)

    def test_create_sql_distinct_none(self) -> None:
        """Test create_sql does not add NULLS (NOT) DISTINCT."""
        constraint = JsonDataUniqueConstraint(
            fields=["name", "data->>'variant'"],
            name="index_name",
            nulls_distinct=None,
            condition=None,
        )
        table_name = "table"
        actual_sql = self.run_create_sql(constraint, table_name=table_name)
        expected_sql = (
            f'CREATE UNIQUE INDEX index_name ON "{table_name}" '
            "(name, (data->>'variant'))"
        )

        self.assertEqual(actual_sql, expected_sql)

    def test_remove_sql(self) -> None:
        """Test remove_sql drop the constraint index."""
        name = "index_name"
        constraint = JsonDataUniqueConstraint(
            fields=["not_used"],
            name=name,
            nulls_distinct=False,
            condition=None,
        )

        self.assertEqual(
            str(constraint.remove_sql(None, None)), f"DROP INDEX {name}"
        )
