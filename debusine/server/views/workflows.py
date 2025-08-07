# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: workflows."""

import logging
from typing import Any

from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from debusine.db.models import WorkRequest, WorkflowTemplate
from debusine.db.models.work_requests import WorkflowTemplateQuerySet
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    CreateWorkflowRequestSerializer,
    WorkRequestSerializer,
    WorkflowTemplateSerializer,
)
from debusine.server.views.base import (
    BaseAPIView,
    CanDisplayFilterBackend,
    CreateAPIViewBase,
    DestroyAPIViewBase,
    RetrieveAPIViewBase,
    UpdateAPIViewBase,
)
from debusine.server.views.rest import IsTokenUserAuthenticated

logger = logging.getLogger(__name__)


class WorkflowTemplateView(
    CreateAPIViewBase[WorkflowTemplate],
    RetrieveAPIViewBase[WorkflowTemplate],
    UpdateAPIViewBase[WorkflowTemplate],
    DestroyAPIViewBase[WorkflowTemplate],
    BaseAPIView,
):
    """Return, create and delete workflow templates."""

    permission_classes = [IsTokenUserAuthenticated]
    serializer_class = WorkflowTemplateSerializer
    filter_backends = [CanDisplayFilterBackend]
    parser_classes = [JSONParser]
    pagination_class = None

    def get_queryset(self) -> WorkflowTemplateQuerySet[Any]:
        """Get the query set for this view."""
        return WorkflowTemplate.objects.in_current_scope()

    def get_object(self) -> WorkflowTemplate:
        """Override to return more API-friendly errors."""
        try:
            return super().get_object()
        except Http404 as exc:
            raise NotFound(str(exc))

    def get(self, request: Request, pk: int) -> Response:  # noqa: U100
        """Retrieve a workflow template."""
        instance = self.get_object()
        self.set_current_workspace(instance.workspace)
        self.enforce(instance.workspace.can_display)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def _check_permissions(
        self, serializer: BaseSerializer[WorkflowTemplate]
    ) -> None:
        """Only users with appropriate permissions may set priorities."""
        priority = serializer.validated_data.get("priority")
        if priority is not None and priority > 0:
            token = self.request.auth
            # TODO: This should be replaced by self.enforce with some
            # appropriate debusine permission.
            if (
                token is None
                or token.user is None
                or not token.user.has_perm("db.manage_workrequest_priorities")
            ):
                raise DRFPermissionDenied(
                    "You are not permitted to set positive priorities"
                )

    def perform_create(
        self, serializer: BaseSerializer[WorkflowTemplate]
    ) -> None:
        """Create a workflow template."""
        workspace = serializer.validated_data["workspace"]
        self.set_current_workspace(workspace)
        self.enforce(workspace.can_configure)
        self._check_permissions(serializer)
        super().perform_create(serializer)

    def perform_update(
        self, serializer: BaseSerializer[WorkflowTemplate]
    ) -> None:
        """Update a workflow template."""
        assert serializer.instance is not None
        self.set_current_workspace(serializer.instance.workspace)
        self.enforce(serializer.instance.workspace.can_configure)
        self._check_permissions(serializer)
        super().perform_update(serializer)

    def perform_destroy(self, instance: WorkflowTemplate) -> None:
        """Delete a workflow template."""
        self.set_current_workspace(instance.workspace)
        self.enforce(instance.workspace.can_configure)
        super().perform_destroy(instance)


class WorkflowView(BaseAPIView):
    """Create workflows from a template."""

    # TODO: This should be replaced by appropriate debusine permissions.
    permission_classes = [IsTokenUserAuthenticated]
    filter_backends = [CanDisplayFilterBackend]

    def post(self, request: Request) -> Response:
        """Create a new workflow from a template."""
        serializer = CreateWorkflowRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = serializer.validated_data["workspace"]
        self.set_current_workspace(workspace)

        try:
            template = WorkflowTemplate.objects.get(
                name=serializer.validated_data["template_name"],
                workspace=workspace,
            )
        except WorkflowTemplate.DoesNotExist:
            raise DebusineAPIException(
                title="Workflow template not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        token = request.auth
        # permission_classes is declared such that we won't get this far
        # unless the request has an enabled token with an associated user.
        assert token is not None
        assert token.user is not None

        try:
            workflow = WorkRequest.objects.create_workflow(
                template=template,
                data=serializer.validated_data.get("task_data", {}),
            )
        except Exception as e:
            raise DebusineAPIException(
                title="Cannot create workflow", detail=str(e)
            )

        return Response(
            WorkRequestSerializer(workflow).data, status=status.HTTP_201_CREATED
        )
