# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for account (log in, log out)."""

from django.test import TestCase
from django.urls import reverse

from debusine.web.views.tests.utils import ViewTestMixin


class LoginTests(ViewTestMixin, TestCase):
    """Test login functionality and template."""

    def test_view(self) -> None:
        """Generic test of the view."""
        next_path = "/workers"
        response = self.client.get(reverse("login") + f"?next={next_path}")
        tree = self.assertResponseHTML(response)

        # Correct title
        self.assertContains(response, "Log in")

        # Labels for the form
        self.assertContains(response, "Username:")
        self.assertContains(response, "Password:")

        # Has "next" form hidden field
        input_next = tree.xpath(
            ".//form//input[@name='next' and @type='hidden']"
        )

        self.assertEqual(len(input_next), 1)

        self.assertEqual(input_next[0].attrib["value"], next_path)

    def test_login_form_errors(self) -> None:
        """If the form has errors: template renders them."""
        response = self.client.post(
            reverse("login"), {"username": "", "password": ""}
        )

        self.assertContains(
            response,
            "Your username and password didn't match. Please try again.",
        )
        self.assertTrue(response.context["form"].errors)

    def test_view_login_without_next(self) -> None:
        """
        _base.html do not include ?next=login path.

        URL for login add is_login_view, _base.html use the variable
        to not add ?next={next_path}
        """
        response = self.client.get(reverse("login"))

        next_path = reverse("login")
        self.assertNotContains(response, f"?next={next_path}")


class LogoutTests(ViewTestMixin, TestCase):
    """Tests related to logout functionality."""

    def test_view_login_without_next(self) -> None:
        """
        _base.html do not include ?next=logout path.

        URL for logout add is_logout_view, _base.html use the variable
        to not add ?next={next_path}
        """
        response = self.client.post(reverse("logout"))

        next_path = reverse("logout")
        self.assertNotContains(response, f"?next={next_path}")

    def test_view_login_with_next(self) -> None:
        """_base.html do include ?next=workspaces_list path."""
        next_path = reverse("scopes:detail")
        response = self.client.get(next_path)
        self.assertContains(response, f"?next={next_path}")
