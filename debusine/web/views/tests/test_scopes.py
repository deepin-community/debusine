# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the scope views."""
from datetime import timedelta
from typing import ClassVar

import lxml
from django.urls import reverse
from rest_framework import status

from debusine.db.models import Scope, Workspace
from debusine.db.playground import scenarios
from debusine.server.scopes import urlconf_scope
from debusine.test.django import TestCase
from debusine.web.views.tests.utils import ViewTestMixin


class ScopeDetailViewTestsEmptyDB(ViewTestMixin, TestCase):
    """Tests for ScopeDetailView with no workspaces."""

    def test_no_workspaces(self) -> None:
        """No workspaces: 'No workspaces' in response."""
        default_workspace = self.playground.get_default_workspace()
        default_workspace.public = False
        default_workspace.save()

        response = self.client.get(reverse("scopes:detail"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "No workspaces.", html=True)


class ScopeDetailViewTests(ViewTestMixin, TestCase):
    """Tests for the ScopeDetailView class."""

    scenario = scenarios.DefaultContext()
    scope: ClassVar[Scope]
    private_workspace: ClassVar[Workspace]
    scope_workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up test fixture."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope("scope")
        cls.private_workspace = cls.playground.create_workspace(name="test")
        cls.scope_workspace = cls.playground.create_workspace(
            scope=cls.scope, public=True, name="other"
        )

    def assertWorkspaces(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        workspaces: list[Workspace],
    ) -> None:
        """Check that a tr element is a workspace list table row."""
        rows = self.workspace_list_table_rows(tree)
        for tr, workspace in zip(rows, workspaces):
            self.assertEqual(tr.tag, "tr")
            a = tr.td[0].a
            self.assertEqual(a.text, workspace.name)
        self.assertEqual(len(rows), len(workspaces))

    def test_reverse(self) -> None:
        """Test reverse interacting with scopes."""
        # Without context set, default to DEBUSINE_DEFAULT_SCOPE
        self.assertEqual(reverse("scopes:detail"), "/debusine/")

        self.scenario.set_current()
        self.assertEqual(reverse("scopes:detail"), "/debusine/")

        # Simulate a different request.urlconf
        with urlconf_scope("scope"):
            self.assertEqual(reverse("scopes:detail"), "/scope/")

    def test_view(self) -> None:
        """Scope view loads."""
        self.scenario.set_current()
        response = self.client.get(reverse("scopes:detail"))
        tree = self.assertResponseHTML(response)

        title = self.assertHasElement(tree, "//title")
        self.assertTextContentEqual(
            title, f"Debusine - {self.scenario.scope.name}"
        )

        h1 = self.assertHasElement(tree, "//h1")
        self.assertTextContentEqual(h1, self.scenario.scope.name)

        # self.private_workspace is not shown
        self.assertWorkspaces(tree, [self.scenario.workspace])

    def test_view_anonymous(self) -> None:
        """Ensure anonymous users only see public workspaces."""
        response = self.client.get(reverse("scopes:detail"))
        tree = self.assertResponseHTML(response)
        # Only public workspaces are shown
        self.assertWorkspaces(tree, [self.scenario.workspace])

    def test_view_with_private_access(self) -> None:
        """Ensure contributors can see their workspaces."""
        self.playground.create_group_role(
            self.private_workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        self.client.force_login(self.scenario.user)
        response = self.client.get(reverse("scopes:detail"))
        tree = self.assertResponseHTML(response)
        self.assertWorkspaces(
            tree, [self.scenario.workspace, self.private_workspace]
        )

    def test_view_otherscope(self) -> None:
        """Test listing workspaces on a different scope."""
        with urlconf_scope("scope"):
            url = reverse("scopes:detail")
        response = self.client.get(url)
        tree = self.assertResponseHTML(response)

        title = self.assertHasElement(tree, "//title")
        self.assertTextContentEqual(title, f"Debusine - {self.scope.name}")

        h1 = self.assertHasElement(tree, "//h1")
        self.assertTextContentEqual(h1, self.scope.name)

        self.assertWorkspaces(tree, [self.scope_workspace])

    def test_list_workspace_expiration(self) -> None:
        """Render public workspace's non-NULL default expiration delay."""
        self.scenario.workspace.default_expiration_delay = timedelta(400)
        self.scenario.workspace.save()
        response = self.client.get(reverse("scopes:detail"))
        tree = self.assertResponseHTML(response)
        rows = self.workspace_list_table_rows(tree)
        self.assertTextContentEqual(rows[0].td[2], "400")
