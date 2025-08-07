# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests related to workers views."""

import datetime
from typing import ClassVar

from django.urls import reverse
from rest_framework import status

from debusine.db.models import WorkRequest, Worker
from debusine.db.playground import scenarios
from debusine.tasks.models import WorkerType
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)
from debusine.web.icons import Icons
from debusine.web.views.tests.utils import ViewTestMixin


class WorkersListViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkersListView`."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_template_with_workers(self) -> None:
        """Test template contains expected information."""
        worker_external = self.playground.create_worker()
        worker_external.name = "a-first-one"
        worker_external.concurrency = 3
        worker_external.save()
        worker_external.mark_connected()

        worker_celery = self.playground.create_worker(
            worker_type=WorkerType.CELERY
        )
        worker_celery.name = "b-second-one"
        worker_celery.save()

        worker_signing = self.playground.create_worker(
            worker_type=WorkerType.SIGNING
        )
        worker_signing.name = "c-third-one"
        worker_signing.save()

        work_request_1 = self.playground.create_work_request(
            task_name="noop",
            worker=worker_external,
            mark_running=True,
            workspace=self.scenario.workspace,
        )
        work_request_2 = self.playground.create_work_request(
            task_name="noop",
            worker=worker_external,
            mark_running=True,
            workspace=self.scenario.workspace,
        )

        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        # Assert names
        self.assertEqual(table.tbody.tr[0].td[0].i.get("title"), "External")
        self.assertEqual(
            table.tbody.tr[0].td[0].i.get("class"),
            f"bi bi-{Icons.WORKER_EXTERNAL}",
        )
        self.assertEqual(table.tbody.tr[1].td[0].i.get("title"), "Celery")
        self.assertEqual(
            table.tbody.tr[1].td[0].i.get("class"),
            f"bi bi-{Icons.WORKER_CELERY}",
        )
        self.assertEqual(table.tbody.tr[2].td[0].i.get("title"), "Signing")
        self.assertEqual(
            table.tbody.tr[2].td[0].i.get("class"),
            f"bi bi-{Icons.WORKER_SIGNING}",
        )

        # Assert status for external worker: "Running ..."
        path_1 = work_request_1.get_absolute_url()
        path_2 = work_request_2.get_absolute_url()
        external_running = (
            f'<td> '
            f'<a href="{path_1}">noop</a><br/>'
            f'<a href="{path_2}">noop</a><br/>'
            f'</td>'
        )
        self.assertHTMLContentsEquivalent(
            table.tbody.tr[0].td[4], external_running
        )

    def test_status_disabled(self) -> None:
        """View shows "Disabled" (worker's token is disabled)."""
        worker = self.playground.create_worker()
        assert worker.token is not None
        worker.token.enabled = False
        worker.token.save()

        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        self.assertHTMLContentsEquivalent(
            table.tbody.tr[0].td[4],
            '<td><span class="badge text-bg-danger">Disabled</span></td>',
        )

    def test_status_idle(self) -> None:
        """View shows "Idle" (enabled worker, not running any work request)."""
        worker = self.playground.create_worker()
        worker.mark_connected()

        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        self.assertHTMLContentsEquivalent(
            table.tbody.tr[0].td[4],
            '<td> <span class="badge text-bg-success">Idle</span> </td>',
        )

    def test_status_disconnected(self) -> None:
        """View shows "Disconnected" (enabled worker, not connected)."""
        self.playground.create_worker()

        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        self.assertHTMLContentsEquivalent(
            table.tbody.tr[0].td[4],
            '<td> <span class="badge text-bg-warning">'
            'Disconnected</span> </td>',
        )

    def test_template_no_workers(self) -> None:
        """Test template renders "No workers registered in this..."."""
        response = self.client.get(reverse("workers:list"))

        self.assertContains(
            response,
            "<p>No workers registered in this debusine instance.</p>",
            html=True,
        )

    def test_template_with_private_work_request_anonymous(self) -> None:
        """Test template hides private work requests from anonymous viewers."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()

        worker_external = self.playground.create_worker()
        worker_external.name = "a-first-one"
        worker_external.concurrency = 3
        worker_external.save()
        worker_external.mark_connected()

        self.playground.create_work_request(
            task_name="noop",
            worker=worker_external,
            mark_running=True,
            workspace=self.scenario.workspace,
        )

        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        # Assert status for external worker: "Running ..."
        external_running = '<td>Private Task<br/></td>'
        self.assertHTMLContentsEquivalent(
            table.tbody.tr[0].td[4], external_running
        )

    def test_template_with_private_work_request_authenticated(self) -> None:
        """Test template shows private work requests to the authenticated."""
        worker_external = self.playground.create_worker()
        worker_external.name = "a-first-one"
        worker_external.concurrency = 3
        worker_external.save()
        worker_external.mark_connected()

        work_request = self.playground.create_work_request(
            task_name="noop",
            worker=worker_external,
            mark_running=True,
            workspace=self.scenario.workspace,
        )

        self.client.force_login(self.scenario.user)
        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        # Assert status for external worker: "Running ..."
        path = work_request.get_absolute_url()
        external_running = f'<td><a href="{path}">noop</a><br/></td>'
        self.assertHTMLContentsEquivalent(
            table.tbody.tr[0].td[4], external_running
        )

    def test_celery_worker(self) -> None:
        """Template shows "-" in Last Seen and Status for Celery workers."""
        self.playground.create_worker(WorkerType.CELERY)

        response = self.client.get(reverse("workers:list"))
        tree = self.assertResponseHTML(response)

        table = self.assertHasElement(tree, "//table[@id='worker-list']")

        # Last Seen At is "-"
        self.assertHTMLContentsEquivalent(table.tbody.tr[0].td[4], "<td>-</td>")

        # Status is "-"
        self.assertHTMLContentsEquivalent(table.tbody.tr[0].td[4], "<td>-</td>")


class WorkersListViewOrderingTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkersListView` ordering."""

    scenario = scenarios.DefaultContext(set_current=True)
    worker_external: ClassVar[Worker]
    worker_celery: ClassVar[Worker]
    worker_signing: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.worker_external = cls.playground.create_worker()
        cls.worker_external.name = "a-external"
        cls.worker_external.concurrency = 3
        cls.worker_external.save()
        cls.worker_external.mark_connected()

        cls.worker_celery = cls.playground.create_worker(
            worker_type=WorkerType.CELERY
        )
        cls.worker_celery.name = "b-celery"
        cls.worker_celery.save()

        cls.worker_signing = cls.playground.create_worker(
            worker_type=WorkerType.SIGNING
        )
        cls.worker_signing.name = "c-signing"
        cls.worker_signing.save()

    def assertTableEntries(
        self, response: TestResponseType, expected: list[str]
    ) -> None:
        """Check that the workers in the table match the given list of names."""
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker-list']")
        actual: list[str] = []
        for tr in table.tbody.tr:
            actual.append(self.get_node_text_normalized(tr.td[1]))
        self.assertEqual(actual, expected)

    def test_order_ascending(self) -> None:
        """Test ordering alphabetically."""
        response = self.client.get(reverse("workers:list"), {"order": "name"})
        self.assertTableEntries(
            response, ["a-external", "b-celery", "c-signing"]
        )

    def test_order_descending(self) -> None:
        """Test ordering reverse-alphabetically."""
        response = self.client.get(
            reverse("workers:list"), {"order": "name.desc"}
        )
        self.assertTableEntries(
            response, ["c-signing", "b-celery", "a-external"]
        )


class WorkerDetailViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkerDetailView`."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_authenticated(self) -> None:
        """Test a GET with an authenticated user."""
        worker = self.playground.create_worker()
        self.client.force_login(self.scenario.user)
        response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)

        el = self.assertHasElement(tree, "//tr[@id='row-name']")
        self.assertTextContentEqual(el.td[0], worker.name)
        el = self.assertHasElement(tree, "//tr[@id='row-worker_type']")
        self.assertTextContentEqual(el.td[0], worker.worker_type)
        el = self.assertHasElement(tree, "//tr[@id='row-registered_at']")
        self.assertDatetimeContentEqual(
            el.td[0], worker.registered_at, anywhere=True
        )
        el = self.assertHasElement(tree, "//tr[@id='row-connected_at']")
        self.assertTextContentEqual(el.td[0], "never")
        el = self.assertHasElement(tree, "//tr[@id='row-instance_created_at']")
        self.assertTextContentEqual(el.td[0], "never")
        el = self.assertHasElement(tree, "//tr[@id='row-concurrency']")
        self.assertTextContentEqual(el.td[0], str(worker.concurrency))

        el = self.assertHasElement(tree, "//tr[@id='row-metadata']")
        body = self.assertHasElement(
            el, ".//div[contains(@class, 'card-body')]"
        )
        self.assertYAMLContentEqual(body, worker.metadata())
        footer = self.assertHasElement(
            el, ".//div[contains(@class, 'card-footer')]"
        )
        assert worker.dynamic_metadata_updated_at is not None
        self.assertDatetimeContentEqual(
            footer.xpath("//em")[0],
            worker.dynamic_metadata_updated_at,
            anywhere=True,
        )

        self.assertFalse(tree.xpath("//tr[@id='row-worker_pool']"))
        self.assertFalse(tree.xpath("//table[@id='work_request-list']"))

    def test_dates(self) -> None:
        """Test a GET with all dates present."""
        worker = self.playground.create_worker()
        worker.instance_created_at = datetime.datetime(
            2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC
        )
        worker.dynamic_metadata_updated_at = datetime.datetime(
            2024, 2, 2, 0, 0, 0, tzinfo=datetime.UTC
        )
        worker.mark_connected()
        worker.save()
        assert worker.connected_at is not None
        self.client.force_login(self.scenario.user)
        response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)

        el = self.assertHasElement(tree, "//tr[@id='row-registered_at']")
        self.assertDatetimeContentEqual(
            el.td[0], worker.registered_at, anywhere=True
        )

        el = self.assertHasElement(tree, "//tr[@id='row-connected_at']")
        self.assertDatetimeContentEqual(
            el.td[0], worker.connected_at, anywhere=True
        )

        el = self.assertHasElement(tree, "//tr[@id='row-instance_created_at']")
        self.assertDatetimeContentEqual(
            el.td[0], worker.instance_created_at, anywhere=True
        )

        el = self.assertHasElement(tree, "//tr[@id='row-metadata']")
        footer = self.assertHasElement(
            el, ".//div[contains(@class, 'card-footer')]"
        )
        self.assertDatetimeContentEqual(
            footer.xpath("//em")[0],
            worker.dynamic_metadata_updated_at,
            anywhere=True,
        )

    def test_no_dynamic_metadata_updated_at(self) -> None:
        """Test a GET with no dynamic_metadata_updated_at."""
        worker = self.playground.create_worker()
        worker.dynamic_metadata_updated_at = None
        worker.save()
        self.client.force_login(self.scenario.user)
        response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)
        el = self.assertHasElement(tree, "//tr[@id='row-metadata']")
        footer = self.assertHasElement(
            el, ".//div[contains(@class, 'card-footer')]"
        )
        self.assertTextContentEqual(footer, "Dynamic metadata never updated.")

    def test_worker_pool(self) -> None:
        """Check presentation of worker pool information."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.worker_pool_data = {"foo": "bar"}
        worker.save()
        assert worker.worker_pool is not None
        self.client.force_login(self.scenario.user)
        response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)
        td = self.assertHasElement(tree, "//tr[@id='row-worker_pool']/td")
        card = self.assertHasElement(td, "div[@class='card']")
        self.assertTextContentEqual(card.div[0], worker.worker_pool.name)
        self.assertYAMLContentEqual(card.div[1], worker.worker_pool_data)

    def test_worker_pool_no_data(self) -> None:
        """Check worker pool information without worker_pool_data."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.worker_pool_data = None
        worker.save()
        assert worker.worker_pool is not None
        self.client.force_login(self.scenario.user)
        response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)
        td = self.assertHasElement(tree, "//tr[@id='row-worker_pool']/td")
        self.assertFalse(td.xpath("div[@class='card']"))
        self.assertTextContentEqual(td, worker.worker_pool.name)

    def test_worker_pool_anonymous(self) -> None:
        """Anonymous users cannot see worker_pool_data."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.worker_pool_data = {"foo": "bar"}
        worker.save()
        assert worker.worker_pool is not None
        response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)
        td = self.assertHasElement(tree, "//tr[@id='row-worker_pool']/td")
        self.assertFalse(td.xpath("div[@class='card']"))
        self.assertTextContentEqual(td, worker.worker_pool.name)

    def test_work_requests_visible(self) -> None:
        """Check the list of assigned work requests."""
        worker = self.playground.create_worker()
        work_request = self.playground.create_work_request()
        work_request.assign_worker(worker)

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_display", AllowAll):
            response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='work_request-list']")
        self.assertEqual(len(table.tbody.tr), 1)

    def test_work_requests_private(self) -> None:
        """Check that private work requests are hidden."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        worker = self.playground.create_worker()
        work_request = self.playground.create_work_request()
        work_request.assign_worker(worker)

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_display", DenyAll):
            response = self.client.get(worker.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//table[@id='work_request-list']"))
