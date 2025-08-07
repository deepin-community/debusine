# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for DebusineBaseCommand class."""

import signal
from io import StringIO
from typing import Any
from unittest import mock

from django.core.management import CommandError
from django.db import connections
from django.db.utils import DatabaseError

from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.test.django import TestCase, TransactionTestCase


class TestSignalHandlersMixin:
    """Class to set and restore SIGINT and SIGTERM signal handlers."""

    def setUp(self) -> None:
        """Initialize object."""
        self.default_sigint_handler = signal.getsignal(signal.SIGINT)
        self.default_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def tearDown(self) -> None:
        """Restore signals."""
        signal.signal(signal.SIGINT, self.default_sigint_handler)
        signal.signal(signal.SIGTERM, self.default_sigterm_handler)


class DebusineBaseCommandRunFromArgv(
    TestSignalHandlersMixin, TransactionTestCase
):
    """
    Tests for the DebusineBaseCommand that use run_from_argv method.

    Django's BaseCommand.run_from_argv calls connections.close_all() . After
    each test it's calling 'connections["default"].connect()' so the
    next test finds the DB connected as expected.
    """

    def setUp(self) -> None:
        """Connect to the DB (tests in this class might disconnect from it)."""
        connections['default'].connect()
        super().setUp()

    def test_signal_handlers_sigint_sigterm_setup(self) -> None:
        """Test DebusineBaseCommand set SIGINT/SIGTERM handlers."""

        class DebusineSignalSpy(DebusineBaseCommand):
            def handle(self, *args: Any, **kwargs: Any) -> None:
                self.sigint_handler = signal.getsignal(signal.SIGINT)
                self.sigterm_handler = signal.getsignal(signal.SIGTERM)

        debusine_command = DebusineSignalSpy()

        # DebusineBaseCommand.__init__ does not change the signal handlers
        self.assertEqual(
            signal.getsignal(signal.SIGINT), self.default_sigint_handler
        )
        self.assertEqual(
            signal.getsignal(signal.SIGTERM), self.default_sigterm_handler
        )

        debusine_command.run_from_argv(["debusine-admin", "test-command"])

        # DebusineBaseCommand.execute() set the handlers correctly (at the time
        # that DebusineSignalSpy.handle() was called the handlers were correctly
        # set)
        self.assertEqual(
            debusine_command.sigint_handler, DebusineBaseCommand._exit_handler
        )
        self.assertEqual(
            debusine_command.sigterm_handler, DebusineBaseCommand._exit_handler
        )

        # It is a global change
        self.assertEqual(
            signal.getsignal(signal.SIGINT), DebusineBaseCommand._exit_handler
        )
        self.assertEqual(
            signal.getsignal(signal.SIGTERM), DebusineBaseCommand._exit_handler
        )

    def test_exit_handler(self) -> None:
        """DebusineBaseCommand.exit_handler() raises SystemExit(3)."""
        with self.assertRaisesSystemExit(3):
            DebusineBaseCommand._exit_handler(signal.SIGINT, None)

    @staticmethod
    def run_set_verbosity_value_in_handle(
        verbosity_option: str, verbosity_level: int
    ) -> int:
        """
        Run a command and set self.verbosity_value to self.verbosity.

        DebusineBaseCommand.execute() is expected to set self.verbosity
        before "handle()" is called. handle() set verbosity_value to allow
        asserting that self.verbosity was set correctly.
        """

        class SetVerbosityValueInHandle(DebusineBaseCommand):
            def handle(self, *args: Any, **kwargs: Any) -> None:
                self.verbosity_value = self.verbosity

        command = SetVerbosityValueInHandle()
        command.run_from_argv(
            [
                "debusine-admin",
                "test-command",
                verbosity_option,
                str(verbosity_level),
            ]
        )
        assert isinstance(command.verbosity_value, int)
        return command.verbosity_value

    def test_verbosity_long_is_set(self) -> None:
        """DebusineBaseCommand.execute() set verbosity to --verbosity value."""
        verbosity = 3
        self.assertEqual(
            self.run_set_verbosity_value_in_handle("--verbosity", verbosity),
            verbosity,
        )

    def test_verbosity_short_is_set(self) -> None:
        """DebusineBaseCommand.execute() set verbosity to -v value."""
        verbosity = 2
        self.assertEqual(
            self.run_set_verbosity_value_in_handle("-v", verbosity), verbosity
        )


class DebusineBaseCommandTests(TestSignalHandlersMixin, TestCase):
    """Tests for the DebusineBaseCommand."""

    def test_operational_error_is_caught(self) -> None:
        """Test that OperationalError raised in execute is dealt with."""
        patch = mock.patch(
            "django.core.management.BaseCommand.execute",
            side_effect=DatabaseError,
        )
        patch.start()
        self.addCleanup(patch.stop)

        err = StringIO()
        command = DebusineBaseCommand(stderr=err)

        with self.assertRaisesRegex(CommandError, "^Database error: ") as exc:
            command.execute(verbosity="1")

        self.assertEqual(exc.exception.returncode, 3)

    def test_print_verbose(self) -> None:
        """Test print_verbose() when used with different verbosity levels."""
        stdout = StringIO()
        command = DebusineBaseCommand(stdout=stdout)

        for verbosity, msg, expected in (
            (1, "test", ""),
            (2, "test", "test\n"),
        ):
            with self.subTest(verbosity=verbosity):
                command.verbosity = verbosity
                command.print_verbose(msg)
                self.assertEqual(stdout.getvalue(), expected)
