# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for generic notifications."""

import logging
from typing import ClassVar
from unittest import mock

from asgiref.sync import sync_to_async
from django.core import mail
from django.core.mail import EmailMessage

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.db.models import NotificationChannel, Token, WorkRequest, Worker
from debusine.server import notifications
from debusine.tasks.models import (
    ActionSendNotification,
    EventReactions,
    NotificationDataEmail,
)
from debusine.test.django import ChannelsHelpersMixin, TestCase


class NotifyWorkerTokenDisabledTests(ChannelsHelpersMixin, TestCase):
    """Tests for notify_worker_token_disabled."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.token = Token.objects.create()

    async def test_message_is_not_sent(self) -> None:
        """Test Token with an associated worker: message is sent."""
        channel = await self.create_channel(self.token.hash)

        await sync_to_async(self.token.disable)()

        await self.assert_channel_nothing_received(channel)

    async def test_message_is_sent(self) -> None:
        """Test Token with an associated worker: message is sent."""
        await sync_to_async(Worker.objects.create_with_fqdn)(
            fqdn="debusine", token=self.token
        )

        channel = await self.create_channel(self.token.hash)

        await sync_to_async(self.token.disable)()

        await self.assert_channel_received(channel, {"type": "worker.disabled"})


class NotifyWorkRequestAssigned(ChannelsHelpersMixin, TestCase):
    """Tests for notify_work_request_assigned."""

    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up test fixture."""
        super().setUpTestData()
        workspace = cls.playground.create_workspace(name="Tests")
        token = cls.playground.create_user_token()
        cls.work_request = cls.playground.create_work_request(
            workspace=workspace, created_by=token.user
        )

    def test_message_is_not_sent(self) -> None:
        """Test WorkRequest.assign_worker(None) is a no-operation."""
        # Asserts that no exception is raised. notify_work_request_assigned
        # is checking that worker is None and not trying to access the
        # worker's token.
        self.work_request.assign_worker(None)  # type: ignore[arg-type]

    async def test_message_is_sent(self) -> None:
        """Test WorkRequest.assign_worker(worker) sends msg to the channel."""
        token = await Token.objects.acreate()
        worker = await sync_to_async(Worker.objects.create_with_fqdn)(
            "debusine", token=token
        )

        channel = await self.create_channel(token.hash)

        await sync_to_async(self.work_request.assign_worker)(worker)

        await self.assert_channel_received(
            channel, {"type": "work_request.assigned"}
        )

    def test_celery_worker(self) -> None:
        """
        WorkRequest.assign_worker sends no message for Celery workers.

        These are assumed to notify the worker of work to do via some other
        mechanism (e.g. a Celery queue).
        """
        worker = Worker.objects.get_or_create_celery()

        # Asserts that no exception is raised.
        self.work_request.assign_worker(worker)


class NotifyWorkRequestCompletedTests(TestCase):
    """Test notify_work_request_completed function."""

    def assert_send_notification(
        self,
        status: WorkRequest.Results,
        expected_subject: str,
        *,
        expected_to: list[str] | None = None,
        event_name: str = 'on_failure',
        notification_data: NotificationDataEmail | None = None,
        expected_cc: list[str] | None = None,
    ) -> None:
        """Assert WorkRequest completed in failure or error."""
        notification_channel_name = "deblts-email"

        event_reactions = EventReactions.parse_obj(
            {
                event_name: [
                    {
                        "action": "send-notification",
                        "channel": notification_channel_name,
                    }
                ]
            }
        )
        if notification_data is not None:
            getattr(event_reactions, event_name)[
                0
            ].data = notification_data.dict()

        work_request: WorkRequest = self.playground.create_work_request(
            status=WorkRequest.Statuses.RUNNING, event_reactions=event_reactions
        )

        if expected_to is None:
            to = ["recipient@example.com"]
        else:
            to = expected_to

        notification_channel_data = {
            "from": "sender@debusine.example.org",
            "to": to,
        }

        NotificationChannel.objects.create(
            name=notification_channel_name,
            method=NotificationChannel.Methods.EMAIL,
            data=notification_channel_data,
        )

        work_request.mark_completed(status)

        self.assertEqual(len(mail.outbox), 1)

        sent_mail = mail.outbox[0]

        self.assertEqual(
            sent_mail.subject,
            expected_subject.format(work_request_id=work_request.id),
        )
        self.assertEqual(sent_mail.body, "Sent by debusine")

        self.assertEqual(sent_mail.to, to)

        if expected_cc is None:
            expected_cc = []

        self.assertEqual(sent_mail.cc, expected_cc)

    def test_send_notification_on_success(self) -> None:
        """One notification email is sent."""
        to = ["a@example.com"]
        cc = ["cc@example.com"]

        self.assert_send_notification(
            WorkRequest.Results.SUCCESS,
            event_name='on_success',
            notification_data=NotificationDataEmail.parse_obj(
                {"to": to, "cc": cc}
            ),
            expected_to=to,
            expected_cc=cc,
            expected_subject=(
                "WorkRequest {work_request_id} completed in success"
            ),
        )

    def test_send_notification_no_to(self) -> None:
        """One notification email is sent."""
        cc = ["cc@example.com"]

        self.assert_send_notification(
            WorkRequest.Results.SUCCESS,
            event_name='on_success',
            notification_data=NotificationDataEmail.parse_obj({"cc": cc}),
            expected_cc=cc,
            expected_subject=(
                "WorkRequest {work_request_id} completed in success"
            ),
        )

    def test_send_notification_on_failure(self) -> None:
        """
        One notification email is sent.

        The subject {invalid_variable} is not replaced
        """
        to = ["a@example.com"]
        self.assert_send_notification(
            WorkRequest.Results.FAILURE,
            notification_data=NotificationDataEmail.parse_obj(
                {
                    "to": to,
                    "subject": (
                        "Bad news: $work_request_id completed "
                        "in $work_request_result $invalid_variable"
                    ),
                }
            ),
            expected_to=to,
            expected_subject="Bad news: {work_request_id} completed in failure "
            "$invalid_variable",
        )

    def test_send_notification_on_error(self) -> None:
        """One notification email is sent."""
        to = ["a@example.com"]
        cc = ["cc@example.com"]

        self.assert_send_notification(
            WorkRequest.Results.ERROR,
            notification_data=NotificationDataEmail.parse_obj(
                {"to": to, "cc": cc}
            ),
            expected_to=to,
            expected_cc=cc,
            expected_subject="WorkRequest {work_request_id} completed in error",
        )

    def test_send_notification_on_error_without_data(self) -> None:
        """Mail is sent, WorkRequest notification's data is not provided."""
        self.assert_send_notification(
            WorkRequest.Results.ERROR,
            expected_subject="WorkRequest {work_request_id} completed in error",
        )

    def test_no_notification_is_sent(self) -> None:
        """No mail notification is sent."""
        for result in (
            WorkRequest.Results.SUCCESS,
            WorkRequest.Results.FAILURE,
        ):
            work_request: WorkRequest = self.playground.create_work_request(
                status=WorkRequest.Statuses.RUNNING,
            )
            work_request.event_reactions = EventReactions()
            with mock.patch(
                'debusine.db.models.WorkRequest'
                '.process_update_collection_with_artifacts'
            ):
                work_request.mark_completed(result)
                self.assertEqual(len(mail.outbox), 0)

    def test_log_notification_channel_does_not_exist(self) -> None:
        """Log contains debug error that notification channel does not exist."""
        channel_name = "does-not-exist"
        event_reactions = EventReactions(
            on_failure=[
                ActionSendNotification(
                    channel=channel_name,
                    data=NotificationDataEmail(
                        to=[pydantic.EmailStr("a@example.com")]
                    ),
                )
            ]
        )
        work_request: WorkRequest = self.playground.create_work_request(
            status=WorkRequest.Statuses.RUNNING, event_reactions=event_reactions
        )

        expected_log = (
            f"WorkRequest {work_request.id}: work-request-completed "
            "notification cannot be sent: "
            f"NotificationChannel {channel_name} does not exist"
        )
        with self.assertLogsContains(
            expected_log, logger="debusine", level=logging.DEBUG
        ):
            work_request.mark_completed(WorkRequest.Results.FAILURE)

    def test_send_work_request_completed_fail_logs(self) -> None:
        """Send email fails: log failure."""
        work_request = self.playground.create_work_request()
        notification_channel = NotificationChannel.objects.create(
            name="deblts-email",
            method=NotificationChannel.Methods.EMAIL,
            data={
                "from": "sender@debusine.example.org",
                "to": ["rcpt@example.com"],
            },
        )

        work_request_notification = ActionSendNotification(channel="test")
        body = "Not used"

        email_send_patch = mock.patch.object(
            EmailMessage, "send", return_value=0
        )
        mocked = email_send_patch.start()
        self.addCleanup(email_send_patch.stop)

        expected_log = (
            f"NotificationChannel email notification for "
            f"WorkRequest {work_request.id} failed sending"
        )
        with self.assertLogsContains(
            expected_log, logger="debusine", level=logging.WARNING
        ):
            notifications._send_work_request_completed_notification(
                notification_channel,
                work_request,
                work_request_notification,
                body,
            )

        mocked.assert_called_once_with(fail_silently=True)
