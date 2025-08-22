# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Pagination support widgets."""

from functools import cached_property
from typing import Generic, TYPE_CHECKING, TypeVar

from django.core import paginator as django_paginator
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.template.context import Context

from debusine.web.views.base import Widget
from debusine.web.views.table.page_navigation import (
    PageNavigation,
    PageNavigationPreview,
    PageNavigationWidget,
)

if TYPE_CHECKING:
    from django.core.paginator import _SupportsPagination

    from debusine.web.views.table.table import Table

    PaginatorBase = django_paginator.Paginator
    PageBase = django_paginator.Page
    _SupportsPagination  # fake usage for vulture
else:
    # Django's Paginator doesn't support generic types at run-time yet.
    class _PaginatorBase:
        def __class_getitem__(*args):
            return django_paginator.Paginator

    class _PageBase:
        def __class_getitem__(*args):
            return django_paginator.Page

    PaginatorBase = _PaginatorBase
    PageBase = _PageBase


M = TypeVar("M", bound=Model)


class TableHead(Widget, Generic[M]):
    """Render a standard table header."""

    template_name = "web/_table_header.html"

    def __init__(self, table: "Table[M]") -> None:
        """Build a TableHead widget."""
        self.table = table

    def render(self, context: Context) -> str:
        """Render as a widget if template_name is set."""
        assert context.template is not None
        template = context.template.engine.get_template(self.template_name)
        with context.update({"table": self.table}):
            return template.render(context)


class TableFoot(Widget, Generic[M]):
    """Render a standard table footer."""

    template_name = "web/_table_footer.html"

    def __init__(self, paginator: "Paginator[M]") -> None:
        """Build a TableFoot widget."""
        self.paginator = paginator

    def render(self, context: Context) -> str:
        """Render as a widget if template_name is set."""
        assert context.template is not None
        template = context.template.engine.get_template(self.template_name)
        with context.update(
            {"paginator": self.paginator, "table": self.paginator.table}
        ):
            return template.render(context)


class Paginator(Widget, PaginatorBase[M], Generic[M]):
    """Pagination handling via widgets."""

    def __init__(
        self,
        table: "Table[M]",
        per_page: int,
        *,
        orphans: int = 0,
        allow_empty_first_page: bool = True,
    ) -> None:
        """
        Build the Pagination object.

        :param table: the table to paginate
        :param per_page: passed to Django's Paginator
        :param orphans: passed to Django's Paginator
        :param allow_empty_first_page: passed to Django's Paginator
        """
        super().__init__(
            table.rows,
            per_page,
            orphans=orphans,
            allow_empty_first_page=allow_empty_first_page,
        )
        self.table = table

    def __bool__(self) -> bool:
        """
        Check if the current page has information to display.

        Returns True if any of this is true:

         * The current page has rows to show
         * There is at least one active filter
        """
        return bool(self.page_obj) or bool(self.table.filters.active)

    @cached_property
    def page_obj(self) -> PageBase[M]:
        """Return the Django paginator.Page object."""
        return self.get_page(
            self.table.request.GET.get(f"{self.table.prefix}page")
        )

    @cached_property
    def page_navigation(self) -> PageNavigationWidget:
        """Return the PageNavigation widget."""
        if self.table.is_preview:
            return PageNavigationPreview(self)
        else:
            return PageNavigation(self)

    @cached_property
    def has_page_navigation(self) -> bool:
        """Check if page navigation is needed."""
        return self.num_pages > 1

    @cached_property
    def thead(self) -> TableHead[M]:
        """Return a standard <thead> widget."""
        return TableHead(self.table)

    @cached_property
    def tfoot(self) -> TableFoot[M]:
        """Return a standard <tfoot> widget."""
        return TableFoot(self)

    def render(self, context: Context) -> str:
        """Render as a widget if template_name is set."""
        if self.table.template_name is None:
            raise ImproperlyConfigured(
                "Table.template_name needs to be set"
                " to use Paginator as a widget"
            )
        assert context.template is not None
        template = context.template.engine.get_template(
            self.table.template_name
        )
        with context.update({"paginator": self, "table": self.table}):
            return template.render(context)
