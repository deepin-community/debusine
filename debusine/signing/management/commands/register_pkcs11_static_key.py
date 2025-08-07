# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-signing register_pkcs11_static_key command.

This registers a static key stored on a PKCS#11 token.
"""

from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser

from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.signing.db.models import AuditLog, Key
from debusine.signing.models import ProtectedKeyPKCS11Static


class Command(DebusineBaseCommand):
    """Command to register a static key stored on a PKCS#11 token."""

    help = "Register a static key stored on a PKCS#11 token"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the register_pkcs11_static_key command."""
        parser.add_argument(
            "purpose",
            choices=list(Key.Purpose),
            help="The purpose of the key.",
        )
        parser.add_argument("pkcs11_uri", help="The pkcs11: URI for the key.")
        parser.add_argument(
            "certificate_file",
            type=Path,
            help="File path to a certificate for the key.",
        )
        parser.add_argument("description", help="Description of the key.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Register a key."""
        purpose = options["purpose"]
        pkcs11_uri = options["pkcs11_uri"]
        public_key = options["certificate_file"].read_bytes()
        description = options["description"]
        fingerprint = Key.objects.get_fingerprint(
            purpose=purpose, public_key=public_key
        )

        try:
            key = Key(
                purpose=purpose,
                fingerprint=fingerprint,
                private_key=ProtectedKeyPKCS11Static.create(
                    pkcs11_uri=pkcs11_uri
                ).dict(),
                public_key=public_key,
            )
            key.full_clean()
            key.save()
            AuditLog.objects.create(
                event=AuditLog.Event.REGISTER,
                purpose=purpose,
                fingerprint=fingerprint,
                data={"description": description},
            )
        except ValidationError as exc:
            raise CommandError(
                "Error creating key: " + "\n".join(exc.messages), returncode=3
            )

        raise SystemExit(0)
