# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the noop task."""

from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from debusine.tasks import Noop, TaskConfigError


class NoopTests(TestCase):
    """Test the Noop class."""

    def test_execute_true(self) -> None:
        """Assert execute() return True (result is True)."""
        noop = Noop({"result": True})
        noop._debug_log_files_directory = TemporaryDirectory(
            prefix="debusine-tests-"
        )
        self.addCleanup(noop._debug_log_files_directory.cleanup)
        with mock.patch.object(
            noop, "_upload_work_request_debug_logs", autospec=True
        ):
            self.assertTrue(noop.execute())

    def test_execute_false(self) -> None:
        """Assert execute() return False (result is False)."""
        noop = Noop({"result": False})
        noop._debug_log_files_directory = TemporaryDirectory(
            prefix="debusine-tests-"
        )
        self.addCleanup(noop._debug_log_files_directory.cleanup)
        with mock.patch.object(
            noop, "_upload_work_request_debug_logs", autospec=True
        ):
            self.assertFalse(noop.execute())

    def test_no_additional_properties(self) -> None:
        """Assert no additional properties."""
        error_msg = "extra fields not permitted"
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            Noop({"result": "True", "input": {}})

    def test_label(self) -> None:
        """Test get_label."""
        noop = Noop({"result": True})
        self.assertEqual(noop.get_label(), "noop")
