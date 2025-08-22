# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test custom path converters for package archives."""

from datetime import datetime, timezone

from django.http import HttpResponse
from django.test import override_settings
from django.urls import path, resolve, reverse

# Make sure the snapshot converter is registered.
import debusine.web.archives.converters  # noqa: F401
from debusine.test.django import TestCase

urlpatterns = [
    path(
        "<str:scope>/<str:workspace>/<snapshot:snapshot>/",
        HttpResponse,
        name="archive",
    )
]


@override_settings(ROOT_URLCONF="debusine.web.archives.tests.test_converters")
class SnapshotConverterTests(TestCase):
    """Test SnapshotConverter."""

    def test_round_trip(self) -> None:
        url = "/test-scope/test-workspace/20250501T000000Z/"
        match = resolve(url)
        self.assertEqual(match.url_name, "archive")
        self.assertEqual(
            match.kwargs,
            {
                "scope": "test-scope",
                "workspace": "test-workspace",
                "snapshot": datetime(2025, 5, 1, tzinfo=timezone.utc),
            },
        )
        self.assertEqual(reverse("archive", kwargs=match.kwargs), url)
