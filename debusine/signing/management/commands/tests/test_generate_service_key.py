# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command generate_service_key."""

import stat

from django.core.management import call_command
from django.test import TestCase as DjangoTestCase
from nacl.public import PrivateKey

from debusine.test import TestCase


class GenerateServiceKeyCommandTests(TestCase, DjangoTestCase):
    """Tests for the generate_service_key command."""

    def test_generate(self) -> None:
        """Write a new key with correct permissions."""
        temp_path = self.create_temporary_directory()
        key_path = temp_path / "0.key"

        with self.assertRaises(SystemExit) as raised:
            call_command("generate_service_key", str(key_path))
        self.assertEqual(raised.exception.code, 0)

        # The file has suitably-restrictive permissions.
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        # We can load the contents of the file as a private key.
        PrivateKey(key_path.read_bytes())
