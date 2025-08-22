# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Table column filtering."""

import abc
from collections.abc import Collection
from functools import cached_property
from typing import Any, Generic, NamedTuple, TYPE_CHECKING, TypeVar

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model, Q, QuerySet
from django.forms import Form, modelform_factory
from django.forms.boundfield import BoundField
from django.http import QueryDict
from django.template.context import Context
from django.utils.html import format_html
from django.utils.safestring import SafeString

from debusine.web.forms import ModelFormBase
from debusine.web.views.base import Widget
from debusine.web.views.table.declarative import Attribute

if TYPE_CHECKING:
    from debusine.web.views.table.table import Table


M = TypeVar("M", bound=Model)


class FilterWidget(Widget):
    """Base class for menu entry widgets."""

    def __init__(self, filter_: "BoundFilter[Any]") -> None:
        """Store a reference to the bound filter."""
        self.filter = filter_


class MenuEntry(FilterWidget):
    """Standard menu entry, activating the filter form."""

    def render(self, context: Context) -> str:  # noqa: U100
        """Render the filter as widget."""
        return format_html(
            "<a class='dropdown-item debusine-dropdown-remote'"
            " aria-expanded='false'"
            " data-bs-target='#{dom_id_form_dropdown}'>"
            "{label}</a>",
            dom_id_form_dropdown=self.filter.dom_id_form_dropdown,
            label=self.filter.label,
        )


class MenuEntryToggle(FilterWidget):
    """Menu entry that toggles the filter value."""

    def render(self, context: Context) -> str:  # noqa: U100
        """Render the filter as widget."""
        if self.filter.value:
            url = self.filter.qs_remove
        else:
            url = self.filter.qs_with_value(True)
        return format_html(
            "<a class='dropdown-item{active}'"
            " href='?{url}' {aria_current}>"
            "{label}</a>",
            url=url,
            label=self.filter.label,
            active=" active" if self.filter.value else "",
            aria_current=(
                SafeString(" aria-current='true'") if self.filter.value else ""
            ),
        )


class MainEntry(FilterWidget):
    """Render the element and optional dropdown form for a filter."""

    template_name: str

    def get_context_data(self) -> dict[str, Any]:
        """Get context for widget rendering."""
        return {"filter": self.filter, "table": self.filter.filters.table}

    def render(self, context: Context) -> str:
        """Render the filter as widget."""
        assert context.template is not None
        template = context.template.engine.get_template(self.template_name)
        with context.update(self.get_context_data()):
            return template.render(context)


class MainEntryForm(MainEntry):
    """Render input forms."""

    template_name = "web/_table_filter_form.html"
    form_template_name: str

    def get_context_data(self) -> dict[str, Any]:
        """Get context for widget rendering."""
        ctx = super().get_context_data()
        ctx["form_template_name"] = self.form_template_name
        return ctx


class MainEntryText(MainEntryForm):
    """Render the input field for a FilterText."""

    form_template_name = "web/_table_filter_text.html"


class MainEntrySelectOne(MainEntryForm):
    """Render the input field for a FilterSelectOne."""

    form_template_name = "web/_table_filter_select_one.html"


class MainEntryField(MainEntryForm):
    """Render the input field for a FilterField."""

    form_template_name = "web/_table_filter_field.html"


class MainEntryToggle(MainEntry):
    """Render the input field for a FilterToggle."""

    template_name = "web/_table_filter_toggle.html"


class BoundFilter(Generic[M]):
    """A Filter bound to a table."""

    dom_id: str
    dom_id_form_dropdown: str

    def __init__(
        self,
        filter_: "Filter",
        filters: "Filters[M]",
    ) -> None:
        """Build from filter and table filters definition."""
        self.filter = filter_
        self.filters = filters
        #: ID for this element in the DOM
        self.dom_id = self.filters.table.dom_id + f"filter-{self.name}"
        self.dom_id_form_dropdown = self.dom_id + "-form-dropdown"

    @cached_property
    def filter_prefix(self) -> str:
        """Compute the filter prefix."""
        return f"{self.filters.filter_prefix}-{self.name}"

    @property
    def name(self) -> str:
        """Return the filter name."""
        return self.filter.name

    @property
    def label(self) -> str:
        """Return the filter label."""
        return self.filter.label

    @property
    def icon(self) -> str | None:
        """Return the filter icon, if defined."""
        return self.filter.icon

    @cached_property
    def value(self) -> Any:
        """Return the current value for the filter."""
        return self.filters.table.request.GET.get(self.filter_prefix) or ""

    @cached_property
    def active(self) -> bool:
        """Check if the filter is active."""
        return bool(self.value)

    def _format_value(self) -> str:
        """Return a pretty printed value."""
        match value := self.value:
            case None:
                return ""
            case str():
                return value
            case Collection():
                return ", ".join(value)
            case _:
                return str(value)

    @cached_property
    def value_formatted(self) -> str:
        """Return a pretty printed value."""
        return self._format_value()

    def value_for_querystring(self, value: Any) -> list[str]:
        """
        Convert the current value to a list of strings for use in a querystring.

        Return an empty list if the given value means the filter is disabled.
        """
        match value:
            case True:
                return ["1"]
            case None | False:
                return []
            case str():
                return [value]
            case Collection():
                return [str(v) for v in value]
            case _:
                return [str(value)]

    def qs_with_value(self, value: Any) -> str:
        """Get a query string setting the field value to a given value."""
        base_args = QueryDict(mutable=True)
        for key, values in self.filters.table.request.GET.lists():
            if key.startswith(self.filter_prefix):
                continue
            base_args.setlist(key, values)
        if v := self.value_for_querystring(value):
            base_args.setlist(self.filter_prefix, v)
        return base_args.urlencode()

    @cached_property
    def qs_remove(self) -> str:
        """Query string that can be used to remove this filter."""
        base_args = QueryDict(mutable=True)
        for key, values in self.filters.table.request.GET.lists():
            if key.startswith(self.filter_prefix):
                continue
            base_args.setlist(key, values)
        return base_args.urlencode()

    @cached_property
    def base_filter_args(self) -> list[tuple[str, str]]:
        """Form arguments for all aspects of this table except this filter."""
        base_arglist: list[tuple[str, str]] = []
        for key, values in self.filters.table.request.GET.lists():
            if key.startswith(self.filter_prefix):
                continue
            for value in values:
                base_arglist.append((key, value))
        return base_arglist

    def add_option(
        self,
        name: str,  # noqa: U100
        label: str,  # noqa: U100
        query: Q,  # noqa: U100
        icon: str | None = None,  # noqa: U100
    ) -> None:
        """Add an option to the selection list."""
        raise ImproperlyConfigured(
            "add_option called on a field that does not support options."
        )

    def filter_queryset(self, queryset: QuerySet[M, M]) -> QuerySet[M, M]:
        """Filter the queryset."""
        return self.filter.filter_queryset(queryset, self.value)

    @cached_property
    def main_entry(self) -> FilterWidget:
        """Return the label to use to show the active filter."""
        return self.filter.main_entry(self)

    @cached_property
    def menu_entry(self) -> FilterWidget:
        """Return the widget to render our entry in the filter menu."""
        return self.filter.menu_entry(self)


class Filter(Attribute, abc.ABC):
    """A way of filtering a table."""

    label: str
    icon: str | None
    bound_class: type[BoundFilter[Any]] = BoundFilter[Any]
    menu_entry: type[FilterWidget] = MenuEntry
    main_entry: type[FilterWidget]

    def __init__(
        self,
        label: str,
        *,
        name: str | None = None,
        icon: str | None = None,
    ) -> None:
        """Store filter definition."""
        super().__init__(name=name)
        self.label = label
        self.icon = icon

    @abc.abstractmethod
    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any
    ) -> QuerySet[M, M]:
        """Filter the queryset using the given filter value."""


class FilterText(Filter):
    """A filter with a text field."""

    main_entry = MainEntryText
    placeholder: str | None

    def __init__(
        self,
        label: str,
        *,
        q_field: str | None = None,
        placeholder: str | None = None,
        icon: str | None = None,
    ) -> None:
        """
        Store filter definition.

        :param q_field: field name to use for matching in Q expressions.
                        Defaults to ``"{self.name}__contains"``
        :param placeholder: Placeholder string to show in the text field
        """
        super().__init__(label, icon=icon)
        self._q_field = q_field
        self.placeholder = placeholder

    @cached_property
    def q_field(self) -> str:
        """Return the field name used for matching in Q expressions."""
        return self._q_field or f"{self.name}__contains"

    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any
    ) -> QuerySet[M, M]:
        """Filter a queryset."""
        return queryset.filter(**{self.q_field: value})


class FilterToggle(Filter):
    """A filter that can be toggled on or off."""

    menu_entry: type[FilterWidget] = MenuEntryToggle
    main_entry: type[FilterWidget] = MainEntryToggle

    def __init__(
        self,
        label: str,
        *,
        q: Q,
        icon: str | None = None,
    ) -> None:
        """
        Store filter definition.

        :param q: Q expression to use for filtering
        """
        super().__init__(label, icon=icon)
        self.q = q

    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any
    ) -> QuerySet[M, M]:
        """Filter a queryset."""
        if not value:
            return queryset
        return queryset.filter(self.q)


class SelectOption(NamedTuple):
    """One option in a select/multiselect filter."""

    name: str
    label: str
    query: Q
    icon: str | None = None


class BoundFilterSelectOne(BoundFilter[M]):
    """Filter specialization handling a set of selectable values."""

    def __init__(
        self,
        filter_: "FilterSelectOne",
        filters: "Filters[M]",
    ) -> None:
        """Build from filter and table filters definition."""
        super().__init__(filter_, filters)
        self.options: dict[str, SelectOption] = {}
        for option in filter_.options:
            self.add(option)

    def add(self, option: SelectOption) -> None:
        """Add an option to the selection list."""
        self.options[option.name] = option

    def add_option(
        self, name: str, label: str, query: Q, icon: str | None = None
    ) -> None:
        """Add an option to the selection list."""
        self.add(SelectOption(name=name, label=label, query=query, icon=icon))

    def filter_queryset(self, queryset: QuerySet[M, M]) -> QuerySet[M, M]:
        """Filter the queryset."""
        if option := self.options.get(self.value):
            return queryset.filter(option.query)
        else:
            return queryset


class FilterSelectOne(Filter):
    """Filter selecting one item among a finite set."""

    bound_class = BoundFilterSelectOne
    main_entry = MainEntrySelectOne

    #: Options to select from
    options: Collection[SelectOption]

    def __init__(
        self,
        label: str,
        *,
        icon: str | None = None,
        options: Collection[SelectOption] = (),
    ) -> None:
        """
        Store filter definition.

        :param options: Collection of ``SelectOption`` elements describing the
                        options the user can choose from. Defaults to empty.
        """
        super().__init__(label, icon=icon)
        self.options = options

    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any  # noqa: U100
    ) -> QuerySet[M, M]:
        """Filter a queryset."""
        raise ImproperlyConfigured(
            "FilterSelectOne should have bound_class"
            " set to a BoundFilterSelectOne"
        )


class BoundFilterField(BoundFilter[M]):
    """Filter specialization using a Django Form field."""

    @cached_property
    def value(self) -> Any:
        """Return the current value for the filter."""
        return self.filters.form.cleaned_data.get(self.name)

    def _format_value(self) -> str:
        """Return a pretty printed value."""
        if self.value is None:
            return ""

        # If we are using a choice field, get its display value
        if choices := getattr(self.form_field.field, "choices", None):
            values: Collection[str] = (
                [self.value] if isinstance(self.value, str) else self.value
            )
            labels: list[str] = []
            for k, v in choices:
                if isinstance(v, (list, tuple)):
                    # This is an optgroup, so look inside the group for options
                    for k2, v2 in v:
                        if k2 in values:
                            labels.append(v2)
                else:
                    if k in values:
                        labels.append(v)
            if labels:
                return ", ".join(labels)

        return super()._format_value()

    @cached_property
    def form_field(self) -> BoundField:
        """Return the bound Django form field for this filter."""
        try:
            return self.filters.form[self.name]
        except KeyError:
            available = repr(sorted(self.filters.form.fields.keys()))
            raise ImproperlyConfigured(
                f"{self.name!r} not found"
                f" in table filter form {self.filters.form.__class__.__name__}."
                f" Available fields: {available}"
            )

    @cached_property
    def filter_prefix(self) -> str:
        """Compute the filter prefix."""
        return self.form_field.html_name

    @property
    def label(self) -> str:
        """Return the filter label."""
        return self.filter.label or str(self.form_field.label)


class FilterField(Filter):
    """Table filter using a Django form field."""

    bound_class = BoundFilterField
    main_entry = MainEntryField

    def __init__(
        self,
        label: str = "",
        icon: str | None = None,
        q_field: str | None = None,
    ) -> None:
        """Store filter definition."""
        super().__init__(label, icon=icon)
        self._q_field = q_field

    @cached_property
    def q_field(self) -> str:
        """Return the field name used for matching in Q expressions."""
        return self._q_field or f"{self.name}__contains"

    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any
    ) -> QuerySet[M, M]:
        """Filter a queryset."""
        return queryset.filter(**{self.q_field: value})


class Filters(Widget, Generic[M]):
    """Filter definitions for a table."""

    template_name: str = "web/_table_filters.html"

    def __init__(self, table: "Table[M]") -> None:
        """Instantiate for the given table."""
        self.table = table

        #: Prefix for filter query string arguments for this table
        self.filter_prefix: str = f"{self.table.prefix}filter"

        #: ID for this element in the DOM
        self.dom_id = self.table.dom_id + "filters"

        #: ID for the filters menu in the DOM
        self.dom_id_filters_menu = self.dom_id + "-menu"

        #: Filters configured for the table
        self.options: dict[str, BoundFilter[M]] = {}

        # Set up query string related members
        query = QueryDict(mutable=True)
        query_items: list[tuple[str, str]] = []
        for key, values in self.table.request.GET.lists():
            if key.startswith(self.filter_prefix):
                continue
            query.setlist(key, values)
            for value in values:
                query_items.append((key, value))

        #: Query string clearing all filters for this table
        self.qs_clear_filters = query.urlencode()
        self.non_filter_args = query_items

    def __bool__(self) -> bool:
        """Return True if there are filters defined."""
        return bool(self.options)

    @cached_property
    def available(self) -> Collection[BoundFilter[M]]:
        """Return all available filters."""
        return self.options.values()

    def __getitem__(self, name: str) -> BoundFilter[M]:
        """Get a filter by name."""
        return self.options[name]

    @cached_property
    def active(self) -> Collection[BoundFilter[M]]:
        """Return all active filters."""
        return [v for v in self.available if v.active]

    @cached_property
    def inactive(self) -> Collection[BoundFilter[M]]:
        """Return all active filters."""
        return [v for v in self.available if not v.active]

    @cached_property
    def form(self) -> Form | ModelFormBase[M]:
        """
        Form to use for table filter input.

        If none is specified, one is automatically inferred using django
        modelforms.
        """
        form_class: type[Form] | type[ModelFormBase[M]]
        if self.table.filter_form_class:
            form_class = self.table.filter_form_class
        else:

            class Base(ModelFormBase[M]):
                """ModelForm base that allows empty and existing values."""

                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    """Override ModelForm defaults."""
                    kwargs["empty_permitted"] = True
                    kwargs["use_required_attribute"] = False
                    super().__init__(*args, **kwargs)

                def validate_unique(self) -> None:
                    """Disable unique validation."""
                    pass

            # Infer the list of fields from the list of FilterField entries
            field_names: list[str] = []
            for name, flt in self.options.items():
                if not isinstance(flt.filter, FilterField):
                    continue
                field_names.append(name)
            form_class = modelform_factory(
                self.table.model,
                form=Base,
                fields=field_names,
            )
        form = form_class(self.table.request.GET, prefix=self.filter_prefix)
        form.is_valid()
        return form

    def add(self, filter_: Filter) -> None:
        """Add a new filter."""
        self.options[filter_.name] = filter_.bound_class(filter_, self)

    def filter_queryset(self, queryset: QuerySet[M, M]) -> QuerySet[M, M]:
        """Filter the queryset using the active filters."""
        for f in self.options.values():
            if f.active:
                queryset = f.filter_queryset(queryset)
        return queryset

    def get_context_data(self) -> dict[str, Any]:
        """Get context for widget rendering."""
        ctx = {
            "filters": self,
        }
        return ctx

    def render(self, context: Context) -> str:
        """Render a form to configure the filter."""
        assert context.template is not None
        template = context.template.engine.get_template(self.template_name)
        with context.update(self.get_context_data()):
            return template.render(context)
