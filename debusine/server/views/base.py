# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: base infrastructure."""
import copy
import logging
from collections.abc import Callable
from typing import (
    Any,
    Generic,
    Protocol,
    Self,
    TYPE_CHECKING,
    TypeVar,
    runtime_checkable,
)

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Model, QuerySet
from django.http import Http404
from rest_framework import status
from rest_framework.filters import BaseFilterBackend
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    GenericAPIView,
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Workspace
from debusine.db.models.permissions import (
    PermissionUser,
    format_permission_check_error,
)
from debusine.server.exceptions import (
    DebusineAPIException,
    raise_workspace_not_found,
)

if TYPE_CHECKING:
    from rest_framework.permissions import _PermissionClass

    _PermissionClass  # fake usage for vulture
    CreateAPIViewBase = CreateAPIView
    DestroyAPIViewBase = DestroyAPIView
    GenericAPIViewBase = GenericAPIView
    ListAPIViewBase = ListAPIView
    RetrieveAPIViewBase = RetrieveAPIView
    UpdateAPIViewBase = UpdateAPIView
else:
    # REST framework's generic views don't support generic types at run-time
    # yet.
    class _CreateAPIViewBase:
        def __class_getitem__(*args):
            return CreateAPIView

    class _DestroyAPIViewBase:
        def __class_getitem__(*args):
            return DestroyAPIView

    class _GenericAPIViewBase:
        def __class_getitem__(*args):
            return GenericAPIView

    class _ListAPIViewBase:
        def __class_getitem__(*args):
            return ListAPIView

    class _RetrieveAPIViewBase:
        def __class_getitem__(*args):
            return RetrieveAPIView

    class _UpdateAPIViewBase:
        def __class_getitem__(*args):
            return UpdateAPIView

    CreateAPIViewBase = _CreateAPIViewBase
    DestroyAPIViewBase = _DestroyAPIViewBase
    GenericAPIViewBase = _GenericAPIViewBase
    ListAPIViewBase = _ListAPIViewBase
    RetrieveAPIViewBase = _RetrieveAPIViewBase
    UpdateAPIViewBase = _UpdateAPIViewBase


class BaseAPIView(APIView):
    """Common base for API views."""

    def enforce(self, predicate: Callable[[PermissionUser], bool]) -> None:
        """Enforce a permission predicate."""
        if predicate(context.user):
            return

        raise DebusineAPIException(
            title=format_permission_check_error(predicate, context.user),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def set_current_workspace(
        self,
        workspace: str | Workspace,
    ) -> None:
        """Set the current workspace in context."""
        if isinstance(workspace, str):
            try:
                workspace = Workspace.objects.get_for_context(name=workspace)
            except Workspace.DoesNotExist:
                raise_workspace_not_found(workspace)
        try:
            workspace.set_current()
        except ContextConsistencyError:
            # Turn exception in a 404 response.
            # 404 is used instead of 403 as an attempt to prevent leaking which
            # private workspace exists that the user cannot see
            logging.debug("permission denied on %s reported as 404", workspace)
            raise_workspace_not_found(workspace)


_M = TypeVar("_M", bound=Model, covariant=True)


class GetOrCreateAPIView(CreateAPIViewBase[_M], BaseAPIView, Generic[_M]):
    """Class to create a new object (return 201 created) or return 200."""

    def get_object(self) -> _M:
        """Override to return more API-friendly errors."""
        try:
            return super().get_object()
        except Http404 as exc:
            raise DebusineAPIException(
                title=str(exc), status_code=status.HTTP_404_NOT_FOUND
            )

    def get_existing_object(self, serializer: BaseSerializer[_M]) -> _M:
        """
        If there is an existing object matching Serializer, return it.

        If not, raise ObjectDoesNotExist.
        """
        return self.get_queryset().get(**serializer.validated_data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Return the object with HTTP 201 (created) or HTTP 200 (already existed).

        It is a modification of CreateAPIView.create().

        The default behaviour in rest_framework when an object cannot be
        inserted because it already exists is to return HTTP 400. In
        GetOrCreateApiView it returns HTTP 200 and the client knows that
        the object that wanted to create was already in the database.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response_status: int
        try:
            obj = self.get_existing_object(serializer)
            response_status = status.HTTP_200_OK
            pk = obj.pk
        except ObjectDoesNotExist:
            self.perform_create(serializer)
            response_status = status.HTTP_201_CREATED
            assert serializer.instance is not None
            pk = serializer.instance.pk

        headers = self.get_success_headers(serializer.data)

        response_data = copy.deepcopy(serializer.data)
        response_data["id"] = pk

        return Response(response_data, status=response_status, headers=headers)


@runtime_checkable
class SupportsCanDisplay(Protocol[_M]):
    """A query set that supports `can_display` filtering."""

    def can_display(self, user: PermissionUser) -> Self:
        """Keep only objects that can be displayed."""


class CanDisplayFilterBackend(BaseFilterBackend):
    """Filter that only allows objects that can be displayed."""

    def filter_queryset(
        self,
        request: Request,  # noqa: U100
        queryset: QuerySet[_M],
        view: APIView,  # noqa: U100
    ) -> QuerySet[_M]:
        """Keep only objects that can be displayed."""
        assert isinstance(queryset, SupportsCanDisplay)
        return queryset.can_display(context.user)


class Whoami(BaseAPIView):
    """Simple view that returns the authentication status."""

    def get(self, request: Request) -> Response:
        """Return information about the current user."""
        user: str
        if request.user.is_authenticated:
            user = request.user.username
        else:
            user = "<anonymous>"
        return Response(
            {
                "user": user,
                "auth": bool(request.auth),
                "worker_token": bool(context.worker_token),
            }
        )
