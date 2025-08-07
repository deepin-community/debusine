# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Compute task status (workers, queue, etc.)."""
from typing import Any

from django.views.generic import TemplateView

from debusine.server.status import TaskQueueSummary, WorkerStatus
from debusine.tasks.models import WorkerType
from debusine.web.views.base import BaseUIView


class TaskStatusView(BaseUIView, TemplateView):
    """View to display task status."""

    template_name = "web/task_status.html"
    title = "Task queue"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with arch_status."""
        context = super().get_context_data(**kwargs)

        status = WorkerStatus.get_status()

        # Re-oragnize status by worker type for table
        worker_status: dict[WorkerType, dict[str, Any]] = {}
        for worker_type, worker_summary in status.workers.items():
            worker_status[worker_type] = {
                "worker_tasks": status.worker_tasks[worker_type],
                "workers": worker_summary,
            }
        context["worker_status"] = worker_status

        # Re-organize status by architecture for table
        arch_status: dict[str, dict[str, Any]] = {}
        for arch, worker_summary in status.external_workers_arch.items():
            arch_status[arch] = {
                "worker_tasks_arch": status.worker_tasks_arch[arch],
                "external_workers_arch": worker_summary,
            }
        if status.worker_tasks_arch:
            # Sorts last
            arch_status["_none_"] = {
                "worker_tasks_arch": status.worker_tasks_arch.get(
                    None, TaskQueueSummary()
                ),
                "external_workers_arch": status.workers[WorkerType.EXTERNAL],
            }
        context["arch_status"] = arch_status

        return context
