# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""DebusineHttpClient interact with the debusine server API."""
from collections.abc import Callable, Generator
from typing import Any, TypeVar, overload

try:
    from pydantic.v1 import BaseModel, ValidationError
except ImportError:
    from pydantic import BaseModel, ValidationError  # type: ignore

import requests
from requests.compat import json  # type: ignore  # noqa: I202
from urllib3.util import Retry

from debusine.client import exceptions
from debusine.client.models import PaginatedResponse

ClientModel = TypeVar("ClientModel", bound=BaseModel)


class DebusineHttpClient:
    """DebusineHttpClient interact with the debusine server API."""

    def __init__(
        self, api_url: str, token: str | None, scope: str | None = None
    ) -> None:
        """Initialize DebusineHttpClient."""
        self._api_url = api_url
        self._scope = scope
        self._token = token

        self._session: requests.Session | None = None

    def get(self, path: str, expected_class: type[ClientModel]) -> ClientModel:
        """Make an HTTP GET request to path, return object of expected_class."""
        return self._api_request("GET", self._api_url + path, expected_class)

    def iter_paginated_get(
        self, path: str, expected_class: type[ClientModel]
    ) -> Generator[ClientModel, None, None]:
        """
        Iterate through a paginated listing endpoint.

        Yield objects of expected_class.
        """
        url = self._api_url + path
        while True:
            r = self._api_request("GET", url, PaginatedResponse)
            for result in r.results:
                try:
                    yield expected_class.parse_obj(result)
                except ValidationError as exc:
                    raise exceptions.UnexpectedResponseError(
                        f"Server ({url}) did not return valid object. "
                        f"Error: {str(exc)}"
                    ) from exc
            if not r.next:
                break
            url = r.next

    @overload
    def post(
        self,
        path: str,
        expected_class: None,
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> None: ...

    @overload
    def post(
        self,
        path: str,
        expected_class: type[ClientModel],
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel: ...

    def post(
        self,
        path: str,
        expected_class: type[ClientModel] | None,
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel | None:
        """
        Make an HTTP POST request to path.

        Send data and return an object of expected_class.
        """
        return self._api_request(
            "POST",
            self._api_url + path,
            expected_class,
            data,
            expected_statuses=expected_statuses,
        )

    def put(
        self,
        path: str,
        expected_class: type[ClientModel] | None,
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel | None:
        """
        Make an HTTP PUT request to path.

        Send data and return an object of expected_class.
        """
        return self._api_request(
            "PUT",
            self._api_url + path,
            expected_class,
            data,
            expected_statuses=expected_statuses,
        )

    @overload
    def patch(
        self,
        path: str,
        expected_class: None,
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> None: ...

    @overload
    def patch(
        self,
        path: str,
        expected_class: type[ClientModel],
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel: ...

    def patch(
        self,
        path: str,
        expected_class: type[ClientModel] | None,
        data: dict[str, Any] | None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel | None:
        """
        Make an HTTP PATCH request to path.

        Send data and return an object of expected_class.
        """
        return self._api_request(
            "PATCH",
            self._api_url + path,
            expected_class,
            data,
            expected_statuses=expected_statuses,
        )

    def _method(self, method: str) -> Callable[..., requests.Response]:
        if not self._session:
            raise AssertionError("self._session not set")
        method_to_func: dict[str, Callable[..., requests.Response]] = {
            'GET': self._session.get,
            'POST': self._session.post,
            'PUT': self._session.put,
            'PATCH': self._session.patch,
        }

        if method not in method_to_func:
            allowed_methods = ", ".join(method_to_func.keys())
            raise ValueError(f'Method must be one of: {allowed_methods}')

        return method_to_func[method]

    @staticmethod
    def _handle_response_content(
        response: requests.Response,
        expected_class: type[ClientModel] | None,
        url: str,
    ) -> ClientModel | None:
        if expected_class is None:
            if response.content != b"":
                raise exceptions.UnexpectedResponseError(
                    f"Server ({url}) expected to return an empty body. "
                    f"Returned:\n{response.content!r}"
                )
            else:
                return None
        else:
            try:
                return expected_class.parse_raw(response.content)
            except ValidationError as exc:
                raise exceptions.UnexpectedResponseError(
                    f"Server ({url}) did not return valid object. "
                    f"Error: {str(exc)}"
                ) from exc

    @overload
    def _api_request(
        self,
        method: str,
        url: str,
        expected_class: None,
        data: dict[str, Any] | None = None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> None: ...

    @overload
    def _api_request(
        self,
        method: str,
        url: str,
        expected_class: type[ClientModel],
        data: dict[str, Any] | None = None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel: ...

    def _api_request(
        self,
        method: str,
        url: str,
        expected_class: type[ClientModel] | None,
        data: dict[str, Any] | None = None,
        *,
        expected_statuses: list[int] | None = None,
    ) -> ClientModel | None:
        """
        Request to the server.

        :param method: HTTP method (GET, POST, ...).
        :param url: The complete URL to request.
        :param expected_class: expected object class that the server.
          will return. Used to deserialize the response and return an object.
          If None expects an empty response body.
        :param expected_statuses: defaults to expect HTTP 200. List of HTTP
          status codes that might be return by the call. If it receives
          an unexpected one it raises UnexpectedResponseError.
        :raises exceptions.UnexpectedResponseError: the server didn't return
          a valid JSON or returned an unexpected HTTP status code.
        :raises exceptions.WorkRequestNotFound: the server could not find the
          work request.
        :raises exceptions.ClientConnectionError: the client could not connect
          to the server.
        :raises ValueError: invalid options passed.
        :raises DebusineError: the server returned an error with
          application/problem+json. Contains the detail message.
        :raises exceptions.ClientForbiddenError: the server returned HTTP 403.
        :return: an object of the expected_class or None if expected_class
          was None.
        """
        self._ensure_session()

        if data is not None and method == 'GET':
            raise ValueError('data argument not allowed with HTTP GET')

        if expected_statuses is None:
            expected_statuses = [requests.codes.ok]

        optional_kwargs = {}

        if data is not None:
            optional_kwargs = {'json': data}

        try:
            headers = {}
            if self._scope is not None:
                headers["X-Debusine-Scope"] = self._scope
            if self._token is not None:
                headers["Token"] = self._token
            response = self._method(method)(
                url, headers=headers, **optional_kwargs
            )
        except (requests.exceptions.RequestException, ConnectionError) as exc:
            raise exceptions.ClientConnectionError(
                f'Cannot connect to {url}. Error: {str(exc)}'
            ) from exc

        if error := self._debusine_problem(response):
            raise exceptions.DebusineError(error)
        elif response.status_code in expected_statuses:
            return self._handle_response_content(response, expected_class, url)
        elif response.status_code == requests.codes.not_found:
            raise exceptions.NotFoundError(f'Not found ({url})')
        elif response.status_code == requests.codes.forbidden:
            raise exceptions.ClientForbiddenError(
                f"HTTP 403. Token ({self._token}) is invalid or disabled"
            )
        else:
            raise exceptions.UnexpectedResponseError(
                f'Server returned unexpected status '
                f'code: {response.status_code} ({url})'
            )

    def _ensure_session(self) -> None:
        if self._session is None:
            self._session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                max_retries=Retry(
                    total=4,
                    backoff_factor=0.1,
                    status_forcelist=[
                        requests.codes.server_error,  # 500
                        requests.codes.service_unavailable,  # 503
                        requests.codes.gateway_timeout,  # 504
                        # backoff factor will result in a short wait
                        requests.codes.too_many_requests,  # 429
                    ],
                    allowed_methods={"GET", "PATCH", "POST", "PUT"},
                )
            )
            self._session.mount("https://", adapter)

    @staticmethod
    def _debusine_problem(response: requests.Response) -> dict[Any, Any] | None:
        """If response is an application/problem+json returns the body JSON."""
        if response.headers.get("content-type") == "application/problem+json":
            try:
                content = response.json()
            except json.JSONDecodeError:
                return None

            assert isinstance(content, dict)
            return content

        return None
