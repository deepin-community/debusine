# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Debusine Celery configuration."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from multiprocessing import Lock
from typing import Any
from unittest import mock

from celery import shared_task, signals, states
from celery.contrib.testing.worker import start_worker
from celery.utils.nodenames import gethostname, nodename
from django.test import override_settings
from django_celery_results.models import TaskResult

from debusine.db.models import Worker
from debusine.project.celery import app, make_app
from debusine.tasks.models import WorkerType
from debusine.test.django import TransactionTestCase

_lock = Lock()


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task  # type: ignore[misc]
def _locked_noop() -> None:
    """Sample Celery task to do nothing, once a lock is available."""
    with _lock:
        pass


# mypy complains that celery.shared_task is untyped, which is true, but we
# can't fix that here.
@shared_task  # type: ignore[misc]
def _locked_reverse_string(argument: str, **kwargs: Any) -> str:
    """Sample Celery task to reverse a string, once a lock is available."""
    with _lock:
        return "".join(reversed(argument))


@override_settings(CELERY_BROKER_URL="memory://")
class TestStorePendingState(TransactionTestCase):
    """Tests for :py:func:`store_pending_state`."""

    def setUp(self) -> None:
        super().setUp()
        self.app = make_app()

    def test_result_states(self) -> None:
        """The task's state can be tracked in the database."""
        with (
            mock.patch("debusine.project.settings.defaults.test_data_override"),
            start_worker(self.app, perform_ping_check=False) as celery_worker,
        ):
            # Schedule a task, but with a lock held so that we get a chance
            # to look at its result in the database before it finishes.
            with _lock:
                result = _locked_reverse_string.delay(
                    "hello", foo="bar", value=1
                )

                expected_task_name = f"{__name__}._locked_reverse_string"
                expected_task_args = ["hello"]
                expected_task_kwargs = {"foo": "bar", "value": 1}
                task_result = TaskResult.objects.get(task_id=result.id)
                self.assertEqual(task_result.status, states.PENDING)
                self.assertEqual(task_result.task_name, expected_task_name)
                self.assertEqual(
                    json.loads(task_result.task_args), expected_task_args
                )
                self.assertEqual(
                    json.loads(task_result.task_kwargs), expected_task_kwargs
                )

            # Release the lock to allow the task to complete.  Its result in
            # the database is updated with the new status.
            self.assertEqual(result.get(), "olleh")

            task_result.refresh_from_db()
            self.assertEqual(task_result.status, states.SUCCESS)
            self.assertEqual(task_result.task_name, expected_task_name)
            self.assertEqual(
                json.loads(task_result.task_args), expected_task_args
            )
            self.assertEqual(
                json.loads(task_result.task_kwargs), expected_task_kwargs
            )

            signals.worker_shutdown.send(sender=celery_worker)

    def test_result_states_no_arguments(self) -> None:
        """A task with no arguments is tracked correctly in the database."""
        with (
            mock.patch("debusine.project.settings.defaults.test_data_override"),
            start_worker(self.app, perform_ping_check=False) as celery_worker,
        ):
            # Schedule a task, but with a lock held so that we get a chance
            # to look at its result in the database before it finishes.
            with _lock:
                result = _locked_noop.apply_async()

                expected_task_name = f"{__name__}._locked_noop"
                task_result = TaskResult.objects.get(task_id=result.id)
                self.assertEqual(task_result.status, states.PENDING)
                self.assertEqual(task_result.task_name, expected_task_name)
                self.assertEqual(json.loads(task_result.task_args), [])
                self.assertEqual(json.loads(task_result.task_kwargs), {})

            # Release the lock to allow the task to complete.  Its result in
            # the database is updated with the new status.
            self.assertIsNone(result.get())

            task_result.refresh_from_db()
            self.assertEqual(task_result.status, states.SUCCESS)
            self.assertEqual(task_result.task_name, expected_task_name)
            self.assertEqual(json.loads(task_result.task_args), [])
            self.assertEqual(json.loads(task_result.task_kwargs), {})

            signals.worker_shutdown.send(sender=celery_worker)


@override_settings(CELERY_BROKER_URL="memory://")
class TestConnectWorker(TransactionTestCase):
    """Tests for :py:func:`connect_worker`."""

    def setUp(self) -> None:
        super().setUp()
        self.app = make_app()

    @contextmanager
    def patch_anon_nodename(self, hostname: str) -> Generator[None, None, None]:
        """
        Patch Celery's hostname.

        Celery >= 5.5 allows passing a custom hostname to
        :py:func:`start_worker`, but earlier versions don't.
        """
        with mock.patch(
            "celery.contrib.testing.worker.anon_nodename", return_value=hostname
        ):
            yield

    def test_server_task_worker(self) -> None:
        """The main worker creates a Worker instance that runs server tasks."""
        with (
            self.patch_anon_nodename(
                hostname=nodename("celery", gethostname())
            ),
            mock.patch("debusine.project.settings.defaults.test_data_override"),
            # Set a concurrency that's unlikely to be the actual number of
            # CPUs we have.
            start_worker(
                self.app, concurrency=5, perform_ping_check=False
            ) as celery_worker,
        ):
            worker = Worker.objects.get(worker_type=WorkerType.CELERY)
            self.assertEqual(worker.concurrency, 5)
            self.assertEqual(
                worker.dynamic_metadata["system:worker_type"], WorkerType.CELERY
            )
            self.assertIsNotNone(worker.connected_at)

            signals.worker_shutdown.send(sender=celery_worker)

            worker.refresh_from_db()
            self.assertIsNone(worker.connected_at)

    def test_scheduler_worker(self) -> None:
        """The scheduler worker does not create a Worker instance."""
        with (
            self.patch_anon_nodename(
                hostname=nodename("scheduler", gethostname())
            ),
            mock.patch("debusine.project.settings.defaults.test_data_override"),
            start_worker(
                app, concurrency=1, perform_ping_check=False
            ) as celery_worker,
        ):
            self.assertFalse(
                Worker.objects.filter(worker_type=WorkerType.CELERY).exists()
            )

            signals.worker_shutdown.send(sender=celery_worker)

            self.assertFalse(
                Worker.objects.filter(worker_type=WorkerType.CELERY).exists()
            )

    def test_provisioner_worker(self) -> None:
        """The provisioner worker does not create a Worker instance."""
        with (
            self.patch_anon_nodename(
                hostname=nodename("provisioner", gethostname())
            ),
            mock.patch("debusine.project.settings.defaults.test_data_override"),
            start_worker(
                app, concurrency=1, perform_ping_check=False
            ) as celery_worker,
        ):
            self.assertFalse(
                Worker.objects.filter(worker_type=WorkerType.CELERY).exists()
            )

            signals.worker_shutdown.send(sender=celery_worker)

            self.assertFalse(
                Worker.objects.filter(worker_type=WorkerType.CELERY).exists()
            )
