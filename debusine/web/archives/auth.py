# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Authentication for package archives."""

from django.conf import settings
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from debusine.db.models import Token, User


class ArchiveAuthentication(BasicAuthentication):
    """Authenticate to view an archive."""

    www_authenticate_realm = "archives"

    @property
    def _archive_fqdns(self) -> list[str]:
        """Hosts used for serving archives."""
        if isinstance(settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN, list):
            return settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN
        else:
            return [settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN]

    def authenticate(self, request: Request) -> tuple[User, None] | None:
        """Only use this authentication scheme for archive requests."""
        if request.get_host() not in self._archive_fqdns:
            return None
        return super().authenticate(request)

    def authenticate_credentials(
        self, userid: str, password: str, request: Request | None = None
    ) -> tuple[User, None]:
        """Authenticate the request, treating passwords as tokens."""
        if request is None or request.scheme != "https":
            raise AuthenticationFailed(
                "Authenticated access is only allowed using HTTPS."
            )
        try:
            token = Token.objects.get_tokens(
                username=userid, key=password
            ).get()
        except Token.DoesNotExist:
            raise AuthenticationFailed("Invalid token.")
        assert token.user is not None
        return token.user, None
