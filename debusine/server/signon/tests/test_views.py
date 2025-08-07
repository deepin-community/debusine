# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for signon views."""


import contextlib
import datetime
import html
import re
from collections.abc import Generator, Sequence
from typing import Any, cast
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from debusine.db.models import Identity, User
from debusine.server.signon import providers
from debusine.server.signon.middleware import RequestSignonProtocol
from debusine.server.signon.providers import BoundOIDCProvider
from debusine.server.signon.signon import Signon
from debusine.server.signon.tests.test_signon import MockSession
from debusine.test.django import TestResponseType


@override_settings(
    SIGNON_PROVIDERS=[
        providers.GitlabProvider(
            name="salsa",
            label="Salsa",
            url="https://salsa.debian.org",
            client_id="clientid",
            client_secret="clientsecret",
        ),
    ],
)
class SalsaSignonViews(TestCase):
    """Test Signon views."""

    def _collect_urls(
        self, response: TestResponseType, match_re: str
    ) -> list[str]:
        """Collect external URLs in the response contents."""
        urls: list[str] = []
        re_url = re.compile(b'<a href="([^"]+)"')
        re_match = re.compile(match_re)
        for a in re_url.finditer(response.content):
            target = html.unescape(a.group(1).decode())
            if re_match.search(target):  # pragma: no cover
                # Excluding from coverage because this is currently always
                # true, but the check is here to be future-proof in case the
                # login page adds further links in other parts of the templates
                urls.append(target)

        return urls

    def _make_identity(
        self,
        user: User | None = None,
        issuer: str = "salsa",
        subject: str | None = None,
        **kwargs: Any,
    ) -> Identity:
        """Create a test identity."""
        if subject is None:
            subject = f"{issuer}@debian.org"
        return Identity.objects.create(
            user=user,
            issuer=issuer,
            subject=subject,
            claims={},
            **kwargs,
        )

    def _make_post_request(self, url: str) -> HttpRequest:
        """Create a request that can be manipulated before invoking a view."""
        factory = RequestFactory()
        request = factory.post(url)
        request.user = AnonymousUser()
        request.session = MockSession()
        cast(RequestSignonProtocol, request).signon = Signon(request)
        # Bypass CSRF checks for logout.  django.test.Client does this, but
        # we're bypassing that in order to manipulate the request, so for
        # now we have to set a private attribute.
        setattr(request, "_dont_enforce_csrf_checks", True)
        return request

    def test_login_links(self) -> None:
        """Login form shows link to authenticate with salsa."""
        response = self.client.get(reverse("login"))

        # Collect urls to the salsa provider
        external_urls = self._collect_urls(
            response, r"salsa\.debian\.org/oauth"
        )
        self.assertEqual(len(external_urls), 1)

        url = urlparse(external_urls[0])

        # Test the components of the authorize URL
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.netloc, "salsa.debian.org")
        self.assertEqual(url.path, "/oauth/authorize")

        query = parse_qs(url.query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(
            query["redirect_uri"],
            [
                "http://testserver"
                + reverse("signon:oidc_callback", kwargs={"name": "salsa"})
            ],
        )
        self.assertIn("client_id", query)
        self.assertIn("state", query)

    def test_login_links_when_signed_in(self) -> None:
        """Active identities don't show a login link."""
        user = get_user_model().objects.create_user("test")
        identity = self._make_identity(user=user)

        # Simulate a session with a logged in user
        url = reverse("logout")
        request = self._make_post_request(url)
        assert isinstance(request, RequestSignonProtocol)
        request.signon.activate_identity(identity)
        self.assertEqual(request.user, user)

        view = resolve(url).func
        response = view(request)
        response.render()

        # Collect urls to the salsa provider
        external_urls = self._collect_urls(
            response, r"salsa\.debian\.org/oauth"
        )
        self.assertEqual(len(external_urls), 0)

    def test_logout(self) -> None:
        """Logout also deactivates signon identities."""
        user = get_user_model().objects.create_user("test")
        identity = self._make_identity(user=user)

        # Simulate a session with a logged in user
        url = reverse("logout")
        request = self._make_post_request(url)
        assert isinstance(request, RequestSignonProtocol)
        request.signon.activate_identity(identity)
        self.assertEqual(request.user, user)

        view = resolve(url).func
        response = view(request)
        response.render()

        # Log out happened
        self.assertContains(response, "You have been logged out")

        # User is not authenticated anymore
        self.assertFalse(request.user.is_authenticated)

        # Identities have been deactivated
        self.assertIsNone(request.session.get("signon_identity_salsa"))
        assert isinstance(request, RequestSignonProtocol)
        self.assertEqual(request.signon.identities, {})

    @contextlib.contextmanager
    def setup_mock_auth(
        self,
        identity: Identity | None = None,
        options: Sequence[str] = (),
        **claims: Any,
    ) -> Generator[None, None, None]:
        """
        Mock remote authentication.

        :param identity: if provided it is used to fill default claims
        """
        if identity is not None:
            claims.setdefault("sub", identity.subject)

        def load_tokens(self: BoundOIDCProvider) -> None:
            self.id_token_claims = claims
            self.options = options

        with mock.patch(
            "debusine.server.signon.providers.BoundOIDCProvider.load_tokens",
            side_effect=load_tokens,
            autospec=True,
        ):
            yield

    def test_oidc_callback_create_identity(self) -> None:
        """The OIDC callback creates a missing identity."""
        self.assertEqual(Identity.objects.count(), 0)

        cb_url = reverse("signon:oidc_callback", kwargs={"name": "salsa"})

        start_time = timezone.now()

        with self.setup_mock_auth(sub="123", profile="profile_url"):
            response = self.client.get(cb_url)

        self.assertRedirects(response, reverse("homepage:homepage"))

        # Check that the Identity has been created and fully populated
        self.assertEqual(Identity.objects.count(), 1)

        identities = list(Identity.objects.all())
        self.assertIsNone(identities[0].user)
        self.assertEqual(identities[0].issuer, "salsa")
        self.assertEqual(identities[0].subject, "123")
        self.assertGreaterEqual(identities[0].last_used, start_time)
        self.assertEqual(
            identities[0].claims, {"sub": "123", "profile": "profile_url"}
        )

        # Check that the Identity is attached to the session
        self.assertEqual(
            self.client.session["signon_identity_salsa"], identities[0].pk
        )

        # The identity is activated
        assert isinstance(response.wsgi_request, RequestSignonProtocol)
        self.assertEqual(
            response.wsgi_request.signon.identities, {"salsa": identities[0]}
        )

        # The user is not authenticated, because the identity is not bound
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_oidc_callback_login(self) -> None:
        """The OIDC callback logs in an existing bound identity."""
        self.assertEqual(Identity.objects.count(), 0)

        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident = self._make_identity(
            user=user,
            subject="123",
            last_used=timezone.now() - datetime.timedelta(days=1),
        )

        cb_url = reverse("signon:oidc_callback", kwargs={"name": ident.issuer})

        start_time = timezone.now()
        with self.setup_mock_auth(identity=ident, profile="profile_url"):
            response = self.client.get(cb_url)

        self.assertRedirects(response, reverse("homepage:homepage"))

        # Check that no new Identity has been created
        self.assertEqual(Identity.objects.count(), 1)

        ident.refresh_from_db()
        # last_used has been updated
        self.assertGreaterEqual(ident.last_used, start_time)
        # Claims in ident have been filled
        self.assertEqual(ident.claims, {"profile": "profile_url", "sub": "123"})

        # Check that the Identity is attached to the session
        self.assertEqual(self.client.session["signon_identity_salsa"], ident.pk)

        # The identity is activated
        assert isinstance(response.wsgi_request, RequestSignonProtocol)
        self.assertEqual(
            response.wsgi_request.signon.identities, {"salsa": ident}
        )

        # The user has been correctly authenticated
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user, user)

    def test_oidc_callback_login_twice(self) -> None:
        """The OIDC callback logs in an existing bound identity."""
        self.assertEqual(Identity.objects.count(), 0)

        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        last_used = timezone.now() - datetime.timedelta(days=1)
        ident = self._make_identity(
            user=user,
            subject="123",
            last_used=last_used,
        )

        cb_url = reverse("signon:oidc_callback", kwargs={"name": ident.issuer})

        self.client.force_login(user)

        start_time = timezone.now()
        with self.setup_mock_auth(identity=ident, profile="profile_url"):
            response = self.client.get(cb_url)

        self.assertEqual(response.status_code, 403)

        # Check that no new Identity has been created
        self.assertEqual(Identity.objects.count(), 1)

        ident.refresh_from_db()
        # last_used has been updated
        self.assertGreaterEqual(ident.last_used, start_time)
        # Claims in ident have been filled
        self.assertEqual(ident.claims, {"profile": "profile_url", "sub": "123"})

        # Check that the Identity is attached to the session
        self.assertNotIn("signon_identity_salsa", self.client.session)

        # The identity is activated
        assert isinstance(response.wsgi_request, RequestSignonProtocol)
        self.assertEqual(response.wsgi_request.signon.identities, {})

    def test_oidc_callback_bind(self) -> None:
        """The OIDC callback performs bind if requested."""
        self.assertEqual(Identity.objects.count(), 0)

        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident = self._make_identity(
            subject="123",
            last_used=timezone.now() - datetime.timedelta(days=1),
        )

        cb_url = reverse("signon:oidc_callback", kwargs={"name": ident.issuer})

        self.client.force_login(user)

        start_time = timezone.now()
        with self.setup_mock_auth(
            identity=ident, options=["bind"], profile="profile_url"
        ):
            response = self.client.get(cb_url)

        self.assertRedirects(response, reverse("homepage:homepage"))

        # Check that no new Identity has been created
        self.assertEqual(Identity.objects.count(), 1)

        ident.refresh_from_db()
        # last_used has been updated
        self.assertGreaterEqual(ident.last_used, start_time)
        # Claims in ident have been filled
        self.assertEqual(ident.claims, {"profile": "profile_url", "sub": "123"})
        # Identity has been bound
        self.assertEqual(ident.user, user)
        # User is still logged in
        self.assertEqual(response.wsgi_request.user, user)

        # Check that the Identity is attached to the session
        assert isinstance(response.wsgi_request, RequestSignonProtocol)
        self.assertEqual(
            response.wsgi_request.signon.identities, {"salsa": ident}
        )

    def test_oidc_callback_wrong_provider(self) -> None:
        """The OIDC callback with a wrong provider."""
        self.assertEqual(Identity.objects.count(), 0)

        cb_url = reverse("signon:oidc_callback", kwargs={"name": "wrong"})

        response = self.client.get(cb_url)
        self.assertEqual(response.status_code, 404)

        self.assertEqual(Identity.objects.count(), 0)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_oidc_callback_redirect_url(self) -> None:
        """Test redirect after successful OIDC callback."""
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident = self._make_identity(
            user=user,
            subject="123",
            last_used=timezone.now() - datetime.timedelta(days=1),
        )

        cb_url = reverse("signon:oidc_callback", kwargs={"name": ident.issuer})

        # Default redirect
        client = Client()
        with self.setup_mock_auth(identity=ident, profile="profile_url"):
            response = client.get(cb_url)
        self.assertRedirects(response, reverse("homepage:homepage"))

        # Redirect comes from SIGNON_DEFAULT_REDIRECT
        with override_settings(SIGNON_DEFAULT_REDIRECT="https://example.org"):
            client = Client()
            with self.setup_mock_auth(identity=ident, profile="profile_url"):
                response = client.get(cb_url)
            self.assertRedirects(
                response, "https://example.org", fetch_redirect_response=False
            )

        # Redirect from the session
        client = Client()
        session = client.session
        session["sso_callback_next_url"] = "https://session.example.org"
        session.save()
        with self.setup_mock_auth(identity=ident, profile="profile_url"):
            response = client.get(cb_url)
        self.assertRedirects(
            response,
            "https://session.example.org",
            fetch_redirect_response=False,
        )
        self.assertNotIn("sso_callback_next_url", client.session)

    def test_bindidentity(self) -> None:
        """Bind identity redirects correctly."""
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        self.client.force_login(user)
        session = self.client.session
        response = self.client.get(
            reverse("signon:bind_identity", kwargs={"name": "salsa"})
        )
        self.assertEqual(response.status_code, 302)
        url = response.headers["Location"]
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "salsa.debian.org")
        self.assertEqual(parsed.path, "/oauth/authorize")
        args = parse_qs(parsed.query)
        state = args["state"][0]
        self.assertEqual(session["signon_state_salsa"], state)
        self.assertEqual(session["signon_state_salsa_options"], ["bind"])

    def test_bindidentity_no_user(self) -> None:
        """Bind identity fails if not logged in."""
        response = self.client.get(
            reverse("signon:bind_identity", kwargs={"name": "salsa"})
        )
        self.assertEqual(response.status_code, 403)

    def test_bindidentity_wrong_provider(self) -> None:
        """Bind identity fails for a nonexisting provider."""
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("signon:bind_identity", kwargs={"name": "wrong"})
        )
        self.assertEqual(response.status_code, 403)

    # TODO: when we will support more than one authentication provider, we can
    # implement a profile view to link another external provider to the current
    # user.
    #
    # For now we only support a single authentication provider, and we have no
    # urgent need of this

    # def test_bind_view(self) -> None:
    #     """Test user profile view to bind external providers."""
    #     raise NotImplementedError()

    # def test_oidc_callback_bind_intent(self) -> None:
    #     """Test callback when authenticated with bind intent."""
    #     raise NotImplementedError()

    # def test_oidc_callback_no_bind_intent(self) -> None:
    #     """Test callback when authenticated with no bind intent."""
    #     raise NotImplementedError()
