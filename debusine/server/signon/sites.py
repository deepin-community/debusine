# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Debusine extensions to Signon."""

import logging
from typing import Any

import requests
from rest_framework import status

from debusine.db.models import Identity, User
from debusine.server.signon import providers
from debusine.server.signon.signon import Signon

log = logging.getLogger("debusine.server.signon")


class DebianSignon(Signon):
    """
    Debian-specific extension to Signon.

    Activate it in django settings with::

        SIGNON_CLASS = "debusine.server.signon.sites.DebianSignon"

    This replaces the standard user authorization with a custom one for Debian,
    which:

    * ``restrict`` is ignored.
    * Fetches user status from nm.debian.org add ads it to claims as
      ``nm:status``
    * Checks for the ``debian`` group in Salsa claims: if present,
      authorization succeeds
    * Check for ``dm`` or ``dm_ga`` statuses in ``nm:status``: if present,
      authorization succeeds
    * ``add_to_group`` is extended to match ``nm:status`` values as groups,
      when prefixed with ``nm:``: see example below.

    You can then set a provider to auto-add users to Debusine groups with::

        SIGNON_PROVIDERS = [
            providers.ProviderClass(
                ...,
                options={
                    "add_to_group": {
                        # Map gitlab group names to Debusine groups
                        "gitlab/group": "scopename/groupname",
                        # Map nm.debian.org statuses to Debusine groups
                        "nm:dm": "scopename/groupname",
                        "nm:dm_ga": "scopename/groupname",
                    },
                }
            )
        ]
    """

    def fetch_nm_claims(self, salsa_id: int) -> dict[str, Any]:
        """Fetch user information from nm.debian.org."""
        url = f"https://nm.debian.org/api/salsa_status/{salsa_id}"
        with requests.get(url) as r:
            match r.status_code:
                case status.HTTP_200_OK:
                    if nm_status := r.json().get("status"):
                        return {"nm_status": nm_status}
                    log.warning(
                        "%s: user record has no 'status' information", url
                    )
                    return {}
                case status.HTTP_404_NOT_FOUND:
                    log.info("%s: user is not known in nm.debian.org", url)
                    return {}
                case _:
                    log.warning(
                        "%s: cannot fetch user information: %d (%s)",
                        url,
                        r.status_code,
                        r.reason,
                    )
                    return {}

    def validate_claims(
        self, provider: providers.Provider, identity: "Identity"
    ) -> list[str]:
        """
        Check if the claims in identity are acceptable.

        :return: a list of error messages, or an empty list if validation passed
        """
        res = []
        if not isinstance(provider, providers.GitlabProvider):
            res.append(
                "unsupported validation of logins from"
                f" {provider.name!r} provider:"
                f" {provider.__class__.__name__} is not supported"
            )
            return res

        # Work only with verified emails
        if not identity.claims.get("email_verified", False):
            res.append(f"identity {identity} does not have a verified email")
            return res

        # Call out to nm.debian.org to add claims["nm_status"]
        if nm_claims := self.fetch_nm_claims(int(identity.subject)):
            identity.claims.update(nm_claims)
            identity.save()

        if "debian" in identity.claims.get("groups_direct", ()):
            # Allow if in the Debian group.
            # We could check nm_status here, but checking groups_direct allows
            # creating accounts for DDs even if nm.debian.org is having
            # troubles
            return res
        elif identity.claims.get("nm_status") in ("dm", "dm_ga"):
            # Allow if a DM / DM with guest account
            return res
        else:
            res.append(f"identity {identity} is not DD or DM")
            return res

    def add_user_to_groups(
        self, user: User, identity: Identity, add_to_group: dict[str, str]
    ) -> None:
        """Add a user to groups according to ``add_to_group`` rules."""
        gitlab_groups = identity.claims.get("groups_direct", ())
        nm_status = identity.claims.get("nm_status", "")
        for remote_group, debusine_group in add_to_group.items():
            if remote_group.startswith("nm:"):
                if nm_status != remote_group[3:]:
                    continue
            elif remote_group not in gitlab_groups:
                continue
            self.add_user_to_group(user, debusine_group)
