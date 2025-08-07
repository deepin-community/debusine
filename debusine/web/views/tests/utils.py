# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utility code for testing views."""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar
from unittest import mock

import lxml.etree
import lxml.objectify
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.template import engines
from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse
from django.utils.formats import date_format as django_date_format
from django.views.generic.base import View
from rest_framework import status

from debusine.db.context import ContextConsistencyError
from debusine.db.models import User, WorkRequest, WorkflowTemplate, Workspace
from debusine.db.models.permissions import PermissionUser
from debusine.test.django import (
    AllowAll,
    BaseDjangoTestCase,
    DenyAll,
    override_permission,
)
from debusine.web.views.base import Widget

ViewClass = TypeVar("ViewClass", bound=View)


class ViewTestMixin(BaseDjangoTestCase):
    """TestCase functions used to test Debusine views."""

    def make_request(self, url: str) -> HttpRequest:
        """Create a request that can be manipulated before invoking a view."""
        factory = RequestFactory()
        request = factory.get(url)
        request.user = AnonymousUser()
        return request

    def instantiate_view_class(
        self,
        view_class: type[ViewClass],
        request_or_url: HttpRequest | str,
        **kwargs: Any,
    ) -> ViewClass:
        """
        Instantiate a View subclass with the given request.

        For convenience, if request is a string it will be passed to
        make_request.
        """
        request: HttpRequest
        if isinstance(request_or_url, str):
            request = self.make_request(request_or_url)
        else:
            request = request_or_url
        view = view_class()
        view.setup(request, **kwargs)
        return view

    @staticmethod
    def _normalize_node(node: lxml.objectify.ObjectifiedElement) -> str:
        """Normalize the HTML to ignore spaces and new lines."""

        def remove_new_lines_blanks(s: str) -> str:
            # Multiple spaces to single space
            s = re.sub(r"\s+", " ", s)

            # New lines and trailing spaces are removed
            return s.replace("\n", "").strip()

        root = lxml.etree.fromstring(lxml.etree.tostring(node))
        for element in root.iter():
            if element.text:
                element.text = remove_new_lines_blanks(element.text)
            if element.tail:
                element.tail = remove_new_lines_blanks(element.tail)
        return lxml.etree.tostring(root, method="html", encoding="unicode")

    def assertHTMLContentsEquivalent(
        self,
        node: lxml.objectify.ObjectifiedElement,
        expected: str,
    ) -> None:
        """Ensure that node's HTML is equivalent to expected_html."""
        expected_element = lxml.objectify.fromstring(expected)

        normalized_html_node = self._normalize_node(node)
        normalized_html_expected = self._normalize_node(expected_element)

        self.assertEqual(normalized_html_node, normalized_html_expected)

    def assertWorkRequestRow(
        self, tr: lxml.objectify.ObjectifiedElement, work_request: WorkRequest
    ) -> None:
        """Ensure the row shows the given work request."""
        work_request_url = work_request.get_absolute_url()
        self.assertTextContentEqual(tr.td[0], str(work_request.id))
        self.assertEqual(tr.td[0].a.get("href"), work_request_url)
        self.assertEqual(
            tr.td[1].get("title"),
            django_date_format(work_request.created_at, "DATETIME_FORMAT"),
        )
        self.assertTextContentEqual(tr.td[2], work_request.task_type)
        self.assertTextContentEqual(tr.td[3], work_request.get_label())
        self.assertTextContentEqual(tr.td[4], work_request.status.capitalize())
        self.assertTextContentEqual(tr.td[5], work_request.result.capitalize())

    def workspace_list_table_rows(
        self, tree: lxml.etree._Element
    ) -> list[lxml.objectify.ObjectifiedElement]:
        """Find the workspace list table in the page and return it."""
        table = tree.xpath("//table[@id='workspace-list-table']")
        if not table:
            self.fail("page has no workspace list table")
        return list(table[0].tbody.tr)

    def assertSetsCurrentWorkspace(
        self,
        workspace: Workspace,
        url: str,
        method: str = "get",
        **kwargs: Any,
    ) -> None:
        """
        Check that the view sets the current workspace correctly.

        :param workspace: Workspace that is supposed to get set
        :param url: URL of the view to call
        :param method: HTTP method to use
        :param kwargs: passed to ``client.{method}``
        """

        class Reached(BaseException):
            """Thrown when the target code is reached."""

        with mock.patch(
            "debusine.db.models.Workspace.set_current",
            side_effect=ContextConsistencyError("expected fail"),
        ):
            response = getattr(self.client, method)(url, **kwargs)
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
            self.assertRegex(
                response.context["exception"],
                r"Workspace .+ not found in scope .+",
            )

        with (
            self.assertRaises(Reached),
            mock.patch(
                "debusine.db.models.Workspace.set_current",
                autospec=True,
                side_effect=Reached(),
            ) as set_current,
        ):
            response = getattr(self.client, method)(url, **kwargs)
        set_current.assert_called_once_with(workspace)

    def assertEnforcesPermission(
        self,
        predicate: Callable[[PermissionUser], bool],
        url: str,
        target_name: str,
        method: str = "get",
        error_code: int = status.HTTP_403_FORBIDDEN,
        **kwargs: Any,
    ) -> None:
        """
        Check that the view enforces the given permission.

        :param predicate: predicate to check
        :param url: URL of the view to call
        :param target_name: name (to be used with mock.patch) of the view
                       method that gets called after permissions are checked.
                       If it does not contain any dot, it is intended as a
                       method of the view class used for ``url``
        :param method: HTTP method to use
        :param kwargs: passed to ``client.{method}``

        """

        class Reached(BaseException):
            """Thrown when the target code is reached."""

        if "." not in target_name:
            view_class = getattr(resolve(url).func, "view_class")
            target_name = (
                f"{view_class.__module__}.{view_class.__qualname__}"
                f".{target_name}"
            )

        with (
            mock.patch(target_name, side_effect=Reached()) as target,
            override_permission(
                getattr(predicate, "__self__").__class__,
                predicate.__name__,
                AllowAll,
            ),
            self.assertRaises(Reached),
        ):
            response = getattr(self.client, method)(url, **kwargs)
        target.assert_called()

        with (
            mock.patch(target_name, side_effect=Reached()) as target,
            override_permission(
                getattr(predicate, "__self__").__class__,
                predicate.__name__,
                DenyAll,
            ),
        ):
            response = getattr(self.client, method)(url, **kwargs)
            self.assertEqual(response.status_code, error_code)
        target.assert_not_called()

    def assertNavCommonElements(
        self, tree: lxml.objectify.ObjectifiedElement, is_homepage: bool = False
    ) -> None:
        """Check that the common elements in the navbar are present."""
        el = self.assertHasElement(tree, "//a[@class='navbar-brand']")

        if is_homepage:
            self.assertEqual(el.get("href"), reverse("homepage:homepage"))
        else:
            self.assertEqual(el.get("href"), reverse("scopes:detail"))

    def assertNavNoWorkspaces(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check that the page has no workspaces in the navbar."""
        self.assertFalse(tree.xpath("//li[@class='nav-workspaces']"))

    def assertNavHasWorkspaces(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        current: Workspace,
        others: list[Workspace],
    ) -> None:
        """Check that the navbar has the given current and other workspaces."""
        el = self.assertHasElement(tree, "//li[@id='nav-workspaces']")
        if others:
            self.assertEqual(
                el.div.a[0].get("href"), current.get_absolute_url()
            )
            expected = [li.a.get("href") for li in el.div.ul.li]
            actual = [ws.get_absolute_url() for ws in others]
            self.assertEqual(expected, actual)
        else:
            self.assertEqual(el.a.get("href"), current.get_absolute_url())
            self.assertTextContentEqual(el.a, current.name)

    def assertNavNoWorkflows(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check that the page has no collections in the navbar."""
        self.assertFalse(tree.xpath("//li[@class='nav-workflows']"))

    def assertNavHasWorkflows(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        workspace: Workspace,
        workflow_templates: list[WorkflowTemplate],
    ) -> None:
        """Check that the page has the given collections in the navbar."""
        el = self.assertHasElement(tree, "//li[@id='nav-workflows']")
        if not workflow_templates:
            workflows_a = self.assertHasElement(el, "a")
            self.assertFalse(el.xpath("div"))
        else:
            workflows_a = self.assertHasElement(el, "div/a[@href!='#']")

        list_url = reverse(
            "workspaces:workflows:list", kwargs={"wname": workspace.name}
        )
        self.assertEqual(workflows_a.get("href"), list_url)
        self.assertTextContentEqual(workflows_a, "Workflows")

        if not workflow_templates:
            return

        expected: list[tuple[str, str]] = [
            (list_url + f"?workflow_templates={wt.name}", wt.name)
            for wt in workflow_templates
        ]
        actual: list[tuple[str, str]] = []
        for li in el.div.ul.li:
            actual.append((li.a.get("href") or "", li.a.text or ""))
        self.assertEqual(expected, actual)

    def assertNavNoCollections(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check that the page has no collections in the navbar."""
        self.assertFalse(tree.xpath("//li[@class='nav-collections']"))

    def assertNavHasCollections(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        workspace: Workspace,
    ) -> None:
        """Check that the page has the given collections in the navbar."""
        el = self.assertHasElement(tree, "//li[@id='nav-collections']")
        self.assertEqual(
            el.div.a[0].get("href"),
            reverse(
                "workspaces:collections:list",
                kwargs={"wname": workspace.name},
            ),
        )
        self.assertTextContentEqual(el.div.a[0], "Collections")

        expected = {
            (c.get_absolute_url(), str(c)) for c in workspace.collections.all()
        }
        actual = set()
        for li in el.div.ul.li:
            actual.add((li.a.get("href"), li.a.text))
        self.assertEqual(expected, actual)

    def assertNavNoUser(self, tree: lxml.objectify.ObjectifiedElement) -> None:
        """Check that the page has no user in the navbar."""
        ul = self.assertHasElement(tree, "//ul[@id='navbar-right']")
        login_url = ul.li.a.get("href")
        assert login_url is not None
        self.assertEqual(login_url.split("?")[0], reverse("login"))

        self.assertFalse(tree.xpath("//ul[@id='navbar-user-actions']"))

    def assertNavHasUser(
        self, tree: lxml.objectify.ObjectifiedElement, user: User
    ) -> None:
        """Check that the page navbar has a user menu for the given user."""
        ul = self.assertHasElement(tree, "//ul[@id='navbar-right']")
        self.assertTextContentEqual(ul.li.a, user.username)

        ul = self.assertHasElement(tree, "//ul[@id='navbar-user-actions']")
        self.assertEqual(
            ul.li[0].a.get("href"),
            reverse("user:detail", kwargs={"username": user.username}),
        )
        self.assertEqual(
            ul.li[1].a.get("href"),
            reverse("user:token-list", kwargs={"username": user.username}),
        )
        self.assertEqual(ul.li[-1].form.get("action"), reverse("logout"))

    def assertNavHasPlumbing(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check that the page navbar has "Plumbing" drop down."""
        a = self.assertHasElement(tree, "//a[@id='navbar-plumbing-actions']")
        self.assertTextContentEqual(a, "Plumbing")

    def assertNavNoPlumbing(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> None:
        """Check that the page navbar has no "Plumbing" drop down."""
        self.assertFalse(tree.xpath("//a[@id='navbar-plumbing-actions']"))

    def render_widget(self, widget: Widget) -> str:
        """Render a widget."""
        template = engines["django"].from_string(
            "{% load debusine %}{% widget widget %}"
        )
        setattr(template, "engine", engines["django"])
        # Set DEBUG=True to have render raise exceptions instead of logging
        # them
        with override_settings(DEBUG=True):
            return template.render({"widget": widget})


def html_check_icon(value: bool) -> str:
    """Return HTML for check icon."""
    if value:
        return '<i style="color:green;" class="bi bi-check2"></i>'
    else:
        return '<i style="color:red;" class="bi bi-x"></i>'


def date_format(dt: datetime) -> str:
    """Return dt datetime formatted with the Django template format."""
    return django_date_format(dt, "DATETIME_FORMAT")
