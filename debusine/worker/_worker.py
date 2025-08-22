#!/usr/bin/env python3

# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Worker client: connects to debusine server.

Overview
--------
* Registration (needed only once per worker): If the worker doesn't have a
  token: it will generate it and register with the server
  (HTTP POST to ``/api/1.0/worker/register``)
* The client will use this token to connect to the server (WebSocket to
  ``/api/ws/1.0/worker/connect``)

Flow
----

  #. The worker is executed and chooses ``~/.config/debusine/worker``
     (if it exists) or ``/etc/debusine/worker``. It reads the file
     ``config.ini`` from the directory and if it already exists the file
     ``token``.
  #. If there isn't a token the worker generates one (using
     :py:func:`secrets.token_hex`) and registers it to the Debusine server
     via HTTP POST to `/api/1.0/worker/register` sending the generated token
     and the worker's FQDN. The token is saved to the ``token`` file in the
     chosen config directory.
  #. The server will create a new Token and Worker in the DB via the models.
     They wouldn't be used until manual validation.
  #. The client can then connect using WebSockets to
     ``/api/ws/1.0/worker/connect`` and wait for commands to execute.

Objects documentation
---------------------

"""
import asyncio
import functools
import logging
import secrets
import signal
import socket
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Literal, NoReturn, assert_never

import aiohttp
from aiohttp.web_exceptions import HTTPCreated, HTTPOk

try:
    from pydantic.v1 import ValidationError
except ImportError:
    from pydantic import ValidationError  # type: ignore

import tenacity

from debusine.artifacts.models import RuntimeStatistics, TaskTypes
from debusine.client.debusine import Debusine
from debusine.client.exceptions import TokenDisabledError
from debusine.client.models import WorkRequestResponse
from debusine.tasks import (
    BaseExternalTask,
    BaseTask,
    BaseTaskWithExecutor,
    TaskConfigError,
)
from debusine.tasks.executors import analyze_worker_all_executors
from debusine.tasks.models import OutputData, OutputDataError, WorkerType
from debusine.worker.config import ConfigHandler
from debusine.worker.debusine_async_http_client import DebusineAsyncHttpClient
from debusine.worker.system_information import (
    cpu_time,
    host_architecture,
    system_metadata,
)


class Worker:
    """Worker class: waits for commands from the debusine server."""

    DEFAULT_LOG_LEVEL = logging.INFO

    def __init__(
        self,
        *,
        log_file: str | None = None,
        log_level: str | None = None,
        worker_type: Literal[
            WorkerType.EXTERNAL, WorkerType.SIGNING
        ] = WorkerType.EXTERNAL,
        config: ConfigHandler | None = None,
    ) -> None:
        """
        Initialize Worker.

        :param log_file: log file to where the logs are saved. If None uses
          settings from config.ini or default's Python (stderr).
        :param log_level: minimum level of the logs being saved. If None uses
          settings from config.ini or DEFAULT_LOG_LEVEL.
        :param config: ConfigHandler to use (or creates a default one)
        """
        self._original_log_file = log_file
        self._original_log_level = log_level
        self._worker_type = worker_type

        if config is None:
            if worker_type == WorkerType.SIGNING:
                self._config = ConfigHandler(
                    directories=[
                        str(Path.home() / ".config/debusine/signing"),
                        "/etc/debusine/signing",
                    ],
                    require_https=True,
                )
            else:
                self._config = ConfigHandler()
        else:
            self._config = config

        self._setup_logging()

        self._config.validate_config_or_fail()

        self._aiohttp_client_session: aiohttp.ClientSession | None = None

        self._async_http_client = DebusineAsyncHttpClient(self._config)

        # ThreadPool for BaseTask.execute (e.g. Sbuild.exec).
        # We only allow a single thread.  We only run one task at a time
        # anyway, and signing workers need to run Django code in tasks which is
        # safest if done from only one thread.
        self._task_executor = ThreadPoolExecutor(max_workers=1)

        # Statistics collected from the worker and task executors.
        self._task_statistics: RuntimeStatistics | None = None
        self._task_statistics_poller: asyncio.Task[None] | None = None

        # The monotonic clock time when the task started executing.
        self._task_start_time: float | None = None

        # The total CPU time used so far when the task started executing.
        # (We assume that the worker only runs one task at a time.)
        self._task_start_cpu_time: float | None = None

        # BaseTask.execute (e.g. Sbuild.exec) future is stored here (avoids
        # garbage collection deletion)
        self._task_exec_future: Future[bool] | None = None

        # Used by ThreadPoolExecutor to submit tasks to the main event loop
        self._main_event_loop: asyncio.AbstractEventLoop | None = None

        # Running task
        self._task_running: BaseTask[Any, Any] | None = None

        # Lock to protect from concurrent work request execution
        self._task_lock: Lock = Lock()

        # Set to True if the retry_attempt_number should be reset
        self._reset_retry_attempt_number: bool = False

    @functools.cached_property
    def _debusine(self) -> Debusine:
        return Debusine(
            self._config.api_url, self._config.token, logger=logging.getLogger()
        )

    def _setup_logging(self) -> None:
        """
        Set logging configuration.

        Use the parameters passed to Worker.__init__() and self._config
        configuration.

        If the log file cannot be opened it aborts.
        """
        log_file = self._original_log_file or self._config.log_file
        log_level = (
            self._original_log_level
            or self._config.log_level
            or logging.getLevelName(self.DEFAULT_LOG_LEVEL)
        )

        try:
            logging.basicConfig(
                format='%(asctime)s %(message)s',
                filename=log_file,
                level=log_level,
                force=True,
            )
        except OSError as exc:
            self._fail(f'Cannot open {log_file}: {exc}')

    def _create_task_signal_int_term_handler(
        self, signum: signal.Signals
    ) -> asyncio.Task[None]:
        return asyncio.create_task(self._signal_int_term_handler(signum))

    async def main(self) -> None:
        """Run the worker."""
        self._set_main_event_loop()
        assert self._main_event_loop is not None

        for signum in [signal.SIGINT, signal.SIGTERM]:
            self._main_event_loop.add_signal_handler(
                signum,
                functools.partial(
                    self._create_task_signal_int_term_handler, signum
                ),
            )

        await self.connect()

        logging.info("debusine-worker lost connection with debusine-server")

        await self.close()

    def _set_main_event_loop(self) -> None:
        self._main_event_loop = asyncio.get_running_loop()

    async def _signal_int_term_handler(self, signum: signal.Signals) -> None:
        if self._task_running is not None:
            self._task_running.abort()

        self.log_forced_exit(signum)
        await self.close()

    async def close(self) -> None:
        """Close the AioHTTP session."""
        if self._aiohttp_client_session is not None:
            await self._aiohttp_client_session.close()

        await self._async_http_client.close()

    @staticmethod
    def _log_server_error(status_code: int, body: str) -> None:
        logging.error(
            'Could not register. Server HTTP status code: %s Body: %s\n',
            status_code,
            body,
        )

    async def _register(self) -> bool:
        """
        Create a token, registers it to debusine, saves it locally.

        The worker will not receive any tasks until the debusine admin
        has approved the token.
        """
        token = secrets.token_hex(32)

        register_path = '/1.0/worker/register/'

        data = {
            "token": token,
            "fqdn": socket.getfqdn(),
            "worker_type": self._worker_type,
        }

        try:
            response = await self._async_http_client.post(
                register_path, json=data, token=self._config.activation_token
            )
        except aiohttp.client_exceptions.ClientResponseError as err:
            self._log_server_error(err.status, 'Not available')
            return False
        except aiohttp.client_exceptions.ClientConnectorError as err:
            logging.error(  # noqa: G200
                'Could not register. Server unreachable: %s', err
            )
            return False

        status, body = (
            response.status,
            (await response.content.read()).decode('utf-8'),
        )

        if status == HTTPCreated.status_code:
            self._config.write_token(token)

            # If we already had a cached client, invalidate it so that we
            # start using the new token.
            try:
                del self._debusine
            except AttributeError:
                pass

            return True
        else:
            self._log_server_error(status, body)
            return False

    @staticmethod
    async def _asyncio_sleep(delay: float) -> None:
        """Sleep asynchronously (mocked in tests)."""
        await asyncio.sleep(delay)

    def _raise_try_again(self) -> NoReturn:
        raise tenacity.TryAgain()

    async def connect(self) -> None:
        """
        Connect (registering if needed) to the debusine server.

        Uses the URL for debusine server from the configuration file.
        """
        self._aiohttp_client_session = aiohttp.ClientSession()

        if not self._config.token:
            result = await self._register()
            if result is False:
                self._fail('Exiting...')

        debusine_ws_url = self._config.api_url.replace('http', 'ws', 1)
        worker_connect_ws_url = f'{debusine_ws_url}/ws/1.0/worker/connect/'

        def tenacity_before_hook(retry_state: tenacity.RetryCallState) -> None:
            self._reset_retry_counter(retry_state)
            self._log_trying_to_reconnect(retry_state)

        @tenacity.retry(
            sleep=self._asyncio_sleep,
            wait=tenacity.wait_random(min=1, max=6),
            before=tenacity_before_hook,
            retry=tenacity.retry_if_exception_type(
                aiohttp.client_exceptions.ClientError
            )
            | tenacity.retry_if_exception_type(TokenDisabledError)
            | tenacity.retry_if_exception_type(ConnectionError),
        )
        async def _do_wait_for_messages() -> None:
            """Connect to the server and waits for commands."""
            assert self._config.token
            headers = {'token': self._config.token}

            try:
                # Set at the top of Worker.connect.
                assert self._aiohttp_client_session is not None
                async with self._aiohttp_client_session.ws_connect(
                    worker_connect_ws_url, headers=headers, heartbeat=60
                ) as ws:
                    self._reset_retry_attempt_number = True
                    msg: aiohttp.WSMessage
                    async for msg in ws:  # pragma: no branch
                        await self._process_message(msg)

                    # The server disconnected the worker
                    self._raise_try_again()

            except TokenDisabledError as exc:
                # The server disconnected the worker because the token is
                # not associated with a worker or is disabled. The server
                # admin should enable the token.
                logging.info(  # noqa: G200
                    'The token (%s) is disabled. '
                    'Debusine admin (%s) should enable it. '
                    'Reason: %s. '
                    'Will try again',
                    self._config.token,
                    self._config.api_url,
                    exc,
                )
                raise
            except (
                aiohttp.client_exceptions.ClientConnectorError,
                aiohttp.client_exceptions.WSServerHandshakeError,
            ) as exc:
                logging.info(  # noqa: G200
                    'Error connecting to %s (%s)',
                    self._config.api_url,
                    exc,
                )
                raise
            except ConnectionResetError:
                logging.debug('ConnectionResetError, will reconnect')
                raise

        await _do_wait_for_messages()

    def _reset_retry_counter(
        self, retry_state: tenacity.RetryCallState
    ) -> None:
        if self._reset_retry_attempt_number:
            retry_state.attempt_number = 0
            self._reset_retry_attempt_number = False

    @staticmethod
    def _log_trying_to_reconnect(retry_state: tenacity.RetryCallState) -> None:
        if retry_state.attempt_number > 1:
            logging.debug(
                'Trying to reconnect, attempt %d', retry_state.attempt_number
            )

    @staticmethod
    def _fail(message: str) -> NoReturn:
        logging.fatal(message)
        raise SystemExit(3)

    async def _request_and_execute_work_request(self) -> None:
        # Grab the task lock to make sure that the previous work request
        # completed fully. We can be notified before the end because the
        # server notifies via websocket while we are still wrapping up
        # the task execution.
        retries = 0
        while not self._task_lock.acquire(blocking=False):
            await self._asyncio_sleep(1)
            retries += 1
            if retries >= 60:
                logging.error(
                    "Worker is busy and can't execute a new work request"
                )
                return

        work_request = await self._request_work_request()
        if work_request:
            await self._execute_work_request_and_submit_result(work_request)
        else:
            logging.debug('No work request available')
            self._task_lock.release()

    async def _process_message(self, msg: aiohttp.WSMessage) -> bool:
        """
        Process messages pushed by the debusine server to the client.

        Return True for processed messages; False for non-processed messages
         and it logs the reason.
        """

        def connected(msg_content: dict[Any, Any]) -> None:  # noqa: U100
            logging.info("Connected to %s", self._config.api_url)

        async def work_request_available(
            msg_content: dict[Any, Any],  # noqa: U100
        ) -> None:
            await self._request_and_execute_work_request()

        return await self._debusine.process_async_message(
            msg,
            {
                "connected": connected,
                "request_dynamic_metadata": self._send_dynamic_metadata,
                "work_request_available": work_request_available,
            },
        )

    async def _request_work_request(self) -> WorkRequestResponse | None:
        """Request a work request and returns it."""
        work_request = await self._async_http_client.get(
            '/1.0/work-request/get-next-for-worker/'
        )

        if work_request.status == HTTPOk.status_code:
            try:
                work_request_obj = WorkRequestResponse.parse_raw(
                    await work_request.text()
                )
            except ValidationError as exc:
                logging.warning(  # noqa: G200
                    'Invalid WorkRequest received from'
                    ' /get-next-for-worker/: %s',
                    exc,
                )
                return None

            return work_request_obj
        else:
            return None

    async def _send_dynamic_metadata(
        self, msg_content: dict[Any, Any]  # noqa: U100
    ) -> None:
        dynamic_metadata_path = '/1.0/worker/dynamic-metadata/'

        metadata = {
            **system_metadata(self._worker_type),
            **analyze_worker_all_executors(),
            **BaseTask.analyze_worker_all_tasks(),
        }

        response = await self._async_http_client.put(
            dynamic_metadata_path, json=metadata
        )

        logging.debug(
            'Sent dynamic_metadata (response: %d) Dynamic metadata: %s',
            response.status,
            metadata,
        )

    def _gather_statistics(self) -> None:
        if self._task_statistics is None:
            self._task_statistics = RuntimeStatistics()

        # We can only start collecting statistics once an executor is
        # instantiated.
        if (
            not isinstance(self._task_running, BaseTaskWithExecutor)
            or self._task_running.executor is None
        ):
            return

        statistics = self._task_running.executor.get_statistics(
            self._task_running.executor_instance
        )

        # We're interested in the high-water mark of these statistics.
        if (
            self._task_statistics.disk_space is not None
            or statistics.disk_space is not None
        ):
            self._task_statistics.disk_space = max(
                self._task_statistics.disk_space or 0,
                statistics.disk_space or 0,
            )
        if (
            self._task_statistics.memory is not None
            or statistics.memory is not None
        ):
            self._task_statistics.memory = max(
                self._task_statistics.memory or 0, statistics.memory or 0
            )

        # We record the initial values of these statistics, but do not
        # expect them to change afterwards.
        if self._task_statistics.available_disk_space is None:
            self._task_statistics.available_disk_space = (
                statistics.available_disk_space
            )
        if self._task_statistics.available_memory is None:
            self._task_statistics.available_memory = statistics.available_memory
        if self._task_statistics.cpu_count is None:
            self._task_statistics.cpu_count = statistics.cpu_count

    async def _poll_statistics(self) -> None:
        assert self._task_running is not None
        assert self._task_statistics is not None

        if not isinstance(self._task_running, BaseTaskWithExecutor):
            return

        while True:
            self._gather_statistics()

            # Arbitrary polling interval.  We want to poll often enough to
            # get useful information about relatively brief spikes in disk
            # or RAM use, but not so often as to spend excessive amounts of
            # time gathering statistics.
            await asyncio.sleep(10)

    def _initialize_statistics(self) -> None:
        self._gather_statistics()
        self._task_statistics_poller = asyncio.create_task(
            self._poll_statistics()
        )
        self._task_start_time = time.monotonic()
        self._task_start_cpu_time = cpu_time()

    async def _execute_work_request_and_submit_result(
        self, work_request: WorkRequestResponse
    ) -> None:
        task_type = TaskTypes(work_request.task_type)
        match self._worker_type:
            case WorkerType.EXTERNAL:
                expected_task_type = TaskTypes.WORKER
            case WorkerType.SIGNING:
                expected_task_type = TaskTypes.SIGNING
            case _ as unreachable:
                assert_never(unreachable)
        error_args: tuple[Any, ...]
        if task_type != expected_task_type:
            error_message = "Task: %s is of type %s, not %s"
            error_args = (work_request.task_name, task_type, expected_task_type)
            logging.error(error_message, *error_args)
            await asyncio.to_thread(
                self._debusine.work_request_completed_update,
                work_request.id,
                "error",
                OutputData(
                    errors=[
                        OutputDataError(
                            message=error_message % error_args,
                            code="wrong-task-type",
                        )
                    ]
                ),
            )
            self._reset_state()
            return

        try:
            task_class = BaseTask.class_from_name(
                TaskTypes(work_request.task_type), work_request.task_name
            )
        except ValueError as exc:
            error_message = "Task: %s Error setup: %s"
            error_args = (work_request.task_name, exc)
            logging.error(error_message, *error_args)
            await asyncio.to_thread(
                self._debusine.work_request_completed_update,
                work_request.id,
                "error",
                OutputData(
                    errors=[
                        OutputDataError(
                            message=error_message % error_args,
                            code="setup-failed",
                        )
                    ]
                ),
            )
            self._reset_state()
            return

        try:
            self._task_running = task_class(
                work_request.task_data, work_request.dynamic_task_data
            )
        except TaskConfigError as exc:
            error_message = "Task: %s Error configure: %s"
            error_args = (work_request.task_name, exc)
            logging.error(error_message, *error_args)
            await asyncio.to_thread(
                self._debusine.work_request_completed_update,
                work_request.id,
                "error",
                OutputData(
                    errors=[
                        OutputDataError(
                            message=error_message % error_args,
                            code="configure-failed",
                        )
                    ]
                ),
            )
            self._reset_state()
            return

        assert self._task_running.TASK_TYPE == work_request.task_type
        assert isinstance(self._task_running, BaseExternalTask)

        logger = logging.getLogger()

        self._task_running.configure_server_access(
            Debusine(self._config.api_url, self._config.token, logger=logger)
        )
        self._task_running.work_request_id = work_request.id
        self._task_running.workspace_name = work_request.workspace
        self._task_running.worker_host_architecture = host_architecture()
        self._initialize_statistics()

        self._task_name = self._task_running.name

        self._task_exec_future = self._task_executor.submit(
            self._task_running.execute_logging_exceptions
        )

        def send_task_result(task_exec_future: Future[bool]) -> None:
            assert self._main_event_loop is not None
            asyncio.run_coroutine_threadsafe(
                self._send_task_result(work_request.id, task_exec_future),
                self._main_event_loop,
            )

        self._task_exec_future.add_done_callback(send_task_result)

    def _reset_state(self) -> None:
        """Reset worker to a clean state, ready for the next work request."""
        self._task_statistics = None
        # This should already have been done by _send_task_result, but
        # there's no harm in making sure.
        if self._task_statistics_poller is not None:
            self._task_statistics_poller.cancel()
        self._task_statistics_poller = None
        self._task_start_time = None
        self._task_start_cpu_time = None
        self._task_exec_future = None
        self._task_running = None
        if self._task_lock.locked():
            # Release the task lock to allow the worker to process the next
            # work request
            self._task_lock.release()

    @staticmethod
    def _exit_worker() -> NoReturn:  # pragma: no cover
        """Exit the worker, mocked in tests."""
        raise SystemExit(1)

    async def _send_task_result(
        self, work_request_id: int, task_exec_future: Future[bool]
    ) -> None:
        """
        Send the result of the task to the debusine server.

        :param work_request_id: WorkRequest.id
        :param task_exec_future: task_exec that executed the BaseTask. This
          method checks the result via .result() or .exception() of this
          object
        """
        # This is called unconditionally after Task completion /
        # failure, in the main thread with the asyncio loop.
        try:
            assert self._task_running is not None
            assert self._task_statistics is not None
            assert self._task_statistics_poller is not None
            assert self._task_start_time is not None
            assert self._task_start_cpu_time is not None

            self._task_statistics_poller.cancel()

            self._task_statistics.duration = int(
                time.monotonic() - self._task_start_time
            )
            self._task_statistics.cpu_time = int(
                cpu_time() - self._task_start_cpu_time
            )
            output_data = OutputData(runtime_statistics=self._task_statistics)

            if self._task_running.aborted:
                logging.info("Task: %s has been aborted", self._task_name)
                # No need to notify debusine-server
                return
            elif task_exec_future.exception():
                error_message = "Task: %s Error execute: %s"
                error_args = (self._task_name, task_exec_future.exception())
                logging.error(error_message, *error_args)
                result = 'error'
                output_data.errors = [
                    OutputDataError(
                        message=error_message % error_args,
                        code="execute-failed",
                    )
                ]
            else:
                result = 'success' if task_exec_future.result() else 'failure'

            try:
                await asyncio.to_thread(
                    self._debusine.work_request_completed_update,
                    work_request_id,
                    result,
                    output_data,
                )
            except Exception:
                # Log this, but leave the work request running.  The server
                # will retry it when this worker next manages to connect and
                # request a new work request to run.
                logging.exception(
                    "Cannot reach server to report work request completed. "
                    "Exiting."
                )
                # Exit so that the worker will try to reconnect.
                self._exit_worker()
        finally:
            self._reset_state()

    @staticmethod
    def log_forced_exit(signum: signal.Signals) -> None:
        """Log a forced exit."""
        logging.info('Terminated with signal %s', signum.name)
