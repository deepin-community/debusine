# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for exceptions."""

import json
import logging
from unittest import mock

from rest_framework import status

from debusine.server.exceptions import (
    DebusineAPIException,
    debusine_exception_handler,
)
from debusine.server.views import ProblemResponse
from debusine.test.django import TestCase


class DebusineExceptionHandler(TestCase):
    """Tests for debusine_exception_handler."""

    def patch_exception_handler(
        self, return_value: ProblemResponse | None
    ) -> None:
        """django_rest exception_handler() return return_value."""
        exception_handler_patcher = mock.patch(
            "debusine.server.exceptions.exception_handler"
        )
        exception_handler_mocked = exception_handler_patcher.start()
        exception_handler_mocked.return_value = return_value
        self.addCleanup(exception_handler_patcher.stop)

    def test_return_exception_handler_status_code(self) -> None:
        """debusine_exception_handler() return status_code from rest()."""
        expected_status_code = status.HTTP_418_IM_A_TEAPOT

        self.patch_exception_handler(
            mock.create_autospec(
                spec=ProblemResponse, status_code=expected_status_code
            )
        )

        response = debusine_exception_handler(Exception(), {})

        self.assertEqual(response.status_code, expected_status_code)

    def test_return_detail_from_exc(self) -> None:
        """debusine_exception_handler() return detail from exception."""
        expected_detail = "This is the detail"

        exc_mock = mock.create_autospec(
            spec=ProblemResponse, detail=expected_detail
        )

        response = debusine_exception_handler(exc_mock, {})

        content = json.loads(response.content)

        self.assertEqual(content["title"], "Error")
        self.assertEqual(content["detail"], expected_detail)

    def test_return_400_status_code(self) -> None:
        """debusine_exception_handler() return default HTTP 400 status code."""
        self.patch_exception_handler(None)

        response = debusine_exception_handler(Exception(), {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_does_not_return_detail(self) -> None:
        """debusine_exception_handler does not return detail."""
        self.patch_exception_handler(None)

        response = debusine_exception_handler(Exception(), {})

        content = json.loads(response.content)

        self.assertNotIn("detail", content)

    def test_logs_error_logs_detail(self) -> None:
        """debusine_exception_handler logs error with no relevant detail."""
        expected_detail = "Detail to be logged"

        exc_mock = mock.create_autospec(
            spec=ProblemResponse, detail=expected_detail
        )

        traceback_formatted = "This is the traceback"
        patcher = mock.patch("traceback.format_exc")
        format_exc_mock = patcher.start()
        format_exc_mock.return_value = traceback_formatted

        self.addCleanup(patcher.stop)

        debusine_exception_handler(exc_mock, {})

        with self.assertLogsContains(
            f"ERROR:debusine.server.exceptions:Server exception. "
            f"status_code: 400 detail: {expected_detail} "
            f"traceback: {traceback_formatted}",
            logger="debusine",
            level=logging.ERROR,
        ):
            debusine_exception_handler(exc_mock, {})

    def test_debusineapiexception(self) -> None:
        """Test turning DebusineAPIException into ProblemResponse."""
        exc = DebusineAPIException(
            title="title",
            detail="detail",
            validation_errors={"validation": "errors"},
            status_code=status.HTTP_418_IM_A_TEAPOT,
        )
        response = debusine_exception_handler(exc, {})
        self.assertIsInstance(response, ProblemResponse)
        self.assertEqual(response.status_code, status.HTTP_418_IM_A_TEAPOT)
        self.assertEqual(
            response.headers["Content-Type"], "application/problem+json"
        )
        self.assertEqual(
            json.loads(response.content),
            {
                'detail': 'detail',
                'title': 'title',
                'validation_errors': {'validation': 'errors'},
            },
        )
