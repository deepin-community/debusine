# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""URLs related to group management."""
from django.urls import path

import debusine.web.views.auth as views

app_name = "groups"

# Scoped URLs
urlpatterns = [
    path(
        "<group>/",
        views.GroupDetailView.as_view(),
        name="detail",
    ),
    path(
        "<group>/edit/<user>/",
        views.MembershipUpdateView.as_view(),
        name="update-member",
    ),
    path(
        "<group>/remove/<user>/",
        views.MembershipDeleteView.as_view(),
        name="remove-member",
    ),
    path(
        "<group>/audit-log/",
        views.GroupAuditLogView.as_view(),
        name="audit-log",
    ),
]
