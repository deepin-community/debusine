# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the view tests utilities."""
import lxml
import lxml.objectify
from django.urls import reverse

from debusine.test.django import TestCase
from debusine.web.views.tests.utils import ViewTestMixin


class TestWebUtils(ViewTestMixin, TestCase):
    """Tests for view test utility code."""

    def test_workspace_list_not_found(self) -> None:
        """Test that workspace_list_table_rows errors when not found."""
        default_workspace = self.playground.get_default_workspace()
        default_workspace.public = False
        default_workspace.save()

        response = self.client.get(reverse("homepage:homepage"))
        tree = self.assertResponseHTML(response)
        with self.assertRaisesRegex(
            AssertionError, r"page has no workspace list table"
        ):
            self.workspace_list_table_rows(tree)

    def test_assert_html_contents_equivalent(self) -> None:
        """Test that assertHTMLContentsEquivalent works as expected."""
        node = lxml.objectify.fromstring(
            '<span> some  na\n\n (<abbr title="Ext">E</abbr>)\n\n</span>\n     '
        )

        self.assertHTMLContentsEquivalent(
            node, '<span>some    na (<abbr title="Ext">E</abbr>)</span>'
        )

        with self.assertRaises(AssertionError):
            self.assertHTMLContentsEquivalent(node, "<span>Sample</span>")

        with self.assertRaises(AssertionError):
            self.assertHTMLContentsEquivalent(
                node, '<span>so me    na (<abbr title="Ext">E</abbr>)</span>'
            )

    def test_assert_enforces_permission_target_name(self) -> None:
        """Test assertEnforcesPermission target_name resolution."""
        artifact, _ = self.playground.create_artifact()

        self.assertEnforcesPermission(
            artifact.can_display,
            artifact.get_absolute_url(),
            "get_context_data",
        )
        self.assertEnforcesPermission(
            artifact.can_display,
            artifact.get_absolute_url(),
            "debusine.web.views.artifacts.ArtifactDetailView.get_context_data",
        )
