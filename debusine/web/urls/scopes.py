# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs related to workspaces."""

from django.urls import path

import debusine.web.views.scopes as views

app_name = "scopes"

urlpatterns = [
    path(
        "",
        views.ScopeDetailView.as_view(),
        name="detail",
    ),
]
