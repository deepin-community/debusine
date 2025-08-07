# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Debusine extensions to Signon."""

from django.core.exceptions import ImproperlyConfigured

from debusine.db.models import Group, Identity, User
from debusine.server.signon.signon import Signon


class DebusineSignon(Signon):
    """
    Debusine-specific extension to Signon.

    Activate it in django settings with::

        SIGNON_CLASS = "debusine.server.signon.debusine.DebusineSignon"

    You can then set a provider to auto-addusers to group with::

        SIGNON_PROVIDERS = [
            providers.ProviderClass(
                ...,
                options={
                    "add_to_group": "scopename/groupname",
                }
            )
        ]
    """

    def create_user_from_identity(self, identity: Identity) -> User | None:
        """Allow to auto-add new users to Debusine groups."""
        user = super().create_user_from_identity(identity)
        if user is None:
            return None

        provider = self.get_provider_for_identity(identity)
        if provider is None:
            return user

        if add_to_group := provider.options.get("add_to_group", None):
            try:
                group = Group.objects.from_scoped_name(add_to_group)
            except Group.DoesNotExist:
                raise ImproperlyConfigured(
                    f"Signon provider {provider.name!r} adds"
                    f" to missing group {add_to_group!r}"
                )
            group.users.add(user)

        return user
