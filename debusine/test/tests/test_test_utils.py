# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the test utility functions."""

import contextlib
import io
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from django.test import TestCase as DjangoTestCase

from debusine.db.models import Scope
from debusine.test import test_utils
from debusine.test.test_utils import (
    date_time_to_isoformat_rest_framework,
    print_sql_and_stack,
    tomorrow,
    yesterday,
)


class UtilsTest(TestCase):
    """Tests for functions in the utils module."""

    def test_data_generator(self) -> None:
        """Test utils.data_generator: assert type and properties of the data."""
        size = 100

        data_generator = test_utils.data_generator(size)

        data_1 = next(data_generator)

        self.assertIsInstance(data_1, bytes)
        self.assertEqual(len(data_1), size)

        half = int(size / 2)

        # Generated data should not be all the same. Ensure that
        # second half is different to the first half
        self.assertNotEqual(data_1[0:half], data_1[half:size])
        self.assertEqual(len(data_1[0:half]), len(data_1[half:size]))

        data_2 = next(data_generator)

        # Calling it again does not return the same data
        self.assertNotEqual(data_1, data_2)

    def test_yesterday(self) -> None:
        """Test utils.yesterday: return a time older than 24 from now in UTC."""
        yesterday_date = yesterday()

        # time zone aware
        self.assertEqual(yesterday_date.tzinfo, timezone.utc)

        delta = yesterday_date - datetime.now(timezone.utc) - timedelta(days=1)

        self.assertLess(delta, timedelta(seconds=1))

    def test_tomorrow(self) -> None:
        """Test utils.tomorrow: return a time older than 24 from now in UTC."""
        tomorrow_date = tomorrow()

        # time zone aware
        self.assertEqual(tomorrow_date.tzinfo, timezone.utc)

        delta = datetime.now(timezone.utc) + timedelta(days=1) - tomorrow_date

        self.assertLessEqual(delta, timedelta(seconds=1))

    def test_date_time_to_isoformat_rest_framework_utc(self) -> None:
        """Test method when timezone is UTC."""
        value = datetime(2023, 4, 21, 13, 14, 43, 999949, tzinfo=timezone.utc)
        self.assertEqual(
            date_time_to_isoformat_rest_framework(value),
            "2023-04-21T13:14:43.999949Z",
        )

    def test_date_time_to_isoformat_rest_framework_another_timezone(
        self,
    ) -> None:
        """Test method when timezone is not UTC."""
        value = datetime(
            2023, 4, 21, 13, 14, 43, 999949, tzinfo=timezone(timedelta(hours=1))
        )
        self.assertEqual(
            date_time_to_isoformat_rest_framework(value),
            "2023-04-21T13:14:43.999949+01:00",
        )


class UtilsDatabaseTest(DjangoTestCase):
    """Tests for functions in the utils module that need a database."""

    def test_print_sql_and_stack(self) -> None:
        """Test print_sql_and_stack."""
        # Nothing is printed if no queries are made
        with contextlib.redirect_stderr(io.StringIO()) as output:
            with print_sql_and_stack():
                pass
        self.assertEqual(output.getvalue(), "")

        # Output happens with queries
        with contextlib.redirect_stderr(io.StringIO()) as output:
            with print_sql_and_stack():
                list(Scope.objects.all())
        self.assertEqual(output.getvalue()[:4], "#1: ")
        self.assertIn("test_print_sql_and_stack", output.getvalue())
        self.assertIn("SELECT", output.getvalue())
