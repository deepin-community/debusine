# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views related to workers."""

from typing import Any, cast

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from debusine.db.context import context
from debusine.db.models import WorkRequest, Worker
from debusine.db.models.workers import WorkerQuerySet
from debusine.tasks.models import WorkerType
from debusine.web.views.base import BaseUIView, DetailViewBase, ListViewBase
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import WorkRequestTable, WorkerTable


class WorkersListView(BaseUIView, TableMixin[Worker], ListViewBase[Worker]):
    """List workers."""

    model = Worker
    template_name = "web/worker-list.html"
    title = "List of workers"
    table_class = WorkerTable
    paginate_by = 50

    def get_queryset(self) -> WorkerQuerySet[Any]:
        """Use the custom QuerySet."""
        queryset = super().get_queryset()
        queryset = cast(
            WorkerQuerySet[Any], queryset.select_related("worker_pool")
        )
        return queryset.active()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add context to the default ListView data."""
        ctx = super().get_context_data(**kwargs)
        ctx["WorkerType"] = {wt.name: wt.value for wt in WorkerType}
        return ctx


class WorkerDetailView(BaseUIView, DetailViewBase[Worker]):
    """Show details about a worker."""

    model = Worker
    template_name = "web/worker-detail.html"
    context_object_name = "worker"

    def get_object(
        self, queryset: QuerySet[Worker, Worker] | None = None
    ) -> Worker:
        """Return the Worker object to show."""
        assert queryset is None
        queryset = self.get_queryset()
        return get_object_or_404(queryset, name=self.kwargs["name"])

    def get_title(self) -> str:
        """Get the title for the page."""
        return f"Worker {self.object.name}"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add to context data."""
        ctx = super().get_context_data(**kwargs)
        ctx["show_metadata"] = self.request.user.is_authenticated
        ctx["work_requests"] = WorkRequestTable(
            self.request,
            cast(
                QuerySet[WorkRequest],
                self.object.assigned_work_requests.can_display(context.user),
            ),
        ).get_paginator(per_page=10)
        return ctx
