# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs related to work_requests."""

from django.urls import path

import debusine.web.views.work_request as views

app_name = "work_requests"

urlpatterns = [
    path(
        "",
        views.WorkRequestListView.as_view(),
        name="list",
    ),
    path(
        "<int:pk>/",
        views.WorkRequestDetailView.as_view(),
        name="detail",
    ),
    path(
        "<int:pk>/retry/",
        views.WorkRequestRetryView.as_view(),
        name="retry",
    ),
    path(
        "<int:pk>/unblock/",
        views.WorkRequestUnblockView.as_view(),
        name="unblock",
    ),
    path(
        "create/",
        views.WorkRequestCreateView.as_view(),
        name="create",
    ),
]
