# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Debusine exceptions."""

from unittest import TestCase

from debusine.client.exceptions import DebusineError


class DebusineErrorTests(TestCase):
    """Tests for DebusineError exception."""

    def test_asdict(self) -> None:
        """Test asdict() returns the dictionary."""
        data = {
            "title": "The title of the error",
            "detail": "The detail of the error",
        }
        debusine_error = DebusineError(data)

        self.assertEqual(debusine_error.asdict(), data)
