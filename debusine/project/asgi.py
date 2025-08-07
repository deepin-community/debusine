# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine websocket URLs Configuration."""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'debusine.project.settings')

django_asgi_app = get_asgi_application()


from debusine.server.middlewares.context import (  # noqa: E402
    ContextMiddlewareChannels,
)
from debusine.server.middlewares.token_last_seen_at import (  # noqa: E402
    TokenLastSeenAtMiddlewareChannels,
)
from debusine.server.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": ContextMiddlewareChannels(
            TokenLastSeenAtMiddlewareChannels(URLRouter(websocket_urlpatterns)),
        ),
    }
)
