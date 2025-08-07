# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command test."""

import os.path
import shutil
from unittest import mock

from django.conf import settings

from debusine.django.management.tests import call_command
from debusine.server.management.commands.test import setup_temp_data_directories
from debusine.test.django import TestCase


class TestCommandTests(TestCase):
    """Tests for test command."""

    def setUp(self) -> None:
        """Save settings."""
        self.original_settings = {
            'DEBUSINE_DATA_PATH': '',
            'DEBUSINE_CACHE_DIRECTORY': '',
            'DEBUSINE_TEMPLATE_DIRECTORY': '',
            'DEBUSINE_UPLOAD_DIRECTORY': '',
            'DEBUSINE_STORE_DIRECTORY': '',
        }
        for key in self.original_settings.keys():
            self.original_settings[key] = getattr(settings, key)

    def tearDown(self) -> None:
        """Restore settings."""
        for key in self.original_settings.keys():
            setattr(settings, key, self.original_settings[key])

    def test_data_directory_removal(self) -> None:
        """Test '--keepdata'."""
        with mock.patch(
            "django.core.management.commands.test.Command.handle", autospec=True
        ):
            setup_temp_data_directories()
            call_command("test")
            self.assertFalse(os.path.exists(settings.DEBUSINE_DATA_PATH))

            setup_temp_data_directories()
            call_command("test", keepdata=True, verbosity=0)
            self.assertTrue(os.path.exists(settings.DEBUSINE_DATA_PATH))
            shutil.rmtree(settings.DEBUSINE_DATA_PATH)

            setup_temp_data_directories()
            call_command("test", keepdata=True, verbosity=1)
            self.assertTrue(os.path.exists(settings.DEBUSINE_DATA_PATH))
            shutil.rmtree(settings.DEBUSINE_DATA_PATH)
