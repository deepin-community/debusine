# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test utility classes to help to test the Worker."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from aiohttp.web_exceptions import HTTPCreated, HTTPNoContent
from multidict import MultiMapping

from debusine.client.models import (
    WorkRequestResponse,
    model_to_json_serializable_dict,
)


@dataclass
class ServerConfig:
    """Settings that define server's behaviour when receiving requests."""

    registered_token: str | None = None

    # HTTP status code returned by /api/1.0/worker/register/ endpoint
    register_response_status_code: int = HTTPCreated.status_code

    # The response body when a websocket connection is established
    # If None the server only sends a "connected" message
    # (/api/ws/1.0/worker/connect/)
    connect_response_bodies: list[dict[str, str]] = field(default_factory=list)

    class GetNextForWorkerResponse(Enum):
        # Next response for GET /api/1.0/work-request/get-next-for-worker/
        VALID_WORK_REQUEST = auto()  # well formatted WorkRequest
        NO_WORK_REQUEST_PENDING = auto()  # HTTP 204 empty content
        INVALID_WORK_REQUEST = auto()  # missing mandatory fields
        INVALID_JSON = auto()  # server did not return valid JSON

    get_next_for_worker: GetNextForWorkerResponse | web.Response = (
        GetNextForWorkerResponse.VALID_WORK_REQUEST
    )

    RESPONSES = {
        'connected': {'text': 'connected'},
        'request_dynamic_metadata': {'text': 'request_dynamic_metadata'},
        'work_request_available': {'text': 'work_request_available'},
        'token_disabled_error': {
            'reason': 'The token is disabled',
            'reason_code': 'TOKEN_DISABLED',
        },
    }


@dataclass
class RequestStorage:
    """Storage for the latest requests received by the server's endpoints."""

    method: str | None = None
    path: str | None = None
    json_content: dict[Any, Any] | None = None

    # the token received from the Worker matched the registered_token
    # from the server's registered_token
    is_authenticated: bool = False

    # received headers
    headers: MultiMapping[str] | None = None


class DebusineAioHTTPTestCase(AioHTTPTestCase):
    """
    Implement get_application(): returns a Debusine test server.

    Tests can set up the test server behaviour (see
    DebusineAioHTTPTestCase.server_config) and saves the latest request in
    DebusineAioHTTPTestCase.server_latest_request.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize configuration of the server."""
        super().__init__(*args, **kwargs)

        # server_config changes what the server returns to the Worker:
        # errors, responses, requests, etc.
        self.server_config = ServerConfig()

        # server_latest_requests stores the requests. Tests use it
        # to assert that request were done correctly
        self.server_requests: list[RequestStorage] = []

    @property
    def server_latest_request(self) -> RequestStorage | None:
        """Return the latest request done to the server or None."""
        if self.server_requests:
            return self.server_requests[-1]
        else:
            return None

    async def _update_latest_request_information(
        self, request: web.Request
    ) -> None:
        """Save request information to self.server_latest_request."""
        request_storage = RequestStorage()
        request_storage.method = request.method
        request_storage.path = request.path

        if request.method in ('PUT', 'POST'):
            request_storage.json_content = await request.json()

        request_storage.is_authenticated = (
            'token' in request.headers
            and request.headers['token'] == self.server_config.registered_token
        )

        request_storage.headers = request.headers

        self.server_requests.append(request_storage)

    async def get_application(self) -> web.Application:
        """
        Return an instance of aiohttp.web.Application with debusine endpoints.

        Return server that will save requests for the tests to verify
        what was requested. Allows (via self.server_config) to change its
        behaviour.
        """

        async def register(request: web.Request) -> web.Response:
            await self._update_latest_request_information(request)

            status = self.server_config.register_response_status_code

            body = ''
            if status != HTTPCreated.status_code:
                body = 'error'

            return web.Response(body=body, status=status)

        async def dynamic_metadata_received(
            request: web.Request,
        ) -> web.Response:
            await self._update_latest_request_information(request)

            return web.Response(status=HTTPNoContent.status_code)

        async def connect(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)

            await collect_request_information(request)

            for body in self.server_config.connect_response_bodies or [
                self.server_config.RESPONSES["connected"]
            ]:
                await ws.send_json(body)

            return ws

        def _create_valid_work_request() -> web.Response:
            task_data = {
                "input": {
                    "source_artifact": 5,
                },
                "environment": "debian/match:codename=bullseye",
                "host_architecture": "amd64",
                "build_components": [
                    "any",
                    "all",
                ],
            }

            next_work_request = WorkRequestResponse(
                id=52,
                task_type="Worker",
                task_name="sbuild",
                created_at=datetime.fromisoformat(
                    "2022-01-05T11:14:22.242178Z"
                ),
                started_at=None,
                completed_at=None,
                duration=None,
                worker=58,
                task_data=task_data,
                dynamic_task_data=None,
                priority_base=0,
                priority_adjustment=0,
                status="running",
                result="",
                artifacts=[],
                workspace="TestWorkspace",
            )

            return web.json_response(
                model_to_json_serializable_dict(next_work_request)
            )

        async def get_next_for_worker(request: web.Request) -> web.Response:
            """Return next_work_request."""
            await self._update_latest_request_information(request)

            res = ServerConfig.GetNextForWorkerResponse

            responses: dict[
                ServerConfig.GetNextForWorkerResponse,
                Callable[[], web.Response],
            ] = {
                res.VALID_WORK_REQUEST: lambda: _create_valid_work_request(),
                res.NO_WORK_REQUEST_PENDING: lambda: web.json_response(
                    status=HTTPNoContent.status_code
                ),
                res.INVALID_WORK_REQUEST: lambda: web.json_response(
                    {'id': 'id should be an int'}
                ),
                res.INVALID_JSON: lambda: web.Response(
                    body='something that is not JSON'
                ),
            }

            if isinstance(self.server_config.get_next_for_worker, web.Response):
                return self.server_config.get_next_for_worker
            else:
                return responses[self.server_config.get_next_for_worker]()

        async def collect_request_information(
            request: web.Request,
        ) -> web.Response:
            """Save the request to be tested."""
            await self._update_latest_request_information(request)
            return web.Response(status=HTTPNoContent.status_code)

        debusine = web.Application()
        debusine.router.add_post('/api/1.0/worker/register/', register)
        debusine.router.add_get('/api/ws/1.0/worker/connect/', connect)
        debusine.router.add_put(
            '/api/1.0/worker/dynamic-metadata/',
            dynamic_metadata_received,
        )

        debusine.router.add_get(
            '/api/1.0/work-request/get-next-for-worker/', get_next_for_worker
        )

        debusine.router.add_get(
            '/collect_request_information/', collect_request_information
        )
        debusine.router.add_put(
            '/collect_request_information/', collect_request_information
        )
        debusine.router.add_post(
            '/collect_request_information/', collect_request_information
        )
        return debusine
