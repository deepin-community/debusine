# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Django Application Configuration for the signing application."""

from django.apps import AppConfig


class SigningConfig(AppConfig):
    """Django's AppConfig for the signing application."""

    name = "debusine.signing"

    def ready(self) -> None:
        """Finish initializing the signing application."""
        # Import signing tasks so that they are registered.
        import debusine.signing.tasks  # noqa: F401
