# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for scope handling in views."""

import unittest

from django.conf import settings
from django.urls import reverse

from debusine.db.context import context
from debusine.server.scopes import urlconf_scope


class URLScopeTests(unittest.TestCase):
    """Tests scope handling in views."""

    def setUp(self) -> None:
        """Reset context at the beginning of tests."""
        super().setUp()
        context.reset()

    def assertReverses(self, scope: str) -> None:
        """Check that URLs are reversed for the right scope."""
        # Non-scoped URL
        self.assertEqual(reverse("homepage:homepage"), "/")
        # Scoped URL
        self.assertEqual(
            reverse("workspaces:detail", kwargs={"wname": "test"}),
            f"/{scope}/test/",
        )
        # API URL
        self.assertEqual(reverse('api:register'), "/api/1.0/worker/register/")

    def test_url_reverse_default(self) -> None:
        """Test URL reverse in the default scope."""
        self.assertIsNone(context.scope)
        self.assertReverses(settings.DEBUSINE_DEFAULT_SCOPE)

    def test_reverse_in_set_scope(self) -> None:
        """Test URL reverse after setting a scope."""
        with urlconf_scope("test"):
            self.assertReverses("test")

    def test_reverse_in_multiple_scopes(self) -> None:
        """Test URL reverse after setting a scope."""
        with urlconf_scope("test"):
            self.assertReverses("test")
        with urlconf_scope("other_test"):
            self.assertReverses("other_test")
        self.assertReverses(settings.DEBUSINE_DEFAULT_SCOPE)
