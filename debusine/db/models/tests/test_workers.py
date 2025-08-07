# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the worker models."""

import datetime
from typing import ClassVar

from django.utils import timezone

from debusine.db.models import Token, WorkRequest, Worker, WorkerPool
from debusine.db.models.workers import WorkerManager
from debusine.tasks.models import WorkerType
from debusine.test.django import TestCase


class WorkerManagerTests(TestCase):
    """Tests for the WorkerManager."""

    def test_connected(self) -> None:
        """WorkerManager.connected() return the connected Workers."""
        worker_connected = Worker.objects.create_with_fqdn(
            'connected-worker', Token.objects.create()
        )

        worker_connected.mark_connected()

        Worker.objects.create_with_fqdn(
            'not-connected-worker',
            Token.objects.create(),
        )

        self.assertQuerySetEqual(Worker.objects.connected(), [worker_connected])

    def test_active(self) -> None:
        """Test active() filter."""
        worker_pool = self.playground.create_worker_pool()
        workers: dict[str, Worker] = {}

        workers["static_disconnected"] = self.playground.create_worker(
            fqdn="disconnected.static.lan"
        )

        workers["static_connected_idle"] = self.playground.create_worker(
            fqdn="idle.connected.static.lan"
        )

        workers["dynamic_terminated"] = self.playground.create_worker(
            fqdn="disconnected.dynamic.lan", worker_pool=worker_pool
        )
        workers["dynamic_terminated"].instance_created_at = None
        workers["dynamic_terminated"].save()

        workers["dynamic_disconnected"] = self.playground.create_worker(
            fqdn="disconnected.dynamic.lan", worker_pool=worker_pool
        )

        workers["dynamic_connected_idle"] = self.playground.create_worker(
            fqdn="idle.connected.dynamic.lan", worker_pool=worker_pool
        )
        workers["dynamic_connected_idle"].connected_at = timezone.now()
        workers["dynamic_connected_idle"].save()

        workers["dynamic_connected_busy"] = self.playground.create_worker(
            fqdn="busy.connected.dynamic.lan", worker_pool=worker_pool
        )
        workers["dynamic_connected_busy"].connected_at = timezone.now()
        workers["dynamic_connected_busy"].save()
        wr = self.playground.create_work_request()
        wr.assign_worker(workers["dynamic_connected_busy"])
        wr.mark_running()

        workers["dynamic_connected_now_idle"] = self.playground.create_worker(
            fqdn="now_idle.connected.dynamic.lan", worker_pool=worker_pool
        )
        workers["dynamic_connected_now_idle"].connected_at = timezone.now()
        workers["dynamic_connected_now_idle"].save()
        wr = self.playground.create_work_request()
        wr.assign_worker(workers["dynamic_connected_now_idle"])
        wr.mark_completed(WorkRequest.Results.SUCCESS)

        for worker, included in (
            ("static_disconnected", True),
            ("static_connected_idle", True),
            ("dynamic_terminated", False),
            ("dynamic_disconnected", True),
            ("dynamic_connected_idle", True),
            ("dynamic_connected_busy", True),
            ("dynamic_connected_now_idle", True),
        ):
            with self.subTest(worker=worker):
                self.assertEqual(
                    Worker.objects.active()
                    .filter(pk=workers[worker].pk)
                    .exists(),
                    included,
                )

    def test_waiting_for_work_request(self) -> None:
        """Test WorkerManager.waiting_for_work_request() return a Worker."""
        worker = Worker.objects.create_with_fqdn(
            'worker-a', Token.objects.create(enabled=True)
        )

        # WorkerManagement.waiting_for_work_request: returns no workers because
        # the worker is not connected
        self.assertQuerySetEqual(Worker.objects.waiting_for_work_request(), [])

        worker.mark_connected()

        # Now the Worker is ready to have a task assigned
        self.assertQuerySetEqual(
            Worker.objects.waiting_for_work_request(), [worker]
        )

        # A task is assigned to the worker
        work_request = self.playground.create_work_request(worker=worker)

        # The worker is not ready (it is busy with a task assigned)
        self.assertQuerySetEqual(Worker.objects.waiting_for_work_request(), [])

        # The task finished
        work_request.status = WorkRequest.Statuses.COMPLETED
        work_request.save()

        # The worker is ready: the WorkRequest that had assigned finished
        self.assertQuerySetEqual(
            Worker.objects.waiting_for_work_request(), [worker]
        )

    def test_waiting_for_work_request_celery(self) -> None:
        """
        Test WorkerManager.waiting_for_work_request() with Celery workers.

        Celery workers have no tokens, so they require special handling.
        """
        worker = Worker.objects.get_or_create_celery()
        worker.concurrency = 3
        worker.save()

        # WorkerManagement.waiting_for_work_request: returns no workers because
        # the worker is not connected
        self.assertQuerySetEqual(Worker.objects.waiting_for_work_request(), [])

        worker.mark_connected()

        # Now the Worker is ready to have a task assigned
        self.assertQuerySetEqual(
            Worker.objects.waiting_for_work_request(), [worker]
        )

        # Assign two tasks to the worker; it can still accept more work
        for _ in range(2):
            work_request = self.playground.create_work_request(worker=worker)
            self.assertQuerySetEqual(
                Worker.objects.waiting_for_work_request(), [worker]
            )

        # Assign a third task to the worker; it is now as busy as it allows
        work_request = self.playground.create_work_request(worker=worker)
        self.assertQuerySetEqual(Worker.objects.waiting_for_work_request(), [])

        # The third task finished
        work_request.status = WorkRequest.Statuses.COMPLETED
        work_request.save()

        # Although it is still running some tasks, the worker can accept
        # more work again
        self.assertQuerySetEqual(
            Worker.objects.waiting_for_work_request(), [worker]
        )

    def test_waiting_for_work_request_no_return_disabled_workers(self) -> None:
        """Test WorkerManager.waiting_for_work_request() no return disabled."""
        worker_enabled = Worker.objects.create_with_fqdn(
            "worker-enabled", Token.objects.create(enabled=True)
        )
        worker_enabled.mark_connected()

        worker_disabled = Worker.objects.create_with_fqdn(
            "worker-disabled", Token.objects.create(enabled=False)
        )
        worker_disabled.mark_connected()

        self.assertQuerySetEqual(
            Worker.objects.waiting_for_work_request(), [worker_enabled]
        )

    def test_waiting_for_work_request_static_first(self) -> None:
        """Ensure waiting_for_work_request() returns static workers first."""
        worker_static = Worker.objects.create_with_fqdn(
            "worker-static", Token.objects.create(enabled=True)
        )

        worker_cloud = Worker.objects.create_with_fqdn(
            "worker-cloud", Token.objects.create(enabled=True)
        )
        worker_cloud.worker_pool = WorkerPool.objects.create(
            name="test",
            provider_account=(
                self.playground.create_cloud_provider_account_asset()
            ),
            registered_at=timezone.now(),
        )
        worker_cloud.save()

        worker_cloud.mark_connected()
        worker_static.mark_connected()

        self.assertQuerySetEqual(
            Worker.objects.waiting_for_work_request(),
            [worker_static, worker_cloud],
        )

    def test_create_with_fqdn_new_fqdn(self) -> None:
        """WorkerManager.create_with_fqdn() return a worker."""
        token = Token.objects.create()
        worker = Worker.objects.create_with_fqdn(
            'a-new-and-unique-name', token=token
        )

        self.assertEqual(worker.name, 'a-new-and-unique-name')
        self.assertEqual(worker.token, token)
        self.assertIsNotNone(worker.pk)

    def test_create_with_fqdn_duplicate_fqdn(self) -> None:
        """
        WorkerManager.create_with_fqdn() return a worker.

        The name ends with -2 because 'connected-worker' is already used.
        """
        Worker.objects.create_with_fqdn(
            'connected-worker', token=Token.objects.create()
        )

        token = Token.objects.create()
        worker = Worker.objects.create_with_fqdn(
            'connected-worker', token=token
        )

        self.assertEqual(worker.name, 'connected-worker-2')
        self.assertEqual(worker.token, token)
        self.assertEqual(worker.worker_type, WorkerType.EXTERNAL)
        self.assertIsNotNone(worker.pk)

    def test_create_pool_members(self) -> None:
        worker_pool = self.playground.create_worker_pool(name="pool")
        # 1 existing worker in the middle of our scheme
        Worker.objects.create_with_fqdn(
            "pool-002", token=Token.objects.create()
        )

        before = timezone.now()
        Worker.objects.create_pool_members(worker_pool, 3)
        after = timezone.now()

        self.assertQuerySetEqual(
            Worker.objects.filter(
                worker_pool=worker_pool,
                activation_token__isnull=False,
                activation_token__expire_at__gte=(
                    before + datetime.timedelta(minutes=15)
                ),
                activation_token__expire_at__lte=(
                    after + datetime.timedelta(minutes=15)
                ),
                activation_token__enabled=True,
                token__isnull=True,
            )
            .order_by("name")
            .values_list("name", flat=True),
            ["pool-001", "pool-003", "pool-004"],
        )

    def test_get_or_create_celery(self) -> None:
        """WorkerManager.get_or_create_celery returns a Celery worker."""
        worker = Worker.objects.get_or_create_celery()

        self.assertEqual(worker.name, "celery")
        self.assertIsNone(worker.token)
        self.assertEqual(worker.worker_type, WorkerType.CELERY)
        self.assertIsNotNone(worker.pk)

        self.assertEqual(Worker.objects.get_or_create_celery(), worker)

    def test_slugify_with_suffix_counter_1(self) -> None:
        """WorkerManager._generate_unique_name does not append '-1'."""
        self.assertEqual(
            WorkerManager._generate_unique_name('worker.lan', 1), 'worker-lan'
        )

    def test_slugify_with_suffix_counter_3(self) -> None:
        """WorkerManager._generate_unique_name appends '-3'."""
        self.assertEqual(
            WorkerManager._generate_unique_name('worker.lan', 3), 'worker-lan-3'
        )

    def test_get_worker_by_token_or_none_return_none(self) -> None:
        """WorkerManager.get_worker_by_token_or_none() return None."""
        self.assertIsNone(
            Worker.objects.get_worker_by_token_key_or_none('non-existing-key')
        )

    def test_get_worker_by_token_or_none_return_worker(self) -> None:
        """WorkerManager.get_worker_by_token_or_none() return the Worker."""
        token = Token.objects.create()

        worker = Worker.objects.create_with_fqdn('worker-a', token)

        self.assertEqual(
            Worker.objects.get_worker_by_token_key_or_none(token.key), worker
        )

    def test_get_worker_or_none_return_worker(self) -> None:
        """WorkerManager.get_worker_or_none() return the Worker."""
        token = Token.objects.create()

        worker = Worker.objects.create_with_fqdn('worker-a', token)

        self.assertEqual(Worker.objects.get_worker_or_none('worker-a'), worker)

    def test_get_worker_or_none_return_none(self) -> None:
        """WorkerManager.get_worker_or_none() return None."""
        self.assertIsNone(Worker.objects.get_worker_or_none('does-not-exist'))

    def test_with_idle_time_no_work_requests_not_connected(self) -> None:
        """Test idle_time with no work requests assigned and never connected."""
        worker = Worker.objects.create_with_fqdn(
            'worker-a', Token.objects.create(enabled=True)
        )
        worker.registered_at = timezone.now() - datetime.timedelta(days=1)
        worker.save()

        idle_time = (
            Worker.objects.with_idle_time().get(pk=worker.pk)
        ).idle_time
        self.assertGreaterEqual(idle_time, 86400)
        self.assertLessEqual(idle_time, 86400 + 3600)

    def test_with_idle_time_no_work_requests_connected(self) -> None:
        """Compute idle_time with no work requests assigned and connected."""
        worker = Worker.objects.create_with_fqdn(
            'worker-a', Token.objects.create(enabled=True)
        )
        worker.registered_at = timezone.now() - datetime.timedelta(days=2)
        worker.instance_created_at = timezone.now() - datetime.timedelta(days=1)
        worker.save()

        idle_time = (
            Worker.objects.with_idle_time().get(pk=worker.pk)
        ).idle_time
        self.assertGreaterEqual(idle_time, 86400)
        self.assertLessEqual(idle_time, 86400 + 3600)

    def test_with_idle_time_running_task(self) -> None:
        """Compute idle_time with a running task."""
        worker = Worker.objects.create_with_fqdn(
            'worker-a', Token.objects.create(enabled=True)
        )
        worker.registered_at = timezone.now() - datetime.timedelta(days=2)
        worker.instance_created_at = timezone.now() - datetime.timedelta(days=1)
        worker.save()
        work_request = self.playground.create_work_request(worker=worker)
        work_request.mark_running()
        worker.refresh_from_db()
        self.assertTrue(worker.is_busy())

        idle_time = (
            Worker.objects.with_idle_time().get(pk=worker.pk)
        ).idle_time
        self.assertGreaterEqual(idle_time, 0)

    def test_with_idle_time_completed_task(self) -> None:
        """Compute idle_time with a running task."""
        worker = Worker.objects.create_with_fqdn(
            'worker-a', Token.objects.create(enabled=True)
        )
        worker.registered_at = timezone.now() - datetime.timedelta(days=2)
        worker.instance_created_at = timezone.now() - datetime.timedelta(days=1)
        worker.save()
        work_request = self.playground.create_work_request(worker=worker)
        work_request.mark_running()
        work_request.mark_completed(WorkRequest.Results.SUCCESS)
        work_request.completed_at = timezone.now() - datetime.timedelta(hours=6)
        work_request.save()

        idle_time = (
            Worker.objects.with_idle_time().get(pk=worker.pk)
        ).idle_time
        self.assertGreaterEqual(idle_time, 6 * 3600)
        self.assertLessEqual(idle_time, 7 * 3600)

    def test_mark_all_disconnected(self) -> None:
        """WorkerManager.mark_all_disconnected() affects non-Celery workers."""
        worker_connected_external = self.playground.create_worker(
            fqdn="connected-external"
        )
        worker_connected_external.mark_connected()
        worker_connected_signing = self.playground.create_worker(
            worker_type=WorkerType.SIGNING, fqdn="connected-signing"
        )
        worker_connected_signing.mark_connected()
        worker_connected_celery = Worker.objects.get_or_create_celery()
        worker_connected_celery.mark_connected()
        worker_disconnected_external = self.playground.create_worker(
            fqdn="disconnected-external"
        )
        worker_disconnected_signing = self.playground.create_worker(
            worker_type=WorkerType.SIGNING, fqdn="disconnected-signing"
        )

        Worker.objects.mark_all_disconnected()

        worker_connected_celery.refresh_from_db()
        self.assertTrue(worker_connected_celery.connected())
        for disconnected in (
            worker_connected_external,
            worker_connected_signing,
            worker_disconnected_external,
            worker_disconnected_signing,
        ):
            disconnected.refresh_from_db()
            self.assertFalse(disconnected.connected())


class WorkerTests(TestCase):
    """Tests for the Worker model."""

    worker: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the Worker for the tests."""
        super().setUpTestData()
        cls.worker = Worker.objects.create_with_fqdn(
            "computer.lan", Token.objects.create()
        )
        cls.worker.static_metadata = {"os": "debian"}
        cls.worker.set_dynamic_metadata({"cpu_cores": "4"})
        cls.worker.save()

    def test_mark_connected(self) -> None:
        """Test mark_connect method."""
        time_before = timezone.now()
        self.assertIsNone(self.worker.connected_at)

        self.worker.mark_connected()
        assert self.worker.connected_at is not None

        self.assertGreaterEqual(self.worker.connected_at, time_before)
        self.assertLessEqual(self.worker.connected_at, timezone.now())

    def test_mark_disconnected(self) -> None:
        """Test mark_disconnected method."""
        self.worker.mark_connected()
        self.assertTrue(self.worker.connected())

        self.worker.mark_disconnected()

        self.assertFalse(self.worker.connected())
        self.assertIsNone(self.worker.connected_at)

    def test_connected(self) -> None:
        """Test connected method."""
        self.assertFalse(self.worker.connected())

        self.worker.connected_at = timezone.now()

        self.assertTrue(self.worker.connected())

    def test_is_busy(self) -> None:
        """Test is_busy method."""
        worker = Worker.objects.create_with_fqdn(
            "test", token=Token.objects.create()
        )
        self.assertFalse(worker.is_busy())

        work_request = self.playground.create_work_request(worker=worker)

        self.assertEqual(work_request.status, WorkRequest.Statuses.PENDING)
        self.assertTrue(worker.is_busy())

        work_request.assign_worker(worker)

        self.assertTrue(worker.is_busy())

        work_request.mark_running()

        self.assertTrue(worker.is_busy())

        work_request.mark_aborted()

        self.assertFalse(worker.is_busy())

    def test_is_busy_concurrency(self) -> None:
        """The is_busy method handles workers with concurrency > 1."""
        worker = Worker.objects.get_or_create_celery()
        worker.concurrency = 3
        worker.save()
        for _ in range(3):
            self.assertFalse(worker.is_busy())
            work_request = self.playground.create_work_request(worker=worker)
            work_request.assign_worker(worker)
        self.assertTrue(worker.is_busy())
        work_request.mark_aborted()
        self.assertFalse(worker.is_busy())

    def test_running_work_requests(self) -> None:
        """The running_work_requests return expected work requests."""
        worker_1 = self.playground.create_worker()
        worker_1.concurrency = 3
        worker_1.save()

        worker_2 = self.playground.create_worker()

        work_request_1 = self.playground.create_work_request(
            worker=worker_1, mark_running=True
        )
        work_request_2 = self.playground.create_work_request(
            worker=worker_1, mark_running=True
        )
        self.playground.create_work_request(worker=worker_1, mark_running=False)
        self.playground.create_work_request(worker=worker_2, mark_running=True)

        self.assertQuerySetEqual(
            worker_1.running_work_requests(), [work_request_1, work_request_2]
        )

    def test_metadata_no_conflict(self) -> None:
        """Test metadata method: return all the metadata."""
        self.assertEqual(
            self.worker.metadata(), {'cpu_cores': '4', 'os': 'debian'}
        )

    def test_metadata_with_conflict(self) -> None:
        """
        Test metadata method: return all the metadata.

        static_metadata has priority over dynamic_metadata
        """
        # Assert initial state
        self.assertEqual(self.worker.dynamic_metadata['cpu_cores'], '4')

        # Add new static_metadata key
        self.worker.static_metadata['cpu_cores'] = '8'

        self.assertEqual(
            self.worker.metadata(), {'cpu_cores': '8', 'os': 'debian'}
        )

    def test_metadata_is_deep_copy(self) -> None:
        """Test metadata does a deep copy."""
        self.worker.dynamic_metadata['list'] = ['foo', 'bar']
        self.worker.metadata()['list'].append('baz')

        self.assertEqual(self.worker.dynamic_metadata['list'], ['foo', 'bar'])

    def test_set_dynamic_metadata(self) -> None:
        """Worker.set_dynamic_metadata sets the dynamic metadata."""
        self.worker.dynamic_metadata = {}
        self.worker.dynamic_metadata_updated_at = None
        self.worker.save()

        dynamic_metadata = {"cpu_cores": 4, "ram": "16"}
        self.worker.set_dynamic_metadata(dynamic_metadata)
        assert self.worker.dynamic_metadata_updated_at is not None

        self.worker.refresh_from_db()

        self.assertEqual(self.worker.dynamic_metadata, dynamic_metadata)
        self.assertLessEqual(
            self.worker.dynamic_metadata_updated_at, timezone.now()
        )

    def test_str(self) -> None:
        """Test WorkerTests.__str__."""
        self.assertEqual(
            self.worker.__str__(),
            f"Id: {self.worker.id} Name: {self.worker.name}",
        )

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""
        self.assertEqual(
            self.worker.get_absolute_url(),
            f"/-/status/workers/{self.worker.name}/",
        )
