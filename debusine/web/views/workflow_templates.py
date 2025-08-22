# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine workflow templates views."""

from typing import cast

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from debusine.db.models.work_requests import (
    WorkflowTemplate,
    WorkflowTemplateQuerySet,
)
from debusine.web.views.base import DetailViewBase, WorkspaceView


class WorkflowTemplateDetailView(
    WorkspaceView, DetailViewBase[WorkflowTemplate]
):
    """Display details about the workflow template."""

    model = WorkflowTemplate
    template_name = "web/workflow-template-detail.html"
    context_object_name = "workflow_template"

    def get_queryset(self) -> QuerySet[WorkflowTemplate]:
        """Filter workflow templates to the current workspace."""
        return cast(
            WorkflowTemplateQuerySet[WorkflowTemplate], super().get_queryset()
        ).in_current_workspace()

    def get_object(
        self,
        queryset: QuerySet[WorkflowTemplate, WorkflowTemplate] | None = None,
    ) -> WorkflowTemplate:
        """Return the workflow template object to show."""
        assert queryset is None
        queryset = self.get_queryset()

        workflow_template = get_object_or_404(
            queryset, name=self.kwargs["name"]
        )
        self.enforce(workflow_template.can_display)
        return workflow_template

    def get_title(self) -> str:
        """Return the page title."""
        return f"Workflow template {self.get_object().name}"
