# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test for the main entry point of debusine."""

from unittest import TestCase, mock

from debusine.client.__main__ import entry_point


class ConfigHandlerTests(TestCase):
    """Tests for ConfigHandler class."""

    def test_main_entry_point(self) -> None:
        """Ensure client's main entry point calls Cli().parse()."""
        with mock.patch(
            "debusine.client.cli.Cli.execute", autospec=True
        ) as mocked_main:
            entry_point()

        self.assertEqual(mocked_main.call_count, 1)
