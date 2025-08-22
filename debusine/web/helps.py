# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data used to display help."""

from typing import NamedTuple

from django.utils.safestring import SafeString

DOCS_BASE_URL = "https://freexian-team.pages.debian.net/debusine"


class Help(NamedTuple):
    """Represents help metadata."""

    title: str
    summary: SafeString
    link: str


HELPS: dict[str, Help] = {
    "dependencies": Help(
        title="Dependencies",
        summary=SafeString(
            "Work requests that must complete "
            "to unblock this one and let it run."
        ),
        link=f"{DOCS_BASE_URL}/explanation/"
        "workflow-orchestration.html#dependencies",
    ),
    "reverse_dependencies": Help(
        title="Reverse dependencies",
        summary=SafeString(
            "Work requests that require this one to complete "
            "to unblock and let them run."
        ),
        link=f"{DOCS_BASE_URL}/explanation/"
        "workflow-orchestration.html#dependencies",
    ),
    "expiration_delay": Help(
        title="Expiration delay",
        summary=SafeString(
            "Days after which objects such as artifacts or "
            "work requests are removed"
        ),
        link=f"{DOCS_BASE_URL}/explanation/expiration-of-data.html",
    ),
}
