# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common code for debusine-client tests."""

import io
from typing import Any
from unittest import mock

from rich.console import Console


class BufferConsole(Console):
    """Rich console set up to direct simple output to a buffer."""

    def __init__(self) -> None:
        """Initialize to send all output to self.output."""
        self.output = io.StringIO()
        super().__init__(
            force_terminal=False, file=self.output, width=4096, height=4096
        )
        self.tables: list[MockTable] = []

    def reset_output(self) -> None:
        """Empty self.output."""
        self.output.truncate(0)
        self.output.seek(0)

    def output_lines(self) -> list[str]:
        """Return output lines."""
        return self.output.getvalue().splitlines()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Wrap print collecting mock tables."""
        if args and isinstance(args[0], MockTable):
            self.tables.append(args[0])
        else:
            super().print(*args, **kwargs)


class MockTable:
    """Mock a rich Table, storing arguments."""

    def __init__(self, *args: Any, **kwargs: Any):
        """Store constructor arguments."""
        self.init = mock.call(*args, **kwargs)
        self.columns: list[mock._Call] = []
        self.rows: list[mock._Call] = []

    def add_column(self, *args: Any, **kwargs: Any) -> None:
        """Store column definition."""
        self.columns.append(mock.call(*args, **kwargs))

    def add_row(self, *args: Any, **kwargs: Any) -> None:
        """Store row."""
        self.rows.append(mock.call(*args, **kwargs))
