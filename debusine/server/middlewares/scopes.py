# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Transparently add scopes to URLs."""

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import django.http
from django.conf import settings
from django.utils import timezone

from debusine.server.scopes import get_scope_urlconf

if TYPE_CHECKING:
    from debusine.db.models import Scope

scope_prefix_re = re.compile(r'^/([^/]+)(/|$)')


class ScopeMiddleware:
    """
    Extract the current scope from the URL prefix.

    If used, it must be sequenced before
    django.middleware.common.CommonMiddleware, since it can make use of URL
    resolution.
    """

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
        from debusine.db.models.scopes import is_valid_scope_name

        scope_name: str | None = None

        if mo := scope_prefix_re.match(request.path_info):
            if mo.group(1) == "api":
                # /api/ gets special treatment, as scope can also be specified
                # in a header
                scope_name = request.headers.get("x-debusine-scope")
            elif is_valid_scope_name(mo.group(1)):
                scope_name = mo.group(1)

        if scope_name is None:
            scope_name = settings.DEBUSINE_DEFAULT_SCOPE

        context.set_scope(self.get_scope(scope_name))
        setattr(request, "urlconf", get_scope_urlconf(scope_name))

        return self.get_response(request)

    def get_scope(self, name: str) -> "Scope":
        """Set the current scope to the given named one."""
        from django.shortcuts import get_object_or_404

        from debusine.db.models import Scope

        return get_object_or_404(Scope, name=name)


class AuthorizationMiddleware:
    """
    Check user access to the current scope.

    If used, it must be sequenced after
    django.contrib.auth.middleware.AuthenticationMiddleware, since it needs the
    current user, and after
    debusine.server.middlewares.token_last_seen_at.TokenLastSeenAtMiddleware
    to validate the access of the worker token.
    """

    def __init__(
        self,
        get_response: Callable[
            [django.http.HttpRequest], django.http.HttpResponse
        ],
    ) -> None:
        """Middleware API entry point."""
        self.get_response = get_response

    def _validate_and_cache_token(
        self, request: django.http.HttpRequest
    ) -> None:
        """Validate and cache the token in the request."""
        from debusine.db.models import Token

        setattr(request, "_debusine_token", None)

        if (token_key := request.headers.get("token")) is None:
            return

        if (token := Token.objects.get_token_or_none(token_key)) is None:
            return

        token.last_seen_at = timezone.now()
        token.save()

        if not token.enabled:
            return

        setattr(request, "_debusine_token", token)

    def __call__(
        self, request: django.http.HttpRequest
    ) -> django.http.HttpResponse:
        """Middleware entry point."""
        from debusine.db.context import ContextConsistencyError, context

        self._validate_and_cache_token(request)

        # request.user may be a lazy object, e.g. as set up by
        # AuthenticationMiddleware.  In that case we must force it to be
        # evaluated here, as otherwise asgiref.sync.AsyncToSync may try to
        # evaluate it when restoring context and will raise a
        # SynchronousOnlyOperation exception.
        request.user.username

        if (token := getattr(request, "_debusine_token", None)) is not None:
            # If it's a worker token, we set it in context
            if hasattr(token, "worker"):
                context.set_worker_token(token)
                if token.user is not None:
                    return django.http.HttpResponseForbidden(
                        "a token cannot be both a user and a worker token"
                    )
            elif user := token.user:
                # If it's a user token, we may set it in context
                if request.user.is_authenticated:
                    return django.http.HttpResponseForbidden(
                        "cannot use both Django and user token authentication"
                    )

                # We set the user in context but NOT in request.user: that is
                # the job for rest_framework. Setting it in request.user here
                # will trigger rest_framework's CSRF protection. See #586 for
                # details
                try:
                    context.set_user(user)
                except ContextConsistencyError as e:
                    return django.http.HttpResponseForbidden(str(e))

                # Quit processing, as we just checked request.user
                return self.get_response(request)
            else:
                # Temporarily log only, later move to fail
                logging.warning(
                    "Token %s has no user and no worker associated", token
                )
                # return django.http.HttpResponseForbidden(
                #     "Token has no user and no worker associated"
                # )

        # We do not have users from tokens to deal with
        try:
            context.set_user(request.user)
        except ContextConsistencyError as e:
            return django.http.HttpResponseForbidden(str(e))

        return self.get_response(request)
