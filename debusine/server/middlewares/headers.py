# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Set common HTTP headers."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers


class HeadersMiddleware:
    """Set common HTTP headers."""

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponse]
    ) -> None:
        """Middleware API entry point."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Middleware entry point."""
        response = self.get_response(request)
        # Don't allow caches to use responses to satisfy requests that have
        # different authentication.
        patch_vary_headers(response, ("Cookie", "Token"))
        return response
