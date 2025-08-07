# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Debusine Celery configuration."""

from collections.abc import Generator
from contextlib import contextmanager
from unittest import mock

from celery import signals
from celery.contrib.testing.worker import start_worker
from celery.utils.nodenames import gethostname, nodename

from debusine.db.models import Worker
from debusine.project.celery import app
from debusine.tasks.models import WorkerType
from debusine.test.django import TransactionTestCase


class TestConnectWorker(TransactionTestCase):
    """Tests for :py:func:`connect_worker`."""

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
            # Set a concurrency that's unlikely to be the actual number of
            # CPUs we have.
            start_worker(
                app, concurrency=5, perform_ping_check=False
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
