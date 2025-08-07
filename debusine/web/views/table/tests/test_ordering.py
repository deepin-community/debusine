# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the table ordering functions."""

from typing import Any, cast
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.db.models import F
from django.test import RequestFactory

from debusine.db.models import User
from debusine.test.django import TestCase
from debusine.web.views.table.ordering import (
    DateTimeOrdering,
    NumberOrdering,
    OrderBys,
    Ordering,
    Sort,
    StringOrdering,
    TableOrdering,
    TableOrderings,
    compute_sort_icon_current_next,
    order_by_tuple,
    render_sort_icon,
)


class OrderByTupleTests(TestCase):
    """Tests for :py:func:`order_by_tuple`."""

    def test_single(self) -> None:
        for val in (
            "name",
            F("name"),
            F("name").asc(),
            ["name"],
            [F("name")],
            [F("name").asc()],
        ):
            with self.subTest(val=val):
                self.assertEqual(
                    str(order_by_tuple(val)),
                    "(OrderBy(F(name), descending=False),)",
                )

    def test_multi(self) -> None:
        for val in (
            ["name1", "name2"],
            [F("name1"), F("name2")],
            [F("name1").asc(), F("name2").asc()],
            cast(OrderBys, ["name1", F("name2").asc()]),
            cast(OrderBys, [F("name1"), "name2"]),
        ):
            with self.subTest(val=val):
                self.assertEqual(
                    str(order_by_tuple(val)),
                    "(OrderBy(F(name1), descending=False),"
                    " OrderBy(F(name2), descending=False))",
                )


class OrderingTests(TestCase):
    """Tests for :py:class:`Ordering`."""

    def test_default_desc(self) -> None:
        """Test inferring descending with a single field."""
        for val in ("name", F("name"), F("name").asc()):
            with self.subTest(val=val):
                o = Ordering(val)
                self.assertEqual(list(o.options.keys()), ["asc", "desc"])
                self.assertEqual(
                    str(o.options["asc"].order_by),
                    "(OrderBy(F(name), descending=False),)",
                )
                self.assertEqual(
                    str(o.options["desc"].order_by),
                    "(OrderBy(F(name), descending=True),)",
                )

    def test_default_desc_multi(self) -> None:
        """Test inferring descending with a single field."""
        for val in (
            ("name1", "name2"),
            (F("name1"), F("name2")),
            (F("name1").asc(), F("name2").asc()),
        ):
            with self.subTest(val=val):
                o = Ordering(val)
                self.assertEqual(list(o.options.keys()), ["asc", "desc"])
                self.assertEqual(
                    str(o.options["asc"].order_by),
                    "(OrderBy(F(name1), descending=False),"
                    " OrderBy(F(name2), descending=False))",
                )
                self.assertEqual(
                    str(o.options["desc"].order_by),
                    "(OrderBy(F(name1), descending=True),"
                    " OrderBy(F(name2), descending=True))",
                )

    def test_asc_desc(self) -> None:
        """Test inferring descending with a single field."""
        o = Ordering("name1", "name2")
        self.assertEqual(list(o.options.keys()), ["asc", "desc"])
        self.assertEqual(
            str(o.options["asc"].order_by),
            "(OrderBy(F(name1), descending=False),)",
        )
        self.assertEqual(
            str(o.options["desc"].order_by),
            "(OrderBy(F(name2), descending=False),)",
        )

    def test_dash(self) -> None:
        """Test inferring descending with a single field."""
        o = Ordering("name", "-name")
        self.assertEqual(list(o.options.keys()), ["asc", "desc"])
        self.assertEqual(
            str(o.options["asc"].order_by),
            "(OrderBy(F(name), descending=False),)",
        )
        self.assertEqual(
            str(o.options["desc"].order_by),
            "(OrderBy(F(name), descending=True),)",
        )

    def test_kwargs(self) -> None:
        """Test passing fields as kwargs."""
        o = Ordering(asc="name", desc="-name")
        self.assertEqual(list(o.options.keys()), ["asc", "desc"])
        self.assertEqual(
            str(o.options["asc"].order_by),
            "(OrderBy(F(name), descending=False),)",
        )
        self.assertEqual(
            str(o.options["desc"].order_by),
            "(OrderBy(F(name), descending=True),)",
        )

    def test_arbitrary_incomplete(self) -> None:
        """Arbitrary orderings not fully specified: ImproperlyConfigured."""
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"ordering that isn't 'asc' or 'desc'"
            r" needs to be given as full 'Sort' instances",
        ):
            Ordering(foo="foo")

    def test_arbitrary(self) -> None:
        """Test arbitrary orderings, with default icon."""
        o = Ordering(foo=Sort("foo", "Label", "icon"))
        self.assertEqual(list(o.options.keys()), ["foo"])
        self.assertEqual(o.options["foo"].label, "Label")
        self.assertEqual(o.options["foo"].icon, "icon")
        self.assertEqual(
            str(o.options["foo"].order_by),
            "(OrderBy(F(foo), descending=False),)",
        )

    def test_arbitrary_default_icon(self) -> None:
        """Test arbitrary orderings, with default icon."""
        o = Ordering(foo=Sort("foo", "Label"))
        self.assertEqual(list(o.options.keys()), ["foo"])
        self.assertEqual(o.options["foo"].label, "Label")
        self.assertEqual(o.options["foo"].icon, "sort-alpha-down")
        self.assertEqual(
            str(o.options["foo"].order_by),
            "(OrderBy(F(foo), descending=False),)",
        )

    def test_infer(self) -> None:
        """Infer label and icon (defaults to string)."""
        o = Ordering("name")
        self.assertEqual(o.options["asc"].label, "Alphabetical order")
        self.assertEqual(o.options["asc"].icon, "sort-alpha-down")
        self.assertEqual(o.options["desc"].label, "Reverse alphabetical order")
        self.assertEqual(o.options["desc"].icon, "sort-alpha-up-alt")


class StringOrderingTests(TestCase):
    """Tests for :py:class:`StringOrdering`."""

    def test_infer(self) -> None:
        """Infer label and icon."""
        o = StringOrdering("field")
        self.assertEqual(o.options["asc"].label, "Alphabetical order")
        self.assertEqual(o.options["asc"].icon, "sort-alpha-down")
        self.assertEqual(o.options["desc"].label, "Reverse alphabetical order")
        self.assertEqual(o.options["desc"].icon, "sort-alpha-up-alt")


class NumberOrderingTests(TestCase):
    """Tests for :py:class:`NumberOrdering`."""

    def test_infer(self) -> None:
        """Infer label and icon."""
        o = NumberOrdering("field")
        self.assertEqual(o.options["asc"].label, "Lowest first")
        self.assertEqual(o.options["asc"].icon, "sort-numeric-down")
        self.assertEqual(o.options["desc"].label, "Highest first")
        self.assertEqual(o.options["desc"].icon, "sort-numeric-up-alt")


class DateTimeOrderingTests(TestCase):
    """Tests for :py:class:`DateTimeOrdering`."""

    def test_infer(self) -> None:
        """Infer label and icon."""
        o = DateTimeOrdering("field")
        self.assertEqual(o.options["asc"].label, "Oldest first")
        self.assertEqual(o.options["asc"].icon, "sort-numeric-down")
        self.assertEqual(o.options["desc"].label, "Newest first")
        self.assertEqual(o.options["desc"].icon, "sort-numeric-up-alt")


class SortIconTests(TestCase):
    """Tests for sort icon rendering."""

    def orderings(
        self, options: int, active: int | None = None
    ) -> list[TableOrdering[User]]:
        """Create ordering lists."""
        table = mock.Mock()
        table.prefix = ""
        table.request = RequestFactory().get("/")
        table_orderings: TableOrderings[User] = TableOrderings(table)

        orderings: list[TableOrdering[User]] = []
        for i in range(options):
            orderings.append(
                TableOrdering(
                    table_orderings,
                    Sort(f"foo{i}", f"Foo{i}", f"icon{i}"),
                    "test",
                    f"foo{i}",
                    "Test",
                    f"order=test.foo{i}",
                )
            )

        if active is not None:
            orderings[active].active = True

        return orderings

    def test_compute_sort_icon_current_next(self) -> None:
        """Test current,next selection."""
        for size, active, current, next_ in (
            (0, None, None, None),
            (1, None, None, 0),
            (1, 0, 0, None),
            (2, None, None, 0),
            (2, 0, 0, 1),
            (2, 1, 1, 0),
            (3, None, None, 0),
            (3, 0, 0, 1),
            (3, 1, 1, 2),
            (3, 2, 2, 0),
        ):
            with self.subTest(size=size, active=active):
                orderings = self.orderings(size, active)
                c, n = compute_sort_icon_current_next(orderings)
                if current is None:
                    self.assertIsNone(c)
                else:
                    self.assertIs(c, orderings[current])
                if next_ is None:
                    self.assertIsNone(n)
                else:
                    self.assertIs(n, orderings[next_])
                if orderings:
                    self.assertHTMLValid(render_sort_icon(orderings))

    def test_render_with_no_orderings(self) -> None:
        """If there are no orderings, render returns None."""
        self.assertEqual(render_sort_icon([]), "")

    def test_render_with_current_only(self) -> None:
        """If current is defined, the icon shows it."""
        orderings = self.orderings(1, 0)
        tree = self.assertHTMLValid(render_sort_icon(orderings))
        i = self.assertHasElement(tree, "body/span/i")
        self.assertEqual(i.get("class"), f"bi bi-{orderings[0].icon}")

    def test_render_with_next_only(self) -> None:
        """If next is defined, the icon is a link to it."""
        orderings = self.orderings(1, None)
        tree = self.assertHTMLValid(render_sort_icon(orderings))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?" + orderings[0].query_string)

    def test_render_with_both(self) -> None:
        """If next is defined, the icon is a link to it."""
        orderings = self.orderings(2, 0)
        tree = self.assertHTMLValid(render_sort_icon(orderings))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?" + orderings[1].query_string)
        i = self.assertHasElement(a, "i")
        self.assertEqual(i.get("class"), f"bi bi-{orderings[0].icon}")
        self.assertEqual(
            i.get("title"),
            f"{orderings[0].sort.label} → {orderings[1].sort.label}",
        )


class TableOrderingTests(TestCase):
    """Tests for :py:class:`TableOrdering`."""

    def _orderings(self) -> TableOrderings[User]:
        """Create a mock table."""
        return mock.Mock()

    def test_members(self) -> None:
        sort = Sort("sname", "Sort Label", "sicon")
        to: TableOrdering[User] = TableOrdering(
            self._orderings(), sort, "what", "how", "What", "order=what.how"
        )
        self.assertEqual(to.sort, sort)
        self.assertEqual(to.what, "what")
        self.assertEqual(to.how, "how")
        self.assertEqual(to.label_what, "What")
        self.assertEqual(to.query_string, "order=what.how")
        self.assertEqual(to.icon, "sicon")
        self.assertFalse(to.active)
        self.assertEqual(str(to), "what.how")
        self.assertEqual(repr(to), """TableOrdering("what.how")""")
        tm = self.assertHTMLValid(to.table_menu_item)
        self.assertTextContentEqual(tm, "What: Sort Label")
        cm = self.assertHTMLValid(to.column_menu_item)
        self.assertTextContentEqual(cm, "Sort Label")


class TableOrderingsTests(TestCase):
    """Tests for :py:class:`TableOrderings`."""

    def _orderings(
        self,
        prefix: str = "",
        default_order: str | None = None,
        order: str | None = None,
    ) -> TableOrderings[User]:
        """Create a mock table."""
        table = mock.Mock()
        table.prefix = prefix
        if default_order is not None:
            table.default_order = default_order

        data: dict[str, Any] = {}
        if order is not None:
            data[f"{prefix}order"] = order
        table.request = RequestFactory().get("/", data=data)
        return TableOrderings(table)

    def test_init(self) -> None:
        orderings = self._orderings()
        self.assertEqual(orderings.field_name, "order")
        self.assertEqual(orderings.options, [])
        self.assertFalse(hasattr(orderings, "current"))

    def test_init_with_prefix(self) -> None:
        orderings = self._orderings(prefix="table-")
        self.assertEqual(orderings.field_name, "table-order")
        self.assertEqual(orderings.options, [])
        self.assertFalse(hasattr(orderings, "current"))

    def test_add(self) -> None:
        orderings = self._orderings()
        res = orderings.add("username", Ordering("username"))
        self.assertEqual(len(res), 2)

        to = res[0]
        self.assertIs(to.orderings, orderings)
        self.assertEqual(to.sort.label, "Alphabetical order")
        self.assertEqual(to.what, "username")
        self.assertEqual(to.how, "asc")
        self.assertEqual(to.label_what, "Username")
        self.assertEqual(to.query_string, "order=username.asc")
        self.assertFalse(to.active)

        to = res[1]
        self.assertIs(to.orderings, orderings)
        self.assertEqual(to.sort.label, "Reverse alphabetical order")
        self.assertEqual(to.what, "username")
        self.assertEqual(to.how, "desc")
        self.assertEqual(to.label_what, "Username")
        self.assertEqual(to.query_string, "order=username.desc")
        self.assertFalse(to.active)

        self.assertEqual(orderings.options, res)

    def test_parse_order(self) -> None:
        orderings = self._orderings()
        for order, exp_what, exp_how in (
            ("user", "user", None),
            ("-user", "user", "desc"),
            ("user.asc", "user", "asc"),
            ("user.desc", "user", "desc"),
            ("user.foo", "user", "foo"),
        ):
            with self.subTest(order=order):
                self.assertEqual(
                    orderings._parse_order(order), (exp_what, exp_how)
                )

    def test_find(self) -> None:
        orderings = self._orderings()
        asc, desc = orderings.add("username", Ordering("username"))
        for order, expected in (
            ("username", asc),
            ("-username", desc),
            ("username.asc", asc),
            ("username.desc", desc),
            ("username.foo", None),
            ("-username.asc", None),
            ("foo", None),
            ("foo.asc", None),
        ):
            self.assertIs(orderings._find(order), expected)

    def test_finalize(self) -> None:
        orderings = self._orderings(default_order="username")
        asc, desc = orderings.add("username", Ordering("username"))
        orderings.finalize()
        self.assertIs(orderings.current, asc)
        self.assertTrue(asc.active)
        self.assertFalse(desc.active)

    def test_finalize_wrong_default_ordering(self) -> None:
        orderings = self._orderings(default_order="foo")
        orderings.add("username", Ordering("username"))
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"default_order 'foo' does not match available ordering options",
        ):
            orderings.finalize()

    def test_finalize_selected_column(self) -> None:
        orderings = self._orderings(
            default_order="username", order="username.desc"
        )
        asc, desc = orderings.add("username", Ordering("username"))
        orderings.finalize()
        self.assertIs(orderings.current, desc)
        self.assertFalse(asc.active)
        self.assertTrue(desc.active)

    def test_finalize_empty(self) -> None:
        """Test finalize when there are no orderings."""
        orderings = self._orderings()
        orderings.finalize()
        self.assertEqual(orderings.options, [])
        self.assertFalse(hasattr(orderings, "current"))

    def test_order_queryset(self) -> None:
        user1 = self.playground.create_user("user1")
        user2 = self.playground.create_user("user2")
        user3 = self.playground.create_user("user3")

        for order, expected in (
            (None, [user1, user2, user3]),
            ("username", [user1, user2, user3]),
            ("-username", [user3, user2, user1]),
            ("username.asc", [user1, user2, user3]),
            ("username.desc", [user3, user2, user1]),
        ):
            with self.subTest(order=order):
                orderings = self._orderings(
                    default_order="username", order=order
                )
                orderings.add("username", Ordering("username"))
                orderings.finalize()
                self.assertQuerySetEqual(
                    orderings.order_queryset(
                        User.objects.filter(username__startswith="user")
                    ),
                    expected,
                )
