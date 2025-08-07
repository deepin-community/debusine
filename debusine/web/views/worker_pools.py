# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views related to worker pools."""

from typing import Any

from django.db.models import Count, QuerySet
from django.shortcuts import get_object_or_404

from debusine.db.models import WorkerPool
from debusine.web.views.base import BaseUIView, DetailViewBase, ListViewBase
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import WorkerPoolTable, WorkerTable


class WorkerPoolsListView(
    TableMixin[WorkerPool], BaseUIView, ListViewBase[WorkerPool]
):
    """List worker pools."""

    model = WorkerPool
    table_class = WorkerPoolTable
    template_name = "web/worker_pool-list.html"
    title = "List of worker pools"
    paginate_by = 20

    def get_queryset(self) -> QuerySet[WorkerPool, WorkerPool]:
        """Use the custom QuerySet."""
        return super().get_queryset().annotate(workers=Count("worker"))


class WorkerPoolDetailView(BaseUIView, DetailViewBase[WorkerPool]):
    """Show details about a worker pool."""

    model = WorkerPool
    template_name = "web/worker_pool-detail.html"
    context_object_name = "worker_pool"

    def get_object(
        self, queryset: QuerySet[WorkerPool, WorkerPool] | None = None
    ) -> WorkerPool:
        """Return the Worker object to show."""
        assert queryset is None
        queryset = self.get_queryset()
        return get_object_or_404(queryset, name=self.kwargs["name"])

    def get_title(self) -> str:
        """Get the title for the page."""
        return f"Worker pool {self.object.name}"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add to context data."""
        ctx = super().get_context_data(**kwargs)
        ctx["workers"] = WorkerTable(
            self.request,
            self.object.worker_set.all(),
        ).get_paginator(per_page=30)
        return ctx
