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
from typing import Any, cast
from unittest import mock

import lxml
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
    BoundFilter,
    BoundFilterField,
    BoundFilterSelectOne,
    MainEntryField,
    MainEntrySelectOne,
    MainEntryText,
    MainEntryToggle,
    MenuEntry,
    MenuEntryToggle,
    SelectOption,
)
from debusine.web.views.table.tests.test_table import TableTestCase
from debusine.web.views.tests.utils import ViewTestMixin


class MenuEntryTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`MenuEntry`."""

    def mock_filter(self) -> BoundFilter[Any]:
        """Build a mock BoundFilter."""
        res = mock.Mock()
        res.filter_prefix = "fieldname"
        res.label = "Label"
        res.dom_id = "dom_id"
        res.dom_id_form_dropdown = "dom_id1"
        return res

    def test_render(self) -> None:
        """Test :py:func:`render`."""
        widget = MenuEntry(self.mock_filter())
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("data-bs-target"), "#dom_id1")
        self.assertTextContentEqual(a, "Label")


class MenuEntryToggleTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`MenuEntryToggle`."""

    def mock_filter(self, value: Any) -> BoundFilter[Any]:
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
        widget = MenuEntryToggle(self.mock_filter(None))
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?fieldname=1")
        self.assertEqual(a.get("class"), "dropdown-item")
        self.assertIsNone(a.get("aria-current"))
        self.assertTextContentEqual(a, "Label")

    def test_render_active(self) -> None:
        """Test :py:func:`render` on an active filter."""
        widget = MenuEntryToggle(self.mock_filter(True))
        tree = self.assertHTMLValid(self.render_widget(widget))
        a = self.assertHasElement(tree, "body/a")
        self.assertEqual(a.get("href"), "?")
        self.assertEqual(a.get("class"), "dropdown-item active")
        self.assertEqual(a.get("aria-current"), "true")
        self.assertTextContentEqual(a, "Label")


class MainEntryFormTests(TableTestCase, ViewTestMixin):
    """Base class for main entry tests for entries with forms."""

    def mock_filter(self, value: Any = None) -> BoundFilterField[Any]:
        """Build a mock BoundFilter."""
        qstring: dict[str, Any] = {"order": "test"}
        if value:
            qstring["filter-username"] = value

        table = self._table(qstring=qstring)
        return cast(BoundFilterField[Any], table.filters["username"])

    def assertDropdownOpener(
        self, opener: lxml.objectify.ObjectifiedElement, f: BoundFilter[Any]
    ) -> None:
        """Check the attributes of the dropdown opener."""
        self.assertEqual(opener.get("id"), f"{f.dom_id}-form-dropdown")
        self.assertEqual(opener.get("data-bs-toggle"), "dropdown")
        self.assertEqual(opener.get("data-bs-auto-close"), "outside")

    def assertDropdown(
        self, dropdown: lxml.objectify.ObjectifiedElement, f: BoundFilter[Any]
    ) -> lxml.objectify.ObjectifiedElement:
        """
        Check the general layout of the dropdown form.

        :returns: the form element
        """
        btn_add = dropdown.button[0]
        self.assertEqual(btn_add.get("type"), "submit")
        self.assertEqual(btn_add.get("form"), f"{f.dom_id}-form")
        self.assertTextContentEqual(btn_add, "Add filter")

        btn_cancel = dropdown.button[1]
        self.assertElementHasClass(btn_cancel, "debusine-dropdown-close")
        self.assertTextContentEqual(btn_cancel, "Cancel")

        form = dropdown.form[0]
        self.assertEqual(form.get("id"), f"{f.dom_id}-form")
        self.assertEqual(form.get("method"), "get")
        return form

    def assertActive(
        self, tree: lxml.objectify.ObjectifiedElement, f: BoundFilter[Any]
    ) -> lxml.objectify.ObjectifiedElement:
        """
        Check that the widget is representing an active field.

        :returns: the form element
        """
        main = self.assertHasElement(tree, "body/div")
        buttongroup = self.assertHasElement(main, "div")
        self.assertEqual(
            buttongroup.get("aria-label"),
            f"Controls for active filter {f.label}",
        )

        opener = self.assertHasElement(buttongroup, "button")
        self.assertDropdownOpener(opener, f)
        self.assertTextContentEqual(opener, f"{f.label}: {f.value_formatted}")

        remover = self.assertHasElement(buttongroup, "a")
        self.assertEqual(remover.get("href"), "?order=test")
        self.assertEqual(remover.get("title"), "Remove filter")

        dropdown = self.assertHasElement(buttongroup, "div")
        return self.assertDropdown(dropdown, f)


class MainEntryFieldTests(MainEntryFormTests):
    """Tests for :py:class:`MainEntryField`."""

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterField("Username")
            default_order = "user"

        return _Table

    def assertForm(
        self, f: BoundFilterField[Any], form: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check the form contents."""
        label = self.assertHasElement(form, "label")
        self.assertTextContentEqual(label, f.label)
        inp = self.assertHasElement(form, "input")
        self.assertEqual(inp.get("id"), f.form_field.id_for_label)
        self.assertEqual(inp.get("name"), f.filter_prefix)
        self.assertEqual(inp.get("value"), f.value)

    def test_render_active(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter("testvalue")
        widget = MainEntryField(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        form = self.assertActive(tree, f)
        self.assertForm(f, form)

    def test_render_inactive(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter()
        widget = MainEntryField(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        main = self.assertHasElement(tree, "body/div")
        self.assertDropdownOpener(main.div[0], f)
        form = self.assertDropdown(main.div[1], f)
        self.assertForm(f, form)


class MainEntryTextTests(MainEntryFormTests):
    """Tests for :py:class:`MainEntryText`."""

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterText("Username")
            default_order = "user"

        return _Table

    def assertForm(
        self, f: BoundFilter[Any], form: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check the form contents."""
        label = self.assertHasElement(form, "label")
        self.assertTextContentEqual(label, f.label)
        inp = self.assertHasElement(form, "input")
        self.assertEqual(
            inp.get("id"), f"{f.dom_id}-formfield-{f.filter_prefix}"
        )
        self.assertEqual(inp.get("name"), f.filter_prefix)
        self.assertEqual(
            inp.get("value"), f.value if f.value is not None else ""
        )

    def test_render_active(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter("testvalue")
        widget = MainEntryText(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        form = self.assertActive(tree, f)
        self.assertForm(f, form)

    def test_render_inactive(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter()
        widget = MainEntryText(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        main = self.assertHasElement(tree, "body/div")
        self.assertDropdownOpener(main.div[0], f)
        form = self.assertDropdown(main.div[1], f)
        self.assertForm(f, form)


class MainEntrySelectOneTests(MainEntryFormTests):
    """Tests for :py:class:`MainEntrySelectOne`."""

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterSelectOne(
                "Username",
                options=(
                    SelectOption("foo", "Foo", Q(username="foo")),
                    SelectOption("bar", "Bar", Q(username="bar")),
                ),
            )
            default_order = "user"

        return _Table

    def assertForm(
        self,
        f: BoundFilter[Any],
        form: lxml.objectify.ObjectifiedElement,
        active: int | None = None,
    ) -> None:
        """Check the form contents."""
        label = self.assertHasElement(form, "label")
        self.assertTextContentEqual(label, f.label)
        select = self.assertHasElement(form, "select")
        self.assertEqual(
            select.get("id"), f"{f.dom_id}-formfield-{f.filter_prefix}"
        )
        self.assertEqual(select.get("name"), f.filter_prefix)

        self.assertEqual(select.option[0].get("value"), "foo")
        self.assertEqual(bool(select.option[0].get("selected")), active == 0)
        self.assertTextContentEqual(select.option[0], "Foo")
        self.assertEqual(select.option[1].get("value"), "bar")
        self.assertEqual(bool(select.option[1].get("selected")), active == 1)
        self.assertTextContentEqual(select.option[1], "Bar")

    def test_render_active(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter("foo")
        widget = MainEntrySelectOne(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        form = self.assertActive(tree, f)
        self.assertForm(f, form, active=0)

    def test_render_inactive(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter()
        widget = MainEntrySelectOne(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        main = self.assertHasElement(tree, "body/div")
        self.assertDropdownOpener(main.div[0], f)
        form = self.assertDropdown(main.div[1], f)
        self.assertForm(f, form)


class MainEntryToggleTests(MainEntryFormTests):
    """Tests for :py:class:`MainEntryToggle`."""

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            filter_username = FilterToggle("Username", q=Q(username="foo"))
            default_order = "user"

        return _Table

    def test_render_active(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter(True)
        widget = MainEntryToggle(f)
        tree = self.assertHTMLValid(self.render_widget(widget))
        div = self.assertHasElement(tree, "body/div")
        self.assertEqual(
            div.get("aria-label"), "Controls for active filter Username"
        )
        self.assertTextContentEqual(div.span, "Username")

        el_close = self.assertHasElement(div, "a")
        self.assertEqual(el_close.get("href"), "?order=test")

    def test_render_inactive(self) -> None:
        """Test :py:func:`render` with an active field."""
        f = self.mock_filter()
        widget = MainEntryToggle(f)
        self.assertEqual(self.render_widget(widget).strip(), "")


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
        self.assertIs(f.main_entry, MainEntryText)

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
        self.assertIs(f.menu_entry, MenuEntryToggle)
        self.assertIs(f.main_entry, MainEntryToggle)

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
        self.assertIs(f.menu_entry, MenuEntryToggle)
        self.assertIs(f.main_entry, MainEntryToggle)

    def test_filter_queryset(self) -> None:
        """Test filter_queryset."""
        f = FilterToggle("User", q=Q(username__contains="foo"))
        f.name = "username"
        self.assertCountEqual(
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
        self.assertIs(f.main_entry, MainEntrySelectOne)

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
        self.assertIs(f.main_entry, MainEntryField)

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
        self.assertIs(f.menu_entry.filter, f)
        self.assertIs(f.main_entry.filter, f)

    def test_table_prefix(self) -> None:
        """Ensure BoundFilter honors table_prefix."""
        table = self._table(prefix="test")
        f = table.filters["user1"]
        self.assertEqual(f.filter_prefix, "test-filter-user1")

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

    def test_value_formatted(self) -> None:
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
                self.assertEqual(f._format_value(), expected)

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
        self.assertIs(f.menu_entry.filter, f)
        self.assertIs(f.main_entry.filter, f)

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
        self.assertIs(f.menu_entry.filter, f)
        self.assertIs(f.main_entry.filter, f)

    def test_value_formatted(self) -> None:
        table = self._table()
        f = table.filters["username"]
        for value, expected in (
            (None, ""),
            ("foo", "foo"),
            ("invalid", "invalid"),
        ):
            with self.subTest(value=value):
                f.value = value
                self.assertEqual(f._format_value(), expected)

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

    def test_value_formatted(self) -> None:
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
                self.assertEqual(f._format_value(), expected)


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
        tree = self.assertHTMLValid(
            self.render_widget(table.filters), dump_on_error=True
        )
        div = self.assertHasElement(tree, "body/div")

        # Filter menu
        filters_opener = self.assertHasElement(
            div, "//div[@id='table-filters-menu']"
        )
        parent = filters_opener.getparent()
        assert parent is not None
        filters_menu = parent.ul[0]
        self.assertEqual(
            [self.get_node_text_normalized(li) for li in filters_menu.li],
            ["Add filter on field…", "Username", "User"],
        )
