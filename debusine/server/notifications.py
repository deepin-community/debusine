# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Notifications using channels or NotificationChannels."""

import logging
import string
from typing import Any, TYPE_CHECKING

from asgiref.sync import async_to_sync
from channels.layers import DEFAULT_CHANNEL_LAYER, channel_layers
from channels_redis.core import RedisChannelLayer
from django.core.mail import EmailMessage

from debusine.tasks.models import ActionSendNotification, EventReaction

if TYPE_CHECKING:
    from debusine.db.models import NotificationChannel, Token, WorkRequest

logger = logging.getLogger(__name__)


async def _group_send_and_close(key: str, message: dict[str, Any]) -> None:
    """
    Send a message to a group via a channel layer, then close pools if needed.

    See https://github.com/django/channels_redis/issues/332.
    """
    channel_layer = channel_layers[DEFAULT_CHANNEL_LAYER]
    await channel_layer.group_send(key, message)
    if isinstance(channel_layer, RedisChannelLayer):  # pragma: no cover
        await channel_layer.close_pools()


def notify_work_request_assigned(work_request: "WorkRequest") -> None:
    """Send a channel message of type 'work_request_assigned'."""
    if work_request.worker is None or work_request.worker.token is None:
        return

    async_to_sync(_group_send_and_close)(
        work_request.worker.token.hash, {"type": "work_request.assigned"}
    )


def notify_worker_token_disabled(token: "Token") -> None:
    """Notify to the WorkerConsumer that a worker has been disabled."""
    if not hasattr(token, "worker"):
        return

    async_to_sync(_group_send_and_close)(
        token.hash, {"type": "worker.disabled"}
    )


def notify_work_request_completed(
    work_request: "WorkRequest", notifications: list[EventReaction]
) -> None:
    """Notify to the interested parties that work_request is completed."""
    # Send to the channel_layer (Websockets) that the work request has completed
    assert work_request.completed_at is not None
    async_to_sync(_group_send_and_close)(
        "work_request_completed",
        {
            "type": "work_request_completed",
            "work_request_id": work_request.id,
            "workspace_id": work_request.workspace.id,
            "completed_at": work_request.completed_at.isoformat(),
            "result": work_request.result,
        },
    )

    from debusine.db.models import NotificationChannel

    # Prepare the notifications to the Notification channel based on the
    # WorkRequest "notifications" section in event_reactions (e.g. email
    # notifications)
    for work_request_notification in notifications:
        assert isinstance(work_request_notification, ActionSendNotification)
        name = work_request_notification.channel
        try:
            notification_channel = NotificationChannel.objects.get(name=name)
        except NotificationChannel.DoesNotExist:
            logger.debug(
                "WorkRequest %s: work-request-completed notification "
                "cannot be sent: "
                "NotificationChannel %s does not exist",
                work_request.id,
                name,
            )
            continue

        body = "Sent by debusine"

        _send_work_request_completed_notification(
            notification_channel,
            work_request,
            work_request_notification,
            body,
        )


def _send_work_request_completed_notification(
    notification_channel: "NotificationChannel",
    work_request: "WorkRequest",
    work_request_notification: ActionSendNotification,
    body: str,
) -> None:
    from debusine.db.models import NotificationChannel

    if notification_channel.method != NotificationChannel.Methods.EMAIL:
        raise NotImplementedError()

    email_data = {**notification_channel.data}
    if work_request_notification.data is not None:
        email_data.update(
            (k, v) for k, v in work_request_notification.data if v is not None
        )

    if (subject := email_data.get("subject")) is None:
        subject = (
            "WorkRequest $work_request_id completed in $work_request_result"
        )
    subject = string.Template(subject).safe_substitute(
        work_request_id=work_request.id, work_request_result=work_request.result
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=email_data["from"],
        to=email_data["to"],
        cc=email_data.get("cc"),
    )

    success = email.send(fail_silently=True)

    if success == 0:
        logger.warning(
            "NotificationChannel email notification for WorkRequest "
            "%s failed sending",
            work_request.id,
        )
