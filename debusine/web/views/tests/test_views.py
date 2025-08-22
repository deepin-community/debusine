# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the views."""
from typing import Any
from unittest import mock

from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from rest_framework import status

from debusine.db.models import WorkRequest, Workspace
from debusine.db.playground import scenarios
from debusine.test.django import ListFilter, TestCase, override_permission
from debusine.web.views import HomepageView
from debusine.web.views.tests.utils import ViewTestMixin, html_check_icon


class HomepageViewTests(ViewTestMixin, TestCase):
    """Tests for the Homepage class."""

    scenario = scenarios.DefaultScopeUser()

    create_work_request_url = reverse(
        "workspaces:work-requests:create", kwargs={"wname": "System"}
    )
    create_work_request_html = (
        f'<a class="dropdown-item" href="{create_work_request_url}">'
        "Create work request</a>"
    )

    xpath_scope_workspace_list = "//table[@id='scope-workspace-list']"
    xpath_current_workflow_list = "//div[@id='current-workflow-list-table']"
    xpath_completed_workflow_list = "//div[@id='completed-workflow-list-table']"

    def test_html_check_icon(self) -> None:
        """Test that html_check_icon returns the right results."""
        self.assertIn("green", html_check_icon(True))
        self.assertNotIn("green", html_check_icon(False))
        self.assertIn("red", html_check_icon(False))
        self.assertNotIn("red", html_check_icon(True))

    def test_slash(self) -> None:
        """
        Slash (/) URL and homepage are the same.

        Make sure that the / is handled by the 'homepage' view.
        """
        self.assertEqual(reverse("homepage:homepage"), "/")

    def test_homepage(self) -> None:
        """Homepage view loads (not logged in)."""
        list_your_tokens_url = reverse(
            "user:token-list", kwargs={"username": self.scenario.user.username}
        )
        list_your_tokens_html = (
            f'<a class="dropdown-item" href="{list_your_tokens_url}">Tokens</a>'
        )

        response = self.client.get(reverse("homepage:homepage"))
        tree = self.assertResponseHTML(response)
        h1 = self.assertHasElement(tree, "//h1")
        self.assertTextContentEqual(h1, "Welcome to Debusine!")

        title = self.assertHasElement(tree, "//title")
        self.assertTextContentEqual(title, "Debusine - Homepage")

        self.assertNavCommonElements(tree, is_homepage=True)
        self.assertNavNoUser(tree)

        self.assertHasElement(tree, self.xpath_scope_workspace_list)

        self.assertNotContains(response, list_your_tokens_html, html=True)
        self.assertFalse(tree.xpath("//a[@id='nav-create-artifact']"))
        self.assertFalse(tree.xpath("//a[@id='nav-create-work-request']"))
        self.assertFalse(tree.xpath(self.xpath_current_workflow_list))
        self.assertFalse(tree.xpath(self.xpath_completed_workflow_list))

    def test_homepage_logged_in(self) -> None:
        """User is logged in: contains "You are authenticated as: username"."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(reverse("homepage:homepage"))
        tree = self.assertResponseHTML(response)

        self.assertNavCommonElements(tree, is_homepage=True)
        self.assertNavHasUser(tree, self.scenario.user)

        self.assertFalse(tree.xpath("//a[@id='nav-create-artifact']"))
        self.assertFalse(tree.xpath("//a[@id='nav-create-work-request']"))

        table = self.assertHasElement(tree, self.xpath_scope_workspace_list)
        tbody = self.assertHasElement(table, "tbody")
        self.assertEqual(len(tbody.tr), 1)
        tr = tbody.tr[0]
        self.assertTextContentEqual(tr.td[0], self.scenario.scope.name)
        self.assertTextContentEqual(tr.td[1], "System")
        self.assertTextContentEqual(tr.td[2], "Viewer")
        self.assertTextContentEqual(tr.td[3], "0")
        self.assertTextContentEqual(tr.td[4], "0")
        self.assertTextContentEqual(tr.td[5], "0")

        table = self.assertHasElement(tree, self.xpath_current_workflow_list)
        table = self.assertHasElement(tree, self.xpath_completed_workflow_list)

    def test_messages(self) -> None:
        """Messages from django.contrib.messages are displayed."""

        def mocked_get_context_data(
            self: HomepageView, **kwargs: Any
        ) -> dict[str, Any]:
            messages.error(self.request, "Error message")
            return {
                "base_template": HomepageView.base_template,
                "workspaces": "",
            }

        with mock.patch(
            "debusine.web.views.views.HomepageView.get_context_data",
            autospec=True,
            side_effect=mocked_get_context_data,
        ):
            response = self.client.get(reverse("homepage:homepage"))

        tree = self.assertResponseHTML(response)
        div = self.assertHasElement(tree, "//div[@id='user-message-container']")
        msgdiv = div.div.div
        self.assertEqual(msgdiv.get("class"), "toast")
        self.assertEqual(msgdiv.get("role"), "alert")
        assert msgdiv.div[1].text is not None
        self.assertEqual(msgdiv.div[1].text.strip(), "Error message")

    def test_homepage_workflow_list(self) -> None:
        """Homepage lists visible scopes."""
        self.client.force_login(self.scenario.user)
        scope1 = self.playground.get_or_create_scope("scope1")
        workspace1 = self.playground.create_workspace(
            name="workspace1", scope=scope1
        )
        scope2 = self.playground.get_or_create_scope("scope2")
        self.playground.create_workspace(name="workspace2", scope=scope2)

        with override_permission(
            Workspace, "can_display", ListFilter, exclude=[workspace1]
        ):
            response = self.client.get(reverse("homepage:homepage"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, self.xpath_scope_workspace_list)
        tbody = self.assertHasElement(table, "tbody")
        actual = [
            (
                self.get_node_text_normalized(tr.td[0]),
                (self.get_node_text_normalized(tr.td[1])),
            )
            for tr in tbody.tr
        ]
        self.assertEqual(
            actual,
            [
                ("debusine", "System"),
                ("scope2", "workspace2"),
            ],
        )

    def test_queries_anonymous(self) -> None:
        """Test the number of queries for an anonymous visit."""
        with self.assertNumQueries(5):
            self.client.get(reverse("homepage:homepage"))

    def test_queries_anonymous_with_workspaces(self) -> None:
        """Test the number of queries for an anonymous visit."""
        for i in range(3):
            self.playground.create_workspace(name=f"workspace{i}")
        with self.assertNumQueries(5):
            self.client.get(reverse("homepage:homepage"))

    def test_queries_authenticated(self) -> None:
        """Test the number of queries for an authenticated visit."""
        self.client.force_login(self.scenario.user)
        with self.assertNumQueries(11):
            self.client.get(reverse("homepage:homepage"))

    def test_queries_authenticated_with_workspaces(self) -> None:
        """Test the number of queries for an anonymous visit."""
        self.client.force_login(self.scenario.user)
        for i in range(3):
            self.playground.create_workspace(name=f"workspace{i}")
        with self.assertNumQueries(11):
            self.client.get(reverse("homepage:homepage"))

    def test_queries_authenticated_with_workflows(self) -> None:
        """Test query count for a visit with work requests shown."""
        self.client.force_login(self.scenario.user)
        for i in range(3):
            self.playground.create_workspace(name=f"workspace{i}")
        template = self.playground.create_workflow_template("test", "noop")
        for i in range(5):
            workflow = self.playground.create_workflow(template)
            workflow.mark_running()
        # with self.assertNumQueries(14): # if there are no workflows to display
        with self.assertNumQueries(22):
            response = self.client.get(reverse("homepage:homepage"))
        self.assertEqual(
            len(response.context["workflows_current"].page_obj.object_list),
            5,
        )

    def test_homepage_customization(self) -> None:
        """Homepage extended by templates installed by site admins."""
        response = self.client.get(reverse("homepage:homepage"))
        tree = self.assertResponseHTML(response)
        h1 = self.assertHasElement(tree, "//h1")
        self.assertTextContentEqual(h1, "Welcome to Debusine!")

        with self.custom_template("web/homepage.html") as template:
            template.write_text(
                "{% extends 'web/homepage.html' %}"
                "{% block introduction %}"
                "<h1>Welcome to a custom Debusine</h1>"
                "{% endblock %}"
            )
            response = self.client.get(reverse("homepage:homepage"))
            tree = self.assertResponseHTML(response)
            h1 = self.assertHasElement(tree, "//h1")
            self.assertTextContentEqual(h1, "Welcome to a custom Debusine")

    def test_only_root_workflows_shown(self) -> None:
        self.client.force_login(self.scenario.user)
        template = self.playground.create_workflow_template("test", "noop")
        root_workflow = self.playground.create_workflow(template)
        root_workflow.mark_running()
        child_workflow = self.playground.create_workflow(
            template, parent=root_workflow
        )
        child_workflow.mark_running()
        completed_child_workflow = self.playground.create_workflow(
            template, parent=root_workflow
        )
        completed_child_workflow.mark_running()
        completed_child_workflow.mark_completed(
            result=WorkRequest.Results.SUCCESS
        )
        response = self.client.get(reverse("homepage:homepage"))
        self.assertQuerySetEqual(
            response.context["workflows_current"].page_obj.object_list,
            [root_workflow],
        )

        workspaces_qs = response.context["workspaces"].table.rows
        self.assertEqual(workspaces_qs.count(), 1)
        workspace = workspaces_qs.first()
        self.assertEqual(
            (workspace.running, workspace.needs_input, workspace.completed),
            (1, None, None),
        )

    def test_workflows_completed_includes_aborted(self) -> None:
        template = self.playground.create_workflow_template("test", "noop")
        running_workflow = self.playground.create_workflow(template)
        running_workflow.mark_running()
        completed_workflow = self.playground.create_workflow(template)
        completed_workflow.mark_running()
        completed_workflow.mark_completed(result=WorkRequest.Results.SUCCESS)
        aborted_workflow = self.playground.create_workflow(template)
        aborted_workflow.mark_running()
        aborted_workflow.mark_aborted()

        self.client.force_login(self.scenario.user)
        response = self.client.get(reverse("homepage:homepage"))
        self.assertQuerySetEqual(
            response.context["workflows_completed"].page_obj.object_list,
            [completed_workflow, aborted_workflow],
            ordered=False,
        )

        workspaces_qs = response.context["workspaces"].table.rows
        self.assertEqual(workspaces_qs.count(), 1)
        workspace = workspaces_qs.first()
        self.assertEqual(
            (workspace.running, workspace.needs_input, workspace.completed),
            (1, None, 2),
        )


class AdminTests(TestCase):
    """Test the admin view."""

    def assertLoginRequired(self, url: str) -> None:
        """Test that a login is required to access the given URL."""
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        # TODO: we apparently have an admin:login view: what does it do? Does
        # it need to be secured? Do we want an admin at all?
        self.assertEqual(
            response["Location"],
            reverse("admin:login") + "?next=" + url,
        )

    def test_admin_url(self) -> None:
        """Test resolving the admin URL."""
        url = reverse("admin:index")
        self.assertTrue(url.startswith("/-/admin"))

    def test_admin_anonymous_user(self) -> None:
        """Test resolving the admin URL."""
        self.assertLoginRequired(reverse("admin:index"))

    def test_admin_ordinary_user(self) -> None:
        """Test resolving the admin URL."""
        self.client.force_login(self.playground.get_default_user())
        self.assertLoginRequired(reverse("admin:index"))

    def test_admin_staff(self) -> None:
        """Test resolving the admin URL."""
        user = self.playground.get_default_user()
        user.is_staff = True
        user.save()
        self.client.force_login(user)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_superuser(self) -> None:
        """Test resolving the admin URL."""
        user = self.playground.get_default_user()
        user.is_superuser = True
        user.save()
        self.client.force_login(user)
        self.assertLoginRequired(reverse("admin:index"))


class LegacyRedirectTests(TestCase):
    """Test the best effort redirects for unscoped URLs."""

    def test_scope_redirect_valid(self) -> None:
        """Test redirection of a valid legacy URL."""
        response = self.client.get("/accounts/foo/bar?baz=true")
        self.assertEqual(
            response.status_code, status.HTTP_301_MOVED_PERMANENTLY
        )
        self.assertEqual(
            response.headers["Location"],
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}/accounts/foo/bar?baz=true",
        )

    def test_scope_redirect_invalid(self) -> None:
        """Test redirection of an invalid legacy URL."""
        # Misspelled url (misses trailing 's')
        response = self.client.get("/account/foo/bar?baz=true")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_scope_reproduce_redirect_loop(self) -> None:
        """Reproduce a redirect loop."""
        artifact, _ = self.playground.create_artifact()

        response = self.client.get(f"/api/1.0/artifact/{artifact.pk}")
        self.assertEqual(
            response.status_code, status.HTTP_301_MOVED_PERMANENTLY
        )
        self.assertEqual(
            response.headers["Location"], f"/api/1.0/artifact/{artifact.pk}/"
        )

        response = self.client.get(
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}/System/artifact/{artifact.pk}"
        )
        self.assertEqual(
            response.status_code, status.HTTP_301_MOVED_PERMANENTLY
        )
        self.assertEqual(
            response.headers["Location"],
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}"
            f"/System/artifact/{artifact.pk}/",
        )

    # urlconfs are created at Django startup, so changing the setting
    # afterwards has no effect: the redirect replacement pattern has already
    # been created. This test would be pointless, but keeping it here as
    # documentation. I would not subclass RedirectView only to have a more
    # dynamic replacement pattern for use only in tests.
    #
    # @override_settings(DEBUSINE_DEFAULT_SCOPE="debian")
    # def test_redirect_different_scope(self) -> None:
    #     """Redirection uses DEBUSINE_DEFAULT_SCOPE."""
    #     response = self.client.get("/accounts/foo/bar?baz=true")
    #     self.assertEqual(
    #         response.status_code, status.HTTP_301_MOVED_PERMANENTLY
    #     )
    #     self.assertEqual(
    #         response.headers["Location"],
    #         "/debian/accounts/foo/bar?baz=true",
    #     )

    def test_workspace_redirect_valid(self) -> None:
        """Test redirection of a valid legacy workspace URL."""
        response = self.client.get("/debusine/workspace/foo/bar?baz=true")
        self.assertEqual(
            response.status_code, status.HTTP_301_MOVED_PERMANENTLY
        )
        self.assertEqual(
            response.headers["Location"],
            "/debusine/foo/bar?baz=true",
        )

    def test_redirect_task_status(self) -> None:
        """Test redirection on the /task-status/ URL."""
        response = self.client.get("/task-status/foo/bar?baz=true")
        self.assertRedirects(
            response,
            "/-/status/queue/foo/bar?baz=true",
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
            fetch_redirect_response=False,
        )

    def test_redirect_workers(self) -> None:
        """Test redirection on the /workers/ URL."""
        response = self.client.get("/workers/foo/bar?baz=true")
        self.assertRedirects(
            response,
            "/-/status/workers/foo/bar?baz=true",
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
            fetch_redirect_response=False,
        )

    def test_redirect_user(self) -> None:
        """Test redirection on the /user/ URL."""
        response = self.client.get("/user/foo/bar?baz=true")
        self.assertRedirects(
            response,
            "/-/user/foo/bar?baz=true",
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
            fetch_redirect_response=False,
        )

    def test_redirect_signon(self) -> None:
        """Test redirection on the signon URLs."""
        scope = settings.DEBUSINE_DEFAULT_SCOPE
        for path, view_name in (
            ("/accounts/oidc_callback/{name}/", "signon:oidc_callback"),
            ("/accounts/bind_identity/{name}/", "signon:bind_identity"),
            ("/{scope}/accounts/oidc_callback/{name}/", "signon:oidc_callback"),
            ("/{scope}/accounts/bind_identity/{name}/", "signon:bind_identity"),
        ):
            for name in ("foo", "bar"):
                url = path.format(name=name, scope=scope)
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertRedirects(
                        response,
                        reverse(view_name, kwargs={"name": name}),
                        status_code=status.HTTP_301_MOVED_PERMANENTLY,
                        fetch_redirect_response=False,
                    )
