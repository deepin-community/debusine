# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_work_requests."""

import datetime
from typing import ClassVar

from django.utils import timezone

from debusine.db.models import Token, WorkRequest, Worker
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class ListWorkRequestsCommandTests(TabularOutputTests, TestCase):
    """Test for list_work_requests management command.."""

    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.work_request = cls.playground.create_work_request(
            task_name="sbuild"
        )

    def test_list_work_requests_not_assigned(self) -> None:
        """Test a non-assigned work request output."""
        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command("list_work_requests")
        self.assertEqual(output.col(0), [str(self.work_request.id)])
        self.assertEqual(
            output.col(2), [self.work_request.created_at.isoformat()]
        )
        self.assertEqual(output.col(5), [self.work_request.status])

    def test_list_work_requests_assigned_finished(self) -> None:
        """Test an assigned work request output."""
        worker = Worker.objects.create_with_fqdn(
            "neptune", token=Token.objects.create()
        )
        self.work_request.worker = worker
        self.work_request.created_at = timezone.now()
        one_sec = datetime.timedelta(seconds=1)
        self.work_request.started_at = self.work_request.created_at + one_sec
        self.work_request.completed_at = self.work_request.started_at + one_sec
        self.work_request.result = WorkRequest.Results.SUCCESS
        self.work_request.save()

        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command("list_work_requests")

        self.assertEqual(output.col(1), ["neptune"])
        self.assertEqual(
            output.col(2), [self.work_request.created_at.isoformat()]
        )
        self.assertEqual(
            output.col(3), [self.work_request.started_at.isoformat()]
        )
        self.assertEqual(
            output.col(4), [self.work_request.completed_at.isoformat()]
        )
        self.assertEqual(output.col(6), [self.work_request.result])
