# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-signing command to generate a service private key.

This key is used to encrypt other private keys.
"""

from pathlib import Path
from typing import Any

from django.core.management import CommandParser
from nacl.public import PrivateKey

from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to generate a service private key."""

    help = "Generate a service private key"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the generate_service_key command."""
        parser.add_argument(
            "output_file",
            type=Path,
            help="Write the new key to this file path.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Generate a new random key."""
        key = PrivateKey.generate()
        options["output_file"].touch(mode=0o600)
        options["output_file"].write_bytes(bytes(key))

        raise SystemExit(0)
