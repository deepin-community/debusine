# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_workers."""

from debusine.db.models import Token, Worker
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class ListWorkersCommandTests(TabularOutputTests, TestCase):
    """Test for list_workers management command."""

    def test_list_workers_connected(self) -> None:
        """
        List worker command prints worker information.

        The worker is connected.
        """
        token = Token.objects.create()
        worker_1 = Worker.objects.create_with_fqdn('recent-ping', token)
        worker_1.mark_connected()
        assert worker_1.connected_at is not None

        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command('list_workers')
        self.assertEqual(output.col(0), [worker_1.name])
        self.assertEqual(output.col(2), [worker_1.registered_at.isoformat()])
        self.assertEqual(output.col(3), [worker_1.connected_at.isoformat()])
        self.assertEqual(output.col(4), [token.hash])
        self.assertEqual(output.col(5), [str(token.enabled)])

    def test_list_workers_not_connected(self) -> None:
        """
        List worker command prints worker information.

        The worker is not connected.
        """
        token = Token.objects.create()
        worker_1 = Worker.objects.create_with_fqdn('recent-ping', token=token)
        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command('list_workers')

        self.assertEqual(output.col(0), [worker_1.name])
        self.assertEqual(output.col(2), [worker_1.registered_at.isoformat()])
        self.assertEqual(output.col(3), ["-"])
        self.assertEqual(output.col(4), [token.hash])
        self.assertEqual(output.col(5), [str(token.enabled)])

    def test_list_workers_celery(self) -> None:
        """list_workers handles Celery workers, which have no tokens."""
        worker = Worker.objects.get_or_create_celery()
        worker.mark_connected()
        assert worker.connected_at is not None
        with self.assertPrintsTable() as output:
            call_command("list_workers")

        self.assertEqual(output.col(0), [worker.name])
        self.assertEqual(output.col(1), [worker.worker_type])
        self.assertEqual(output.col(2), [worker.registered_at.isoformat()])
        self.assertEqual(output.col(3), [worker.connected_at.isoformat()])
        self.assertEqual(output.col(4), ["-"])
        self.assertEqual(output.col(5), ["-"])
