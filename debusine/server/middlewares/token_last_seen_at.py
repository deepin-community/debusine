# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Middleware for updating Worker.last_seen_at."""
from collections.abc import Awaitable, Callable
from typing import Any, TYPE_CHECKING

from channels.db import database_sync_to_async
from django.utils import timezone

from debusine.db.context import context
from debusine.db.models import Token

if TYPE_CHECKING:
    from django.core.handlers.asgi import _ReceiveCallback, _SendCallback

    _ReceiveCallback  # fake usage for vulture
    _SendCallback  # fake usage for vulture


class TokenLastSeenAtMiddlewareChannels:
    """
    Save the Token in the scope and update Worker.last_seen_at.

    Token available in the scope helps to avoid double lookups.
    """

    def __init__(
        self,
        app: Callable[
            [dict[str, Any], "_ReceiveCallback", "_SendCallback"],
            Awaitable[None],
        ],
    ) -> None:
        """Middleware API entry point."""
        self.app = app

    @staticmethod
    def get_token_header(headers: list[tuple[bytes, bytes]]) -> str | None:
        """Return the token key from the headers or None."""
        for header in headers:
            if header[0].lower() == b"token":
                return header[1].decode("utf-8")

        return None

    async def is_worker_token(self, token: Token) -> bool:
        """Check if a token is a worker token."""
        is_worker_token = await database_sync_to_async(
            lambda: hasattr(token, "worker")
        )()
        assert isinstance(is_worker_token, bool)
        return is_worker_token

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: "_ReceiveCallback",
        send: "_SendCallback",
    ) -> None:
        """Middleware entry point."""
        # When middleware is modifying the scope, it should make a copy of the
        # scope object before mutating it.
        # See https://asgi.readthedocs.io/en/latest/specs/main.html#middleware
        scope = dict(scope)

        token_key = self.get_token_header(scope["headers"])

        if token_key is None:
            # No token was in the request, nothing needs to be done
            scope["token"] = None
            return await self.app(scope, receive, send)

        token = await database_sync_to_async(Token.objects.get_token_or_none)(
            token_key
        )

        if token is not None:
            token.last_seen_at = timezone.now()
            await token.asave()
            # NOTE: checking if a token is a worker token should not hit the
            # database since get_token_or_none does select_related("worker").
            # However, it does (see !!1278, so it needs database_sync_to_async
            if token.enabled and await self.is_worker_token(token):
                context.set_worker_token(token)

        scope["token"] = token

        return await self.app(scope, receive, send)
