# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""DebusineAsyncHttpClient: interact with the debusine server."""

import asyncio
import logging
from typing import Any, TYPE_CHECKING

import aiohttp
from aiohttp import ClientSession

from debusine.worker.config import ConfigHandler

if TYPE_CHECKING:
    from aiohttp.client import _RequestContextManager


class DebusineAsyncHttpClient:
    """HTTP Client to interact with the debusine server."""

    def __init__(self, config: ConfigHandler) -> None:
        """Initialize DebusineAsyncHttpClient."""
        self._config = config

        self._client_session: ClientSession | None = None

    def get(self, path: str) -> "_RequestContextManager":
        """Make a GET request."""
        return self._make_http_request('GET', path)

    def post(
        self, path: str, *, json: dict[str, Any], token: str | None = None
    ) -> "_RequestContextManager":
        """
        Make a POST request.

        :param path: used to create the URL (from config.api_url + path).
        :param json: body of the request.
        """
        return self._make_http_request('POST', path, json=json, token=token)

    def put(
        self, path: str, *, json: dict[str, Any]
    ) -> "_RequestContextManager":
        """
        Make a PUT request.

        :param path: used to create the URL (from config.api_url + path).
        :param json: body of the request.
        """
        return self._make_http_request('PUT', path, json=json)

    def _make_http_request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> "_RequestContextManager":
        """
        Make an HTTP request to the debusine server.

        Include the token if available in the config.
        """
        self._ensure_client_session()
        assert self._client_session is not None

        method_to_func = {
            'PUT': self._client_session.put,
            'POST': self._client_session.post,
            'GET': self._client_session.get,
        }

        if method not in method_to_func:
            allowed_methods = ", ".join(method_to_func.keys())
            raise ValueError(f'Method must be one of: {allowed_methods}')

        if method == 'GET' and 'data' in kwargs:
            raise ValueError('data not allowed in GET requests')

        if not path.startswith('/'):
            raise ValueError('Path must be absolute')

        headers = kwargs.pop('headers', {})

        if token is not None:
            headers['token'] = token
        elif self._config.token is not None:
            headers['token'] = self._config.token

        url = f'{self._config.api_url}{path}'

        logging.debug(
            "HTTP %s to the server: URL: %s args: %s kwargs: %s",
            method,
            url,
            args,
            kwargs,
        )
        return method_to_func[method](url, *args, **kwargs, headers=headers)

    def _ensure_client_session(
        self, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        if self._client_session is None:
            self._client_session = aiohttp.ClientSession(loop=loop)

    async def close(self) -> None:
        """Close the client session if needed."""
        if self._client_session is not None:
            await self._client_session.close()
