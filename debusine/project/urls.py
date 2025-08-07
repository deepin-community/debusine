# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine URL Configuration.

Debusine supports multi-tenancy by setting the current scope using the
``/{scope}/`` prefix at the beginning of most URLs.

This is implemented in Django by using a middleware to detect the scope from
URL, and then setting `request.urlconf` to point to the urlconf to use to
resolve URLs for that scope.

API calls use the unscoped ``/api/`` prefix in URLs. Scope for API calls is
provided using the `X-Debusine-Scope: name` HTTP header. If the header is
missing, Debusine will use the `DEBUSINE_DEFAULT_SCOPE` setting.

Unscoped service views are placed in ``/-/``

To ease transition from legacy unscoped URLs, generated urlconfs contain
best-effort redirects from the old versions of URLs to the new ones. To prevent
ambiguity, scope names are validated against a list of reserved keywords, which
contains prefixes used for non-scoped URLs.
"""
from typing import Any

from django.conf import settings
from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path, re_path
from django.views.generic import RedirectView

from debusine.web.views.auth import LoginView, LogoutView
from debusine.web.views.task_status import TaskStatusView

# from django.conf import settings

# if settings.DEBUG:
#     import debug_toolbar

service_urlpatterns: list[URLPattern | URLResolver] = [
    path("admin/", admin.site.urls),
    path("status/queue/", TaskStatusView.as_view(), name="task-status"),
    path(
        "status/workers/",
        include("debusine.web.urls.workers", namespace="workers"),
    ),
    path(
        "status/worker-pools/",
        include("debusine.web.urls.worker_pools", namespace="worker-pools"),
    ),
    path(
        "user/",
        include("debusine.web.urls.user", namespace="user"),
    ),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path(
        "signon/",
        include("debusine.web.urls.signon", namespace="signon"),
    ),
    path(
        "enroll/",
        include("debusine.web.urls.enroll", namespace="enroll"),
    ),
]


def make_urlpatterns(scope: str) -> list[Any]:
    """
    Create the URL structure of the website given a scope name.

    This is used by ScopeMiddleware to create a different urlconf for each
    scope, to be set it in request.urlconf when the scope is known.
    """
    return [
        path('api/', include('debusine.server.urls', namespace='api')),
        path("-/", include(service_urlpatterns)),
        path("", include("debusine.web.urls.homepage", namespace="homepage")),
        re_path(
            fr"^{scope}/(?:workspace)/(?P<path>.+)",
            RedirectView.as_view(
                url=f"/{scope}/%(path)s",
                permanent=True,
                query_string=True,
            ),
        ),
        path(f"{scope}/", include('debusine.web.urls')),
        path(
            'api-auth/<path:path>',
            RedirectView.as_view(
                url="/api/auth/%(path)s",
                permanent=True,
                query_string=True,
            ),
        ),
        path(
            "accounts/oidc_callback/<name>/",
            RedirectView.as_view(
                pattern_name="signon:oidc_callback", permanent=True
            ),
        ),
        path(
            "accounts/bind_identity/<name>/",
            RedirectView.as_view(
                pattern_name="signon:bind_identity", permanent=True
            ),
        ),
        re_path(
            r"^(?P<path>(?:accounts|workspace|artifact)/.+)",
            RedirectView.as_view(
                url=f"/{settings.DEBUSINE_DEFAULT_SCOPE}/%(path)s",
                permanent=True,
                query_string=True,
            ),
        ),
        path(
            "task-status/<path:path>",
            RedirectView.as_view(
                url="/-/status/queue/%(path)s",
                permanent=True,
                query_string=True,
            ),
        ),
        path(
            "workers/<path:path>",
            RedirectView.as_view(
                url="/-/status/workers/%(path)s",
                permanent=True,
                query_string=True,
            ),
        ),
        path(
            "user/<path:path>",
            RedirectView.as_view(
                url="/-/user/%(path)s",
                permanent=True,
                query_string=True,
            ),
        ),
    ]


# Default urlpatterns
urlpatterns = make_urlpatterns(settings.DEBUSINE_DEFAULT_SCOPE)
