# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test of the signon authentication middleware for external providers."""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured, MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from debusine.server.signon import providers
from debusine.server.signon.middleware import (
    RequestSignonProtocol,
    SignonMiddleware,
)
from debusine.server.signon.signon import Signon

PROVIDERS_CONFIG = [
    providers.Provider(
        name="debsso",
        label="sso.debian.org",
    ),
    providers.Provider(name="salsa", label="Salsa"),
]


def mock_get_response(request: HttpRequest) -> HttpResponse:  # noqa: U100
    """Mock version of get_response to use for testing middlewares."""
    return HttpResponse()


class SignonSubclass(Signon):
    """Subclass of Signon to use in tests."""


class TestMiddleware(TestCase):
    """Test SignonMiddleware."""

    def setUp(self) -> None:
        """Provide a mock unauthenticated request for tests."""
        super().setUp()
        self.factory = RequestFactory()
        self.request = self.factory.get("/")

    @override_settings(SIGNON_PROVIDERS=PROVIDERS_CONFIG)
    def test_no_authentication_middleware(self) -> None:
        """Enforce that AuthenticationMiddleware is required."""
        mw = SignonMiddleware(get_response=mock_get_response)
        with self.assertRaises(ImproperlyConfigured):
            mw(self.request)

    @override_settings(SIGNON_PROVIDERS=[])
    def test_no_providers(self) -> None:
        """Skip middleware if there are no providers."""
        with self.assertRaises(MiddlewareNotUsed):
            SignonMiddleware(get_response=mock_get_response)

    @override_settings(SIGNON_PROVIDERS=PROVIDERS_CONFIG)
    def test_all_fine(self) -> None:
        """Test the case where all requirements are met."""
        self.request.user = AnonymousUser()
        assert not isinstance(self.request, RequestSignonProtocol)

        mw = SignonMiddleware(get_response=mock_get_response)
        mw(self.request)

        assert isinstance(self.request, RequestSignonProtocol)
        self.assertIsInstance(self.request.signon, Signon)

    @override_settings(
        SIGNON_PROVIDERS=PROVIDERS_CONFIG,
        SIGNON_CLASS="debusine.does.not.Exist",
    )
    def test_signon_class_not_found(self) -> None:
        """Test setting SIGNON_CLASS to an invalid path."""
        with self.assertRaisesRegex(
            ImportError, r"No module named 'debusine.does'"
        ):
            SignonMiddleware(get_response=mock_get_response)

    @override_settings(
        SIGNON_PROVIDERS=PROVIDERS_CONFIG,
        SIGNON_CLASS="debusine.db.models.Scope",
    )
    def test_signon_class_invalid(self) -> None:
        """Test setting SIGNON_CLASS to a non-Signon class."""
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"debusine.db.models.Scope is not a subclass of Signon",
        ):
            SignonMiddleware(get_response=mock_get_response)

    @override_settings(
        SIGNON_PROVIDERS=PROVIDERS_CONFIG,
        SIGNON_CLASS=f"{SignonSubclass.__module__}.SignonSubclass",
    )
    def test_signon_class(self) -> None:
        """Test setting SIGNON_CLASS to a valid subclass."""
        mw = SignonMiddleware(get_response=mock_get_response)
        self.assertIs(mw.signon_class, SignonSubclass)
