# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Test Django REST Framework authentication infrastructure for Debusine."""

from typing import ClassVar

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from rest_framework.request import Request

from debusine.db.models import Token, User
from debusine.server.views.auth import DebusineTokenAuthentication
from debusine.test.django import TestCase


class DebusineTokenAuthenticationTests(TestCase):
    """Test DebusineTokenAuthentication."""

    token: ClassVar[Token]

    def authenticate(
        self, token: Token | str | None = None
    ) -> tuple[User | AnonymousUser | None, Token | None] | None:
        """Run the authentication method."""
        django_request = RequestFactory().get("/")
        setattr(django_request, "_debusine_token", token)
        request = Request(django_request)
        return DebusineTokenAuthentication().authenticate(request)

    def test_bare_token(self) -> None:
        """Check authenticating a bare token."""
        token = self.playground.create_bare_token()
        self.assertEqual(self.authenticate(token), (AnonymousUser(), token))

    def test_worker_token(self) -> None:
        """Check authenticating a worker token."""
        token = self.playground.create_worker_token()
        self.assertEqual(self.authenticate(token), (AnonymousUser(), token))

    def test_user_token(self) -> None:
        """Check authenticating a user token."""
        token = self.playground.create_user_token()
        self.assertEqual(
            self.authenticate(token),
            (self.playground.get_default_user(), token),
        )

    def test_both_token(self) -> None:
        """Check authenticating a user and worker token."""
        token = self.playground.create_worker_token()
        token.user = self.playground.get_default_user()
        token.save()
        self.assertEqual(self.authenticate(token), (token.user, token))

    def test_no_token(self) -> None:
        """Request without a token skip authentication."""
        self.assertIsNone(self.authenticate())
