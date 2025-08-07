# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for task status view."""
from django.template.loader import get_template
from django.test import RequestFactory, override_settings
from django.urls import reverse

from debusine.db.context import context
from debusine.db.models import WorkRequest
from debusine.db.playground import scenarios
from debusine.server.status import TaskQueueSummary, WorkerSummary
from debusine.tasks.models import WorkerType
from debusine.test.django import TestCase
from debusine.web.views.task_status import TaskStatusView
from debusine.web.views.tests.utils import ViewTestMixin


class TaskStatusViewTests(ViewTestMixin, TestCase):
    """Tests for TaskStatusView."""

    scenario = scenarios.DefaultContext(set_current=True)

    def setUp(self) -> None:
        """Set up objects."""
        super().setUp()
        self.factory = RequestFactory()

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = TaskStatusView()
        self.assertEqual(view.get_title(), "Task queue")

    def test_template_no_work_requests(self) -> None:
        """Test template display the "No work requests..." message."""
        response = self.client.get(reverse("task-status"))

        self.assertContains(
            response, "<p>There are no pending work requests.</p>", html=True
        )

    def test_template_simple(self) -> None:
        """Test template row for arch-None without any tasks."""
        self.playground.create_worker(
            worker_type=WorkerType.EXTERNAL,
            extra_dynamic_metadata={
                "system:host_architecture": "amd64",
                "system:architectures": ["amd64", "i386"],
            },
        ).mark_connected()
        response = self.client.get(reverse("task-status"))
        tree = self.assertResponseHTML(response)
        arch_table = self.assertHasElement(
            tree, "//table[@id='task-arch-queue']"
        )
        self.assertHasElement(arch_table.tbody, "tr[@id='arch-_none_']")

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_template(self) -> None:
        """Test template contains expected output."""
        worker = self.playground.create_worker(
            worker_type=WorkerType.EXTERNAL,
            extra_dynamic_metadata={
                "system:host_architecture": "amd64",
                "system:architectures": ["amd64", "i386"],
            },
        )
        worker.mark_connected()

        self.playground.create_work_request(
            task_name="noop",
            task_data={"host_architecture": "amd64"},
            status=WorkRequest.Statuses.PENDING,
            worker=worker,
        )
        self.playground.create_work_request(
            task_name="noop",
            task_data={"host_architecture": "i386"},
            status=WorkRequest.Statuses.RUNNING,
        )
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(task_name="sbuild")

        response = self.client.get(reverse("task-status"))
        tree = self.assertResponseHTML(response)

        type_table = self.assertHasElement(tree, "//table[@id='task-queue']")
        row = self.assertHasElement(
            type_table.tbody, "tr[@id='worker_type-external']"
        )
        # Columns: type, registered, connected, busy, idle, running, pending
        self.assertHTMLContentsEquivalent(row.td[0], "<td>External</td>")
        self.assertHTMLContentsEquivalent(row.td[1], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[2], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[3], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[4], "<td>0</td>")
        self.assertHTMLContentsEquivalent(row.td[5], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[6], "<td>1</td>")

        arch_table = self.assertHasElement(
            tree, "//table[@id='task-arch-queue']"
        )

        # First row: architecture amd64, 1 connected worker, 1 work request
        row = self.assertHasElement(arch_table.tbody, "tr[@id='arch-amd64']")
        self.assertHTMLContentsEquivalent(row.td[0], '<td>amd64</td>')
        # Columns: registered, connected, busy, idle, running, pending
        self.assertHTMLContentsEquivalent(row.td[1], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[2], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[3], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[4], "<td>0</td>")
        self.assertHTMLContentsEquivalent(row.td[5], "<td>0</td>")
        self.assertHTMLContentsEquivalent(row.td[6], "<td>1</td>")

        # Second row: architecture i386, 1 connected worker, 0 work requests
        row = arch_table.tbody.xpath("tr[@id='arch-i386']")[0]
        self.assertHTMLContentsEquivalent(row.td[0], '<td>i386</td>')
        self.assertHTMLContentsEquivalent(row.td[1], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[2], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[3], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[4], "<td>0</td>")
        self.assertHTMLContentsEquivalent(row.td[5], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[6], "<td>0</td>")

        # Third row: arch unspecified, 1 connected worker, 2 work requests
        row = arch_table.tbody.xpath("tr[@id='arch-_none_']")[0]
        self.assertHTMLContentsEquivalent(row.td[0], "<td>Not Specified</td>")
        self.assertHTMLContentsEquivalent(row.td[1], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[2], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[3], "<td>1</td>")
        self.assertHTMLContentsEquivalent(row.td[4], "<td>0</td>")
        self.assertHTMLContentsEquivalent(row.td[5], "<td>0</td>")
        self.assertHTMLContentsEquivalent(row.td[6], "<td>2</td>")

        # Three rows for two architectures: amd64 and i386, then None
        self.assertEqual(len(arch_table.tbody.tr), 3)

    def test_task_queue_sorted_in_template(self) -> None:
        """task_queue is sorted by architecture in the template."""
        worker_status = {
            WorkerType.EXTERNAL: {
                "worker_tasks": TaskQueueSummary(),
                "workers": WorkerSummary(),
            },
        }
        # Create an unsorted arch_status dictionary
        arch_status = {
            "i386": {
                "external_workers_arch": WorkerSummary(connected=3),
                "worker_tasks_arch": TaskQueueSummary(pending=5),
            },
            "amd64": {
                "external_workers_arch": WorkerSummary(connected=2),
                "worker_tasks_arch": TaskQueueSummary(pending=3),
            },
        }
        template = get_template("web/task_status.html")
        # FIXME: template.render does not use context processors, so we need to
        # explicitly re-add here what the template expects. This may get more
        # complicated in the future, as the base template may get to rely more
        # on default context
        rendered_html = template.render(
            {
                "arch_status": arch_status,
                "worker_status": worker_status,
                "workspace": context.workspace,
                "base_template": "web/_base.html",
            }
        )

        i386_index = rendered_html.find("i386")
        amd64_index = rendered_html.find("amd64")

        self.assertLess(amd64_index, i386_index)
