# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the DebusineHttpClient."""

import json
from collections import deque
from importlib.metadata import version
from io import BytesIO
from itertools import count
from typing import Any
from unittest import mock, skipIf

import requests
import responses

# Exported from top-level urllib3 module in >= 2.0.0, but that isn't in
# bookworm.
from urllib3.response import HTTPHeaderDict  # type: ignore[attr-defined]
from urllib3.response import HTTPResponse

from debusine.client import exceptions
from debusine.client.debusine_http_client import DebusineHttpClient
from debusine.client.models import StrictBaseModel
from debusine.test import TestCase

try:  # pragma: no cover
    from responses.registries import OrderedRegistry
except ImportError:
    OrderedRegistry = None  # type: ignore


class TestResponse(StrictBaseModel):
    """A dummy model for test cases."""

    __test__ = False

    id: int
    page: int | None = None


class DebusineHttpClientTests(TestCase):
    """Tests for the DebusineHttpClient."""

    def setUp(self) -> None:
        """Initialize member variables for the tests."""
        self.api_url = "https://debusine.debian.org/api/1.0"
        self.token = "token-for-server"

    def responses_add_test_endpoint(
        self, status: int, method: str = responses.GET
    ) -> None:
        """
        Add a test response.

        Used for testing the debusine API client low level methods.
        """
        json = None
        if status == requests.codes.ok:  # pragma: no cover
            json = {'id': 12}
        responses.add(
            method=method,
            url=self.api_url,
            json=json,
            status=status,
        )

    @staticmethod
    def responses_add_problem(
        method: str,
        url: str,
        title: str,
        detail: str | None = None,
        validation_errors: dict[str, str] | None = None,
        status: int = requests.codes.bad_request,
    ) -> None:
        """Add a debusine problem+json to responses."""
        response = {
            "title": title,
            "detail": detail,
            "validation_errors": validation_errors,
        }

        responses.add(
            method,
            url,
            json=response,
            content_type="application/problem+json",
            status=status,
        )

    @staticmethod
    def responses_add_paginated_responses(
        base_url: str, *, length: int = 4, page_size: int = 3
    ) -> None:
        """Add a set of responses for paginated listing requests."""
        objects = deque(TestResponse(id=i) for i in range(length))

        url = base_url
        for pageno in count(1):
            body: dict[str, Any] = {
                "count": length,
                "results": [],
            }
            for i in range(page_size):
                if not objects:
                    break
                obj = objects.popleft()
                obj.page = pageno
                body["results"].append(obj.dict())
            if objects:
                body["next"] = base_url + f"?page={pageno + 1}"
            if pageno > 1:
                body["previous"] = base_url + f"?page={pageno - 1}"

            from responses.matchers import query_string_matcher

            url_without_query_string, _, query_string = url.partition("?")

            responses.add(
                responses.GET,
                url_without_query_string,
                json=body,
                match=[query_string_matcher(query_string)],
            )

            if not objects:
                break
            url = body["next"]
        else:  # pragma: no cover
            pass

    def test_api_request_get_with_body_raise_value_error(self) -> None:
        """Raise ValueError if passing a body for the GET method."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaisesRegex(
            ValueError, "^data argument not allowed with HTTP GET$"
        ):
            debusine_client._api_request(
                "GET", self.api_url, TestResponse, data={}
            )

    @responses.activate
    def test_api_request_expected_class_none_response_content_raise_error(
        self,
    ) -> None:
        """Raise UnexpectedResponseError: response was not empty."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)

        method = "PUT"
        content = "test"

        responses.add(method, self.api_url, body=content)

        expected_regexp = (
            fr"^Server \({self.api_url}\) expected to return "
            f"an empty body. Returned:\nb'{content}'$"
        )

        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError, expected_regexp
        ):
            debusine_client._api_request(method, self.api_url, None, data=None)

    def test_api_request_only_valid_methods(self) -> None:
        """Raise ValueError if the method is not recognized."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaisesRegex(
            ValueError, "^Method must be one of: GET, POST, PUT, PATCH$"
        ):
            debusine_client._api_request("BAD-METHOD", "", TestResponse)

    @responses.activate
    def test_api_request_not_found_raise_exception(self) -> None:
        """Raise NotFoundError if the request returns 404."""
        self.responses_add_test_endpoint(status=requests.codes.not_found)
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaises(exceptions.NotFoundError):
            debusine_client._api_request("GET", self.api_url, requests.codes.ok)

        self.assert_token_key_included_in_all_requests(self.token)

    @responses.activate
    def test_api_request_raise_debusine_error(self) -> None:
        """Raise DebusineError: server returned HTTP 400 and error message."""
        title = "Cannot update task"
        detail = "Invalid task_name"
        errors = {"field": "invalid value"}

        self.responses_add_problem(
            responses.GET, self.api_url, title, detail, errors
        )

        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaises(exceptions.DebusineError) as debusine_error:
            debusine_client._api_request("GET", self.api_url, TestResponse)

        exception = debusine_error.exception

        self.assertEqual(
            exception.problem,
            {"title": title, "detail": detail, "validation_errors": errors},
        )

    @responses.activate
    def test_api_request_raise_token_disabled_error(self) -> None:
        """Raise TokenDisabledError: server returned HTTP 403."""
        self.responses_add_test_endpoint(status=requests.codes.forbidden)

        token = "b3ecf243c43"
        debusine_client = DebusineHttpClient(self.api_url, token)
        with self.assertRaisesRegex(
            exceptions.ClientForbiddenError,
            rf"^HTTP 403. Token \({token}\) is invalid or disabled$",
        ):
            debusine_client._api_request("GET", self.api_url, TestResponse)

    @responses.activate
    def test_api_request_raise_debusine_error_404(self) -> None:
        """Raise DebusineError even with response.code == 404."""
        title = "Error"
        detail = "Workflow template not found"

        self.responses_add_problem(
            responses.POST,
            self.api_url,
            title,
            detail=detail,
            status=requests.codes.not_found,
        )
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaises(exceptions.DebusineError) as debusine_error:
            debusine_client._api_request("POST", self.api_url, TestResponse)

        exception = debusine_error.exception

        self.assertEqual(
            exception.problem,
            {"title": title, "detail": detail, "validation_errors": None},
        )

    @responses.activate
    def test_api_request_raise_403_error_with_detail(self) -> None:
        """Raise TokenDisabledError: server returned HTTP 403 with detail."""
        title = "Error"
        detail = "You do not have permission to perform this action."

        self.responses_add_problem(
            responses.GET,
            self.api_url,
            title,
            detail=detail,
            status=requests.codes.forbidden,
        )

        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaises(exceptions.DebusineError) as debusine_error:
            debusine_client._api_request("GET", self.api_url, TestResponse)

        exception = debusine_error.exception

        self.assertEqual(
            exception.problem,
            {"title": title, "detail": detail, "validation_errors": None},
        )

    @responses.activate
    def test_api_request_raise_unexpected_error(self) -> None:
        """Raise UnexpectedResponseError for unexpected status code."""
        self.responses_add_test_endpoint(status=requests.codes.teapot)
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError,
            r"^Server returned unexpected status code: 418 "
            rf"\({self.api_url}\)$",
        ):
            debusine_client._api_request("GET", self.api_url, TestResponse)

        self.assert_token_key_included_in_all_requests(self.token)

    @responses.activate
    def test_api_request_raise_unexpected_response_400_no_error_msg(
        self,
    ) -> None:
        """Raise UnexpectedError: server returned HTTP 400 and no error msg."""
        self.responses_add_test_endpoint(status=requests.codes.bad_request)
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError,
            "^Server returned unexpected status code: 400 "
            fr"\({self.api_url}\)$",
        ):
            debusine_client._api_request("GET", self.api_url, TestResponse)

    @responses.activate
    def test_api_request_raise_unexpected_response_400_no_json(self) -> None:
        """Raise UnexpectedError: server returned HTTP 400 and no JSON."""
        responses.add(
            responses.GET,
            self.api_url,
            body="Not including JSON",
            status=requests.codes.bad_request,
        )
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError,
            "^Server returned unexpected status code: 400 "
            fr"\({self.api_url}\)$",
        ):
            debusine_client._api_request("GET", self.api_url, TestResponse)

    @skipIf(
        [int(part) for part in version("responses").split(".")] < [0, 21],
        "Requires responses >= 0.21 that supports testing Retry",
    )
    @responses.activate(registry=OrderedRegistry)
    def test_api_request_retry_errors(self) -> None:  # pragma: no cover
        """Some HTTP error responses are retried."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)

        for code in (
            requests.codes.server_error,  # 500
            requests.codes.service_unavailable,  # 503
            requests.codes.gateway_timeout,  # 504
            requests.codes.too_many_requests,  # 429
        ):
            for method in (
                responses.GET,
                responses.PATCH,
                responses.POST,
                responses.PUT,
            ):
                with self.subTest(method=method, code=code):
                    responses.reset()
                    self.responses_add_test_endpoint(method=method, status=code)
                    self.responses_add_test_endpoint(
                        method=method, status=requests.codes.ok
                    )
                    debusine_client._api_request(
                        method, self.api_url, TestResponse
                    )

    @skipIf(
        [int(part) for part in version("responses").split(".")] < [0, 21],
        "Requires responses >= 0.21 that supports testing Retry",
    )
    @responses.activate(registry=OrderedRegistry)
    def test_api_request_dont_retry_error(self) -> None:  # pragma: no cover
        """Many HTTP error responses are not retried, test a few."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)

        for code in (
            requests.codes.bad_request,  # 400
            requests.codes.unauthorized,  # 401
            requests.codes.forbidden,  # 403
            # Enabled by default in Retry:
            requests.codes.request_entity_too_large,  # 413
        ):
            with self.subTest(code=code):
                responses.reset()
                self.responses_add_test_endpoint(status=code)
                self.responses_add_test_endpoint(status=requests.codes.ok)
                with self.assertRaises(
                    (
                        exceptions.ClientForbiddenError,
                        exceptions.UnexpectedResponseError,
                    )
                ):
                    debusine_client._api_request(
                        responses.GET, self.api_url, TestResponse
                    )

    @responses.activate
    def test_api_request_returns_not_json_raise_exception(self) -> None:
        """Raise UnexpectedResponseError if body is not a valid JSON."""
        responses.add(
            responses.GET,
            self.api_url,
            body='Something that is not JSON as client expects',
        )
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError,
            fr"^Server \({self.api_url}\) did not return valid object. Error: ",
        ):
            debusine_client._api_request("GET", self.api_url, TestResponse)

        self.assert_token_key_included_in_all_requests(self.token)

    def test_api_request_create_and_use_session(self) -> None:
        """_api_request method create a session, uses and reuses it."""
        session_patch = mock.patch("requests.Session", autospec=True)
        session_mock = session_patch.start()
        self.addCleanup(session_patch.stop)

        token = "token"
        debusine_client = DebusineHttpClient(self.api_url, token)

        # Lazy creation of the session: not created yet
        self.assertIsNone(debusine_client._session)

        # Make a call (the HTTP GET is done, but it failed)
        with self.assertRaises(exceptions.UnexpectedResponseError):
            debusine_client._api_request("GET", self.api_url, TestResponse)

        session_mock.assert_called_once()
        session_mock.return_value.get.assert_called_once_with(
            self.api_url, headers={"Token": token}
        )

        session = debusine_client._session
        # Make a call (the HTTP GET is done, but it failed)
        with self.assertRaises(exceptions.UnexpectedResponseError):
            debusine_client._api_request("GET", self.api_url, TestResponse)

        # Same session reused
        self.assertIs(debusine_client._session, session)

    def test_api_request_no_token(self) -> None:
        """_api_request sends no Token header if the client has no token."""
        session_patch = mock.patch("requests.Session", autospec=True)
        session_mock = session_patch.start()
        self.addCleanup(session_patch.stop)

        debusine_client = DebusineHttpClient(self.api_url, None)

        # Make a call (the HTTP GET is done, but it failed)
        with self.assertRaises(exceptions.UnexpectedResponseError):
            debusine_client._api_request("GET", self.api_url, TestResponse)

        session_mock.assert_called_once()
        session_mock.return_value.get.assert_called_once_with(
            self.api_url, headers={}
        )

    def test_api_request_scope(self) -> None:
        """_api_request sends X-Debusine-Scope header."""
        session_patch = mock.patch("requests.Session", autospec=True)
        session_mock = session_patch.start()
        self.addCleanup(session_patch.stop)

        debusine_client = DebusineHttpClient(self.api_url, None, scope="test")

        # Lazy creation of the session: not created yet
        self.assertIsNone(debusine_client._session)

        # Make a call (the HTTP GET is done, but it failed)
        with self.assertRaises(exceptions.UnexpectedResponseError):
            debusine_client._api_request("GET", self.api_url, TestResponse)

        session_mock.assert_called_once()
        session_mock.return_value.get.assert_called_once_with(
            self.api_url, headers={"X-Debusine-Scope": "test"}
        )

    @responses.activate
    def test_api_request_submit_data_different_status_codes(self) -> None:
        """_api_request() return requests.codes.ok or created."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        data = {"foo": "bar"}

        for status_code in [requests.codes.ok, requests.codes.created]:
            responses.reset()
            responses.add(
                responses.POST,
                self.api_url,
                status=status_code,
                json={'id': 1},
            )

            if status_code == requests.codes.ok:
                expected_statuses = None
            else:
                expected_statuses = [status_code]

            debusine_client._api_request(
                "POST",
                self.api_url,
                TestResponse,
                data=data,
                expected_statuses=expected_statuses,
            )

            self.assert_token_key_included_in_all_requests(self.token)
            assert responses.calls[0].request.body is not None
            self.assertEqual(json.loads(responses.calls[0].request.body), data)

    @responses.activate
    def test_api_request_ok_no_content_type(self) -> None:
        """_api_request() handles a 200 response with no Content-Type."""

        class EmptyResponse(responses.BaseResponse):
            """A response without even a Content-Type."""

            def get_response(
                self, request: requests.PreparedRequest
            ) -> HTTPResponse:
                headers = HTTPHeaderDict()
                body = BytesIO()
                # See comments in responses._form_response for the mypy
                # suppressions here.
                orig_response = HTTPResponse(
                    body=body,
                    msg=headers,  # type: ignore[arg-type]
                )
                return HTTPResponse(
                    status=requests.codes.ok,
                    body=body,
                    headers=headers,
                    original_response=orig_response,  # type: ignore[arg-type]
                    request_method=request.method,
                )

        debusine_client = DebusineHttpClient(self.api_url, self.token)
        responses.add(EmptyResponse("PUT", self.api_url))

        debusine_client._api_request("PUT", self.api_url, None)

    @responses.activate
    def test_debusine_error_details_combinations_everything(self) -> None:
        """Test combinations of status and body for _debusine_error_details."""
        json_with_error_message = {'title': 'error message'}
        no_json = 'There is no JSON'

        content_type_problem = 'application/problem+json'
        content_type_not_problem = 'application/json'

        for body, expected, content_type in (
            (
                json_with_error_message,
                json_with_error_message,
                content_type_problem,
            ),
            (json_with_error_message, None, content_type_not_problem),
            (no_json, None, content_type_problem),
            (json_with_error_message, None, content_type_not_problem),
            (no_json, None, content_type_problem),
        ):
            with self.subTest(body=body):
                responses.reset()
                kwargs: dict[str, Any] = {'content_type': content_type}

                if isinstance(body, dict):
                    kwargs['json'] = body
                else:
                    kwargs['body'] = body

                responses.add(responses.GET, self.api_url, **kwargs)
                response = requests.get(self.api_url)

                error = DebusineHttpClient._debusine_problem(response)

                self.assertEqual(error, expected)

    @responses.activate
    def test_api_request_raise_client_connection_error(self) -> None:
        """Raise ClientConnectionError for RequestException/ConnectionError."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)

        for exception in (
            requests.exceptions.RequestException,
            ConnectionError,
        ):
            with self.subTest(exception=exception):
                responses.add(
                    responses.GET,
                    self.api_url,
                    body=exception(),
                )

                with self.assertRaisesRegex(
                    exceptions.ClientConnectionError,
                    f"^Cannot connect to {self.api_url}. Error: $",
                ):
                    debusine_client._api_request(
                        "GET", self.api_url, TestResponse
                    )

    @responses.activate
    def test_iter_paginated_get_iterates(self) -> None:
        """iter_paginated_get() iterates across pages."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        self.responses_add_paginated_responses(
            self.api_url, length=4, page_size=3
        )

        iter_ = debusine_client.iter_paginated_get("", TestResponse)
        for i in range(4):
            obj = next(iter_)
            self.assertEqual(obj.id, i)
        self.assertEqual(obj.page, 2)

    @responses.activate
    def test_iter_paginated_get_terminates(self) -> None:
        """iter_paginated_get() terminates at the end."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        self.responses_add_paginated_responses(
            self.api_url, length=4, page_size=3
        )

        response = list(debusine_client.iter_paginated_get("", TestResponse))
        self.assertEqual(
            response,
            [
                TestResponse(id=0, page=1),
                TestResponse(id=1, page=1),
                TestResponse(id=2, page=1),
                TestResponse(id=3, page=2),
            ],
        )

    @responses.activate
    def test_iter_paginated_get_handles_empty_set(self) -> None:
        """iter_paginated_get() handles an empty resultset."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        self.responses_add_paginated_responses(
            self.api_url, length=0, page_size=3
        )

        response = list(debusine_client.iter_paginated_get("", TestResponse))
        self.assertEqual(response, [])

    @responses.activate
    def test_iter_paginated_get_invalid_pagination_structure(self) -> None:
        """iter_paginated_get() handles an invalid pagination response."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        responses.add(
            responses.GET,
            self.api_url,
            json={"foo": "bar"},
        )
        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError,
            fr"^Server \({self.api_url}\) did not return valid object. Error: ",
        ):
            next(debusine_client.iter_paginated_get("", TestResponse))

    @responses.activate
    def test_iter_paginated_get_invalid_response(self) -> None:
        """iter_paginated_get() handles an invalid object in responses."""
        debusine_client = DebusineHttpClient(self.api_url, self.token)
        responses.add(
            responses.GET,
            self.api_url,
            json={
                "count": 12,
                "results": [{"foo": "bar"}],
            },
        )
        with self.assertRaisesRegex(
            exceptions.UnexpectedResponseError,
            fr"^Server \({self.api_url}\) did not return valid object. Error: ",
        ):
            next(debusine_client.iter_paginated_get("", TestResponse))
