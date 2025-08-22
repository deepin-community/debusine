# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine workflows views."""
from typing import cast

from django.db.models import QuerySet

from debusine.artifacts.models import TaskTypes
from debusine.db.context import context
from debusine.db.models import WorkRequest
from debusine.db.models.work_requests import WorkRequestQuerySet
from debusine.web.views.base import ListViewBase, WorkspaceView
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import WorkflowTable


class WorkflowsListView(
    WorkspaceView, TableMixin[WorkRequest], ListViewBase[WorkRequest]
):
    """List workflows."""

    model = WorkRequest
    template_name = "web/workflow-list.html"
    table_class = WorkflowTable
    context_object_name = "workflow_list"
    paginate_by = 50

    def get_title(self) -> str:
        """Return the page title."""
        return f"{context.require_workspace().name} workflows"

    def get_queryset(self) -> QuerySet[WorkRequest]:
        """Filter workflows by current workspace."""
        queryset = (
            cast(WorkRequestQuerySet[WorkRequest], super().get_queryset())
            .in_current_workspace()
            .can_display(context.user)
        )
        queryset = queryset.filter(
            task_type=TaskTypes.WORKFLOW, parent__isnull=True
        )
        return queryset
