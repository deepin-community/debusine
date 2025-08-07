# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the paginator extensions."""

from django.core.exceptions import ImproperlyConfigured
from django.template import engines
from django.test import override_settings

from debusine.db.models import User
from debusine.web.views.table import Column, Table
from debusine.web.views.table.tests.test_table import TableTestCase


class PaginatorTests(TableTestCase):
    """Tests for :py:class:`Paginator`."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        for idx in range(10):
            cls.playground.create_user(f"user{idx:02d}")

    def get_table_class(self) -> type[Table[User]]:
        class _Table(Table[User]):
            user = Column("User", ordering="username")
            default_order = "user"

        return _Table

    def test_bool_has_rows(self) -> None:
        """Test __bool__."""
        p = self._table().get_paginator(per_page=3)
        self.assertTrue(p)

    def test_bool_has_no_rows(self) -> None:
        """Test __bool__."""
        User.objects.all().delete()
        p = self._table().get_paginator(per_page=3)
        self.assertFalse(p)

    def test_pages(self) -> None:
        """Test with no columns defined."""
        p = self._table().get_paginator(per_page=3)
        self.assertEqual(p.num_pages, 4)
        self.assertFalse(p.page_obj.has_previous())

    def test_has_page_navigation(self) -> None:
        """Test has_page_navigation attribute."""
        table = self._table()
        for page_size, expected in (
            (3, True),
            (10, True),
            (11, False),
            (12, False),
        ):
            with self.subTest(page_size=page_size):
                p = table.get_paginator(per_page=page_size)
                self.assertEqual(p.has_page_navigation, expected)

    def test_render(self) -> None:
        """Test rendering as a widget."""
        with self.template_dir() as template_dir:
            (template_dir / "test.html").write_text("{{paginator.per_page}}")
            table = self._table()
            table.template_name = "test.html"
            p = table.get_paginator(per_page=42)
            rendered = self.render_widget(p)
            self.assertEqual(rendered, "42")

    def test_render_thead(self) -> None:
        """Test rendering <thead> as a widget."""
        with self.template_dir() as template_dir:
            (template_dir / "test.html").write_text(
                "{% load debusine %}{% widget paginator.thead %}"
            )
            table = self._table()
            table.template_name = "test.html"
            p = table.get_paginator(per_page=42)
            tree = self.assertHTMLValid(self.render_widget(p))
            thead = self.assertHasElement(tree, "body/thead")
            # There are no filters, so there is only one row
            self.assertEqual(len(thead.tr), 1)
            self.assertEqual(
                [
                    self.get_node_text_normalized(th.div.div[0])
                    for th in thead.tr.th
                ],
                ["User"],
            )

    def test_render_tfoot(self) -> None:
        """Test rendering <tfoot> as a widget."""
        with self.template_dir() as template_dir:
            (template_dir / "test.html").write_text(
                "{% load debusine %}{% widget paginator.tfoot %}"
            )
            table = self._table()
            table.template_name = "test.html"
            p = table.get_paginator(per_page=10)
            tree = self.assertHTMLValid(self.render_widget(p))
            tfoot = self.assertHasElement(tree, "body/tfoot")
            tr = self.assertHasElement(tfoot, "tr")
            self.assertEqual(tr.td.get("colspan"), "1")

    def test_render_without_template_name(self) -> None:
        """Test rendering as a widget."""
        table = self._table()
        p = table.get_paginator(per_page=42)

        template = engines["django"].from_string(
            "{% load debusine %}{% widget widget %}"
        )
        setattr(template, "engine", engines["django"])
        # Set DEBUG=True to have render raise exceptions instead of logging
        # them
        with (
            self.assertRaisesRegex(
                ImproperlyConfigured,
                "Table.template_name needs to be set"
                " to use Paginator as a widget",
            ),
            override_settings(DEBUG=True),
        ):
            template.render({"widget": p})
