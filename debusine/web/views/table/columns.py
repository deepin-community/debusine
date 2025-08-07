# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Table column definition and rendering."""

from collections.abc import Iterator
from functools import cached_property
from typing import Any, Generic, TYPE_CHECKING, TypeVar

from django.db.models import Model
from django.template.context import Context
from django.utils.safestring import SafeString

from debusine.web.views.base import Widget
from debusine.web.views.table.declarative import Attribute
from debusine.web.views.table.ordering import (
    DateTimeOrdering,
    NumberOrdering,
    OrderBys,
    Ordering,
    StringOrdering,
    TableOrdering,
    render_sort_icon,
)

if TYPE_CHECKING:
    from debusine.web.views.table.table import Table


M = TypeVar("M", bound=Model)


class Column(Attribute):
    """A table column definition."""

    #: Default Ordering class to use
    ordering_class: type[Ordering] = Ordering

    #: Column title
    title: str
    #: Column description
    help_text: str | None
    #: Ordering
    ordering: Ordering | None

    def __init__(
        self,
        title: str,
        *,
        ordering: Ordering | OrderBys | None = None,
        name: str | None = None,
        help_text: str | None = None,
    ) -> None:
        """Store arguments in the Column object for rendering."""
        super().__init__(name=name)
        from debusine.web.views.table.ordering import Ordering, order_by_tuple

        self.title = title
        self.help_text = help_text

        match ordering:
            case Ordering() | None:
                self.ordering = ordering
            case _:
                self.ordering = self.ordering_class(order_by_tuple(ordering))


class StringColumn(Column):
    """A table column that sorts alphabetically."""

    ordering_class: type[Ordering] = StringOrdering


class NumberColumn(Column):
    """A table column that sorts numerically."""

    ordering_class: type[Ordering] = NumberOrdering


class DateTimeColumn(Column):
    """A table column that sorts as datetime."""

    ordering_class: type[Ordering] = DateTimeOrdering


class BoundColumn(Widget, Generic[M]):
    """An instantiated table column."""

    def __init__(self, column: Column, table: "Table[Any]") -> None:
        """Store arguments in the Column object for rendering."""
        self.column = column
        self.table = table
        #: Available sort options
        self.orderings: list[TableOrdering[M]] = []

    def __str__(self) -> str:
        """Return the column name."""
        return self.name

    def __repr__(self) -> str:
        """Return a debugging representation."""
        return f"BoundColumn({self.name!r})"

    @property
    def name(self) -> str:
        """Return the column name."""
        return self.column.name

    @property
    def title(self) -> str:
        """Return the column title."""
        return self.column.title

    @cached_property
    def sort_icon(self) -> SafeString:
        """Return the sort icon for the table header."""
        return render_sort_icon(self.orderings)

    def get_context_data(self) -> dict[str, Any]:
        """Return context for rendering."""
        return {"column": self}

    def render(self, context: Context) -> str:
        """Render the column header."""
        assert context.template is not None
        template = context.template.engine.get_template(
            "web/_table_column_header.html"
        )
        with context.update(self.get_context_data()):
            return template.render(context)


class Columns(Generic[M]):
    """All columns for a table."""

    def __init__(self, table: "Table[M]"):
        """Columns for a table."""
        self.table = table
        self.columns: dict[str, BoundColumn[M]] = {}

    def add(self, name: str, column: Column) -> BoundColumn[M]:
        """Add a column definition."""
        bc: BoundColumn[M] = BoundColumn(column, self.table)
        self.columns[name] = bc
        return bc

    def __getitem__(self, name: str) -> BoundColumn[M]:
        """Get a filter by name."""
        return self.columns[name]

    def __iter__(self) -> Iterator[BoundColumn[M]]:
        """Iterate all columns."""
        return iter(self.columns.values())
