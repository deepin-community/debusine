# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the UIShortcuts base view."""

from django.template import Context
from django.utils.safestring import SafeString

from debusine.db.models import Artifact, User, WorkRequest
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.web.icons import Icons
from debusine.web.views import sidebar
from debusine.web.views.base import Widget
from debusine.web.views.sidebar import SidebarAction, SidebarElement


class TestSidebar(TestCase):
    """Tests for Sidebar."""

    scenario = scenarios.DefaultContext()

    def _render(self, widget: Widget) -> str | SafeString:
        """Render a widget."""
        return widget.render(Context())

    def test_info(self) -> None:
        """Test selection of information for UI."""
        kwargs = {"icon": "I", "label": "L"}

        el = SidebarElement(label="L")
        self.assertEqual(el.icon, "app")
        self.assertEqual(el.content, "-")
        self.assertEqual(el.main_tooltip, "L")
        self.assertIsNone(el.icon_tooltip)

        el = SidebarElement(**kwargs)
        self.assertEqual(el.icon, "I")
        self.assertEqual(el.content, "-")
        self.assertEqual(el.main_tooltip, "L")
        self.assertIsNone(el.icon_tooltip)

        el = SidebarElement(value="V", **kwargs)
        self.assertEqual(el.icon, "I")
        self.assertEqual(el.content, "V")
        self.assertEqual(el.main_tooltip, "L")
        self.assertIsNone(el.icon_tooltip)

        el = SidebarElement(detail="D", **kwargs)
        self.assertEqual(el.icon, "I")
        self.assertEqual(el.content, "D")
        self.assertEqual(el.main_tooltip, "L")
        self.assertIsNone(el.icon_tooltip)

        el = SidebarElement(value="V", detail="D", **kwargs)
        self.assertEqual(el.icon, "I")
        self.assertEqual(el.content, "V")
        self.assertEqual(el.main_tooltip, "D")
        self.assertEqual(el.icon_tooltip, "L")

    def test_render_action(self) -> None:
        """Test SidebarAction rendering."""
        kwargs = {"icon": "I", "label": "L", "url": "U"}
        classes = (
            '"list-group-item list-group-item-action'
            ' d-flex justify-content-between"'
        )

        el = SidebarAction(detail="D", value="V", **kwargs)
        self.assertEqual(
            self._render(el),
            f'<a class={classes} title="D" href="U">'
            '<span><span class="bi bi-I" title="L"></span> V</span>'
            '<span>…</span></a>',
        )

        el = SidebarAction(detail="D", **kwargs)
        self.assertEqual(
            self._render(el),
            f'<a class={classes} title="L" href="U">'
            '<span><span class="bi bi-I"></span> D</span>'
            '<span>…</span></a>',
        )

        el = SidebarAction(value="V", **kwargs)
        self.assertEqual(
            self._render(el),
            f'<a class={classes} title="L" href="U">'
            '<span><span class="bi bi-I"></span> V</span>'
            '<span>…</span></a>',
        )

        el = SidebarAction(**kwargs)
        self.assertEqual(
            self._render(el),
            f'<a class={classes} title="L" href="U">'
            '<span><span class="bi bi-I"></span> -</span>'
            '<span>…</span></a>',
        )

    def test_render_element(self) -> None:
        """Test SidebarElement rendering."""
        kwargs = {"icon": "I", "label": "L"}

        el = SidebarElement(detail="D", value="V", **kwargs)
        self.assertEqual(
            self._render(el),
            '<span class="list-group-item" title="D">'
            '<span class="bi bi-I" title="L"></span> V</span>',
        )

        el = SidebarElement(detail="D", **kwargs)
        self.assertEqual(
            self._render(el),
            '<span class="list-group-item" title="L">'
            '<span class="bi bi-I"></span> D</span>',
        )

        el = SidebarElement(value="V", **kwargs)
        self.assertEqual(
            self._render(el),
            '<span class="list-group-item" title="L">'
            '<span class="bi bi-I"></span> V</span>',
        )

        el = SidebarElement(**kwargs)
        self.assertEqual(
            self._render(el),
            '<span class="list-group-item" title="L">'
            '<span class="bi bi-I"></span> -</span>',
        )

    def test_work_request_status(self) -> None:
        """Test create_work_request_status."""
        wr = WorkRequest(status=WorkRequest.Statuses.PENDING)
        el = sidebar.create_work_request_status(wr)
        self.assertEqual(
            el.value, '<span class="badge text-bg-secondary">Pending</span>'
        )

        wr = WorkRequest(status=WorkRequest.Statuses.BLOCKED)
        el = sidebar.create_work_request_status(wr)
        self.assertEqual(
            el.value, '<span class="badge text-bg-dark">Blocked</span>'
        )

        wr.status, wr.result = (
            WorkRequest.Statuses.COMPLETED,
            WorkRequest.Results.FAILURE,
        )
        el = sidebar.create_work_request_status(wr)
        self.assertEqual(
            el.value,
            '<span class="badge text-bg-primary">Completed</span>'
            ' <span class="badge text-bg-warning">Failure</span>',
        )

        wr.status, wr.result = (
            WorkRequest.Statuses.COMPLETED,
            WorkRequest.Results.SKIPPED,
        )
        el = sidebar.create_work_request_status(wr)
        self.assertEqual(
            el.value,
            '<span class="badge text-bg-primary">Completed</span>'
            ' <span class="badge text-bg-light">Skipped</span>',
        )

        # This case does not make sense but it must be rendered somewhat
        wr.status, wr.result = (
            WorkRequest.Statuses.ABORTED,
            WorkRequest.Results.ERROR,
        )
        el = sidebar.create_work_request_status(wr)
        self.assertEqual(
            el.value,
            '<span class="badge text-bg-dark">Aborted</span>'
            ' <span class="badge text-bg-danger">Error</span>',
        )

    def test_user(self) -> None:
        """Test create_user."""
        artifact = Artifact()
        work_request = WorkRequest(status=WorkRequest.Statuses.PENDING)
        user = User(username="test")

        el = sidebar.create_user(user)
        self.assertEqual(el.label, "User")
        self.assertEqual(el.value, "test")

        el = sidebar.create_user(user, context=artifact)
        self.assertEqual(el.label, "User who created the artifact")
        self.assertEqual(el.value, "test")

        el = sidebar.create_user(user, context=work_request)
        self.assertEqual(el.label, "User who created the work request")
        self.assertEqual(el.value, "test")

    def test_supersede(self) -> None:
        """Test create_user."""
        wr_old = WorkRequest(pk=1, workspace=self.scenario.workspace)
        wr_new = WorkRequest(pk=2, workspace=self.scenario.workspace)
        wr_old.superseded = wr_new

        el = sidebar.create_work_request_superseded(wr_old)
        self.assertEqual(el.value, "2")
        el = sidebar.create_work_request_supersedes(wr_old)
        self.assertIsNone(el.value)
        el = sidebar.create_work_request_superseded(wr_new)
        self.assertIsNone(el.value)
        el = sidebar.create_work_request_supersedes(wr_new)
        self.assertEqual(el.value, "1")


class TestCreateWorker(TestCase):
    """Test :py:func:`create_worker`."""

    def test_none(self) -> None:
        """Test with no worker."""
        item = sidebar.create_worker(None)
        self.assertEqual(item.label, "Worker")
        self.assertEqual(item.icon, Icons.WORKER)
        self.assertIsNone(item.value)
        self.assertFalse(hasattr(item, "url"))

    def test_worker(self) -> None:
        """Test with a worker."""
        worker = self.playground.create_worker()
        item = sidebar.create_worker(worker)
        assert isinstance(item, SidebarAction)
        self.assertEqual(item.label, "Worker")
        self.assertEqual(item.icon, Icons.WORKER)
        self.assertEqual(item.value, worker.name)
        self.assertEqual(item.url, worker.get_absolute_url())
