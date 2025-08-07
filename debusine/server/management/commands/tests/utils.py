# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common code for tabular output tests."""

import contextlib
import unittest
from collections.abc import Generator
from typing import Any
from unittest import mock

from rich.table import Table


class TableOutput:
    """Holder for assertPrintTable result."""

    def __init__(self, table: Table | None = None) -> None:
        """Make space to hold a table."""
        self.table: Table | None = table

    def col(self, index: int) -> list[Any]:
        """Return a list of values for a table column."""
        assert self.table is not None
        return list(self.table.columns[index].cells)


class TabularOutputTests(unittest.TestCase):
    """Common functions for testing tabular output."""

    @contextlib.contextmanager
    def assertPrintsTable(self) -> Generator[TableOutput, None, None]:
        """Check that a rich.Table is printed."""
        output = TableOutput()
        with mock.patch("rich.print") as rprint:
            yield output
        rprint.assert_called_once()
        output.table = rprint.call_args.args[0]
        self.assertIsInstance(output.table, Table)

    @contextlib.contextmanager
    def assertPrintsTables(self) -> Generator[list[TableOutput], None, None]:
        """Check that multiple rich.Tables are printed."""
        output: list[TableOutput] = []
        with mock.patch("rich.print") as rprint:
            yield output
        rprint.assert_called()
        for call in rprint.call_args_list:
            table = call.args[0]
            self.assertIsInstance(table, Table)
            output.append(TableOutput(table=table))
