# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Table ordering definitions."""

from collections.abc import Sequence
from functools import cached_property
from typing import (
    Any,
    Generic,
    Optional,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    assert_never,
    cast,
)

from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, Model, QuerySet
from django.db.models.expressions import OrderBy
from django.forms.utils import pretty_name
from django.utils.html import format_html
from django.utils.safestring import SafeString

from debusine.web.views.table.declarative import Attribute

if TYPE_CHECKING:
    from debusine.web.views.table.table import Table

M = TypeVar("M", bound=Model)

OrderBys: TypeAlias = str | F | OrderBy | Sequence[str | F | OrderBy]


def to_order_by(val: str | F | OrderBy) -> OrderBy:
    """Convert the argument to an F-expression if needed."""
    match val:
        case str():
            if val.startswith("-"):
                return F(val[1:]).desc()
            else:
                return F(val).asc()
        case F():
            return val.asc()
        case OrderBy():
            return val
        case _ as unreachable:
            assert_never(unreachable)


def order_by_tuple(arg: OrderBys) -> tuple[OrderBy, ...]:
    """Normalize an argument to a tuple of OrderBy expressions."""
    match arg:
        case str() | F() | OrderBy():
            return (to_order_by(arg),)
        case _:
            return tuple(to_order_by(x) for x in arg)


class Sort:
    """A way to sort table rows."""

    #: Django ORM order_by expression
    order_by: tuple[OrderBy, ...]
    #: User-visible label
    label: str
    #: Icon name
    icon: str

    def __init__(
        self, order_by: OrderBys, label: str, icon: str = "sort-alpha-down"
    ):
        """
        Store sort information.

        :param order_by: ORM ordering definition
        :param label: label for this sort style
        :param icon: icon for this sort style
        """
        self.order_by = order_by_tuple(order_by)
        self.label = label
        self.icon = icon


class Ordering(Attribute):
    """
    Declarative definition of a bundle of ordering options.

    This is a shortcut for declaratively defining one or more related ordering
    variants.
    """

    #: order_by expressions indexed by ordering name
    options: dict[str, Sort]

    def __init__(
        self,
        asc: Sort | OrderBys | None = None,
        desc: Sort | OrderBys | None = None,
        label: str | None = None,
        **kwargs: Sort | OrderBys,
    ) -> None:
        """
        Parse arguments into Sort instances.

        :param asc: ascending or default sort order
        :param desc: descending/reverse of asc
        :param label: ordering label (optional, inferred if needed)
        """
        self.label = label
        self.options = {}
        if asc is not None:
            self.options["asc"] = (
                asc if isinstance(asc, Sort) else self.make_asc(asc)
            )
        if desc is not None:
            self.options["desc"] = (
                desc if isinstance(desc, Sort) else self.make_desc(desc)
            )
        elif asc is not None:
            self.options["desc"] = self.make_desc(
                tuple(
                    cast(OrderBy, x.copy().reverse_ordering())
                    for x in self.options["asc"].order_by
                )
            )
        for name, sort in kwargs.items():
            if not isinstance(sort, Sort):
                raise ImproperlyConfigured(
                    "ordering that isn't 'asc' or 'desc' needs"
                    " to be given as full 'Sort' instances"
                )
            self.options[name] = sort

    def make_asc(self, order_by: OrderBys) -> Sort:
        """Infer details for ascending sorting."""
        return Sort(
            order_by_tuple(order_by), "Alphabetical order", "sort-alpha-down"
        )

    def make_desc(self, order_by: OrderBys) -> Sort:
        """Infer details for descending sorting."""
        return Sort(
            order_by_tuple(order_by),
            "Reverse alphabetical order",
            "sort-alpha-up-alt",
        )


class StringOrdering(Ordering):
    """Sorts strings alphabetically."""


class NumberOrdering(Ordering):
    """Sort numbers."""

    def make_asc(self, order_by: OrderBys) -> Sort:
        """Infer details for ascending sorting."""
        return Sort(
            order_by_tuple(order_by), "Lowest first", "sort-numeric-down"
        )

    def make_desc(self, order_by: OrderBys) -> Sort:
        """Infer details for descending sorting."""
        return Sort(
            order_by_tuple(order_by),
            "Highest first",
            "sort-numeric-up-alt",
        )


class DateTimeOrdering(Ordering):
    """Sort datetimes."""

    def make_asc(self, order_by: OrderBys) -> Sort:
        """Infer details for ascending sorting."""
        return Sort(
            order_by_tuple(order_by), "Oldest first", "sort-numeric-down"
        )

    def make_desc(self, order_by: OrderBys) -> Sort:
        """Infer details for descending sorting."""
        return Sort(
            order_by_tuple(order_by),
            "Newest first",
            "sort-numeric-up-alt",
        )


def compute_sort_icon_current_next(
    orderings: list["TableOrdering[Any]"],
) -> tuple[Optional["TableOrdering[Any]"], Optional["TableOrdering[Any]"]]:
    """Select current and next orderings to use for sort icons."""
    if not orderings:
        return None, None
    elif len(orderings) == 1:
        return (
            orderings[0] if orderings[0].active else None,
            orderings[0] if not orderings[0].active else None,
        )
    else:
        # Cycle between available sort options
        for idx, ordering in enumerate(orderings):
            if ordering.active:
                return (
                    ordering,
                    orderings[(idx + 1) % len(orderings)],
                )
        else:
            return (None, orderings[0])


def render_sort_icon(
    orderings: list["TableOrdering[M]"], for_table: bool = False
) -> SafeString:
    """Render an icon showing current ordering."""
    current, next_ = compute_sort_icon_current_next(orderings)
    class_ = "btn"
    if for_table:
        class_ += " btn-light"
    else:
        class_ += " btn-sm"
    if current:
        if not for_table:
            class_ += " active"
        if next_:
            return format_html(
                "<a href='?{query_string}' class='{class_}'>"
                "<i class='bi bi-{icon}'"
                " title='{current_label} → {next_label}'>"
                "</i></a>",
                class_=SafeString(class_),
                query_string=next_.query_string,
                icon=current.icon,
                current_label=current.sort.label,
                next_label=next_.sort.label,
            )
        else:
            return format_html(
                "<span class='{class_}'>"
                "<i class='bi bi-{icon}' title='{label}'></i>"
                "</span>",
                class_=SafeString(class_),
                icon=current.icon,
                label=current.sort.label,
            )
    elif next_:
        return format_html(
            "<a href='?{query_string}' class='{class_}'>"
            "<i class='bi bi-{icon}' title='{label}'></i></a>",
            class_=SafeString(class_),
            query_string=next_.query_string,
            icon=next_.icon,
            label=next_.sort.label,
        )
    else:
        return SafeString()


class TableOrdering(Generic[M]):
    """
    Instantiated ordering option for a table.

    This is a way to order a table, defining what is being ordered and in what
    way.
    """

    def __init__(
        self,
        orderings: "TableOrderings[M]",
        sort: Sort,
        what: str,
        how: str,
        label_what: str,
        query_string: str,
    ) -> None:
        """
        Ordering action bound to a table column.

        :param orderings: containing collection of table orderings
        :param sort: definition of how rows are ordered
        :param what: codename of what we are ordering
        :param how: codename of how we are ordering
        :param label_what: label describing what is being ordered
        :param query_string: URL query string to activate this ordering
        """
        self.orderings = orderings
        self.sort = sort
        self.what: str = what
        self.how: str = how
        self.label_what: str = label_what
        self.query_string: str = query_string
        #: Whether this is the current ordering
        #: (externally set at table instantiation)
        self.active: bool = False

    def __str__(self) -> str:
        """Return a stringified name."""
        return f"{self.what}.{self.how}"

    def __repr__(self) -> str:
        """Return a stringified name."""
        return f"""TableOrdering("{self.what}.{self.how}")"""

    @property
    def icon(self) -> str:
        """Icon representing the ordering."""
        return self.sort.icon

    @cached_property
    def table_menu_item(self) -> SafeString:
        """Render the ordering as a table-wide menu entry."""
        return format_html(
            "<a class='dropdown-item{active}' href='?{query_string}'"
            "{aria_current}><i class='bi bi-{icon}'></i> "
            "{label_what}: {label}</a>",
            label_what=self.label_what,
            label=self.sort.label,
            icon=self.icon,
            query_string=self.query_string,
            active=" active" if self.active else "",
            aria_current=(
                SafeString(" aria-current='true'") if self.active else ""
            ),
        )

    @cached_property
    def column_menu_item(self) -> SafeString:
        """Render the ordering as a column header menu entry."""
        return format_html(
            "<a class='dropdown-item{active}' href='?{query_string}'"
            "{aria_current}><i class='bi bi-{icon}'></i> {label}</a>",
            label=self.sort.label,
            icon=self.icon,
            query_string=self.query_string,
            active=" active" if self.active else "",
            aria_current=(
                SafeString(" aria-current='true'") if self.active else ""
            ),
        )


class TableOrderings(Generic[M]):
    """All available table ordering options."""

    def __init__(self, table: "Table[M]") -> None:
        """Orderings for a table."""
        self.table = table
        #: Query string field name used to choose the table ordering
        self.field_name = self.table.prefix + "order"
        #: ID for this element in the DOM
        self.dom_id = self.table.dom_id + "orderings"
        #: Available sort options
        self.options: list[TableOrdering[M]] = []
        #: Active sort option
        self.current: TableOrdering[M]

    def finalize(self) -> None:
        """Finish setting up after the orderings definition is complete."""
        if not self.options:
            return
        # Resolve the default ordering
        default_ordering = self._find(self.table.default_order)
        if default_ordering is None:
            raise ImproperlyConfigured(
                f"default_order {self.table.default_order!r}"
                " does not match available ordering options"
            )

        # Resolve the requested ordering and pick the current one
        requested = self.table.request.GET.get(self.field_name)
        if requested is None:
            self.current = default_ordering
        else:
            self.current = self._find(requested) or default_ordering
        self.current.active = True

    def add(self, what: str, ordering: Ordering) -> list[TableOrdering[M]]:
        """Add an ordering definition."""
        added: list[TableOrdering[M]] = []
        query = self.table.request.GET.copy()
        for idx, (how, sort) in enumerate(ordering.options.items()):
            query[self.field_name] = f"{what}.{how}"
            table_ordering = TableOrdering(
                orderings=self,
                sort=sort,
                what=what,
                how=how,
                label_what=ordering.label or pretty_name(what),
                query_string=query.urlencode(),
            )
            added.append(table_ordering)

        self.options += added
        return added

    def _find(self, value: str) -> TableOrdering[M] | None:
        """
        Find an ordering by what and how.

        :param what: ``what`` argument to match
        :param how: if None, returns the first entry for the given ``what``
        """
        what, how = self._parse_order(value)
        for to in self.options:
            if what != to.what:
                continue
            if how is None or how == to.how:
                return to
        return None

    def _parse_order(self, value: str) -> tuple[str, str | None]:
        """Resolve an order definition into a (what, how) tuple."""
        if value.startswith("-"):
            return value[1:], "desc"
        elif "." not in value:
            return value, None
        else:
            what, how = value.split(".", 1)
            return what, how

    @cached_property
    def what_menu_entries(self) -> list[SafeString]:
        """Return a list of menu entries for a what menu."""
        what_options: dict[str, TableOrdering[M]] = {}
        for o in self.options:
            if o.what in what_options:
                continue
            what_options[o.what] = o

        items: list[SafeString] = []
        for o in what_options.values():
            active = self.current.what == o.what
            items.append(
                format_html(
                    "<a class='dropdown-item{active}' href='?{query_string}'"
                    "{aria_current}>{label}</a>",
                    label=o.label_what,
                    query_string=o.query_string,
                    active=" active" if active else "",
                    aria_current=(
                        SafeString(" aria-current='true'") if active else ""
                    ),
                )
            )
        return items

    @cached_property
    def how_menu_entries(self) -> list[SafeString]:
        """Return a list of menu entries for a how menu for the current what."""
        hows = [o for o in self.options if o.what == self.current.what]
        items: list[SafeString] = []
        for o in hows:
            active = self.current == o
            items.append(
                format_html(
                    "<a class='dropdown-item{active}' href='?{query_string}'"
                    "{aria_current}>{label}</a>",
                    label=o.sort.label,
                    query_string=o.query_string,
                    active=" active" if active else "",
                    aria_current=(
                        SafeString(" aria-current='true'") if active else ""
                    ),
                )
            )
        return items

    @cached_property
    def how_sort_icon(self) -> SafeString:
        """Return a sort icon for the current hows."""
        hows = [o for o in self.options if o.what == self.current.what]
        return render_sort_icon(hows, for_table=True)

    def order_queryset(self, queryset: QuerySet[M, M]) -> QuerySet[M, M]:
        """Order a queryset."""
        return queryset.order_by(*self.current.sort.order_by)
