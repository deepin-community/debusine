# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for table filters."""

import re
from typing import Any
from unittest import mock

from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db.models import Q
from django.forms import ChoiceField, Form, modelform_factory

from debusine.db.models import User
from debusine.test.django import TestCase
from debusine.web.views.table import (
    Column,
    FilterField,
    FilterSelectOne,
    FilterText,
    FilterToggle,
    Table,
)
from debusine.web.views.table.filters import (
    ActiveEntry,
    ActiveEntryToggle,
    BoundFilter,
    BoundFilterField,
    BoundFilterSelectOne,
    FormEntryField,
    FormEntrySelectOne,
    FormEntryText,
    MenuEntry,
    MenuEntryToggle,
    SelectOption,
)
from debusine.web.views.table.tests.test_table import TableTestCase
from debusine.web.views.tests.utils import ViewTestMixin


class MenuEntryTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`MenuEntry`."""

    def _mock_filter(self) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.filter_prefix = "fieldname"
        res.label = "Label"
        return res

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        widget = MenuEntry(self._mock_filter())
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "#filters-form-fieldname")
        self.assertEqual(a.get("aria-controls"), "filters-form-fieldname")
        self.assertTextContentEqual(a, "Label")


class MenuEntryToggleTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`MenuEntryToggle`."""

    def _mock_filter(self, value: Any) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.filter_prefix = "fieldname"
        res.label = "Label"
        res.qs_remove = ""
        res.qs_with_value = mock.Mock(return_value="fieldname=1")
        res.value = value
        return res

    def test_render_inactive(self) -> None:
        """Test :py:func:`render` on an inactive filter."""
        widget = MenuEntryToggle(self._mock_filter(None))
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?fieldname=1")
        self.assertEqual(a.get("class"), "dropdown-item")
        self.assertIsNone(a.get("aria-current"))
        self.assertTextContentEqual(a, "Label")

    def test_render_active(self) -> None:
        """Test :py:func:`render` on an active filter."""
        widget = MenuEntryToggle(self._mock_filter(True))
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?")
        self.assertEqual(a.get("class"), "dropdown-item active")
        self.assertEqual(a.get("aria-current"), "true")
        self.assertTextContentEqual(a, "Label")


class FormEntryTextTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`FormEntryText`."""

    def _mock_filter(self, value: Any) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.filter_prefix = "fieldname"
        res.label = "Label"
        res.value = value
        return res

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        widget = FormEntryText(self._mock_filter("testvalue"))
        tree = self.assertHTMLValid(self.render_widget(widget))
        div = self.assertHasElement(tree, "body/div")
        inp = self.assertHasElement(div, "input")
        self.assertEqual(inp.get("name"), "fieldname")
        self.assertEqual(inp.get("value"), "testvalue")


class FormEntrySelectOneTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`FormEntrySelectOne`."""

    def _mock_filter(self, value: Any) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.filter_prefix = "fieldname"
        res.label = "Label"
        res.value = value
        res.options = {
            "foo": SelectOption("foo", "Foo", Q(username="foo")),
            "bar": SelectOption("bar", "Bar", Q(username="bar")),
        }
        return res

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        widget = FormEntrySelectOne(self._mock_filter("foo"))
        tree = self.assertHTMLValid(self.render_widget(widget))
        div = self.assertHasElement(tree, "body/div")
        select = self.assertHasElement(div, "select")
        self.assertEqual(select.get("name"), "fieldname")
        self.assertEqual(select.option[0].get("value"), "foo")
        self.assertTrue(select.option[0].get("selected"))
        self.assertTextContentEqual(select.option[0], "Foo")
        self.assertEqual(select.option[1].get("value"), "bar")
        self.assertFalse(select.option[1].get("selected"))
        self.assertTextContentEqual(select.option[1], "Bar")


class ActiveEntryTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`ActiveEntry`."""

    def _mock_filter(self, value: str) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.qs_remove = ""
        res.label = "Label"
        res.format_value = mock.Mock(return_value=value)
        res.filter_prefix = "filter_prefix"
        return res

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        widget = ActiveEntry(self._mock_filter("42"))
        tree = self.assertHTMLValid(self.render_widget(widget))
        span = self.assertHasElement(tree, "body/span")
        open_a = span.a[0]
        self.assertEqual(open_a.get("href"), "#filters-form-filter_prefix")
        self.assertTextContentEqual(open_a, "Label: 42")
        remove_a = span.a[1]
        self.assertEqual(remove_a.get("href"), "?")


class ActiveEntryToggleTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`ActiveEntryToggle`."""

    def _mock_filter(self) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.qs_remove = ""
        res.label = "Label"
        return res

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        widget = ActiveEntryToggle(self._mock_filter())
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?")
        self.assertTextContentEqual(a, "Label")


class FilterTextTests(TestCase):
    """Tests for :py:class:`FilterText`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def test_metadata_default(self) -> None:
        """Test defaults for field metadata."""
        f = FilterText("User")
        f.name = "username"
        self.assertEqual(f.label, "User")
        self.assertEqual(f.q_field, "username__contains")
        self.assertIsNone(f.icon)
        self.assertIsNone(f.placeholder)
        self.assertIs(f.menu_entry, MenuEntry)
        self.assertIs(f.active_entry, ActiveEntry)
        self.assertIs(f.form_entry, FormEntryText)

    def test_metadata_full(self) -> None:
        """Test field metadata."""
        f = FilterText(
            "User",
            q_field="username__in",
            icon="icon",
            placeholder="placeholder",
        )
        self.assertEqual(f.label, "User")
        self.assertEqual(f.icon, "icon")
        self.assertEqual(f.q_field, "username__in")
        self.assertEqual(f.placeholder, "placeholder")

    def test_filter_queryset(self) -> None:
        """Test filter_queryset."""
        f = FilterText("User")
        f.name = "username"
        for value, expected in (
            ("foo", ["foo", "foobar"]),
            ("bar", ["foobar", "bar"]),
            ("barfoo", []),
        ):
            with self.subTest(value=value):
                self.assertCountEqual(
                    [
                        u.username
                        for u in f.filter_queryset(User.objects.all(), value)
                    ],
                    expected,
                )


class FilterToggleTests(TestCase):
    """Tests for :py:class:`FilterToggle`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def test_metadata_default(self) -> None:
        """Test defaults for field metadata."""
        f = FilterToggle("User", q=Q(username__contains="foo"))
        f.name = "username"
        self.assertEqual(f.label, "User")
        self.assertIsInstance(f.q, Q)
        self.assertIsNone(f.icon)
        self.assertIsNone(f.form_entry)
        self.assertIs(f.menu_entry, MenuEntryToggle)
        self.assertIs(f.active_entry, ActiveEntryToggle)
        self.assertIsNone(f.form_entry)

    def test_metadata_full(self) -> None:
        """Test field metadata."""
        f = FilterToggle(
            "User",
            q=Q(username__contains="foo"),
            icon="icon",
        )
        self.assertEqual(f.label, "User")
        self.assertIsInstance(f.q, Q)
        self.assertEqual(f.icon, "icon")
        self.assertIsNone(f.form_entry)

    def test_filter_queryset(self) -> None:
        """Test filter_queryset."""
        f = FilterToggle("User", q=Q(username__contains="foo"))
        f.name = "username"
        self.assertEqual(
            [u.username for u in f.filter_queryset(User.objects.all(), True)],
            ["foo", "foobar"],
        )

    def test_filter_queryset_inactive(self) -> None:
        """Test filter_queryset with an inactive field."""
        f = FilterToggle("User", q=Q(username__contains="foo"))
        f.name = "username"
        self.assertCountEqual(
            [u.username for u in f.filter_queryset(User.objects.all(), None)],
            ["_system", "foo", "foobar", "bar"],
        )


class FilterSelectOneTests(TestCase):
    """Tests for :py:class:`FilterSelectOne`."""

    def test_metadata_default(self) -> None:
        """Test defaults for field metadata."""
        f = FilterSelectOne(
            "User",
        )
        self.assertEqual(f.label, "User")
        self.assertEqual(f.options, ())
        self.assertIsNone(f.icon)
        self.assertIs(f.menu_entry, MenuEntry)
        self.assertIs(f.active_entry, ActiveEntry)
        self.assertIs(f.form_entry, FormEntrySelectOne)

    def test_metadata_full(self) -> None:
        """Test field metadata."""
        option = SelectOption("test", "Test", Q(username="test"), "icon")
        f = FilterSelectOne(
            "User",
            icon="icon",
            options=[option],
        )
        self.assertEqual(f.label, "User")
        self.assertEqual(f.icon, "icon")
        self.assertEqual(f.options, [option])

    def test_filter_queryset(self) -> None:
        """Test filter_queryset."""
        f = FilterSelectOne("User")
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"FilterSelectOne should have bound_class"
            r" set to a BoundFilterSelect",
        ):
            f.filter_queryset(User.objects.all(), "test")


class FilterFieldTests(TestCase):
    """Tests for :py:class:`FilterField`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def test_metadata_default(self) -> None:
        """Test defaults for field metadata."""
        f = FilterField()
        f.name = "username"
        self.assertEqual(f.label, "")
        self.assertEqual(f.q_field, "username__contains")
        self.assertIsNone(f.icon)
        self.assertIs(f.menu_entry, MenuEntry)
        self.assertIs(f.active_entry, ActiveEntry)
        self.assertIs(f.form_entry, FormEntryField)

    def test_metadata_full(self) -> None:
        """Test field metadata."""
        f = FilterField("Test", "testicon", q_field="test")
        f.name = "username"
        self.assertEqual(f.label, "Test")
        self.assertEqual(f.q_field, "test")
        self.assertEqual(f.icon, "testicon")

    def test_filter_queryset(self) -> None:
        """Test filter_queryset."""
        f = FilterField()
        f.name = "username"
        self.assertCountEqual(
            [u.username for u in f.filter_queryset(User.objects.all(), "bar")],
            ["foobar", "bar"],
        )


class BoundFilterTests(TableTestCase):
    """Tests for :py:class:`BoundFilter`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_user1 = FilterText("Username", q_field="username__contains")
            filter_user2 = FilterSelectOne("User")
            default_order = "user"

        return _Table

    def test_members(self) -> None:
        """Test object members."""
        table = self._table()
        f = table.filters["user1"]
        self.assertIs(f.filter, table.__class__.filter_definitions["user1"])
        self.assertIs(f.filters, table.filters)
        self.assertEqual(f.filter_prefix, "filter-user1")
        self.assertEqual(f.value, "")
        self.assertFalse(f.active)
        self.assertEqual(f.qs_remove, "")
        self.assertEqual(f.base_filter_args, [])
        self.assertEqual(f.name, "user1")
        self.assertEqual(f.label, "Username")
        self.assertIsNone(f.icon)
        self.assertEqual(f.qs_with_value(42), "filter-user1=42")
        self.assertIs(f.active_entry.filter, f)
        self.assertIs(f.menu_entry.filter, f)
        assert f.form is not None
        self.assertIs(f.form.filter, f)

    def test_table_prefix(self) -> None:
        """Ensure BoundFilter honors table_prefix."""
        table = self._table(prefix="test")
        f = table.filters["user1"]
        self.assertEqual(f.filter_prefix, "test-filter-user1")

    def test_missing_form_widget(self) -> None:
        """Getting form widget for FilterToggle gives None."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_user = FilterToggle("Foo", q=Q(username="foo"))
            default_order = "user"

        table = self._table(table_class=_Table)
        f = table.filters["user"]
        self.assertIsNone(f.form)

    def test_preserve_order(self) -> None:
        """Ensure BoundFilter query string handling preserves ordering."""
        table = self._table(qstring={"order": "user"})
        f = table.filters["user1"]
        self.assertEqual(f.qs_remove, "order=user")
        self.assertEqual(f.base_filter_args, [("order", "user")])

    def test_preserve_other_filters(self) -> None:
        """Ensure BoundFilter query string handling preserves other filterse."""
        table = self._table(
            qstring={
                "order": "user",
                "filter-user1": "foo",
                "filter-user2": "bar",
            }
        )
        f = table.filters["user1"]
        self.assertEqual(f.qs_remove, "order=user&filter-user2=bar")
        self.assertEqual(
            f.base_filter_args, [("order", "user"), ("filter-user2", "bar")]
        )

    def test_format_value(self) -> None:
        table = self._table()
        f = table.filters["user1"]
        for value, expected in (
            (42, "42"),
            ("foo", "foo"),
            (None, ""),
            (["foo", "bar"], "foo, bar"),
        ):
            with self.subTest(value=value):
                f.value = value
                self.assertEqual(f.format_value(), expected)

    def test_value_for_querystring(self) -> None:
        table = self._table()
        f = table.filters["user1"]
        for value, expected in (
            (True, ["1"]),
            (None, []),
            (False, []),
            ("foo", ["foo"]),
            ([1, 2, 3], ["1", "2", "3"]),
            (42, ["42"]),
        ):
            with self.subTest(value=value):
                self.assertEqual(f.value_for_querystring(value), expected)

    def test_qs_with_value(self) -> None:
        table = self._table(
            qstring={
                "order": "user",
                "filter-user1": "foo",
                "filter-user2": "bar",
            }
        )
        f = table.filters["user1"]
        self.assertEqual(
            set(f.qs_with_value([1, 2]).split("&")),
            {
                "order=user",
                "filter-user1=1",
                "filter-user1=2",
                "filter-user2=bar",
            },
        )

    def test_qs_with_value_empty_value(self) -> None:
        """Calling qs_with_value with an empty value gives qs_remove."""
        table = self._table(
            qstring={
                "order": "user",
                "filter-user1": "foo",
                "filter-user2": "bar",
            }
        )
        f = table.filters["user1"]
        self.assertEqual(
            set(f.qs_with_value(None).split("&")),
            {
                "order=user",
                "filter-user2=bar",
            },
        )

    def test_add_option(self) -> None:
        """BoundFilter.add_option refuses to work."""
        table = self._table(
            qstring={
                "order": "user",
                "filter-user1": "foo",
                "filter-user2": "bar",
            }
        )
        f = table.filters["user1"]
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            "add_option called on a field that does not support options.",
        ):
            f.add_option("test", "Test", Q(username="test"))

    def test_filter_queryset(self) -> None:
        """Test :py:func:`filter_queryset`."""
        table = self._table(qstring={"filter-user1": "foo"})
        f = table.filters["user1"]
        self.assertCountEqual(
            [u.username for u in f.filter_queryset(User.objects.all())],
            ["foo", "foobar"],
        )


class BoundFilterSelectOneTests(TableTestCase):
    """Tests for :py:class:`BoundFilterSelectOne`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_user1 = FilterText("Username", q_field="username")
            filter_user2 = FilterSelectOne("User")
            filter_user3 = FilterSelectOne(
                "User",
                options=[
                    SelectOption("bar", "Bar", Q(username="bar")),
                ],
            )
            default_order = "user"

            def init(self) -> None:
                super().init()
                self.filters["user2"].add_option(
                    "foo", "Foo", Q(username="foo")
                )
                self.filters["user2"].add_option(
                    "bar", "Bar", Q(username="bar")
                )

        return _Table

    def test_members(self) -> None:
        """Test object members."""
        table = self._table()
        f = table.filters["user2"]
        self.assertIsInstance(f, BoundFilterSelectOne)
        assert isinstance(f, BoundFilterSelectOne)
        self.assertIs(f.filter, table.__class__.filter_definitions["user2"])
        self.assertIs(f.filters, table.filters)
        self.assertEqual(f.filter_prefix, "filter-user2")
        self.assertEqual(f.value, "")
        self.assertFalse(f.active)
        self.assertEqual(f.qs_remove, "")
        self.assertEqual(f.base_filter_args, [])
        self.assertEqual(f.name, "user2")
        self.assertEqual(f.label, "User")
        self.assertIsNone(f.icon)
        self.assertEqual(list(f.options.keys()), ["foo", "bar"])
        self.assertEqual(f.qs_with_value(42), "filter-user2=42")
        self.assertIs(f.active_entry.filter, f)
        self.assertIs(f.menu_entry.filter, f)
        assert f.form is not None
        self.assertIs(f.form.filter, f)

    def test_declarative_options(self) -> None:
        """Test setting filter options declaratively."""
        table = self._table()
        f = table.filters["user3"]
        assert isinstance(f, BoundFilterSelectOne)
        self.assertEqual(list(f.options.keys()), ["bar"])

    def test_filter_queryset(self) -> None:
        """Test :py:func:`filter_queryset`."""
        table = self._table(qstring={"filter-user2": "foo"})
        f = table.filters["user2"]
        self.assertCountEqual(
            [u.username for u in f.filter_queryset(User.objects.all())],
            ["foo"],
        )

    def test_filter_queryset_invalid_value(self) -> None:
        """Test :py:func:`filter_queryset`."""
        table = self._table(qstring={"filter_user2": "does-not-exist"})
        f = table.filters["user2"]
        self.assertCountEqual(
            {u.username for u in f.filter_queryset(User.objects.all())},
            {"_system", "foo", "foobar", "bar"},
        )


class BoundFilterFieldTests(TableTestCase):
    """Tests for :py:class:`BoundFilterField`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterField("Username")
            default_order = "user"

        return _Table

    def test_members(self) -> None:
        """Test object members."""
        table = self._table()
        f = table.filters["username"]
        self.assertIsInstance(f, BoundFilterField)
        assert isinstance(f, BoundFilterField)
        self.assertIs(f.filter, table.__class__.filter_definitions["username"])
        self.assertIs(f.filters, table.filters)
        self.assertEqual(f.filter_prefix, "filter-username")
        self.assertIsNone(f.value)
        self.assertFalse(f.active)
        self.assertEqual(f.qs_remove, "")
        self.assertEqual(f.base_filter_args, [])
        self.assertEqual(f.name, "username")
        self.assertEqual(f.label, "Username")
        self.assertIsNone(f.icon)
        self.assertEqual(f.qs_with_value(42), "filter-username=42")
        self.assertIs(f.active_entry.filter, f)
        self.assertIs(f.menu_entry.filter, f)
        assert f.form is not None
        self.assertIs(f.form.filter, f)

    def test_format_value(self) -> None:
        table = self._table()
        f = table.filters["username"]
        for value, expected in (
            (None, ""),
            ("foo", "foo"),
            ("invalid", "invalid"),
        ):
            with self.subTest(value=value):
                f.value = value
                self.assertEqual(f.format_value(), expected)

    def test_invalid_field_name_with_auto_form(self) -> None:
        """Test object members."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_mischief = FilterField()
            default_order = "user"

        table = self._table(table_class=_Table)
        f = table.filters["mischief"]
        with self.assertRaisesRegex(
            FieldError,
            re.escape("Unknown field(s) (mischief) specified for User"),
        ):
            f.value

    def test_invalid_field_name_with_custom_form(self) -> None:
        """Test object members."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_mischief = FilterField()
            filter_form_class = modelform_factory(User, fields="__all__")
            default_order = "user"

        table = self._table(table_class=_Table)
        f = table.filters["mischief"]
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"'mischief' not found in table filter form UserForm."
            r" Available fields: \[.+\]",
        ):
            # Trigger instantiating the form field
            f.label

    def test_filter_queryset(self) -> None:
        """Test :py:func:`filter_queryset`."""
        table = self._table(qstring={"filter-username": "foo"})
        f = table.filters["username"]
        self.assertEqual(f.value, "foo")
        self.assertTrue(f.active)
        self.assertCountEqual(
            [u.username for u in f.filter_queryset(User.objects.all())],
            ["foo", "foobar"],
        )


class BoundFilterSelectFieldTests(TableTestCase):
    """Tests for :py:class:`BoundFilterField` using a select form field."""

    def get_table_class(self) -> type[Table[User]]:
        class _Form(Form):
            user = ChoiceField(
                choices=(
                    ("foo", "Foo"),
                    ("bar", "Bar"),
                    ("Multi", [("test1", "Test1"), ("test2", "Test2")]),
                )
            )

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_user = FilterField("Username")
            default_order = "user"
            filter_form_class = _Form

        return _Table

    def test_format_value(self) -> None:
        table = self._table()
        f = table.filters["user"]
        for value, expected in (
            (None, ""),
            ("foo", "Foo"),
            ("bar", "Bar"),
            ("test1", "Test1"),
            ("test2", "Test2"),
            (["foo", "test1"], "Foo, Test1"),
            ("invalid", "invalid"),
        ):
            with self.subTest(value=value):
                f.value = value
                self.assertEqual(f.format_value(), expected)


class FiltersTests(TableTestCase):
    """Tests for :py:class:`Filters`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.playground.create_user("foo")
        cls.playground.create_user("foobar")
        cls.playground.create_user("bar")

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_user1 = FilterText("Username", q_field="username__contains")
            filter_user2 = FilterSelectOne("User")
            default_order = "user"

        return _Table

    def test_filters_fields(self) -> None:
        """Test Filters fields instantiated from Table."""
        table = self._table()
        filters = table.filters
        self.assertIs(filters.table, table)
        self.assertEqual(filters.filter_prefix, "filter")
        self.assertEqual(list(filters.options.keys()), ["user1", "user2"])
        self.assertEqual(filters.qs_clear_filters, "")
        self.assertEqual(filters.non_filter_args, [])
        self.assertTrue(filters)
        self.assertEqual(
            [f.name for f in filters.available], ["user1", "user2"]
        )
        self.assertEqual([f.name for f in filters.active], [])
        self.assertEqual(filters["user1"].name, "user1")
        self.assertEqual(filters["user2"].name, "user2")
        self.assertEqual(filters.get_context_data(), {"filters": filters})

    def test_qs_clear_filters(self) -> None:
        """qs_clear_filters preserves ordering elements."""
        table = self._table(qstring={"order": "user", "filter-user": "test"})
        filters = table.filters
        self.assertEqual(filters.qs_clear_filters, "order=user")
        self.assertEqual(filters.non_filter_args, [("order", "user")])

    def test_qs_clear_filters_table_prefix(self) -> None:
        """qs_clear_filters preserves ordering elements."""
        table = self._table(
            prefix="test",
            qstring={"test-order": "user", "test-filter-user": "test"},
        )
        filters = table.filters
        self.assertEqual(filters.filter_prefix, "test-filter")
        self.assertEqual(filters.qs_clear_filters, "test-order=user")
        self.assertEqual(filters.non_filter_args, [("test-order", "user")])

    def test_form_default(self) -> None:
        """Test autogenerating a filter form."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterField()
            default_order = "user"

        table = self._table(table_class=_Table)
        form = table.filters.form
        self.assertCountEqual(
            form.fields.keys(),
            ["username"],
        )

    def test_form_default_skip_nonform_fields(self) -> None:
        """Skip non-form fields when autogenerating a filter form."""

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterField()
            filter_email = FilterText("Email")
            default_order = "user"

        table = self._table(table_class=_Table)
        form = table.filters.form
        self.assertCountEqual(
            form.fields.keys(),
            ["username"],
        )

    def test_form_custom(self) -> None:
        """Test autogenerating a filter form."""
        form_class = modelform_factory(User, fields=["email", "username"])

        class _Table(Table[User]):
            user = Column("User", ordering="username")
            default_order = "user"
            filter_form_class = form_class

        table = self._table(table_class=_Table)
        form = table.filters.form
        self.assertCountEqual(
            form.fields.keys(),
            [
                "email",
                "username",
            ],
        )

    def test_filter_queryset(self) -> None:
        """Test :py:func:`filter_queryset`."""
        table = self._table(qstring={"filter-user1": "foo"})
        self.assertCountEqual(
            [
                u.username
                for u in table.filters.filter_queryset(User.objects.all())
            ],
            ["foo", "foobar"],
        )

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        table = self._table()
        tree = self.assertHTMLValid(self.render_widget(table.filters))
        div = self.assertHasElement(tree, "body/div")

        # Filter menu
        menu_ul = self.assertHasElement(div.div[0], "ul")
        self.assertEqual(
            [self.get_node_text_normalized(li) for li in menu_ul.li],
            ["Username", "User", "", "Reset filters"],
        )
