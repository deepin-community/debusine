# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the signing migrations."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from debusine.assets import KeyPurpose
from debusine.signing.models import ProtectedKeyStorage


class MigrationTests(TestCase):
    """Test signing migrations."""

    def test_key_discriminated_union(self) -> None:
        """Keys are converted to the current layout for discriminated unions."""
        migrate_from = [("signing", "0001_initial")]
        migrate_to = [("signing", "0002_key_discriminated_union")]
        old_keys = [
            (
                "0" * 64,
                {
                    "storage": ProtectedKeyStorage.NACL,
                    "data": {"public_key": "pub1", "encrypted": "enc1"},
                },
                b"pub1",
            ),
            (
                "1" * 64,
                {
                    "storage": ProtectedKeyStorage.NACL,
                    "data": {"public_key": "pub2", "encrypted": "enc2"},
                },
                b"pub2",
            ),
        ]
        new_keys = [
            (
                "0" * 64,
                {
                    "storage": ProtectedKeyStorage.NACL,
                    "public_key": "pub1",
                    "encrypted": "enc1",
                },
                b"pub1",
            ),
            (
                "1" * 64,
                {
                    "storage": ProtectedKeyStorage.NACL,
                    "public_key": "pub2",
                    "encrypted": "enc2",
                },
                b"pub2",
            ),
        ]

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        for old_fingerprint, old_private_key, old_public_key in old_keys:
            old_apps.get_model("signing", "Key").objects.create(
                purpose=KeyPurpose.UEFI,
                fingerprint=old_fingerprint,
                private_key=old_private_key,
                public_key=old_public_key,
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        for new_fingerprint, new_private_key, new_public_key in new_keys:
            key = new_apps.get_model("signing", "Key").objects.get(
                purpose=KeyPurpose.UEFI, fingerprint=new_fingerprint
            )
            self.assertEqual(key.private_key, new_private_key)
            self.assertEqual(bytes(key.public_key), new_public_key)

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        for old_fingerprint, old_private_key, old_public_key in old_keys:
            key = old_apps.get_model("signing", "Key").objects.get(
                purpose=KeyPurpose.UEFI, fingerprint=old_fingerprint
            )
            self.assertEqual(key.private_key, old_private_key)
            self.assertEqual(bytes(key.public_key), old_public_key)
