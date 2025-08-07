# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the UIShortcuts base view."""

from typing import ClassVar

from django.template import Context
from django.utils.safestring import SafeString

from debusine.db.context import context
from debusine.db.models import Artifact, WorkRequest
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.web.templatetags.debusine import (
    ui_shortcuts as template_ui_shortcuts,
)
from debusine.web.views import ui_shortcuts
from debusine.web.views.base import Widget
from debusine.web.views.base_rightbar import RightbarUIView


class TestUIShortcuts(TestCase):
    """Tests for UIShortcut."""

    scenario = scenarios.DefaultScopeUser()
    work_request: ClassVar[WorkRequest]
    source: ClassVar[Artifact]
    buildlog: ClassVar[Artifact]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up test fixture."""
        super().setUpTestData()
        cls.work_request = cls.playground.create_work_request()
        cls.source = cls.playground.create_source_artifact()
        cls.buildlog = cls.playground.create_build_log_artifact(
            work_request=cls.work_request
        )

    def _render(self, widget: Widget) -> str | SafeString:
        """Render a widget."""
        return widget.render(Context())

    def test_render(self) -> None:
        """Test rendering to HTML."""
        shortcut = ui_shortcuts.UIShortcut(
            label="LABEL", icon="ICON", url="URL"
        )
        self.assertEqual(
            self._render(shortcut),
            "<a class='btn btn-outline-secondary'"
            " href='URL' title='LABEL'>"
            "<span class='bi bi-ICON'></span>"
            "</a>",
        )

    def test_work_request_view(self) -> None:
        """Test create_work_request_view."""
        action = ui_shortcuts.create_work_request_view(self.work_request)
        self.assertEqual(action.label, "View work request")
        self.assertEqual(action.icon, "hammer")
        self.assertEqual(action.url, self.work_request.get_absolute_url())

    def test_artifact_view(self) -> None:
        """Test create_artifact_view."""
        action = ui_shortcuts.create_artifact_view(self.source)
        self.assertEqual(action, ui_shortcuts.create_artifact_view(self.source))

        action = ui_shortcuts.create_artifact_view(self.buildlog)
        self.assertEqual(
            action, ui_shortcuts.create_artifact_view(self.buildlog)
        )

    def test_artifact_download(self) -> None:
        """Test create_artifact_download."""
        action = ui_shortcuts.create_artifact_download(self.source)
        self.assertEqual(action.label, "Download artifact")
        self.assertEqual(action.icon, "download")
        self.assertEqual(action.url, self.source.get_absolute_url_download())

    def test_ui_shortcuts_in_context_data(self) -> None:
        """Test that UI shortcuts are added to context."""
        self.scenario.set_current()
        view = RightbarUIView()
        actions = view.get_context_data()
        self.assertEqual(actions["main_ui_shortcuts"], [])

    def test_object_actions(self) -> None:
        """Test that stored object actions can be retrieved."""
        action1 = ui_shortcuts.create_artifact_view(self.buildlog)
        action2 = ui_shortcuts.create_artifact_download(self.buildlog)
        view = RightbarUIView()
        view.add_object_ui_shortcuts(self.buildlog, action1)
        view.add_object_ui_shortcuts(self.buildlog, action2)
        self.assertEqual(
            template_ui_shortcuts(self.buildlog), [action1, action2]
        )
        self.assertEqual(template_ui_shortcuts(self.work_request), [])
