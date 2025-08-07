# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for consumers."""

import asyncio
from datetime import timedelta
from typing import Any, cast
from unittest import mock
from unittest.mock import PropertyMock
from urllib.parse import urlencode

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.utils import timezone
from rest_framework.fields import DateTimeField

from debusine.db.context import context
from debusine.db.models import Token, WorkRequest, Worker, Workspace
from debusine.server import consumers
from debusine.server.consumers import WorkerConsumer
from debusine.server.middlewares.token_last_seen_at import (
    TokenLastSeenAtMiddlewareChannels,
)
from debusine.server.routing import websocket_urlpatterns
from debusine.test.django import (
    BaseDjangoTestCase,
    TestCase,
    TransactionTestCase,
)
from debusine.test.test_utils import date_time_to_isoformat_rest_framework


class WebSocketURLTests(TestCase):
    """Tests for websocket_urlpatterns."""

    def test_websocket_urlpatterns(self) -> None:
        """Ensure websocket_urlpatterns provides a valid URLRouter config."""
        URLRouter(websocket_urlpatterns)


class WorkerConsumerMixin(BaseDjangoTestCase):
    """Methods used by different WorkerConsumer tests classes."""

    CONNECTED_PAYLOAD = {
        "type": "websocket.send",
        "text": "connected",
    }

    REQUEST_DYNAMIC_METADATA_PAYLOAD = {
        "type": "websocket.send",
        "text": "request_dynamic_metadata",
    }

    WORK_REQUEST_AVAILABLE_PAYLOAD = {
        "type": "websocket.send",
        "text": "work_request_available",
    }

    # mypy complains that database_sync_to_async is untyped, which is true,
    # but we can't fix that here.
    @database_sync_to_async  # type: ignore[misc]
    def acreate_worker(self) -> Worker:
        """Async version to return a new Worker."""
        return self._create_worker()

    def _create_worker(self) -> Worker:
        """Return a new Worker."""
        worker = Worker.objects.create_with_fqdn(
            "computer.lan",
            token=cast(TestCase, self).playground.create_bare_token(),
        )
        worker.set_dynamic_metadata(
            {
                "cpu_cores": 4,
                "sbuild:version": 1,
                "sbuild:available": True,
            }
        )
        return worker

    async def connect(
        self,
        *,
        token_key: str | None = None,
        wait_for_fully_connected: bool = True,
    ) -> WebsocketCommunicator:
        """Return WebsocketCommunicator to api/ws/1.0/worker/connect/."""
        headers = [(b'User-Agent', b'A user agent')]

        if token_key:
            headers.append((b'token', token_key.encode('utf-8')))

        communicator = WebsocketCommunicator(
            TokenLastSeenAtMiddlewareChannels(
                consumers.WorkerConsumer.as_asgi()
            ),
            'api/ws/1.0/worker/connect/',
            headers=headers,
        )

        connected, subprotocol = await communicator.connect()
        self.assertTrue(connected)

        if wait_for_fully_connected:
            self.assertEqual(
                await communicator.receive_json_from(timeout=3),
                self.CONNECTED_PAYLOAD,
            )

        return communicator

    async def connect_to_new_worker(
        self,
    ) -> tuple[WebsocketCommunicator, Worker]:
        """Return a communicator connected to a new worker."""
        worker = await self.acreate_worker()
        communicator = await self.connect(token_key=worker.token.key)

        # Assert that only a request for dynamic metadata is sent
        await self.assertRequestDynamicMetadata(communicator)
        self.assertTrue(await communicator.receive_nothing())

        await worker.arefresh_from_db()
        self.assertTrue(worker.connected())

        return communicator, worker

    def assertTaskExists(self, task_name: str) -> None:  # pragma: no cover
        """Fail if task_name is not in asyncio.all_tasks()."""
        for task in asyncio.all_tasks():
            if task.get_name() == task_name:
                return

        self.fail(f"Asynchronous task '{task_name}' does not exist")

    def assertTaskNotExists(self, task_name: str) -> None:  # pragma: no cover
        """Fail if task_name is in asyncio.all_tasks()."""
        for task in asyncio.all_tasks():
            if task.get_name() == task_name:
                self.fail(f"Asynchronous task '{task_name}' does exist")

    async def assertRequestDynamicMetadata(
        self, communicator: WebsocketCommunicator
    ) -> None:
        """Assert the next communicator msg is request for dynamic metadata."""
        self.assertEqual(
            await communicator.receive_json_from(),
            self.REQUEST_DYNAMIC_METADATA_PAYLOAD,
        )

    def patch_workerconsumer_REQUEST_DYNAMIC_METADATA_SECONDS_fast(
        self,
    ) -> mock.MagicMock:
        """Patch WorkerConsumer.REQUEST_DYNAMIC_METADATA_SECONDS to be fast."""
        patcher = mock.patch(
            'debusine.server.consumers.'
            'WorkerConsumer.REQUEST_DYNAMIC_METADATA_SECONDS',
            new_callable=PropertyMock,
            side_effect=[0, 3600],
        )

        sleep_until_dynamic_metadata_request_mock = patcher.start()
        self.addCleanup(patcher.stop)

        return sleep_until_dynamic_metadata_request_mock

    async def assert_client_rejected(
        self,
        token_key: str | None,
        reason: str,
        reason_code: str | None = None,
    ) -> None:
        """Assert that the client is rejected with a reason."""
        communicator = await self.connect(
            token_key=token_key, wait_for_fully_connected=False
        )

        msg = {
            'type': 'connection_rejected',
            'reason': reason,
        }
        if reason_code is not None:
            msg["reason_code"] = reason_code

        self.assertEqual(await communicator.receive_json_from(), msg)

        self.assertEqual(
            await communicator.receive_output(), {'type': 'websocket.close'}
        )


class WorkerConsumerTransactionTests(WorkerConsumerMixin, TransactionTestCase):
    """
    Tests for WorkerConsumer class.

    Some tests must be implemented in TransactionTestCase because of a bug
    using the database from a TestCase in async.

    See https://code.djangoproject.com/ticket/30448 and
    https://github.com/django/channels/issues/1091#issuecomment-701361358
    """

    async def test_disconnect(self) -> None:
        """Disconnect marks the Worker as disconnected."""
        communicator, worker = await self.connect_to_new_worker()

        await communicator.disconnect()

        await worker.arefresh_from_db()

        self.assertFalse(worker.connected())

    async def assert_messages_send_on_connect(
        self, token_key: str, expected_msgs: list[dict[str, str]]
    ) -> None:
        """
        Connect authenticating with token_key and expects expected_msgs.

        The order of the received and expected_msgs is not enforced. The
        number of received and expected messages must match.
        """
        communicator = await self.connect(token_key=token_key)

        received_messages = []

        while True:
            try:
                received_messages.append(await communicator.receive_json_from())
            except asyncio.exceptions.TimeoutError:
                break

        self.assertEqual(len(expected_msgs), len(received_messages))

        for message in expected_msgs:
            self.assertIn(message, received_messages)

        await communicator.disconnect()

    async def test_connect_valid_token(self) -> None:
        """Connect succeeds and a request for dynamic metadata is received."""
        worker = await self.acreate_worker()

        await self.assert_messages_send_on_connect(
            worker.token.key, [self.REQUEST_DYNAMIC_METADATA_PAYLOAD]
        )

    async def test_connected_send_work_request_available_running(self) -> None:
        """Connect succeeds and consumer send expected two messages."""
        worker = await self.acreate_worker()

        with context.disable_permission_checks():
            await database_sync_to_async(self.playground.create_work_request)(
                status=WorkRequest.Statuses.PENDING, worker=worker
            )

        await self.assert_messages_send_on_connect(
            worker.token.key,
            [
                self.REQUEST_DYNAMIC_METADATA_PAYLOAD,
                self.WORK_REQUEST_AVAILABLE_PAYLOAD,
            ],
        )

    async def test_connected_send_work_request_available_pending(self) -> None:
        """Connect succeeds and consumer send expected two messages."""
        worker = await self.acreate_worker()

        with context.disable_permission_checks():
            await database_sync_to_async(self.playground.create_work_request)(
                status=WorkRequest.Statuses.RUNNING, worker=worker
            )

        await self.assert_messages_send_on_connect(
            worker.token.key,
            [
                self.REQUEST_DYNAMIC_METADATA_PAYLOAD,
                self.WORK_REQUEST_AVAILABLE_PAYLOAD,
            ],
        )

    async def test_connected_token_without_worker(self) -> None:
        """
        Connect succeeds and an error message is returned.

        Reason of disconnection: token did not have a worker associated.
        """
        token = await Token.objects.acreate()

        await database_sync_to_async(token.enable)()

        # Assert that the server returns that the token does not exist
        await self.assert_client_rejected(
            token.key,
            "No token header, token not associated to a worker or not enabled",
            reason_code="TOKEN_DISABLED",
        )

    async def test_connected_token_not_enabled(self) -> None:
        """
        Connect succeeds and an error message is returned.

        Reason of disconnection: token was not enabled.
        """
        token = await Token.objects.acreate()
        await Worker.objects.acreate(registered_at=timezone.now(), token=token)

        await self.assert_client_rejected(
            token.key,
            "No token header, token not associated to a worker or not enabled",
            reason_code="TOKEN_DISABLED",
        )

    async def test_disconnect_cancel_request_dynamic_metadata(self) -> None:
        """Disconnect cancels the dynamic metadata refresher."""
        self.assertTaskNotExists('dynamic_metadata_refresher')

        communicator, worker = await self.connect_to_new_worker()

        self.assertTaskExists('dynamic_metadata_refresher')

        await communicator.disconnect()

        self.assertTaskNotExists('dynamic_metadata_refresher')

    async def test_disconnect_leaves_work_request_as_running(self) -> None:
        """Worker disconnect does not interrupt the associated WorkRequest."""
        communicator, worker = await self.connect_to_new_worker()

        with context.disable_permission_checks():
            work_request: WorkRequest = await database_sync_to_async(
                self.playground.create_work_request
            )()

        await database_sync_to_async(work_request.assign_worker)(worker)
        await database_sync_to_async(work_request.mark_running)()

        self.assertEqual(work_request.status, work_request.Statuses.RUNNING)

        await communicator.disconnect()

        await worker.arefresh_from_db()

        await work_request.arefresh_from_db()

        self.assertFalse(worker.connected())

        # Assert that work_request is left as running. The Worker
        # is expected to re-connect and pick it up or to update the
        # status to COMPLETED (via the API).
        #
        # During the execution of a task, debusine-server will send
        # pings to the debusine-worker (via daphne defaults). debusine-worker
        # will not be able to respond until the task has finished.
        # This causes debusine server to disconnect the worker during
        # the execution of the task.
        #
        # If the status of the work_request changed from RUNNING to
        # PENDING (for example) when debusine-worker tries to update
        # the status to COMPLETED, debusine-server would reject it because
        # a work-request cannot transition from PENDING to COMPLETED.
        self.assertEqual(work_request.status, work_request.Statuses.RUNNING)

    async def test_disconnect_refreshes_worker_from_db(self) -> None:
        """
        Worker disconnect refreshes worker from DB.

        Otherwise asynchronous changes to the worker (such as editing static
        metadata) may be overwritten.
        """
        communicator, worker = await self.connect_to_new_worker()

        def update_static_metadata(worker: Worker) -> None:
            worker.static_metadata = {"system:architectures": ["amd64", "i386"]}
            worker.save()

        await database_sync_to_async(update_static_metadata)(worker)

        await communicator.disconnect()

        await worker.arefresh_from_db()

        self.assertEqual(
            worker.static_metadata, {"system:architectures": ["amd64", "i386"]}
        )
        self.assertFalse(worker.connected())

    async def test_request_dynamic_metadata_after_connect(self) -> None:
        """Debusine sends request_dynamic_metadata_updated after connect."""
        worker = await self.acreate_worker()

        self.patch_workerconsumer_REQUEST_DYNAMIC_METADATA_SECONDS_fast()

        communicator = await self.connect(token_key=worker.token.key)

        # debusine sends a request for dynamic metadata because the worker
        # just connected
        await self.assertRequestDynamicMetadata(communicator)

        # sends another one because in this test the method
        # self.patch_workerconsumer_REQUEST_DYNAMIC_METADATA_SECONDS_fast()
        # was called and there is no waiting time
        await self.assertRequestDynamicMetadata(communicator)

        # Nothing else is received for now (the next request for dynamic
        # metadata would happen in 3600 seconds, as per
        # self.patch_workerconsumer_REQUEST_DYNAMIC_METADATA_SECONDS_fast()
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()

    async def test_connect_redis_not_available(self) -> None:
        """
        Connect succeeds and an error message is returned.

        For example the Redis server was not available at the time of
        connection.
        """
        patcher = mock.patch(
            'debusine.server.consumers.WorkerConsumer._channel_layer_group_add',
            side_effect=OSError,
            autospec=True,
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        token = await database_sync_to_async(
            self.playground.create_bare_token
        )()
        await Worker.objects.acreate(
            registered_at=timezone.now(),
            token=token,
        )

        await self.assert_client_rejected(token.key, 'Service unavailable')
        mocked.assert_called()

    async def test_worker_disabled(self) -> None:
        """Worker is disabled and msg disconnection msg sent to the worker."""
        communicator, worker = await self.connect_to_new_worker()

        await communicator.send_input({"type": "worker.disabled"})

        def get_token_hash() -> str:
            assert worker.token is not None
            return worker.token.hash

        hash_ = await database_sync_to_async(get_token_hash)()

        self.assertEqual(
            await communicator.receive_json_from(),
            {
                "type": "connection_closed",
                "reason": f'Token has been disabled (token hash: "{hash_}")',
                "reason_code": "TOKEN_DISABLED",
            },
        )

        await communicator.disconnect()

    async def test_work_request_assigned(self) -> None:
        """Assert it sends message to the worker."""
        communicator, worker = await self.connect_to_new_worker()

        def assign_work_request(worker: Worker) -> None:
            work_request = self.playground.create_work_request()
            work_request.assign_worker(worker)

        with context.disable_permission_checks():
            await database_sync_to_async(assign_work_request)(worker)

        self.assertEqual(
            await communicator.receive_json_from(),
            {"text": "work_request_available", "type": "websocket.send"},
        )

        await communicator.disconnect()

    async def test_connect_invalid_token(self) -> None:
        """
        Connect succeeds and an error message is returned.

        Reason of disconnection: The token does not exist in the database
        """
        await self.assert_client_rejected(
            "does-not-exist",
            "No token header, token not associated to a worker or not enabled",
            reason_code="TOKEN_DISABLED",
        )


class WorkerConsumerTests(WorkerConsumerMixin, TestCase):
    """Tests for the WorkerConsumer class."""

    async def test_connect_without_token(self) -> None:
        """
        Connect succeeds and an error message is returned.

        Reason of disconnection: missing 'token' header.
        """
        await self.assert_client_rejected(
            None,
            "No token header, token not associated to a worker or not enabled",
            reason_code="TOKEN_DISABLED",
        )

    def test_get_header_value_key_found(self) -> None:
        """WorkerConsumer.get_header_value return the key (str)."""
        worker = WorkerConsumer()

        token = '1fb371ea69dca7b'

        worker.scope = {'headers': [(b'token', token.encode('utf-8'))]}

        self.assertEqual(worker.get_header_value('token'), token)

    def test_get_header_value_key_not_found(self) -> None:
        """WorkerConsumer.get_header_value return None when key not found."""
        worker = WorkerConsumer()

        worker.scope = {'headers': []}

        self.assertIsNone(worker.get_header_value('token'))

    async def test_disconnect_while_not_connected(self) -> None:
        """
        WorkerConsumer.disconnect returns without exceptions.

        Check that WorkerConsumer.disconnect() does not raise exceptions
        when called with a worker that has not connected.
        """
        worker = WorkerConsumer()
        await worker.disconnect(0)


class WorkRequestCompletedConsumerTests(TransactionTestCase):
    """Tests for OnWorkRequestCompletedConsumer class."""

    CONNECTED_PAYLOAD = {
        "type": "websocket.send",
        "text": "connected",
    }

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.token = self.playground.create_user_token()

    async def connect(
        self,
        *,
        token_key: str | None = None,
        query_params: dict[str, Any] = {},
        wait_for_fully_connected: bool = True,
    ) -> WebsocketCommunicator:
        """Connect to the WorkRequestCompleted endpoint."""
        headers = [(b"User-Agent", b"A user agent")]

        if token_key is not None:
            headers.append((b"token", token_key.encode("utf-8")))

        query_params_formatted = urlencode(query_params)

        communicator = WebsocketCommunicator(
            TokenLastSeenAtMiddlewareChannels(
                consumers.OnWorkRequestCompletedConsumer.as_asgi()
            ),
            "api/ws/1.0/work-request/on-completed/?" + query_params_formatted,
            headers=headers,
        )

        connected, subprotocol = await communicator.connect()

        self.assertTrue(connected)

        if wait_for_fully_connected:
            self.assertEqual(
                await communicator.receive_json_from(), self.CONNECTED_PAYLOAD
            )

        return communicator

    async def test_connect(self) -> None:
        """Connect to receive notifications that a Work Request completed."""
        communicator = await self.connect(token_key=self.token.key)

        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()

    async def test_disconnect(self) -> None:
        """Ensure we clean-up, cf. #225."""
        consumer = consumers.OnWorkRequestCompletedConsumer()

        consumer.channel_layer = mock.AsyncMock()
        consumer.channel_name = "some-channel-name"
        consumer._channel_added_to_group = True
        await consumer.disconnect("close_code")

        consumer.channel_layer.group_discard.assert_awaited_once_with(
            "work_request_completed", consumer.channel_name
        )

    async def test_connect_rejected_no_token_header(self) -> None:
        """Connect is rejected: no token was provided."""
        communicator = await self.connect(wait_for_fully_connected=False)

        self.assertEqual(
            await communicator.receive_json_from(),
            {
                "reason": "No token header, token not associated to a user or "
                "not enabled",
                "type": "connection_rejected",
            },
        )

        await communicator.disconnect()

    async def test_connect_rejected_workspace_not_found(self) -> None:
        """Connect is rejected: workspace is not found."""
        communicator = await self.connect(
            token_key=self.token.key,
            query_params={"workspaces": "does-not-exist"},
            wait_for_fully_connected=False,
        )

        self.assertEqual(
            await communicator.receive_json_from(),
            {
                "reason": 'Workspace "does-not-exist" not found',
                "type": "connection_rejected",
            },
        )

        await communicator.disconnect()

    async def test_connect_receive_greater_than_last_modified(self) -> None:
        """
        Consumer filter out WorkRequest that completed_at < completed_at_since.

        There are two work requests:
        -WorkRequest work_request_notified completed_at = now() - 1 minute
        -WorkRequest work_request_to_notify completed at after now()

        Client connects and passes now. Only the work_request_to_notify
        is returned.
        """
        result = WorkRequest.Results.SUCCESS

        now = timezone.now()

        with context.disable_permission_checks():
            work_request_notified = await database_sync_to_async(
                self.playground.create_work_request
            )(
                assign_new_worker=True,
                mark_running=True,
                result=result,
            )

        work_request_notified.completed_at = now - timedelta(minutes=1)
        await work_request_notified.asave()

        work_request_to_notify = await database_sync_to_async(
            self.playground.create_work_request
        )(
            assign_new_worker=True,
            mark_running=True,
            result=result,
        )

        communicator = await self.connect(
            token_key=self.token.key, query_params={"completed_at_since": now}
        )

        actual = await communicator.receive_json_from()

        self.assertEqual(
            actual,
            {
                "completed_at": DateTimeField().to_representation(
                    work_request_to_notify.completed_at
                ),
                "result": str(work_request_to_notify.result),
                "text": "work_request_completed",
                "type": "websocket.send",
                "work_request_id": work_request_to_notify.id,
            },
        )

        # Received only one WorkRequest
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()

    async def test_connect_receive_no_include_last_modified(self) -> None:
        """
        Consumer filter out WorkRequest that completed_at == completed_at_since.

        Only one WorkRequest and completed_at == last_completed. Consumer
        does not return it: the client was already aware of this one.
        """
        result = WorkRequest.Results.SUCCESS

        now = timezone.now()

        with context.disable_permission_checks():
            await database_sync_to_async(self.playground.create_work_request)(
                assign_new_worker=True,
                mark_running=True,
                result=result,
                completed_at=now,
            )

        communicator = await self.connect(
            token_key=self.token.key, query_params={"completed_at_since": now}
        )

        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()

    async def test_connect_receive_include_last_modified(self) -> None:
        """
        Consumer return 2 WorkRequests with completed_at == completed_at_since.

        There are two WorkRequests where completed_at == last_completed. The
        client might have processed either one or two of them. The server
        returns both of them to avoid any risk of the client
        processing one of them.

        This case is very unusual: the server marked two WorkRequests
        with the same completed_at to the milliseconds.
        """
        result = WorkRequest.Results.SUCCESS

        now = timezone.now()

        with context.disable_permission_checks():
            work_request_last_created_1 = await database_sync_to_async(
                self.playground.create_work_request
            )(
                assign_new_worker=True,
                mark_running=True,
                result=result,
            )

        work_request_last_created_1.completed_at = now
        await work_request_last_created_1.asave()

        work_request_last_created_2 = await database_sync_to_async(
            self.playground.create_work_request
        )(
            assign_new_worker=True,
            mark_running=True,
            result=result,
            completed_at=now,
        )

        communicator = await self.connect(
            token_key=self.token.key, query_params={"completed_at_since": now}
        )

        actual = await communicator.receive_json_from()

        work_request_completed_at = DateTimeField().to_representation(
            work_request_last_created_1.completed_at
        )

        self.assertEqual(
            actual,
            {
                "completed_at": work_request_completed_at,
                "result": str(work_request_last_created_1.result),
                "text": "work_request_completed",
                "type": "websocket.send",
                "work_request_id": work_request_last_created_1.id,
            },
        )

        actual = await communicator.receive_json_from()
        self.assertEqual(
            actual,
            {
                "completed_at": work_request_completed_at,
                "result": str(work_request_last_created_2.result),
                "text": "work_request_completed",
                "type": "websocket.send",
                "work_request_id": work_request_last_created_2.id,
            },
        )

        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()

    async def create_work_request(self, workspace: Workspace) -> WorkRequest:
        """Create a work request."""
        work_request = await database_sync_to_async(
            self.playground.create_work_request
        )(
            workspace=workspace,
            assign_new_worker=True,
            mark_running=True,
            result=WorkRequest.Results.SUCCESS,
        )
        assert isinstance(work_request, WorkRequest)
        return work_request

    async def test_work_request_completed_selected_workspace(self) -> None:
        """Notification is received: from a monitored workspace."""
        with context.disable_permission_checks():
            workspace_name = "lts"
            workspace = await database_sync_to_async(
                self.playground.create_workspace
            )(name=workspace_name)

        workspace_names = f"{workspace_name},{workspace_name}"
        communicator = await self.connect(
            token_key=self.token.key,
            query_params={"workspaces": workspace_names},
        )

        # Wait that the communicator is fully connected before creating
        # the work request
        self.assertTrue(await communicator.receive_nothing())

        work_request = await self.create_work_request(workspace)
        assert work_request.completed_at is not None

        actual = await communicator.receive_json_from()
        self.assertEqual(
            actual,
            {
                "type": "websocket.send",
                "text": "work_request_completed",
                "work_request_id": work_request.id,
                "completed_at": date_time_to_isoformat_rest_framework(
                    work_request.completed_at
                ),
                "result": work_request.result,
            },
        )

        await communicator.disconnect()

    async def test_work_request_completed_workspace_not_monitored(self) -> None:
        """Notification not received: from a non-monitored workspace."""
        # Need to exist when connecting
        with context.disable_permission_checks():
            await database_sync_to_async(self.playground.create_workspace)(
                name=settings.DEBUSINE_DEFAULT_WORKSPACE
            )

            workspace = await database_sync_to_async(
                self.playground.create_workspace
            )(name="lts")

        communicator = await self.connect(
            token_key=self.token.key,
            query_params={"workspaces": settings.DEBUSINE_DEFAULT_WORKSPACE},
        )

        # Wait that the communicator is fully connected before creating
        # the work request
        self.assertTrue(await communicator.receive_nothing())

        result = WorkRequest.Results.SUCCESS

        await database_sync_to_async(self.playground.create_work_request)(
            workspace=workspace,
            assign_new_worker=True,
            mark_running=True,
            result=result,
        )
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()

    async def test_work_request_completed_no_workspace_specified(self) -> None:
        """Notification is received: all workspaces are monitored."""
        communicator = await self.connect(token_key=self.token.key)

        result = WorkRequest.Results.SUCCESS

        with context.disable_permission_checks():
            workspace = await database_sync_to_async(
                self.playground.create_workspace
            )()

        work_request = await self.create_work_request(workspace)
        assert work_request.completed_at is not None

        actual = await communicator.receive_json_from()
        self.assertEqual(
            actual,
            {
                "type": "websocket.send",
                "text": "work_request_completed",
                "work_request_id": work_request.id,
                "completed_at": date_time_to_isoformat_rest_framework(
                    work_request.completed_at
                ),
                "result": result,
            },
        )

        await communicator.disconnect()
