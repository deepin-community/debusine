# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs for viewing artifacts."""

from django.urls import path

import debusine.web.views.artifacts as views

app_name = "artifacts"

urlpatterns = [
    path("create/", views.CreateArtifactView.as_view(), name="create"),
    path(
        "<int:artifact_id>/raw/<path:path>",
        views.FileDetailViewRaw.as_view(),
        name="file-raw",
    ),
    path(
        "<int:artifact_id>/file/<path:path>",
        views.FileDetailView.as_view(),
        name="file-detail",
    ),
    path(
        "<int:artifact_id>/download/<path:path>",
        views.DownloadPathView.as_view(),
        name="file-download",
    ),
    path(
        "<int:artifact_id>/download/",
        views.DownloadPathView.as_view(),
        name="download",
    ),
    path(
        "<int:artifact_id>/",
        views.ArtifactDetailView.as_view(),
        name="detail",
    ),
]
