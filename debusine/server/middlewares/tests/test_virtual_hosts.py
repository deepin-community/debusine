# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for VirtualHostMiddleware."""

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from debusine.server.middlewares.virtual_hosts import VirtualHostMiddleware
from debusine.test.django import TestCase


@override_settings(ALLOWED_HOSTS=["*"])
class VirtualHostMiddlewareTests(TestCase):
    """Test VirtualHostMiddleware."""

    def test_debian_archive_fqdn(self) -> None:
        wrapped_view = VirtualHostMiddleware(lambda _: HttpResponse())
        request = RequestFactory().get(
            "/", HTTP_HOST=settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN
        )
        wrapped_view(request)
        self.assertEqual(
            getattr(request, "urlconf"), "debusine.web.archives.urls"
        )

    @override_settings(
        DEBUSINE_DEBIAN_ARCHIVE_FQDN=["deb.example.com", "deb.example.org"]
    )
    def test_any_of_debian_archive_fqdn(self) -> None:
        wrapped_view = VirtualHostMiddleware(lambda _: HttpResponse())
        request = RequestFactory().get("/", HTTP_HOST="deb.example.org")
        wrapped_view(request)
        self.assertEqual(
            getattr(request, "urlconf"), "debusine.web.archives.urls"
        )

    def test_other_fqdn(self) -> None:
        wrapped_view = VirtualHostMiddleware(lambda _: HttpResponse())
        request = RequestFactory().get("/", HTTP_HOST=settings.DEBUSINE_FQDN)
        wrapped_view(request)
        self.assertIsNone(getattr(request, "urlconf", None))

    @override_settings(
        DEBUSINE_DEBIAN_ARCHIVE_FQDN=["deb.example.com", "deb.example.org"]
    )
    def test_not_any_of_debian_archive_fqdn(self) -> None:
        wrapped_view = VirtualHostMiddleware(lambda _: HttpResponse())
        request = RequestFactory().get("/", HTTP_HOST="deb.example.net")
        wrapped_view(request)
        self.assertIsNone(getattr(request, "urlconf", None))
