# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the open metrics view."""
from django.test import SimpleTestCase
from django.urls import reverse

from debusine.server.views.open_metrics import extract_media_type
from debusine.test.django import TestCase


class HelperTests(SimpleTestCase):
    """Tests for simple helper methods."""

    def test_extract_media_type(self) -> None:
        self.assertEqual(
            extract_media_type("text/plain; version=1; charset=utf-8"),
            "text/plain",
        )


class ServiceStatusViewTests(TestCase):
    """Tests for ServiceStatusView."""

    def test_output_headers_prometheus(self) -> None:
        from prometheus_client import CONTENT_TYPE_LATEST

        response = self.client.get(reverse("api:open-metrics"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], CONTENT_TYPE_LATEST)

    def test_output_headers_openmetrics(self) -> None:
        from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST

        response = self.client.get(
            reverse("api:open-metrics"),
            headers={"Accept": "application/openmetrics-text"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], CONTENT_TYPE_LATEST)
