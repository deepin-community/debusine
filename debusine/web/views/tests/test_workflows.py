# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the workflow views."""

from typing import ClassVar
from unittest.mock import patch

import lxml
from django.urls import reverse
from django.utils import timezone

from debusine.db.models import WorkRequest, WorkflowTemplate
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.web.views.tests.utils import ViewTestMixin


class WorkflowListViewTests(ViewTestMixin, TestCase):
    """Tests for WorkRequestDetailView class."""

    template: ClassVar[WorkflowTemplate]
    workflow_1: ClassVar[WorkRequest]
    workflow_2: ClassVar[WorkRequest]

    scenario = scenarios.DefaultContext(set_current=True)

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up test data."""
        super().setUpTestData()

        cls.template = cls.playground.create_workflow_template(
            "name-2", "noop", workspace=cls.scenario.workspace
        )

        cls.workflow_1 = cls.playground.create_workflow(
            cls.template,
            task_data={},
        )
        cls.workflow_2 = cls.playground.create_workflow(
            cls.template,
            task_data={},
        )

    def assertWorkflowRow(
        self, row: lxml.objectify.ObjectifiedElement, workflow: WorkRequest
    ) -> None:
        """Ensure the row shows the given work request."""
        line1 = row.div[0]
        assert workflow.workflow_display_name_parameters
        self.assertTextContentEqual(
            line1.div[0], workflow.workflow_display_name_parameters
        )

        line2 = row.div[1]
        self.assertTextContentEqual(line2.div.a, f"#{workflow.id}")
        self.assertEqual(line2.div.a.get("href"), workflow.get_absolute_url())

    def test_can_display_used(self) -> None:
        """Permissions are used via can_display()."""
        self.client.force_login(self.scenario.user)

        with patch(
            "debusine.db.models.work_requests.WorkRequestQuerySet.can_display"
        ) as mock_can_display:
            mock_can_display.return_value = WorkRequest.objects.all()

            self.client.get(
                reverse(
                    "workspaces:workflows:list",
                    kwargs={"wname": self.scenario.workspace.name},
                ),
            )

        mock_can_display.assert_called_once()

    def test_no_workflows(self) -> None:
        """View details shows "No workflows" if no workflows in the space."""
        WorkRequest.objects.all().delete()

        response = self.client.get(
            reverse(
                "workspaces:workflows:list",
                kwargs={"wname": self.scenario.workspace.name},
            ),
        )

        self.assertContains(response, "<p>No workflows.</p>", html=True)

    def test_list_no_filtering_check_workspace(self) -> None:
        """View detail return all workflows for the specific workspace."""
        workspace_unused = self.playground.create_workspace(
            name="unused-workspace", public=True
        )
        # The following workflow is not in the workspace that will be used
        template_unused = self.playground.create_workflow_template(
            "name-1", "noop", workspace=workspace_unused
        )
        self.playground.create_workflow(template_unused, task_data={})

        wr = self.workflow_1.create_child("noop")
        wr.started_at = timezone.now()
        wr.save()

        self.workflow_1.started_at = timezone.now()
        self.workflow_1.save()

        self.workflow_2.completed_at = timezone.now()
        self.workflow_2.save()

        response = self.client.get(
            reverse(
                "workspaces:workflows:list",
                kwargs={"wname": self.scenario.workspace.name},
            ),
        )
        tree = self.assertResponseHTML(response)
        link = (
            "https://freexian-team.pages.debian.net/"
            "debusine/explanation/concepts.html#workflows"
        )
        self.assertContains(
            response,
            f'<p>This page lets you monitor '
            f'<a href="{link}">workflows</a> started in the '
            f'{self.scenario.workspace} workspace.</p>',
            html=True,
        )

        table = self.assertHasElement(tree, "//div[@id='workflow-list-table']")
        rows = self.assertHasElement(
            table, "div[@id='workflow-list-table-rows']"
        )

        # One work request is in a different workspace
        self.assertEqual(len(rows.div), 2)

        self.assertWorkflowRow(rows.div[0], self.workflow_2)
        self.assertWorkflowRow(rows.div[1], self.workflow_1)
