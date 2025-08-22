# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine list view paginators."""

from typing import Any, TypeVar

from django.db.models import Model, Q, QuerySet

from debusine.db.models import (
    CollectionItem,
    User,
    WorkRequest,
    Worker,
    WorkerPool,
    WorkflowTemplate,
)
from debusine.db.models.auth import GroupAuditLog, GroupMembership
from debusine.db.models.work_requests import workflow_flattened
from debusine.web.forms import WorkflowFilterForm
from debusine.web.views.table import (
    Column,
    DateTimeColumn,
    FilterField,
    FilterSelectOne,
    FilterToggle,
    NumberColumn,
    Ordering,
    StringColumn,
    Table,
)

M = TypeVar("M", bound=Model)


class GroupMembershipTable(Table[GroupMembership]):
    """Table set up for GroupMembership."""

    name = StringColumn(
        "Name",
        ordering=Ordering(
            asc=["user__username", "role"], desc=["-user__username", "role"]
        ),
    )
    display_name = StringColumn("Display name")
    role = StringColumn(
        "Role",
        ordering=Ordering(
            asc=["role", "user__username"], desc=["-role", "user__username"]
        ),
    )

    template_name = "web/_group_membership-list.html"
    default_order = "role"


class GroupMembershipAdminTable(GroupMembershipTable):
    """GroupMembership table with an Actions column."""

    actions = Column("Actions")


class GroupAuditLogTable(Table[GroupAuditLog]):
    """Table set up for GroupAuditLog."""

    date = DateTimeColumn("Date", ordering="created_at")
    actor = StringColumn(
        "User",
        ordering=Ordering(
            asc=["actor__username", "-created_at"],
            desc=["-actor__username", "-created_at"],
        ),
    )
    message = StringColumn("Message")

    template_name = "web/_group_audit_log-list.html"
    default_order = "-date"


class WorkerTable(Table[Worker]):
    """Table set up for Worker."""

    type_ = StringColumn("Type", ordering="worker_type", name="type")
    name = StringColumn("Name", ordering="name")
    pool = StringColumn("Pool", ordering="worker_pool")
    last_seen = DateTimeColumn("Last seen", ordering="token__last_seen_at")
    status = StringColumn("Status")

    filter_name = FilterField("Name")
    filter_pool = FilterSelectOne("Worker pool")
    filter_status = FilterSelectOne("Status")

    template_name = "web/_worker-list.html"
    default_order = "name"

    def init(self) -> None:
        """Add filter options based on database contents."""
        super().init()

        # Add options to worker pool filter
        self.filters["pool"].add_option(
            "_none", "Static workers", Q(worker_pool__isnull=True)
        )
        for pk, name in (
            WorkerPool.objects.filter(worker__in=self.queryset)
            .order_by("name")
            .distinct()
            .values_list("id", "name")
        ):
            self.filters["pool"].add_option(
                name=str(pk), label=name, query=Q(worker_pool__id=pk)
            )

        # Add options to status filter
        self.filters["status"].add_option(
            "disabled",
            "Disabled",
            Q(token__isnull=True) | Q(token__enabled=False),
        )
        self.filters["status"].add_option(
            "disconnected",
            "Disconnected",
            Q(token__enabled=True, connected_at__isnull=True),
        )
        self.filters["status"].add_option(
            "running",
            "Running",
            Q(
                token__enabled=True,
                connected_at__isnull=False,
                assigned_work_requests__status=(WorkRequest.Statuses.RUNNING),
            ),
        )
        self.filters["status"].add_option(
            "idle",
            "Idle",
            Q(token__enabled=True, connected_at__isnull=False)
            & ~Q(
                assigned_work_requests__status=(WorkRequest.Statuses.RUNNING),
            ),
        )


class WorkerPoolTable(Table[WorkerPool]):
    """Table set up for WorkerPool."""

    name = StringColumn("Name", ordering="name")
    enabled = StringColumn("Enabled", ordering="enabled")
    instance_wide = StringColumn("Instance-wide", ordering="instance_wide")
    ephemeral = StringColumn("Ephemeral", ordering="ephemeral")
    workers = StringColumn("Workers", ordering="workers")

    template_name = "web/_worker_pool-list.html"
    default_order = "name"


class WorkRequestTable(Table[WorkRequest]):
    """Table set up for WorkRequest."""

    id_ = NumberColumn("ID", ordering="pk", name="id")
    created_at = DateTimeColumn("Created", ordering="created_at")
    task_type = StringColumn("Task type", ordering="task_type")
    task_name = StringColumn("Task", ordering="task_name")
    status = StringColumn("Status", ordering="status")
    result = StringColumn("Result", ordering="result")

    template_name = "web/_work_request-list.html"
    default_order = "-created_at"


class FilterStatuses(FilterField):
    """Custom queryset filtering for workflow statuses."""

    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any
    ) -> QuerySet[M, M]:
        """Filter a queryset."""
        assert isinstance(value, list)
        q_objects = Q()
        for status in value:
            if status == "running__any":
                q_objects |= Q(status=WorkRequest.Statuses.RUNNING)
            elif status.startswith("running__"):
                q_objects |= Q(
                    status=WorkRequest.Statuses.RUNNING,
                    workflow_runtime_status=status[9:],
                )
            else:
                q_objects |= Q(status=status)
        return queryset.filter(q_objects)


class FilterFailedWorkRequests(FilterToggle):
    """Keep only workflows with failed work requests."""

    def __init__(self, label: str):
        """Pass an empty Q, since we ignore it for filtering."""
        super().__init__(label, q=Q())

    def filter_queryset(
        self, queryset: QuerySet[M, M], value: Any
    ) -> QuerySet[M, M]:
        """Filter the queryset."""
        if not value:
            return queryset
        # TODO: This filter makes a complex query for all workflows in the
        # queryset. This will need to be changed (cached in the DB?)
        # See #656
        work_requests_ids_relevant = []
        for work_request in queryset:
            count = (
                workflow_flattened(work_request)  # type: ignore[arg-type]
                .filter(result=WorkRequest.Results.FAILURE)
                .count()
                or 0
            )
            if count > 0:
                work_requests_ids_relevant.append(work_request.pk)
        return queryset.filter(id__in=work_requests_ids_relevant)


class WorkflowTable(Table[WorkRequest]):
    """Table set up for workflow WorkRequests."""

    id_ = NumberColumn("ID", ordering="pk", name="id")
    workflow_template = StringColumn("Template", ordering="task_name")
    status = StringColumn(
        "Status", ordering=("status", "workflow_runtime_status")
    )
    result = StringColumn("Result", ordering="result")
    started_at = DateTimeColumn("Started", ordering="started_at")
    completed_at = DateTimeColumn("Completed", ordering="completed_at")
    wr_count = Column("Count of work requests")
    last_activity = DateTimeColumn(
        "Last activity",
        ordering="workflow_last_activity_at",
        help_text="Latest activity recorded for workflow’s work request",
    )
    started_by = StringColumn("Started by", ordering="created_by__username")

    filter_statuses = FilterStatuses()
    filter_results = FilterField(q_field="result__in")
    filter_workflow_templates = FilterSelectOne("Workflow template")
    filter_started_by = FilterSelectOne("Started by")
    filter_with_failed_work_requests = FilterFailedWorkRequests(
        "With failed work requests"
    )

    template_name = "web/_workflow-list.html"
    default_order = "id.desc"
    filter_form_class = WorkflowFilterForm

    def init_filters(self) -> None:
        """Add filter options based on database contents."""
        super().init_filters()
        for wt in (
            WorkflowTemplate.objects.filter(
                pk__in=self.queryset.values("workflow_template")
            )
            .order_by("name")
            .distinct()
        ):
            self.filters["workflow_templates"].add_option(
                wt.name, wt.name, query=Q(workflow_template=wt)
            )

        for user in (
            User.objects.filter(pk__in=self.queryset.values("created_by"))
            .order_by("first_name", "last_name")
            .distinct()
        ):
            self.filters["started_by"].add_option(
                user.username, user.get_full_name(), query=Q(created_by=user)
            )


class CollectionItemTable(Table[CollectionItem]):
    """Table set up for CollectionItem."""

    category = StringColumn("Category", ordering="category")
    name = StringColumn("Name", ordering="name")
    created_at = DateTimeColumn("Created at", ordering="created_at")
    details = Column("Details")

    template_name = "web/_collection_item-list.html"
    default_order = "category"
