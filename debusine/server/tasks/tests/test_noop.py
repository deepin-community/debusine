# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the server-side no-operation task."""

from unittest import TestCase

from debusine.server.tasks import ServerNoop
from debusine.tasks import TaskConfigError


class ServerNoopTests(TestCase):
    """Test the ServerNoop task."""

    def test_execute_result_true(self) -> None:
        """If result=True, the task returns True."""
        noop = ServerNoop({"result": True})
        self.assertTrue(noop._execute())

    def test_execute_result_false(self) -> None:
        """If result=False, the task returns False."""
        noop = ServerNoop({"result": False})
        self.assertFalse(noop._execute())

    def test_execute_exception(self) -> None:
        """If exception=True, the task raises an exception."""
        noop = ServerNoop({"exception": True})
        self.assertRaisesRegex(
            RuntimeError, "Client requested an exception", noop._execute
        )

    def test_no_additional_properties(self) -> None:
        """Assert no additional properties."""
        error_msg = "extra fields not permitted"
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            ServerNoop({"result": "True", "input": {}})

    def test_label(self) -> None:
        """Test get_label."""
        task = ServerNoop({"result": True})
        self.assertEqual(task.get_label(), "noop")
