# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_file_store."""

import io

import yaml
from django.test import override_settings

from debusine.db.models import FileStore
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class CreateFileStoreCommandTests(TestCase):
    """Tests for the create_file_store command."""

    def test_forwards_with_deprecation_warning(self) -> None:
        """`create_file_store` forwards to `file_store create` and warns."""
        name = "test"
        backend = FileStore.BackendChoices.LOCAL
        configuration = {"base_directory": "/nonexistent"}
        with self.assertWarnsMessage(
            DeprecationWarning,
            "The `debusine-admin create_file_store` command has been "
            "deprecated in favour of `debusine-admin file_store create`",
        ):
            stdout, stderr, exit_code = call_command(
                "create_file_store",
                name,
                backend,
                stdin=io.StringIO(yaml.safe_dump(configuration)),
            )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        file_store = FileStore.objects.get(name="test")
        self.assertEqual(file_store.backend, backend)
        self.assertEqual(file_store.configuration, configuration)
