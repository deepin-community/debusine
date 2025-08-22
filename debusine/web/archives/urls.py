# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs for package archives."""

from django.urls import include, path

# Make sure the snapshot converter is registered.
import debusine.web.archives.converters  # noqa: F401
from debusine.web.archives.views import (
    DistsByHashFileView,
    DistsFileView,
    PoolFileView,
    TopLevelFileView,
)

archive_urlpatterns = [
    path(
        # Debusine currently only stores SHA256 checksums.
        "dists/<str:suite>/<path:directory>/by-hash/SHA256/<str:checksum>",
        DistsByHashFileView.as_view(),
        name="dists-by-hash-file",
    ),
    path(
        "dists/<str:suite>/<path:path>",
        DistsFileView.as_view(),
        name="dists-file",
    ),
    path(
        "pool/<str:component>/<str:sourceprefix>/<str:source>/<str:filename>",
        PoolFileView.as_view(),
        name="pool-file",
    ),
    path("<path:path>", TopLevelFileView.as_view(), name="top-level-file"),
]

urlpatterns = [
    path(
        "<str:scope>/<str:workspace>/<snapshot:snapshot>/",
        include(archive_urlpatterns),
    ),
    path("<str:scope>/<str:workspace>/", include(archive_urlpatterns)),
]
