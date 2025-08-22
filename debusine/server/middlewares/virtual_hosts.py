# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Assign the correct URL configuration for the requested virtual host."""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class VirtualHostMiddleware:
    """Assign appropriate URL configuration."""

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponse]
    ) -> None:
        """Middleware API entry point."""
        self.get_response = get_response

    @property
    def _archive_fqdns(self) -> list[str]:
        """Hosts used for serving archives."""
        if isinstance(settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN, list):
            return settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN
        else:
            return [settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN]

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Middleware entry point."""
        host = request.get_host()
        if host in self._archive_fqdns:
            setattr(request, "urlconf", "debusine.web.archives.urls")
        return self.get_response(request)
