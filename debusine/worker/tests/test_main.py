# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the __main__ (Worker's CLI launcher)."""

import signal
from unittest import mock
from unittest.mock import AsyncMock

from debusine.test import TestCase
from debusine.worker.__main__ import main


class MainTests(TestCase):
    """Tests for __main__ (Worker's CLI launcher)."""

    DEFAULT_ARGV = ['--log-file', '/dev/null']

    def setUp(self) -> None:
        """Initialize test."""
        self.default_sigint_handler = signal.getsignal(signal.SIGINT)
        self.default_sigterm_handler = signal.getsignal(signal.SIGTERM)

        self.patch_config_handler()

    def tearDown(self) -> None:
        """Restore default signals (__main__.main() changes them)."""
        signal.signal(signal.SIGINT, self.default_sigint_handler)
        signal.signal(signal.SIGTERM, self.default_sigterm_handler)

    def patch_config_handler(self) -> None:
        """Mock ConfigHandler to avoid trying to access config.ini."""
        config_handler_patcher = mock.patch(
            'debusine.worker._worker.ConfigHandler', autospec=True
        )
        self.config_handler_mocked = config_handler_patcher.start()
        self.config_handler_mocked().log_level = None
        self.addCleanup(config_handler_patcher.stop)

    def patch_worker_main(self) -> mock.MagicMock:
        """Patch and return mock for Worker.main()."""
        patch = mock.patch(
            "debusine.worker.__main__.Worker.main", autospec=True
        )
        main_mocked = patch.start()
        self.addCleanup(patch.stop)

        return main_mocked

    def test_main_entry_point(self) -> None:
        """Worker's main entry point calls Worker().main()."""
        mocked_main = self.patch_worker_main()

        main(self.DEFAULT_ARGV)

        mocked_main.assert_awaited_once()
        self.config_handler_mocked.assert_called()

    def patch_worker(self) -> mock.MagicMock:
        """Patch Worker class and returns its mock."""
        patch = mock.patch("debusine.worker.__main__.Worker", autospec=True)
        worker_mocked = patch.start()
        self.addCleanup(patch.stop)

        worker_mocked().main = AsyncMock()

        return worker_mocked

    def test_main_entry_point_log_file(self) -> None:
        """Worker main entry point calls run_worker() with the right params."""
        worker_mocked = self.patch_worker()

        main(['--log-file', '/dev/null'])

        worker_mocked.assert_called_with(log_file='/dev/null', log_level=None)

    def test_main_entry_point_no_log_file(self) -> None:
        """Worker main entry point calls run_worker() with the right params."""
        worker_mocked = self.patch_worker()

        main([])

        worker_mocked.assert_called_with(log_file=None, log_level=None)
