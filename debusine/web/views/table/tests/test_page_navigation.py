# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for thepage navigation widget."""


import lxml
from django.template import Context

from debusine.db.models import User
from debusine.web.views.table import Column, Table
from debusine.web.views.table.tests.test_table import TableTestCase


class PageNavigationTests(TableTestCase):
    """Tests for :py:class:`PageNavigation`."""

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

    def get_page_numbers(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> list[str]:
        """Return the list of page numbers in the rendered widget."""
        items = tree.xpath("//li[contains(@class, 'page-item')]")
        return [self.get_node_text_normalized(li) for li in items]

    def test_render_single_page(self) -> None:
        """Test rendering when no pagination is needed."""
        table = self._table()
        paginator = table.get_paginator(per_page=20)
        self.assertEqual(paginator.num_pages, 1)
        nav = paginator.page_navigation
        self.assertIs(nav.paginator, paginator)
        self.assertEqual(nav.render(Context()), "")

    def test_render(self) -> None:
        """Test a simple use case."""
        table = self._table()
        paginator = table.get_paginator(per_page=3)
        nav = paginator.page_navigation
        self.assertIs(nav.table, table)
        self.assertIs(nav.paginator, paginator)
        tree = self.assertHTMLValid(nav.render(Context()))
        current = self.assertHasElement(tree, "//li[@class='page-item active']")
        self.assertTextContentEqual(current, "1")
        self.assertEqual(self.get_page_numbers(tree), ["1", "2", "3", "4"])

    def test_ellipsis(self) -> None:
        """Test page navigation with ellipses."""
        for idx in range(20):
            self.playground.create_user(f"user1{idx:02d}")
        table = self._table()
        paginator = table.get_paginator(per_page=3)
        nav = paginator.page_navigation
        self.assertIs(nav.paginator, paginator)
        tree = self.assertHTMLValid(nav.render(Context()))
        self.assertEqual(
            self.get_page_numbers(tree), ["1", "2", "3", "4", "…", "10", "11"]
        )


class PageNavigationPreviewTests(TableTestCase):
    """Tests for :py:class:`PageNavigationPreview`."""

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

    def test_render_single_page(self) -> None:
        """Test rendering when no pagination is needed."""
        table = self._table(preview=True)
        paginator = table.get_paginator(per_page=20)
        self.assertEqual(paginator.num_pages, 1)
        nav = paginator.page_navigation
        self.assertIs(nav.paginator, paginator)
        self.assertEqual(nav.render(Context()), "")

    def test_render(self) -> None:
        """Test a simple use case."""
        table = self._table(preview=True)
        paginator = table.get_paginator(per_page=3)
        nav = paginator.page_navigation
        self.assertIs(nav.table, table)
        self.assertIs(nav.paginator, paginator)
        tree = self.assertHTMLValid(nav.render(Context()))
        self.assertTextContentEqual(tree, "3 out of 11 shown")
