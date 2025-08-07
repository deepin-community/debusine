# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs related to user (e.g. token handling)."""

from django.urls import path

import debusine.web.views.enroll as views

app_name = "enroll"

urlpatterns = [
    path("confirm/<nonce>/", views.ConfirmView.as_view(), name="confirm"),
]
