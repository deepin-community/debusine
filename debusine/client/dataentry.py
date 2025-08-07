# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-client interactive data entry."""

import abc
import cmd
import inspect
import re
import readline
from typing import Any, Literal, overload

import rich.markup
from rich.console import Console


class DataEntry(cmd.Cmd, abc.ABC):
    """Base class for interactive data entry loops."""

    def __init__(self, console: Console | None = None) -> None:
        """Set up a Rich console for output."""
        super().__init__()
        self.console = console or Console()

    @overload
    def input_line(
        self,
        prompt: str,
        *,
        initial: str | None = None,
        required: Literal[True],
        validator: re.Pattern[str] | None = None,
    ) -> str: ...

    @overload
    def input_line(
        self,
        prompt: str,
        *,
        initial: str | None = None,
        required: Literal[False] = False,
        validator: re.Pattern[str] | None = None,
    ) -> str: ...

    def input_line(
        self,
        prompt: str,
        *,
        initial: str | None = None,
        required: bool = False,
        validator: re.Pattern[str] | None = None,
    ) -> str | None:
        """
        Input a line of text.

        :param prompt: prompt to display
        :param initial: initial value that the user can correct
        :param required: set to True to insist that a value is provided
        """
        if initial is not None:
            readline.set_startup_hook(
                lambda: readline.insert_text(initial)  # pragma: no cover
            )

        try:
            while True:
                # Rich plays badly with readline, see:
                # https://github.com/Textualize/rich/issues/2293
                self.console.print(prompt)
                result = input("> ")
                result = result.strip()
                if not result:
                    if required:
                        self.console.print(
                            "Please enter a value: this field is needed"
                        )
                    else:
                        return None
                elif validator and not validator.match(result):
                    self.console.print(
                        rich.markup.escape(repr(result)),
                        "should match"
                        f" [bold]{rich.markup.escape(validator.pattern)}[/]",
                    )
                else:
                    return result
        finally:
            readline.set_startup_hook()

    def do_help(self, arg: str) -> None:
        """
        Command line help.

        You can use `help command` for details about the command
        """
        arg = arg.strip().lower()
        self.console.print()
        if not arg:
            self.console.print(
                "Command summary (using the first letter is enough):"
            )
            self.console.print()

            for name, value in inspect.getmembers(self):
                if not name.startswith("do_"):
                    continue
                summary = (inspect.getdoc(value) or "").splitlines()[0].strip()
                self.console.print(f" * {name[3:]}: {summary}")

            self.console.print()
            self.console.print(
                r"You can use '[bold]h[/]elp' command for detailed help"
            )
        else:
            func = getattr(self, f"do_{arg}", None)
            if func is None:
                self.console.print(f"No help found for {arg!r}", markup=False)
            else:
                self.console.print(inspect.getdoc(func))
                self.console.print()

    def do_quit(self, arg: str) -> bool:  # noqa: U100
        """Quit editor."""
        return True

    def do_eof(self, arg: str) -> bool:
        """Stop processing."""
        self.console.print()
        return self.do_quit(arg)

    @abc.abstractmethod
    def menu(self) -> None:
        """Print a menu for the user."""

    # Cmd.default is really expected to return a bool|None, and it looks like
    # badly typed (see how Cmd.onecmd uses it)
    def default(self, line: str) -> bool:  # type: ignore[override]
        """Print error to console."""
        self.console.print(
            f"[red]command not recognized: {rich.markup.escape(repr(line))}[/]",
            highlight=False,
        )
        return False

    def precmd(self, line: str) -> str:
        """Allow abbreviated commands."""
        parts = line.split(None, 1)
        if parts:
            for name, value in inspect.getmembers(self):
                if name.startswith(f"do_{parts[0].lower()}"):
                    parts[0] = name[3:]
        return " ".join(parts)

    def postcmd(self, stop: bool, line: str) -> bool:
        """Show the menu before a prompt."""
        res = super().postcmd(stop, line)
        if not res:
            self.menu()
        return res

    def cmdloop(self, intro: Any | None = None) -> None:  # noqa: U100
        """Show menu when entering the loop."""
        self.menu()
        super().cmdloop()
