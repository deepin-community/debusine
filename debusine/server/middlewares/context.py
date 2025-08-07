# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Middleware for maintaining debusine.db.context."""

from collections.abc import Awaitable, Callable
from typing import Any, TYPE_CHECKING

import django.http

if TYPE_CHECKING:
    from django.core.handlers.asgi import _ReceiveCallback, _SendCallback

    _ReceiveCallback  # fake usage for vulture
    _SendCallback  # fake usage for vulture


class ContextMiddleware:
    """Run get_response in a private contextvar context."""

    def __init__(
        self,
        get_response: Callable[
            [django.http.HttpRequest], django.http.HttpResponse
        ],
    ) -> None:
        """Middleware API entry point."""
        self.get_response = get_response

    def __call__(
        self, request: django.http.HttpRequest
    ) -> django.http.HttpResponse:
        """Middleware entry point."""
        from debusine.db.context import context

        # Make application context request-local
        context.reset()
        return self.get_response(request)


class ContextMiddlewareChannels:
    """Run the app in a private contextvar context."""

    def __init__(
        self,
        app: Callable[
            [dict[str, Any], "_ReceiveCallback", "_SendCallback"],
            Awaitable[None],
        ],
    ) -> None:
        """Middleware API entry point."""
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: "_ReceiveCallback",
        send: "_SendCallback",
    ) -> None:
        """Middleware entry point."""
        from debusine.db.context import context

        # Make application context request-local
        context.reset()
        return await self.app(scope, receive, send)
