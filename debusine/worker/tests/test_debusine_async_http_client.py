# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests DebusineAsyncHttpClient."""
import asyncio.log
import itertools
import logging

from debusine.test import TestCase
from debusine.worker.config import ConfigHandler
from debusine.worker.debusine_async_http_client import DebusineAsyncHttpClient
from debusine.worker.tests import server


class TestDebusineAsyncHttpClient(server.DebusineAioHTTPTestCase, TestCase):
    """Tests for DebusineAsyncHttpClient."""

    async def setUpAsync(self) -> None:
        """Set up tests."""
        # asyncio/base_events.py print "<some information> is serving" in the
        # debug log. Unit tests can be executed with environment variables:
        # PYTHONASYNCDEBUG=1 PYTHONDEBUG=1 (which has been useful in the past)
        # but the "<some information> is serving" message can be discarded.
        logger = logging.getLogger(asyncio.log.logger.name)

        logger.addFilter(lambda record: not record.msg.endswith("is serving"))

        await super().setUpAsync()
        self.debusine_url = str(self.server.make_url(''))

        self.config = self.build_basic_config()

        self.debusine_async_http_client = DebusineAsyncHttpClient(self.config)

    async def tearDownAsync(self) -> None:
        """Tear down."""
        await super().tearDownAsync()
        await self.debusine_async_http_client.close()

    def build_basic_config(
        self, overwrite_url: str | None = None
    ) -> ConfigHandler:
        """Build a basic configuration with log-file = /dev/null."""
        url = overwrite_url or self.debusine_url

        config_temp_directory = self.create_temp_config_directory(
            {'General': {'api-url': url}}
        )

        config = ConfigHandler(directories=[config_temp_directory])
        config['General']['log-file'] = '/dev/null'

        return config

    def setup_valid_token(self, include_token: bool) -> None:
        """Set a valid token in the test server and client configuration."""
        self.config = self.build_basic_config()

        if include_token:
            token = '6c931875627131b5135b7de3371c44'
            self.server_config.registered_token = token
            self.config.write_token(token)
        else:
            self.server_config.registered_token = None

        self.debusine_async_http_client._config = self.config

    async def test_make_http_request_raise_path_must_be_absolute(self) -> None:
        """Worker._make_http_request raise exception for relative paths."""
        with self.assertRaisesRegex(ValueError, 'Path must be absolute'):
            self.debusine_async_http_client._make_http_request(
                'PUT', 'api/endpoint'
            )

    async def test_make_http_request_raise_invalid_method(self) -> None:
        """Worker._make_http_request raise exception for unknown HTTP method."""
        with self.assertRaisesRegex(
            ValueError, '^Method must be one of: PUT, POST, GET$'
        ):
            self.debusine_async_http_client._make_http_request(
                'connect', '/api/endpoint'
            )

    async def make_http_request(
        self, method: str, path: str, token: str | None = None
    ) -> dict[str, str] | None:
        """
        Make HTTP request, return data sent (or None if no data was sent).

        :param method: HTTP method (GET, POST...)
        :param path: path for the request.
        """
        if method != 'GET':
            data = {'key': 'data'}
        else:
            data = None

        await self.debusine_async_http_client._make_http_request(
            method, path, json=data, token=token
        )

        return data

    async def assert_http_request(
        self,
        method: str,
        path: str,
        include_token: bool,
        data: dict[str, str] | None,
    ) -> None:
        """
        Assert that Worker._make_http_request sends the data and token.

        :param method: HTTP method used for the test.
        :param path: path to match the request.
        :param include_token: includes the token in the headers or not.
        :param data: data to match the request.
        """
        # We've always performed at least one request by this point.
        assert self.server_latest_request is not None
        assert self.server_latest_request.headers is not None

        # Token was included
        self.assertEqual(
            'token' in self.server_latest_request.headers, include_token
        )

        # HTTP method sent is the method received
        self.assertEqual(method, self.server_latest_request.method)

        # Data sent from the client is the data received in the server
        self.assertEqual(self.server_latest_request.json_content, data)

        # Path that the request was made is the path that it was received
        self.assertEqual(self.server_latest_request.path, path)

    async def test_make_http_request(self) -> None:
        """Worker._make_http_request makes the request without token."""
        path = '/collect_request_information/'

        for method, include_token in itertools.product(
            ["PUT", "POST", "GET"], [True, False]
        ):
            with self.subTest(method=method):
                self.setup_valid_token(include_token)
                data = await self.make_http_request(method, path)
                await self.assert_http_request(
                    method, path, include_token=include_token, data=data
                )

    async def test_make_http_request_explicit_token(self) -> None:
        """_make_http_request sends an explicitly-requested token."""
        path = '/collect_request_information/'
        token = 'a84b92e3c3379733bf0d2b25700c4c'

        for method, config_has_token in itertools.product(
            ["PUT", "POST", "GET"], [True, False]
        ):
            with self.subTest(method=method):
                self.setup_valid_token(config_has_token)
                data = await self.make_http_request(method, path, token=token)
                await self.assert_http_request(
                    method, path, include_token=True, data=data
                )

    async def test_make_http_request_get_with_data_raises_value_error(
        self,
    ) -> None:
        """_make_http_request raises ValueError if data passed in HTTP GET."""
        with self.assertRaisesRegex(
            ValueError, '^data not allowed in GET requests$'
        ):
            self.debusine_async_http_client._make_http_request(
                'GET', path='/path/', data={'some_data': 'data'}
            )
