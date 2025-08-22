# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: lookups."""

import logging

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response

from debusine.server.collections.lookup import lookup_multiple, lookup_single
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    LookupMultipleSerializer,
    LookupResponseSerializer,
    LookupSingleSerializer,
)
from debusine.server.views.base import BaseAPIView
from debusine.server.views.rest import IsWorkerAuthenticated
from debusine.tasks.models import LookupMultiple

logger = logging.getLogger(__name__)


class LookupSingleView(BaseAPIView):
    """View used to look up a single collection item."""

    permission_classes = [IsWorkerAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request: Request) -> Response:
        """
        Resolve a lookup for a single collection item.

        The request data must be a :ref:`lookup-single`.
        """
        lookup_deserialized = LookupSingleSerializer(data=request.data)
        lookup_deserialized.is_valid(raise_exception=True)
        work_request = lookup_deserialized.validated_data["work_request"]

        if (worker := getattr(request.auth, "worker", None)) is None:
            # lookup_user = request.require_user
            raise NotImplementedError(
                "this function is only tested when called from workers"
            )
        elif work_request.worker != worker:
            raise DebusineAPIException(
                title=(
                    f"Work request {work_request.id} is not assigned to the "
                    f"authenticated worker"
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        else:
            lookup_user = work_request.created_by

        try:
            result = lookup_single(
                lookup_deserialized.validated_data["lookup"],
                work_request.workspace,
                user=lookup_user,
                default_category=lookup_deserialized.validated_data.get(
                    "default_category"
                ),
                workflow_root=work_request.get_workflow_root(),
                expect_type=lookup_deserialized.validated_data["expect_type"],
            )
        except KeyError as e:
            raise DebusineAPIException(
                title="No matches",
                detail=str(e),
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except LookupError as e:
            raise DebusineAPIException(
                title="Lookup error",
                detail=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            LookupResponseSerializer(result).data, status=status.HTTP_200_OK
        )


class LookupMultipleView(BaseAPIView):
    """View used to look up multiple collection items."""

    permission_classes = [IsWorkerAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request: Request) -> Response:
        """
        Resolve a lookup for a single collection item.

        The request data must be a :ref:`lookup-multiple`.
        """
        lookup_deserialized = LookupMultipleSerializer(data=request.data)
        lookup_deserialized.is_valid(raise_exception=True)
        work_request = lookup_deserialized.validated_data["work_request"]

        if (worker := getattr(request.auth, "worker", None)) is None:
            # lookup_user = request.require_user
            raise NotImplementedError(
                "this function is only tested when called from workers"
            )
        elif work_request.worker != worker:
            raise DebusineAPIException(
                title=(
                    f"Work request {work_request.id} is not assigned to the "
                    f"authenticated worker"
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        else:
            lookup_user = work_request.created_by

        try:
            lookup = LookupMultiple.parse_obj(
                lookup_deserialized.validated_data["lookup"]
            )
        except ValueError as e:
            raise DebusineAPIException(
                title="Cannot deserialize lookup",
                validation_errors={"lookup": str(e)},
            )

        try:
            result = lookup_multiple(
                lookup,
                work_request.workspace,
                user=lookup_user,
                default_category=lookup_deserialized.validated_data.get(
                    "default_category"
                ),
                workflow_root=work_request.get_workflow_root(),
                expect_type=lookup_deserialized.validated_data["expect_type"],
            )
        except KeyError as e:
            raise DebusineAPIException(
                title="One of the lookups returned no matches",
                detail=str(e),
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except LookupError as e:
            raise DebusineAPIException(
                title="Lookup error",
                detail=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            # https://github.com/typeddjango/djangorestframework-stubs/issues/260
            LookupResponseSerializer(
                result, many=True  # type: ignore[arg-type]
            ).data,
            status=status.HTTP_200_OK,
        )
