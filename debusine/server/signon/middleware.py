# Copyright 2020-2023 Enrico Zini <enrico@debian.org>
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Authentication middleware that authenticates using signon Providers.

It adds a `request.signon` member that is a Signon object, providing an entry
point for managing externally authenticated identities.
"""
from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable

import django.http
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, MiddlewareNotUsed
from django.utils.module_loading import import_string

from debusine.server.signon.signon import Signon


@runtime_checkable
class RequestSignonProtocol(Protocol):
    """A Django request that was processed by :py:class:`SignonMiddleware`."""

    signon: Signon


class SignonMiddleware:
    """Authenticate via external signon providers."""

    signon_class: type[Signon]

    def __init__(
        self,
        get_response: Callable[
            [django.http.HttpRequest], django.http.HttpResponse
        ],
    ) -> None:
        """Middleware API entry point."""
        self.providers = getattr(settings, "SIGNON_PROVIDERS", ())
        if not self.providers:
            raise MiddlewareNotUsed()

        # Find the Signon class to use. This allows customizing behaviour by
        # subclassing Signon
        if (
            signon_class_path := getattr(settings, "SIGNON_CLASS", None)
        ) is None:
            self.signon_class = Signon
        else:
            self.signon_class = import_string(signon_class_path)
            if not issubclass(self.signon_class, Signon):
                raise ImproperlyConfigured(
                    f"{signon_class_path} is not a subclass of Signon"
                )

        self.get_response = get_response

    def __call__(
        self, request: django.http.HttpRequest
    ) -> django.http.HttpResponse:
        """Middleware API entry point."""
        # AuthenticationMiddleware is required so that request.user exists.
        if not hasattr(request, 'user'):
            raise ImproperlyConfigured(
                "The signon middleware requires the authentication middleware"
                " to be installed.  Edit your MIDDLEWARE setting to insert"
                " 'django.contrib.auth.middleware.AuthenticationMiddleware'"
                " before the SignonMiddleware class."
            )

        # Add request.signon
        cast(RequestSignonProtocol, request).signon = self.signon_class(request)

        return self.get_response(request)
