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
from types import SimpleNamespace
from typing import Any

from celery import Celery, Task, states
from celery.result import AsyncResult
from celery.signals import before_task_publish, worker_init, worker_shutdown
from celery.worker import WorkController

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "debusine.project.settings")


# mypy complains that Task is untyped, which is true, but we can't fix that
# here.
class TaskWithBetterArgumentRepresentation(Task):  # type: ignore[misc]
    """
    Custom task class to control result argument serialization.

    See https://github.com/celery/django-celery-results/issues/113.
    """

    def apply_async(
        self,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        **options: Any,
    ) -> AsyncResult:
        """
        Override Celery's default argument encoding in the result backend.

        By default, Celery encodes arguments using ``repr()`` when storing
        them in the result backend, which makes it difficult to query them.
        Passing them straight through allows ``django_celery_results`` to
        apply JSON encoding instead.
        """
        options["argsrepr"] = args or ()
        options["kwargsrepr"] = kwargs or {}
        return super().apply_async(args=args, kwargs=kwargs, **options)


def make_app() -> Celery:
    """Make a Celery application."""
    app = Celery("debusine", task_cls=TaskWithBetterArgumentRepresentation)
    app.config_from_object("django.conf:settings", namespace="CELERY")
    app.autodiscover_tasks()
    return app


app = make_app()


# Based loosely on:
#   https://stackoverflow.com/q/9824172
#   https://github.com/celery/django-celery-results/issues/286
# mypy complains that before_task_publish.connect is untyped, which is true,
# but we can't fix that here.
@before_task_publish.connect  # type: ignore[misc]
def store_pending_state(
    *, sender: str, headers: dict[str, Any], **kwargs: Any
) -> None:
    """Tell the result backend about newly-published tasks."""
    task = app.tasks.get(sender)
    backend = task.backend if task else app.backend
    backend.store_result(
        headers["id"], None, states.PENDING, request=SimpleNamespace(**headers)
    )


# mypy complains that worker_init.connect is untyped, which is true, but we
# can't fix that here.
@worker_init.connect  # type: ignore[misc]
def connect_worker(*, sender: WorkController, **kwargs: Any) -> None:
    """Set up and connect a :py:class:`Worker` when Celery starts."""
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
    """Disconnect the appropriate :py:class:`Worker` when Celery exits."""
    from debusine.db.models import Worker

    if sender.hostname.startswith("celery@"):
        Worker.objects.get_or_create_celery().mark_disconnected()
