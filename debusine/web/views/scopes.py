# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine scope views."""

from typing import Any

from django.views.generic import TemplateView

from debusine.db.context import context
from debusine.db.models import Workspace
from debusine.web.views.base import BaseUIView


class ScopeDetailView(BaseUIView, TemplateView):
    """Landing page for a scope."""

    # This is anomalous for a detail view in that it does not take arguments,
    # and only displays the current scope. This is due to scopes being the
    # roots of different urlconfs, rather than a parameterized path inside a
    # single urlconf.

    template_name = "web/scope-detail.html"

    def get_title(self) -> str:
        """Get the page title."""
        return context.require_scope().name

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Build template context data."""
        ctx = super().get_context_data(**kwargs)
        ctx["workspaces"] = (
            Workspace.objects.in_current_scope()
            .can_display(context.user)
            .order_by("name")
        )
        return ctx
