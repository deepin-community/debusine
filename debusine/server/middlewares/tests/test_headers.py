# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for HeadersMiddleware."""

from django.http import HttpResponse
from django.test import RequestFactory
from django.utils.cache import has_vary_header

from debusine.server.middlewares.headers import HeadersMiddleware
from debusine.test.django import TestCase


class HeadersMiddlewareTests(TestCase):
    """Test HeadersMiddleware."""

    def test_unauthenticated(self) -> None:
        wrapped_view = HeadersMiddleware(lambda _: HttpResponse())
        response = wrapped_view(RequestFactory().get("/"))
        self.assertTrue(has_vary_header(response, "Cookie"))
        self.assertTrue(has_vary_header(response, "Token"))

    def test_authenticated_with_cookie(self) -> None:
        wrapped_view = HeadersMiddleware(lambda _: HttpResponse())
        response = wrapped_view(
            RequestFactory().get("/", headers={"Cookie": "tasty"})
        )
        self.assertTrue(has_vary_header(response, "Cookie"))
        self.assertTrue(has_vary_header(response, "Token"))

    def test_authenticated_with_token(self) -> None:
        wrapped_view = HeadersMiddleware(lambda _: HttpResponse())
        response = wrapped_view(
            RequestFactory().get("/", headers={"Token": "valid"})
        )
        self.assertTrue(has_vary_header(response, "Cookie"))
        self.assertTrue(has_vary_header(response, "Token"))
