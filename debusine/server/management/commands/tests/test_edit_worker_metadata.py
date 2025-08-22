# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command edit_worker_metadata."""

from typing import ClassVar

from debusine.db.models import Token, Worker
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


class EditWorkerMetadataCommandTests(TestCase):
    """Test for edit_worker_metadata command."""

    token: ClassVar[Token]
    worker: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Create a default Token and Worker."""
        super().setUpTestData()
        cls.token = Token.objects.create()
        cls.worker = Worker.objects.create_with_fqdn(
            "worker-01.lan", token=cls.token
        )

    def test_forwards_with_deprecation_warning(self) -> None:
        """Forwards to `worker edit_metadata` and warns."""
        static_metadata_file = self.create_temporary_file()
        static_metadata_file.write_bytes(
            b'sbuild:\n  architectures:\n    - amd64\n    - i386\n'
            b'  distributions:\n    - jessie\n    - stretch'
        )
        with self.assertWarnsMessage(
            DeprecationWarning,
            "The `debusine-admin edit_worker_metadata` command has been "
            "deprecated in favour of `debusine-admin worker edit_metadata`",
        ):
            call_command(
                "edit_worker_metadata",
                "--set",
                str(static_metadata_file),
                self.worker.name,
            )

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata,
            {
                'sbuild': {
                    'architectures': ['amd64', 'i386'],
                    'distributions': ['jessie', 'stretch'],
                }
            },
        )
