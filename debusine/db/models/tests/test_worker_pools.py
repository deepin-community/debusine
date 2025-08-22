# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the worker pool models."""

import logging
from datetime import timedelta
from unittest.mock import PropertyMock, patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from debusine.assets import (
    HetznerProviderAccountConfiguration,
    HetznerProviderAccountCredentials,
    HetznerProviderAccountData,
)
from debusine.db.models import (
    ScopeWorkerPool,
    WorkRequest,
    Worker,
    WorkerPool,
    WorkerPoolStatistics,
)
from debusine.server.worker_pools import (
    AWSEC2WorkerPoolSpecification,
    DummyWorkerPool,
    DummyWorkerPoolSpecification,
    ScopeWorkerPoolLimits,
    WorkerPoolLimits,
)
from debusine.test.django import TestCase


class WorkerPoolManagerTests(TestCase):
    """Tests for the WorkerPoolManager Manager."""

    def test_enabled(self) -> None:
        provider_account = self.playground.create_cloud_provider_account_asset()
        enabled = self.playground.create_worker_pool(
            name="enabled", provider_account=provider_account
        )
        self.playground.create_worker_pool(
            name="disabled", enabled=False, provider_account=provider_account
        )
        self.assertQuerySetEqual(WorkerPool.objects.enabled(), [enabled])


class WorkerPoolTests(TestCase):
    """Tests for the WorkerPool model."""

    def test_str(self) -> None:
        worker_pool = WorkerPool(id=42, name="foo")
        self.assertEqual(
            worker_pool.__str__(),
            "Id: 42 Name: foo",
        )

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""
        worker_pool = self.playground.create_worker_pool()
        self.assertEqual(
            worker_pool.get_absolute_url(),
            f"/-/status/worker-pools/{worker_pool.name}/",
        )

    def test_limits_model(self) -> None:
        limits = WorkerPoolLimits(max_active_instances=10)
        worker_pool = WorkerPool(limits=limits.dict())
        self.assertEqual(worker_pool.limits_model, limits)

    def test_specifications_model(self) -> None:
        provider_account = self.playground.create_cloud_provider_account_asset()
        specifications = DummyWorkerPoolSpecification(features=["foo"])
        worker_pool = WorkerPool(
            specifications=specifications.dict(),
            provider_account=provider_account,
        )
        self.assertEqual(worker_pool.specifications_model, specifications)

    def test_specifications_model_mismatching_account(self) -> None:
        provider_account = self.playground.create_cloud_provider_account_asset(
            data=HetznerProviderAccountData(
                name="hetzner",
                configuration=HetznerProviderAccountConfiguration(
                    region_name="nbg1",
                ),
                credentials=HetznerProviderAccountCredentials(
                    api_token="secret token",
                ),
            )
        )
        specifications = AWSEC2WorkerPoolSpecification(launch_templates=[])
        worker_pool = WorkerPool(
            name="foo",
            specifications=specifications.dict(),
            provider_account=provider_account,
        )
        with self.assertRaisesRegex(
            ValueError,
            (
                r"specifications for worker_pool foo do not have a "
                r"provider_account with a matching provider_type\."
            ),
        ):
            worker_pool.specifications_model

    def create_pool_with_one_running_worker(
        self,
    ) -> tuple[WorkerPool, list[Worker], list[Worker]]:
        """Return a worker_pool with a mix of workers, some running."""
        worker_pool = self.playground.create_worker_pool()
        running: list[Worker] = []
        stopped: list[Worker] = []
        for id_ in range(3):
            worker = self.playground.create_worker(worker_pool=worker_pool)
            if id_ == 2:
                worker.instance_created_at = None
                worker.save()
                stopped.append(worker)
            else:
                running.append(worker)
        return worker_pool, running, stopped

    def test_workers_running(self) -> None:
        worker_pool, running, _ = self.create_pool_with_one_running_worker()
        self.assertQuerySetEqual(
            worker_pool.workers_running.order_by("id"), running
        )

    def test_workers_stopped(self) -> None:
        worker_pool, _, stopped = self.create_pool_with_one_running_worker()
        self.assertQuerySetEqual(
            worker_pool.workers_stopped.order_by("id"), stopped
        )

    def test_provider_interface(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        self.assertIsInstance(worker_pool.provider_interface, DummyWorkerPool)

    def test_launch_workers(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        # Create one pre-existing worker
        Worker.objects.create_pool_members(worker_pool=worker_pool, count=1)
        worker = worker_pool.worker_set.first()
        assert worker and worker.activation_token
        old_activation_token = worker.activation_token

        worker_pool.launch_workers(3)
        self.assertEqual(worker_pool.worker_set.count(), 3)
        self.assertEqual(
            worker_pool.workers_running.filter(
                worker_pool_data__launched=True
            ).count(),
            3,
        )

        worker.refresh_from_db()
        self.assertNotEqual(old_activation_token, worker.activation_token)
        self.assertIsNotNone(worker.activation_token.expire_at)
        self.assertTrue(worker.activation_token.enabled)

    def test_launch_workers_integrity_error(self) -> None:
        """Exercise the branch from transaction.atomic() to exit."""
        with (
            patch.object(
                WorkerPool, "provider_interface", new_callable=PropertyMock
            ) as provider_interface,
            self.assertRaises(IntegrityError),
        ):
            provider_interface.side_effect = IntegrityError
            worker_pool = self.playground.create_worker_pool()
            worker_pool.launch_workers(1)

    def test_launch_workers_preexisting(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        # Create two pre-existing workers
        Worker.objects.create_pool_members(worker_pool=worker_pool, count=2)
        worker_pool.launch_workers(2)
        self.assertEqual(worker_pool.worker_set.count(), 2)
        self.assertEqual(worker_pool.workers_running.count(), 2)

    def test_launch_workers_disable_old_token(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        # Create one pre-existing worker
        Worker.objects.create_pool_members(worker_pool=worker_pool, count=1)
        worker = worker_pool.worker_set.first()
        assert worker and worker.activation_token
        self.playground.create_worker_token(worker=worker)
        assert worker.token
        worker.activation_token = None
        worker.save()

        worker_pool.launch_workers(3)
        self.assertEqual(worker_pool.worker_set.count(), 3)
        self.assertEqual(
            worker_pool.workers_running.filter(
                worker_pool_data__launched=True
            ).count(),
            3,
        )

        worker.refresh_from_db()
        assert worker.activation_token is not None
        self.assertIsNotNone(worker.activation_token.expire_at)
        self.assertTrue(worker.activation_token.enabled)
        self.assertFalse(worker.token.enabled)

    def test_terminate_worker_with_activation_token(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        assert worker.activation_token
        self.assertTrue(worker.activation_token.enabled)

        worker_pool.terminate_worker(worker)

        worker.activation_token.refresh_from_db()
        self.assertFalse(worker.activation_token.enabled)

        worker.refresh_from_db()
        # The provider should have done this on termination:
        self.assertIsNone(worker.instance_created_at)

    def test_terminate_worker_with_token(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        assert worker.activation_token
        self.playground.create_worker_token(worker=worker)
        assert worker.token
        worker.activation_token = None
        worker.save()

        worker_pool.terminate_worker(worker)

        worker.token.refresh_from_db()
        self.assertFalse(worker.token.enabled)

        worker.refresh_from_db()
        # The provider should have done this on termination:
        self.assertIsNone(worker.instance_created_at)

    def test_terminate_worker_wrong_pool(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker()

        with self.assertRaisesRegex(
            ValueError,
            r"pool Id: \d+ Name: test cannot terminate worker for pool None",
        ):
            worker_pool.terminate_worker(worker)

        worker.refresh_from_db()
        # The provider should have done this on termination:
        self.assertIsNone(worker.instance_created_at)

    def test_terminate_worker_records_statistics(self) -> None:
        """Terminating a worker records running time statistics."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.instance_created_at = timezone.now() - timedelta(minutes=1)
        worker.save()

        worker_pool.terminate_worker(worker)

        self.assertQuerySetEqual(
            WorkerPoolStatistics.objects.filter(
                worker_pool=worker_pool
            ).values_list("worker_pool", "worker", "runtime"),
            [(worker_pool.id, worker.id, 60)],
        )
        worker.refresh_from_db()
        self.assertIsNone(worker.instance_created_at)

    def test_terminate_worker_no_instance_created_at_no_statistics(
        self,
    ) -> None:
        """If instance_created_at is null, terminate_worker skips statistics."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.instance_created_at = None
        worker.save()

        worker_pool.terminate_worker(worker)

        self.assertFalse(
            WorkerPoolStatistics.objects.filter(
                worker_pool=worker_pool
            ).exists()
        )

    def test_terminate_worker_retries_running_work_request(self) -> None:
        """Terminate a worker retries a running work request."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        work_request_pending = self.playground.create_work_request()
        work_request_running = self.playground.create_work_request()
        work_request_running.assign_worker(worker)
        work_request_running.mark_running()

        with self.assertNoLogs(
            logger="debusine.db.models.worker_pools", level=logging.DEBUG
        ):
            worker_pool.terminate_worker(worker)

        # The running work request was retried, though not yet assigned to a
        # worker.
        work_request_running.refresh_from_db()
        self.assertEqual(
            work_request_running.status, WorkRequest.Statuses.ABORTED
        )
        self.assertTrue(hasattr(work_request_running, "superseded"))
        self.assertIsNone(work_request_running.superseded.worker)
        self.assertEqual(
            work_request_running.superseded.workflow_data.retry_count, 1
        )

        # The pending work request was left alone.
        work_request_pending.refresh_from_db()
        self.assertEqual(
            work_request_pending.status, WorkRequest.Statuses.PENDING
        )
        self.assertFalse(hasattr(work_request_pending, "superseded"))

    def test_terminate_worker_cannot_retry_running_work_request(self) -> None:
        """If a running work request cannot be retried, it is logged."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        work_request_running = self.playground.create_work_request()
        work_request_running.assign_worker(worker)
        work_request_running.task_data = {"invalid": True}
        work_request_running.configured_task_data = {"invalid": True}
        work_request_running.save()
        work_request_running.mark_running()

        with self.assertLogsContains(
            "Cannot retry previously-running work request: "
            "Task dependencies cannot be satisfied",
            logger="debusine.db.models.worker_pools",
            level=logging.DEBUG,
        ):
            worker_pool.terminate_worker(worker)

        # The running work request was aborted but could not be retried.
        work_request_running.refresh_from_db()
        self.assertEqual(
            work_request_running.status, WorkRequest.Statuses.ABORTED
        )
        self.assertFalse(hasattr(work_request_running, "superseded"))

    def test_clean_valid(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker_pool.clean()

    def test_clean_specifications(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker_pool.specifications = {
            "provider_type": "dummy",
            "extra_key": "unknown",
        }
        with self.assertRaisesRegex(
            ValidationError,
            r"validation errors? for DummyWorkerPoolSpecification",
        ):
            worker_pool.clean()

    def test_clean_limits(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker_pool.limits = {"extra_key": "unknown"}
        with self.assertRaisesRegex(
            ValidationError, r"validation errors? for WorkerPoolLimits"
        ):
            worker_pool.clean()


class ScopeWorkerPoolTests(TestCase):
    """Tests for the ScopeWorkerPool model."""

    def test_str(self) -> None:
        worker_pool = WorkerPool(name="foo")
        scope = self.playground.get_default_scope()
        scope_worker_pool = ScopeWorkerPool(
            id=42, worker_pool=worker_pool, scope=scope
        )
        self.assertEqual(
            scope_worker_pool.__str__(),
            f"Id: 42 WorkerPool: foo Scope: {scope.name}",
        )

    def test_limits_model(self) -> None:
        limits = ScopeWorkerPoolLimits(target_max_seconds_per_month=99)
        scope_worker_pool = ScopeWorkerPool(limits=limits.dict())
        self.assertEqual(scope_worker_pool.limits_model, limits)
