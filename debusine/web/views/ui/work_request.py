# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""UI helpers for work requests."""

from collections import defaultdict
from functools import cached_property

from debusine.db.models import WorkRequest, WorkflowTemplate, work_requests
from debusine.web.helps import DOCS_BASE_URL
from debusine.web.views.ui.base import UI


class WorkflowTemplateUIHelper(UI[WorkflowTemplate]):
    """UI helpers for WorkflowTemplate instances."""

    def get_help_url(self) -> str:
        """Return a URL to the appropriate workflow documentation."""
        # We could avoid hardcoding this URL structure by using Sphinx to
        # fetch and parse the intersphinx inventory.  However, Sphinx seems
        # like too heavy a dependency to pull in just for this.
        return (
            f"{DOCS_BASE_URL}/reference/workflows/specs/"
            f"{self.instance.task_name.replace('_', '-')}.html"
        )


class WorkRequestUIHelper(UI[WorkRequest]):
    """UI helpers for WorkRequest instances."""

    @cached_property
    def workflow_flattened(self) -> list[WorkRequest]:
        """Cached version of work_requests.workflow_flattened."""
        return list(work_requests.workflow_flattened(self.instance))

    @cached_property
    def workflow_work_requests_success(self) -> int:
        """Return number of successful work requests."""
        return sum(
            child.result == WorkRequest.Results.SUCCESS
            for child in self.workflow_flattened
        )

    @cached_property
    def workflow_work_requests_failure(self) -> int:
        """Return number of failed work requests."""
        return sum(
            child.result == WorkRequest.Results.FAILURE
            for child in self.workflow_flattened
        )

    @cached_property
    def workflow_work_requests_pending(self) -> int:
        """Return number of pending work requests."""
        return sum(
            child.status == WorkRequest.Statuses.PENDING
            for child in self.workflow_flattened
        )

    @cached_property
    def workflow_work_requests_blocked(self) -> int:
        """Return number of blocked work requests."""
        return sum(
            child.status == WorkRequest.Statuses.BLOCKED
            for child in self.workflow_flattened
        )

    @cached_property
    def workflow_children(self) -> dict[int, list[WorkRequest]]:
        """Mapping from work request IDs to their children in this workflow."""
        children: defaultdict[int, list[WorkRequest]] = defaultdict(list)
        for wr in (
            work_requests.workflow_flattened(
                self.instance, include_workflows=True
            )
            .filter(parent__isnull=False)
            .visible_in_workflow()
            .order_by("id")
            .select_related("workspace__scope")
        ):
            assert wr.parent_id is not None
            children[wr.parent_id].append(wr)
        return children
