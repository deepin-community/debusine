# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the ContextMiddleware."""

from collections.abc import Mapping
from typing import Any, TYPE_CHECKING, cast
from unittest import IsolatedAsyncioTestCase

from django.http import HttpRequest, HttpResponse

from debusine.db.context import context
from debusine.server.middlewares.context import (
    ContextMiddleware,
    ContextMiddlewareChannels,
)
from debusine.test.django import TestCase

if TYPE_CHECKING:
    from django.core.handlers.asgi import _ReceiveCallback, _SendCallback

    from debusine.db.models import Scope

    _ReceiveCallback  # fake usage for vulture
    _SendCallback  # fake usage for vulture


class ContextMiddlewareTests(TestCase):
    """Test ContextMiddleware."""

    def test_preserve_context(self) -> None:
        """Make sure context works as intended on sync views."""
        mock_scope = cast("Scope", 42)
        mock_scope_old = cast("Scope", 41)

        def view(request: HttpRequest) -> HttpResponse:  # noqa: U100
            # Application context is reset at the beginning of the view
            self.assertIsNone(context.scope)

            # It can be set inside the view
            context.set_scope(mock_scope)
            self.assertEqual(context.scope, mock_scope)

            return HttpResponse()

        with context.local():
            # Simulate a scope left around by a previous request
            context.set_scope(mock_scope_old)

            wrapped_view = ContextMiddleware(view)
            wrapped_view(cast(HttpRequest, None))

            # Application context is left set
            self.assertEqual(context.scope, mock_scope)

        self.assertIsNone(context.scope)


class ContextMiddlewareChannelsTests(IsolatedAsyncioTestCase):
    """Test ContextMiddlewareChannels."""

    @classmethod
    async def _noop_receive(cls) -> dict[str, Any]:  # pragma: no cover
        """No-op receive callback for testing."""
        return {}

    @classmethod
    async def _noop_send(cls, message: Mapping[str, Any]) -> None:
        """No-op send callback for testing."""

    async def test_preserve_context(self) -> None:
        """Make sure context works as intended on ssync views."""
        mock_scope = cast("Scope", 42)
        mock_scope_old = cast("Scope", 41)

        async def view(
            scope: dict[str, Any],  # noqa: U100
            receive: "_ReceiveCallback",  # noqa: U100
            send: "_SendCallback",  # noqa: U100
        ) -> None:
            # Application context is reset at the beginning of the view
            self.assertIsNone(context.scope)

            # It can be set inside the view
            context.set_scope(mock_scope)
            self.assertEqual(context.scope, mock_scope)

        with context.local():
            # Simulate a scope left around by a previous request
            context.set_scope(mock_scope_old)

            wrapped_view = ContextMiddlewareChannels(view)
            await wrapped_view(
                cast(dict[str, Any], None), self._noop_receive, self._noop_send
            )

            # Application context is left set
            self.assertEqual(context.scope, mock_scope)

        self.assertIsNone(context.scope)
