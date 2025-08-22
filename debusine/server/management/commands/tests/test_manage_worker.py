# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command manage_worker."""
import datetime
from typing import ClassVar

from django.utils import timezone

from debusine.db.models import Token, WorkRequest, Worker
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


class ManageWorkerCommandTests(TestCase):
    """Tests for manage_worker command."""

    token: ClassVar[Token]
    worker: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a default token and worker."""
        super().setUpTestData()
        cls.token = Token.objects.create()
        cls.worker = Worker.objects.create(
            name='worker-a', token=cls.token, registered_at=timezone.now()
        )

    def test_enable_forwards_with_deprecation_warning(self) -> None:
        """`manage_worker enable` forwards to `worker enable` and warns."""
        self.assertFalse(self.token.enabled)

        with self.assertWarnsMessage(
            DeprecationWarning,
            "The `debusine-admin manage_worker enable` command has been "
            "deprecated in favour of `debusine-admin worker enable`",
        ):
            call_command("manage_worker", "enable", self.worker.name)

        self.token.refresh_from_db()
        self.assertTrue(self.token.enabled)

    def test_disable_forwards_with_deprecation_warning(self) -> None:
        """`manage_worker disable` forwards to `worker disable` and warns."""
        self.token.enable()
        self.token.refresh_from_db()

        work_request_running = self.playground.create_work_request(
            worker=self.worker,
            status=WorkRequest.Statuses.RUNNING,
            started_at=timezone.now(),
        )
        work_request_pending = self.playground.create_work_request(
            worker=self.worker,
            status=WorkRequest.Statuses.PENDING,
            started_at=timezone.now(),
        )
        work_request_finished = self.playground.create_work_request(
            worker=self.worker,
            status=WorkRequest.Statuses.COMPLETED,
            started_at=timezone.now(),
        )

        self.assertTrue(self.token.enabled)

        with self.assertWarnsMessage(
            DeprecationWarning,
            "The `debusine-admin manage_worker disable` command has been "
            "deprecated in favour of `debusine-admin worker disable`",
        ):
            call_command("manage_worker", "disable", self.worker.name)

        # Worker is disabled
        self.token.refresh_from_db()
        self.assertFalse(self.token.enabled)

        # work requests in running or pending status are not assigned
        # to the worker anymore
        for work_request in work_request_running, work_request_pending:
            work_request_running.refresh_from_db()
            self.assertIsNone(work_request_running.worker)
            self.assertEqual(
                work_request_running.status, WorkRequest.Statuses.PENDING
            )
            self.assertIsNone(work_request_running.started_at)

        # work request that was completed: fields did not change
        work_request_finished.refresh_from_db()
        self.assertEqual(
            work_request_finished.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(work_request_finished.worker, self.worker)
        self.assertIsInstance(
            work_request_finished.started_at, datetime.datetime
        )
