# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine workspace views."""

import itertools
from typing import Any, cast

from django.db.models import Count, QuerySet

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineTaskConfiguration,
)
from debusine.db.context import context
from debusine.db.models import Collection, WorkflowTemplate, Workspace
from debusine.db.models.workspaces import WorkspaceChain
from debusine.server.collections.debusine_task_configuration import (
    build_configuration,
    list_configuration,
)
from debusine.web.forms import TaskConfigurationInspectorForm, WorkspaceForm
from debusine.web.views.base import (
    DetailViewBase,
    UpdateViewBase,
    WorkspaceView,
)


class WorkspaceDetailView(WorkspaceView, DetailViewBase[Workspace]):
    """Show a workspace detail."""

    model = Workspace
    template_name = "web/workspace-detail.html"
    context_object_name = "workspace"

    def get_object(
        self, queryset: QuerySet[Workspace] | None = None  # noqa: U100
    ) -> Workspace:
        """Return the workspace object to show."""
        # Enforced by WorkspaceView.init()
        assert context.workspace is not None
        return context.workspace

    def get_title(self) -> str:
        """Return the page title."""
        return f"Workspace {self.object.name}"

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context for this view."""
        ctx = super().get_context_data(*args, **kwargs)

        ctx["parents"] = [
            x.parent
            for x in WorkspaceChain.objects.filter(child=self.object)
            .order_by("order")
            .select_related("parent")
            if x.parent.can_display(self.request.user)
        ]
        ctx["workflow_templates"] = (
            WorkflowTemplate.objects.can_display(context.user)
            .filter(workspace=self.object)
            .order_by("name")
        )

        # Collections grouped by category
        stats = list(
            Collection.objects.filter(workspace=self.object)
            .exclude(category="debusine:workflow-internal")
            .values("category")
            .annotate(count=Count("category"))
            .order_by("category")
        )
        ctx["collection_stats"] = stats
        ctx["collection_count"] = sum(s["count"] for s in stats)

        return ctx


class WorkspaceUpdateView(
    WorkspaceView, UpdateViewBase[Workspace, WorkspaceForm]
):
    """Configure a workspace."""

    form_class = WorkspaceForm
    template_name = "web/workspace-update.html"

    def init_view(self) -> None:
        """Set the current workspace."""
        super().init_view()
        # Enforced by WorkspaceView.init()
        assert context.workspace is not None
        self.enforce(context.workspace.can_configure)

    def get_object(
        self, queryset: QuerySet[Workspace] | None = None  # noqa: U100
    ) -> Workspace:
        """Return the workspace object to show."""
        # Enforced by WorkspaceView.init()
        assert context.workspace is not None
        return context.workspace

    def get_title(self) -> str:
        """Return the page title."""
        return f"Configure workspace {self.object.name}"


class TaskConfigInspectorView(WorkspaceView, DetailViewBase[Workspace]):
    """Look up task configuration items."""

    model = Workspace
    template_name = "web/task-configuration-inspector.html"
    context_object_name = "workspace"

    def get_title(self) -> str:
        """Get the page title."""
        return f"Task configuration inspector for {self.object.name} workspace"

    def get_object(
        self, queryset: QuerySet[Workspace] | None = None  # noqa: U100
    ) -> Workspace:
        """Return the workspace object to show."""
        # Enforced by WorkspaceView.init()
        assert context.workspace is not None
        return context.workspace

    def get_collections(self) -> list[Collection]:
        """Return all accessible task-configuration collections."""
        return list(
            self.object.list_accessible_collections(
                self.request.user, CollectionCategory.TASK_CONFIGURATION
            )
        )

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return context data for the view."""
        ctx = super().get_context_data(**kwargs)

        collections = self.get_collections()
        ctx["collections"] = collections

        # Pick the collection to inspect, if possible
        collection: Collection | None = None
        if (collection_id := self.request.GET.get("collection")) is None:
            if len(collections) == 1:
                collection = collections[0]
        else:
            # Without the cast, we get:
            # > Incompatible types in assignment
            # > (expression has type "A", variable has type "Collection | None")
            collection = cast(
                Collection,
                Collection.objects.can_display(self.request.user)
                .select_related("workspace")
                .get(pk=collection_id),
            )

        if collection is not None:
            item_count = collection.child_items.filter(
                category=BareDataCategory.TASK_CONFIGURATION,
            ).count()
            ctx["item_count"] = item_count

            if item_count > 0:
                form = TaskConfigurationInspectorForm(
                    self.request.GET, collection=collection
                )
                if form.is_valid():
                    ctx["has_result"] = True

                    task_type, task_name = form.cleaned_data["task"].split(
                        ":", 1
                    )
                    # Look up debusine:task-configuration items
                    lookup_names = DebusineTaskConfiguration.get_lookup_names(
                        task_type,
                        task_name,
                        form.cleaned_data["subject"] or None,
                        form.cleaned_data["context"] or None,
                    )
                    resolved_lookup_names = {
                        name: list(list_configuration(collection, name))
                        for name in lookup_names
                    }
                    ctx["lookup_names"] = resolved_lookup_names

                    config_items = list(
                        itertools.chain.from_iterable(
                            resolved_lookup_names.values()
                        )
                    )
                    ctx["config_items"] = config_items

                    # Turn items into a configuration to apply
                    default_values, override_values = build_configuration(
                        config_items
                    )
                    ctx["default_values"] = default_values
                    ctx["override_values"] = override_values
                ctx["form"] = form

        ctx["collection"] = collection
        return ctx
