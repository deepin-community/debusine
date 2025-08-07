# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the TokenLastSeenAtMiddleware."""
from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

from channels.db import database_sync_to_async
from django.utils import timezone

from debusine.db.context import context
from debusine.server.middlewares.token_last_seen_at import (
    TokenLastSeenAtMiddlewareChannels,
)
from debusine.test.django import TransactionTestCase

if TYPE_CHECKING:
    from django.core.handlers.asgi import _ReceiveCallback, _SendCallback

    _ReceiveCallback  # fake usage for vulture
    _SendCallback  # fake usage for vulture


class TokenLastSeenAtMiddlewareChannelsTests(TransactionTestCase):
    """Test TokenLastSeenAtMiddlewareChannels."""

    @classmethod
    async def _noop_receive(cls) -> dict[str, Any]:  # pragma: no cover
        """No-op receive callback for testing."""
        return {}

    @classmethod
    async def _noop_send(cls, message: Mapping[str, Any]) -> None:
        """No-op send callback for testing."""

    async def test_update_last_seen_at_store_token(self) -> None:
        """Request with a token updates token's last_seen_at."""
        token = await database_sync_to_async(
            self.playground.create_bare_token
        )()

        self.assertIsNone(token.last_seen_at)

        before = timezone.now()

        scope_upstream = {
            "type": "http",
            "method": "GET",
            "headers": [(b"token", token.key.encode("utf-8"))],
        }

        async def mock_app(
            scope_downstream: dict[str, Any],
            receive: "_ReceiveCallback",  # noqa: U100
            send: "_SendCallback",  # noqa: U100
        ) -> None:
            """Mock ASGI app to inspect the modified scope."""
            await token.arefresh_from_db()
            self.assertEqual(scope_downstream["token"], token)
            self.assertIsNone(context.worker_token)
            # self.assertEqual(context.worker_token, token)
            self.assertGreaterEqual(token.last_seen_at, before)
            self.assertLess(token.last_seen_at, timezone.now())

        middleware = TokenLastSeenAtMiddlewareChannels(app=mock_app)

        await middleware(scope_upstream, self._noop_receive, self._noop_send)

    async def test_update_last_seen_at_store_worker_token(self) -> None:
        """Request with a token updates token's last_seen_at."""
        token = await database_sync_to_async(
            self.playground.create_worker_token
        )()

        self.assertIsNone(token.last_seen_at)

        before = timezone.now()

        scope_upstream = {
            "type": "http",
            "method": "GET",
            "headers": [(b"token", token.key.encode("utf-8"))],
        }

        async def mock_app(
            scope_downstream: dict[str, Any],
            receive: "_ReceiveCallback",  # noqa: U100
            send: "_SendCallback",  # noqa: U100
        ) -> None:
            """Mock ASGI app to inspect the modified scope."""
            await token.arefresh_from_db()
            self.assertEqual(scope_downstream["token"], token)
            self.assertEqual(context.worker_token, token)
            self.assertGreaterEqual(token.last_seen_at, before)
            self.assertLess(token.last_seen_at, timezone.now())

        middleware = TokenLastSeenAtMiddlewareChannels(app=mock_app)

        await middleware(scope_upstream, self._noop_receive, self._noop_send)

    async def test_request_without_token(self) -> None:
        """Request without a token."""
        scope_upstream = {"type": "http", "method": "GET", "headers": {}}

        async def mock_app(
            scope_downstream: dict[str, Any],
            receive: "_ReceiveCallback",  # noqa: U100
            send: "_SendCallback",  # noqa: U100
        ) -> None:
            """Mock ASGI app to inspect the modified scope."""
            self.assertIsNone(scope_downstream["token"])
            self.assertIsNone(context.worker_token)

        middleware = TokenLastSeenAtMiddlewareChannels(app=mock_app)

        await middleware(scope_upstream, self._noop_receive, self._noop_send)

    async def test_request_token_key_does_not_exist(self) -> None:
        """Request where the Token key in the header does not exist."""
        scope_upstream = {
            "type": "http",
            "method": "GET",
            "headers": {"token": "DOES-NOT-EXIST"},
        }

        async def mock_app(
            scope_downstream: dict[str, Any],
            receive: "_ReceiveCallback",  # noqa: U100
            send: "_SendCallback",  # noqa: U100
        ) -> None:
            """Mock ASGI app to inspect the modified scope."""
            self.assertFalse(hasattr(scope_downstream, "token"))
            self.assertIsNone(context.worker_token)

        middleware = TokenLastSeenAtMiddlewareChannels(app=mock_app)
        await middleware(scope_upstream, self._noop_receive, self._noop_send)

    async def test_request_token_disabled(self) -> None:
        """Request with a disabled Token."""
        token = await database_sync_to_async(
            self.playground.create_worker_token
        )(enabled=False)

        scope_upstream = {
            "type": "http",
            "method": "GET",
            "headers": [(b"token", token.key.encode("utf-8"))],
        }

        async def mock_app(
            scope_downstream: dict[str, Any],
            receive: "_ReceiveCallback",  # noqa: U100
            send: "_SendCallback",  # noqa: U100
        ) -> None:
            """Mock ASGI app to inspect the modified scope."""
            self.assertEqual(scope_downstream["token"], token)
            self.assertIsNone(context.worker_token)

        middleware = TokenLastSeenAtMiddlewareChannels(app=mock_app)
        await middleware(scope_upstream, self._noop_receive, self._noop_send)
