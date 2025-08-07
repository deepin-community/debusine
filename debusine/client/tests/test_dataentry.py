# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the debusine-client interactive data entry code."""

import re
import textwrap
from typing import Any
from unittest import mock

from debusine.client.dataentry import DataEntry
from debusine.client.tests.utils import TestConsole
from debusine.test import TestCase


class MockDataEntry(DataEntry):
    """Concrete DataEntry implementation tweaked for tests."""

    console: TestConsole

    def __init__(self) -> None:
        """Set up the rich console to output plaing text to self.output."""
        super().__init__(console=TestConsole())

    def menu(self) -> None:
        """Mock menu implementation."""
        self.console.print("__menu__")


class DataEntryTests(TestCase):
    """Tests for :py:class:`DataEntry`."""

    DEFAULT_PROMPT: str = "__prompt__"

    def assertInputLine(
        self,
        de: MockDataEntry,
        input_lines: list[str],
        output_lines: list[str],
        result: str | None,
        prompt: str = DEFAULT_PROMPT,
        **kwargs: Any,
    ) -> None:
        """Run an input_line session on the DataEntry object."""
        de.console.reset_output()
        with mock.patch(
            "debusine.client.dataentry.input", side_effect=iter(input_lines)
        ):
            res = de.input_line(prompt, **kwargs)
        self.assertEqual(res, result)
        self.assertEqual(
            de.console.output.getvalue().splitlines(), output_lines
        )

    def test_input_line_plain(self) -> None:
        """Test plain input_line."""
        de = MockDataEntry()
        with mock.patch(
            "debusine.client.dataentry.readline.set_startup_hook"
        ) as readline_startup_hook:
            self.assertInputLine(de, ["test"], [self.DEFAULT_PROMPT], "test")
        self.assertEqual(len(readline_startup_hook.call_args_list), 1)

    def test_input_line_empty(self) -> None:
        """Test input_line with an empty value."""
        de = MockDataEntry()
        self.assertInputLine(de, [""], [self.DEFAULT_PROMPT], None)

    def test_input_line_required(self) -> None:
        """Test input_line requiring a value."""
        de = MockDataEntry()
        self.assertInputLine(
            de,
            ["", "test"],
            [
                self.DEFAULT_PROMPT,
                "Please enter a value: this field is needed",
                self.DEFAULT_PROMPT,
            ],
            "test",
            required=True,
        )

    def test_input_line_validator(self) -> None:
        """Test input_line with validation."""
        de = MockDataEntry()
        self.assertInputLine(
            de,
            ["1234", "test"],
            [
                self.DEFAULT_PROMPT,
                "'1234' should match ^[a-z]+$",
                self.DEFAULT_PROMPT,
            ],
            "test",
            validator=re.compile(r"^[a-z]+$"),
        )

    def test_input_line_initial(self) -> None:
        """Test input_line initial value."""
        de = MockDataEntry()
        with mock.patch(
            "debusine.client.dataentry.readline.set_startup_hook"
        ) as readline_startup_hook:
            self.assertInputLine(
                de, ["test"], [self.DEFAULT_PROMPT], "test", initial="foo"
            )

        self.assertEqual(len(readline_startup_hook.call_args_list), 2)

    def test_help(self) -> None:
        """Test help generation."""
        de = MockDataEntry()
        self.assertFalse(de.onecmd("help"))
        self.assertEqual(
            de.console.output.getvalue(),
            textwrap.dedent(
                """
            Command summary (using the first letter is enough):

             * eof: Stop processing.
             * help: Command line help.
             * quit: Quit editor.

            You can use 'help' command for detailed help
               """
            ),
        )

    def test_help_missing(self) -> None:
        """Test help for a missing command."""
        de = MockDataEntry()
        self.assertFalse(de.onecmd("help missing"))
        self.assertEqual(
            de.console.output.getvalue(),
            "\nNo help found for 'missing'\n",
        )

    def test_help_command(self) -> None:
        """Test help for an existing command."""
        de = MockDataEntry()
        self.assertFalse(de.onecmd("help quit"))
        self.assertEqual(
            de.console.output.getvalue(),
            "\nQuit editor.\n\n",
        )

    def test_quit(self) -> None:
        """Quit signals to end the Cmd loop."""
        de = MockDataEntry()
        self.assertTrue(de.onecmd("quit"))
        self.assertEqual(de.console.output.getvalue(), "")

    def test_EOF(self) -> None:
        """EOF signals to end the Cmd loop."""
        de = MockDataEntry()
        with mock.patch.object(de, "do_quit", return_value=True) as do_quit:
            self.assertTrue(de.do_eof(""))
        do_quit.assert_called()
        self.assertEqual(de.console.output.getvalue(), "\n")

    def test_default(self) -> None:
        """Test handling unrecognized commands."""
        de = MockDataEntry()
        self.assertFalse(de.onecmd("mischief"))
        self.assertEqual(
            de.console.output.getvalue(), "command not recognized: 'mischief'\n"
        )

    def test_auto_menu(self) -> None:
        """Test printing menu at the end of each command."""
        de = MockDataEntry()
        self.assertFalse(de.postcmd(False, ""))
        self.assertEqual(de.console.output.getvalue(), "__menu__\n")
        de.console.reset_output()
        self.assertTrue(de.postcmd(True, ""))
        self.assertEqual(de.console.output.getvalue(), "")

    def test_cmdloop_menu(self) -> None:
        """Test printing menu at the beginning of cmdloop."""
        de = MockDataEntry()
        with (
            mock.patch.object(de, "menu") as menu,
            mock.patch("debusine.client.dataentry.cmd.Cmd.cmdloop") as cmdloop,
        ):
            de.cmdloop()

        menu.assert_called()
        cmdloop.assert_called()

    def test_abbreviations(self) -> None:
        """Test abbreviating commands."""
        de = MockDataEntry()
        for entered, expected in (
            ("", ""),
            ("h", "help"),
            ("he", "help"),
            ("hel", "help"),  # codespell:ignore hel
            ("help", "help"),
            ("h foo", "help foo"),
            ("q", "quit"),
            ("q foo", "quit foo"),
            ("x", "x"),
            ("x foo", "x foo"),
        ):
            with self.subTest(entered=entered):
                self.assertEqual(de.precmd(entered), expected)
