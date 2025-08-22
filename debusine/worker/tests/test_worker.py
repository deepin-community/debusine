# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the Worker."""

import asyncio
import concurrent.futures
import functools
import logging
import secrets
import signal
import socket
import threading
import time
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from unittest import mock
from unittest.mock import MagicMock

import aiohttp
import tenacity
from aiohttp import RequestInfo, WSMessage, WSMsgType
from aiohttp.client_reqrep import ConnectionKey
from aiohttp.web import json_response
from aiohttp.web_exceptions import HTTPForbidden, HTTPInternalServerError
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

import debusine.tasks.models as task_models
from debusine.artifacts.models import RuntimeStatistics, TaskTypes
from debusine.client.debusine import Debusine
from debusine.client.exceptions import TokenDisabledError
from debusine.client.models import model_to_json_serializable_dict
from debusine.tasks import (
    BaseTask,
    BaseTaskWithExecutor,
    Noop,
    Sbuild,
    TaskConfigError,
)
from debusine.tasks.executors import ExecutorStatistics, UnshareExecutor
from debusine.tasks.models import (
    BackendType,
    OutputData,
    OutputDataError,
    SbuildData,
    SbuildInput,
    WorkerType,
)
from debusine.tasks.tests.helper_mixin import (
    SampleBaseExternalTask,
    SampleBaseTask,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_work_request_response,
)
from debusine.worker import Worker
from debusine.worker.config import ConfigHandler
from debusine.worker.system_information import (
    cpu_time,
    host_architecture,
    system_metadata,
)
from debusine.worker.tests import server
from debusine.worker.tests.server import ServerConfig


class WorkerTests(TestCase, server.DebusineAioHTTPTestCase):
    """Test Worker class."""

    async def setUpAsync(self) -> None:
        """Initialize TestWorker."""
        await super().setUpAsync()
        self.worker: Worker | None = None

        self.api_url = str(self.server.make_url('')) + "/api"

        self.config_temp_directory = self.create_temp_config_directory(
            {'General': {'api-url': self.api_url}}
        )

        self.config = ConfigHandler(directories=[self.config_temp_directory])
        self.config['General']['log-file'] = '/dev/null'

        self.default_sigint_handler = signal.getsignal(signal.SIGINT)
        self.default_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def tearDown(self) -> None:
        """Cleanup after executing a test."""
        super().tearDown()
        # Restore signal handlers. Cli.execute() changes them
        signal.signal(signal.SIGINT, self.default_sigint_handler)
        signal.signal(signal.SIGTERM, self.default_sigterm_handler)

    async def tearDownAsync(self) -> None:
        """Asynchronous cleanup."""
        await super().tearDownAsync()
        if self.worker:
            await self.worker.close()

    def setup_valid_token(self) -> None:
        """Set a valid token in the test server and client configuration."""
        token = '6c931875627131b5135b7de3371c44'
        self.server_config.registered_token = token
        self.config.write_token(token)

    def setup_worker(
        self,
        config: ConfigHandler | None = None,
        *,
        connect: bool = False,
        log_file: str | None = None,
        log_level: str | None = None,
        worker_type: Literal[
            WorkerType.EXTERNAL, WorkerType.SIGNING
        ] = WorkerType.EXTERNAL,
        assert_raise_try_again_called: bool = False,
        disable_endless_retry: bool = True,
    ) -> None:
        """Set a new worker to self.worker (with config or self.config)."""
        if config is None:
            config = self.config

        self.worker = Worker(
            log_file=log_file,
            log_level=log_level,
            worker_type=worker_type,
            config=config,
        )

        try:
            # If the caller of setup_worker created an event loop
            # then let the worker use it.
            self.worker._set_main_event_loop()
        except RuntimeError:
            pass

        if disable_endless_retry:
            raise_try_again_mock = self.disable_endless_retry()

        if connect:
            self.async_run(self.worker.connect())

        if assert_raise_try_again_called:
            raise_try_again_mock.assert_called()

    def patch_http_method(self, http_method: str, **kwargs: Any) -> MagicMock:
        """Patch a specific HTTP method of Worker's HTTP Client."""
        # setup_worker must be called first.
        assert self.worker is not None

        patcher = mock.patch.object(
            self.worker._async_http_client, http_method, autospec=True
        )
        mocked = patcher.start()
        for key, value in kwargs.items():
            setattr(mocked, key, value)
        self.addCleanup(patcher.stop)
        return mocked

    def patch_worker(self, attribute: str, **kwargs: Any) -> mock.AsyncMock:
        """Patch an attribute of the worker object."""
        patcher = mock.patch.object(self.worker, attribute, autospec=True)
        mocked = patcher.start()
        for key, value in kwargs.items():
            setattr(mocked, key, value)
        self.addCleanup(patcher.stop)
        return mocked

    @staticmethod
    def async_run(coroutine: Awaitable[Any]) -> None:
        """Run coroutine in an event loop."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(coroutine)

    def patch_config_handler(self) -> MagicMock:
        """
        Patch ConfigHandler class.

        :return: the MagicMock
        """
        patch = mock.patch(
            "debusine.worker._worker.ConfigHandler", autospec=True
        )
        config_handler_mocked = patch.start()
        self.addCleanup(patch.stop)

        return config_handler_mocked

    def test_worker_init(self) -> None:
        """Init without any keyword."""
        mocked_config_handler = self.patch_config_handler()

        worker = Worker(log_file="/dev/null", log_level="WARNING")

        # Worker() created a ConfigHandler default object and set it
        # to Worker._config
        self.assertIsNotNone(worker._config)
        mocked_config_handler.assert_called_once_with()

    def test_worker_init_signing(self) -> None:
        """Signing workers use different dirs and require an HTTPS api-url."""
        mocked_config_handler = self.patch_config_handler()

        worker = Worker(
            log_file="/dev/null",
            log_level="WARNING",
            worker_type=WorkerType.SIGNING,
        )

        self.assertIsNotNone(worker._config)
        mocked_config_handler.assert_called_once_with(
            directories=[
                str(Path.home() / ".config/debusine/signing"),
                "/etc/debusine/signing",
            ],
            require_https=True,
        )

    def test_worker_init_use_config_handler(self) -> None:
        """worker.set_config_handler() sets the ConfigHandler."""
        self.setup_worker(log_file="/dev/null", log_level="WARNING")
        assert self.worker is not None

        self.assertIs(self.worker._config, self.config)

    def test_registration_success(self) -> None:
        """
        Worker client registers successfully.

        Client creates a token, sends it to the server and save to the
        configuration.
        """
        # No token in the configuration
        self.assertIsNone(self.config.token)

        # Worker connects: it will create a token, send and save
        self.setup_worker(connect=True)

        # A token has been retrieved
        self.assertIsNotNone(self.config.token)

        registration_request = self.server_requests[-2]
        connect_request = self.server_requests[-1]

        self.assertEqual(registration_request.path, '/api/1.0/worker/register/')
        self.assertEqual(connect_request.path, '/api/ws/1.0/worker/connect/')

        # Server side received expected registration data
        self.assertEqual(
            registration_request.json_content,
            {
                'token': self.config.token,
                'fqdn': socket.getfqdn(),
                'worker_type': WorkerType.EXTERNAL,
            },
        )
        assert registration_request.headers is not None
        self.assertNotIn('token', registration_request.headers)

    def test_registration_success_activation_token(self) -> None:
        """Worker client registers successfully with an activation token."""
        activation_token = secrets.token_hex(32)
        activation_token_path = Path(self.config._activation_token_file)
        activation_token_path.write_bytes(activation_token.encode())
        activation_token_path.chmod(0o600)
        self.assertEqual(self.config.activation_token, activation_token)

        # No token in the configuration
        self.assertIsNone(self.config.token)

        # Worker connects: it will create a token, send and save
        self.setup_worker(connect=True)

        # A token has been retrieved
        self.assertIsNotNone(self.config.token)

        registration_request = self.server_requests[-2]
        connect_request = self.server_requests[-1]

        self.assertEqual(registration_request.path, '/api/1.0/worker/register/')
        self.assertEqual(connect_request.path, '/api/ws/1.0/worker/connect/')

        # Server side received expected registration data
        self.assertEqual(
            registration_request.json_content,
            {
                'token': self.config.token,
                'fqdn': socket.getfqdn(),
                'worker_type': WorkerType.EXTERNAL,
            },
        )
        assert registration_request.headers is not None
        self.assertEqual(
            registration_request.headers['token'], activation_token
        )

    def test_registration_failure(self) -> None:
        """
        Worker client registration failure.

        The server returns 403, the client raises WorkerRegistrationError.
        """
        self.server_config.register_response_status_code = (
            HTTPForbidden.status_code
        )

        log_message = (
            'Could not register. Server HTTP status code: 403 Body: error'
        )

        self.setup_worker()
        assert self.worker is not None
        with (
            self.assertLogsContains(log_message),
            self.assertRaisesSystemExit(3),
        ):
            self.async_run(self.worker.connect())

    def test_connection_success(self) -> None:
        """Worker client connects successfully."""
        self.setup_valid_token()

        self.setup_worker(connect=True)
        assert self.server_latest_request is not None
        self.assertEqual(
            self.server_latest_request.path, '/api/ws/1.0/worker/connect/'
        )
        self.assertTrue(self.server_latest_request.is_authenticated)

    def disable_endless_retry(self) -> mock.MagicMock:
        """Disable Worker._try_again() raising tenacity.RetryAgain."""
        patcher = mock.patch.object(
            self.worker, "_raise_try_again", autospec=True
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked

    def test_connection_failure(self) -> None:
        """Worker client cannot connect: invalid token."""
        self.registered_token = 'f924ba0099588bf6'

        self.config.write_token('e3c0284')

        self.setup_worker(connect=True, assert_raise_try_again_called=True)

        assert self.server_latest_request is not None
        self.assertEqual(
            self.server_latest_request.path, '/api/ws/1.0/worker/connect/'
        )
        self.assertFalse(self.server_latest_request.is_authenticated)

    async def test_connection_reset_error(self) -> None:
        """Worker client handles ConnectionResetError exception."""
        self.setup_valid_token()
        self.setup_worker()
        assert self.worker is not None

        self.patch_worker("_process_message", side_effect=ConnectionResetError)
        mock_wait_before_reconnect = self.patch_worker(
            "_asyncio_sleep",
            side_effect=[None],
        )

        self.server_config.connect_response_bodies = [{}]

        log_message = 'ConnectionResetError, will reconnect'

        # It tried twice to connect: once before the call to
        # Worker._wait_before_reconnect and once when it has been executed
        with (
            self.assertLogsContains(
                log_message, expected_count=2, level=logging.DEBUG
            ),
            self.assertRaises(StopAsyncIteration),
        ):
            await self.worker.connect()

        # It's called twice: first it returns None and the second time raises
        # and exception because side_effect=[None]
        self.assertEqual(mock_wait_before_reconnect.call_count, 2)

    async def test_connection_read_error(self) -> None:
        """Worker cannot read data from the server (low level disconnection)."""
        self.setup_worker()
        assert self.worker is not None
        mock_post = self.patch_http_method(
            "post",
            side_effect=aiohttp.client_exceptions.ClientResponseError(
                RequestInfo(
                    URL('https://test.com'),
                    '',
                    CIMultiDictProxy(CIMultiDict()),
                    URL('https://test.com'),
                ),
                (),
            ),
        )
        log_message = (
            'Could not register. Server HTTP status code: 0 '
            'Body: Not available'
        )
        with (
            self.assertLogsContains(log_message),
            self.assertRaisesSystemExit(3),
        ):
            await self.worker.connect()

        self.assertTrue(mock_post.called)

    async def test_client_connector_error(self) -> None:
        """Worker cannot connect to the server: server unreachable."""
        self.setup_worker()
        assert self.worker is not None
        connection_key = mock.create_autospec(spec=ConnectionKey)
        mock_post = self.patch_http_method(
            "post",
            side_effect=aiohttp.client_exceptions.ClientConnectorError(
                connection_key, OSError("Unreachable")
            ),
        )
        log_message = "Could not register. Server unreachable: "
        with (
            self.assertLogsContains(log_message),
            self.assertRaisesSystemExit(3),
        ):
            await self.worker.connect()

        self.assertTrue(mock_post.called)

    def test_connection_rejected(self) -> None:
        """Worker tries to connect, server returns 500."""
        self.server_config.register_response_status_code = (
            HTTPInternalServerError.status_code
        )

        log_message = (
            'Could not register. Server HTTP status code: 500 Body: error'
        )

        self.setup_worker()
        assert self.worker is not None

        with (
            self.assertLogsContains(log_message),
            self.assertRaisesSystemExit(3),
        ):
            self.async_run(self.worker.connect())

    async def test_connection_error_connection_refused(self) -> None:
        """Worker client cannot connect: connection refused."""
        # Because the token exists the worker will try to connect
        # instead of to register
        self.config.write_token('e3c0284')

        await self.server.close()

        self.setup_worker()
        assert self.worker is not None
        mocked_sleep = self.patch_worker(
            "_asyncio_sleep", side_effect=[None, None]
        )

        log_message = 'Error connecting to'

        # Assert that it tried three times to connect
        # The first one Work._wait_before_reconnect wasn't called, then
        # twice before Work._wait_before_reconnect returns None twice

        with (
            self.assertLogsContains(log_message, expected_count=3),
            self.assertRaises(StopAsyncIteration),
        ):
            # StopAsyncIteration because mocked_sleep side_effects
            await self.worker.connect()

        # It's called three times: twice return None and once raises
        # and exception because mocked_sleep.side_effect=[None, None]
        self.assertEqual(mocked_sleep.call_count, 3)

    async def test_do_wait_for_messages_raise_value_error(self) -> None:
        """Exception ValueError is raised (not retried via tenacity)."""
        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['token_disabled_error']
        ]

        self.setup_worker()
        assert self.worker is not None

        exception = ValueError
        self.patch_worker("_process_message", side_effect=exception)

        with self.assertRaises(exception):
            await self.worker.connect()

    async def test_connection_error_token_disabled_try_again(self) -> None:
        """Worker tries to connect again if server returns "TOKEN_DISABLED"."""
        self.setup_valid_token()
        assert self.config.token is not None
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['token_disabled_error']
        ]

        # No request to any URL...
        self.assertIsNone(self.server_latest_request)

        self.setup_worker()
        assert self.worker is not None
        mocked_method = self.patch_worker(
            '_asyncio_sleep', side_effect=[None, None]
        )

        with (
            self.assertRaises(StopAsyncIteration),
            self.assertLogsContains(
                self.config.token, expected_count=3
            ) as logs,
        ):
            # RuntimeError due to the implementation of the mocked
            # returning None twice
            await self.worker.connect()

        # It's called three times: twice _wait_before_reconnected returned None,
        # and once raises StopIteration
        self.assertEqual(mocked_method.call_count, 3)

        # Assert that the logs contain "Reason: The token is disabled"
        token_is_disabled_found = False
        for log in logs.output:  # pragma: no cover
            if "Reason: The token is disabled" in log:
                token_is_disabled_found = True
                break

        self.assertTrue(token_is_disabled_found)

    async def test_process_message_dynamic_metadata_request(self) -> None:
        """Worker receives a request to send the dynamic metadata."""
        executors_metadata = {"executor:unshare:available": True}
        tasks_metadata = {"sbuild:version": 1, "sbuild:available": True}

        analyze_executors_patcher = mock.patch(
            "debusine.worker._worker.analyze_worker_all_executors",
            autospec=True,
        )
        mocked_analyze_executors = analyze_executors_patcher.start()
        mocked_analyze_executors.return_value = executors_metadata
        self.addCleanup(analyze_executors_patcher.stop)

        analyze_tasks_patcher = mock.patch(
            'debusine.tasks.BaseTask.analyze_worker_all_tasks', autospec=True
        )
        mocked_analyze_tasks = analyze_tasks_patcher.start()
        mocked_analyze_tasks.return_value = tasks_metadata
        self.addCleanup(analyze_tasks_patcher.stop)

        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['request_dynamic_metadata']
        ]

        # No request to any URL...
        self.assertIsNone(self.server_latest_request)

        self.setup_worker()
        assert self.worker is not None
        await self.worker.connect()

        # The server sends a 'request_dynamic_metadata' and the worker
        # sends system_metadata(task_models.WorkerType.EXTERNAL),
        # analyze_worker_all_executors, and
        # BaseTask.analyze_worker_all_tasks

        assert self.server_latest_request is not None
        self.assertEqual(
            self.server_latest_request.path, '/api/1.0/worker/dynamic-metadata/'
        )

        self.assertTrue(self.server_latest_request.is_authenticated)

        self.assertEqual(
            self.server_latest_request.json_content,
            {
                **system_metadata(task_models.WorkerType.EXTERNAL),
                **executors_metadata,
                **tasks_metadata,
            },
        )

    async def test_process_message_dynamic_metadata_request_signing(
        self,
    ) -> None:
        """Signing workers send the correct worker type in dynamic metadata."""
        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES["request_dynamic_metadata"]
        ]
        self.assertIsNone(self.server_latest_request)

        self.setup_worker(worker_type=WorkerType.SIGNING)
        assert self.worker is not None
        with (
            mock.patch(
                "debusine.worker._worker.analyze_worker_all_executors",
                return_value={},
            ),
            mock.patch(
                "debusine.tasks.BaseTask.analyze_worker_all_tasks",
                return_value={},
            ),
        ):
            await self.worker.connect()

        assert self.server_latest_request is not None
        self.assertEqual(
            self.server_latest_request.path, "/api/1.0/worker/dynamic-metadata/"
        )
        self.assertTrue(self.server_latest_request.is_authenticated)
        self.assertEqual(
            self.server_latest_request.json_content,
            {
                **system_metadata(task_models.WorkerType.SIGNING),
            },
        )

    def assert_request_done_times(
        self, method: str, path: str, times: int
    ) -> None:
        """Assert that request method regexp_path has been done times."""
        count = 0
        for request in self.server_requests:
            if request.method == method and request.path == path:
                count += 1

        self.assertEqual(count, times)

    async def test_poll_statistics(self) -> None:
        """`_poll_statistics` accumulates statistics."""
        self.setup_worker()
        assert self.worker is not None
        self.worker._task_running = Sbuild(
            SbuildData(
                input=SbuildInput(source_artifact=1),
                host_architecture="amd64",
                backend=BackendType.UNSHARE,
                environment="debian/match:codename=sid",
            ).dict()
        )
        with mock.patch(
            "debusine.tasks.executors.images.ImageCache.image_artifact",
            return_value=create_artifact_response(id=1),
        ):
            self.worker._task_running.executor = UnshareExecutor(
                mock.create_autospec(Debusine), 1
            )
        self.worker._task_statistics = RuntimeStatistics()
        GiB = 1024 * 1024 * 1024
        got_statistics = [
            ExecutorStatistics(),
            ExecutorStatistics(
                disk_space=GiB,
                memory=GiB,
                available_disk_space=10 * GiB,
                available_memory=4 * GiB,
                cpu_count=2,
            ),
            ExecutorStatistics(
                disk_space=3 * GiB,
                memory=2 * GiB,
                available_disk_space=10 * GiB,
                available_memory=4 * GiB,
                cpu_count=2,
            ),
            ExecutorStatistics(
                disk_space=4 * GiB,
                memory=3 * GiB,
                available_disk_space=10 * GiB,
                available_memory=4 * GiB,
                cpu_count=2,
            ),
            ExecutorStatistics(
                disk_space=2 * GiB,
                memory=GiB,
                available_disk_space=10 * GiB,
                available_memory=4 * GiB,
                cpu_count=2,
            ),
        ]
        sleep_results = [None] * (len(got_statistics) - 1) + [
            asyncio.CancelledError
        ]

        with (
            self.assertRaises(asyncio.CancelledError),
            mock.patch(
                "debusine.tasks.executors.unshare.UnshareExecutor"
                ".get_statistics",
                side_effect=got_statistics,
            ),
            mock.patch("asyncio.sleep", side_effect=sleep_results),
        ):
            await self.worker._poll_statistics()

        self.assertEqual(
            self.worker._task_statistics,
            RuntimeStatistics(
                disk_space=4 * GiB,
                memory=3 * GiB,
                available_disk_space=10 * GiB,
                available_memory=4 * GiB,
                cpu_count=2,
            ),
        )

    async def test_poll_statistics_no_executor(self) -> None:
        """`_poll_statistics` ignores tasks without an executor."""
        self.setup_worker()
        assert self.worker is not None
        self.worker._task_running = Noop({"result": True})
        self.worker._task_statistics = RuntimeStatistics()

        with mock.patch("asyncio.sleep") as mock_sleep:
            await self.worker._poll_statistics()

        mock_sleep.assert_not_called()
        self.assertEqual(self.worker._task_statistics, RuntimeStatistics())

    @asynccontextmanager
    async def completed_task(
        self, *, keep_state: bool = False
    ) -> AsyncGenerator[None]:
        """
        Context manager that waits for a task to complete.

        :param keep_state: If True, don't reset state at the end of the
          task, to allow tests to inspect it.
        """
        assert self.worker is not None
        assert self.worker._main_event_loop is not None

        completed = asyncio.Event()
        original_reset_state = self.worker._reset_state

        async def report_completed(worker: Worker) -> None:
            if keep_state:
                # Worker._reset_state would do this; we should clean up too.
                if worker._task_lock.locked():  # pragma: no cover
                    worker._task_lock.release()
            else:
                original_reset_state()

            completed.set()

        with mock.patch(
            "debusine.worker._worker.Worker._reset_state",
            side_effect=functools.partial(
                asyncio.run_coroutine_threadsafe,
                report_completed(self.worker),
                self.worker._main_event_loop,
            ),
        ):
            async with asyncio.timeout(10):
                waiter_task = asyncio.create_task(completed.wait())
                yield
                await waiter_task

    async def receive_task_submit_result(
        self,
        task_type: TaskTypes,
        task_name: str,
        execute_result: bool,
        json_result: str,
        worker_type: Literal[
            WorkerType.EXTERNAL, WorkerType.SIGNING
        ] = WorkerType.EXTERNAL,
    ) -> None:
        """Worker fetches the task and validates submitted JSON."""
        task_class = BaseTask.class_from_name(task_type, task_name)

        patcher_execute = mock.patch.object(
            task_class, 'execute', autospec=True
        )
        mocked_execute = patcher_execute.start()
        mocked_execute.return_value = execute_result
        self.addCleanup(patcher_execute.stop)

        patcher_configure_server_access = mock.patch.object(
            task_class, "configure_server_access", autospec=True
        )
        mocked_configure_server_access = patcher_configure_server_access.start()
        self.addCleanup(patcher_configure_server_access.stop)

        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['work_request_available']
        ]

        # No request to any URL...
        self.assertIsNone(self.server_latest_request)

        self.setup_worker(worker_type=worker_type)
        assert self.worker is not None

        work_request_completed_mocked = (
            self.patch_debusine_work_request_completed_update()
        )

        with mock.patch(
            "debusine.tasks.executors.images.ImageCache.image_artifact",
            return_value=create_artifact_response(id=1),
        ):
            executor = UnshareExecutor(mock.create_autospec(Debusine), 1)
        GiB = 1024 * 1024 * 1024

        with (
            mock.patch(
                "debusine.tasks._task.BaseTaskWithExecutor.executor",
                create=True,
                new_callable=mock.PropertyMock,
                return_value=executor,
            ),
            mock.patch.object(
                executor,
                "get_statistics",
                return_value=ExecutorStatistics(
                    disk_space=2 * GiB,
                    memory=GiB,
                    available_disk_space=20 * GiB,
                    available_memory=8 * GiB,
                    cpu_count=4,
                ),
            ),
        ):
            async with self.completed_task(keep_state=True):
                await self.worker.connect()

        # The WorkRequest is executed...
        self.assertEqual(mocked_execute.call_count, 1)

        # Check the workspace and worker host architecture assigned to the
        # task
        task = mocked_execute.call_args[0][0]
        self.assertEqual(task.workspace_name, "TestWorkspace")
        self.assertEqual(task.worker_host_architecture, host_architecture())

        # BaseTask.configure_server_access was called
        debusine_server = mocked_configure_server_access.mock_calls[0][1][1]
        self.assertIsInstance(debusine_server, Debusine)
        self.assertEqual(debusine_server.token, self.config.token)
        self.assertEqual(debusine_server.base_api_url, self.config.api_url)

        work_request_completed_mocked.assert_called_once_with(
            task.work_request_id, json_result, mock.ANY
        )

        output_data = work_request_completed_mocked.call_args[0][2]
        self.assertIsInstance(output_data, OutputData)
        runtime_statistics = output_data.runtime_statistics
        assert runtime_statistics is not None
        assert self.worker._task_start_time is not None
        assert self.worker._task_start_cpu_time is not None
        self.assertLessEqual(
            runtime_statistics.duration,
            time.monotonic() - self.worker._task_start_time,
        )
        self.assertLessEqual(
            runtime_statistics.cpu_time,
            cpu_time() - self.worker._task_start_cpu_time,
        )
        if isinstance(task, BaseTaskWithExecutor):
            self.assertEqual(runtime_statistics.disk_space, 2 * GiB)
            self.assertEqual(runtime_statistics.memory, GiB)
            self.assertEqual(runtime_statistics.available_disk_space, 20 * GiB)
            self.assertEqual(runtime_statistics.available_memory, 8 * GiB)
            self.assertEqual(runtime_statistics.cpu_count, 4)
        else:
            self.assertIsNone(runtime_statistics.disk_space)
            self.assertIsNone(runtime_statistics.memory)
            self.assertIsNone(runtime_statistics.available_disk_space)
            self.assertIsNone(runtime_statistics.available_memory)
            self.assertIsNone(runtime_statistics.cpu_count)

    async def test_task_is_received_executed_submit_success(self) -> None:
        """Receive a task, execute it and submit success completion."""
        await self.receive_task_submit_result(
            TaskTypes.WORKER, "sbuild", True, "success"
        )

    async def test_task_is_received_executed_submit_failure(self) -> None:
        """Receive a task, executes it (failure) and submits failure."""
        await self.receive_task_submit_result(
            TaskTypes.WORKER, "sbuild", False, "failure"
        )

    async def test_print_no_work_request_available(self) -> None:
        """Worker logs 'No work request available'."""
        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['work_request_available']
        ]
        self.server_config.get_next_for_worker = (
            ServerConfig.GetNextForWorkerResponse.NO_WORK_REQUEST_PENDING
        )

        self.setup_worker()
        assert self.worker is not None

        log_message = 'No work request available'

        with self.assertLogsContains(log_message, level=logging.DEBUG):
            await self.worker.connect()

    async def test_log_work_request_ignored_already_running(self) -> None:
        """Worker receive two Work Request available, fetches only one."""
        lock = threading.Lock()
        lock.acquire()

        def wait(sbuild_self: Sbuild) -> None:
            sbuild_self  # fake usage for vulture
            lock.acquire()
            lock.release()

        patcher = mock.patch.object(Sbuild, "execute", autospec=True)
        mocked_execute = patcher.start()
        mocked_execute.side_effect = wait
        self.addCleanup(patcher.stop)

        # avoid NotFoundError in test env
        patcher = mock.patch(
            "debusine.client.debusine.Debusine.work_request_completed_update"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.setup_valid_token()

        work_request_available = ServerConfig.RESPONSES[
            "work_request_available"
        ]

        self.server_config.connect_response_bodies = [
            work_request_available,
            work_request_available,
        ]

        self.setup_worker()
        assert self.worker is not None
        self.patch_worker("_asyncio_sleep")
        mocked_lock = self.patch_worker("_task_lock")
        mocked_lock.acquire.side_effect = [True] + [False] * 61

        log_message = "Worker is busy and can't execute a new work request"

        async with self.completed_task():
            # Log that a Work Request is available (coming from the server)
            # but the Worker ignores it because the worker is already
            # executing a task
            with self.assertLogsContains(log_message):
                await self.worker.connect()

            # Sbuild.execute() is called only once
            self.assertEqual(mocked_execute.call_count, 1)

            # The server sent twice "work_request_available" and the worker
            # called get-next-for-worker/ once only: because a Task is
            # already being executed
            self.assert_request_done_times(
                "GET", "/api/1.0/work-request/get-next-for-worker/", times=1
            )

            # Allow the execute call to finish
            lock.release()

    async def test_log_work_request_cannot_report_exits(self) -> None:
        """Worker can't report to the server, so logs this and exits."""
        # Mock Sbuild.execute()
        patcher_execute = mock.patch.object(Sbuild, 'execute', autospec=True)
        mocked_execute = patcher_execute.start()
        mocked_execute.return_value = True
        self.addCleanup(patcher_execute.stop)

        patcher_configure_server_access = mock.patch.object(
            Sbuild, "configure_server_access", autospec=True
        )
        patcher_configure_server_access.start()
        self.addCleanup(patcher_configure_server_access.stop)

        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['work_request_available']
        ]

        self.setup_worker()
        assert self.worker is not None

        work_request_completed_mocked = (
            self.patch_debusine_work_request_completed_update()
        )
        work_request_completed_mocked.side_effect = Exception("network error")

        with (
            self.assertLogsContains("Cannot reach server", level=logging.ERROR),
            mock.patch(
                "debusine.worker.Worker._exit_worker", autospec=True
            ) as exit_worker,
        ):
            async with self.completed_task():
                await self.worker.connect()
            exit_worker.assert_called_once()

    async def assert_invalid_get_next_for_worker_logging(
        self, get_next_for_worker: ServerConfig.GetNextForWorkerResponse
    ) -> None:
        """Assert log_message is logged for get_next_for_worker response."""
        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['work_request_available']
        ]
        self.server_config.get_next_for_worker = get_next_for_worker

        self.setup_worker()
        assert self.worker is not None

        with self.assertLogsContains(
            "Invalid WorkRequest received from", level=logging.WARNING
        ):
            await self.worker.connect()

    async def test_request_work_request_invalid_work_request_response(
        self,
    ) -> None:
        """Worker logs 'Invalid content of...'."""
        await self.assert_invalid_get_next_for_worker_logging(
            ServerConfig.GetNextForWorkerResponse.INVALID_WORK_REQUEST
        )

    async def test_request_work_request_invalid_json_response(self) -> None:
        """Worker logs 'Invalid JSON response...'."""
        await self.assert_invalid_get_next_for_worker_logging(
            ServerConfig.GetNextForWorkerResponse.INVALID_JSON
        )

    async def assert_execute_work_request_send_and_log_error(
        self,
        error_msg: str,
        error_code: str,
        worker_type: Literal[
            WorkerType.EXTERNAL, WorkerType.SIGNING
        ] = WorkerType.EXTERNAL,
    ) -> None:
        """Assert error is sent to the server and logged."""
        self.setup_valid_token()
        self.server_config.connect_response_bodies = [
            ServerConfig.RESPONSES['work_request_available']
        ]

        self.setup_worker(worker_type=worker_type)
        assert self.worker is not None

        work_request_completed_mocked = (
            self.patch_debusine_work_request_completed_update()
        )

        with self.assertLogsContains(error_msg):
            async with self.completed_task():
                await self.worker.connect()

        work_request_completed_mocked.assert_called_once_with(
            mock.ANY, "error", mock.ANY
        )
        work_request_id, _, output_data = (
            work_request_completed_mocked.call_args[0]
        )
        self.assertIsInstance(work_request_id, int)
        self.assertIsInstance(output_data, OutputData)
        self.assertEqual(
            output_data.errors,
            [OutputDataError(message=error_msg, code=error_code)],
        )

    async def test_task_configure_error_submit_error(self) -> None:
        """Sbuild.configure fails, Worker submits error."""
        patcher = mock.patch.object(Sbuild, '_configure', autospec=True)
        mocked_configure = patcher.start()
        mocked_configure.side_effect = TaskConfigError('Invalid schema')
        self.addCleanup(patcher.stop)

        await self.assert_execute_work_request_send_and_log_error(
            "Task: sbuild Error configure: Invalid schema", "configure-failed"
        )

    async def test_server_task_on_external_worker(self) -> None:
        """An external worker refuses to run a server task."""

        class ServerTestData(task_models.BaseTaskData):
            pass

        class ServerTest(
            SampleBaseTask[ServerTestData, task_models.BaseDynamicTaskData],
        ):
            TASK_VERSION = 1
            TASK_TYPE = TaskTypes.SERVER

            def _execute(self) -> bool:
                raise NotImplementedError()

            def _upload_work_request_debug_logs(self) -> None:
                raise NotImplementedError()

        self.addCleanup(
            BaseTask._sub_tasks[TaskTypes.SERVER].__delitem__, "servertest"
        )

        next_work_request = create_work_request_response(
            id=52,
            task_type="Server",
            task_name="servertest",
            created_at=datetime.fromisoformat("2022-01-05T11:14:22.242178Z"),
            started_at=None,
            completed_at=None,
            duration=None,
            worker=58,
            task_data={},
            dynamic_task_data={},
            priority_base=0,
            priority_adjustment=0,
            status="running",
            result="",
            artifacts=[],
            workspace="TestWorkspace",
        )
        self.server_config.get_next_for_worker = json_response(
            model_to_json_serializable_dict(next_work_request)
        )

        await self.assert_execute_work_request_send_and_log_error(
            "Task: servertest is of type Server, not Worker", "wrong-task-type"
        )

    async def test_signing_task_on_external_worker(self) -> None:
        """An external worker refuses to run a signing task."""

        class SigningTestData(task_models.BaseTaskData):
            pass

        class SigningTest(
            SampleBaseExternalTask[
                SigningTestData, task_models.BaseDynamicTaskData
            ],
        ):
            TASK_VERSION = 1
            TASK_TYPE = TaskTypes.SIGNING

            def run(self, execute_directory: Path) -> bool:  # noqa: U100
                raise NotImplementedError()

        self.addCleanup(
            BaseTask._sub_tasks[TaskTypes.SIGNING].__delitem__, "signingtest"
        )

        next_work_request = create_work_request_response(
            id=52,
            task_type="Signing",
            task_name="signingtest",
            created_at=datetime.fromisoformat("2022-01-05T11:14:22.242178Z"),
            started_at=None,
            completed_at=None,
            duration=None,
            worker=58,
            task_data={},
            dynamic_task_data={},
            priority_base=0,
            priority_adjustment=0,
            status="running",
            result="",
            artifacts=[],
            workspace="TestWorkspace",
        )
        self.server_config.get_next_for_worker = json_response(
            model_to_json_serializable_dict(next_work_request)
        )

        await self.assert_execute_work_request_send_and_log_error(
            "Task: signingtest is of type Signing, not Worker",
            "wrong-task-type",
        )

    async def test_worker_task_on_signing_worker(self) -> None:
        """A signing worker refuses to run a worker task."""
        next_work_request = create_work_request_response(
            id=52,
            task_type="Worker",
            task_name="noop",
            created_at=datetime.fromisoformat("2022-01-05T11:14:22.242178Z"),
            started_at=None,
            completed_at=None,
            duration=None,
            worker=58,
            task_data={},
            dynamic_task_data={},
            priority_base=0,
            priority_adjustment=0,
            status="running",
            result="",
            artifacts=[],
            workspace="TestWorkspace",
        )
        self.server_config.get_next_for_worker = json_response(
            model_to_json_serializable_dict(next_work_request)
        )

        await self.assert_execute_work_request_send_and_log_error(
            "Task: noop is of type Worker, not Signing",
            "wrong-task-type",
            worker_type=WorkerType.SIGNING,
        )

    async def test_signing_task_on_signing_worker(self) -> None:
        """A signing worker can run a signing task."""

        class SigningTestData(task_models.BaseTaskData):
            pass

        class SigningTest(
            SampleBaseExternalTask[
                SigningTestData, task_models.BaseDynamicTaskData
            ],
        ):
            TASK_VERSION = 1
            TASK_TYPE = TaskTypes.SIGNING

            def run(self, execute_directory: Path) -> bool:  # noqa: U100
                raise NotImplementedError()

        self.addCleanup(
            BaseTask._sub_tasks[TaskTypes.SIGNING].__delitem__, "signingtest"
        )

        next_work_request = create_work_request_response(
            id=52,
            task_type="Signing",
            task_name="signingtest",
            created_at=datetime.fromisoformat("2022-01-05T11:14:22.242178Z"),
            started_at=None,
            completed_at=None,
            duration=None,
            worker=58,
            task_data={},
            dynamic_task_data={},
            priority_base=0,
            priority_adjustment=0,
            status="running",
            result="",
            artifacts=[],
            workspace="TestWorkspace",
        )
        self.server_config.get_next_for_worker = json_response(
            model_to_json_serializable_dict(next_work_request)
        )

        await self.receive_task_submit_result(
            TaskTypes.SIGNING,
            "signingtest",
            True,
            "success",
            worker_type=WorkerType.SIGNING,
        )

    async def test_unknown_task_name(self) -> None:
        """A worker refuses to run a task with an unknown name."""
        next_work_request = create_work_request_response(
            id=52,
            task_type="Worker",
            task_name="nonexistent",
            created_at=datetime.fromisoformat("2022-01-05T11:14:22.242178Z"),
            started_at=None,
            completed_at=None,
            duration=None,
            worker=58,
            task_data={},
            dynamic_task_data={},
            priority_base=0,
            priority_adjustment=0,
            status="running",
            result="",
            artifacts=[],
            workspace="TestWorkspace",
        )
        self.server_config.get_next_for_worker = json_response(
            model_to_json_serializable_dict(next_work_request)
        )

        await self.assert_execute_work_request_send_and_log_error(
            "Task: nonexistent Error setup:"
            " 'nonexistent' is not a registered Worker task_name",
            "setup-failed",
        )

    def patch_debusine_work_request_completed_update(self) -> MagicMock:
        """Patch self.worker._debusine. Return its mock."""
        # setup_worker must be called first.
        assert self.worker is not None

        patcher = mock.patch.object(
            self.worker._debusine,
            "work_request_completed_update",
            autospec=True,
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    async def test_task_execution_error_submit_error(self) -> None:
        """Sbuild.execute raises TaskConfigError, Worker submits error."""
        patcher_execute = mock.patch.object(Sbuild, 'execute', autospec=True)
        mocked_execute = patcher_execute.start()
        mocked_execute.side_effect = TaskConfigError('Broken')
        self.addCleanup(patcher_execute.stop)

        await self.assert_execute_work_request_send_and_log_error(
            'Task: sbuild Error execute: Broken', "execute-failed"
        )

    async def test_process_message_invalid_msg(self) -> None:
        """Worker._process_message returns False."""
        msg = WSMessage(WSMsgType.TEXT, '{"text": "something"}', None)

        self.setup_worker()
        assert self.worker is not None

        message_processed = await self.worker._process_message(msg)

        self.assertFalse(message_processed)

    async def test_process_message_txt_without_msg_key(self) -> None:
        """Worker._process_message returns False (no msg text)."""
        msg = WSMessage(WSMsgType.TEXT, '{"foo": "bar"}', None)
        self.setup_worker()
        assert self.worker is not None

        log_message = "Disconnected. Reason: 'unknown'"

        with self.assertLogsContains(log_message):
            message_processed = await self.worker._process_message(msg)

        self.assertFalse(message_processed)

    async def test_process_message_raise_token_disabled_error_disabled(
        self,
    ) -> None:
        """Worker._process_message logs reason of worker's not connection."""
        msg = WSMessage(
            WSMsgType.TEXT,
            '{"reason": "Disabled token", "reason_code": "TOKEN_DISABLED"}',
            None,
        )
        self.setup_worker()
        assert self.worker is not None

        with self.assertRaises(TokenDisabledError) as exc:
            await self.worker._process_message(msg)

        self.assertEqual(str(exc.exception), "Disabled token")

    async def test_process_message_raise_token_disabled_error_unknown(
        self,
    ) -> None:
        """Worker._process_message logs reason of worker's, exc is 'Unknown'."""
        msg = WSMessage(
            WSMsgType.TEXT,
            '{"reason_code": "TOKEN_DISABLED"}',
            None,
        )
        self.setup_worker()
        assert self.worker is not None

        with self.assertRaises(TokenDisabledError) as exc:
            await self.worker._process_message(msg)

        self.assertEqual(str(exc.exception), "Unknown")

    async def test_process_message_unknown_reason_code_is_logged_ignored(
        self,
    ) -> None:
        """Worker logs and returns False for msg with an unknown reason_code."""
        msg = WSMessage(
            WSMsgType.TEXT,
            '{"reason": "Disabled token",'
            '"reason_code": "SOMETHING_NOT_IMPLEMENTED"}',
            None,
        )

        self.setup_worker()
        assert self.worker is not None

        with self.assertLogsContains('SOMETHING_NOT_IMPLEMENTED'):
            processed = await self.worker._process_message(msg)

        self.assertFalse(processed)

    async def test_process_message_non_text_type(self) -> None:
        """Worker._process_message returns False for non-text type."""
        msg = WSMessage(WSMsgType.PING, None, None)
        self.setup_worker()
        assert self.worker is not None

        message_processed = await self.worker._process_message(msg)

        self.assertFalse(message_processed)

    async def test_process_message_invalid_msg_type(self) -> None:
        """Worker._process_message return False for an invalid message type."""
        msg = 'Something not an instance of WMessage'

        self.setup_worker()
        assert self.worker is not None

        log_message = (
            "Worker._process_message: unexpected type: <class 'str'> is not an "
            "instance of "
        )
        with self.assertLogsContains(log_message, level=logging.DEBUG):
            message_processed = await self.worker._process_message(
                msg  # type: ignore[arg-type]
            )

        self.assertFalse(message_processed)

    async def test_process_message_invalid_json(self) -> None:
        """Worker._process_message return False if the JSON is invalid."""
        msg = WSMessage(WSMsgType.TEXT, '{[', None)

        self.setup_worker()
        assert self.worker is not None

        message_processed = await self.worker._process_message(msg)

        self.assertFalse(message_processed)

    def patch_config_handler_fail(
        self, config_handler: ConfigHandler
    ) -> MagicMock:
        """
        Patch ConfigHandler._fail and returns mock.

        side_effect is aligned with the behaviour of _fail.
        """
        patched = mock.patch.object(config_handler, '_fail', autospec=True)
        fail_mocked = patched.start()
        fail_mocked.side_effect = SystemExit(3)
        self.addCleanup(patched.stop)

        return fail_mocked

    async def test_invalid_configuration_missing_debusine_url(self) -> None:
        """Worker() raises an exception: api_url is not in [General]."""
        config_temp_directory = self.create_temp_config_directory(
            {'General': {}}
        )
        config = ConfigHandler(directories=[config_temp_directory])

        log_message = (
            f'Missing required key "api-url" in General '
            f'section in the configuration file '
            f'({config.active_configuration_file})'
        )

        fail_mocked = self.patch_config_handler_fail(config)

        with self.assertRaisesSystemExit(3):
            self.setup_worker(config)

        fail_mocked.assert_called_with(log_message)

    async def test_invalid_configuration_missing_general_section(self) -> None:
        """Worker() raises an exception: [General] section is not available."""
        config_temp_directory = self.create_temp_config_directory({})
        config = ConfigHandler(directories=[config_temp_directory])

        log_message = (
            f'Missing required section "General" in the configuration file '
            f'({config.active_configuration_file})'
        )

        fail_mocked = self.patch_config_handler_fail(config)

        with self.assertRaisesSystemExit(3):
            self.setup_worker(config)

        fail_mocked.assert_called_with(log_message)

    async def test_invalid_configuration_api_url_not_https(self) -> None:
        """A worker configured to require an HTTPS api-url fails with HTTP."""
        config_temp_directory = self.create_temp_config_directory(
            {"General": {"api-url": "http://example.org/"}}
        )
        config = ConfigHandler(
            directories=[config_temp_directory], require_https=True
        )

        log_message = (
            f'api-url in {config.active_configuration_file} does not use '
            f'HTTPS: http://example.org/'
        )

        fail_mocked = self.patch_config_handler_fail(config)

        with self.assertRaisesSystemExit(3):
            self.setup_worker(config)

        fail_mocked.assert_called_with(log_message)

    async def test_valid_configuration_api_url_https(self) -> None:
        """A worker configured to require an HTTPS api-url accepts HTTPS."""
        config_temp_directory = self.create_temp_config_directory(
            {"General": {"api-url": "https://example.org/"}}
        )
        config = ConfigHandler(
            directories=[config_temp_directory], require_https=True
        )

        self.setup_worker(config)

    def assert_functools_partial_equal(
        self, partial1: functools.partial[Any], partial2: functools.partial[Any]
    ) -> None:
        """Assert two functools.partials are semantically equal."""
        # functools.partial does not implement __eq__
        self.assertEqual(partial1.func, partial2.func)
        self.assertEqual(partial1.args, partial2.args)
        self.assertEqual(partial1.keywords, partial2.keywords)

    async def test_main_calls_connect(self) -> None:
        """Ensure worker.main() calls worker.connect()."""
        self.setup_worker()
        assert self.worker is not None
        mock_connect = self.patch_worker("connect")

        patcher = mock.patch.object(
            self.worker._main_event_loop, "add_signal_handler", autospec=True
        )
        mocked_add_signal_handler = patcher.start()
        self.addCleanup(patcher.stop)

        await self.worker.main()

        sigint_call = mocked_add_signal_handler.mock_calls[0][1]
        self.assertEqual(sigint_call[0], signal.SIGINT)
        self.assert_functools_partial_equal(
            sigint_call[1],
            functools.partial(
                self.worker._create_task_signal_int_term_handler, signal.SIGINT
            ),
        )

        sigterm_call = mocked_add_signal_handler.mock_calls[1][1]
        self.assertEqual(sigterm_call[0], signal.SIGTERM)
        self.assert_functools_partial_equal(
            sigterm_call[1],
            functools.partial(
                self.worker._create_task_signal_int_term_handler, signal.SIGTERM
            ),
        )

        self.assertEqual(len(mocked_add_signal_handler.mock_calls), 2)

        mock_connect.assert_awaited()

    async def test_create_task_signal_int_term_handler(self) -> None:
        """
        Test Task returned from Worker._create_task_signal_int_term_handler.

        Assert that the task logs "Terminated with signal ...".
        Other tests on the behaviour of Worker._signal_int_term_handler
        is implemented in the method test_signal_int_term_handler.

        Testing that Worker._create_task_signal_int_term_handler returns
        a Task that will execute Worker._signal_int_term_handler might be
        possible but only if accessing Task._coro private method.
        """
        self.setup_worker()
        assert self.worker is not None

        test_signals = [signal.SIGINT, signal.SIGTERM]

        for test_signal in test_signals:
            with self.subTest(test_signal=test_signal):
                sig_handler = self.worker._create_task_signal_int_term_handler(
                    test_signal
                )

                self.assertIsInstance(sig_handler, asyncio.Task)

                with self.assertLogsContains(
                    f"Terminated with signal {test_signal.name}"
                ):
                    await sig_handler

    def assert_logging_setup(
        self,
        argv_setup: dict[str, Any],
        config_setup: dict[str, Any],
        expected: dict[str, Any],
    ) -> None:
        """
        Assert logging setup for argv_setup and config_setup.

        :param argv_setup: parameters that a user might have passed using
          the CLI
        :param config_setup: parameters that might be set in the config.ini
        :param expected: given the argv_setup and config_setup: what is the
          expected configuration
        """
        del self.config['General']['log-file']
        if config_setup['log-file'] is not None:
            self.config['General']['log-file'] = config_setup['log-file']
        if config_setup['log-level'] is not None:
            self.config['General']['log-level'] = logging.getLevelName(
                config_setup['log-level']
            )

        # Normalizes log_level: always using the str representation and not
        # the int
        if argv_setup['log_level'] is not None:
            argv_setup['log_level'] = logging.getLevelName(
                argv_setup['log_level']
            )
        expected['level'] = logging.getLevelName(expected['level'])

        # Patches basicConfig
        patcher = mock.patch(
            'debusine.worker._worker.logging.basicConfig', autospec=True
        )
        mocked_logging = patcher.start()
        self.addCleanup(patcher.stop)

        self.setup_worker(**argv_setup)

        self.assertTrue(mocked_logging.called)
        called_args = mocked_logging.call_args[1]
        self.assertEqual(called_args['filename'], expected['filename'])
        self.assertEqual(called_args['level'], expected['level'])

    def test_setup_logging_no_logging_configuration(self) -> None:
        """Logging system: no explicit setup: use Worker.DEFAULT_LOG_LEVEL."""
        argv_setup = {'log_file': None, 'log_level': None}
        config_setup = {'log-file': None, 'log-level': None}
        expected = {'filename': None, 'level': Worker.DEFAULT_LOG_LEVEL}
        self.assert_logging_setup(argv_setup, config_setup, expected)

    def test_setup_logging_from_constructor(self) -> None:
        """Logging system: use settings from the Worker constructor."""
        log_file = '/var/log/debusine.log'
        argv_setup = {'log_file': log_file, 'log_level': logging.DEBUG}
        config_setup = {'log-file': None, 'log-level': None}
        expected = {'filename': log_file, 'level': logging.DEBUG}

        self.assert_logging_setup(argv_setup, config_setup, expected)

    def test_setup_logging_from_config(self) -> None:
        """Logging system: use settings from the configuration file."""
        log_file = '/var/log/debusine.log'
        argv_setup = {'log_file': None, 'log_level': None}
        config_setup = {'log-file': log_file, 'log-level': logging.DEBUG}
        expected = {'filename': log_file, 'level': logging.DEBUG}
        self.assert_logging_setup(argv_setup, config_setup, expected)

    def test_setup_logging_worker_constructor_overwrites_config(self) -> None:
        """Logging system: use settings from the Worker constructor."""
        log_file = '/var/log/debusine.log'
        argv_setup = {'log_file': log_file, 'log_level': logging.DEBUG}
        config_setup = {'log-file': 'unused', 'log-level': logging.ERROR}
        expected = {'filename': log_file, 'level': logging.DEBUG}

        self.assert_logging_setup(argv_setup, config_setup, expected)

    def test_setup_logging_cannot_open_log_file(self) -> None:
        """Worker raises SystemExit if the log-file cannot be opened."""
        patched = mock.patch('debusine.worker.Worker._fail', autospec=True)

        fail_mocked = patched.start()
        fail_mocked.side_effect = SystemExit(3)
        self.addCleanup(patched.stop)

        with self.assertRaisesRegex(SystemExit, '^3$'):
            self.setup_worker(log_file='/')

        fail_mocked.assert_called()

    def test_log_forced_exit(self) -> None:
        """log_forced_exit logs the event."""
        self.setup_worker()
        assert self.worker is not None

        with self.assertLogsContains(
            "Terminated with signal SIGINT", level=logging.INFO
        ):
            self.worker.log_forced_exit(signal.SIGINT)

    async def test_asyncio_sleep(self) -> None:
        """Worker._asyncio_sleep calls asyncio.sleep."""
        patched = mock.patch('asyncio.sleep', autospec=True)
        mocked = patched.start()
        self.addCleanup(patched.stop)

        await Worker._asyncio_sleep(1.5)

        mocked.assert_called_with(1.5)

    async def test_send_task_result_do_not_send_aborted_task(self) -> None:
        """Assert that _send_task_result does not send the result (aborted)."""
        self.setup_worker()
        assert self.worker is not None

        work_request_completed_update = (
            self.patch_debusine_work_request_completed_update()
        )

        self.worker._task_name = "Noop"
        self.worker._task_running = Noop({"result": False})
        self.worker._initialize_statistics()
        self.worker._task_running.abort()

        with self.assertLogsContains(
            "Task: Noop has been aborted", level=logging.INFO
        ):
            await self.worker._send_task_result(11, concurrent.futures.Future())

        work_request_completed_update.assert_not_called()

    async def test_signal_int_term_handler(self) -> None:
        """Test signal handler logs "Terminated..." and closes the session."""
        self.setup_worker()
        assert self.worker is not None

        await self.worker.connect()
        assert self.worker._aiohttp_client_session is not None

        self.assertFalse(self.worker._aiohttp_client_session.closed)

        with self.assertLogsContains("Terminated with signal SIGINT"):
            await self.worker._signal_int_term_handler(signal.SIGINT)

        self.assertTrue(self.worker._aiohttp_client_session.closed)

    async def test_signal_int_term_handler_cancels_task(self) -> None:
        """Test signal handler aborts() the task."""
        self.setup_worker()
        assert self.worker is not None
        self.worker._task_running = Noop({"result": False})

        self.assertFalse(self.worker._task_running.aborted)

        with self.assertLogsContains("Terminated with signal SIGTERM"):
            await self.worker._signal_int_term_handler(signal.SIGTERM)

        self.assertTrue(self.worker._task_running.aborted)

    async def test_setup_signal_int_term_handler(self) -> None:
        """Test that SIGINT/SIGTERM setup is done in Worker.main()."""
        self.setup_worker()
        assert self.worker is not None

        self.worker._set_main_event_loop()
        assert self.worker._main_event_loop is not None

        self.assertFalse(
            self.worker._main_event_loop.remove_signal_handler(signal.SIGINT)
        )
        self.assertFalse(
            self.worker._main_event_loop.remove_signal_handler(signal.SIGTERM)
        )

        await self.worker.main()

        self.assertTrue(
            self.worker._main_event_loop.remove_signal_handler(signal.SIGINT)
        )
        self.assertTrue(
            self.worker._main_event_loop.remove_signal_handler(signal.SIGTERM)
        )

    def test_raise_try_again(self) -> None:
        """Assert Worker._raise_try_again() raise tenacity.TryAgain()."""
        self.setup_worker(disable_endless_retry=False)
        assert self.worker is not None

        self.assertRaises(tenacity.TryAgain, self.worker._raise_try_again)

    def test_reset_retry_counter(self) -> None:
        """Assert Worker._reset_retry_counter reset the counter and boolean."""
        self.setup_worker()
        assert self.worker is not None

        retry_state = MagicMock()

        retry_state.attempt_number = 5
        self.worker._reset_retry_attempt_number = True

        self.worker._reset_retry_counter(retry_state)

        self.assertEqual(retry_state.attempt_number, 0)
        self.assertFalse(self.worker._reset_retry_attempt_number)

    async def test_reset_state_cancels_task_statistics_poller(self) -> None:
        """Worker._reset_state makes sure the statistics poller is cancelled."""
        self.setup_worker()
        assert self.worker is not None

        self.worker._task_name = "Noop"
        self.worker._task_running = Noop({"result": False})
        self.worker._initialize_statistics()
        task_statistics_poller = self.worker._task_statistics_poller
        assert task_statistics_poller is not None
        self.assertFalse(task_statistics_poller.cancelling())

        self.worker._reset_state()

        self.assertTrue(task_statistics_poller.cancelling())
        self.assertIsNone(self.worker._task_statistics_poller)
