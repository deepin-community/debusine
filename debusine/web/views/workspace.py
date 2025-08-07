# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine workspace views."""

from typing import Any

from django.db.models import Count, QuerySet

from debusine.db.context import context
from debusine.db.models import Collection, WorkflowTemplate, Workspace
from debusine.web.forms import WorkspaceForm
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
