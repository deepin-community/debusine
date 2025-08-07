# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Django REST Framework authentication infrastructure for Debusine."""

from typing import Optional, TYPE_CHECKING, Union

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication

if TYPE_CHECKING:
    from rest_framework.request import Request

    from debusine.db.models import Token, User


class DebusineTokenAuthentication(BaseAuthentication):
    """Restframework authentication for Debusine."""

    def authenticate(
        self, request: "Request"
    ) -> tuple[Union["User", AnonymousUser, None], Optional["Token"]] | None:
        """Authenticate the request and return a two-tuple of (user, token)."""
        token = getattr(request._request, "_debusine_token")
        if token is None:
            return None
        if token.user:
            return (token.user, token)
        else:
            return (AnonymousUser(), token)
