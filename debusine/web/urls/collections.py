# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs related to collections in workspaces."""

from django.urls import path

import debusine.web.views.collections as views

app_name = "collections"

urlpatterns = [
    path(
        "",
        views.CollectionListView.as_view(),
        name="list",
    ),
    path(
        "<str:ccat>/",
        views.CollectionCategoryListView.as_view(),
        name="category_list",
    ),
    path(
        "<str:ccat>/<str:cname>/",
        views.CollectionDetailView.as_view(),
        name="detail",
    ),
    path(
        "<str:ccat>/<str:cname>/search/",
        views.CollectionSearchView.as_view(),
        name="search",
    ),
    path(
        "<str:ccat>/<str:cname>/item/<str:iid>/<path:iname>/",
        views.CollectionItemDetailView.as_view(),
        name="item_detail",
    ),
]
