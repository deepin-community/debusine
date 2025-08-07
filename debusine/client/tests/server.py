#  Copyright © The Debusine Developers
#  See the AUTHORS file at the top-level directory of this distribution
#
#  This file is part of Debusine. It is subject to the license terms
#  in the LICENSE file found in the top-level directory of this
#  distribution. No part of Debusine, including this file, may be copied,
#  modified, propagated, or distributed except according to the terms
#  contained in the LICENSE file.

"""Test server for the tests."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from debusine.client.models import (
    OnWorkRequestCompleted,
    model_to_json_serializable_dict,
)


@dataclass
class ServerConfig:
    """Settings that define server's behaviour when receiving requests."""


class DebusineAioHTTPTestCase(AioHTTPTestCase):
    """Test server for the debusine client websocket functionality."""

    WORK_REQUEST_ID = 10
    WORK_REQUEST_RESULT = "success"
    COMPLETED_AT = datetime.utcnow()
    ON_COMPLETED_WORK_REQUESTS = 1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize configuration of the server."""
        super().__init__(*args, **kwargs)

        self.server_config = ServerConfig()
        self.requests: list[web.Request] = []

    async def get_application(self) -> web.Application:
        """
        Return an instance of aiohttp.web.Application with debusine endpoints.

        Used to test debusine client websocket related functionality.
        """

        async def connect(request: web.Request) -> web.WebSocketResponse:
            self.requests.append(request)

            ws = web.WebSocketResponse()
            await ws.prepare(request)

            await ws.send_json({"text": "connected"})

            for loop in range(0, self.ON_COMPLETED_WORK_REQUESTS):
                await ws.send_json(
                    {
                        "text": "work_request_completed",
                        **model_to_json_serializable_dict(
                            OnWorkRequestCompleted(
                                work_request_id=self.WORK_REQUEST_ID + loop,
                                result=self.WORK_REQUEST_RESULT,
                                completed_at=self.COMPLETED_AT,
                            )
                        ),
                    }
                )

            return ws

        debusine = web.Application()
        debusine.router.add_route(
            "GET", "/api/ws/1.0/work-request/on-completed/", connect
        )

        return debusine
