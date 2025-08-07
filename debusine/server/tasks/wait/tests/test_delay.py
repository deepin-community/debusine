# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Delay task."""

from unittest import TestCase

from django.utils import timezone

from debusine.server.tasks.wait import Delay
from debusine.tasks import TaskConfigError


class DelayTaskTests(TestCase):
    """Test the Delay task."""

    def test_missing_delay_until(self) -> None:
        """The `delay_until` field is required."""
        error_msg = (
            r"delay_until\s+field required \(type=value_error\.missing\)"
        )
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            Delay({})

    def test_no_additional_properties(self) -> None:
        """Assert no additional properties."""
        error_msg = "extra fields not permitted"
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            Delay({"delay_until": timezone.now().isoformat(), "input": {}})

    def test_label(self) -> None:
        """Test get_label."""
        task = Delay({"delay_until": "2024-01-01T12:00:00+00:00"})
        self.assertEqual(
            task.get_label(), "delay until 2024-01-01 12:00:00+00:00"
        )
