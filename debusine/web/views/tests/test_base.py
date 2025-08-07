# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Tests for the base views."""

from importlib.metadata import PackageNotFoundError
from typing import cast
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.template.response import TemplateResponse
from django.test import RequestFactory
from django.views.generic import TemplateView

from debusine.artifacts.models import CollectionCategory
from debusine.db.context import context
from debusine.db.models import Scope
from debusine.db.playground import scenarios
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)
from debusine.web.views.base import BaseUIView
from debusine.web.views.tests.utils import ViewTestMixin


class TemplateBaseUIView(BaseUIView, TemplateView):
    """BaseUIView and TemplateView to test base template rendering."""

    template_name = "web/_base.html"


class TestBaseUI(ViewTestMixin, TestCase):
    """Test the BaseUI view."""

    scenario = scenarios.DefaultContext()

    def render_base(self) -> TestResponseType:
        """Render the base template."""
        factory = RequestFactory()
        request = factory.get("/")
        assert context.user is not None
        request.user = context.user
        view = TemplateBaseUIView.as_view()
        response = cast(TemplateResponse, view(request))
        response.render()
        # Django is somewhat inconsistent with naming: alias context_data to
        # context to maintain the API of test responses
        response.context = response.context_data  # type: ignore[assignment]
        return cast(TestResponseType, response)

    def test_enforce(self) -> None:
        """Test the enforce method."""
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()
        context.set_scope(scope)
        context.set_user(user)
        view = BaseUIView()
        with override_permission(Scope, "can_display", AllowAll):
            view.enforce(scope.can_display)
        with (
            override_permission(Scope, "can_display", DenyAll),
            self.assertRaisesRegex(
                PermissionDenied, r"playground cannot display scope debusine"
            ),
        ):
            view.enforce(scope.can_display)

    def test_set_current_workspace_wrong_name(self) -> None:
        """Test setting the current workspace to an invalid name."""
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        view = BaseUIView()
        with self.assertRaises(Http404):
            view.set_current_workspace("does-not-exist")

    def _call_init_view(self, method: str, add_method: bool) -> bool:
        """Check if init_view is called for the given method."""
        factory = RequestFactory()
        request = getattr(factory, method)("/")

        if add_method:
            view_cls = type(
                "TestView",
                (BaseUIView,),
                {method: lambda *args: HttpResponse("ok")},
            )
            view = view_cls.as_view()  # type: ignore[attr-defined]
        else:
            view = BaseUIView.as_view()

        with mock.patch(
            "debusine.web.views.base.BaseUIView.init_view"
        ) as init_view:
            view(request)

        return init_view.called

    def test_init_view(self) -> None:
        """Test init_view handling."""
        for method in ("get", "post", "put", "patch", "delete", "head"):
            with self.subTest(method):
                self.assertFalse(self._call_init_view(method, False))
                self.assertTrue(self._call_init_view(method, True))

        # options is always defined in views, but init_view is not called for
        # it
        self.assertFalse(self._call_init_view("options", False))

        # init_view is not called for trace
        self.assertFalse(self._call_init_view("trace", False))
        self.assertFalse(self._call_init_view("trace", True))

    def test_get_context_data_no_workspace(self) -> None:
        """Test get_context_data with no current workspace."""
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        view = BaseUIView()
        ctx = view.get_context_data()
        self.assertEqual(ctx["base_template"], "web/_base.html")
        self.assertEqual(ctx["title"], "")
        self.assertNotIn("workspace_collections", ctx)

    def test_get_context_data_with_workspace(self) -> None:
        """Test get_context_data with a current workspace."""
        self.scenario.set_current()

        self.playground.create_collection(
            name="testing", category=CollectionCategory.WORKFLOW_INTERNAL
        )

        view = BaseUIView()
        ctx = view.get_context_data()
        self.assertEqual(ctx["base_template"], "web/_base.html")
        self.assertEqual(ctx["title"], "")
        self.assertQuerySetEqual(
            ctx["workspace_collections"],
            list(
                self.scenario.workspace.collections.exclude(
                    category=CollectionCategory.WORKFLOW_INTERNAL
                )
            ),
            ordered=False,
        )

    def test_get_context_data_with_version(self) -> None:
        """get_context_data returns debusine's version if known."""
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        view = BaseUIView()

        with mock.patch(
            "debusine.web.views.base.version", return_value="0.1.0"
        ) as mock_version:
            ctx = view.get_context_data()

        self.assertEqual(ctx["debusine_version"], "0.1.0")
        mock_version.assert_called_once_with("debusine")

    def test_get_context_data_without_version(self) -> None:
        """get_context_data tolerates not knowing debusine's version."""
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        view = BaseUIView()

        with mock.patch(
            "debusine.web.views.base.version", side_effect=PackageNotFoundError
        ):
            ctx = view.get_context_data()

        self.assertNotIn("debusine_version", ctx)

    def test_navbar_not_logged_in(self) -> None:
        """Test the navbar with no user and no current workspace."""
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        response = self.render_base()
        tree = self.assertResponseHTML(response)

        self.assertNavCommonElements(tree)
        self.assertNavNoUser(tree)
        self.assertNavNoWorkspaces(tree)
        self.assertNavNoWorkflows(tree)
        self.assertNavNoCollections(tree)
        self.assertNavNoPlumbing(tree)

    def test_navbar_no_workspace(self) -> None:
        """Test the navbar with no user and no current workspace."""
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)

        response = self.render_base()
        tree = self.assertResponseHTML(response)

        self.assertNavCommonElements(tree)
        self.assertNavHasUser(tree, self.scenario.user)
        self.assertNavNoWorkspaces(tree)
        self.assertNavNoWorkflows(tree)
        self.assertNavNoCollections(tree)
        self.assertNavNoPlumbing(tree)

    def test_navbar_with_workspace(self) -> None:
        """Test the navbar with user and current workspace."""
        self.scenario.set_current()

        response = self.render_base()
        tree = self.assertResponseHTML(response)

        self.assertNavCommonElements(tree)
        self.assertNavHasCollections(tree, self.scenario.workspace)
        self.assertNavHasWorkflows(tree, self.scenario.workspace, [])
        self.assertNavHasWorkspaces(tree, self.scenario.workspace, [])
        self.assertNavHasUser(tree, self.scenario.user)
        self.assertNavHasPlumbing(tree)

    def test_navbar_with_workflow_templates(self) -> None:
        """Test the navbar with user and current workspace."""
        self.scenario.set_current()

        wt_foo = self.playground.create_workflow_template("foo", "noop")
        wt_bar = self.playground.create_workflow_template("bar", "noop")
        wt_baz = self.playground.create_workflow_template("baz", "noop")

        response = self.render_base()
        tree = self.assertResponseHTML(response)

        self.assertNavCommonElements(tree)
        self.assertNavHasCollections(tree, self.scenario.workspace)
        self.assertNavHasWorkflows(
            tree,
            self.scenario.workspace,
            [wt_bar, wt_baz, wt_foo],
        )
        self.assertNavHasWorkspaces(tree, self.scenario.workspace, [])
        self.assertNavHasUser(tree, self.scenario.user)

    def test_navbar_with_other_workspace(self) -> None:
        """Test the navbar with current plus other workspaces to list."""
        self.scenario.set_current()
        b = self.playground.create_workspace(name="b", public=True)
        a = self.playground.create_workspace(name="a", public=True)
        self.playground.create_workspace(name="c")

        response = self.render_base()
        tree = self.assertResponseHTML(response)
        self.assertEqual(response.context["other_workspaces"], [a, b])

        self.assertNavCommonElements(tree)
        self.assertNavHasCollections(tree, self.scenario.workspace)
        self.assertNavHasWorkspaces(tree, self.scenario.workspace, [a, b])
        self.assertNavHasUser(tree, self.scenario.user)

    def test_footer_with_version(self) -> None:
        """The footer shows debusine's version if known."""
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        with mock.patch(
            "debusine.web.views.base.version", return_value="0.1.0"
        ):
            response = self.render_base()
        tree = self.assertResponseHTML(response)

        self.assertTextContentEqual(
            tree.xpath("//footer/p")[-1],
            "Documentation • Bugs • Code • Contributing • 0.1.0",
        )

    def test_footer_without_version(self) -> None:
        """The footer skips debusine's version if not known."""
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        with mock.patch(
            "debusine.web.views.base.version", side_effect=PackageNotFoundError
        ):
            response = self.render_base()
        tree = self.assertResponseHTML(response)

        self.assertTextContentEqual(
            tree.xpath("//footer/p")[-1],
            "Documentation • Bugs • Code • Contributing",
        )
