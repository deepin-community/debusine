# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Consumers for the server application."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from rest_framework.fields import DateTimeField
from rest_framework.permissions import BasePermission
from rest_framework.renderers import JSONRenderer

from debusine.db.models import Token, WorkRequest, Worker, Workspace
from debusine.server.serializers import OnWorkRequestCompleted
from debusine.server.views.rest import (
    IsTokenUserAuthenticated,
    IsWorkerAuthenticated,
)

logger = logging.getLogger(__name__)


class RequestAdaptor:
    """Minimal class used when helper classes expect a Request."""

    def __init__(self, scope: dict[str, Any]) -> None:
        """Initialize RequestAdaptor."""
        self.auth = scope["token"]


class ConsumerMixin:
    """Mixin used for different consumer classes."""

    scope: dict[str, Any]

    def get_header_value(
        self: AsyncWebsocketConsumer, header_field_name: str
    ) -> str | None:
        """
        Return the value of header_field_name from self.scope['headers'].

        :param header_field_name: case-insensitive, utf-8 encoded.
        :return: None if header_field_name is not found in
          self.scope['headers'] or the header's content.
        """
        encoded_header_field_name = header_field_name.lower().encode('utf-8')

        for name, value in self.scope['headers']:
            if name == encoded_header_field_name:
                assert isinstance(value, bytes)
                return value.decode('utf-8')

        return None

    async def reject_connection(
        self: AsyncWebsocketConsumer,
        reason: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        """Send JSON rejecting the connection and logs it."""
        msg = {
            'type': 'connection_rejected',
            'reason': reason,
        }
        if reason_code is not None:
            msg["reason_code"] = reason_code

        await self.send(text_data=json.dumps(msg), close=True)

        logger.info("Consumer rejected. %s", reason)

    async def has_permission(
        self, permission_class: type[BasePermission]
    ) -> bool:
        """Return bool checking if the request has permission to connect."""
        request_adaptor = RequestAdaptor(self.scope)

        has_permission = await database_sync_to_async(
            permission_class().has_permission
        )(request_adaptor, None)
        assert isinstance(has_permission, bool)
        return has_permission


# mypy complains that AsyncWebsocketConsumer is untyped, which is true, but
# we can't fix that here.
class WorkerConsumer(
    ConsumerMixin, AsyncWebsocketConsumer  # type: ignore[misc]
):
    """
    Implement server-side of a Worker.

    After the client worker connects to the server: WorkerConsumer
    requests information, send tasks, etc.
    """

    # Call self._send_dynamic_metadata_request() every
    # REQUEST_DYNAMIC_METADATA_SECONDS
    REQUEST_DYNAMIC_METADATA_SECONDS = 3600

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize WorkerConsumer member variables."""
        super().__init__(*args, **kwargs)
        self._worker: Worker | None = None
        self._request_dynamic_metadata_task: asyncio.Task[None] | None = None

    async def _channel_layer_group_add(
        self, token_hash: str, channel_name: str
    ) -> None:
        await self.channel_layer.group_add(token_hash, channel_name)

    async def connect(self) -> None:
        """Worker client is connecting."""
        await self.accept()

        if not await self.has_permission(IsWorkerAuthenticated):
            await self.reject_connection(
                "No token header, token not associated to a worker "
                "or not enabled",
                reason_code="TOKEN_DISABLED",
            )
            return

        token_header: str | None = self.get_header_value("token")
        token_hash: str | None = (
            Token._generate_hash(token_header) if token_header else None
        )

        self._worker = await Worker.objects.select_related("token").aget(
            token__hash=token_hash
        )
        # Must be true, since we already checked IsWorkerAuthenticated.
        assert self._worker is not None
        assert self._worker.token is not None

        try:
            await self._channel_layer_group_add(
                self._worker.token.hash, self.channel_name
            )
        except Exception as exc:
            logger.error(  # noqa: G200
                'Error adding worker to group (Redis): %s', exc
            )
            await self.reject_connection('Service unavailable')
            return

        logger.info("Worker connected: %s", self._worker.name)

        await database_sync_to_async(self._worker.mark_connected)()

        self._request_dynamic_metadata_task = asyncio.create_task(
            self._dynamic_metadata_refresher(),
            name='dynamic_metadata_refresher',
        )

        await self.send(
            text_data=json.dumps(
                {"type": "websocket.send", "text": "connected"}
            )
        )

        # If there are WorkRequests running or pending: send to the worker
        # "work-request-available" message
        if await database_sync_to_async(self._worker.is_busy)():
            await self._send_work_request_available()

    async def _send_work_request_available(self) -> None:
        await self.send(
            text_data=json.dumps(
                {
                    "type": "websocket.send",
                    "text": "work_request_available",
                }
            )
        )

    async def _dynamic_metadata_refresher(self) -> None:
        while True:
            await self._send_dynamic_metadata_request()
            await asyncio.sleep(self.REQUEST_DYNAMIC_METADATA_SECONDS)

    async def _send_dynamic_metadata_request(self) -> None:
        await self.send(
            text_data=json.dumps(
                {
                    "type": "websocket.send",
                    "text": "request_dynamic_metadata",
                }
            )
        )

    async def disconnect(self, close_code: Any) -> None:
        """Worker has disconnected. Cancel tasks, mark as disconnect, etc."""
        if self._request_dynamic_metadata_task:
            self._request_dynamic_metadata_task.cancel()

        if self._worker:
            assert self._worker.token is not None

            await self.channel_layer.group_discard(
                self._worker.token.hash, self.channel_name
            )

            await self._worker.arefresh_from_db()
            await database_sync_to_async(self._worker.mark_disconnected)()
            logger.info(
                "Worker disconnected: %s (code: %s)",
                self._worker.name,
                close_code,
            )

    async def worker_disabled(
        self, event: dict[str, Any]  # noqa: U100
    ) -> None:
        """Worker has been disabled. Send a connection_closed msg."""
        assert self._worker is not None
        assert self._worker.token is not None

        logger.info("Worker %s disabled", self._worker.name)

        msg = {
            "type": "connection_closed",
            "reason": 'Token has been disabled '
            f'(token hash: "{self._worker.token.hash}")',
            "reason_code": "TOKEN_DISABLED",
        }
        await self.send(text_data=json.dumps(msg), close=True)

    async def work_request_assigned(
        self, event: dict[str, Any]  # noqa: U100
    ) -> None:
        """Work Request has been assigned to the worker. Send channel msg."""
        await self._send_work_request_available()


# mypy complains that AsyncWebsocketConsumer is untyped, which is true, but
# we can't fix that here.
class OnWorkRequestCompletedConsumer(
    ConsumerMixin, AsyncWebsocketConsumer  # type: ignore[misc]
):
    """Send work_request_completed notifications."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize OnWorkRequestCompletedConsumer."""
        super().__init__(*args, **kwargs)

        # If _workspaces is not None: set of the workspaces that are being
        # monitored, otherwise all the workspaces that the client has access to
        self._workspaces_only: set[int] | None = None

        self._query_string: dict[str, str] = {}

        # RedisPubSubChannelLayer in channels_redis 4.0.0 has a bug in
        # group_discard where it fails if group_add hadn't been called.
        # This was fixed in 4.1.0, but work around it.
        self._channel_added_to_group: bool = False

    async def _get_workspaces_from_query_params(self) -> set[int] | None:
        workspace_names_str = self._query_string.get("workspaces")

        if workspace_names_str is None:
            return None

        workspace_names = workspace_names_str.split(",")

        def get_workspace_ids(workspace_names: list[str]) -> set[int]:
            # TODO: check that the current user has access to each
            # workspace (need implementing #80)
            names_to_ids = {
                workspace.name: workspace.id
                for workspace in Workspace.objects.filter(
                    name__in=workspace_names
                )
            }
            for workspace_name in workspace_names:
                if workspace_name not in names_to_ids:
                    raise Workspace.DoesNotExist(
                        f'Workspace "{workspace_name}" not found'
                    )
            return set(names_to_ids.values())

        workspace_ids = await database_sync_to_async(get_workspace_ids)(
            workspace_names
        )
        assert isinstance(workspace_ids, set)

        return workspace_ids

    async def connect(self) -> None:
        """Client is connecting."""
        self._query_string = dict(
            parse_qsl(self.scope["query_string"].decode("utf-8"))
        )

        await self.accept()

        if not await self.has_permission(IsTokenUserAuthenticated):
            await self.reject_connection(
                "No token header, token not associated to a user "
                "or not enabled"
            )
            return

        try:
            self._workspaces_only = (
                await self._get_workspaces_from_query_params()
            )
        except Workspace.DoesNotExist as exc:
            await self.reject_connection(str(exc))
            return

        await self.channel_layer.group_add(
            "work_request_completed", self.channel_name
        )
        self._channel_added_to_group = True

        await self.send(
            text_data=json.dumps(
                {"type": "websocket.send", "text": "connected"}
            )
        )

        await self._send_pending_work_requests_since_last_completed()

    async def disconnect(self, close_code: Any) -> None:  # noqa: U100
        """Client is disconnecting, clean-up."""
        if self._channel_added_to_group:
            await self.channel_layer.group_discard(
                "work_request_completed", self.channel_name
            )
        self._channel_added_to_group = False

    async def _get_pending_work_requests(
        self, completed_at_since: datetime
    ) -> list[WorkRequest]:
        """
        Return WorkRequests that completed_at on or after completed_at_since.

        If there is one WorkRequest that completed_at == completed_at_since:
        do not include this one (the client was already notified).

        If more than one WorkRequest completed_at == completed_at_since:
        include both of them. The client might have not processed all of them.
        """
        on_last_completed_count = await WorkRequest.objects.filter(
            completed_at=completed_at_since
        ).acount()

        if on_last_completed_count == 1:
            filter_kwargs = {"completed_at__gt": completed_at_since}
        else:
            filter_kwargs = {"completed_at__gte": completed_at_since}

        return [
            wr
            async for wr in WorkRequest.objects.filter(
                **filter_kwargs
            ).order_by("completed_at")
        ]

    async def _send_pending_work_requests_since_last_completed(self) -> None:
        completed_at_since = self._query_string.get("completed_at_since")

        if completed_at_since is None:
            return

        for work_request in await self._get_pending_work_requests(
            DateTimeField().to_internal_value(completed_at_since)
        ):
            assert work_request.completed_at is not None
            await self._send_work_request_completed(
                work_request.id,
                work_request.workspace_id,
                work_request.completed_at.isoformat(),
                work_request.result,
            )

    async def work_request_completed(self, event: dict[str, Any]) -> None:
        """Work Request has completed."""
        work_request_id = event["work_request_id"]
        work_space_id = event["workspace_id"]
        completed_at = event["completed_at"]
        result = event["result"]

        await self._send_work_request_completed(
            work_request_id, work_space_id, completed_at, result
        )

    async def _send_work_request_completed(
        self,
        work_request_id: int,
        workspace_id: int,
        completed_at: str,
        result: str,
    ) -> None:
        if (
            self._workspaces_only is not None
            and workspace_id not in self._workspaces_only
        ):
            return

        serializer = OnWorkRequestCompleted(
            data={
                "type": "websocket.send",
                "text": "work_request_completed",
                "work_request_id": work_request_id,
                "completed_at": datetime.fromisoformat(completed_at),
                "result": result,
            }
        )

        serializer.is_valid(raise_exception=True)

        await self.send(
            text_data=JSONRenderer().render(serializer.data).decode("utf-8")
        )
