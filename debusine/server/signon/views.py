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
Views needed to interact with external authentication providers.

Login and logout hooks are implemented as mixing for the normal
django.contrib.auth.LoginView/LogoutView.
"""

from collections.abc import Sequence
from typing import Any

from django import http
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.handlers.exception import response_for_exception
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import View

from debusine.db.models import Identity
from debusine.server.signon import providers
from debusine.server.signon.middleware import RequestSignonProtocol


class SignonLogoutMixin:
    """Mixin to logout external signon providers in a logout view."""

    @method_decorator(never_cache)
    def dispatch(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Wrap the normal logout to also log out identifiers."""
        if signon := getattr(request, "signon", None):
            signon.logout_identities()
        assert isinstance(self, View)
        return super().dispatch(request, *args, **kwargs)


class BindIdentityView(View):
    """
    Bind an external identity to the current user.

    This will initiate an external authentication, setting things up so that on
    success the identity is bound to the current user
    """

    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Check, setup, and redirect to the external identity provider."""
        if not request.user.is_authenticated:
            raise PermissionDenied

        try:
            provider = providers.get(self.kwargs["name"])
        except ImproperlyConfigured:
            raise PermissionDenied
        bound_provider = provider.bind(request)
        url = bound_provider.get_authorization_url("bind")
        return redirect(url)


class OIDCAuthenticationCallbackView(View):
    """
    Handle a callback from an external ODIC authentication provider.

    If successful, this activates the identity related to the provider,
    creating it if missing
    """

    def _validate(
        self, request: http.HttpRequest
    ) -> tuple[dict[str, Any], Sequence[str]]:
        """
        Validate the information from the remote OIDC provider.

        :return: the claims dict and the options passed to
                 BoundProvider.get_authorization_url
        """
        name = self.kwargs["name"]
        try:
            provider = providers.get(name)
        except ImproperlyConfigured:
            raise http.Http404

        bound_provider = provider.bind(request)
        bound_provider.load_tokens()

        return bound_provider.id_token_claims, bound_provider.options

    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """
        Notify successful authentication from from the external OIDC server.

        This is called by the external OIDC server.

        Validate the server information, activate the relevant Identity and
        recompute authentication information with the new information.
        """
        name = self.kwargs["name"]
        claims, options = self._validate(request)

        try:
            identity = Identity.objects.get(issuer=name, subject=claims["sub"])
        except Identity.DoesNotExist:
            identity = Identity.objects.create(
                issuer=name,
                subject=claims["sub"],
            )

        # Remove the audience claim, which we don't need to store
        claims.pop("aud", None)

        identity.claims = claims
        identity.last_used = timezone.now()
        identity.save()

        with transaction.atomic():
            # Handle the exception ourselves, since we want to save the
            # identity's last_used state even if this fails.
            try:
                assert isinstance(request, RequestSignonProtocol)
                request.signon.activate_identity(identity, *options)
            except Exception as exc:
                return response_for_exception(request, exc)

        if not (next_url := request.session.pop("sso_callback_next_url", None)):
            next_url = getattr(
                settings, "SIGNON_DEFAULT_REDIRECT", "homepage:homepage"
            )

        return redirect(next_url)
