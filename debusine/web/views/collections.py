# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine collection views."""

from functools import cached_property
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.http import Http404

from debusine.db.context import context
from debusine.db.models import Collection, CollectionItem
from debusine.web.forms import CollectionSearchForm
from debusine.web.views import sidebar, ui_shortcuts
from debusine.web.views.base import (
    DetailViewBase,
    FormMixinBase,
    ListViewBase,
    WorkspaceView,
)
from debusine.web.views.mixins import RightbarUIMixin, UIShortcutsMixin
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import CollectionItemTable
from debusine.web.views.view_utils import format_yaml


class CollectionListView(WorkspaceView, ListViewBase[Collection]):
    """List collections."""

    model = Collection
    template_name = "web/collection-list.html"
    context_object_name = "collection_list"
    ordering = ["category", "name"]

    def get_title(self) -> str:
        """Return the page title."""
        return f"{context.require_workspace().name} collections"

    def get_queryset(self) -> QuerySet[Collection]:
        """Filter collection by accessible workspace."""
        queryset = super().get_queryset()
        return queryset.filter(workspace=context.workspace).exclude(
            category="debusine:workflow-internal"
        )


class CollectionCategoryListView(WorkspaceView, ListViewBase[Collection]):
    """List collections with a given category."""

    model = Collection
    template_name = "web/collection-category-list.html"
    context_object_name = "collection_list"
    ordering = ["name"]

    def get_title(self) -> str:
        """Return the page title."""
        return (
            f"{context.require_workspace().name}"
            f" {self.kwargs['ccat']} collections"
        )

    def get_queryset(self) -> QuerySet[Collection]:
        """Filter collection by accessible workspace."""
        queryset = super().get_queryset()
        return (
            queryset.filter(workspace=context.workspace)
            .exclude(category="debusine:workflow-internal")
            .filter(category=self.kwargs["ccat"])
        )

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with category."""
        context = super().get_context_data(**kwargs)
        context["category"] = self.kwargs["ccat"]
        return context


class CollectionViewMixin(WorkspaceView, UIShortcutsMixin, RightbarUIMixin):
    """Common functions for collection-specific views."""

    @cached_property
    def collection(self) -> Collection:
        """Collection for this request."""
        try:
            return Collection.objects.get(
                workspace=context.workspace,
                name=self.kwargs["cname"],
                category=self.kwargs["ccat"],
            )
        except Collection.DoesNotExist:
            raise Http404(
                f"{self.kwargs['cname']}@{self.kwargs['ccat']}"
                " collection not found"
            )

    def get_sidebar_items(self) -> list[sidebar.SidebarItem]:
        """Return a list of sidebar items."""
        items = super().get_sidebar_items()
        items.append(sidebar.create_collection(self.collection))
        assert context.workspace is not None
        items.append(sidebar.create_workspace(context.workspace))
        return items

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with collection."""
        context = super().get_context_data(**kwargs)
        context["collection"] = self.collection
        return context


class CollectionDetailView(CollectionViewMixin, DetailViewBase[Collection]):
    """Show a collection detail."""

    model = Collection
    template_name = "web/collection-detail.html"
    context_object_name = "collection"

    def get_object(
        self, queryset: QuerySet[Collection] | None = None  # noqa: U100
    ) -> Collection:
        """Return the collection object to show."""
        return self.collection

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with work_request_list and workspace_list."""
        context = super().get_context_data(**kwargs)
        if self.object.data:
            context["data"] = format_yaml(self.object.data)

        context["collections"] = (
            CollectionItem.objects.active()
            .filter(parent_collection=self.object, collection__isnull=False)
            .order_by("category", "name")
        )

        context["artifacts"] = (
            CollectionItem.objects.filter(
                parent_collection=self.object, artifact__isnull=False
            )
            .values("category")
            .annotate(
                count=Count("category", filter=Q(removed_at__isnull=True)),
                count_removed=Count(
                    "category", filter=Q(removed_at__isnull=False)
                ),
            )
            .order_by("category")
        )

        context["bare"] = (
            CollectionItem.objects.active()
            .filter(
                parent_collection=self.object,
                collection__isnull=True,
                artifact__isnull=True,
            )
            .order_by("category", "name")
        )

        return context


class CollectionSearchView(
    TableMixin[CollectionItem],
    CollectionViewMixin,
    FormMixinBase[CollectionSearchForm],
    ListViewBase[CollectionItem],
):
    """Search a collection contents."""

    model = CollectionItem
    template_name = "web/collection-search.html"
    context_object_name = "item_list"
    form_class = CollectionSearchForm
    table_class = CollectionItemTable
    paginate_by = 50

    def get_queryset(self) -> QuerySet[CollectionItem]:
        """Filter items based on the search form."""
        queryset = (
            super().get_queryset().filter(parent_collection=self.collection)
        )
        queryset = queryset.select_related(
            "parent_collection__workspace__scope",
            "collection",
            "artifact__workspace__scope",
        )
        # FIXME: this builds the form twice: if it becomes a problem we can
        # override get_form to cache its result
        form = self.get_form()
        if form.is_valid():
            if name := form.cleaned_data["name"]:
                queryset = queryset.filter(name__startswith=name)
            if category := form.cleaned_data["category"]:
                queryset = queryset.filter(category=category)
            if form.cleaned_data["historical"]:
                queryset = queryset.filter(removed_at__isnull=False)
            else:
                queryset = queryset.filter(removed_at__isnull=True)
        else:
            queryset = queryset.none()
        return queryset

    def get_form_kwargs(self) -> dict[str, Any]:
        """Get arguments used to instantiate the form."""
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.collection
        kwargs["data"] = self.request.GET
        return kwargs

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with work_request_list and workspace_list."""
        context = super().get_context_data(**kwargs)
        context["collection"] = self.collection
        # TODO: we need to match model objects exactly in order to be able to
        # lookup UI shortcuts, and this is a hack to do that, and also avoid to
        # iterate queryset twice.
        # See !1540 for a possible way to avoid this hack
        page_obj = context["paginator"].page_obj
        page_obj.object_list = list(page_obj.object_list)
        for item in page_obj.object_list:
            self.add_object_ui_shortcuts(
                item, ui_shortcuts.create_collection_item(item)
            )
            if item.artifact is not None:
                self.add_object_ui_shortcuts(
                    item,
                    ui_shortcuts.create_artifact_view(item.artifact),
                    ui_shortcuts.create_artifact_download(item.artifact),
                )
        return context


class CollectionItemDetailView(
    CollectionViewMixin, DetailViewBase[CollectionItem]
):
    """Show a collection item detail."""

    model = CollectionItem
    template_name = "web/collection-item-detail.html"
    context_object_name = "item"

    def get_title(self) -> str:
        """Return page title."""
        return self.object.name

    def get_sidebar_items(self) -> list[sidebar.SidebarItem]:
        """Return a list of sidebar items."""
        items = super().get_sidebar_items()
        # TODO: create collection-specific or artifact-specific UI shortcuts as
        # needed
        items.append(
            sidebar.create_user(
                self.object.created_by_user, context=self.object
            )
        )
        items.append(sidebar.create_created_at(self.object.created_at))
        return items

    def get_object(
        self, queryset: QuerySet[CollectionItem] | None = None
    ) -> CollectionItem:
        """Return the collection object to show."""
        assert queryset is None
        queryset = self.get_queryset()
        try:
            return queryset.filter(removed_at__isnull=True).get(
                pk=self.kwargs["iid"], parent_collection=self.collection
            )
        except CollectionItem.DoesNotExist:
            raise Http404(
                f"{self.kwargs['iid']} ({self.kwargs['iname']})"
                " item not found in "
                f"{self.kwargs['cname']}@{self.kwargs['ccat']}"
            )

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context_data."""
        context = super().get_context_data(**kwargs)
        if self.object.data:
            context["data"] = format_yaml(self.object.data)
        return context
