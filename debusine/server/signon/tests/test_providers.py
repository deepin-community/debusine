# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for signon provider backends."""

import contextlib
import io
import json
import re
from collections.abc import Generator, Sequence
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest import mock

import django.http
import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings
from jwcrypto import jwk, jws, jwt

from debusine.db.models import Identity
from debusine.server.signon import providers
from debusine.server.signon.tests.test_signon import MockSession

SALSA_PROVIDER = providers.GitlabProvider(
    name="salsa",
    label="Salsa",
    icon="signon/gitlabian.svg",
    client_id="123client_id",
    client_secret="123client_secret",
    url="https://salsa.debian.org",
    scope=("openid", "profile", "email"),
)

GITLAB_PROVIDER = providers.GitlabProvider(
    name="gitlab",
    label="GitLab",
    icon="signon/gitlab.svg",
    client_id="123client_id",
    client_secret="123client_secret",
    url="https://gitlab.com",
)


class TestProvider(TestCase):
    """Test the base Provider class."""

    def test_defaults(self) -> None:
        """Test instantiating Provider with default arguments."""
        p = providers.Provider("name", "label")
        self.assertEqual(p.name, "name")
        self.assertEqual(p.label, "label")
        self.assertIsNone(p.icon)
        self.assertEqual(p.options, {})

    def test_all_set(self) -> None:
        """Test instantiating Provider."""
        p = providers.Provider(
            "name", "label", icon="icon", options={"answer": 42}
        )
        self.assertEqual(p.name, "name")
        self.assertEqual(p.label, "label")
        self.assertEqual(p.icon, "icon")
        self.assertEqual(p.options, {"answer": 42})


class SignonProviders(TestCase):
    """Test signon provider definitions."""

    remote_key: ClassVar[jwk.JWK]
    remote_keyset: ClassVar[jwk.JWKSet]

    @classmethod
    def setUpClass(cls) -> None:
        """Create a reusable remote keyset only once for all the tests."""
        super().setUpClass()
        # Keyset used for mocking encrypted communication with the OIDC
        # provider.
        #
        # This would normally use an asymmetric key, but we use a symmetric one
        # to avoid slowing down tests.
        cls.remote_key = jwk.JWK.generate(kty='oct', size=256)
        cls.remote_keyset = jwk.JWKSet.from_json(
            json.dumps(
                {
                    "keys": [cls.remote_key.export_symmetric(as_dict=True)],
                }
            )
        )

    @contextlib.contextmanager
    def mock_load_keyset(
        self, provider: providers.OIDCProvider
    ) -> Generator[None, None, None]:
        """Do the right mocking setup to prepare loading a remote keyset."""
        # Mock answer with the keyset
        response = requests.Response()
        response.url = provider.url_jwks
        response.status_code = 200
        # Mock fetching the remote keyset
        response.raw = io.BytesIO(self.remote_keyset.export().encode())

        # On first access it fetches remote keys
        with mock.patch(
            "requests_oauthlib.OAuth2Session.get", return_value=response
        ):
            yield

    @contextlib.contextmanager
    def mock_fetch_token(
        self, response_token: str, time: int = 123450
    ) -> Generator[None, None, None]:
        """Mock the remote call in OAuth2Session.fetch_token."""
        with mock.patch(
            "requests_oauthlib.OAuth2Session.fetch_token",
            return_value={
                "id_token": response_token,
                "access_token": "accesstoken",
            },
        ):
            with mock.patch(
                "time.time",
                return_value=time,
            ):
                yield

    @contextlib.contextmanager
    def mock_load_tokens(
        self,
        provider: providers.OIDCProvider,
        response_token: str,
        time: int = 123450,
    ) -> Generator[None, None, None]:
        """Do the mocking needed to call load_tokens."""
        with self.mock_load_keyset(provider):
            with self.mock_fetch_token(response_token, time):
                yield

    def _make_request(
        self,
        session_state: str | None = "teststate",
        remote_state: str | None = "teststate",
        session_options: Sequence[str] | None = (),
    ) -> django.http.HttpRequest:
        """Create a test request."""
        # Mock request
        request_factory = RequestFactory()
        data = {}
        if remote_state is not None:
            data["state"] = remote_state
        request = request_factory.get("/callback", data=data)
        request.session = MockSession()
        if session_state is not None:
            request.session["signon_state_salsa"] = session_state
        if session_options is not None:
            request.session["signon_state_salsa_options"] = session_options
        return request

    def _make_identity(
        self,
        issuer: str = "salsa",
        claims: dict[str, Any] | None = None,
    ) -> Identity:
        """Create a test identity."""
        subject = f"{issuer}@debian.org"
        if claims is None:
            claims = {}
        return Identity.objects.create(
            issuer=issuer,
            subject=subject,
            claims=claims,
        )

    def _make_provider(
        self, name: str = "salsa", restrict: Sequence[str] = ()
    ) -> providers.GitlabProvider:
        """Create a provider for testing restrictions."""
        return providers.GitlabProvider(
            name=name,
            label=name.capitalize(),
            client_id="123client_id",
            client_secret="123client_secret",
            url=f"https://{name}.debian.org",
            scope=("openid", "profile", "email"),
            restrict=restrict,
        )

    def assertSignonStateRemoved(
        self, request: django.http.HttpRequest
    ) -> None:
        """Ensure that the signon state has been removed from session."""
        self.assertNotIn("signon_state_salsa", request.session)
        self.assertNotIn("signon_state_salsa_options", request.session)

    def test_get_setting_undefined(self) -> None:
        """Lookup with no SIGNON_PROVIDERS raises ImproperlyConfigured."""
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            "signon provider salsa requested, but SIGNON_PROVIDERS is not "
            "defined in settings",
        ):
            providers.get("salsa")

    @override_settings(SIGNON_PROVIDERS=[])
    def test_get_empty(self) -> None:
        """Lookup in an empty provider list raises ImproperlyConfigured."""
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            "signon provider salsa requested, but not found in "
            "SIGNON_PROVIDERS setting",
        ):
            providers.get("salsa")

    @override_settings(SIGNON_PROVIDERS=[SimpleNamespace(name="salsa")])
    def test_get_wrong_type(self) -> None:
        """A provider of the wrong type raises ImproperlyConfigured."""
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            "signon provider salsa requested, but its entry in "
            "SIGNON_PROVIDERS setting is not a Provider",
        ):
            providers.get("salsa")

    @override_settings(SIGNON_PROVIDERS=[SALSA_PROVIDER, GITLAB_PROVIDER])
    def test_get(self) -> None:
        """Test GitLab provider parameter construction."""
        salsa = providers.get("salsa")
        self.assertIsNotNone(salsa)

        gitlab = providers.get("gitlab")
        self.assertIsNotNone(gitlab)

        # Getting a provider twice returns the same object
        self.assertIs(providers.get("salsa"), salsa)
        self.assertIs(providers.get("gitlab"), gitlab)

        assert isinstance(salsa, providers.GitlabProvider)
        self.assertEqual(salsa.name, "salsa")
        self.assertEqual(salsa.label, "Salsa")
        self.assertEqual(salsa.icon, "signon/gitlabian.svg")
        self.assertEqual(salsa.scope, ["openid", "profile", "email"])
        self.assertEqual(salsa.client_id, "123client_id")
        self.assertEqual(salsa.client_secret, "123client_secret")
        self.assertEqual(salsa.url_issuer, "https://salsa.debian.org")
        self.assertEqual(
            salsa.url_authorize, "https://salsa.debian.org/oauth/authorize"
        )
        self.assertEqual(
            salsa.url_token, "https://salsa.debian.org/oauth/token"
        )
        self.assertEqual(
            salsa.url_userinfo, "https://salsa.debian.org/oauth/userinfo"
        )
        self.assertEqual(
            salsa.url_jwks, "https://salsa.debian.org/oauth/discovery/keys"
        )

        assert isinstance(gitlab, providers.GitlabProvider)
        self.assertEqual(gitlab.name, "gitlab")
        self.assertEqual(gitlab.label, "GitLab")
        self.assertEqual(gitlab.icon, "signon/gitlab.svg")
        self.assertEqual(gitlab.scope, ["openid"])
        self.assertEqual(gitlab.client_id, "123client_id")
        self.assertEqual(gitlab.client_secret, "123client_secret")
        self.assertEqual(gitlab.url_issuer, "https://gitlab.com")
        self.assertEqual(
            gitlab.url_authorize, "https://gitlab.com/oauth/authorize"
        )
        self.assertEqual(gitlab.url_token, "https://gitlab.com/oauth/token")
        self.assertEqual(
            gitlab.url_userinfo, "https://gitlab.com/oauth/userinfo"
        )
        self.assertEqual(
            gitlab.url_jwks, "https://gitlab.com/oauth/discovery/keys"
        )

    def test_bind(self) -> None:
        """Provider.bind binds to the request and proxies correctly."""
        request = self._make_request()
        provider = providers.GitlabProvider(
            name="salsa",
            label="Salsa",
            client_id="123client_id",
            client_secret="123client_secret",
            url="https://salsa.debian.org",
            scope=("openid", "profile", "email"),
        )
        bound = provider.bind(request)
        self.assertIs(bound.provider, provider)
        self.assertIs(bound.request, request)
        self.assertEqual(bound.name, provider.name)
        self.assertEqual(bound.label, provider.label)
        self.assertEqual(bound.client_id, provider.client_id)
        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)

    def test_oidc_load_keys(self) -> None:
        """OIDC keys loads and parses the keyset."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)

        # On first access it fetches remote keys
        with self.mock_load_keyset(provider):
            self.assertIsNotNone(bound.keyset)

        # Subsequent accesses have the results cached
        with mock.patch("requests_oauthlib.OAuth2Session.get") as session_get:
            self.assertIsNotNone(bound.keyset)
        session_get.assert_not_called()

    def _make_response_token(
        self,
        provider: providers.OIDCProvider,
        iss: str | None = None,
        aud: str | None = None,
        exp: int = 123456,
        key: jwk.JWK | None = None,
    ) -> str:
        """Generate a response token."""
        if iss is None:
            iss = provider.url_issuer
        if aud is None:
            aud = provider.client_id

        # Encrypted response payload from the OIDC authentication provider
        response_payload = jwt.JWT(
            header={"alg": "HS256"},
            claims={
                "iss": iss,
                "aud": aud,
                "sub": "1234",
                "profile": "https://salsa.debian.org/username",
                "exp": exp,
            },
        )
        if key is None:
            key = self.remote_key
        response_payload.make_signed_token(key)
        response_token = response_payload.serialize()
        assert isinstance(response_token, str)
        return response_token

    def test_oidc_load_tokens(self) -> None:
        """Tokens are loaded from the auth provider and decoded."""
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.mock_load_tokens(provider, response_token):
            bound.load_tokens()

        self.assertIsNotNone(bound.tokens)
        self.assertIsNotNone(bound.id_token_claims)
        self.assertEqual(bound.options, ())
        self.assertSignonStateRemoved(request)

    def test_oidc_load_tokens_options(self) -> None:
        """Tokens are loaded from the auth provider and decoded."""
        provider = SALSA_PROVIDER
        request = self._make_request(session_options=["bind"])
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.mock_load_tokens(provider, response_token):
            bound.load_tokens()

        self.assertIsNotNone(bound.tokens)
        self.assertIsNotNone(bound.id_token_claims)
        self.assertEqual(bound.options, ["bind"])
        self.assertSignonStateRemoved(request)

    def test_oidc_load_state_not_in_session(self) -> None:
        """Callback state argument must match session."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request(session_state=None)
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.assertRaisesRegex(
            providers.OIDCValidationError,
            r"expected state not found in session",
        ):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_options_not_in_session(self) -> None:
        """Callback options argument must be in session."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request(session_options=None)
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.assertRaisesRegex(
            providers.OIDCValidationError,
            "options not found in session",
        ):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_state_not_in_get(self) -> None:
        """Callback state argument must match session."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request(remote_state=None)
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.assertRaisesRegex(
            providers.OIDCValidationError,
            r"state not found in remote response",
        ):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_wrong_state(self) -> None:
        """Callback state argument must match session."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request(session_state="wrong")
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.assertRaisesRegex(
            providers.OIDCValidationError, r"Request state mismatch"
        ):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_expired_token(self) -> None:
        """An expired token is rejected."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)

        with self.assertRaises(jwt.JWTExpired):
            with self.mock_load_tokens(provider, response_token, time=123556):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_tokens_wrong_keys(self) -> None:
        """Remote tokens need to be signed with the right keys."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)

        wrong_key = jwk.JWK.generate(kty='oct', size=256)
        response_token = self._make_response_token(provider, key=wrong_key)

        with self.assertRaises(jwt.JWTMissingKey):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_tokens_wrong_issuer(self) -> None:
        """Remote tokens need to have the right issuer claim."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)
        response_token = self._make_response_token(provider, iss="WRONG")

        with self.assertRaisesRegex(
            providers.OIDCValidationError, r"Issuer mismatch"
        ):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_tokens_wrong_audience(self) -> None:
        """Remote tokens need to have the right audience claim."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)
        response_token = self._make_response_token(provider, aud="WRONG")

        with self.assertRaisesRegex(
            providers.OIDCValidationError, r"Audience mismatch"
        ):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def test_oidc_load_tokens_corrupted(self) -> None:
        """A remote tokens that is corrupted is detected."""
        # Mock provider
        provider = SALSA_PROVIDER
        request = self._make_request()
        bound = provider.bind(request)
        response_token = self._make_response_token(provider)
        response_token = response_token[:4] + '0' + response_token[5:]

        with self.assertRaises(jws.InvalidJWSObject):
            with self.mock_load_tokens(provider, response_token):
                bound.load_tokens()

        self.assertIsNone(bound.tokens)
        self.assertIsNone(bound.id_token_claims)
        self.assertSignonStateRemoved(request)

    def assertRestrictValidates(
        self, identity: Identity, restrict: Sequence[str]
    ) -> None:
        """Identity passes this restrict rule."""
        provider = self._make_provider(name=identity.issuer)
        provider.restrict = restrict
        self.assertEqual(provider.validate_claims(identity), [])

    def assertRestrictDoesNotValidate(
        self,
        identity: Identity,
        restrict: Sequence[str],
        *regexps: str,
    ) -> None:
        """Identity does not pass this restrict rule."""
        provider = self._make_provider(name=identity.issuer)
        provider.restrict = restrict
        errors = provider.validate_claims(identity)
        self.assertNotEqual(errors, [])
        for regexp in regexps:
            for idx, error in enumerate(errors):
                if re.search(regexp, error):  # pragma: no cover
                    errors.pop(idx)
                    break
            else:
                self.fail(
                    f"error list {errors!r} did not match {regexp!r}"
                )  # pragma: no cover
        if errors:
            self.fail(f"unexpected errors: {errors!r}")  # pragma: no cover

    def test_validate_email_verified(self) -> None:
        """email-verified validates as intended."""
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
            }
        )
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["email-verified"], "does not have a verified email"
        )

        ident.claims["email_verified"] = True
        self.assertRestrictValidates(ident, [])
        self.assertRestrictValidates(ident, ["email-verified"])

    def test_validate_group(self) -> None:
        """group:* validates as intended."""
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "groups_direct": ["debian"],
            }
        )
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["group:admin"], "not in group admin"
        )
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictDoesNotValidate(
            ident, ["group:debian", "group:admin"], "not in group admin"
        )

        ident.claims["groups_direct"] = []
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["group:admin"], "not in group admin"
        )
        self.assertRestrictDoesNotValidate(
            ident,
            ["group:debian", "group:admin"],
            "not in group debian",
            "not in group admin",
        )

        ident.claims["groups_direct"] = ["debian", "admin"]
        self.assertRestrictValidates(ident, [])
        self.assertRestrictValidates(ident, ["group:admin"])
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictValidates(ident, ["group:debian", "group:admin"])

    def test_validate_combine(self) -> None:
        """group:* and email-verified can be used together."""
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
                "groups_direct": ["debian"],
            }
        )
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["email-verified"], "does not have a verified email"
        )
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictDoesNotValidate(
            ident,
            ["email-verified", "group:debian"],
            "does not have a verified email",
        )

        ident.claims["email_verified"] = True
        self.assertRestrictValidates(ident, [])
        self.assertRestrictValidates(ident, ["email-verified"])
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictValidates(ident, ["email-verified", "group:debian"])
        self.assertRestrictDoesNotValidate(
            ident, ["email-verified", "group:admin"], "not in group admin"
        )

    def test_validate_invalid_expression(self) -> None:
        """An invalid restrict value raises ImproperlyConfigured."""
        provider = self._make_provider(restrict=["invalid"])
        identity = self._make_identity()
        with self.assertRaises(ImproperlyConfigured):
            provider.validate_claims(identity)
