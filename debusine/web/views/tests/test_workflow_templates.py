# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the workflow templates views."""

from typing import ClassVar

from rest_framework import status

from debusine.db.models import WorkflowTemplate
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.web.views.tests.utils import ViewTestMixin


class WorkflowTemplateDetailViewTests(ViewTestMixin, TestCase):
    """Tests for WorkflowTemplateDetailView class."""

    template: ClassVar[WorkflowTemplate]
    scenario = scenarios.DefaultContext(set_current=True)

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up test data."""
        super().setUpTestData()

        cls.template = cls.playground.create_workflow_template(
            "name-1",
            "noop",
            workspace=cls.scenario.workspace,
            task_data={"succeeds": "True"},
        )

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            self.template.get_absolute_url(),
        )
        self.assertEnforcesPermission(
            self.template.can_display,
            self.template.get_absolute_url(),
            "get_context_data",
        )

    def test_detail_view(self) -> None:
        """Test detail view."""
        response = self.client.get(self.template.get_absolute_url())
        tree = self.assertResponseHTML(response)

        h1 = self.assertHasElement(tree, "//h1[1]")
        self.assertEqual(h1, f"Workflow template {self.template.name}")

        div = self.assertHasElement(tree, '//div[@id="workflow-information"]')
        self.assertTextContentEqual(
            div.div,
            f"{self.template.name} "
            f"(type {self.template.task_name}, "
            f"priority {self.template.priority})",
        )
        self.assertEqual(div.div.a, self.template.task_name)
        self.assertEqual(
            div.div.a.attrib["href"],
            f"https://freexian-team.pages.debian.net/debusine/"
            f"reference/workflows/specs/{self.template.task_name}.html",
        )

    def test_namespaced_by_workspace(self) -> None:
        # Create another workflow template with the same name, in a
        # neighbouring workspace
        self.playground.create_workflow_template(
            self.template.name,
            "noop",
            workspace=self.playground.create_workspace(name="another"),
            task_data={"succeeds": "True"},
        )
        response = self.client.get(self.template.get_absolute_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_does_not_exist(self) -> None:
        """A nonexistent workflow template returns 404."""
        response = self.client.get(
            f"/{self.scenario.workspace}/workflow-template/nonexistent/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No WorkflowTemplate matches the given query.",
        )
