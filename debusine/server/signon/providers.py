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
Support for external signon providers.

This is configured by the SIGNON_PROVIDERS variable in django settings.

SIGNON_PROVIDERS is expected to be a sequence of `Provider` instances, one for
each supported signon provider.

Example::

    # Configure salsa.debian.org as identity provider
    SIGNON_PROVIDERS=[
        providers.GitlabProvider(
            name="salsa",
            label="Salsa",
            icon="signon/gitlabian.svg",
            client_id="123client_id",
            client_secret="123client_secret",
            url="https://salsa.debian.org",
            restrict=["email-verified", "group:debian"],
        ),
    ]

The `restrict` setting can be used to restrict local account creation to
remote users that have a specific set of claims. It can be set to a sequence of
strings, each specifying a restriction:

* `group:name` the given group name must be present
* `email-verified` the primary email needs to be reported as verified

All listed restrictions must be met (i.e. they are AND-ed together).
"""

import functools
import json
from collections.abc import Collection
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import django.http
    import jwcrypto.jwk

# Note: this module is supposed to be imported from settings.py
#
# Its module-level import list for the case of defining providers should be
# kept accordingly minimal


def get(name: str) -> "Provider":
    """
    Look up a provider by name.

    :param name: name of the provider to look up, matching Provider.name
    :raises ImproperlyConfigured: if no provider with that name has been
                                  defined in settings
    :return: the Provider instance
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    providers = getattr(settings, "SIGNON_PROVIDERS", None)
    if providers is None:
        raise ImproperlyConfigured(
            f"signon provider {name} requested,"
            " but SIGNON_PROVIDERS is not defined in settings"
        )

    # Lookup provider by name
    for p in providers:
        if p.name == name:
            if not isinstance(p, Provider):
                raise ImproperlyConfigured(
                    f"signon provider {name} requested,"
                    f" but its entry in SIGNON_PROVIDERS setting is not a"
                    f" Provider"
                )
            return p

    raise ImproperlyConfigured(
        f"signon provider {name} requested,"
        " but not found in SIGNON_PROVIDERS setting"
    )


class BoundProvider:
    """
    Request-aware proxy for Provider.

    This class provides provider-specific functionality based on the current
    Django request object.
    """

    def __init__(
        self, provider: "Provider", request: "django.http.HttpRequest"
    ) -> None:
        """
        Construct a BoundProvider from a Provider and a HttpRequest.

        :param provider: provider to bind to a request
        :param request: current Django request
        """
        self.provider = provider
        self.request = request

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to the provider definition."""
        return getattr(self.provider, name)

    def logout(self) -> None:
        """Log out an externally authenticated identity."""
        from debusine.server.signon.middleware import RequestSignonProtocol

        assert isinstance(self.request, RequestSignonProtocol)
        self.request.signon.identities.pop(self.provider.name, None)
        self.request.session.pop(f"signon_identity_{self.provider.name}", None)


class Provider:
    """Information about a signon identity provider."""

    #: Identifier to reference the provider in code and configuration
    name: str
    #: User-visible description
    label: str
    #: Optional user-visible icon, resolved via ``{% static %}`` in templates
    icon: str | None
    #: Freeform options used to configure behaviour for Signon subclasses
    options: dict[str, Any]
    #: Class used to create a request-bound version
    bound_class: type["BoundProvider"] = BoundProvider

    def __init__(
        self,
        name: str,
        label: str,
        *,
        icon: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        """
        Define an external authentication provider.

        Provider implementation subclasses can definer further keyword
        arguments.
        """
        self.name = name
        self.label = label
        self.icon = icon
        self.options: dict[str, Any] = options or {}

    def bind(self, request: "django.http.HttpRequest") -> "BoundProvider":
        """Create a BoundProvider for this session."""
        return self.bound_class(self, request)


class OIDCValidationError(Exception):
    """Exception raised when OIDC authentication fails validation."""


class BoundOIDCProvider(BoundProvider):
    """Bound version of the OpenID Connect provider."""

    provider: "OIDCProvider"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Construct a BoundProvider for OpenID Connect."""
        super().__init__(*args, **kwargs)
        from django.urls import reverse
        from requests_oauthlib import OAuth2Session

        self.oauth = OAuth2Session(
            self.provider.client_id,
            scope=self.provider.scope,
            redirect_uri=self.request.build_absolute_uri(
                # FIXME: the URL name is currently dependent on the way this
                # module is deployed. This needs rethinking if we want to
                # publish this as a reusable module
                reverse("signon:oidc_callback", args=(self.name,))
            ),
        )
        self.tokens = None
        self.id_token_claims: dict[str, Any] | None = None
        self.options: Collection[str] | None = None

    def get_authorization_url(self, *options: str) -> str:
        """Return an authorization URL for this provider."""
        url, state = self.oauth.authorization_url(self.provider.url_authorize)
        self.request.session[f"signon_state_{self.provider.name}"] = state
        self.request.session[f"signon_state_{self.provider.name}_options"] = (
            options
        )
        assert isinstance(url, str)
        return url

    @functools.cached_property
    def keyset(self) -> "jwcrypto.jwk.JWKSet":
        """Load crypto keys from settings."""
        # TODO: better key caching can be considered in the future.
        #
        # This caches the server keys forever in process memory.
        #
        # If usage scales up, this can be changed to introduce a way to cache
        # the keys into persistent storage, to avoid one call to url_jwks per
        # process.
        #
        # Also, if the way this is deployed means that processes are very long
        # lived, more attention should be paid to deal with key rotation.
        #
        # See https://openid.net/specs/openid-connect-core-1_0.html#RotateSigKeys  # noqa: E501
        import jwcrypto.jwk

        key_response = self.oauth.get(self.provider.url_jwks)
        key_response.raise_for_status()
        return jwcrypto.jwk.JWKSet.from_json(key_response.text)

    def load_tokens(self) -> None:
        """Fetch and validate access_token and id_token from OIDC provider."""
        import jwcrypto.jwt
        from django.utils.crypto import constant_time_compare

        expected_state = self.request.session.pop(
            f"signon_state_{self.provider.name}", None
        )
        options = self.request.session.pop(
            f"signon_state_{self.provider.name}_options", None
        )

        if expected_state is None:
            raise OIDCValidationError(
                "Request state mismatch: expected state not found in session"
            )
        if options is None:
            raise OIDCValidationError(
                "Request state mismatch: options not found in session"
            )

        remote_state = self.request.GET.get("state")
        if remote_state is None:
            raise OIDCValidationError(
                "Request state mismatch: state not found in remote response"
            )

        if not constant_time_compare(remote_state, expected_state):
            raise OIDCValidationError(
                "Request state mismatch:"
                f" remote: {remote_state!r},"
                f" expected: {expected_state!r}"
            )

        tokens = self.oauth.fetch_token(
            self.url_token,
            authorization_response=self.request.build_absolute_uri(),
            client_secret=self.client_secret,
        )

        # See https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation  # noqa: E501

        id_token = tokens["id_token"]

        tok = jwcrypto.jwt.JWT(key=self.keyset, jwt=id_token)
        id_token_claims = json.loads(tok.claims)

        if not constant_time_compare(
            id_token_claims["iss"], self.provider.url_issuer
        ):
            raise OIDCValidationError(
                f"Issuer mismatch: remote: {id_token_claims['iss']!r},"
                f" expected: {self.provider.url_issuer!r}"
            )

        if not constant_time_compare(
            id_token_claims["aud"], self.provider.client_id
        ):
            raise OIDCValidationError(
                f"Audience mismatch: remote: {id_token_claims['aud']!r},"
                f" expected: {self.provider.client_id!r}"
            )

        # Note: the 'exp' claim is checked by default by JWT

        # TODO: potential extra checks to be implemented if needed:
        #
        # * assert tok["iat"] to be not too old
        #
        # OpenID Connect Core 1.0 states:
        #
        # > The iat Claim can be used to reject tokens that were issued too far
        # > away from the current time, limiting the amount of time that nonces
        # > need to be stored to prevent attacks. The acceptable range is
        # > Client specific.
        #
        # * honor tok["auth_time"]
        #
        # OpenID Connect Core 1.0 states:
        #
        # > If the auth_time Claim was requested, either through a specific
        # > request for this Claim or by using the max_age parameter, the
        # > Client SHOULD check the auth_time Claim value and request
        # > re-authentication if it determines too much time has elapsed since
        # > the last End-User authentication.

        self.tokens = tokens
        self.id_token_claims = id_token_claims
        self.options = options


class OIDCProvider(Provider):
    """OpenID Connect identity provider."""

    bound_class = BoundOIDCProvider

    def __init__(
        self,
        *args: Any,
        client_id: str,
        client_secret: str,
        url_issuer: str,
        url_authorize: str,
        url_token: str,
        url_userinfo: str,
        url_jwks: str,
        scope: str | Collection[str],
        restrict: Collection[str] = ("email-verified",),
        **kwargs: Any,
    ) -> None:
        """
        Define an OpenID Connect provider.

        :param client_id: client identifier configured in the authentication
            server
        :param client_secret: client_secret provided by the authentication
            server
        :param url_issuer: URL identifying the authentication server
        :param url_authorize: OIDC authorization endpoint
        :param url_token: OIDC token endpoint
        :param url_userinfo: OIDC userinfo endpoint
        :param url_jwks: OIDC jwks_uri to retrieve the authentication server
            signing keys
        :param restrict: list of restrictions for mapping remote accounts to
                         local ones

        See https://openid.net/specs/openid-connect-core-1_0.html for details
        """
        super().__init__(*args, **kwargs)
        self.client_id = client_id
        self.client_secret = client_secret
        self.url_issuer = url_issuer
        self.url_authorize = url_authorize
        self.url_token = url_token
        self.url_userinfo = url_userinfo
        self.url_jwks = url_jwks
        self.scope: list[str]
        if isinstance(scope, str):
            self.scope = [scope]
        else:
            self.scope = list(scope)
        self.restrict: Collection[str] = tuple(restrict)


class GitlabProvider(OIDCProvider):
    """Gitlab OIDC identity provider."""

    def __init__(self, *args: Any, url: str, **kwargs: Any) -> None:
        """
        Define a GitLab-based OIDC Connect provider.

        :param url: URL to the root of the GitLab server. It will be used to
            automatically generate all ``url_*`` arguments for OIDCProvider
        """
        kwargs.setdefault("scope", "openid")
        kwargs["url_issuer"] = url
        kwargs["url_authorize"] = f"{url}/oauth/authorize"
        kwargs["url_token"] = f"{url}/oauth/token"
        kwargs["url_userinfo"] = f"{url}/oauth/userinfo"
        kwargs["url_jwks"] = f"{url}/oauth/discovery/keys"
        super().__init__(*args, **kwargs)
