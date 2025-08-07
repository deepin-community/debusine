# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for apps."""
import importlib
from unittest.mock import patch

from debusine.db.models import Worker
from debusine.server.apps import ServerConfig
from debusine.test.django import TestCase, TransactionTestCase


class ServerConfigTestsBase(TransactionTestCase):
    """Common behaviour for ServerConfig test suites."""

    def setUp(self) -> None:
        """Set up test objects."""
        super().setUp()

        self.worker = self.playground.create_worker()
        self.worker.mark_connected()

        app_name = "debusine.server"
        module = importlib.import_module(app_name)
        self.server_config = ServerConfig(app_name, module)


class ServerConfigTests(ServerConfigTestsBase, TestCase):
    """Tests for ServerConfig class."""

    def test_ready_mark_workers_as_disconnected(self) -> None:
        """
        Test ready() method mark workers as disconnected.

        The environment variable DEBUSINE_WORKER_MANAGER is 1.
        """
        with patch.dict("os.environ", {"DEBUSINE_WORKER_MANAGER": "1"}):
            self.server_config.ready()

        self.worker.refresh_from_db()
        self.assertFalse(self.worker.connected())

    def test_ready_celery_workers_not_marked_as_disconnected(self) -> None:
        """
        Test ready() method does not mark Celery workers as disconnected.

        These are instead marked as disconnected when the Celery worker
        shuts down.
        """
        worker = Worker.objects.get_or_create_celery()
        worker.mark_connected()

        with patch.dict("os.environ", {"DEBUSINE_WORKER_MANAGER": "1"}):
            self.server_config.ready()

        worker.refresh_from_db()
        self.assertTrue(worker.connected())

    def test_ready_workers_not_marked_as_disconnected(self) -> None:
        """
        Test ready() method does not mark workers as disconnected.

        The environment variable DEBUSINE_WORKER_MANAGER is not set.
        """
        self.server_config.ready()
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.connected())


class ServerConfigTransactionTests(ServerConfigTestsBase):
    """Tests for asynchronous behaviour of ServerConfig class."""

    async def test_async_ready_mark_workers_as_disconnected(self) -> None:
        """
        The ready() method marks workers as disconnected when async.

        The environment variable DEBUSINE_WORKER_MANAGER is 1.
        """
        with patch.dict("os.environ", {"DEBUSINE_WORKER_MANAGER": "1"}):
            self.server_config.ready()

        await self.worker.arefresh_from_db()
        self.assertFalse(self.worker.connected())
