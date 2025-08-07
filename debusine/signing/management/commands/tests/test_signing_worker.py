# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command signing_worker."""

from unittest import mock
from unittest.mock import AsyncMock

from django.core.management import call_command
from django.test import TestCase

from debusine.tasks.models import WorkerType


class SigningWorkerCommandTests(TestCase):
    """Tests for the signing_worker command."""

    def test_no_options(self) -> None:
        """Default to making a signing worker logging to stderr."""
        with (
            mock.patch(
                "debusine.signing.management.commands.signing_worker.Worker",
                autospec=True,
            ) as mock_worker,
            self.assertRaises(SystemExit),
        ):
            mock_worker().main = AsyncMock()

            call_command("signing_worker")

        mock_worker.assert_called_with(
            log_file=None, log_level=None, worker_type=WorkerType.SIGNING
        )

    def test_log_file(self) -> None:
        """The --log-file option is passed through to the worker."""
        with (
            mock.patch(
                "debusine.signing.management.commands.signing_worker.Worker",
                autospec=True,
            ) as mock_worker,
            self.assertRaises(SystemExit),
        ):
            mock_worker().main = AsyncMock()

            call_command("signing_worker", "--log-file", "/dev/null")

        mock_worker.assert_called_with(
            log_file="/dev/null", log_level=None, worker_type=WorkerType.SIGNING
        )

    def test_log_level(self) -> None:
        """The --log-level option is passed through to the worker."""
        with (
            mock.patch(
                "debusine.signing.management.commands.signing_worker.Worker",
                autospec=True,
            ) as mock_worker,
            self.assertRaises(SystemExit),
        ):
            mock_worker().main = AsyncMock()

            call_command("signing_worker", "--log-level", "DEBUG")

        mock_worker.assert_called_with(
            log_file=None, log_level="DEBUG", worker_type=WorkerType.SIGNING
        )

    def test_run_worker(self) -> None:
        """Run a signing worker."""
        with (
            mock.patch(
                "debusine.worker._worker.ConfigHandler", autospec=True
            ) as mock_config_handler,
            mock.patch(
                "debusine.worker.Worker.main", autospec=True
            ) as mock_main,
            self.assertRaises(SystemExit) as raised,
        ):
            mock_config_handler().log_file = None
            mock_config_handler().log_level = None

            call_command("signing_worker")

        mock_main.assert_awaited_once()
        self.assertEqual(raised.exception.code, 0)
