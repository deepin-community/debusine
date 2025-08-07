# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application's system health status."""

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from debusine.client.models import model_to_json_serializable_dict
from debusine.server.status import WorkerStatus
from debusine.server.views.base import BaseAPIView


class ServiceStatusView(BaseAPIView):
    """View used to get the service status."""

    def get(self, request: Request) -> Response:  # noqa: U100
        """Return system health status."""
        health_status = WorkerStatus.get_status()
        data = model_to_json_serializable_dict(health_status)
        return Response(data, status=status.HTTP_200_OK)
