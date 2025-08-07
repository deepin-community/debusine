# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine celery configuration."""

import os
from typing import Any

from celery import Celery
from celery.signals import worker_init, worker_shutdown
from celery.worker import WorkController

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "debusine.project.settings")

app = Celery("debusine")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# mypy complains that worker_init.connect is untyped, which is true, but we
# can't fix that here.
@worker_init.connect  # type: ignore[misc]
def connect_worker(*, sender: WorkController, **kwargs: Any) -> None:
    """Set up and connect an appropriate :class:`Worker` when Celery starts."""
    from debusine.db.models import Worker
    from debusine.tasks import BaseTask
    from debusine.tasks.executors import analyze_worker_all_executors
    from debusine.tasks.models import WorkerType
    from debusine.worker.system_information import system_metadata

    if sender.hostname.startswith("celery@"):
        worker = Worker.objects.get_or_create_celery()
        worker.concurrency = sender.concurrency
        worker.save()

        metadata = {
            **system_metadata(WorkerType.CELERY),
            **analyze_worker_all_executors(),
            **BaseTask.analyze_worker_all_tasks(),
        }

        worker.set_dynamic_metadata(metadata)
        worker.mark_connected()


# mypy complains that worker_shutdown.connect is untyped, which is true, but
# we can't fix that here.
@worker_shutdown.connect  # type: ignore[misc]
def disconnect_worker(*, sender: WorkController, **kwargs: Any) -> None:
    """Disconnect the appropriate :class:`Worker` when Celery exits."""
    from debusine.db.models import Worker

    if sender.hostname.startswith("celery@"):
        Worker.objects.get_or_create_celery().mark_disconnected()
