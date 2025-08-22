# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test tables."""

from typing import Any, ClassVar

from django.test import RequestFactory

from debusine.artifacts.models import TaskTypes
from debusine.db.models import WorkRequest, Worker, WorkerPool, WorkflowTemplate
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.web.views.tables import (
    FilterFailedWorkRequests,
    WorkRequestTable,
    WorkerPoolTable,
    WorkerTable,
    WorkflowTable,
)


class WorkerTableTests(TestCase):
    """Tests for :py:class:`WorkerTable`."""

    def test_defaults(self) -> None:
        """Test default arguments for custom Pagination object."""
        request = RequestFactory().get("/")
        table = WorkerTable(request, Worker.objects.all())
        self.assertEqual(table.default_order, "name")
        self.assertEqual(
            [c.name for c in table.columns],
            ["type", "name", "pool", "last_seen", "status"],
        )
        self.assertEqual(table.template_name, "web/_worker-list.html")

    def test_worker_pool_options(self) -> None:
        """Test population of worker pool options."""
        pool = self.playground.create_worker_pool(name="testpool")
        self.playground.create_worker(worker_pool=pool)
        request = RequestFactory().get("/")
        table = WorkerTable(request, Worker.objects.all())
        f = table.filters["pool"]
        self.assertEqual(
            list(f.options.keys()),  # type: ignore[attr-defined]
            ["_none", str(pool.pk)],
        )


class WorkerPoolTableTests(TestCase):
    """Tests for :py:class:`WorkerPoolTable`."""

    def test_defaults(self) -> None:
        """Test default arguments for custom Pagination object."""
        request = RequestFactory().get("/")
        table = WorkerPoolTable(request, WorkerPool.objects.all())
        self.assertEqual(table.default_order, "name")
        self.assertEqual(
            [c.name for c in table.columns],
            ["name", "enabled", "instance_wide", "ephemeral", "workers"],
        )


class WorkRequestTableTests(TestCase):
    """Tests for :py:class:`WorkRequestTable`."""

    def test_defaults(self) -> None:
        """Test default arguments for custom Pagination object."""
        request = RequestFactory().get("/")
        table = WorkRequestTable(request, WorkRequest.objects.all())
        self.assertEqual(table.default_order, "-created_at")
        self.assertEqual(
            [c.name for c in table.columns],
            ["id", "created_at", "task_type", "task_name", "status", "result"],
        )


class FilterFailedWorkRequestsTests(TestCase):
    """Tests for :py:class:`FilterFailedWorkRequests`."""

    def test_filter_when_inactive(self) -> None:
        """Test calling filter_queryset with an inactive filter."""
        f = FilterFailedWorkRequests("Test")
        queryset = WorkRequest.objects.all()
        self.assertIs(f.filter_queryset(queryset, None), queryset)


class WorkflowTableTests(TestCase):
    """Tests for :py:class:`WorkflowTable`."""

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

    def _table(
        self, query_params: dict[str, Any] | None = None
    ) -> WorkflowTable:
        """Instantiate a table with the given query string parameters."""
        request = RequestFactory().get("/", data=query_params)
        return WorkflowTable(
            request,
            WorkRequest.objects.filter(
                task_type=TaskTypes.WORKFLOW, parent__isnull=True
            ),
        )

    def test_defaults(self) -> None:
        """Test default arguments for custom Pagination object."""
        table = self._table()
        self.assertEqual(table.default_order, "id.desc")
        self.assertEqual(
            [c.name for c in table.columns],
            [
                "id",
                "workflow_template",
                "status",
                "result",
                "started_at",
                "completed_at",
                "wr_count",
                "last_activity",
                "started_by",
            ],
        )

    def test_filter_form_with_issues(self) -> None:
        """Client submit filter form which is not valid."""
        table = self._table(
            {
                "filter-statuses": "does-not-exist",
                "filter-with_failed_work_requests": "1",
            }
        )
        f = table.filters["statuses"]
        self.assertIsNone(f.value)
        f = table.filters["with_failed_work_requests"]
        self.assertEqual(f.value, "1")

    def assert_filtered(self, workflow: WorkRequest, **kwargs: Any) -> None:
        """Make a request with the form and assert one workflow is listed."""
        table = self._table({f"filter-{k}": v for k, v in kwargs.items()})
        self.assertQuerySetEqual(table.rows, [workflow])

    def test_list_filtering_started_by(self) -> None:
        """View detail filters by started_by user."""
        user = self.playground.create_user(username="relevant")
        self.workflow_1.created_by = user
        self.workflow_1.save()
        self.assert_filtered(self.workflow_1, started_by=user.username)

    def test_list_filtering_status(self) -> None:
        """View detail filters by status."""
        self.workflow_1.status = WorkRequest.Statuses.COMPLETED
        self.workflow_1.save()
        self.assert_filtered(
            self.workflow_1, statuses=[WorkRequest.Statuses.COMPLETED]
        )

    def test_list_filtering_runtime_status(self) -> None:
        """View detail filters by runtime status."""
        self.workflow_1.status = WorkRequest.Statuses.RUNNING
        self.workflow_1.save()
        self.workflow_2.status = WorkRequest.Statuses.RUNNING
        self.workflow_2.save()
        self.workflow_1.create_child(
            "noop",
            status=WorkRequest.Statuses.BLOCKED,
        )
        self.assert_filtered(self.workflow_1, statuses=["running__blocked"])

    def test_list_filtering_runtime_any(self) -> None:
        """
        Test Workflow with running status is displayed.

        No need for any specific WorkRequest.workflow_running_status.
        """
        self.workflow_1.status = WorkRequest.Statuses.RUNNING
        self.workflow_1.save()
        self.workflow_1.create_child(
            "noop",
            status=WorkRequest.Statuses.BLOCKED,
        )
        self.assert_filtered(self.workflow_1, statuses=["running__any"])

    def test_list_filtering_result(self) -> None:
        """View detail filters by result."""
        self.workflow_1.result = WorkRequest.Results.SUCCESS
        self.workflow_1.save()
        self.assert_filtered(
            self.workflow_1, results=[WorkRequest.Results.SUCCESS]
        )

    def test_list_filtering_with_failed_work_requests(self) -> None:
        """View detail filters by with failed work requests."""
        wr = self.workflow_1.create_child("noop")
        wr.result = WorkRequest.Results.FAILURE
        wr.save()
        self.assert_filtered(self.workflow_1, with_failed_work_requests="1")

    def test_list_filtering_workflow_templates(self) -> None:
        """View detail filters by workflow_templates."""
        other_template = self.playground.create_workflow_template(
            "other_template", "noop"
        )
        self.workflow_2.workflow_template = other_template
        self.workflow_2.save()
        self.assert_filtered(
            self.workflow_1, workflow_templates=self.template.name
        )

    def test_sorting_id(self) -> None:
        """Test output of workflows for "id" and "-id" sorting."""
        table = self._table({"order": "id.desc"})
        self.assertQuerySetEqual(table.rows, [self.workflow_2, self.workflow_1])
        table = self._table({"order": "id.asc"})
        self.assertQuerySetEqual(table.rows, [self.workflow_1, self.workflow_2])
