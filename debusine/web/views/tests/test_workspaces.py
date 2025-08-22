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
from unittest import mock

import lxml
import lxml.objectify
from django.template.response import SimpleTemplateResponse
from django.urls import reverse
from rest_framework import status

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineTaskConfiguration,
    TaskTypes,
)
from debusine.db.models import Collection, Scope, WorkRequest, Workspace
from debusine.db.playground import scenarios
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
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
        tr = self.assertHasElement(
            details, "//tr[@id='workspace-details-expiration']"
        )
        self.assertTextContentEqual(tr.th, "Expires")
        self.assertTextContentEqual(tr.td, "Never")

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

    def test_detail_parents(self) -> None:
        """Parent workspaces are shown when they exist."""
        workspace = self.playground.create_workspace(name="base", public=True)
        workspace.set_inheritance(
            [self.workspace_private, self.workspace_public]
        )

        response = self.client.get(workspace.get_absolute_url())
        tree = self.assertResponseHTML(response)
        details = self.assertHasElement(
            tree, "//table[@id='workspace-details']"
        )
        tr = self.assertHasElement(
            details, "*/tr[@id='workspace-details-parents']", dump_on_error=True
        )
        self.assertTextContentEqual(tr.th, "Inherits from")
        self.assertTextContentEqual(tr.td, str(self.workspace_public))

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
        self.playground.create_workspace(
            name="Public", public=True, scope=scope
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
        self.assertEqual(workflow_templates.tbody.tr[0].td[1].a, "noop")
        self.assertEqual(
            workflow_templates.tbody.tr[0].td[1].a.attrib["href"],
            "https://freexian-team.pages.debian.net/debusine/"
            "reference/workflows/specs/noop.html",
        )

    def test_detail_workflowtemplate_links(self) -> None:
        """Test links to workflow tables."""
        workflow_list_base_url = reverse(
            "workspaces:workflows:list",
            kwargs={"wname": self.workspace_public.name},
        )
        self.playground.create_workflow_template(
            name="noop-template",
            task_name="noop",
            workspace=self.workspace_public,
        )

        # 1 completed workflow
        self.playground.create_workflow(
            task_name="noop",
            status=WorkRequest.Statuses.COMPLETED,
        )

        response = self.client.get(self.workspace_public.get_absolute_url())
        tree = self.assertResponseHTML(response)

        div = self.assertHasElement(tree, "//div[@id='workflow-templates']")
        tr = div.table.tbody.tr[0]

        # Running workflows
        url = tr.td[2].a.get("href")
        assert url is not None
        self.assertIn(workflow_list_base_url, url)
        response = self.client.get(url)
        paginator = response.context["paginator"]
        self.assertEqual(
            paginator.table.filters["workflow_templates"].value, "noop-template"
        )
        self.assertEqual(
            paginator.table.filters["statuses"].value, ["running__any"]
        )

        # Input needed workflows
        url = tr.td[3].a.get("href")
        assert url is not None
        self.assertIn(workflow_list_base_url, url)
        response = self.client.get(url)
        paginator = response.context["paginator"]
        self.assertEqual(
            paginator.table.filters["workflow_templates"].value, "noop-template"
        )
        self.assertEqual(
            paginator.table.filters["statuses"].value, ["running__needs_input"]
        )

        # Completed workflows
        url = tr.td[4].a.get("href")
        assert url is not None
        self.assertIn(workflow_list_base_url, url)
        response = self.client.get(url)
        paginator = response.context["paginator"]
        self.assertEqual(
            paginator.table.filters["workflow_templates"].value, "noop-template"
        )
        self.assertEqual(
            paginator.table.filters["statuses"].value, ["completed"]
        )


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
        assert response.context_data is not None
        form = response.context_data["form"]
        self.assertTrue(form["public"].initial)
        self.assertIsNone(form["expiration_delay"].initial)
        self.assertEqual(form["default_expiration_delay"].initial, timedelta(0))


class TaskConfigurationInspectorTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`TaskConfigurationInspector` view."""

    scenario = scenarios.DefaultContext()
    collection: ClassVar[Collection]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.collection = cls.scenario.workspace.collections.get(
            name="default",
            category=CollectionCategory.TASK_CONFIGURATION,
        )

    @classmethod
    def add_config(cls, entry: DebusineTaskConfiguration) -> None:
        """Add a config entry to config_collection."""
        cls.collection.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=cls.scenario.user,
            data=entry,
        )

    def get(
        self,
        collections: list[Collection] | None = None,
        **kwargs: str,
    ) -> TestResponseType:
        workspace = self.scenario.workspace
        if collections is None:
            collections = [self.collection]
        url = reverse(
            "workspaces:task_configuration_inspector",
            kwargs={"wname": workspace.name},
        )

        with mock.patch(
            "debusine.db.models.workspaces."
            "Workspace.list_accessible_collections",
            return_value=collections,
        ):
            return self.client.get(url, kwargs)

    def assertHasInputContainer(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> lxml.objectify.ObjectifiedElement:
        """Return the main user input container element."""
        return self.assertHasElement(tree, "//div[@id='user-input']")

    def assertHasCollectionSelector(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> lxml.objectify.ObjectifiedElement:
        """Return the collection selector root element."""
        input_container = self.assertHasInputContainer(tree)
        return self.assertHasElement(
            input_container, "//div[@id='collection-selector']"
        )

    def assertCollectionSelected(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        collection: Collection,
        can_reselect: bool = False,
        is_empty: bool = False,
    ) -> None:
        """Ensure the given collection is currently selected."""
        input_container = self.assertHasInputContainer(tree)

        # Ensure the collection selector is not shown
        self.assertFalse(
            input_container.xpath("//div[@id='collection-selector']")
        )

        input_header = self.assertHasElement(
            input_container, "div[contains(@class, 'card-header')]"
        )
        input_title = self.assertHasElement(input_header, "*/h2")
        self.assertTextContentEqual(
            input_title,
            f"Collection {collection.name} in {collection.workspace}",
        )

        if can_reselect:
            reselect = self.assertHasElement(input_header, "a[@href='.']")
            self.assertEqual(reselect.get("title"), "select another collection")
        else:
            self.assertFalse(input_header.xpath("a[@href='.']"))

        input_body = self.assertHasElement(
            input_container, "div[contains(@class, 'card-body')]"
        )
        if is_empty:
            self.assertTextContentEqual(input_body, "This collection is empty")
        else:
            selected_collection = self.assertHasElement(
                input_body, "form/input[@name='collection']"
            )
            self.assertEqual(
                selected_collection.get("value"), str(collection.id)
            )

    def test_title(self) -> None:
        expected_title = (
            "Task configuration inspector for "
            f"{self.scenario.workspace.name} workspace"
        )

        response = self.get()
        tree = self.assertResponseHTML(response)
        title = self.assertHasElement(tree, "//head//title")
        self.assertTextContentEqual(title, f"Debusine - {expected_title}")
        h1 = self.assertHasElement(tree, "//body//h1")
        self.assertTextContentEqual(h1, expected_title)

    def test_collection_selector(self) -> None:
        ws_other = self.playground.create_workspace(name="Other", public=True)
        coll_other = self.playground.create_collection(
            "default", CollectionCategory.TASK_CONFIGURATION, workspace=ws_other
        )

        response = self.get(collections=[self.collection, coll_other])
        tree = self.assertResponseHTML(response)
        div = self.assertHasCollectionSelector(tree)
        self.assertEqual(
            div.a[0].get("href"), f"?collection={self.collection.pk}"
        )
        self.assertTextContentEqual(
            div.a[0], f"{self.collection.name} in {self.collection.workspace}"
        )
        self.assertEqual(div.a[1].get("href"), f"?collection={coll_other.pk}")
        self.assertTextContentEqual(
            div.a[1], f"{coll_other.name} in {self.collection.workspace}"
        )

    def test_collection_selected(self) -> None:
        ws_other = self.playground.create_workspace(name="Other", public=True)
        coll_other = self.playground.create_collection(
            "default", CollectionCategory.TASK_CONFIGURATION, workspace=ws_other
        )

        response = self.get(
            collections=[self.collection, coll_other],
            collection=str(coll_other.id),
        )
        tree = self.assertResponseHTML(response)
        self.assertCollectionSelected(
            tree, coll_other, can_reselect=True, is_empty=True
        )

    def test_collection_autoselect(self) -> None:
        response = self.get(collections=[self.collection])
        tree = self.assertResponseHTML(response)
        self.assertCollectionSelected(
            tree, self.collection, can_reselect=False, is_empty=True
        )

    def test_collection_lookup(self) -> None:
        item = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.add_config(item)
        response = self.get(
            collections=[self.collection], task=f"{TaskTypes.WORKER}:noop"
        )
        tree = self.assertResponseHTML(response)
        self.assertCollectionSelected(
            tree, self.collection, can_reselect=False, is_empty=False
        )

        results = self.assertHasElement(tree, "//div[@id='results']")

        default_values = self.assertHasElement(
            results, "div[@id='default-values']"
        )
        self.assertYAMLContentEqual(default_values.div[1], {})
        override_values = self.assertHasElement(
            results, "div[@id='override-values']"
        )
        self.assertYAMLContentEqual(override_values.div[1], {})
        configuration_items = self.assertHasElement(
            results, "div[@id='configuration-items']"
        )
        self.assertTextContentEqual(
            configuration_items.div[0].div[0], item.name()
        )
        self.assertYAMLContentEqual(
            configuration_items.div[0].div[1], item.dict()
        )

    def test_form_invalid(self) -> None:
        item = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.add_config(item)
        response = self.get(
            collections=[self.collection], task=f"{TaskTypes.WORKER}:invalid"
        )
        tree = self.assertResponseHTML(response)
        self.assertCollectionSelected(
            tree, self.collection, can_reselect=False, is_empty=False
        )

        self.assertFalse(tree.xpath("//div[@id='results']"))
