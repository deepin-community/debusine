# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db work requests."""

import enum
import logging
import re
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import Enum, auto
from itertools import chain
from typing import (
    Any,
    Generic,
    Literal,
    Optional,
    Self,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    assert_never,
    cast,
)

from django.core.exceptions import (
    FieldError,
    ImproperlyConfigured,
    ValidationError,
)
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from django.db.models import (
    Case,
    DateTimeField,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    JSONField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    UniqueConstraint,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest
from django.urls import reverse
from django.utils import timezone
from django_cte import CTEQuerySet, With

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineHistoricalTaskRun,
    TaskTypes,
    WorkRequestResults,
)
from debusine.client.models import (
    LookupChildType,
    model_to_json_serializable_dict,
)
from debusine.db.context import context
from debusine.db.models import Collection, CollectionItem, Scope, permissions
from debusine.db.models.permissions import (
    Allow,
    PermissionUser,
    Role,
    enforce,
    permission_check,
    permission_filter,
)
from debusine.db.models.worker_pools import WorkerPoolTaskExecutionStatistics
from debusine.db.models.workspaces import Workspace
from debusine.server import notifications
from debusine.tasks import BaseTask, TaskConfigError
from debusine.tasks.models import (
    ActionRecordInTaskHistory,
    ActionRetryWithDelays,
    ActionSkipIfLookupResultChanged,
    ActionTypes,
    ActionUpdateCollectionWithArtifacts,
    ActionUpdateCollectionWithData,
    BaseTaskData,
    EventReaction,
    EventReactions,
    NotificationDataEmail,
    OutputData,
)

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.db.models.auth import User
    from debusine.db.models.workers import Worker
    from debusine.server.workflows.models import (
        WorkRequestManualUnblockAction,
        WorkRequestWorkflowData,
    )
else:
    TypedModelMeta = object

logger = logging.getLogger(__name__)

A = TypeVar("A")

#: Limit on how many times a work request may be retried automatically.
MAX_AUTOMATIC_RETRIES = 3


class CannotRetry(Exception):
    """Exception raised if a work request cannot be retried."""


class CannotAbort(Exception):
    """Exception raised if a work request cannot be aborted."""


class CannotUnblock(Exception):
    """Exception raised if a work request cannot be unblocked."""


class InternalTaskError(Exception):
    """Exception raised when trying to instantiate an internal task."""


class SkipWorkRequest(Exception):
    """Exception raised when a work request should be skipped."""


class WorkRequestRetryReason(Enum):
    """The reason for retrying a work request."""

    #: The worker that was running this work request failed or was
    #: terminated.  Only retry up to the limit set by
    #: :py:const:`MAX_AUTOMATIC_RETRIES`, just in case this work request is
    #: killing the worker, and increment the retry count.
    WORKER_FAILED = auto()

    #: The work request is being automatically retried due to a
    #: ``retry-with-delays`` action.  Increment the retry count, but ignore
    #: :py:const:`MAX_AUTOMATIC_RETRIES`; the ``delays`` option sets a limit
    #: on how many times the work request will be retried.
    DELAY = auto()

    #: A user requested a retry.  Reset the (automatic) retry count.
    MANUAL = auto()


class WorkRequestRoleBase(permissions.RoleBase):
    """WorkRequest role implementation."""

    implied_by_scope_roles: frozenset[Scope.Roles]
    implied_by_workspace_roles: frozenset[Workspace.Roles]
    implied_by_public: bool
    implied_by_creator: bool

    def _setup(self) -> None:
        """Set up implications for a newly constructed role."""
        implied_by_scope_roles: set[Scope.Roles] = set()
        implied_by_workspace_roles: set[Workspace.Roles] = set()
        implied_by_public: bool = False
        implied_by_creator: bool = False
        for i in self.implied_by:
            match i:
                case Workspace.Roles():
                    implied_by_scope_roles |= i.implied_by_scope_roles
                    implied_by_workspace_roles |= i.implied_by_workspace_roles
                    implied_by_public = implied_by_public or i.implied_by_public
                case Role():
                    # Resolve a role passed during class definition into its
                    # enum instance
                    wr = self.__class__(i.value)
                    implied_by_scope_roles |= wr.implied_by_scope_roles
                    implied_by_workspace_roles |= wr.implied_by_workspace_roles
                    implied_by_public = (
                        implied_by_public or wr.implied_by_public
                    )
                    implied_by_creator = (
                        implied_by_creator or wr.implied_by_creator
                    )
                case "creator":
                    implied_by_creator = True
                case _:
                    raise ImproperlyConfigured(
                        "WorkRequest roles do not support"
                        f" implications by {i!r}"
                    )
        self.implied_by_scope_roles = frozenset(implied_by_scope_roles)
        self.implied_by_workspace_roles = frozenset(implied_by_workspace_roles)
        self.implied_by_public = implied_by_public
        self.implied_by_creator = implied_by_creator

    def q(self, user: PermissionUser) -> Q:
        """Return a Q expression to select work requests with this role."""
        assert user is not None
        if not user.is_authenticated:
            if self.implied_by_public:
                return Q(workspace__public=True)
            else:
                return Q(pk__in=())

        workspace_q = Q(
            roles__group__users=user,
            roles__role__in=self.implied_by_workspace_roles,
        ) | Q(
            scope__in=Scope.objects.filter(
                roles__group__users=user,
                roles__role__in=self.implied_by_scope_roles,
            )
        )
        if self.implied_by_public:
            workspace_q |= Q(public=True)
        q = Q(workspace__in=Workspace.objects.filter(workspace_q))
        if self.implied_by_creator:
            q |= Q(created_by=user)

        return q

    def implies(self, role: "WorkRequestRoles") -> bool:
        """Check if this role implies the given one."""
        return (
            self.implied_by_scope_roles <= role.implied_by_scope_roles
            and self.implied_by_workspace_roles
            <= role.implied_by_workspace_roles
            and self.implied_by_public <= role.implied_by_public
            and self.implied_by_creator <= role.implied_by_creator
        )


class WorkRequestRoles(permissions.Roles, WorkRequestRoleBase, enum.ReprEnum):
    """Available roles for a Scope."""

    OWNER = Role("owner", implied_by=[Workspace.Roles.OWNER])
    CREATOR = Role("creator", implied_by=[OWNER, "creator"])
    CONTRIBUTOR = Role(
        "contributor", implied_by=[OWNER, Workspace.Roles.CONTRIBUTOR]
    )
    VIEWER = Role("viewer", implied_by=[CONTRIBUTOR, Workspace.Roles.VIEWER])


WorkRequestRoles.setup()


class WorkflowTemplateQuerySet(QuerySet["WorkflowTemplate", A], Generic[A]):
    """Custom QuerySet for WorkflowTemplate."""

    def in_current_scope(self) -> "WorkflowTemplateQuerySet[A]":
        """Filter to work requests in the current scope."""
        return self.filter(workspace__scope=context.require_scope())

    def in_current_workspace(self) -> "WorkflowTemplateQuerySet[A]":
        """Filter to work requests in the current workspace."""
        return self.filter(workspace=context.require_workspace())

    @permission_filter(workers=Allow.ALWAYS, anonymous=Allow.PASS)
    def can_display(
        self, user: PermissionUser
    ) -> "WorkflowTemplateQuerySet[A]":
        """Keep only WorkflowTemplates that can be displayed."""
        return self.filter(
            workspace__in=Workspace.objects.with_role(
                user, Workspace.Roles.VIEWER
            )
        ).distinct()

    @permission_filter()
    def can_run(self, user: PermissionUser) -> "WorkflowTemplateQuerySet[A]":
        """Keep only WorkflowTemplates that can be started."""
        return self.filter(
            workspace__in=Workspace.objects.with_role(
                user, Workspace.Roles.CONTRIBUTOR
            )
        ).distinct()


class WorkflowTemplateManager(models.Manager["WorkflowTemplate"]):
    """Manager for WorkflowTemplate model."""

    def get_queryset(self) -> WorkflowTemplateQuerySet[Any]:
        """Use the custom QuerySet."""
        return WorkflowTemplateQuerySet(self.model, using=self._db)


class WorkflowTemplate(models.Model):
    """
    Database model for Workflow templates.

    Workflow templates contain the information needed to instantiate a
    workflow, with a Workflow orchestrator and mandatory parameters.
    """

    objects = WorkflowTemplateManager.from_queryset(WorkflowTemplateQuerySet)()

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["name", "workspace"],
                name="%(app_label)s_%(class)s_unique_name_workspace",
            ),
        ]

    name = models.CharField(max_length=255)
    workspace = models.ForeignKey("Workspace", on_delete=models.PROTECT)
    task_name = models.CharField(
        max_length=100, verbose_name='Name of the Workflow orchestrator class'
    )
    task_data = JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    priority = models.IntegerField(
        default=0,
        help_text="Base priority for work requests created from this template",
    )

    def clean(self) -> None:
        """
        Ensure that task_name and task data are valid.

        :raise ValidationError: for invalid data.
        """
        # Import here to prevent circular imports
        from debusine.server.workflows import Workflow, WorkflowValidationError

        if not isinstance(self.task_data, dict):
            raise ValidationError(
                {"task_data": "task data must be a dictionary"}
            )

        # Instantiate the orchestrator and use it to validate task_data
        workflow_cls = Workflow.from_name(self.task_name)
        try:
            workflow_cls.validate_template_data(self.task_data)
        except (
            KeyError,
            ValueError,
            RuntimeError,
            WorkflowValidationError,
        ) as exc:
            raise ValidationError({"task_data": str(exc)})

    @permission_check(
        "{user} cannot display {resource}",
        workers=Allow.ALWAYS,
        anonymous=Allow.PASS,
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the workflow template can be displayed."""
        return self.workspace.has_role(user, Workspace.Roles.VIEWER)

    @permission_check("{user} cannot run {resource}")
    def can_run(self, user: PermissionUser) -> bool:
        """Check if the workflow template can be run."""
        return self.workspace.has_role(user, Workspace.Roles.CONTRIBUTOR)

    def completed_workflows(self) -> int:
        """Count completed workflows for this template in context.workspace."""
        return (
            self.workrequest_set.filter(workspace=context.workspace)
            .filter(status=WorkRequest.Statuses.COMPLETED)
            .count()
        )

    def running_workflows(self) -> int:
        """Count completed workflows for this template in context.workspace."""
        return (
            self.workrequest_set.in_current_workspace()
            .filter(status=WorkRequest.Statuses.RUNNING)
            .count()
        )

    def needs_input_workflows(self) -> int:
        """Count completed workflows for this template in context.workspace."""
        return (
            self.workrequest_set.filter(workspace=context.workspace)
            .filter(
                workflow_runtime_status=WorkRequest.RuntimeStatuses.NEEDS_INPUT
            )
            .count()
        )

    def get_absolute_url(self) -> str:
        """Return the canonical display URL."""
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.workspace.scope.name):
            return reverse(
                "workspaces:workflow_templates:detail",
                kwargs={"wname": self.workspace.name, "name": self.name},
            )


class _WorkRequestStatuses(models.TextChoices):
    """Choices for WorkRequest.status."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    ABORTED = "aborted", "Aborted"
    BLOCKED = "blocked", "Blocked"


class WorkRequestQuerySet(
    CTEQuerySet,  # type: ignore[misc]
    QuerySet["WorkRequest", A],
    Generic[A],
):
    """Custom QuerySet for WorkRequest."""

    def in_current_scope(self) -> Self:
        """Filter to work requests in the current scope."""
        return self.filter(workspace__scope=context.require_scope())

    def in_current_workspace(self) -> Self:
        """Filter to work requests in the current workspace."""
        return self.filter(workspace=context.require_workspace())

    def with_role_annotations(self, user: PermissionUser) -> Self:
        """
        Annotate the queryset with a bool for each role, with its presence.

        The returned annotations are in the form ``"is_{role}"`` and are True
        if the user has the given role on the annotated resource.

        A further ``roles_for`` annotation marks the stringified public key of
        the user used to compute annotations, or the empty string if the
        annotations are for the anonymous user.
        """
        queryset = self.annotate(
            roles_for=Value(
                str(user.pk) if user and user.is_authenticated else ""
            )
        )
        for role in WorkRequestRoles:
            queryset = queryset.annotate(
                **{
                    f"is_{role}": Exists(
                        WorkRequest.objects.filter(pk=OuterRef("pk")).with_role(
                            user, role
                        )
                    )
                }
            )
        return queryset

    def with_role(self, user: PermissionUser, role: WorkRequestRoles) -> Self:
        """Keep only resources where the user has the given role."""
        return self.filter(role.q(user)).distinct()

    def can_be_unblocked(self) -> "WorkRequestQuerySet[WorkRequest]":
        """Filter to work requests that can be unblocked at all."""
        return cast(
            WorkRequestQuerySet[WorkRequest],
            self.filter(
                # The work request must currently be blocked.
                Q(status=WorkRequest.Statuses.BLOCKED)
                # If this work request is in a workflow, then that workflow
                # must be running.
                & (
                    Q(parent__isnull=True)
                    | Q(parent__status=WorkRequest.Statuses.RUNNING)
                )
            ),
        )

    def can_be_automatically_unblocked(
        self,
    ) -> "WorkRequestQuerySet[WorkRequest]":
        """Filter to work requests that can be automatically unblocked."""
        return self.can_be_unblocked().filter(
            # DEPS work requests can be unblocked if and only if all their
            # dependencies have completed.  We don't know how to deal with
            # other unblock strategies automatically.
            Q(unblock_strategy=WorkRequest.UnblockStrategy.DEPS)
            & ~Exists(
                WorkRequest.objects.filter(
                    Q(reverse_dependencies=OuterRef("id"))
                    & ~Q(status=WorkRequest.Statuses.COMPLETED)
                )
            )
        )

    @permission_filter(workers=Allow.ALWAYS, anonymous=Allow.PASS)
    def can_display(self, user: PermissionUser) -> "WorkRequestQuerySet[A]":
        """Keep only WorkRequests that can be displayed."""
        return self.with_role(user, WorkRequestRoles.VIEWER)

    @permission_filter(workers=Allow.ALWAYS)
    def can_retry(self, user: PermissionUser) -> "WorkRequestQuerySet[A]":
        """Keep only WorkRequests that can be retried."""
        return self.with_role(user, WorkRequestRoles.CONTRIBUTOR)

    @permission_filter()
    def can_abort(self, user: PermissionUser) -> "WorkRequestQuerySet[A]":
        """Keep only WorkRequests that can be aborted."""
        return self.with_role(user, WorkRequestRoles.CREATOR)

    @permission_filter()
    def can_unblock(self, user: PermissionUser) -> "WorkRequestQuerySet[A]":
        """Keep only WorkRequests that can be unblocked."""
        return self.with_role(user, WorkRequestRoles.OWNER)

    def pending(
        self, exclude_assigned: bool = False, worker: Optional["Worker"] = None
    ) -> "WorkRequestQuerySet[Any]":
        """
        Return a QuerySet of tasks in WorkRequest.Statuses.PENDING status.

        QuerySet is ordered by descending effective priority, then by
        created_at.

        Filter out the assigned pending ones if exclude_assigned=True,
        and include only the WorkRequest for worker.

        PENDING is the default status of a task on creation.
        """
        if exclude_assigned and worker is not None:
            raise ValueError("Cannot exclude_assigned and filter by worker")

        qs = self.filter(status=WorkRequest.Statuses.PENDING)

        if exclude_assigned:
            qs = qs.exclude(worker__isnull=False)

        if worker is not None:
            qs = qs.filter(worker=worker)

        qs = qs.order_by(
            (F("priority_base") + F("priority_adjustment")).desc(), "created_at"
        )

        return qs

    def running(
        self, worker: Optional["Worker"] = None
    ) -> "WorkRequestQuerySet[Any]":
        """Return a QuerySet of tasks in running status."""
        qs = self.filter(status=WorkRequest.Statuses.RUNNING)

        if worker is not None:
            qs = qs.filter(worker=worker)

        return qs

    def completed(self) -> "WorkRequestQuerySet[Any]":
        """Return a QuerySet of tasks in completed status."""
        return self.filter(status=WorkRequest.Statuses.COMPLETED)

    def aborted(self) -> "WorkRequestQuerySet[Any]":
        """Return a QuerySet of tasks in aborted status."""
        return self.filter(status=WorkRequest.Statuses.ABORTED)

    def aborted_or_failed(self) -> "WorkRequestQuerySet[Any]":
        """Return a QuerySet of tasks that aborted or failed."""
        return self.filter(
            Q(status=WorkRequest.Statuses.ABORTED)
            | Q(
                status=WorkRequest.Statuses.COMPLETED,
                result=WorkRequest.Results.FAILURE,
            )
        )

    def expired(self, at: datetime) -> "WorkRequestQuerySet[Any]":
        """
        Return queryset with work requests that have expired.

        :param at: datetime to check if the work requests are expired.
        :return: work requests which expire before the given datetime.
        """
        return (
            self.annotate(
                effective_expiration_delay=Coalesce(
                    "expiration_delay",
                    "workspace__default_expiration_delay",
                )
            )
            .exclude(effective_expiration_delay=timedelta(0))
            .filter(
                # https://github.com/typeddjango/django-stubs/issues/1548
                created_at__lte=(
                    at - F("effective_expiration_delay")  # type: ignore[operator] # noqa: E501
                )
            )
        )

    def workflows(self) -> "WorkRequestQuerySet[A]":
        """Keep only workflows."""
        return self.filter(task_type=TaskTypes.WORKFLOW)

    def needs_input(self) -> "WorkRequestQuerySet[A]":
        """Keep only work requests in NEEDS_INPUT state."""
        return self.filter(
            workflow_runtime_status=WorkRequest.RuntimeStatuses.NEEDS_INPUT
        )

    def visible_in_workflow(self) -> "WorkRequestQuerySet[A]":
        """Keep only work requests that should be shown in workflow views."""
        return self.annotate(
            visible=Coalesce(
                F("workflow_data_json__visible"),
                Value("true"),
                output_field=JSONField(),
            )
        ).filter(visible=True)

    def unsuperseded(self) -> "WorkRequestQuerySet[A]":
        """Keep only work requests that have not been superseded."""
        return self.filter(superseded__isnull=True)

    def terminated(self) -> "WorkRequestQuerySet[A]":
        """
        Keep only work requests that have terminated in some way.

        Work requests that are completed or aborted will not change any more
        (except perhaps for being retried, which will create a new work
        request).
        """
        return self.filter(
            status__in={
                WorkRequest.Statuses.COMPLETED,
                WorkRequest.Statuses.ABORTED,
            }
        )

    def with_workflow_root_id(self) -> "WorkRequestQuerySet[Any]":
        """Annotate the queryset with corresponding workflow roots."""

        def workflow_root_cte(cte: With) -> QuerySet[Any]:
            return self.values(
                rec_parent=F("parent"), rec_child=F("id"), start=F("id")
            ).union(
                cte.join(WorkRequest, id=cte.col.rec_parent).values(
                    rec_parent=F("parent"),
                    rec_child=F("id"),
                    start=cte.col.start,
                )
            )

        ancestors = With.recursive(workflow_root_cte, name="ancestors")
        roots = With(
            ancestors.queryset()
            .filter(rec_parent__isnull=True)
            .values(descendant=F("start"), root=F("rec_child"))
            .order_by(),
            name="roots",
        )
        qs = (
            roots.join(self, id=roots.col.descendant)
            .with_cte(ancestors)
            .with_cte(roots)
            .annotate(
                workflow_root_id=Case(
                    When(
                        Q(task_type=TaskTypes.WORKFLOW)
                        | Q(parent__isnull=False),
                        then=roots.col.root,
                    ),
                    default=None,
                )
            )
        )
        assert isinstance(qs, WorkRequestQuerySet)
        return qs


class WorkRequestManager(models.Manager["WorkRequest"]):
    """Manager for WorkRequest model."""

    def get_queryset(self) -> WorkRequestQuerySet[Any]:
        """Use the custom QuerySet."""
        return WorkRequestQuerySet(self.model, using=self._db)

    def create_workflow_callback(
        self,
        *,
        parent: "WorkRequest",
        step: str,
        display_name: str | None = None,
        status: _WorkRequestStatuses | None = None,
        visible: bool = True,
    ) -> "WorkRequest":
        """
        Create a workflow callback WorkRequest.

        A parent is always required, as a callback only makes sense as part of
        a workflow.

        :param step: string set by the workflow to identify this callback
        """
        # Import here to prevent circular imports
        from debusine.server.workflows.models import WorkRequestWorkflowData

        return self.create(
            workspace=parent.workspace,
            parent=parent,
            created_by=parent.created_by,
            status=status or WorkRequest.Statuses.PENDING,
            task_type=TaskTypes.INTERNAL,
            task_name="workflow",
            task_data={},
            priority_base=parent.priority_effective,
            workflow_data_json=WorkRequestWorkflowData(
                step=step, display_name=display_name or step, visible=visible
            ).dict(exclude_unset=True),
        )

    def create_workflow(
        self,
        *,
        template: WorkflowTemplate,
        data: dict[str, Any],
        parent: Optional["WorkRequest"] = None,
        status: _WorkRequestStatuses | None = None,
        validate: bool = True,
    ) -> "WorkRequest":
        """Create a workflow from a template and user-provided data."""
        # Import here to prevent circular imports
        from debusine.server.workflows import Workflow
        from debusine.server.workflows.models import WorkRequestWorkflowData

        enforce(template.can_run)
        created_by = context.require_user()
        assert created_by.is_authenticated

        # Merge user provided data into template data
        task_data: dict[str, Any] = {}
        task_data.update(data)
        task_data.update(template.task_data)

        # Build the WorkRequest
        work_request = self.create(
            workspace=template.workspace,
            parent=parent,
            workflow_template=template,
            workflow_data_json=WorkRequestWorkflowData(
                workflow_template_name=template.name
            ).dict(exclude_unset=True),
            created_by=created_by,
            status=status or WorkRequest.Statuses.PENDING,
            task_type=TaskTypes.WORKFLOW,
            task_name=template.task_name,
            task_data=task_data,
            priority_base=template.priority,
        )

        # Root work requests need an internal collection
        # (:collection:`debusine:workflow-internal`)
        if parent is None:
            work_request.internal_collection = Collection.objects.create(
                name=f"workflow-{work_request.id}",
                category=CollectionCategory.WORKFLOW_INTERNAL,
                workspace=work_request.workspace,
                retains_artifacts=Collection.RetainsArtifacts.WORKFLOW,
            )
            work_request.save()

        if validate:
            # Lookup the orchestrator
            workflow_cls = Workflow.from_name(template.task_name)

            # Instantiate the orchestrator
            orchestrator = workflow_cls(work_request)

            # Thorough input validation
            orchestrator.validate_input()

        return work_request

    def create_synchronization_point(
        self,
        *,
        parent: "WorkRequest",
        step: str,
        display_name: str | None = None,
        status: _WorkRequestStatuses | None = None,
    ) -> "WorkRequest":
        """
        Create a synchronization point WorkRequest.

        A parent is always required, as a synchronization point only makes
        sense as part of a workflow.
        """
        # Import here to prevent circular imports
        from debusine.server.workflows.models import WorkRequestWorkflowData

        return self.create(
            workspace=parent.workspace,
            parent=parent,
            created_by=parent.created_by,
            status=status or WorkRequest.Statuses.PENDING,
            task_type=TaskTypes.INTERNAL,
            task_name="synchronization_point",
            task_data={},
            priority_base=parent.priority_effective,
            workflow_data_json=WorkRequestWorkflowData(
                step=step, display_name=display_name or step
            ).dict(exclude_unset=True),
        )


class _RuntimeStatuses(models.TextChoices):
    NEEDS_INPUT = "needs_input", "Needs Input"
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    PENDING = "pending", "Pending"
    ABORTED = "aborted", "Aborted"
    COMPLETED = "completed", "Completed"
    BLOCKED = "blocked", "Blocked"


class WorkRequest(models.Model):
    """
    Database model of a request to execute a task.

    Time-consuming operations offloaded to Workers and using
    Artifacts (and associated Files) as input and output.

    Submission API needs to check if the request is valid using
    ontological rules - e.g. whether the specified distribution for
    a build task exists.

    Avoid exposing the status of tasks to the admin interface to avoid
    runaway changes whilst the scheduler process is running.

    The WorkRequest uses the non-Django tasks module to do the checks on
    whether a task can run on a particular worker.

    WorkRequest State Machine
    =========================

    New WorkRequest database entries default to
    ``WorkRequest.Statuses.PENDING``.

    Once the WorkRequest is assigned to a worker and is running starts running
    the status is changed to ``WorkRequest.Statuses.RUNNING``.

    If the WorkRequest is aborted, the Scheduled.Task status is
    ``WorkRequest.Statuses.ABORTED``.

    If the task finish on the Worker the WorkRequest status will be
    ``WorkRequest.Statuses.COMPLETED`` and a WorkRequest.Result is then set,
    ``WorkRequest.Results.PASSED`` or ``WorkRequest.Results.FAILED``.

    .. graphviz::

       digraph {
          Statuses_PENDING -> Statuses_RUNNING -> Statuses_COMPLETED;
          Statuses_PENDING -> Statuses_COMPLETED;
          Statuses_PENDING -> Statuses_ABORTED;
          Statuses_PENDING -> Statuses_RUNNING -> Statuses_ABORTED;
       }

    ``WorkRequest.started_at`` is set when the WorkRequest moves from
    ``WorkRequest.Statuses.PENDING`` to ``WorkRequest.Statuses.RUNNING``.
    ``WorkRequest.completed_at`` is set when the Task moves from
    ``WorkRequest.Statuses.RUNNING`` to ``WorkRequest.Statuses.COMPLETED``.
    """

    objects = WorkRequestManager.from_queryset(WorkRequestQuerySet)()
    Roles: TypeAlias = WorkRequestRoles

    Statuses: TypeAlias = _WorkRequestStatuses
    RuntimeStatuses: TypeAlias = _RuntimeStatuses
    Results: TypeAlias = WorkRequestResults

    class UnblockStrategy(models.TextChoices):
        DEPS = "deps", "Dependencies have completed"
        MANUAL = "manual", "Manually unblocked"

    workspace = models.ForeignKey("Workspace", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    # workflow_last_activity_at was introduced to support UI changes and
    # could be dropped in the future to have a different way to support
    # UI (or, if the UI changes and is not needed anymore: this field could
    # be deleted).
    #
    # Use it only in the context of UI, and bear in mind it is a strong
    # candidate for refactoring.
    #
    # See also #656
    workflow_last_activity_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey("User", on_delete=models.PROTECT)
    aborted_by = models.ForeignKey(
        "User",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="aborted_work_requests",
    )
    status = models.CharField(
        max_length=9,
        choices=Statuses.choices,
        default=Statuses.PENDING,
        editable=False,
    )
    # workflow_runtime_status was introduced to support UI changes and
    # could be dropped in the future to have a different way to support
    # UI (or, if the UI changes and is not needed anymore: this field could
    # be deleted).
    #
    # Use it only in the context of UI, and bear in mind it is a strong
    # candidate for refactoring.
    #
    # See also #656
    workflow_runtime_status = models.CharField(
        max_length=11,
        choices=RuntimeStatuses.choices,
        editable=False,
        blank=True,
        null=True,
    )
    # Flag indicating that debusine.server.celery.update_workflows needs to
    # update workflow_last_activity_at and workflow_runtime_status on
    # workflows containing this work request.
    workflows_need_update = models.BooleanField(default=False)
    result = models.CharField(
        max_length=7,
        choices=Results.choices,
        default=Results.NONE,
        editable=False,
    )
    # any one work request can only be on one worker
    # even if the worker can handle multiple work request.
    worker = models.ForeignKey(
        "Worker",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="assigned_work_requests",
    )
    task_type = models.CharField(
        max_length=8,
        choices=TaskTypes.choices,
        default=TaskTypes.WORKER,
        editable=False,
        verbose_name="Type of task to execute",
    )
    task_name = models.CharField(
        max_length=100, verbose_name='Name of the task to execute'
    )
    task_data = JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    dynamic_task_data = JSONField(
        null=True, blank=True, encoder=DjangoJSONEncoder
    )
    priority_base = models.IntegerField(
        default=0, help_text="Base priority of this work request"
    )
    priority_adjustment = models.IntegerField(
        default=0,
        help_text=(
            "Administrator adjustment to the priority of this work request"
        ),
    )
    output_data_json = JSONField(
        null=True,
        blank=True,
        db_column="output_data",
        encoder=DjangoJSONEncoder,
    )

    # Workflows
    unblock_strategy = models.CharField(
        max_length=6,
        choices=UnblockStrategy.choices,
        default=UnblockStrategy.DEPS,
        editable=False,
    )
    # workflow hierarchy tree (optional, null if stand-alone)
    parent = models.ForeignKey(
        "self",
        # WorkRequests are only deleted through expiration, use CASCADE
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="children",
    )
    # order of execution within a workflow hierarchy (optional)
    dependencies = models.ManyToManyField(
        "self", symmetrical=False, related_name="reverse_dependencies"
    )
    workflow_template = models.ForeignKey(
        "WorkflowTemplate", on_delete=models.SET_NULL, null=True, blank=True
    )
    workflow_data_json = JSONField(
        default=dict,
        blank=True,
        db_column="workflow_data",
        encoder=DjangoJSONEncoder,
    )
    event_reactions_json = JSONField(
        default=dict,
        blank=True,
        db_column="event_reactions",
        encoder=DjangoJSONEncoder,
    )
    internal_collection = models.OneToOneField(
        "Collection",
        related_name="workflow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    expiration_delay = models.DurationField(blank=True, null=True)
    supersedes = models.OneToOneField(
        "WorkRequest",
        on_delete=models.SET_NULL,
        related_name="superseded",
        null=True,
        blank=True,
    )
    configured_task_data = JSONField(
        null=True,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="task_data with task configuration applied",
    )
    version = models.IntegerField(
        default=0,
        help_text="version of the code that computed the dynamic task data",
    )

    class Meta(TypedModelMeta):
        indexes = [
            # Handles the main scheduler query.
            models.Index(
                (F("priority_base") + F("priority_adjustment")).desc(),
                "created_at",
                name="%(app_label)s_%(class)s_pending_idx",
                condition=Q(status=_WorkRequestStatuses.PENDING),
            ),
            # Handles queries from workers.
            models.Index(
                "worker",
                name="%(app_label)s_%(class)s_worker_idx",
                condition=Q(
                    status__in=(
                        _WorkRequestStatuses.PENDING,
                        _WorkRequestStatuses.RUNNING,
                    )
                ),
            ),
            # Handles debusine.server.celery.update_workflows.
            models.Index(
                "workflows_need_update",
                name="%(app_label)s_%(class)s_wf_update_idx",
            ),
        ]
        permissions = [
            (
                "manage_workrequest_priorities",
                "Can set positive priority adjustments on work requests",
            )
        ]
        ordering = ["id"]

    def __str__(self) -> str:
        """Return the id of the WorkRequest."""
        return str(self.id)

    def get_absolute_url(self) -> str:
        """Return an absolute URL to this work request."""
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.workspace.scope.name):
            return reverse(
                "workspaces:work_requests:detail",
                kwargs={"wname": self.workspace.name, "pk": self.pk},
            )

    def get_absolute_url_retry(self) -> str:
        """Return an absolute URL to retry this work request."""
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.workspace.scope.name):
            return reverse(
                "workspaces:work_requests:retry",
                kwargs={"wname": self.workspace.name, "pk": self.pk},
            )

    def get_absolute_url_abort(self) -> str:
        """Return an absolute URL to abort this work request."""
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.workspace.scope.name):
            return reverse(
                "workspaces:work_requests:abort",
                kwargs={"wname": self.workspace.name, "pk": self.pk},
            )

    def get_absolute_url_unblock(self) -> str:
        """Return an absolute URL to unblock this work request."""
        from debusine.server.scopes import urlconf_scope

        with urlconf_scope(self.workspace.scope.name):
            return reverse(
                "workspaces:work_requests:unblock",
                kwargs={"wname": self.workspace.name, "pk": self.pk},
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the current instance."""
        if self._state.adding:
            self.process_event_reactions("on_creation")
        super().save(*args, **kwargs)

    def has_role(self, user: PermissionUser, role: WorkRequestRoles) -> bool:
        """Check if the user has the given role on this Workspace."""
        # Honor implied_by_public immediately as it's independent from the user
        if role.implied_by_public and self.workspace.public:
            return True

        if not user or not user.is_authenticated:
            return False

        if role.implied_by_creator and user == self.created_by:
            return True

        if context.user == user and context.workspace == self.workspace:
            # Allowed as implied by scope roles the user has
            if not context.scope_roles.isdisjoint(role.implied_by_scope_roles):
                return True

            # Allowed as implied by workspace roles the user has
            if not context.workspace_roles.isdisjoint(
                role.implied_by_workspace_roles
            ):
                return True

            # The context has enough information to know that the user does not
            # have the given role on this workspace
            return False
        return (
            WorkRequest.objects.with_role(user, role)
            .filter(pk=self.pk)
            .exists()
        )

    def get_roles(self, user: PermissionUser) -> frozenset[WorkRequestRoles]:
        """
        Get the effective roles of the user on this workspace.

        If you invoke this method on a whole queryset, you greatly reduce the
        number of queries by annotating the queryset using
        ``with_role_annotations``.
        """
        roles: list[WorkRequestRoles] = []
        if ((roles_for := getattr(self, "roles_for", None)) is not None) and (
            (
                roles_for
                and user
                and user.is_authenticated
                and int(roles_for) == user.pk
            )
            or (not roles_for and (not user or not user.is_authenticated))
        ):
            # Use roles from annotations
            for role in WorkRequestRoles:
                if getattr(self, f"is_{role}", False):
                    roles.append(role)
        else:
            # Fallback on querying each role
            for role in WorkRequestRoles:
                if self.has_role(user, role):
                    roles.append(role)
        return WorkRequestRoles.from_iterable(roles)

    @permission_check(
        "{user} cannot display {resource}",
        workers=Allow.ALWAYS,
        anonymous=Allow.PASS,
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the work request can be displayed."""
        return self.has_role(user, WorkRequest.Roles.VIEWER)

    @permission_check("{user} cannot retry {resource}", workers=Allow.ALWAYS)
    def can_retry(self, user: PermissionUser) -> bool:
        """Check if the work request can be retried."""
        return self.has_role(user, WorkRequestRoles.CONTRIBUTOR)

    @permission_check("{user} cannot abort {resource}")
    def can_abort(self, user: PermissionUser) -> bool:
        """Check if the work request can be aborted."""
        return self.has_role(user, WorkRequestRoles.CREATOR)

    @permission_check("{user} cannot unblock {resource}")
    def can_unblock(self, user: PermissionUser) -> bool:
        """Check if the work request can be unblocked."""
        return self.has_role(user, WorkRequestRoles.OWNER)

    def set_current(self) -> None:
        """Set the execution context to this scope/user/workspace."""
        context.set_scope(self.workspace.scope)
        context.set_user(self.created_by)
        self.workspace.set_current()

    @property
    def used_task_data(self) -> Any:
        """Return the task data used to run this task."""
        if self.configured_task_data is not None:
            return self.configured_task_data
        else:
            return self.task_data

    @property
    def output_data(self) -> OutputData | None:
        """Access output_data_json as a pydantic structure."""
        return (
            None
            if self.output_data_json is None
            else OutputData(**self.output_data_json)
        )

    @output_data.setter
    def output_data(self, value: OutputData | None) -> None:
        """Set output_data_json from a pydantic structure."""
        self.output_data_json = (
            None if value is None else value.dict(exclude_unset=True)
        )

    @property
    def workflow_data(self) -> "WorkRequestWorkflowData":
        """Access workflow_data_json as a python structure."""
        # Import here to avoid a circular dependency
        from debusine.server.workflows.models import WorkRequestWorkflowData

        return WorkRequestWorkflowData(**self.workflow_data_json)

    @workflow_data.setter
    def workflow_data(self, value: "WorkRequestWorkflowData") -> None:
        """Set workflow_data_json from a python structure."""
        self.workflow_data_json = model_to_json_serializable_dict(
            value, exclude_unset=True
        )

    @property
    def event_reactions(self) -> EventReactions:
        """Access event_reactions_json as a pydantic structure."""
        return EventReactions(**self.event_reactions_json)

    @event_reactions.setter
    def event_reactions(self, value: EventReactions) -> None:
        """Set event_reactions_json from a pydantic structure."""
        self.event_reactions_json = value.dict()

    def add_event_reaction(
        self,
        event_name: Literal[
            "on_creation",
            "on_unblock",
            "on_assignment",
            "on_success",
            "on_failure",
        ],
        action: EventReaction,
    ) -> None:
        """Add an event reaction."""
        event_reactions = self.event_reactions
        if action not in getattr(event_reactions, event_name):
            getattr(event_reactions, event_name).append(action)
            self.event_reactions = event_reactions
            self.save()

    @contextmanager
    def scheduling_disabled(self) -> Generator[None, None, None]:
        """Temporarily disable scheduling on changes to this work request."""
        try:
            # See debusine.server.scheduler._work_request_changed.
            self._disable_signals = True
            yield
        finally:
            del self._disable_signals

    def create_child(
        self,
        task_name: str,
        status: Statuses = Statuses.BLOCKED,
        task_type: TaskTypes = TaskTypes.WORKER,
        task_data: BaseTaskData | dict[str, Any] | None = None,
        workflow_data: Optional["WorkRequestWorkflowData"] = None,
        event_reactions: EventReactions | None = None,
        relative_priority: int = 0,
    ) -> "WorkRequest":
        """Create a child WorkRequest."""
        if self.task_type != TaskTypes.WORKFLOW:
            raise ValueError("Only workflows may have child work requests.")
        if isinstance(task_data, BaseTaskData):
            task_data_json = model_to_json_serializable_dict(
                task_data, exclude_unset=True
            )
        else:
            task_data_json = task_data or {}
        if "task_configuration" not in task_data_json and (
            current_task_config := self.task_data.get("task_configuration")
        ):
            task_data_json["task_configuration"] = current_task_config
        child = WorkRequest.objects.create(
            workspace=self.workspace,
            parent=self,
            created_by=self.created_by,
            status=status,
            task_type=task_type,
            task_name=task_name,
            task_data=task_data_json,
            priority_base=self.priority_effective + relative_priority,
            workflow_data_json=(
                workflow_data.dict(exclude_unset=True) if workflow_data else {}
            ),
            event_reactions_json=(
                event_reactions.dict() if event_reactions else {}
            ),
        )
        child.full_clean()

        workflow_status = compute_workflow_runtime_status(self)
        self.workflow_runtime_status = workflow_status
        self.save()

        return child

    def can_satisfy_dynamic_data(self) -> bool:
        """Check if dynamic data can still be looked up."""
        # Import here to prevent an import loop
        from debusine.db.models.task_database import TaskDatabase

        try:
            task = self.get_task()
        except TaskConfigError:
            return False
        try:
            task.compute_dynamic_data(TaskDatabase(self))
        except LookupError:
            return False
        return True

    def is_aborted_failed(self) -> bool:
        """Check if the work request was aborted or failed."""
        return self.status == WorkRequest.Statuses.ABORTED or (
            self.status == WorkRequest.Statuses.COMPLETED
            and self.result == WorkRequest.Results.FAILURE
        )

    def _verify_retry(self) -> None:
        """
        Check if a work request can be retried.

        :raises CannotRetry: raised with an explanation of why it cannot be
                             retried
        """
        if self.task_type not in {
            TaskTypes.WORKFLOW,
            TaskTypes.WORKER,
            TaskTypes.INTERNAL,
            TaskTypes.SIGNING,
        }:
            raise CannotRetry(
                "Only workflow, worker, internal, or signing tasks"
                " can be retried"
            )

        if hasattr(self, "superseded"):
            raise CannotRetry("Cannot retry old superseded tasks")

        if not self.is_aborted_failed():
            raise CannotRetry("Only aborted or failed tasks can be retried")

        if self.task_type in {TaskTypes.INTERNAL, TaskTypes.WORKFLOW}:
            return

        if not self.can_satisfy_dynamic_data():
            raise CannotRetry("Task dependencies cannot be satisfied")

    def verify_retry(self) -> bool:
        """Check if this work request can be retried."""
        if not self.can_retry(context.user):
            return False
        try:
            self._verify_retry()
        except CannotRetry:
            return False
        return True

    def _retry_workflow(self, reason: WorkRequestRetryReason) -> Self:
        """Retry logic for workflows."""
        # Import here to prevent circular imports
        from debusine.server.workflows.base import orchestrate_workflow

        # Set the workflow back to running, so that further actions execute in
        # a running workflow
        self.status = self.Statuses.PENDING
        self.mark_running()

        # Update the retry count
        workflow_data = self.workflow_data
        if reason in {
            WorkRequestRetryReason.WORKER_FAILED,
            WorkRequestRetryReason.DELAY,
        }:
            workflow_data.retry_count += 1
        else:
            workflow_data.retry_count = 0
        self.workflow_data = workflow_data
        self.save()

        try:
            # TODO: workflow orchestrators may be slow; this should move to
            # a Celery task
            # Give the workflow a chance to update its graph
            orchestrate_workflow(self)

            # Retry failed child work requests
            for child in (
                WorkRequest.objects.aborted_or_failed()
                .filter(parent=self)
                .order_by("id")
            ):
                try:
                    child.retry(reason=reason)
                except CannotRetry as exc:
                    raise CannotRetry(
                        f"child work request {child.pk} cannot be retried"
                    ) from exc
        finally:
            self.maybe_finish_workflow()

        return self

    def _retry_supersede(self, reason: WorkRequestRetryReason) -> "WorkRequest":
        """Retry logic superseding work requests."""
        work_request = WorkRequest.objects.create(
            workspace=self.workspace,
            created_by=self.created_by,
            status=WorkRequest.Statuses.BLOCKED,
            task_type=self.task_type,
            task_name=self.task_name,
            task_data=self.task_data,
            priority_base=self.priority_base,
            priority_adjustment=self.priority_adjustment,
            unblock_strategy=self.unblock_strategy,
            parent=self.parent,
            workflow_template=self.workflow_template,
            workflow_data_json=self.workflow_data_json,
            event_reactions_json=self.event_reactions_json,
            expiration_delay=self.expiration_delay,
            supersedes=self,
        )

        # Copy forward dependencies
        for dep in self.dependencies.all():
            work_request.add_dependency(dep)

        # Update any reverse-dependencies of the old work request to point to
        # the new one, and move them from pending to blocked
        for dep in self.reverse_dependencies.all():
            dep.dependencies.remove(self)
            dep.add_dependency(work_request)

        # If the task was part of an aborted workflow, set it back to running
        if (workflow := work_request.parent) and workflow.is_aborted_failed():
            # We cannot use mark_running, as it would fail to run when the
            # workflow is failed or aborted
            workflow.status = WorkRequest.Statuses.RUNNING
            workflow.result = WorkRequest.Results.NONE
            workflow.completed_at = None
            workflow.save()

        # Update the retry count
        workflow_data = work_request.workflow_data
        if reason in {
            WorkRequestRetryReason.WORKER_FAILED,
            WorkRequestRetryReason.DELAY,
        }:
            workflow_data.retry_count += 1
        else:
            workflow_data.retry_count = 0
        work_request.workflow_data = workflow_data
        work_request.save()

        if work_request.can_be_automatically_unblocked():
            work_request.mark_pending()

        # Update promises associated with the previous work request
        if (
            self.parent is not None
            and self.parent.internal_collection is not None
        ):
            for debusine_promise in CollectionItem.objects.filter(
                category=BareDataCategory.PROMISE,
                parent_collection=self.parent.internal_collection,
                data__promise_work_request_id=self.id,
            ):
                debusine_promise.data["promise_work_request_id"] = (
                    work_request.id
                )
                debusine_promise.save()

        self.schedule_workflow_update()

        return work_request

    def retry(
        self, reason: WorkRequestRetryReason = WorkRequestRetryReason.MANUAL
    ) -> "WorkRequest":
        """
        Create a WorkRequest that supersedes this one.

        :param reason: The reason for retrying this work request.  If it is
          ``WORKER_FAILED``, then only allow the retry if the retry count is
          less than :py:const:`MAX_AUTOMATIC_RETRIES`.  If it is either
          ``WORKER_FAILED`` or ``DELAY``, then increment the work request's
          ``retry_count``; otherwise, set it back to 0.
        """
        enforce(self.can_retry)
        self._verify_retry()

        if (
            reason == WorkRequestRetryReason.WORKER_FAILED
            and self.workflow_data.retry_count >= MAX_AUTOMATIC_RETRIES
        ):
            raise CannotRetry("Maximum number of automatic retries exceeded")

        if self.task_type == TaskTypes.WORKFLOW:
            return self._retry_workflow(reason=reason)
        else:
            return self._retry_supersede(reason=reason)

    def _verify_abort(self) -> None:
        """
        Check if this work request can be aborted.

        :raises CannotAbort: if the work request cannot be aborted.
        """
        if hasattr(self, "superseded"):
            raise CannotAbort("Cannot abort old superseded tasks")

        if self.status not in {
            WorkRequest.Statuses.PENDING,
            WorkRequest.Statuses.BLOCKED,
            WorkRequest.Statuses.RUNNING,
        }:
            raise CannotAbort(
                "Only pending, blocked, or running tasks can be aborted"
            )

    def verify_abort(self) -> bool:
        """Check if this work request can be aborted."""
        if not self.can_abort(context.user):
            return False
        try:
            self._verify_abort()
        except CannotAbort:
            return False
        return True

    def abort(self) -> None:
        """
        Abort a work request.

        If the work request is a workflow, then recursively abort its child
        work requests too.
        """
        enforce(self.can_abort)
        user = context.require_user()
        # Enforcing `can_abort` ensures that the user can't be anonymous by
        # this point.
        assert user.is_authenticated
        self._verify_abort()

        for work_request in WorkRequest.objects.filter(
            id__in=workflow_flattened(
                self, include_workflows=True, include_invisible=True
            )
            .filter(
                superseded__isnull=True,
                status__in={
                    WorkRequest.Statuses.PENDING,
                    WorkRequest.Statuses.BLOCKED,
                    WorkRequest.Statuses.RUNNING,
                },
            )
            .values_list("id", flat=True)
        ).select_for_update():
            work_request.mark_aborted(user=user)
            # TODO: If the work request was assigned to a worker, we should
            # tell the worker so that it can abandon execution
            # (https://salsa.debian.org/freexian-team/debusine/-/issues/824).
            work_request.worker = None
            work_request.save()

    def clean(self) -> None:
        """
        Ensure that task data is valid for this task name.

        :raise ValidationError: for invalid data.
        """
        if not isinstance(self.task_data, dict):
            raise ValidationError(
                {"task_data": "task data must be a dictionary"}
            )

        match self.task_type:
            case (
                TaskTypes.WORKER
                | TaskTypes.SERVER
                | TaskTypes.SIGNING
                | TaskTypes.WAIT
            ):
                try:
                    task_cls = BaseTask.class_from_name(
                        self.task_type, self.task_name
                    )
                except (KeyError, ValueError) as e:
                    raise ValidationError(
                        {
                            "task_name": f"{self.task_name}:"
                            f" invalid {self.task_type} task name"
                        }
                    ) from e

                try:
                    task_cls(task_data=self.task_data)
                except TaskConfigError as e:
                    raise ValidationError(
                        {
                            "task_data": f"invalid {self.task_type}"
                            f" task data: {e}"
                        }
                    ) from e
            case TaskTypes.WORKFLOW:
                # Import here to prevent circular imports
                from debusine.server.workflows import Workflow

                try:
                    workflow_cls = Workflow.from_name(self.task_name)
                except (KeyError, ValueError) as e:
                    raise ValidationError(
                        {
                            "task_name": f"{self.task_name}:"
                            f" invalid workflow name"
                        }
                    ) from e

                try:
                    workflow_cls(self)
                except TaskConfigError as e:
                    raise ValidationError(
                        {"task_data": f"invalid workflow data: {e}"}
                    ) from e

                # TODO: do we want to run expensive tests here
                # (Workflow.validate_input), like looking up the types of
                # references artifacts to validate them?
            case TaskTypes.INTERNAL:
                if self.task_name not in ("synchronization_point", "workflow"):
                    raise ValidationError(
                        {
                            "task_name": f"{self.task_name}:"
                            " invalid task name for internal task"
                        }
                    )
                # Without this pass, python coverage is currently unable to
                # detect that code does flow through here
                pass
            case _:
                raise NotImplementedError(
                    f"task type {self.task_type} not yet supported"
                )

        match self.task_type:
            case TaskTypes.WAIT:
                if self.workflow_data.needs_input is None:
                    raise ValidationError("WAIT tasks must specify needs_input")
                # Without this pass, python coverage is currently unable to
                # detect that code does flow through here
                pass

    def get_task(self, worker: "Worker | None" = None) -> BaseTask[Any, Any]:
        """
        Instantiate the Task for this work request.

        :param worker: if set, the worker that this work request is intended
          to run on; `worker_host_architecture` will be set to its host
          architecture
        :raise InternalTaskError: if the work request is for an internal
          task other than a workflow callback
        """
        # Import here to prevent circular imports
        from debusine.server.workflows import Workflow

        match (self.task_type, self.task_name):
            case (
                (TaskTypes.WORKER, _)
                | (TaskTypes.SERVER, _)
                | (TaskTypes.SIGNING, _)
                | (TaskTypes.WAIT, _)
            ):
                task_cls = BaseTask.class_from_name(
                    TaskTypes(self.task_type), self.task_name
                )
                task = task_cls(
                    task_data=self.used_task_data,
                    dynamic_task_data=self.dynamic_task_data,
                )
            case (TaskTypes.WORKFLOW, _):
                workflow_cls = Workflow.from_name(self.task_name)
                task = workflow_cls(self)
            case (TaskTypes.INTERNAL, "workflow"):
                if (workflow := self.parent) is None:
                    raise InternalTaskError(
                        "Workflow callback is not contained in a workflow"
                    )
                assert workflow.task_type == TaskTypes.WORKFLOW
                workflow_cls = Workflow.from_name(workflow.task_name)
                task = workflow_cls(workflow)
            case (TaskTypes.INTERNAL, _):
                raise InternalTaskError(
                    "Internal tasks other than workflow callbacks cannot be "
                    "instantiated"
                )
            case _ as unreachable:  # noqa: F841
                raise NotImplementedError(
                    f"task type {self.task_type} not yet supported"
                )

        if worker is not None:
            task.worker_host_architecture = worker.metadata().get(
                "system:host_architecture"
            )

        return task

    def mark_running(self) -> bool:
        """Worker has begun executing the task."""
        if (
            self.task_type
            in {TaskTypes.WORKER, TaskTypes.SERVER, TaskTypes.SIGNING}
            and self.worker is None
        ):
            logger.debug(
                "Cannot mark WorkRequest %s as running: it does not have "
                "an assigned worker",
                self.pk,
            )
            return False

        if self.status == self.Statuses.RUNNING:
            # It was already running - nothing to do
            return True

        if self.status != self.Statuses.PENDING:
            logger.debug(
                "Cannot mark as running - current status %s", self.status
            )
            return False

        if self.worker is not None:
            work_requests_running_for_worker = WorkRequest.objects.running(
                worker=self.worker
            )

            # There is a possible race condition here.  This check (and
            # other checks in this class) currently help to avoid
            # development mistakes not database full integrity
            if (
                work_requests_running_for_worker.count()
                >= self.worker.concurrency
            ):
                logger.debug(
                    "Cannot mark WorkRequest %s as running - the assigned "
                    "worker %s is running too many other WorkRequests: %s",
                    self.pk,
                    self.worker,
                    list(work_requests_running_for_worker.order_by("id")),
                )
                return False

        self.started_at = timezone.now()
        self.status = self.Statuses.RUNNING
        self.save()

        self.schedule_workflow_update()

        logger.debug("Marked WorkRequest %s as running", self.pk)
        return True

    def mark_completed(
        self,
        result: "WorkRequest.Results",
        output_data: OutputData | None = None,
    ) -> bool:
        """Worker has finished executing the task."""
        if self.status not in (self.Statuses.PENDING, self.Statuses.RUNNING):
            logger.debug(
                "Cannot mark WorkRequest %s as completed: current status is %s",
                self.pk,
                self.status,
            )
            return False
        if result == self.Results.NONE:
            raise AssertionError("result cannot be NONE")

        self.result = result
        self.completed_at = timezone.now()
        self.status = self.Statuses.COMPLETED
        if output_data is not None:
            if self.output_data is None:
                self.output_data = output_data
            else:
                self.output_data = self.output_data.merge(output_data)
        self.save()

        if (
            output_data is not None
            and output_data.runtime_statistics is not None
            and output_data.runtime_statistics.duration is not None
            and self.worker is not None
            and self.worker.worker_pool is not None
        ):
            WorkerPoolTaskExecutionStatistics.objects.create(
                worker_pool=self.worker.worker_pool,
                worker=self.worker,
                scope=self.workspace.scope,
                runtime=output_data.runtime_statistics.duration,
            )

        self.schedule_workflow_update()

        logger.debug("Marked WorkRequest %s as completed", self.pk)
        # mark dependencies ready before sending notification
        self.unblock_reverse_dependencies()
        match self.result:
            case self.Results.SUCCESS:
                self.process_event_reactions("on_success")
            case self.Results.FAILURE | self.Results.ERROR:
                self.process_event_reactions("on_failure")
            case self.Results.SKIPPED:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        if self.parent is not None:
            self.parent.maybe_finish_workflow()
        return True

    def process_event_reactions(
        self,
        event_name: Literal[
            "on_creation",
            "on_unblock",
            "on_assignment",
            "on_success",
            "on_failure",
        ],
    ) -> None:
        """Process list of actions to perform."""
        actions = self.get_triggered_actions(event_name)
        self.process_skip_if_lookup_result_changed(
            actions[ActionTypes.SKIP_IF_LOOKUP_RESULT_CHANGED]
        )
        if event_name in {"on_success", "on_failure"}:
            notifications.notify_work_request_completed(
                self, actions[ActionTypes.SEND_NOTIFICATION]
            )
        self.process_update_collection_with_artifacts(
            actions[ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS]
        )
        self.process_update_collection_with_data(
            actions[ActionTypes.UPDATE_COLLECTION_WITH_DATA]
        )
        self.process_retry_with_delays(actions[ActionTypes.RETRY_WITH_DELAYS])
        self.process_record_in_task_history(
            actions[ActionTypes.RECORD_IN_TASK_HISTORY]
        )

    def get_triggered_actions(
        self,
        event_name: Literal[
            "on_creation",
            "on_unblock",
            "on_assignment",
            "on_success",
            "on_failure",
        ],
    ) -> dict[str, list[EventReaction]]:
        """
        Filter events to trigger, grouped by type.

        :param event_name: the name of the event being triggered.
        """
        actions: dict[str, list[EventReaction]] = {
            action: [] for action in ActionTypes
        }
        try:
            task = self.get_task()
        except Exception:
            task_event_reactions = []
        else:
            task_event_reactions = task.get_event_reactions(event_name)
        for action in chain(
            task_event_reactions, getattr(self.event_reactions, event_name)
        ):
            action_type = action.action
            actions[action_type].append(action)
        return actions

    def process_skip_if_lookup_result_changed(
        self, actions: list[EventReaction]
    ) -> None:
        """Skip this work request if the result of a lookup has changed."""
        # Import here to prevent circular imports.
        from debusine.server.collections.lookup import lookup_single

        workflow_root = self.get_workflow_root()
        should_skip = False
        for skip in actions:
            assert isinstance(skip, ActionSkipIfLookupResultChanged)
            try:
                result = lookup_single(
                    skip.lookup,
                    self.workspace,
                    user=self.created_by,
                    workflow_root=workflow_root,
                )
            except KeyError:
                result = None
            except LookupError as e:
                logger.error("%s in WorkRequest %s", e, self.pk)  # noqa: G200
                continue

            new_collection_item_id = (
                None
                if result is None or result.collection_item is None
                else result.collection_item.id
            )
            if new_collection_item_id == skip.collection_item_id:
                continue

            # Mark ourselves as complete, with a note about why.
            should_skip = True
            self.mark_completed(
                WorkRequest.Results.SKIPPED,
                output_data=OutputData(
                    skip_reason=f"Result of lookup {skip.lookup!r} changed"
                ),
            )

            if (
                skip.promise_name is not None
                and workflow_root is not None
                and result is not None
                and result.collection_item is not None
            ):
                assert workflow_root.internal_collection is not None

                # Use the lookup result to resolve the promise.
                if result.artifact is not None:
                    workflow_root.internal_collection.manager.add_artifact(
                        result.artifact,
                        user=self.created_by,
                        workflow=self.parent,
                        variables=result.collection_item.data,
                        name=skip.promise_name,
                        replace=True,
                    )
                else:
                    workflow_root.internal_collection.manager.add_bare_data(
                        BareDataCategory(result.collection_item.category),
                        user=self.created_by,
                        workflow=self.parent,
                        data=result.collection_item.data,
                        name=skip.promise_name,
                        replace=True,
                    )

        if should_skip:
            raise SkipWorkRequest

    def process_update_collection_with_artifacts(
        self, actions: list[EventReaction]
    ) -> None:
        """Update collection with artifacts following event_reactions."""
        # local import to avoid circular dependency
        from debusine.server.collections import (
            CollectionManagerInterface,
            ItemAdditionError,
            ItemRemovalError,
        )
        from debusine.server.collections.lookup import lookup_single

        for update in actions:
            assert isinstance(update, ActionUpdateCollectionWithArtifacts)
            try:
                collection = lookup_single(
                    update.collection,
                    self.workspace,
                    user=self.created_by,
                    workflow_root=self.get_workflow_root(),
                    expect_type=LookupChildType.COLLECTION,
                ).collection
            except LookupError as e:
                logger.error("%s in WorkRequest %s", e, self.pk)  # noqa: G200
                continue

            manager = CollectionManagerInterface.get_manager_for(collection)

            try:
                artifacts_to_add = self.artifact_set.filter(
                    **update.artifact_filters
                )
            except FieldError:
                logger.exception(
                    "Invalid update-collection-with-artifacts"
                    " artifact_filters in WorkRequest %s",
                    self.pk,
                )
                continue

            for artifact in artifacts_to_add:
                try:
                    expanded_variables = CollectionItem.expand_variables(
                        update.variables or {}, artifact.data
                    )
                except (KeyError, ValueError):
                    logger.exception(
                        "Invalid update-collection-with-artifacts variables "
                        "in WorkRequest %s",
                        self.pk,
                    )
                    continue

                if update.name_template is not None:
                    item_name = CollectionItem.expand_name(
                        update.name_template, expanded_variables
                    )
                else:
                    item_name = None

                try:
                    manager.add_artifact(
                        artifact,
                        user=self.created_by,
                        workflow=self.parent,
                        variables=expanded_variables,
                        name=item_name,
                        replace=True,
                        created_at=update.created_at,
                    )
                except (ItemAdditionError, ItemRemovalError):
                    logger.exception(
                        "Cannot replace or add artifact %s to collection %s"
                        " from WorkRequest %s",
                        artifact,
                        collection,
                        self.pk,
                    )

    def process_update_collection_with_data(
        self, actions: list[EventReaction]
    ) -> None:
        """Update collection with bare data following event_reactions."""
        # local import to avoid circular dependency
        from debusine.server.collections import (
            CollectionManagerInterface,
            ItemAdditionError,
            ItemRemovalError,
        )
        from debusine.server.collections.lookup import lookup_single

        for update in actions:
            assert isinstance(update, ActionUpdateCollectionWithData)
            try:
                collection = lookup_single(
                    update.collection,
                    self.workspace,
                    user=self.created_by,
                    workflow_root=self.get_workflow_root(),
                    expect_type=LookupChildType.COLLECTION,
                ).collection
            except LookupError as e:
                logger.error("%s in WorkRequest %s", e, self.pk)  # noqa: G200
                continue

            manager = CollectionManagerInterface.get_manager_for(collection)
            data = update.data or {}

            if update.name_template is not None:
                item_name = CollectionItem.expand_name(
                    update.name_template, data
                )
            else:
                item_name = None

            try:
                manager.add_bare_data(
                    update.category,
                    user=self.created_by,
                    workflow=self.parent,
                    data=data,
                    name=item_name,
                    replace=True,
                    created_at=update.created_at,
                )
            except (ItemAdditionError, ItemRemovalError):
                logger.exception(
                    "Cannot replace or add bare data of category %s to"
                    " collection %s from WorkRequest %s",
                    update.category,
                    collection,
                    self.pk,
                )

    @context.disable_permission_checks()
    def process_retry_with_delays(self, actions: list[EventReaction]) -> None:
        """Retry a work request with delays."""
        # Import here to prevent circular imports
        from debusine.server.tasks.wait.models import DelayData
        from debusine.server.workflows.models import WorkRequestWorkflowData

        for action in actions:
            assert isinstance(action, ActionRetryWithDelays)
            if self.parent is None:
                raise CannotRetry(
                    "retry-with-delays action may only be used in a workflow"
                )
            retry_count = self.workflow_data.retry_count
            if retry_count >= len(action.delays):
                continue
            m = ActionRetryWithDelays._delay_re.match(
                action.delays[retry_count]
            )
            # Checked by ActionRetryWithDelays.validate_delays.
            assert m is not None
            match m.group(2):
                case "m":
                    delay = timedelta(minutes=int(m.group(1)))
                case "h":
                    delay = timedelta(hours=int(m.group(1)))
                case "d":
                    delay = timedelta(days=int(m.group(1)))
                case "w":
                    delay = timedelta(weeks=int(m.group(1)))
                case _ as unreachable:
                    raise AssertionError(
                        f"Unexpected delay unit: {unreachable}"
                    )
            wr_delay = self.parent.create_child(
                task_type=TaskTypes.WAIT,
                task_name="delay",
                task_data=DelayData(delay_until=timezone.now() + delay),
                status=self.Statuses.PENDING,
                workflow_data=WorkRequestWorkflowData(needs_input=False),
            )
            wr_retried = self.retry(reason=WorkRequestRetryReason.DELAY)
            wr_retried.add_dependency(wr_delay)

    def process_record_in_task_history(
        self, actions: list[EventReaction]
    ) -> None:
        """Record a task run in a task-history collection."""
        if (
            actions
            and self.started_at is not None
            and self.dynamic_task_data is not None
            and self.output_data is not None
            and self.output_data.runtime_statistics is not None
        ):
            try:
                collection = self.workspace.get_singleton_collection(
                    user=self.created_by,
                    category=CollectionCategory.TASK_HISTORY,
                )
            except Collection.DoesNotExist:
                # No task-history collection; skip these actions.
                return

            default_subject: str | None = None
            default_context: str | None = None
            try:
                task = self.get_task()
            except Exception:
                pass
            else:
                assert task.dynamic_data is not None
                default_subject = task.dynamic_data.subject
                default_context = task.dynamic_data.runtime_context

            for action in actions:
                assert isinstance(action, ActionRecordInTaskHistory)
                collection.manager.add_bare_data(
                    BareDataCategory.HISTORICAL_TASK_RUN,
                    user=self.created_by,
                    workflow=self.parent,
                    data=DebusineHistoricalTaskRun(
                        task_type=TaskTypes(self.task_type),
                        task_name=self.task_name,
                        subject=action.subject or default_subject,
                        context=action.context or default_context,
                        timestamp=int(self.started_at.timestamp()),
                        work_request_id=self.id,
                        result=WorkRequestResults(self.result),
                        runtime_statistics=self.output_data.runtime_statistics,
                    ),
                )

    def mark_pending(self) -> bool:
        """Worker is ready for execution."""
        if self.status != self.Statuses.BLOCKED:
            logger.debug(
                "Cannot mark WorkRequest %s as pending: current status is %s",
                self.pk,
                self.status,
            )
            return False
        self.status = self.Statuses.PENDING
        self.save()

        self.schedule_workflow_update()

        logger.debug("Marked WorkRequest %s as pending", self.pk)
        self.process_event_reactions("on_unblock")
        return True

    def add_dependency(self, dependency: "WorkRequest") -> None:
        """Make this work request depend on another one."""
        if self.is_part_of_workflow:
            # Work requests in a workflow may only depend on other work
            # requests in the same workflow.
            my_root = self.get_workflow_root()
            assert my_root is not None
            if dependency.get_workflow_root() != my_root:
                raise ValueError(
                    "Work requests in a workflow may not depend on other work "
                    "requests outside that workflow"
                )
        self.dependencies.add(dependency)
        if (
            self.status == WorkRequest.Statuses.PENDING
            and self.unblock_strategy == WorkRequest.UnblockStrategy.DEPS
            and dependency.status != WorkRequest.Statuses.COMPLETED
        ):
            self.status = WorkRequest.Statuses.BLOCKED
        self.save()

    def maybe_finish_workflow(self) -> bool:
        """Update workflow status if its children are no longer in progress."""
        # Only relevant for running workflows where all work requests have been
        # either completed or aborted
        if (
            self.task_type != TaskTypes.WORKFLOW
            or self.status != WorkRequest.Statuses.RUNNING
            or self.has_children_in_progress
        ):
            return False

        # If there are aborted child requests, abort the workflow
        if (
            self.children.filter(superseded__isnull=True)
            .exclude(status=WorkRequest.Statuses.COMPLETED)
            .exists()
        ):
            self.mark_aborted()
            return True

        # If there are failed/errored child requests, fail the workflow
        if (
            self.children.filter(superseded__isnull=True)
            .exclude(
                Q(
                    result__in={
                        WorkRequest.Results.SUCCESS,
                        WorkRequest.Results.SKIPPED,
                    }
                )
                | Q(workflow_data_json__contains={"allow_failure": True})
            )
            .exists()
        ):
            self.mark_completed(WorkRequest.Results.FAILURE)
            return True

        # All child requests succeeded
        self.mark_completed(WorkRequest.Results.SUCCESS)
        return True

    def can_be_unblocked(self) -> bool:
        """Return True iff this work request can be unblocked at all."""
        return (
            WorkRequest.objects.can_be_unblocked().filter(id=self.id).exists()
        )

    def can_be_automatically_unblocked(self) -> bool:
        """Return True iff this work request can be automatically unblocked."""
        return (
            WorkRequest.objects.can_be_automatically_unblocked()
            .filter(id=self.id)
            .exists()
        )

    def unblock_reverse_dependencies(self) -> None:
        """Unblock reverse dependencies."""
        # Shortcuts to keep line length sane
        r = WorkRequest.Results
        s = WorkRequest.Statuses
        allow_failure = self.workflow_data and self.workflow_data.allow_failure

        if self.result in {r.SUCCESS, r.SKIPPED} or allow_failure:
            # lookup work requests that depend on the completed work
            # request and unblock them if no other work request is
            # blocking them
            for (
                rdep
            ) in self.reverse_dependencies.can_be_automatically_unblocked():
                rdep.mark_pending()
        else:  # failure and !allow_failure
            for rdep in self.reverse_dependencies.filter(status=s.BLOCKED):
                rdep.mark_aborted()

    def unblock_workflow_children(self) -> None:
        """Unblock children of a workflow that has just started running."""
        for child in self.children.can_be_automatically_unblocked():
            child.mark_pending()

    def review_manual_unblock(
        self,
        *,
        user: "User",
        notes: str,
        action: "WorkRequestManualUnblockAction | None",
    ) -> None:
        """Review a work request awaiting manual unblocking."""
        # Import here to prevent circular imports
        from debusine.server.workflows.models import (
            WorkRequestManualUnblockAction,
            WorkRequestManualUnblockData,
            WorkRequestManualUnblockLog,
        )

        enforce(self.can_unblock)

        if self.unblock_strategy != WorkRequest.UnblockStrategy.MANUAL:
            raise CannotUnblock(
                f"Work request {self.id} cannot be manually unblocked"
            )
        if (
            not self.is_part_of_workflow
            or (workflow_data := self.workflow_data) is None
        ):
            raise CannotUnblock(
                f"Work request {self.id} is not part of a workflow"
            )
        if not self.can_be_unblocked():
            raise CannotUnblock(f"Work request {self.id} cannot be unblocked")

        if workflow_data.manual_unblock is None:
            workflow_data.manual_unblock = WorkRequestManualUnblockData()
        manual_unblock_log = list(workflow_data.manual_unblock.log)
        manual_unblock_log.append(
            WorkRequestManualUnblockLog(
                user_id=user.id,
                timestamp=timezone.now(),
                notes=notes,
                action=action,
            )
        )
        workflow_data.manual_unblock.log = manual_unblock_log
        self.workflow_data = workflow_data
        self.save()

        match action:
            case WorkRequestManualUnblockAction.ACCEPT:
                # Always succeeds, since we already checked that the current
                # status is BLOCKED (via `work_request.can_be_unblocked()`).
                if not self.mark_pending():
                    raise AssertionError(
                        "Work request could not be marked pending"
                    )
            case WorkRequestManualUnblockAction.REJECT:
                # The current implementation always aborts the work request.
                if not self.mark_aborted():
                    raise AssertionError("Work request could not be aborted")

    @property
    def requires_signature(self) -> bool:
        """True if this task is blocked on `debusine provide-signature`."""
        # TODO: At some point it may be worth having this delegate to a
        # property on the task, but for now this is good enough.
        return (
            self.task_type == TaskTypes.WAIT
            and self.task_name == "externaldebsign"
            and self.status == self.Statuses.RUNNING
        )

    def mark_aborted(self, *, user: "User | None" = None) -> bool:
        """
        Worker has aborted the task after request from UI.

        Task will typically be in CREATED or RUNNING status.
        """
        self.aborted_by = user
        self.completed_at = timezone.now()
        self.status = self.Statuses.ABORTED
        self.save()
        self.unblock_reverse_dependencies()
        if self.parent is not None:
            self.parent.maybe_finish_workflow()
        self.schedule_workflow_update()

        logger.debug(
            "Marked WorkRequest %s as aborted (from status %s)",
            self.pk,
            self.status,
        )
        return True

    def configure_for_worker(self, worker: "Worker") -> None:
        """Populate configured and dynamic task_data for this worker."""
        from debusine.db.models.task_database import TaskDatabase
        from debusine.server.collections.debusine_task_configuration import (
            apply_configuration,
        )

        # Import here to prevent circular import
        from debusine.server.collections.lookup import lookup_single

        self.configured_task_data = None
        self.dynamic_task_data = None

        # Instantiate the unconfigured task
        task = self.get_task(worker=worker)

        # Lookup the task-configuration collection to use
        config_collection: Collection | None = None
        if (task_configuration := task.data.task_configuration) is not None:
            # Look up the debusine:task-configuration collection
            try:
                lookup_result = lookup_single(
                    lookup=task_configuration,
                    workspace=self.workspace,
                    user=self.created_by,
                    default_category=CollectionCategory.TASK_CONFIGURATION,
                    workflow_root=self.get_workflow_root(),
                    expect_type=LookupChildType.COLLECTION,
                )
            except KeyError:
                if (
                    task_configuration
                    != BaseTaskData.DEFAULT_TASK_CONFIGURATION_LOOKUP
                ):
                    raise
            else:
                config_collection = lookup_result.collection

        # Compute the configured task data
        self.configured_task_data = model_to_json_serializable_dict(
            task.data, exclude_unset=True
        )

        # Compute dynamic task data once to extract subject and
        # configuration_context
        # TODO: these values can be computed in a separate methods if the rest
        # of compute_dynamic_data becomes too expensive to run twice
        early_dynamic_task_data = task.compute_dynamic_data(TaskDatabase(self))

        if config_collection is not None:
            apply_configuration(
                self.configured_task_data,
                config_collection,
                task.TASK_TYPE,
                task.name,
                early_dynamic_task_data.subject,
                early_dynamic_task_data.configuration_context,
            )

        # Reinstantiate using configured_task_data
        try:
            configured_task = self.get_task(worker=worker)
        except TaskConfigError as exc:
            exc.add_parent_message("Configured task data is invalid")
            raise

        dynamic_task_data = configured_task.compute_dynamic_data(
            TaskDatabase(self)
        )
        if config_collection is not None:
            dynamic_task_data.task_configuration_id = config_collection.pk
        self.dynamic_task_data = model_to_json_serializable_dict(
            dynamic_task_data, exclude_unset=True
        )

    def assign_worker(self, worker: "Worker") -> None:
        """Assign worker and save it."""
        self.configure_for_worker(worker)
        self.worker = worker
        self.save()
        notifications.notify_work_request_assigned(self)
        self.process_event_reactions("on_assignment")

    def de_assign_worker(self) -> bool:
        """
        De-assign a worker from this work request.

        Only allowed if the status is RUNNING or PENDING.
        """
        if self.status not in {
            WorkRequest.Statuses.RUNNING,
            WorkRequest.Statuses.PENDING,
        }:
            logger.debug(
                "WorkRequest %d cannot be de-assigned: current status: %s",
                self.pk,
                self.status,
            )
            return False

        self.worker = None
        self.started_at = None
        self.status = WorkRequest.Statuses.PENDING
        self.save()
        return True

    @property
    def duration(self) -> float | None:
        """Return duration in seconds between started_at and completed_at."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        else:
            return None

    @property
    def priority_effective(self) -> int:
        """The effective priority of this work request."""
        return self.priority_base + self.priority_adjustment

    @property
    def is_workflow(self) -> bool:
        """Return whether this work request is a workflow."""
        return self.task_type == TaskTypes.WORKFLOW

    @property
    def is_part_of_workflow(self) -> bool:
        """Return whether this work request is part of a workflow."""
        return self.parent is not None

    @property
    def is_workflow_root(self) -> bool:
        """Return whether this work request is a root workflow."""
        return self.is_workflow and self.parent is None

    def get_workflow_root(self) -> Optional["WorkRequest"]:
        """
        Return the root of this work request's workflow, if any.

        For bulk situations, prefer using
        ``WorkRequest.objects.with_workflow_root_id()`` and then using the
        ``workflow_root_id`` attribute of the annotated query set.
        """
        if not self.is_workflow and not self.is_part_of_workflow:
            return None
        parent = self
        while parent.parent is not None:
            parent = parent.parent
        return parent

    @property
    def workflow_display_name(self) -> str:
        """Return this work request's name for display in a workflow."""
        if (
            self.workflow_data is not None
            and self.workflow_data.display_name is not None
        ):
            return self.workflow_data.display_name
        else:
            return self.task_name

    @property
    def workflow_display_name_parameters(self) -> str | None:
        """Return this workflow template name with useful information."""
        assert self.is_workflow

        if (template_name := self.workflow_data.workflow_template_name) is None:
            return None

        if (
            self.dynamic_task_data
            and (
                parameter_summary := self.dynamic_task_data.get(
                    "parameter_summary"
                )
            )
            is not None
        ):
            return f"{template_name}({parameter_summary})"
        else:
            return template_name

    def schedule_workflow_update(self) -> None:
        """
        Schedule updates of expensive properties of containing workflows.

        The ``workflow_last_activity_at`` and ``workflow_runtime_status``
        fields of workflows are used in the web UI and need to be kept up to
        date, but updating them requires scanning the workflow graph and can
        be expensive.  When making changes to a large workflow graph, much
        of this work can be amortized: for example, we only need to update
        the root workflow once even if many work requests under it have
        changed.  We therefore defer that work until the end of the
        transaction and run it in a Celery task.
        """
        from debusine.server.celery import update_workflows

        if self.parent:
            self.workflows_need_update = True
            self.save()

            # Run the update on commit, but only once per savepoint.  This
            # uses undocumented Django internals, but if it goes wrong
            # without an obvious unit test failure, then the worst case
            # should be some extra workflow updates.
            if (set(connection.savepoint_ids), update_workflows.delay) not in [
                hook[:2] for hook in connection.run_on_commit
            ]:
                transaction.on_commit(update_workflows.delay)

    @property
    def has_children_in_progress(self) -> bool:
        """Return whether child work requests are still in progress."""
        return self.children.exclude(
            status__in={
                WorkRequest.Statuses.COMPLETED,
                WorkRequest.Statuses.ABORTED,
            }
        ).exists()

    def effective_expiration_delay(self) -> timedelta:
        """Return expiration_delay, inherited if None."""
        expiration_delay = self.expiration_delay
        if self.expiration_delay is None:  # inherit
            expiration_delay = self.workspace.default_expiration_delay
        assert expiration_delay is not None
        return expiration_delay

    @property
    def expire_at(self) -> datetime | None:
        """Return computed expiration date."""
        delay = self.effective_expiration_delay()
        if delay == timedelta(0):
            return None
        return self.created_at + delay

    def get_label(self, task: BaseTask[Any, Any] | None = None) -> str:
        """
        Return a label for this work request.

        Optionally reuse an already instantiated task.
        """
        try:
            if task is None:
                task = self.get_task()
        except TaskConfigError:
            # This task has invalid data, so we can't render a proper label
            # for it; but we don't need to log the validation error here,
            # since it should be displayed elsewhere.
            return self.workflow_display_name
        except InternalTaskError:
            # Internal tasks can't be instantiated, but it's still useful to
            # be able to render some kind of label for them.
            return self.workflow_display_name
        return task.get_label()


#: Regexp matching the structure of notification channel names
notification_channel_name_regex = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*$")


def is_valid_notification_channel_name(value: str) -> bool:
    """Check if value is a valid scope name."""
    return bool(notification_channel_name_regex.match(value))


def validate_notification_channel_name(value: str) -> None:
    """Validate notification channel names."""
    if not is_valid_notification_channel_name(value):
        raise ValidationError(
            "%(value)r is not a valid notification channel name",
            params={"value": value},
        )


class NotificationChannel(models.Model):
    """Model to store notification configuration."""

    class Methods(models.TextChoices):
        EMAIL = "email", "Email"

    data_validators = {Methods.EMAIL: NotificationDataEmail}

    name = models.CharField(
        max_length=20,
        unique=True,
        validators=[validate_notification_channel_name],
    )
    method = models.CharField(max_length=10, choices=Methods.choices)
    data = models.JSONField(default=dict, blank=True)

    def clean(self) -> None:
        """
        Ensure that data is valid for the specific method.

        :raise ValidationError: for invalid data.
        """
        try:
            self.data_validators[self.Methods(self.method)](**self.data)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"NotificationChannel data is not valid: {exc}"
            )

        return super().clean()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Run validators and save the instance."""
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return name."""
        return self.name


def workflow_flattened(
    work_request: WorkRequest,
    *,
    include_workflows: bool = False,
    include_invisible: bool = False,
) -> WorkRequestQuerySet[WorkRequest]:
    """
    Return queryset of all the work requests that are part of the workflow.

    :param work_request: The work request to start from.  If it is a
      workflow, recursively include its children.
    :param include_workflows: If True, include work requests of type
      workflow; otherwise, exclude them (i.e. only include leaf work
      requests).
    :param include_invisible: If True, include work requests whose workflow
      data field has ``visible=False`` set, indicating that the task should
      not be shown in the visual representation of the workflow; otherwise,
      exclude them.
    """
    work_request_queryset: WorkRequestQuerySet[WorkRequest] = (
        WorkRequestQuerySet(type(work_request), using=WorkRequest.objects._db)
    )

    def workflow_flattened_cte(cte: With) -> WorkRequestQuerySet[WorkRequest]:
        recursive = (
            # .id is evasive action for problems encountered when
            # `work_request` is from a model retrieved as part of a
            # migration.
            work_request_queryset.filter(id=work_request.id)
            .values(rec_parent=F("parent"), rec_child=F("id"))
            .union(
                cte.join(type(work_request), parent=cte.col.rec_child).values(
                    rec_parent=F("parent"), rec_child=F("id")
                )
            )
        )
        assert isinstance(recursive, WorkRequestQuerySet)
        return recursive

    cte = With.recursive(workflow_flattened_cte)
    flattened = cte.join(work_request_queryset, id=cte.col.rec_child).with_cte(
        cte
    )
    if not include_workflows:
        flattened = flattened.exclude(task_type=TaskTypes.WORKFLOW)
    if not include_invisible:
        flattened = flattened.visible_in_workflow()
    assert isinstance(flattened, WorkRequestQuerySet)
    return flattened


def compute_workflow_last_activity(workflow: WorkRequest) -> datetime | None:
    """
    Compute the last activity timestamp of a workflow.

    This is the latest ``started_at``/``completed_at`` of its descendants.
    """
    last_activity = workflow_flattened(
        workflow, include_invisible=True
    ).aggregate(
        last_activity=Max(
            Greatest("started_at", "completed_at"),
            output_field=DateTimeField(blank=True, null=True),
        )
    )[
        "last_activity"
    ]
    assert last_activity is None or isinstance(last_activity, datetime)
    return last_activity


def compute_workflow_runtime_status(
    workflow: WorkRequest,
) -> WorkRequest.RuntimeStatuses:
    """
    Compute the runtime status of the workflow.

    This is recursively computed based on the status of all
    the work requests in the workflow and its sub-workflows's work requests.
    """
    assert workflow.task_type == TaskTypes.WORKFLOW

    flattened = workflow_flattened(workflow, include_invisible=True)

    if flattened.filter(
        task_type=TaskTypes.WAIT,
        status=WorkRequest.Statuses.RUNNING,
        workflow_data_json__needs_input=True,
    ).exists():
        return _RuntimeStatuses.NEEDS_INPUT

    if (
        flattened.exclude(task_type=TaskTypes.WAIT)
        .filter(status=WorkRequest.Statuses.RUNNING)
        .exists()
    ):
        return _RuntimeStatuses.RUNNING

    if flattened.filter(
        task_type=TaskTypes.WAIT,
        status=WorkRequest.Statuses.RUNNING,
        workflow_data_json__needs_input=False,
    ).exists():
        return _RuntimeStatuses.WAITING

    if flattened.filter(status=WorkRequest.Statuses.PENDING).exists():
        return _RuntimeStatuses.PENDING

    if flattened.filter(status=WorkRequest.Statuses.BLOCKED).exists():
        return _RuntimeStatuses.BLOCKED

    if flattened.filter(status=WorkRequest.Statuses.ABORTED).exists():
        return _RuntimeStatuses.ABORTED

    if not flattened.exclude(status=WorkRequest.Statuses.COMPLETED).exists():
        return _RuntimeStatuses.COMPLETED

    # Any possibility should have been dealt with
    raise AssertionError(
        f"Could not compute runtime status for workflow {workflow}"
    )


def workflow_ancestors(
    work_requests: QuerySet[WorkRequest],
) -> QuerySet[WorkRequest]:
    """
    Return workflows containing any of these work requests.

    This returns any workflows that are one of the given work requests, or
    direct or indirect ancestors.
    """
    work_request_queryset = CTEQuerySet(
        WorkRequest, using=WorkRequest.objects._db
    )

    def workflow_ancestors_cte(cte: With) -> QuerySet[WorkRequest]:
        recursive = (
            work_request_queryset.filter(id__in=work_requests)
            .values(rec_parent=F("parent"), rec_child=F("id"))
            .union(
                cte.join(WorkRequest, id=cte.col.rec_parent).values(
                    rec_parent=F("parent"), rec_child=F("id")
                )
            )
        )
        assert isinstance(recursive, QuerySet)
        return recursive

    cte = With.recursive(workflow_ancestors_cte)
    ancestors = (
        cte.join(work_request_queryset, id=cte.col.rec_child)
        .with_cte(cte)
        .distinct("id")
        .filter(task_type=TaskTypes.WORKFLOW)
    )
    assert isinstance(ancestors, QuerySet)
    return ancestors


class DeleteUnused:
    """
    Delete work requests not currently in use.

    Delete work requests that don't have unexpired child work requests.

    Delete also the work requests' internal collection if present.
    """

    #: Work requests to delete
    work_requests: set[WorkRequest]
    #: Internal collections to delete
    collections: set["Collection"]

    def __init__(self, queryset: QuerySet[WorkRequest]) -> None:
        """
        Initialize object.

        :param work_requests: candidate work_requests to delete
        """
        self.queryset = queryset
        self.scan_work_requests()
        self.scan_collections()

    def scan_work_requests(self) -> None:
        """Look for work requests to delete, filling self.work_requests."""
        # to_delete: expired work requests excluding the ones referenced
        # by a CollectionItem.created_by_workflow/removed_by_workflow
        # that are not in the CollectionItem.parent_collection
        #
        # In other words: if a WorkRequest is referenced by a
        # CollectionItem.created_by_workflow/removed_by_workflow but the
        # CollectionItem.parent_collection is WorkRequest.internal_collection
        # the WorkRequest can be deleted.
        to_delete = {
            wr
            for wr in self.queryset.exclude(
                Exists(
                    # See https://stackoverflow.com/questions/67775006/django-queryset-matching-null-value-with-null-outerref  # noqa: E501
                    # and https://code.djangoproject.com/ticket/31714
                    CollectionItem.objects.annotate(
                        internal_collection_id=ExpressionWrapper(
                            OuterRef("internal_collection_id"),
                            output_field=IntegerField(),
                        )
                    ).filter(
                        (
                            Q(created_by_workflow_id=OuterRef("id"))
                            | Q(removed_by_workflow_id=OuterRef("id"))
                        )
                        & (
                            Q(internal_collection_id__isnull=True)
                            | ~Q(
                                parent_collection_id=F("internal_collection_id")
                            )
                        )
                    )
                )
            )
        }

        # Keep work requests whose parent is not in the queryset
        to_keep: set[WorkRequest]
        while to_delete:
            to_keep = set()
            for wr in to_delete:
                if wr.parent is not None and wr.parent not in to_delete:
                    to_keep.add(wr)
            to_delete -= to_keep
            if not to_keep:
                break

        # Keep work requests that have children not in the queryset

        # Precache children of all nodes in to_delete
        children = defaultdict(list)
        for child in WorkRequest.objects.filter(
            parent__in=to_delete
        ).select_related("parent"):
            children[child.parent].append(child)

        while to_delete:
            to_keep = set()
            for wr in to_delete:
                for child in children[wr]:
                    if child not in to_delete:
                        to_keep.add(wr)
                        break
            to_delete -= to_keep
            if not to_keep:
                break

        self.work_requests = to_delete

    def scan_collections(self) -> None:
        """Look for collections to delete, filling self.collections."""
        collections: set[Collection] = set()
        for wr in self.work_requests:
            if wr.internal_collection:
                collections.add(wr.internal_collection)
        self.collections = collections

    def perform_deletions(self) -> None:
        """Delete objects found in self.work_requests and self.collections."""
        CollectionItem.objects.filter(
            parent_collection__in=self.collections
        ).delete()
        WorkRequest.objects.filter(
            id__in=[wr.id for wr in self.work_requests]
        ).delete()
        Collection.objects.filter(
            id__in=[c.id for c in self.collections]
        ).delete()
