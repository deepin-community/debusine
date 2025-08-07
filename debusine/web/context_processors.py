# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine template context processors."""
from typing import Any, TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover
    import django.http


def server_info(
    request: "django.http.HttpRequest",  # noqa: U100
) -> dict[str, Any]:
    """Add general server information to the request context."""
    return {
        "DEBUSINE_FQDN": settings.DEBUSINE_FQDN,
    }


def application_context(
    request: "django.http.HttpRequest",  # noqa: U100
) -> dict[str, Any]:
    """Add general server information to the request context."""
    from debusine.db.context import context

    return {
        "scope": lambda: context.scope,
        "workspace": lambda: context.workspace,
    }
