# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tabular presentation of QuerySets."""

from functools import cached_property
from typing import Any, ClassVar, Generic, TYPE_CHECKING, TypeVar, cast

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model, QuerySet
from django.forms import Form
from django.http import HttpRequest

from debusine.utils import extract_generic_type_arguments
from debusine.web.forms import ModelFormBase
from debusine.web.views.table.columns import BoundColumn, Column, Columns
from debusine.web.views.table.declarative import Attribute
from debusine.web.views.table.filters import Filter, Filters
from debusine.web.views.table.ordering import Ordering, TableOrderings

if TYPE_CHECKING:
    from django.core.paginator import _SupportsPagination

    from debusine.web.views.table.paginator import Paginator

    _SupportsPagination  # fake usage for vulture

M = TypeVar("M", bound=Model)
Attr = TypeVar("Attr", bound=Attribute)


class Table(Generic[M]):
    """Definition of a tabular presentation of a QuerySet."""

    #: Model class
    model: type[M]

    #: Columns declaratively defined in subclass definitions,
    #: collected by __init_subclass__
    column_definitions: ClassVar[dict[str, Column]]

    #: Orderings declaratively defined in subclass definitions,
    #: collected by __init_subclass__
    ordering_definitions: ClassVar[dict[str, Ordering]]

    #: Filters declaratively defined in subclass definitions,
    #: collected by __init_subclass__
    filter_definitions: ClassVar[dict[str, Filter]]

    #: Form class for a form to use for filtering results
    filter_form_class: type[Form] | type[ModelFormBase[M]] | None = None

    #: Default sorting for the table
    default_order: str

    #: Template name to use to render the table as a widget
    template_name: str | None = None

    #: The column selected for sorting
    current_column: BoundColumn[M]

    @classmethod
    def _collect_declarative(
        cls,
        attr_class: type[Attr],
        collected_name: str,
        *,
        prefix: str | None = None,
    ) -> None:
        """
        Collect declaratively defined members.

        :param attr_class: members need to be an instance of this class
        :param collected_name: class member with a dictionary of collected
                               members, indexed by name
        :param prefix: if defined, enforce that member names begin with this
                       prefix. This only applies to names used for class
                       members: names defined inside member instances can be
                       anything
        """
        # We cannot use inspect.getmembers as it sorts results by name, and we
        # need to preserve the order of definitions

        definitions: dict[str, Attr] = {}

        # Add definitions from base classes
        for base in cls.__bases__:
            if not issubclass(base, Table):
                continue
            definitions.update(getattr(base, collected_name, {}))

        # Add definitions from class dict
        for name, member in cls.__dict__.items():
            if not isinstance(member, attr_class):
                continue

            # Enforce member prefix
            if prefix is not None:
                if not name.startswith(prefix):
                    raise ImproperlyConfigured(
                        f"{name} = {member.__class__.__name__}(…):"
                        f" assigned name does not start with {prefix!r}"
                    )
                name = name.removeprefix(prefix)

            # Set the default name
            if not hasattr(member, "name"):
                member.name = name

            # Store the definition using the member's name
            definitions[member.name] = member

        # Store the collected members
        setattr(cls, collected_name, definitions)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Collect column and filter definitions."""
        super().__init_subclass__(**kwargs)
        [cls.model] = extract_generic_type_arguments(cls, Table)

        cls._collect_declarative(Column, "column_definitions")
        # Mypy disallow using type[T] where T is an abstract class.
        # However, here we are not instantiating, only using it for isinstance
        # checks, so we silence the warning.
        # See https://github.com/python/mypy/issues/4717
        cls._collect_declarative(
            Filter,  # type: ignore[type-abstract]
            "filter_definitions",
            prefix="filter_",
        )
        cls._collect_declarative(
            Ordering,
            "ordering_definitions",
            prefix="ordering_",
        )

    def __init__(
        self,
        request: HttpRequest,
        object_list: "_SupportsPagination[M]",
        *,
        prefix: str = "",
        default_order: str | None = None,
    ) -> None:
        """
        Build the table definition.

        :param request: the current request object
        :param object_list: the queryset to present
        :param prefix: the query string prefix for paginator arguments (set
                       this if you paginate multiple object lists in the same
                       page)
        :param default_order: name of the default column to use for sorting.
                               Prefix with '-' to make it descending. Use
                               ``colname.sortname`` to specify the sort option
                               by name.
        """
        #: Current request
        self.request = request

        if not isinstance(object_list, QuerySet):
            raise ImproperlyConfigured(
                "Table cannot work with an object_list"
                f" of class {object_list.__class__.__name__},"
                " only with QuerySets"
            )
        #: Queryset to filter
        self.queryset: QuerySet[M, M] = cast(QuerySet[M, M], object_list)

        #: Query string argument prefix
        self.prefix = f"{prefix}-" if prefix else ""

        # Override definition default with user-provided column name
        if default_order is not None:
            self.default_order = default_order

        #: Column definitions
        self.columns: Columns[M] = Columns(self)

        #: Filters
        self.filters: Filters[M] = Filters(self)

        #: Orderings
        self.orderings: TableOrderings[M] = TableOrderings(self)

        # Allow subclasses to programmatically add to the table definition
        self.init()

        self._post_init()

    def init(self) -> None:
        """
        Set up Table attributes.

        This function does nothing by default, and can be overridden by
        subclasses to customize the paginator for their specific use.
        """
        # Instantiate declaratively defined columns
        for name, column in self.column_definitions.items():
            self.add_column(name, column)

        # Instantiate declaratively defined orderings
        for name, ordering in self.ordering_definitions.items():
            self.add_ordering(name, ordering)

        # Instantiate declaratively defined filters
        for filter_ in self.filter_definitions.values():
            self.add_filter(filter_)

    def add_column(self, name: str, column: Column) -> None:
        """Add a column to the table."""
        bc = self.columns.add(name, column)
        if column.ordering:
            column.ordering.name = name
            added = self.orderings.add(column.name, column.ordering)
            bc.orderings += added

    def add_ordering(self, name: str, ordering: Ordering) -> None:
        """Add an ordering to the table."""
        self.orderings.add(name, ordering)

    def add_filter(self, filter_: Filter) -> None:
        """Add a filter to the table."""
        self.filters.add(filter_)

    def _post_init(self) -> None:
        """Finish setting up after the table definition is complete."""
        if (
            self.orderings.options
            and getattr(self, "default_order", None) is None
        ):
            raise ImproperlyConfigured(
                "table is sortable,"
                " but default_order not set in class or constructor arguments"
            )
        self.orderings.finalize()

    def get_paginator(
        self,
        per_page: int,
        *,
        orphans: int = 0,
        allow_empty_first_page: bool = True,
    ) -> "Paginator[M]":
        """Return the Paginator for this table."""
        from debusine.web.views.table.paginator import Paginator

        return Paginator(
            self,
            per_page=per_page,
            orphans=orphans,
            allow_empty_first_page=allow_empty_first_page,
        )

    @cached_property
    def rows(self) -> QuerySet[M, M]:
        """Return the queryset sorted as the user requested."""
        queryset = self.filters.filter_queryset(self.queryset)
        if self.orderings.options:
            return self.orderings.order_queryset(queryset)
        else:
            return queryset
