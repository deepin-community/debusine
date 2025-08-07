# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine views."""

from typing import Any

from django.views.generic import TemplateView

from debusine.db.context import context
from debusine.db.models import Scope, WorkRequest
from debusine.tasks.models import TaskTypes
from debusine.web.views.base import BaseUIView
from debusine.web.views.tables import WorkRequestTable


class HomepageView(BaseUIView, TemplateView):
    """Class for the homepage view."""

    template_name = "web/homepage.html"
    title = "Homepage"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Return context_data with work_request_list and workspace_list."""
        ctx = super().get_context_data(**kwargs)

        # Signal the base template that we're outside of all scopes
        ctx["debusine_homepage"] = True

        if self.request.user.is_authenticated:
            ctx["work_requests"] = WorkRequestTable(
                self.request,
                WorkRequest.objects.filter(
                    created_by=self.request.user
                ).exclude(task_type=TaskTypes.INTERNAL),
            ).get_paginator(per_page=7)
        ctx["scopes"] = Scope.objects.can_display(context.user).order_by("name")
        return ctx
