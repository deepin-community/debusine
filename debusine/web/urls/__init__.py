# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Django pages URL's for Debusine."""
from django.urls import URLPattern, URLResolver, include, path
from django.views.generic import RedirectView

service_urlpatterns: list[URLPattern | URLResolver] = [
    path(
        "groups/",
        include("debusine.web.urls.groups", namespace="groups"),
    ),
]

# Scoped URLs
urlpatterns = [
    path("-/", include(service_urlpatterns)),
    # TODO Preserved for compatibility redirects only
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
    path(
        "",
        include("debusine.web.urls.scopes", namespace="scopes"),
    ),
    path(
        "",
        include("debusine.web.urls.workspaces", namespace="workspaces"),
    ),
    path(
        "",
        include(
            "debusine.web.urls.workflow_templates",
            namespace="workflow-templates",
        ),
    ),
]
