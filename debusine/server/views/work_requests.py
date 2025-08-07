# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: work requests."""

import itertools
import logging
from typing import Any

from django.http import Http404
from django.http.response import HttpResponseBase
from rest_framework import status
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    NotificationChannel,
    TaskDatabase,
    WorkRequest,
)
from debusine.db.models.work_requests import (
    CannotRetry,
    CannotUnblock,
    WorkRequestQuerySet,
)
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    WorkRequestExternalDebsignRequestSerializer,
    WorkRequestExternalDebsignSerializer,
    WorkRequestSerializer,
    WorkRequestUnblockSerializer,
    WorkRequestUpdateSerializer,
)
from debusine.server.tasks.wait.models import ExternalDebsignDynamicData
from debusine.server.views.base import (
    BaseAPIView,
    CanDisplayFilterBackend,
    GenericAPIViewBase,
    ListAPIViewBase,
    RetrieveAPIViewBase,
    UpdateAPIViewBase,
)
from debusine.server.views.rest import (
    IsGet,
    IsTokenUserAuthenticated,
    IsWorkerAuthenticated,
)
from debusine.tasks import BaseTask
from debusine.tasks.models import ActionTypes, TaskTypes

logger = logging.getLogger(__name__)


class WorkRequestPagination(CursorPagination):
    """Pagination for lists of work requests."""

    ordering = "-created_at"


class WorkRequestView(
    ListAPIViewBase[WorkRequest], UpdateAPIViewBase[WorkRequest], BaseAPIView
):
    """View used by the debusine client to get information of a WorkRequest."""

    permission_classes = [
        IsTokenUserAuthenticated | IsWorkerAuthenticated & IsGet
    ]
    serializer_class = WorkRequestSerializer
    filter_backends = [CanDisplayFilterBackend]
    pagination_class = WorkRequestPagination
    lookup_url_kwarg = "work_request_id"

    def get_queryset(self) -> WorkRequestQuerySet[Any]:
        """Get the query set for this view."""
        return WorkRequest.objects.in_current_scope()

    def get_object(self) -> WorkRequest:
        """Override to return more API-friendly errors."""
        try:
            return super().get_object()
        except Http404 as exc:
            raise NotFound(str(exc))

    def get(
        self, request: Request, work_request_id: int | None = None
    ) -> Response:
        """Return status information for WorkRequest or not found."""
        if work_request_id is None:
            return super().get(request)

        return Response(
            WorkRequestSerializer(self.get_object()).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request: Request) -> Response:
        """Create a new work request."""
        data = request.data.copy()

        data["created_by"] = request.auth.user.id
        work_request_deserialized = WorkRequestSerializer(
            data=data,
            only_fields=[
                'task_name',
                'task_data',
                'event_reactions',
                'workspace',
                'created_by',
            ],
        )

        try:
            work_request_deserialized.is_valid(raise_exception=True)
        except DebusineAPIException as e:
            logger.debug(
                "Error creating work request. Could not be deserialized: "
                "%s (detail: %s, validation errors: %s)",
                e.debusine_title,
                e.debusine_detail,
                e.debusine_validation_errors,
            )
            raise

        workspace = work_request_deserialized.validated_data["workspace"]
        self.set_current_workspace(workspace)
        self.enforce(workspace.can_create_work_requests)

        # only accept send-notification actions
        for event, reactions in work_request_deserialized.validated_data.get(
            "event_reactions_json", {}
        ).items():
            for action in reactions:
                if action["action"] != ActionTypes.SEND_NOTIFICATION:
                    raise DebusineAPIException(
                        title="Invalid event_reactions",
                        detail=(
                            f"Action type {action['action']!r} is not allowed "
                            f"here"
                        ),
                    )

        if len(non_existing_channels := self._non_existing_channels(data)) > 0:
            raise DebusineAPIException(
                title=f"Non-existing channels: {sorted(non_existing_channels)}"
            )

        task_name = data['task_name']

        task_types = [TaskTypes.WORKER]
        if request.auth.user.is_superuser:
            task_types.append(TaskTypes.SERVER)

        for task_type in task_types:
            if BaseTask.is_valid_task_name(task_type, task_name):
                break
        else:
            valid_task_names = ', '.join(
                itertools.chain(
                    *(
                        BaseTask.task_names(task_type)
                        for task_type in task_types
                    ),
                ),
            )
            logger.debug(
                "Error creating work request: task name is not registered: %s",
                task_name,
            )
            raise DebusineAPIException(
                title="Cannot create work request: task name is not registered",
                detail=(
                    f'Task name: "{task_name}". '
                    f"Registered task names: {valid_task_names}"
                ),
            )

        # Return an error if compute_dynamic_data() raise an exception
        # (otherwise it happens during the scheduler() and the user
        # doesn't know why)
        #
        # If more checks are added here: they might need to be added as well
        # in the view WorkRequestCreateView.form_valid()
        w = work_request_deserialized.save(task_type=task_type)

        try:
            w.get_task().compute_dynamic_data(TaskDatabase(w))
        except Exception as exc:
            raise DebusineAPIException(
                title=(
                    "Cannot create work request: error computing dynamic data"
                ),
                detail=f"Task data: {w.task_data} Error: {exc}",
            )

        w.refresh_from_db()

        return Response(
            WorkRequestSerializer(w).data, status=status.HTTP_200_OK
        )

    @staticmethod
    def _non_existing_channels(data: dict[str, Any]) -> set[str]:
        """Return non existing channels from event_reactions."""
        non_existing_channels: set[str] = set()

        for event, reactions in data.get("event_reactions", {}).items():
            for action in reactions:
                assert (
                    action["action"] == ActionTypes.SEND_NOTIFICATION
                )  # filtered above
                channel_name = action["channel"]
                try:
                    NotificationChannel.objects.get(name=channel_name)
                except NotificationChannel.DoesNotExist:
                    non_existing_channels.add(channel_name)

        return non_existing_channels

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Update properties of a work request."""
        # Only a subset of properties may be patched.
        work_request_deserialized = WorkRequestUpdateSerializer(
            data=request.data
        )
        work_request_deserialized.is_valid(raise_exception=True)

        # Only users with appropriate permissions may set priority
        # adjustments.
        priority_adjustment = work_request_deserialized.validated_data.get(
            "priority_adjustment"
        )
        if priority_adjustment is None:
            pass
        else:
            token = request.auth
            # permission_classes is declared such that we won't get this far
            # unless the request has an enabled token with an associated
            # user.
            assert token is not None
            assert token.user is not None
            # Users with manage_workrequest_priorities have full access.
            if token.user.has_perm("db.manage_workrequest_priorities"):
                pass
            # Users can set non-positive priority adjustments on their own
            # work requests.
            elif (
                priority_adjustment <= 0
                and token.user == self.get_object().created_by
            ):
                pass
            else:
                raise DebusineAPIException(
                    title="You are not permitted to set priority adjustments",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        return super().patch(request, *args, **kwargs)


class WorkRequestRetryView(GenericAPIViewBase[WorkRequest], BaseAPIView):
    """View used by the debusine client to get information of a WorkRequest."""

    permission_classes = [IsTokenUserAuthenticated]
    filter_backends = [CanDisplayFilterBackend]
    lookup_url_kwarg = "work_request_id"

    def get_queryset(self) -> WorkRequestQuerySet[Any]:
        """Get the query set for this view."""
        return WorkRequest.objects.in_current_scope()

    def get_object(self) -> WorkRequest:
        """Override to return more API-friendly errors."""
        try:
            return super().get_object()
        except Http404 as exc:
            raise NotFound(str(exc))

    def post(self, *args: Any, **kwargs: Any) -> Response:
        """Retry a work request."""
        work_request = self.get_object()

        try:
            new_work_request = work_request.retry()
        except CannotRetry as e:
            raise DebusineAPIException(
                title="Cannot retry work request",
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

        return Response(
            WorkRequestSerializer(new_work_request).data,
            status=status.HTTP_200_OK,
        )


class WorkRequestUnblockView(GenericAPIViewBase[WorkRequest], BaseAPIView):
    """View used to unblock a work request awaiting manual approval."""

    permission_classes = [IsTokenUserAuthenticated]
    serializer_class = WorkRequestUnblockSerializer
    filter_backends = [CanDisplayFilterBackend]
    lookup_url_kwarg = "work_request_id"

    def get_queryset(self) -> WorkRequestQuerySet[Any]:
        """Get the query set for this view."""
        return WorkRequest.objects.in_current_scope()

    def get_object(self) -> WorkRequest:
        """Override to return more API-friendly errors."""
        try:
            return super().get_object()
        except Http404 as exc:
            raise NotFound(str(exc))

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Edit a manual unblock."""
        work_request = self.get_object()
        self.set_current_workspace(work_request.workspace)
        self.enforce(work_request.can_unblock)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notes = serializer.validated_data.get("notes")
        action = serializer.validated_data.get("action")

        token = self.request.auth
        # permission_classes is declared such that we won't get this far
        # unless the request has an enabled token with an associated user.
        assert token is not None
        assert token.user is not None

        try:
            work_request.review_manual_unblock(
                user=token.user, notes=notes, action=action
            )
        except CannotUnblock as e:
            raise DebusineAPIException(title=str(e))

        return Response(
            WorkRequestSerializer(work_request).data, status=status.HTTP_200_OK
        )


class IsWorkRequestCreator(BasePermission):
    """Allow access if the requester created the work request."""

    def has_object_permission(
        self, request: Request, view: APIView, obj: Any  # noqa: U100
    ) -> bool:
        """Return True if the requester created the work request."""
        if isinstance(obj, WorkRequest) and request.auth.user != obj.created_by:
            # Raise an explicit exception in this case for the sake of a
            # better error message.
            raise PermissionDenied(
                "Only the user who created the work request can provide a "
                "signature"
            )

        return True


class WorkRequestExternalDebsignView(
    RetrieveAPIViewBase[WorkRequest], BaseAPIView
):
    """View used to handle `ExternalDebsign` wait tasks."""

    permission_classes = [
        IsTokenUserAuthenticated,
        IsGet | IsWorkRequestCreator,
    ]
    serializer_class = WorkRequestExternalDebsignSerializer
    filter_backends = [CanDisplayFilterBackend]
    lookup_url_kwarg = "work_request_id"

    def get_queryset(self) -> WorkRequestQuerySet[Any]:
        """Get the query set for this view."""
        return WorkRequest.objects.in_current_scope()

    def get_object(self) -> WorkRequest:
        """Override to add constraints and return more API-friendly errors."""
        try:
            work_request = super().get_object()
        except Http404 as exc:
            raise NotFound(str(exc))

        if (
            work_request.task_type != TaskTypes.WAIT
            or work_request.task_name != "externaldebsign"
        ):
            raise ValidationError(
                detail=(
                    f"Expected work request to be Wait/ExternalDebsign; got "
                    f"{work_request.task_type}/{work_request.task_name}"
                )
            )
        if work_request.status != WorkRequest.Statuses.RUNNING:
            raise ValidationError(
                detail=(
                    f"Work request is not running. "
                    f"Status: {work_request.status}"
                )
            )

        return work_request

    def post(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Complete an `ExternalDebsign` task."""
        work_request = self.get_object()
        serializer = WorkRequestExternalDebsignRequestSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        dynamic_task_data = ExternalDebsignDynamicData(
            **self.get_serializer(work_request).data["dynamic_task_data"]
        )
        unsigned = Artifact.objects.get(id=dynamic_task_data.unsigned_id)
        signed = serializer.validated_data["signed_artifact"]

        unsigned_files = {
            fia.path: fia.file.hash_digest
            for fia in unsigned.fileinartifact_set.select_related("file")
        }
        signed_files = {
            fia.path: fia.file.hash_digest
            for fia in signed.fileinartifact_set.select_related("file")
        }

        new_paths = set(signed_files) - set(unsigned_files)
        if new_paths:
            raise ValidationError(
                detail=(
                    f"Signed upload adds extra files when compared to "
                    f"unsigned upload. New paths: {sorted(new_paths)}"
                )
            )

        changed_paths = {
            path
            for path, hash_digest in signed_files.items()
            if unsigned_files.get(path) != hash_digest
        }
        if not any(path.endswith(".changes") for path in changed_paths):
            raise ValidationError(
                detail="Signed upload does not change the .changes file"
            )
        if not all(
            path.endswith(".changes")
            or path.endswith(".dsc")
            or path.endswith(".buildinfo")
            for path in changed_paths
        ):
            raise ValidationError(
                detail=(
                    f"Signed upload changes more than "
                    f".changes/.dsc/.buildinfo. "
                    f"Changed paths: {sorted(changed_paths)}"
                )
            )

        ArtifactRelation.objects.create(
            artifact=signed,
            target=unsigned,
            type=ArtifactRelation.Relations.RELATES_TO,
        )
        signed.created_by_work_request = work_request
        signed.save()
        work_request.mark_completed(WorkRequest.Results.SUCCESS)

        return Response(self.get_serializer(work_request).data)
