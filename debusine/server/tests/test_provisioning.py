# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for cloud computing provisioning backend functions."""

import datetime
from collections.abc import Generator
from contextlib import contextmanager
from unittest import mock

from django.contrib.postgres.functions import TransactionNow
from django.db import connection
from django.utils import timezone

from debusine.db.models import ScopeWorkerPool, Worker, WorkerPool
from debusine.db.models.worker_pools import (
    WorkerPoolStatistics,
    WorkerPoolTaskExecutionStatistics,
)
from debusine.db.playground import scenarios
from debusine.server.provisioning import (
    ScopeWorkerAllocator,
    WorkerAllocator,
    create_new_workers,
    provision,
    terminate_idle,
)
from debusine.server.worker_pools.models import WorkerPoolLimits
from debusine.test.django import TestCase


class ProvisionTests(TestCase):
    """Test :py:func:`provision`."""

    def test_no_idle(self) -> None:
        """Call create_new_workers if there are no idle workers."""
        with (
            mock.patch(
                "debusine.server.provisioning.terminate_idle",
                return_value=False,
            ) as ti,
            mock.patch(
                "debusine.server.provisioning.create_new_workers"
            ) as cnw,
        ):
            provision()
        ti.assert_called()
        cnw.assert_called()

    def test_has_idle(self) -> None:
        """No not call create_new_workers if there are idle workers."""
        with (
            mock.patch(
                "debusine.server.provisioning.terminate_idle", return_value=True
            ) as ti,
            mock.patch(
                "debusine.server.provisioning.create_new_workers"
            ) as cnw,
        ):
            provision()
        ti.assert_called()
        cnw.assert_not_called()


class TerminateIdleTests(TestCase):
    """Test :py:func:`terminate_idle`."""

    @contextmanager
    def mock_terminate(self) -> Generator[mock.MagicMock, None, None]:
        """Mock WorkerPool.terminate_idle."""
        with mock.patch.object(
            WorkerPool,
            "terminate_worker",
            autospec=True,
        ) as term:
            yield term

    def _create_worker(
        self, idle_minutes: int, worker_pool: WorkerPool | None = None
    ) -> Worker:
        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.registered_at = timezone.now() - datetime.timedelta(
            seconds=idle_minutes * 60
        )
        worker.instance_created_at = timezone.now() - datetime.timedelta(
            seconds=idle_minutes * 60
        )
        worker.save()
        return worker

    def test_no_workers(self) -> None:
        """Call with no workers at all."""
        with self.mock_terminate() as term:
            self.assertFalse(terminate_idle())
        term.assert_not_called()

    def test_no_idle(self) -> None:
        """Call with no idle workers."""
        worker_pool = self.playground.create_worker_pool("test")
        self._create_worker(idle_minutes=1, worker_pool=None)
        self._create_worker(idle_minutes=1, worker_pool=worker_pool)
        with self.mock_terminate() as term:
            self.assertFalse(terminate_idle(minutes=10))
        term.assert_not_called()

    def test_one_static_idle(self) -> None:
        """Call with one idle static worker."""
        worker_pool = self.playground.create_worker_pool("test")
        self._create_worker(idle_minutes=11, worker_pool=None)
        self._create_worker(idle_minutes=1, worker_pool=worker_pool)
        with self.mock_terminate() as term:
            self.assertFalse(terminate_idle(minutes=10))
        term.assert_not_called()

    def test_one_dynamic_idle(self) -> None:
        """Call with one idle cloud worker."""
        worker_pool = self.playground.create_worker_pool("test")
        self._create_worker(idle_minutes=1, worker_pool=None)
        cloud = self._create_worker(idle_minutes=11, worker_pool=worker_pool)
        with self.mock_terminate() as term:
            self.assertTrue(terminate_idle(minutes=10))
        term.assert_called_with(worker_pool, cloud)


class CreateNewWorkersTests(TestCase):
    """Test :py:func:`create_new_workers`."""

    def test_noop(self) -> None:
        """Call the function: it does nothing at the moment."""
        create_new_workers()


class WorkerAllocatorTests(TestCase):
    """Test :py:class:`WorkerAllocator`."""

    def test_static_workers_count_no_workers(self) -> None:
        """Static workers count with no workers is 0."""
        allocator = WorkerAllocator()
        self.assertEqual(allocator.static_workers_count, 0)

    def test_static_workers_count(self) -> None:
        """Static workers count counts only connected static workers."""
        pool = self.playground.create_worker_pool()
        connected_static_worker = self.playground.create_worker()
        connected_static_worker.mark_connected()
        self.playground.create_worker()
        self.playground.create_worker()
        self.playground.create_worker(worker_pool=pool)
        self.playground.create_worker(worker_pool=pool)
        allocator = WorkerAllocator()
        self.assertEqual(allocator.static_workers_count, 1)

    def test_start_of_month(self) -> None:
        """Ensure WorkerAllocator computes start_of_month correctly."""
        utc = datetime.UTC
        for today, expected in (
            (
                datetime.datetime(2025, 1, 2, 3, 4, 5, 6, tzinfo=utc),
                datetime.datetime(2025, 1, 1, tzinfo=utc),
            ),
            (
                datetime.datetime(2025, 1, 1, tzinfo=utc),
                datetime.datetime(2025, 1, 1, tzinfo=utc),
            ),
        ):
            with self.subTest(today):
                with mock.patch(
                    "debusine.server.provisioning.timezone.now",
                    return_value=today,
                ):
                    allocator = WorkerAllocator()
                self.assertEqual(allocator.start_of_month, expected)

    def test_limit_by_instances_no_limits(self) -> None:
        """Call limit_by_max_active_instances with no limits."""
        pool = self.playground.create_worker_pool(
            limits=WorkerPoolLimits(max_active_instances=None)
        )
        worker_allocator = WorkerAllocator()
        for value in 0, 10, 1000:
            with self.subTest(value=value):
                self.assertEqual(
                    worker_allocator.limit_by_max_active_instances(pool, value),
                    value,
                )

    def test_limit_by_instances_no_workers(self) -> None:
        """Call limit_by_max_active_instances with no workers."""
        pool = self.playground.create_worker_pool(
            limits=WorkerPoolLimits(max_active_instances=5)
        )
        worker_allocator = WorkerAllocator()
        for value, expected in (
            (0, 0),
            (1, 1),
            (5, 5),
            (6, 5),
            (10, 5),
            (1000, 5),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    worker_allocator.limit_by_max_active_instances(pool, value),
                    expected,
                )

    def test_limit_by_instances(self) -> None:
        """Call limit_by_max_active_instances with workers."""
        pool = self.playground.create_worker_pool(
            limits=WorkerPoolLimits(max_active_instances=5)
        )
        worker_allocator = WorkerAllocator()
        for value, workers, expected in (
            (0, 1, 0),
            (1, 1, 1),
            (1, 5, 0),
            (1, 6, 0),
            (5, 0, 5),
            (5, 1, 4),
            (5, 5, 0),
            (6, 0, 5),
            (6, 1, 4),
            (6, 5, 0),
        ):
            Worker.objects.all().delete()
            for _ in range(workers):
                self.playground.create_worker(worker_pool=pool)
            with self.subTest(value=value):
                self.assertEqual(
                    worker_allocator.limit_by_max_active_instances(pool, value),
                    expected,
                )

    def test_get_used_seconds_no_stats(self) -> None:
        """Call get_used_seconds with no WorkerPoolStatistics."""
        pool = self.playground.create_worker_pool()
        allocator = WorkerAllocator()
        self.assertEqual(allocator.get_used_seconds(pool), 0)

    def test_get_used_seconds_only_current_month(self) -> None:
        """get_used_seconds uses only data from current month."""
        pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=pool)
        allocator = WorkerAllocator()
        expected_used_seconds = 0

        for since_start_of_month, runtime, runtime_this_month in (
            (-datetime.timedelta(days=60), 1, 0),
            (datetime.timedelta(), 2, 0),
            (datetime.timedelta(seconds=2), 10, 2),
            (datetime.timedelta(days=60), 4, 4),
        ):
            ws = WorkerPoolStatistics.objects.create(
                worker_pool=pool, worker=worker, runtime=runtime
            )
            ws.timestamp = allocator.start_of_month + since_start_of_month
            ws.save()
            expected_used_seconds += runtime_this_month

        self.assertEqual(
            allocator.get_used_seconds(pool), expected_used_seconds
        )

    def test_get_used_seconds_adds_connected(self) -> None:
        """get_used_seconds adds runtime from connected workers."""
        pool = self.playground.create_worker_pool()
        workers = [
            self.playground.create_worker(worker_pool=pool) for _ in range(2)
        ]
        workers.append(
            self.playground.create_worker(
                worker_pool=self.playground.create_worker_pool(
                    name="test2", provider_account=pool.provider_account
                )
            )
        )
        allocator = WorkerAllocator()

        ws = WorkerPoolStatistics.objects.create(
            worker_pool=pool, worker=workers[0], runtime=10
        )
        ws.timestamp = allocator.start_of_month + datetime.timedelta(seconds=2)
        ws.save()
        expected_used_seconds = 2

        workers[0].connected_at = workers[0].instance_created_at = (
            TransactionNow() - datetime.timedelta(minutes=1)
        )
        workers[0].save()
        expected_used_seconds += 60

        workers[1].connected_at = workers[1].instance_created_at = (
            allocator.start_of_month - datetime.timedelta(hours=1)
        )
        workers[1].save()
        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
            [now] = cursor.fetchone()
        expected_used_seconds += int(
            (now - allocator.start_of_month).total_seconds()
        )

        # This worker's runtime is not added, since it's in a different pool.
        workers[2].connected_at = workers[2].instance_created_at = (
            allocator.start_of_month
        )
        workers[2].save()

        self.assertEqual(
            allocator.get_used_seconds(pool), expected_used_seconds
        )

    def test_limit_max_seconds_per_month_no_limit(self) -> None:
        """Call with ScopeWorkerPool having None as limit: no limits."""
        pool = self.playground.create_worker_pool()
        allocator = WorkerAllocator()
        for val in (0, 10, 100, 1000):
            with self.subTest(val=val):
                self.assertEqual(
                    allocator.limit_by_pool_target_max_seconds_per_month(
                        pool, val
                    ),
                    val,
                )

    def test_limit_max_seconds_per_month(self) -> None:
        """Call with various limit scenarios."""
        pool = self.playground.create_worker_pool()
        allocator = WorkerAllocator()
        for limit, used, amount, expected in (
            # There are WorkerPoolStatistics not exceeding target
            # by more than TASK_DURATION_SECONDS
            (1000, 200, 1, 1),
            # There are WorkerPoolStatistics not exceeding target
            # by less than TASK_DURATION_SECONDS
            (1000, 500, 1, 0),
            # There are WorkerPoolStatistics exceeding target
            (1000, 1000, 1, 0),
            (1000, 1200, 1, 0),
            # Amount can get limited not just zeroed
            (6000, 0, 10, 10),
            (6000, 1, 10, 9),
            # Limit of 0 means no allocation allowed
            (0, 0, 1, 0),
        ):
            with (
                self.subTest(limit=limit, used=used, amount=amount),
                mock.patch.object(
                    allocator, "get_used_seconds", return_value=used
                ),
            ):
                pool.limits = {"target_max_seconds_per_month": limit}
                pool.save()
                self.assertEqual(
                    allocator.limit_by_pool_target_max_seconds_per_month(
                        pool, amount
                    ),
                    expected,
                )

    def test_process_scope(self) -> None:
        scope = self.playground.get_default_scope()
        allocator = WorkerAllocator()
        with mock.patch(
            "debusine.server.provisioning.ScopeWorkerAllocator.process_scope",
            autospec=True,
        ) as process_scope:
            allocator.process_scope(scope)

        process_scope.assert_called()
        scope_allocator = process_scope.call_args[0][0]
        self.assertIs(scope_allocator.worker_allocator, allocator)
        self.assertIs(scope_allocator.scope, scope)


class ScopeWorkerAllocatorTests(TestCase):
    """Test :py:class:`ScopeWorkerAllocator`."""

    scenario = scenarios.DefaultContext()

    def test_limit_max_seconds_per_month_no_scopeworkerpool(self) -> None:
        """Call with no ScopeWorkerPool: no limits."""
        pool = self.playground.create_worker_pool()
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        for val in (0, 10, 100, 1000):
            with self.subTest(val=val):
                self.assertEqual(
                    allocator.limit_by_scope_pool_target_max_seconds_per_month(
                        pool, val
                    ),
                    val,
                )

    def test_limit_max_seconds_per_month_no_limit(self) -> None:
        """Call with ScopeWorkerPool having None as limit: no limits."""
        pool = self.playground.create_worker_pool()
        ScopeWorkerPool.objects.create(
            worker_pool=pool,
            scope=self.scenario.scope,
            limits={"target_max_seconds_per_month": None},
        )
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        for val in (0, 10, 100, 1000):
            with self.subTest(val=val):
                self.assertEqual(
                    allocator.limit_by_scope_pool_target_max_seconds_per_month(
                        pool, val
                    ),
                    val,
                )

    def test_limit_max_seconds_per_month(self) -> None:
        """Call with various limit scenarios."""
        pool = self.playground.create_worker_pool()
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        for limit, used, amount, expected in (
            # There are WorkerPoolTaskExecutionStatistics not exceeding target
            # by more than TASK_DURATION_SECONDS
            (1000, 200, 1, 1),
            # There are WorkerPoolTaskExecutionStatistics not exceeding target
            # by less than TASK_DURATION_SECONDS
            (1000, 500, 1, 0),
            # There are WorkerPoolTaskExecutionStatistics exceeding target
            (1000, 1000, 1, 0),
            (1000, 1200, 1, 0),
            # Amount can get limited not just zeroed
            (6000, 0, 10, 10),
            (6000, 1, 10, 9),
            # Limit of 0 means no allocation allowed
            (0, 0, 1, 0),
        ):
            with (
                self.subTest(limit=limit, used=used, amount=amount),
                mock.patch.object(
                    allocator, "get_used_seconds", return_value=used
                ),
            ):
                ScopeWorkerPool.objects.all().delete()
                ScopeWorkerPool.objects.create(
                    worker_pool=pool,
                    scope=self.scenario.scope,
                    limits={"target_max_seconds_per_month": limit},
                )
                self.assertEqual(
                    allocator.limit_by_scope_pool_target_max_seconds_per_month(
                        pool, amount
                    ),
                    expected,
                )

    def test_get_used_seconds_no_stats(self) -> None:
        """Call get_used_seconds with no WorkerPoolTaskExecutionStatistics."""
        pool = self.playground.create_worker_pool()
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        self.assertEqual(allocator.get_used_seconds(pool), 0)

    def test_get_used_seconds_only_current_month(self) -> None:
        """get_used_seconds uses only data from current month."""
        pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=pool)
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        expected_used_seconds = 0

        for since_start_of_month, runtime, runtime_this_month in (
            (-datetime.timedelta(days=60), 1, 0),
            (datetime.timedelta(), 2, 0),
            (datetime.timedelta(seconds=2), 10, 2),
            (datetime.timedelta(days=60), 4, 4),
        ):
            ws = WorkerPoolTaskExecutionStatistics.objects.create(
                worker_pool=pool,
                worker=worker,
                scope=self.scenario.scope,
                runtime=runtime,
            )
            ws.timestamp = (
                worker_allocator.start_of_month + since_start_of_month
            )
            ws.save()
            expected_used_seconds += runtime_this_month

        self.assertEqual(
            allocator.get_used_seconds(pool), expected_used_seconds
        )

    def test_get_used_seconds_only_selected_scope_pool(self) -> None:
        """get_used_seconds uses only data from the selected scope and pool."""
        worker_allocator = WorkerAllocator()

        provider_account = self.playground.create_cloud_provider_account_asset()

        scope1 = self.scenario.scope
        scope2 = self.playground.get_or_create_scope(name="scope2")

        pool1 = self.playground.create_worker_pool(
            "pool1", provider_account=provider_account
        )
        pool2 = self.playground.create_worker_pool(
            "pool2", provider_account=provider_account
        )

        # A static worker should not interfere
        self.playground.create_worker()
        w1 = self.playground.create_worker(worker_pool=pool1)
        w2 = self.playground.create_worker(worker_pool=pool2)

        # Work done in pool1 for scope1
        WorkerPoolTaskExecutionStatistics.objects.create(
            worker_pool=pool1,
            worker=w1,
            scope=scope1,
            timestamp=worker_allocator.start_of_month,
            runtime=1,
        )
        # Work done in pool2 for scope1
        WorkerPoolTaskExecutionStatistics.objects.create(
            worker_pool=pool2,
            worker=w2,
            scope=scope1,
            timestamp=worker_allocator.start_of_month,
            runtime=2,
        )
        # Work done in pool1 for scope2
        WorkerPoolTaskExecutionStatistics.objects.create(
            worker_pool=pool1,
            worker=w1,
            scope=scope2,
            timestamp=worker_allocator.start_of_month,
            runtime=4,
        )
        # Work done in pool2 for scope2
        WorkerPoolTaskExecutionStatistics.objects.create(
            worker_pool=pool2,
            worker=w2,
            scope=scope2,
            timestamp=worker_allocator.start_of_month,
            runtime=8,
        )

        allocator1 = ScopeWorkerAllocator(worker_allocator, scope1)
        self.assertEqual(allocator1.get_used_seconds(pool1), 1)
        self.assertEqual(allocator1.get_used_seconds(pool2), 2)

        allocator2 = ScopeWorkerAllocator(worker_allocator, scope2)
        self.assertEqual(allocator2.get_used_seconds(pool1), 4)
        self.assertEqual(allocator2.get_used_seconds(pool2), 8)

    def test_get_used_seconds_adds_running(self) -> None:
        """get_used_seconds adds runtime from running work requests."""
        pool = self.playground.create_worker_pool()
        workers = [
            self.playground.create_worker(worker_pool=pool) for _ in range(3)
        ]
        workers.append(
            self.playground.create_worker(
                worker_pool=self.playground.create_worker_pool(
                    name="test2", provider_account=pool.provider_account
                )
            )
        )
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)

        ws = WorkerPoolTaskExecutionStatistics.objects.create(
            worker_pool=pool,
            worker=workers[0],
            scope=self.scenario.scope,
            runtime=10,
        )
        ws.timestamp = worker_allocator.start_of_month + datetime.timedelta(
            seconds=2
        )
        ws.save()
        expected_used_seconds = 2

        workspace_in_other_scope = self.playground.create_workspace(
            scope=self.playground.get_or_create_scope(name="other-scope")
        )
        work_requests = [
            self.playground.create_work_request(workspace=workspace)
            for workspace in (
                self.scenario.workspace,
                self.scenario.workspace,
                workspace_in_other_scope,
                self.scenario.workspace,
            )
        ]
        for worker, work_request in zip(workers, work_requests):
            work_request.assign_worker(worker)
            work_request.mark_running()

        work_requests[0].started_at = TransactionNow() - datetime.timedelta(
            minutes=1
        )
        work_requests[0].save()
        expected_used_seconds += 60

        work_requests[1].started_at = (
            worker_allocator.start_of_month - datetime.timedelta(hours=1)
        )
        work_requests[1].save()
        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
            [now] = cursor.fetchone()
        expected_used_seconds += int(
            (now - worker_allocator.start_of_month).total_seconds()
        )

        # This work request's runtime is not added, since it's in a
        # different scope.
        work_requests[2].started_at = worker_allocator.start_of_month
        work_requests[2].save()

        # This work request's runtime is not added, since it's in a
        # different pool.
        work_requests[3].started_at = worker_allocator.start_of_month
        work_requests[3].save()

        self.assertEqual(
            allocator.get_used_seconds(pool), expected_used_seconds
        )

    def test_allocate_workers(self) -> None:
        pool = WorkerPool()
        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)

        asked = [10, 10, 10]
        limited_to = [0, 1, 10]
        expected = [0, 1, 10]
        with (
            mock.patch.object(
                worker_allocator,
                "limit_by_max_active_instances",
                side_effect=lambda _, amount: amount,
            ),
            mock.patch.object(
                worker_allocator,
                "limit_by_pool_target_max_seconds_per_month",
                side_effect=lambda _, amount: amount,
            ),
            mock.patch.object(
                allocator,
                "limit_by_scope_pool_target_max_seconds_per_month",
                side_effect=limited_to,
            ),
        ):
            for a, l, e in zip(asked, limited_to, expected):
                with mock.patch.object(
                    pool, "launch_workers"
                ) as launch_workers:
                    self.assertEqual(allocator.allocate_workers(pool, 10), e)
                if e == 0:
                    launch_workers.assert_not_called()
                else:
                    launch_workers.assert_called_with(e)

    def test_pending_task_count(self) -> None:
        other_scope = self.playground.get_or_create_scope(name="other")
        other_workspace = self.playground.create_workspace(
            name="other", scope=other_scope
        )
        other_workspace_artifact = self.playground.create_source_artifact(
            workspace=other_workspace
        )
        other_environment = self.playground.create_debian_environment(
            workspace=other_workspace
        ).artifact
        assert other_environment is not None
        self.playground.create_sbuild_work_request(
            source=other_workspace_artifact,
            architecture="amd64",
            environment=other_environment,
            workspace=other_workspace,
        )

        artifact = self.playground.create_source_artifact()
        environment = self.playground.create_debian_environment().artifact
        assert environment is not None
        self.playground.create_sbuild_work_request(
            source=artifact, architecture="amd64", environment=environment
        )
        self.playground.create_sbuild_work_request(
            source=artifact, architecture="amd64", environment=environment
        )
        self.playground.create_sbuild_work_request(
            source=artifact, architecture="arm64", environment=environment
        )

        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        # Only amd64 tasks for the selected scope are counted
        self.assertEqual(allocator.pending_task_count, 2)

    def test_workers_required(self) -> None:
        worker_allocator = WorkerAllocator()
        # We currently assume a task takes 10 minutes to run, and all tasks
        # need to run within an hour
        for queue_size, workers_needed in (
            (1, 1),  # One task can run within an hour, one worker is needed
            (6, 1),  # For up to 6 tasks, one worker is ok
            (7, 2),  # For the 7th task, we need another worker
            (12, 2),  # Two workers can do 12 tasks in an hour
        ):
            with self.subTest(queue_size=queue_size):
                allocator = ScopeWorkerAllocator(
                    worker_allocator, self.scenario.scope
                )
                # Calculations are based on the effort required to run all
                # but the last pending task.
                allocator.pending_task_count = queue_size + 1
                self.assertEqual(
                    allocator.workers_required,
                    workers_needed,
                )

    def test_workers_available(self) -> None:
        provider_account = self.playground.create_cloud_provider_account_asset()

        instance_wide_pool = self.playground.create_worker_pool(
            "iw", instance_wide=True, provider_account=provider_account
        )

        scope_pool = self.playground.create_worker_pool(
            "scope", instance_wide=False, provider_account=provider_account
        )
        ScopeWorkerPool.objects.create(
            worker_pool=scope_pool,
            scope=self.scenario.scope,
        )

        other_pool = self.playground.create_worker_pool(
            "other", instance_wide=False, provider_account=provider_account
        )

        # 3 static workers, of which 1 is connected
        for connected in (True, False, False):
            worker = self.playground.create_worker()
            if connected:
                worker.mark_connected()

        # 2 workers in instance_wide pool
        for _ in range(2):
            self.playground.create_worker(worker_pool=instance_wide_pool)

        # 4 workers in scope_pool
        for _ in range(4):
            self.playground.create_worker(worker_pool=scope_pool)

        # 8 workers in other_pool
        for _ in range(8):
            self.playground.create_worker(worker_pool=other_pool)

        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        self.assertEqual(allocator.workers_available, 7)

    def test_allocation_pools(self) -> None:
        provider_account = self.playground.create_cloud_provider_account_asset()

        instance_wide_pool = self.playground.create_worker_pool(
            "iw", instance_wide=True, provider_account=provider_account
        )

        scope_pool1 = self.playground.create_worker_pool(
            "scope1", instance_wide=False, provider_account=provider_account
        )
        ScopeWorkerPool.objects.create(
            worker_pool=scope_pool1,
            scope=self.scenario.scope,
            priority=1,
        )

        scope_pool2 = self.playground.create_worker_pool(
            "scope2", instance_wide=False, provider_account=provider_account
        )
        ScopeWorkerPool.objects.create(
            worker_pool=scope_pool2,
            scope=self.scenario.scope,
            priority=2,
        )

        self.playground.create_worker_pool(
            "other", instance_wide=False, provider_account=provider_account
        )

        worker_allocator = WorkerAllocator()
        allocator = ScopeWorkerAllocator(worker_allocator, self.scenario.scope)
        self.assertEqual(
            allocator.allocation_pools,
            [scope_pool2, scope_pool1, instance_wide_pool],
        )

    def test_process_scope(self) -> None:
        """Test how process_scope allocates needed workers."""
        worker_allocator = WorkerAllocator()
        for required, available, npools, allocs_asked, allocs_given in (
            # 5 workers to allocate, one pool that can satisfy them all
            (10, 5, 1, [5], [5]),
            # 5 workers to allocate, first pool can satisfy them all
            (10, 5, 2, [5], [5]),
            # 5 workers to allocate, first pool can only allocate 2
            (10, 5, 2, [5, 2], [3, 2]),
            # No new workers to allocate
            (1, 2, 0, [], []),
        ):
            allocator = ScopeWorkerAllocator(
                worker_allocator, self.scenario.scope
            )
            allocator.workers_required = required
            allocator.workers_available = available
            allocator.allocation_pools = [WorkerPool() for _ in range(npools)]
            with mock.patch.object(
                allocator, "allocate_workers", side_effect=allocs_given
            ) as allocate_workers:
                allocator.process_scope()

            if not allocs_asked:
                allocate_workers.assert_not_called()
            else:
                for call, pool, asked in zip(
                    allocate_workers.call_args_list,
                    allocator.allocation_pools,
                    allocs_asked,
                ):
                    self.assertEqual(call.args, (pool, asked))
