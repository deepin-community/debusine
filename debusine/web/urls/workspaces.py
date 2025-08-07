# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs related to workspaces."""

from django.urls import URLPattern, URLResolver, include, path

import debusine.web.views.workspace as views

app_name = "workspaces"

workspace_urlpatterns: list[URLPattern | URLResolver] = [
    path(
        "",
        views.WorkspaceDetailView.as_view(),
        name="detail",
    ),
    path(
        "configure/",
        views.WorkspaceUpdateView.as_view(),
        name="update",
    ),
    path(
        "collection/",
        include(
            "debusine.web.urls.collections",
            namespace="collections",
        ),
    ),
    path(
        "workflow/",
        include(
            "debusine.web.urls.workflows",
            namespace="workflows",
        ),
    ),
    path(
        "workflow-template/",
        include(
            "debusine.web.urls.workflow_templates",
            namespace="workflow_templates",
        ),
    ),
    path(
        "artifact/",
        include(
            "debusine.web.urls.artifacts",
            namespace="artifacts",
        ),
    ),
    path(
        "work-request/",
        include("debusine.web.urls.work_requests", namespace="work-requests"),
    ),
]

urlpatterns = [
    path(
        "<str:wname>/",
        include(workspace_urlpatterns),
    ),
]
