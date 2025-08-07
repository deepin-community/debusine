# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs related to user (e.g. token handling)."""

from django.urls import URLPattern, URLResolver, include, path

import debusine.web.views.auth as views

app_name = "user"

user_urlpatterns: list[URLPattern | URLResolver] = [
    path(
        "token/",
        views.UserTokenListView.as_view(),
        name="token-list",
    ),
    path(
        "token/create/",
        views.UserTokenCreateView.as_view(),
        name="token-create",
    ),
    path(
        "token/<int:pk>/edit/",
        views.UserTokenUpdateView.as_view(),
        name="token-edit",
    ),
    path(
        "token/<int:pk>/delete/",
        views.UserTokenDeleteView.as_view(),
        name="token-delete",
    ),
    path(
        "groups/",
        views.UserGroupListView.as_view(),
        name="groups",
    ),
    path(
        "",
        views.UserDetailView.as_view(),
        name="detail",
    ),
]

urlpatterns = [
    path(
        "<str:username>/",
        include(user_urlpatterns),
    ),
]
