# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-client enrolling views."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import django.db
from channels.layers import get_channel_layer
from django.db import transaction
from django.http import HttpRequest, HttpResponseBase, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from rest_framework import status

from debusine.client.models import EnrollPayload
from debusine.db.models.auth import ClientEnroll
from debusine.server.views import ProblemResponse


@method_decorator(transaction.non_atomic_requests, name="dispatch")
@method_decorator(csrf_exempt, name="dispatch")
class EnrollView(View):
    """Wait for a token after user confirmation."""

    #: Every this interval, send some data while waiting during the long poll
    PING_INTERVAL: int | float = 10

    def error_response(
        self,
        title: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> ProblemResponse:
        """Return an HTTP error."""
        return ProblemResponse(title=title, status_code=status_code)

    async def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Handle long-polling waiting for a token."""
        # Verify content-type
        if request.headers.get("Content-Type") != "application/json":
            return self.error_response(
                "only JSON data is accepted", status.HTTP_400_BAD_REQUEST
            )

        # Cap body length
        if len(request.body) > 4096:
            return self.error_response(
                "JSON payload too big", status.HTTP_400_BAD_REQUEST
            )

        # Decode the payload as JSON
        try:
            payload_unparsed = json.loads(request.body)
        except json.JSONDecodeError:
            return self.error_response(
                "payload is not valid JSON", status.HTTP_400_BAD_REQUEST
            )

        # Validate input with pydantic
        try:
            payload = EnrollPayload.parse_obj(payload_unparsed)
        except ValueError:
            return self.error_response(
                "payload data is invalid", status.HTTP_400_BAD_REQUEST
            )

        # TODO: add info from http data? IP address, what else?

        # Store the request in the database
        try:
            self.object = await ClientEnroll.objects.acreate(
                nonce=payload.nonce, payload=payload.dict()
            )
        except django.db.DatabaseError:
            return self.error_response(
                "duplicate or invalid request received",
                status.HTTP_400_BAD_REQUEST,
            )

        return StreamingHttpResponse(
            self.long_poll(payload.nonce), content_type="application/json"
        )

    async def long_poll(self, nonce: str) -> AsyncGenerator[str]:
        """Handle long polling as a StreamingHttpResponse generator."""
        channel_layer = get_channel_layer()

        try:
            channel_name = f"enroll.{nonce}"
            confirm_task = asyncio.create_task(
                channel_layer.receive(channel_name)
            )
            while True:
                done, pending = await asyncio.wait(
                    [confirm_task], timeout=self.PING_INTERVAL
                )
                if not done:
                    # Generate some traffic to avoid nginx timing out the
                    # request. json.loads() will ignore leading whitespace,
                    # so we can use it for that purpose. See #857
                    yield "\n"
                else:
                    yield json.dumps(confirm_task.result())
                    break
        finally:
            await self.object.adelete()
