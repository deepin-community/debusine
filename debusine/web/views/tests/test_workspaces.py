# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the workspace views."""

from datetime import timedelta
from typing import ClassVar

from django.template.response import SimpleTemplateResponse
from rest_framework import status

from debusine.artifacts.models import CollectionCategory
from debusine.db.context import context
from debusine.db.models import Collection, Scope, WorkRequest, Workspace
from debusine.db.playground import scenarios
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    override_permission,
)
from debusine.web.views.tests.utils import ViewTestMixin


class WorkspaceDetailTests(ViewTestMixin, TestCase):
    """Tests for WorkspaceDetail view."""

    scenario = scenarios.DefaultScopeUser()

    only_public_workspaces_message = (
        "Not authenticated. Only public workspaces are listed."
    )

    workspace_public: ClassVar[Workspace]
    workspace_private: ClassVar[Workspace]
    collection: ClassVar[Collection]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.workspace_public = cls.playground.create_workspace(
            name="Public", public=True
        )
        cls.workspace_private = cls.playground.create_workspace(name="Private")

        cls.collection = cls.playground.create_collection(
            "Public",
            CollectionCategory.WORKFLOW_INTERNAL,
            workspace=cls.workspace_public,
        )

    def test_detail(self) -> None:
        """Test workspace detail view."""
        response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)
        details = self.assertHasElement(
            tree, "//table[@id='workspace-details']"
        )
        self.assertHasElement(details, "//tr[@id='workspace-details-public']")
        self.assertHasElement(
            details, "//tr[@id='workspace-details-default-expiration']"
        )

    def test_detail_permissions(self) -> None:
        """Test permissions."""
        self.assertSetsCurrentWorkspace(
            self.workspace_public,
            self.workspace_public.get_absolute_url(),
        )

    def test_detail_no_expiration(self) -> None:
        """No expiration is shown on non-expiring workspaces."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)
        details = self.assertHasElement(
            tree, "//table[@id='workspace-details']"
        )
        self.assertFalse(
            details.xpath("//tr[@id='workspace-details-expiration']")
        )

    def test_detail_expiration(self) -> None:
        """Expiration is shown on expiring workspaces."""
        self.workspace_public.expiration_delay = timedelta(days=7)
        self.workspace_public.save()

        self.client.force_login(self.scenario.user)
        response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)
        details = self.assertHasElement(
            tree, "//table[@id='workspace-details']"
        )
        tr = self.assertHasElement(
            details, "//tr[@id='workspace-details-expiration']"
        )
        self.assertTextContentEqual(tr.th, "Expires")
        self.assertTextContentEqual(
            tr.td,
            (self.workspace_public.created_at + timedelta(days=7)).strftime(
                "%Y-%m-%d"
            ),
        )

    def test_detail_can_create_artifact(self) -> None:
        """User is logged in and can create artifacts."""
        self.client.force_login(self.scenario.user)
        with (
            override_permission(Workspace, "can_create_artifacts", AllowAll),
            override_permission(Workspace, "can_create_work_requests", DenyAll),
        ):
            response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertHasElement(tree, "//a[@id='nav-create-artifact']")
        self.assertFalse(tree.xpath("//a[@id='nav-create-work-request']"))

    def test_detail_can_create_work_request(self) -> None:
        """User is logged in and can create work_request."""
        self.client.force_login(self.scenario.user)
        with (
            override_permission(Workspace, "can_create_artifacts", DenyAll),
            override_permission(
                Workspace, "can_create_work_requests", AllowAll
            ),
        ):
            response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//a[@id='nav-create-artifact']"))
        self.assertHasElement(tree, "//a[@id='nav-create-work-request']")

    def test_detail_private(self) -> None:
        """Unauthenticated detail view cannot access private workspaces."""
        response = self.client.get(self.workspace_private.get_absolute_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_login(self.playground.get_default_user())
        response = self.client.get(self.workspace_private.get_absolute_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.playground.create_group_role(
            self.workspace_private,
            Workspace.Roles.OWNER,
            users=[self.playground.get_default_user()],
        )
        response = self.client.get(self.workspace_private.get_absolute_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_name_duplicate(self) -> None:
        """Homonymous workspace in different scope does not trigger a dup."""
        scope = Scope.objects.create(name="tests")
        with context.disable_permission_checks():
            self.playground.create_workspace(
                name="Public",
                public=True,
                scope=scope,
            )
        response = self.client.get(self.workspace_public.get_absolute_url())
        self.assertResponseHTML(response)

    def test_detail_workflowtemplate_list_empty(self) -> None:
        """Test showing a workspace with no workflow templates."""
        response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)
        div = self.assertHasElement(tree, "//div[@id='workflow-templates']")
        self.assertTextContentEqual(
            div, "No workflow templates configured for this workspace."
        )

    def test_detail_workflowtemplate(self) -> None:
        """Test showing a workspace with one workflow templates."""
        template = self.playground.create_workflow_template(
            name="noop-template",
            task_name="noop",
            workspace=self.workspace_public,
        )

        # 3 completed workflow
        for i in range(3):
            self.playground.create_workflow(
                task_name="noop",
                status=WorkRequest.Statuses.COMPLETED,
            )

        # 2 running workflows
        for i in range(2):
            self.playground.create_workflow(
                task_name="noop",
                status=WorkRequest.Statuses.RUNNING,
            )

        # 1 needs input workflow
        workflow_needs_input = self.playground.create_workflow(
            task_name="noop",
            status=WorkRequest.Statuses.RUNNING,
        )
        workflow_needs_input.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.NEEDS_INPUT
        )
        workflow_needs_input.save()

        response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)

        self.assertTrue(response.context["workflow_templates"].ordered)

        div = self.assertHasElement(tree, "//div[@id='workflow-templates']")
        workflow_templates = div.table

        self.assertEqual(
            workflow_templates.attrib["id"], "workflow-templates-list"
        )

        self.assertEqual(
            workflow_templates.tbody.tr[0].td[0].a, "noop-template"
        )
        self.assertEqual(
            workflow_templates.tbody.tr[0].td[0].a.attrib["href"],
            template.get_absolute_url(),
        )
        self.assertEqual(workflow_templates.tbody.tr[0].td[1], "noop")


class WorkspaceUpdateTests(ViewTestMixin, TestCase):
    """Tests for WorkspaceUpdate view."""

    scenario = scenarios.DefaultContext()

    def test_permissions(self) -> None:
        """Test permissions."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            self.scenario.workspace.get_absolute_url_configure(),
        )
        self.assertEnforcesPermission(
            self.scenario.workspace.can_configure,
            self.scenario.workspace.get_absolute_url_configure(),
            "get_context_data",
        )

    @override_permission(Workspace, "can_configure", AllowAll)
    def test_configure(self) -> None:
        """Test changing workspace configuration."""
        ws = self.scenario.workspace
        self.playground.create_group_role(
            ws, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.client.force_login(self.scenario.user)

        response = self.client.post(
            ws.get_absolute_url_configure(),
            {
                "public": False,
                "expiration_delay": 7,
                "default_expiration_delay": 14,
            },
        )
        self.assertRedirects(response, ws.get_absolute_url())

        ws.refresh_from_db()

        self.assertFalse(ws.public)
        self.assertEqual(ws.expiration_delay, timedelta(days=7))
        self.assertEqual(ws.default_expiration_delay, timedelta(days=14))

    @override_permission(Workspace, "can_configure", AllowAll)
    def test_initial_form(self) -> None:
        """Get request to ensure the form is displayed."""
        ws = self.scenario.workspace
        self.client.force_login(self.scenario.user)

        title_text = f"Configure workspace {ws.name}"
        response = self.client.get(ws.get_absolute_url_configure())
        tree = self.assertResponseHTML(response)
        title = self.assertHasElement(tree, "//head//title")
        self.assertTextContentEqual(title, f"Debusine - {title_text}")
        h1 = self.assertHasElement(tree, "//body//h1")
        self.assertTextContentEqual(h1, title_text)

        assert isinstance(response, SimpleTemplateResponse)
        form = response.context_data["form"]
        self.assertTrue(form["public"].initial)
        self.assertIsNone(form["expiration_delay"].initial)
        self.assertEqual(form["default_expiration_delay"].initial, timedelta(0))
