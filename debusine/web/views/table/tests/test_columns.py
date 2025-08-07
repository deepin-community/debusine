# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for table columns."""

from unittest import mock

from django.test import RequestFactory

from debusine.db.models import User
from debusine.test.django import TestCase
from debusine.web.views.table import (
    Column,
    DateTimeColumn,
    NumberColumn,
    Ordering,
    Sort,
    StringColumn,
    Table,
)
from debusine.web.views.table.columns import BoundColumn, Columns


class BoundColumnTests(TestCase):
    """Tests for :py:class:`BoundColumn`."""

    def _column(self, column: Column) -> BoundColumn[User]:
        """Create a column bound to a mock table."""

        class _Table(Table[User]):
            def init(self) -> None:
                super().init()
                self.add_column(column.name, column)

        table = _Table(
            RequestFactory().get("/"),
            User.objects.all(),
            default_order=column.name,
        )

        return table.columns[column.name]

    def assertOrderings(self, col: BoundColumn[User], names: list[str]) -> None:
        """Ensure the orderings match the given "what.how" names."""
        self.assertEqual([f"{o.what}.{o.how}" for o in col.orderings], names)

    def test_column(self) -> None:
        """Test Column defaulting to StringColumn."""
        col = self._column(Column("User", ordering="username", name="user"))
        self.assertOrderings(col, ["user.asc", "user.desc"])
        self.assertEqual(str(col), "user")
        self.assertEqual(repr(col), "BoundColumn('user')")
        self.assertEqual(col.name, "user")
        self.assertEqual(col.title, "User")
        ctx = col.get_context_data()
        self.assertEqual(ctx["column"], col)
        self.assertHTMLValid(col.sort_icon)

    def test_string_column(self) -> None:
        """Test StringColumn."""
        col = self._column(
            StringColumn("Email", ordering="email", name="email")
        )
        self.assertOrderings(col, ["email.asc", "email.desc"])
        self.assertEqual(str(col), "email")
        self.assertEqual(repr(col), "BoundColumn('email')")
        self.assertEqual(col.name, "email")
        self.assertEqual(col.title, "Email")
        ctx = col.get_context_data()
        self.assertEqual(ctx["column"], col)
        self.assertHTMLValid(col.sort_icon)

    def test_number_column(self) -> None:
        """Test NumberColumn."""
        col = self._column(NumberColumn("Age", ordering="age", name="age"))
        self.assertOrderings(col, ["age.asc", "age.desc"])
        self.assertEqual(str(col), "age")
        self.assertEqual(repr(col), "BoundColumn('age')")
        self.assertEqual(col.name, "age")
        self.assertEqual(col.title, "Age")
        self.assertEqual(col.get_context_data(), {"column": col})
        self.assertHTMLValid(col.sort_icon)

    def test_datetime_column(self) -> None:
        """Test DateTimeColumn."""
        col = self._column(
            DateTimeColumn("Last Seen", ordering="last_seen", name="last_seen")
        )
        self.assertOrderings(col, ["last_seen.asc", "last_seen.desc"])
        self.assertEqual(str(col), "last_seen")
        self.assertEqual(repr(col), "BoundColumn('last_seen')")
        self.assertEqual(col.name, "last_seen")
        self.assertEqual(col.title, "Last Seen")
        self.assertEqual(col.get_context_data(), {"column": col})
        self.assertHTMLValid(col.sort_icon)

    def test_custom_sort(self) -> None:
        """Test DateTimeColumn."""
        col = self._column(
            Column(
                "Test",
                ordering=Ordering(foo=Sort("username", "Foo", "icon")),
                name="test",
            )
        )
        self.assertOrderings(col, ["test.foo"])
        self.assertEqual(str(col), "test")
        self.assertEqual(repr(col), "BoundColumn('test')")
        self.assertEqual(col.name, "test")
        self.assertEqual(col.title, "Test")
        self.assertEqual(col.get_context_data(), {"column": col})
        self.assertHTMLValid(col.sort_icon)

    def test_unsortable_column(self) -> None:
        """Test data of unsortable columns."""
        col = self._column(Column("Extra", name="extra"))
        self.assertEqual(col.orderings, [])
        self.assertEqual(str(col), "extra")
        self.assertEqual(repr(col), "BoundColumn('extra')")
        self.assertEqual(col.name, "extra")
        self.assertEqual(col.title, "Extra")
        self.assertEqual(col.get_context_data(), {"column": col})
        self.assertEqual(col.sort_icon, "")


class ColumnsTests(TestCase):
    """Tests for :py:class:`Columns`."""

    def _columns(self) -> Columns[User]:
        """Create a Columns bound to a mock table."""
        table = mock.Mock()
        table.request = RequestFactory().get("/")
        return Columns(table)

    def test_init(self) -> None:
        columns = self._columns()
        self.assertEqual(columns.columns, {})

    def test_add(self) -> None:
        columns = self._columns()
        col = columns.add(
            "user", Column("User", ordering="username", name="user")
        )
        self.assertIs(col.table, columns.table)
        self.assertEqual(columns.columns, {"user": col})

    def test_getitem(self) -> None:
        columns = self._columns()
        col = columns.add(
            "user", Column("User", ordering="username", name="user")
        )
        self.assertIs(columns["user"], col)
        with self.assertRaises(KeyError):
            columns["foo"]
