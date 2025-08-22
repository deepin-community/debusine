# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the signing no-operation task."""

from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase

from debusine.signing.tasks import SigningNoop
from debusine.tasks import TaskConfigError


class SigningNoopTests(TestCase):
    """Test the SigningNoop task."""

    def test_execute_result_true(self) -> None:
        """If result=True, the task returns True."""
        noop = SigningNoop({"result": True})
        noop._debug_log_files_directory = TemporaryDirectory(
            prefix="debusine-tests-"
        )
        self.addCleanup(noop._debug_log_files_directory.cleanup)
        with mock.patch.object(
            noop, "_upload_work_request_debug_logs", autospec=True
        ):
            self.assertTrue(noop.execute())

    def test_execute_result_false(self) -> None:
        """If result=False, the task returns False."""
        noop = SigningNoop({"result": False})
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
            SigningNoop({"result": "True", "input": {}})

    def test_label(self) -> None:
        """Test get_label."""
        task = SigningNoop({"result": True})
        self.assertEqual(task.get_label(), "noop")
