# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_notification_channels."""

import os
import socket
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from debusine.django.management.tests import call_command


class InfoTests(TestCase):
    """Tests for the info command."""

    def test_info_defaults(self) -> None:
        """Info output with no overrides."""
        fqdn = settings.DEBUSINE_FQDN

        with (
            mock.patch(
                "shutil.get_terminal_size",
                return_value=os.terminal_size((65536, 25)),
            ),
            mock.patch(
                # We make sure DEBUSINE_FQDN is an FQDN, for tests
                # Return our value, in case we added an example domain.
                "socket.getfqdn",
                return_value=fqdn,
            ),
        ):
            stdout, stderr, exit_code = call_command("info")

        self.assertIn(f" * DEBUSINE_FQDN = {fqdn!r}", stdout)
        self.assertIn(
            "The current value matches the autodetected default"
            " from socket.getfqdn().",
            stdout,
        )

        # The test runner adds 'testserver' therefore editing the setting from
        # its autodetected value
        self.assertIn(
            f" * ALLOWED_HOSTS = [{fqdn!r}, 'localhost', '127.0.0.1', "
            "'testserver']",
            stdout,
        )
        self.assertIn(
            "The current value is changed from default"
            f" [{fqdn!r}, 'localhost', '127.0.0.1']",
            stdout,
        )

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_info_default_allowed_hosts(self) -> None:
        """Info output with ALLOWED_HOSTS having a default value."""
        fqdn = settings.DEBUSINE_FQDN
        with (
            mock.patch(
                "shutil.get_terminal_size",
                return_value=os.terminal_size((65536, 25)),
            ),
            mock.patch(
                # We make sure DEBUSINE_FQDN is an FQDN, for tests
                # Return our value, in case we added an example domain.
                "socket.getfqdn",
                return_value=fqdn,
            ),
        ):
            with override_settings(
                ALLOWED_HOSTS=[fqdn, "localhost", "127.0.0.1"]
            ):
                stdout, stderr, exit_code = call_command("info")

        self.assertIn(f" * DEBUSINE_FQDN = {fqdn!r}", stdout)
        self.assertIn(
            "The current value matches the autodetected default"
            " from socket.getfqdn().",
            stdout,
        )

        self.assertIn(
            f" * ALLOWED_HOSTS = [{fqdn!r}, 'localhost', '127.0.0.1']", stdout
        )
        self.assertIn(
            "The current value matches the autodetected default from"
            " DEBUSINE_FQDN.",
            stdout,
        )

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_info_changed_debusine_fqdn(self) -> None:
        """Info output with overridden DEBUSINE_FQDN."""
        with mock.patch(
            "shutil.get_terminal_size",
            return_value=os.terminal_size((65536, 25)),
        ):
            with override_settings(
                DEBUSINE_FQDN="test.example.org",
                # ALLOWED_HOSTS is built from DEBUSINE_FQDN
                ALLOWED_HOSTS=["foo", "testserver"],
            ):
                stdout, stderr, exit_code = call_command("info")

        fqdn = socket.getfqdn()
        self.assertIn(" * DEBUSINE_FQDN = 'test.example.org'", stdout)
        self.assertIn(
            f"The current value is changed from the default {fqdn!r}",
            stdout,
        )
        self.assertIn(" * ALLOWED_HOSTS = ['foo', 'testserver']", stdout)
        self.assertIn(
            "The current value is changed from default"
            " ['test.example.org', 'localhost', '127.0.0.1']",
            stdout,
        )

        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
