# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests related to worker pool views."""

from typing import ClassVar

from django.urls import reverse

from debusine.db.models import WorkerPool
from debusine.db.playground import scenarios
from debusine.test.django import TestCase, TestResponseType
from debusine.web.views.tests.utils import ViewTestMixin


class WorkerPoolListViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkerPoolListView`."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_template_with_workerpools(self) -> None:
        """Test template contains expected information."""
        pool1 = self.playground.create_worker_pool(name="first")
        pool2 = self.playground.create_worker_pool(
            name="second", provider_account=pool1.provider_account
        )

        self.playground.create_worker(worker_pool=pool1)
        self.playground.create_worker(worker_pool=pool2)
        self.playground.create_worker(worker_pool=pool2)

        response = self.client.get(reverse("worker-pools:list"))
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker_pool-list']")

        # Name
        self.assertTextContentEqual(table.tbody.tr[0].td[0], "first")
        self.assertEqual(
            table.tbody.tr[0].td[0].a.get("href"), pool1.get_absolute_url()
        )
        self.assertTextContentEqual(table.tbody.tr[1].td[0], "second")
        self.assertEqual(
            table.tbody.tr[1].td[0].a.get("href"), pool2.get_absolute_url()
        )
        # Enabled
        self.assertTextContentEqual(table.tbody.tr[0].td[1], "yes")
        self.assertTextContentEqual(table.tbody.tr[1].td[1], "yes")
        # Instance-wide
        self.assertTextContentEqual(table.tbody.tr[0].td[2], "yes")
        self.assertTextContentEqual(table.tbody.tr[1].td[2], "yes")
        # Ephemeral
        self.assertTextContentEqual(table.tbody.tr[0].td[3], "no")
        self.assertTextContentEqual(table.tbody.tr[1].td[3], "no")
        # Workers
        self.assertTextContentEqual(table.tbody.tr[0].td[4], "1")
        self.assertTextContentEqual(table.tbody.tr[1].td[4], "2")

        self.assertFalse(tree.xpath("//p[@id='worker_pool-list-empty']"))

    def test_template_no_workerpools(self) -> None:
        """Test template renders "No worker pools registered in this..."."""
        response = self.client.get(reverse("worker-pools:list"))
        tree = self.assertResponseHTML(response)
        p = self.assertHasElement(tree, "//p[@id='worker_pool-list-empty']")
        self.assertTextContentEqual(
            p, "No worker pools registered in this debusine instance."
        )


class WorkerPoolsListViewOrderingTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkerPoolListView` ordering."""

    scenario = scenarios.DefaultContext(set_current=True)
    pool1: ClassVar[WorkerPool]
    pool2: ClassVar[WorkerPool]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.pool1 = cls.playground.create_worker_pool(name="first")
        cls.pool2 = cls.playground.create_worker_pool(
            name="second", provider_account=cls.pool1.provider_account
        )

        cls.playground.create_worker(worker_pool=cls.pool1)
        cls.playground.create_worker(worker_pool=cls.pool1)
        cls.playground.create_worker(worker_pool=cls.pool2)

    def assertTableEntries(
        self, response: TestResponseType, expected: list[str]
    ) -> None:
        """Check that the workers in the table match the given list of names."""
        tree = self.assertResponseHTML(response)
        table = self.assertHasElement(tree, "//table[@id='worker_pool-list']")
        actual: list[str] = []
        for tr in table.tbody.tr:
            actual.append(self.get_node_text_normalized(tr.td[0]))
        self.assertEqual(actual, expected)

    def test_name_ascending(self) -> None:
        """Sort by name ascending."""
        response = self.client.get(
            reverse("worker-pools:list"), {"order": "name"}
        )
        self.assertTableEntries(response, ["first", "second"])

    def test_name_descending(self) -> None:
        """Sort by name descending."""
        response = self.client.get(
            reverse("worker-pools:list"), {"order": "name.desc"}
        )
        self.assertTableEntries(response, ["second", "first"])

    def test_workers_ascending(self) -> None:
        """Sort by workers ascending."""
        response = self.client.get(
            reverse("worker-pools:list"), {"order": "workers"}
        )
        self.assertTableEntries(response, ["second", "first"])

    def test_workers_descending(self) -> None:
        """Sort by workers descending."""
        response = self.client.get(
            reverse("worker-pools:list"), {"order": "workers.desc"}
        )
        self.assertTableEntries(response, ["first", "second"])


class WorkerPoolDetailViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkerPoolDetailView`."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_default(self) -> None:
        """Test a GET with an default playground pool."""
        wp = self.playground.create_worker_pool()
        self.client.force_login(self.scenario.user)
        response = self.client.get(wp.get_absolute_url())
        tree = self.assertResponseHTML(response)

        el = self.assertHasElement(tree, "//tr[@id='row-name']")
        self.assertTextContentEqual(el.td[0], wp.name)

        el = self.assertHasElement(tree, "//tr[@id='row-provider_type']")
        self.assertTextContentEqual(
            el.td[0], wp.provider_account.data["provider_type"]
        )

        el = self.assertHasElement(tree, "//tr[@id='row-enabled']")
        self.assertTextContentEqual(el.td[0], "yes" if wp.enabled else "no")

        el = self.assertHasElement(tree, "//tr[@id='row-instance_wide']")
        self.assertTextContentEqual(
            el.td[0], "yes" if wp.instance_wide else "no"
        )

        el = self.assertHasElement(tree, "//tr[@id='row-ephemeral']")
        self.assertTextContentEqual(el.td[0], "yes" if wp.ephemeral else "no")

        el = self.assertHasElement(tree, "//tr[@id='row-registered_at']")
        self.assertDatetimeContentEqual(el, wp.registered_at, anywhere=True)

        el = self.assertHasElement(tree, "//tr[@id='row-architectures']")
        self.assertTextContentEqual(el.td[0], ", ".join(wp.architectures))

        el = self.assertHasElement(tree, "//tr[@id='row-tags']")
        self.assertTextContentEqual(el.td[0], "none")

        el = self.assertHasElement(tree, "//tr[@id='row-specifications']")
        self.assertYAMLContentEqual(el.td[0], wp.specifications)

        el = self.assertHasElement(tree, "//tr[@id='row-limits']")
        self.assertYAMLContentEqual(el.td[0], wp.limits)

        self.assertFalse(tree.xpath("//table[@id='worker-list']"))

    def test_no_architectures(self) -> None:
        """Test with no architectures."""
        wp = self.playground.create_worker_pool()
        wp.architectures = []
        wp.save()
        self.client.force_login(self.scenario.user)
        response = self.client.get(wp.get_absolute_url())
        tree = self.assertResponseHTML(response)
        el = self.assertHasElement(tree, "//tr[@id='row-architectures']")
        self.assertTextContentEqual(el.td[0], "none")

    def test_tags(self) -> None:
        """Test with tags."""
        wp = self.playground.create_worker_pool()
        wp.tags = ["foo", "bar"]
        wp.save()
        self.client.force_login(self.scenario.user)
        response = self.client.get(wp.get_absolute_url())
        tree = self.assertResponseHTML(response)
        el = self.assertHasElement(tree, "//tr[@id='row-tags']")
        self.assertTextContentEqual(el.td[0], "bar, foo")

    def test_workers(self) -> None:
        """Check presentation of worker information."""
        wp = self.playground.create_worker_pool()
        self.playground.create_worker(worker_pool=wp)
        self.playground.create_worker(worker_pool=wp)

        self.client.force_login(self.scenario.user)
        response = self.client.get(wp.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertHasElement(tree, "//table[@id='worker-list']")
