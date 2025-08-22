# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Base for views using the rightbar UI layout."""

from typing import Any

from django.db.models import Model
from django.views.generic.base import ContextMixin

from debusine.web.views.sidebar import SidebarItem
from debusine.web.views.ui_shortcuts import UIShortcut


class UIShortcutsMixin(ContextMixin):
    """Mixin for views that add UI shortcuts."""

    def get_main_ui_shortcuts(self) -> list[UIShortcut]:
        """Return a list of shortcuts for this view."""
        return []

    def add_object_ui_shortcuts(self, obj: Model, *actions: UIShortcut) -> None:
        """
        Store one or more shortcuts for an object.

        This allows computing object-specific shortcuts when it's possible to
        do it in a database-efficient way, and storing them to be looked up in
        a way that is convenient for the templates.
        """
        stored = getattr(obj, "_ui_shortcuts", None)
        if stored is None:
            stored = []
            setattr(obj, "_ui_shortcuts", stored)
        stored.extend(actions)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add UI shortcuts to context."""
        context = super().get_context_data(**kwargs)
        context["main_ui_shortcuts"] = self.get_main_ui_shortcuts()
        return context


class RightbarUIMixin(ContextMixin):
    """Base class for views using a sidebar layout."""

    base_template = "web/_base_rightbar.html"

    def get_sidebar_items(self) -> list[SidebarItem]:
        """Return a list of sidebar items."""
        return []

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add UI elements to the template context."""
        context = super().get_context_data(**kwargs)
        context["sidebar_items"] = self.get_sidebar_items()
        return context
