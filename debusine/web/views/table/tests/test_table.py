# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for tabular representation of querysets."""

import abc
from urllib.parse import urlencode

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

from debusine.db.models import User
from debusine.test.django import TestCase
from debusine.web.views.table import Column, FilterText, Ordering, Table
from debusine.web.views.tests.utils import ViewTestMixin


class TableTestCase(ViewTestMixin, TestCase, abc.ABC):
    """Common test infrastructure."""

    @abc.abstractmethod
    def get_table_class(self) -> type[Table[User]]:
        """Get the table class to use for tests."""

    def _table(
        self,
        prefix: str = "",
        default_order: str | None = None,
        table_class: type[Table[User]] | None = None,
        qstring: dict[str, str] | None = None,
        preview: bool = False,
    ) -> Table[User]:
        """Instantiate a table."""
        url = "/"
        if qstring:
            url += "?" + urlencode(qstring)
        request = RequestFactory().get(url)
        queryset = User.objects.all()
        if table_class is None:
            table_class = self.get_table_class()
        return table_class(
            request=request,
            object_list=queryset,
            prefix=prefix,
            default_order=default_order,
            preview=preview,
        )


class TableTests(TableTestCase):
    """Tests for :py:class:`Table`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        for idx in range(10):
            cls.playground.create_user(f"user{idx:02d}")

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            email = Column("Email", ordering="email")
            test = Column("Test")
            default_order = "user"

        return _Table

    def test_declarative(self) -> None:
        """Test declarative column definition."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            default_order = "user"

        self.assertEqual(list(_Table.column_definitions.keys()), ["user"])

        table = self._table(table_class=_Table)
        self.assertIs(table.model, User)
        self.assertEqual(list(table.columns.columns.keys()), ["user"])
        self.assertEqual(
            [f"{o.what}.{o.how}" for o in table.orderings.options],
            ["user.asc", "user.desc"],
        )
        self.assertEqual(list(table.filters.options.keys()), [])

        col = table.columns["user"]
        self.assertEqual(col.orderings, table.orderings.options)

    def test_declarative_aliased(self) -> None:
        """Test declarative column definition with an aliased name."""

        class _Table(Table[User]):
            type_ = Column("Type", ordering="username", name="type")
            default_order = "type"

        self.assertEqual(list(_Table.column_definitions.keys()), ["type"])

        table = self._table(table_class=_Table)
        self.assertEqual(list(table.columns.columns.keys()), ["type"])

        self.assertEqual(
            [f"{o.what}.{o.how}" for o in table.orderings.options],
            ["type.asc", "type.desc"],
        )
        self.assertEqual(list(table.filters.options.keys()), [])

        col = table.columns["type"]
        self.assertEqual(col.orderings, table.orderings.options)

    def test_declarative_inherited(self) -> None:
        """Test declarative column inherited from base classes."""

        class _Base(Table[User]):
            name = Column("Name")

        class _Derived(_Base):
            email = Column("Email")

        self.assertEqual(list(_Base.column_definitions), ["name"])
        self.assertEqual(list(_Derived.column_definitions), ["name", "email"])

    def test_declarative_non_table_base(self) -> None:
        """Test declarative column inherited from base classes."""

        class Mixin:
            pass

        class _Base(Table[User]):
            name = Column("Name")

        class _Derived(Mixin, _Base):
            email = Column("Email")

        self.assertEqual(list(_Base.column_definitions), ["name"])
        self.assertEqual(list(_Derived.column_definitions), ["name", "email"])

    def test_declarative_multiple_inheritance(self) -> None:
        """Test declarative column inherited from base classes."""

        class _Base1(Table[User]):
            name = Column("Name")

        class _Base2(Table[User]):
            status = Column("Status")

        class _Derived(_Base1, _Base2):
            email = Column("Email")

        self.assertEqual(list(_Base1.column_definitions), ["name"])
        self.assertEqual(list(_Base2.column_definitions), ["status"])
        self.assertEqual(
            list(_Derived.column_definitions), ["name", "status", "email"]
        )

    def test_declarative_filter(self) -> None:
        """Test declarative table filters."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterText("User")
            default_order = "user"

        self.assertEqual(list(_Table.filter_definitions.keys()), ["username"])

        table = self._table(table_class=_Table)
        self.assertEqual(list(table.filters.options.keys()), ["username"])
        flt = table.filters.options["username"]
        self.assertEqual(flt.name, "username")
        self.assertEqual(flt.label, "User")

    def test_declarative_filter_prefix(self) -> None:
        """Declarative filter members must start with ``filter_``."""
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"f_username = FilterText\(…\): assigned name"
            r" does not start with 'filter_'",
        ):

            class _Table(Table[User]):
                user = Column("User", ordering="username")
                f_username = FilterText("User")
                default_order = "user"

    def test_declarative_ordering(self) -> None:
        """Test declarative table orderings."""

        class _Table(Table[User]):
            user = Column("User")
            ordering_test = Ordering("username")
            default_order = "test"

        self.assertEqual(list(_Table.ordering_definitions.keys()), ["test"])

        table = self._table(table_class=_Table)
        self.assertEqual(
            [str(o) for o in table.orderings.options], ["test.asc", "test.desc"]
        )
        ordering = table.orderings.options[0]
        self.assertEqual(ordering.what, "test")
        self.assertEqual(ordering.how, "asc")
        self.assertEqual(ordering.label_what, "Test")

    def test_mandate_queryset(self) -> None:
        """Table needs a QuerySet for object_list."""
        table_class = self.get_table_class()
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"Table cannot work with an object_list of class list,"
            r" only with QuerySets",
        ):
            table_class(
                request=RequestFactory().get("/"),
                object_list=list(User.objects.all()),
            )

    def test_missing_default_order(self) -> None:
        """Test with no default ordering."""

        class _Table(Table[User]):
            """Table with default ordering not set."""

            user = Column("User", ordering="username")

        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"default_order not set in class or constructor arguments",
        ):
            self._table(table_class=_Table)

    def test_unsortable(self) -> None:
        """Test a table with no ordering options."""

        class _Table(Table[User]):
            """Unsortable table."""

            user = Column("User")

        self.assertEqual(list(_Table.column_definitions.keys()), ["user"])
        self.assertEqual(_Table.filter_definitions, {})

        table = self._table(table_class=_Table)
        self.assertIs(table.model, User)
        self.assertEqual(list(table.columns.columns.keys()), ["user"])
        self.assertEqual(table.orderings.options, [])
        self.assertEqual(table.filters.options, {})

        col = table.columns["user"]
        self.assertEqual(col.orderings, [])

        self.assertQuerySetEqual(
            table.rows, list(User.objects.all()), ordered=False
        )

    def test_preview_disables_filters(self) -> None:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterText("User")
            default_order = "user"

        table = self._table(table_class=_Table, preview=True)
        self.assertEqual(list(table.filters.options.keys()), [])
