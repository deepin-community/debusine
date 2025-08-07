# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine WorkRequest view."""

import functools
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.generic.base import View
from rest_framework import status

from debusine.db.context import context
from debusine.db.models import Artifact, TaskDatabase, User, WorkRequest
from debusine.db.models.work_requests import (
    CannotRetry,
    CannotUnblock,
    InternalTaskError,
    WorkRequestQuerySet,
)
from debusine.server.workflows.models import WorkRequestManualUnblockAction
from debusine.tasks import TaskConfigError
from debusine.tasks.models import TaskTypes
from debusine.web.forms import WorkRequestForm, WorkRequestUnblockForm
from debusine.web.views import sidebar, ui_shortcuts
from debusine.web.views.base import (
    CreateViewBase,
    DetailViewBase,
    FormMixinBase,
    ListViewBase,
    SingleObjectMixinBase,
    WorkspaceView,
)
from debusine.web.views.base_rightbar import RightbarUIView
from debusine.web.views.http_errors import HttpError400, catch_http_errors
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import WorkRequestTable
from debusine.web.views.view_utils import format_yaml


class WorkRequestObjectMixin(WorkspaceView):
    """
    Add permission filtering to a Single/MultipleObjectMixin.

    Since this inherits from WorkspaceView, this also sets the workspace in the
    context and checks that it is accessible.
    """

    def get_queryset(self) -> WorkRequestQuerySet[WorkRequest]:
        """Filter work requests by current workspace."""
        # Both SingleObjectMixin and MultipleObjectsMixin define a get_queryset
        # without a common base, so we cannot inherit from a base class with
        # the right signature.
        qs = cast(
            WorkRequestQuerySet[WorkRequest],
            super().get_queryset(),  # type: ignore[misc]
        )
        return qs.in_current_workspace().can_display(context.user)


class WorkRequestDetailView(
    WorkRequestObjectMixin, RightbarUIView, DetailViewBase[WorkRequest]
):
    """Show a work request."""

    model = WorkRequest
    context_object_name = "work_request"
    default_template_name = "web/work_request-detail.html"

    @functools.cached_property
    def _validation_error_message(self) -> str | None:
        """If the work request fails validation, return a suitable message."""
        try:
            self.object.full_clean()
        except ValidationError as e:
            return str(e)
        else:
            return None

    def _current_view_is_specialized(self) -> bool:
        """
        Specialized (based on a plugin) view will be served.

        User did not force the default view, a plugin exists, and the work
        request passes validation.
        """
        use_specialized = self.request.GET.get("view", "default") != "generic"
        plugin_class = WorkRequestPlugin.plugin_for(
            self.object.task_type, self.object.task_name
        )

        return (
            use_specialized
            and plugin_class is not None
            and self._validation_error_message is None
        )

    def get_main_ui_shortcuts(self) -> list[ui_shortcuts.UIShortcut]:
        """Return a list of UI shortcuts for this view."""
        actions = super().get_main_ui_shortcuts()
        if self.request.user.is_authenticated and self.object.verify_retry():
            actions.append(ui_shortcuts.create_work_request_retry(self.object))
        return actions

    def get_sidebar_items(self) -> list[sidebar.SidebarItem]:
        """Return a list of sidebar items."""
        items = super().get_sidebar_items()
        items.append(sidebar.create_work_request(self.object, link=False))
        if hasattr(self.object, "superseded"):
            items.append(sidebar.create_work_request_superseded(self.object))
        if self.object.supersedes:
            items.append(sidebar.create_work_request_supersedes(self.object))
        items.append(sidebar.create_workflow(self.object.get_workflow_root()))
        items.append(sidebar.create_work_request_status(self.object))
        items.append(sidebar.create_workspace(self.object.workspace))
        items.append(
            sidebar.create_user(self.object.created_by, context=self.object)
        )
        items.append(sidebar.create_created_at(self.object.created_at))
        items.append(sidebar.create_worker(self.object.worker))
        items.append(sidebar.create_work_request_started_at(self.object))
        items.append(sidebar.create_work_request_duration(self.object))
        items.append(sidebar.create_expire_at(self.object.expire_at))
        return items

    def get_title(self) -> str:
        """Get the title for the page."""
        return f"{self.object.id}: {self.object.get_label()}"

    def get_manual_unblock_log(self) -> list[dict[str, Any]]:
        """Get a representation of the manual unblock log."""
        workflow_data = self.object.workflow_data
        if workflow_data.manual_unblock is None:
            return []

        users = {
            user.id: user
            for user in get_user_model().objects.filter(
                id__in={log.user_id for log in workflow_data.manual_unblock.log}
            )
        }
        return [
            {
                "timestamp": log.timestamp,
                "user": users.get(log.user_id, f"Deleted user {log.user_id}"),
                "action": log.action,
                "notes": log.notes,
            }
            for log in workflow_data.manual_unblock.log
        ]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """
        Add context to the default DetailView context.

        Add the artifacts related to the work request.
        """
        context = super().get_context_data(**kwargs)

        plugin_view = WorkRequestPlugin.plugin_for(
            self.object.task_type, self.object.task_name
        )

        if plugin_view is not None and self._current_view_is_specialized():
            return {**context, **plugin_view(self.object).get_context_data()}

        context["manual_unblock_log_entries"] = self.get_manual_unblock_log()
        context["manual_unblock_form"] = WorkRequestUnblockForm()

        if self._validation_error_message is not None:
            context["validation_error"] = self._validation_error_message
        elif plugin_view:
            # plugin_view for WorkRequest.task_name exists, but the
            # response will return the generic WorkRequest view
            context["specialized_view_path"] = self.request.path

        try:
            task = self.object.get_task()
        except (TaskConfigError, InternalTaskError):
            task = None
        source_artifacts: list[Artifact] = []
        try:
            if task and (
                source_artifact_ids := task.get_source_artifacts_ids()
            ):
                source_artifacts.extend(
                    Artifact.objects.filter(
                        pk__in=source_artifact_ids
                    ).annotate_complete()
                )
                context["source_artifacts"] = source_artifacts
        except NotImplementedError:
            # TODO: remove this once get_source_artifacts_ids has been
            # implemented for all artifact types
            context["source_artifacts_not_implemented"] = True

        built_artifacts = list(
            Artifact.objects.filter(created_by_work_request=self.object)
            .annotate_complete()
            .order_by("category", "id")
        )

        # Generate UI shortcuts for artifacts
        for artifact in source_artifacts + built_artifacts:
            self.add_object_ui_shortcuts(
                artifact,
                ui_shortcuts.create_artifact_view(artifact),
                ui_shortcuts.create_artifact_download(artifact),
            )

        context["built_artifacts"] = built_artifacts
        context["task_data"] = format_yaml(self.object.used_task_data)
        if self.object.task_data != self.object.used_task_data:
            context["task_data_original"] = format_yaml(self.object.task_data)
            context["task_data_configured"] = True
        else:
            context["task_data_configured"] = False

        return context

    def get_template_names(self) -> list[str]:
        """Return the plugin's template_name or the default one."""
        if self._current_view_is_specialized():
            plugin_class = WorkRequestPlugin.plugin_for(
                self.object.task_type, self.object.task_name
            )
            assert plugin_class is not None
            return [plugin_class.template_name]

        return [self.default_template_name]


class WorkRequestPlugin:
    """
    WorkRequests with specific outputs must subclass it.

    When subclassing, the subclass:
    - Is automatically used by the /work-request/ID/ endpoints
    - Must define "template_name", "task_type", and "task_name"
    - Must implement "get_context_data()"
    """

    model = WorkRequest
    template_name: str
    task_type: TaskTypes
    task_name: str

    _work_request_plugins: dict[tuple[str, str], type["WorkRequestPlugin"]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: U100
        """Register the plugin."""
        cls._work_request_plugins[(cls.task_type, cls.task_name)] = cls

    def __init__(self, work_request: WorkRequest) -> None:
        """Store the work_request to display."""
        self.work_request = work_request

    @classmethod
    def plugin_for(
        cls, task_type: str, task_name: str
    ) -> type["WorkRequestPlugin"] | None:
        """Return WorkRequestPlugin for task_name or None."""
        return cls._work_request_plugins.get((task_type, task_name))

    def get_context_data(self) -> dict[str, Any]:
        """Must be implemented by subclasses."""
        raise NotImplementedError()


class WorkRequestListView(
    TableMixin[WorkRequest], WorkRequestObjectMixin, ListViewBase[WorkRequest]
):
    """List work requests."""

    model = WorkRequest
    table_class = WorkRequestTable
    template_name = "web/work_request-list.html"
    context_object_name = "work_request_list"
    title = "List of work requests"
    paginate_by = 50

    @staticmethod
    def _filter_by_architecture(
        work_requests: WorkRequestQuerySet[WorkRequest], architecture: str
    ) -> WorkRequestQuerySet[WorkRequest]:
        pending_without_host_architecture = work_requests.filter(
            task_data__host_architecture__isnull=True
        )

        # TODO when WorkRequests have tags: have a host_architecture
        # tag and filter in the database. This will allow:
        # -simplify logic filtering (instead of using
        # task_data__host_architecture and for the ones without this
        # instantiating the Task
        # -more efficient (done in the DB) so we can filter per architecture
        # for any status (currently it's limited for WorkRequests where
        # status == PENDING to avoid instantiating too many tasks

        # Work requests with task_data__host_architecture=arch_param
        # (leave out work requests for which the architecture
        # is defined elsewhere)
        queryset = work_requests.filter(
            task_data__host_architecture=architecture
        )

        # For work requests which task_data__host_architecture is
        # not there: instantiate the work requests and use
        # Task.host_architecture(), add to the query set
        for work_request in pending_without_host_architecture:
            try:
                task = work_request.get_task()
            except TaskConfigError:
                # When the scheduler picks this work request: will
                # mark it
                continue

            if task.host_architecture() == architecture:
                queryset = queryset | WorkRequest.objects.filter(
                    id=work_request.id
                )

        return queryset

    def get_queryset(self) -> WorkRequestQuerySet[WorkRequest]:
        """Filter work requests displayed by the workspace GET parameter."""
        queryset = super().get_queryset().exclude(task_type=TaskTypes.INTERNAL)

        arch_param = self.request.GET.get("arch")
        status_param = self.request.GET.get("status")

        status_mapping = {
            "pending": WorkRequest.Statuses.PENDING,
            "running": WorkRequest.Statuses.RUNNING,
            "completed": WorkRequest.Statuses.COMPLETED,
            "aborted": WorkRequest.Statuses.ABORTED,
            "blocked": WorkRequest.Statuses.BLOCKED,
        }

        status_value = (
            None if status_param is None else status_mapping.get(status_param)
        )

        if status_param is not None and status_value is None:
            messages.warning(
                self.request, 'Invalid "status" parameter, ignoring it'
            )

        if status_value:
            queryset = queryset.filter(status=status_value)

        if arch_param:
            if status_value == WorkRequest.Statuses.PENDING:
                # Filtering by architecture is only allowed for PENDING
                # Work Requests. Filtering by architecture, currently,
                # requires instantiating the Task and calling
                # host_architecture()
                queryset = self._filter_by_architecture(queryset, arch_param)
            else:
                messages.warning(
                    self.request,
                    'Filter by architecture is only supported when '
                    'also filtering by "status=pending", ignoring architecture'
                    'filtering',
                )

        return queryset


class WorkRequestCreateView(
    WorkRequestObjectMixin,
    CreateViewBase[WorkRequest, WorkRequestForm],
):
    """Form view for creating a work request."""

    model = WorkRequest
    template_name = "web/work_request-create.html"
    form_class = WorkRequestForm
    title = "Create work request"

    def init_view(self) -> None:
        """Set the current workspace."""
        super().init_view()
        self.enforce(context.require_workspace().can_create_work_requests)

    def get_form_kwargs(self) -> dict[str, Any]:
        """Extend the default kwarg arguments: add "user"."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = context.user
        kwargs["workspace"] = context.workspace
        return kwargs

    def get_success_url(self) -> str:
        """Redirect to work_requests:detail for the created WorkRequest."""
        assert self.object is not None
        return self.object.get_absolute_url()

    def form_valid(self, form: WorkRequestForm) -> HttpResponse:
        """Validate the work request."""
        self.object = form.save(commit=False)

        try:
            self.object.get_task().compute_dynamic_data(
                TaskDatabase(self.object)
            )
        except Exception as exc:
            form.add_error("task_data", f"Invalid task data: {exc}")
            return self.form_invalid(form)

        return super().form_valid(form)


class WorkRequestRetryView(
    WorkRequestObjectMixin, SingleObjectMixinBase[WorkRequest], View
):
    """Form view for retrying a work request."""

    model = WorkRequest

    def retry(self, work_request: WorkRequest) -> HttpResponse:
        """Retry the work request."""
        try:
            new_work_request = work_request.retry()
        except CannotRetry as e:
            messages.error(self.request, f"Cannot retry: {e}")
            return redirect(work_request.get_absolute_url())

        return redirect(new_work_request.get_absolute_url())

    def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any  # noqa: U100
    ) -> HttpResponse:
        """Handle POST requests."""
        work_request = self.get_object()
        self.enforce(work_request.can_retry)
        return self.retry(work_request)


class WorkRequestUnblockView(
    WorkRequestObjectMixin,
    SingleObjectMixinBase[WorkRequest],
    FormMixinBase[WorkRequestUnblockForm],
    View,
):
    """Form view for reviewing a work request awaiting manual approval."""

    model = WorkRequest
    form_class = WorkRequestUnblockForm

    def get_action(self) -> WorkRequestManualUnblockAction | None:
        """Get the unblock action from query arguments."""
        raw_action = self.request.POST["action"]
        match raw_action:
            case "Accept":
                return WorkRequestManualUnblockAction.ACCEPT
            case "Reject":
                return WorkRequestManualUnblockAction.REJECT
            case "Record notes only":
                return None
            case _:
                raise HttpError400(f"Invalid action parameter: {raw_action!r}")

    def unblock(self, work_request: WorkRequest) -> HttpResponse:
        """Perform the unblock."""
        # This view requires a permission, so the user can't be an
        # AnonymousUser by this point.
        assert isinstance(self.request.user, User)

        form = self.get_form()
        if not form.is_valid():
            # TODO: This is ugly and we should probably try to render
            # work_requests:detail with inline unblock form errors instead.
            # However, since the form is so limited it's very difficult to
            # actually hit this case in practice, so it doesn't seem worth
            # the effort for now.
            return render(
                self.request,
                "400.html",
                context={"error": form.errors.as_json()},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            work_request.review_manual_unblock(
                user=self.request.user,
                notes=form.cleaned_data["notes"],
                action=self.get_action(),
            )
        except CannotUnblock as e:
            raise HttpError400(f"Cannot unblock: {e}")
        return redirect(work_request.get_absolute_url())

    @catch_http_errors
    def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any  # noqa: U100
    ) -> HttpResponse:
        """Handle POST requests."""
        work_request = self.get_object()
        self.enforce(work_request.can_unblock)
        return self.unblock(work_request)
