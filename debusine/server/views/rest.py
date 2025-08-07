# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: rest framework extensions."""

from collections.abc import Iterable
from typing import Any, TYPE_CHECKING

from django.http import JsonResponse
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

if TYPE_CHECKING:
    from rest_framework.views import APIView


class ProblemResponse(JsonResponse):
    """
    Holds a title and other optional fields to return problems to the client.

    Follows RFC7807 (https://www.rfc-editor.org/rfc/rfc7807#section-6.1)
    """

    def __init__(
        self,
        title: str,
        detail: str | None = None,
        validation_errors: Iterable[Any] | None = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        """
        Initialize object.

        :param title: included in the response data.
        :param detail: if not None, included in the response data.
        :param validation_errors: if not None, included in the response data.
        :param status_code: HTTP status code for the response.
        """
        data: dict[str, Any] = {"title": title}

        if detail is not None:
            data["detail"] = detail

        if validation_errors is not None:
            data["validation_errors"] = validation_errors

        super().__init__(
            data, status=status_code, content_type="application/problem+json"
        )


class IsTokenAuthenticated(BasePermission):
    """Allow access to requests with a valid token."""

    def has_permission(
        self, request: Request, view: "APIView"  # noqa: U100
    ) -> bool:
        """Return True if the request is authenticated with a Token."""
        from debusine.db.models import Token

        return isinstance(request.auth, Token) and request.auth.enabled


class IsTokenUserAuthenticated(IsTokenAuthenticated):
    """Allow access if the request has an enabled Token with associated user."""

    def has_permission(
        self, request: Request, view: "APIView"  # noqa: U100
    ) -> bool:
        """Return True if valid token has a User assigned."""
        from debusine.db.models import Token

        return (
            isinstance(request.auth, Token)
            and request.auth.enabled
            and request.auth.user is not None
        )


class IsWorkerAuthenticated(IsTokenAuthenticated):
    """Allow access to requests with a token assigned to a worker."""

    def has_permission(
        self, request: Request, view: "APIView"  # noqa: U100
    ) -> bool:
        """
        Return True if the request is an authenticated worker.

        The Token must exist in the database and have a Worker.
        """
        if not super().has_permission(request, view):
            # No token authenticated: no Worker Authenticated
            return False

        if not hasattr(request.auth, "worker"):
            # request.auth is None; or it's a Token without a "worker"
            return False

        return True


class IsGet(BasePermission):
    """Allow access if the request's method is GET."""

    def has_permission(
        self, request: Request, view: "APIView"  # noqa: U100
    ) -> bool:
        """Return True if request.method == "GET"."""
        return request.method == "GET"
