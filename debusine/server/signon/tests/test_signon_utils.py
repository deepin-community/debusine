# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for signon provider backends."""


from unittest import TestCase

from debusine.server.signon.signon_utils import split_full_name


class SpliyFullName(TestCase):
    """Test split_full_name."""

    def test_empty(self) -> None:
        """Split an empty name."""
        self.assertEqual(split_full_name(""), ("", ""))

    def test_single(self) -> None:
        """Split a single name."""
        self.assertEqual(split_full_name("Test"), ("Test", ""))

    def test_first_last(self) -> None:
        """Split a common First+Last name."""
        self.assertEqual(split_full_name("First Last"), ("First", "Last"))

    def test_middle(self) -> None:
        """Split a 3-part name."""
        self.assertEqual(
            split_full_name("First Middle Last"), ("First Middle", "Last")
        )

    def test_hispanic(self) -> None:
        """Split a 2+2 name as is common in hispanic countries."""
        self.assertEqual(
            split_full_name("First1 First2 Last1 Last2"),
            ("First1 First2", "Last1 Last2"),
        )

    def test_many(self) -> None:
        """Deal gracefully with larger numbers of names."""
        self.assertEqual(
            split_full_name("First1 First2 Last1 Last2 Last3"),
            ("First1 First2", "Last1 Last2 Last3"),
        )

        self.assertEqual(
            split_full_name("First1 First2 First3 Last1 Last2 Last3"),
            ("First1 First2 First3", "Last1 Last2 Last3"),
        )
