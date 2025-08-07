# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Routes for the server application."""

from django.urls import path

from debusine.server import consumers

websocket_urlpatterns = [
    path(
        "api/ws/1.0/worker/connect/",
        consumers.WorkerConsumer.as_asgi(),
        name='ws-connect',
    ),
    path(
        "api/ws/1.0/work-request/on-completed/",
        consumers.OnWorkRequestCompletedConsumer.as_asgi(),
        name="ws-work-request-on-completed",
    ),
]
