# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine views."""

from typing import Any

from django.utils.safestring import SafeString
from django.views.generic import TemplateView

from debusine.db.context import context
from debusine.db.models import WorkRequest, Workspace
from debusine.web.views.base import BaseUIView
from debusine.web.views.table import NumberColumn, StringColumn, Table
from debusine.web.views.tables import WorkflowTable


class ScopeWorkspaceTable(Table[Workspace]):
    """Table showing scopes and workspaces."""

    scope = StringColumn("Scope")
    workspace = StringColumn("Workspace")
    role = StringColumn("Role")
    running = NumberColumn(
        SafeString(
            '<span title="Running workflows"'
            ' class="badge text-bg-secondary">R</span>'
        )
    )
    input_needed = NumberColumn(
        SafeString(
            '<span title="Input needed workflows"'
            ' class="badge text-bg-secondary">I</span>'
        )
    )
    completed = NumberColumn(
        SafeString(
            '<span title="Completed and aborted workflows"'
            ' class="badge text-bg-primary">C</span>'
        )
    )

    template_name = "web/_scope_workspace-list.html"


class HomepageView(BaseUIView, TemplateView):
    """Class for the homepage view."""

    template_name = "web/homepage.html"
    title = "Homepage"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with work_request_list and workspace_list."""
        ctx = super().get_context_data(**kwargs)

        # Signal the base template that we're outside of all scopes
        ctx["debusine_homepage"] = True

        if self.request.user.is_authenticated:
            workflows = (
                WorkRequest.objects.select_related("workspace__scope")
                .can_display(context.user)
                .filter(created_by=self.request.user)
                .workflows()
                .filter(parent__isnull=True)
            )
            ctx["workflows_current"] = WorkflowTable(
                self.request,
                workflows.running(),
                prefix="current",
                preview=True,
            ).get_paginator(per_page=5)

            ctx["workflows_completed"] = WorkflowTable(
                self.request,
                workflows.filter(
                    status__in=(
                        WorkRequest.Statuses.COMPLETED,
                        WorkRequest.Statuses.ABORTED,
                    )
                ),
                prefix="completed",
                preview=True,
            ).get_paginator(per_page=5)

        ctx["workspaces"] = ScopeWorkspaceTable(
            self.request,
            Workspace.objects.can_display(context.user)
            .select_related("scope")
            .order_by("scope", "name")
            .annotate_with_workflow_stats()
            .with_role_annotations(context.user),
            prefix="workspaces",
        ).get_paginator(per_page=10)

        return ctx
