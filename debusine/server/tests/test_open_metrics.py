# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for OpenMetrics statistics in Debusine."""

import time
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.test.utils import freeze_time
from django.utils import timezone
from prometheus_client.core import Metric

from debusine.artifacts import LocalArtifact
from debusine.artifacts.models import ArtifactCategory, TaskTypes
from debusine.assets.models import AssetCategory
from debusine.db.models import (
    Artifact,
    Identity,
    WorkRequest,
    WorkerPoolStatistics,
)
from debusine.server.open_metrics import DebusineCollector, ZeroGauge
from debusine.server.signon.providers import Provider
from debusine.tasks.models import BackendType, WorkerType
from debusine.test.django import TestCase


class ZeroGaugeTests(SimpleTestCase):
    """Tests for our zero-filling ZeroGauge."""

    def test_add_metric_stores_samples(self) -> None:
        gauge = ZeroGauge("test", "Description", labels=["type"])
        gauge.add_metric(["foo"], 1)

        self.assertEqual(len(gauge.samples), 1)
        sample = gauge.samples[0]
        self.assertEqual(sample.value, 1)
        self.assertEqual(sample.labels, {"type": "foo"})

    def test_add_metric_sets_seen(self) -> None:
        gauge = ZeroGauge("test", "Description", labels=["type"])
        gauge.add_metric(["foo"], 1)
        gauge.add_metric(["bar"], 2)

        self.assertEqual(gauge._seen, {("foo",), ("bar",)})

    def test_fill_zeros(self) -> None:
        gauge = ZeroGauge("test", "Description", labels=["type"])
        gauge.add_metric(["foo"], 1)
        gauge.fill_zeros(("foo", "bar"))

        self.assertEqual(len(gauge.samples), 2)
        sample = gauge.samples[1]
        self.assertEqual(sample.value, 0)
        self.assertEqual(sample.labels, {"type": "bar"})

    def test_fill_zeros_complete(self) -> None:
        gauge = ZeroGauge("test", "Description", labels=["type"])
        gauge.add_metric(["foo"], 1)
        gauge.add_metric(["bar"], 2)
        gauge.fill_zeros(("foo", "bar"))

        self.assertEqual(len(gauge.samples), 2)
        self.assertNotEqual(gauge.samples[0].value, 0)
        self.assertNotEqual(gauge.samples[1].value, 0)

    def test_fill_zeros_empty(self) -> None:
        gauge = ZeroGauge("test", "Description", labels=["type"])
        gauge.fill_zeros(("foo", "bar"))

        self.assertEqual(len(gauge.samples), 2)
        self.assertEqual(gauge.samples[0].value, 0)
        self.assertEqual(gauge.samples[1].value, 0)

    def test_fill_zeros_matrix(self) -> None:
        gauge = ZeroGauge("test", "Description", labels=("a", "b"))
        gauge.add_metric(("0", "0"), 1)
        gauge.fill_zeros(("0", "1"), ("0", "1"))

        self.assertEqual(len(gauge.samples), 4)


class DebusineCollectorTests(TestCase):
    """Test DebusineCollector's collectors."""

    def collector(self) -> DebusineCollector:
        collector = DebusineCollector()
        return collector

    def sample_by_label(self, metric: Metric, labels: dict[str, str]) -> float:
        """Find the sample for labels in metric."""
        for sample in metric.samples:
            if sample.labels == labels:
                return sample.value
        raise KeyError(f"{labels} not found in {metric}")

    def assertMetricEquals(
        self, metric: Metric, value: float, **labels: str
    ) -> None:
        """Assert that there is a sample for labels in metric with value."""
        self.assertEqual(self.sample_by_label(metric, labels), value)

    def assertNoMetric(self, metric: Metric, **labels: str) -> None:
        """Assert that there is no sample for labels in metric."""
        with self.assertRaises(KeyError):
            self.sample_by_label(metric, labels)

    def test_count_by_category(self) -> None:
        self.playground.create_artifact()

        gauge = self.collector().count_by_category(
            Artifact, LocalArtifact.artifact_categories()
        )
        self.assertEqual(gauge.name, "artifacts")
        self.assertEqual(gauge.documentation, "Number of Artifacts")

        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(
            gauge, 1, category=ArtifactCategory.TEST, scope=scope
        )
        self.assertMetricEquals(
            gauge, 0, category=ArtifactCategory.BLHC, scope=scope
        )

    def test_count_assets(self) -> None:
        self.playground.create_signing_key_asset()

        gauge = self.collector().count_assets()
        self.assertEqual(gauge.name, "assets")

        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(
            gauge, 1, category=AssetCategory.SIGNING_KEY, scope=scope
        )
        self.assertNoMetric(
            gauge, category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, scope=scope
        )
        self.assertMetricEquals(
            gauge, 0, category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, scope=""
        )
        self.assertNoMetric(gauge, category=AssetCategory.SIGNING_KEY, scope="")

    def test_measure_file_stores(self) -> None:
        store = self.playground.get_default_file_store()
        contents = b"abc123"
        self.playground.create_file_in_backend(contents=contents)
        gauges = {
            gauge.name: gauge
            for gauge in self.collector().measure_file_stores()
        }
        self.assertEqual(len(gauges), 2)

        file_store_size = gauges["file_store_size"]
        self.assertMetricEquals(
            file_store_size,
            len(contents),
            backend=store.backend,
            name=store.name,
        )
        file_store_max_size = gauges["file_store_max_size"]
        self.assertNoMetric(
            file_store_max_size, backend=store.backend, name=store.name
        )

    def test_measure_file_stores_max_size(self) -> None:
        store = self.playground.get_default_file_store()
        store.max_size = 1024
        store.save()
        gauges = {
            gauge.name: gauge
            for gauge in self.collector().measure_file_stores()
        }
        file_store_max_size = gauges["file_store_max_size"]
        self.assertMetricEquals(
            file_store_max_size, 1024, backend=store.backend, name=store.name
        )

    def test_count_groups(self) -> None:
        scope1 = self.playground.get_default_scope()
        scope2 = self.playground.get_or_create_scope("scope2")
        self.playground.create_group("group1", scope=scope1)
        self.playground.create_group("group2", scope=scope2, ephemeral=True)
        gauge = self.collector().count_groups()
        self.assertEqual(gauge.name, "groups")
        self.assertMetricEquals(gauge, 1, ephemeral="0", scope=scope1.name)
        self.assertMetricEquals(gauge, 0, ephemeral="0", scope=scope2.name)
        self.assertMetricEquals(gauge, 0, ephemeral="1", scope=scope1.name)
        self.assertMetricEquals(gauge, 1, ephemeral="1", scope=scope2.name)

    def test_count_tokens(self) -> None:
        self.playground.create_bare_token(enabled=False)
        self.playground.create_worker()  # creates a worker token
        self.playground.create_user_token()
        self.playground.create_user_token()

        gauge = self.collector().count_tokens()
        self.assertEqual(gauge.name, "tokens")
        self.assertMetricEquals(gauge, 1, user="0", worker="0", enabled="0")
        self.assertMetricEquals(gauge, 1, user="0", worker="1", enabled="1")
        self.assertMetricEquals(gauge, 2, user="1", worker="0", enabled="1")
        self.assertMetricEquals(gauge, 0, user="1", worker="0", enabled="0")

    def test_count_users(self) -> None:
        self.playground.get_default_user()
        user2 = self.playground.create_user("user2")
        user2.is_active = False
        user2.save()
        gauge = self.collector().count_users()
        self.assertEqual(gauge.name, "users")
        self.assertMetricEquals(gauge, 1, active="1")
        self.assertMetricEquals(gauge, 1, active="0")

    @freeze_time(time.time())
    def test_count_user_activity(self) -> None:
        for username, ages in (
            ("user1", (0, 365)),
            ("user2", (4, 5)),
            ("user3", (364,)),
            ("user4", (365 * 2,)),
            ("user5", ()),
        ):
            user = self.playground.create_user(username)
            for days in ages:
                wr = self.playground.create_workflow(created_by=user)
                wr.created_at = timezone.now() - timedelta(days=days)
                wr.save()

        gauge = self.collector().count_user_activity()
        self.assertEqual(gauge.name, "user_activity")

        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(gauge, 1, scope=scope, le="1")
        self.assertMetricEquals(gauge, 1, scope=scope, le="3")
        self.assertMetricEquals(gauge, 2, scope=scope, le="7")
        self.assertMetricEquals(gauge, 2, scope=scope, le="14")
        self.assertMetricEquals(gauge, 2, scope=scope, le="30")
        self.assertMetricEquals(gauge, 2, scope=scope, le="90")
        self.assertMetricEquals(gauge, 3, scope=scope, le="365")
        self.assertMetricEquals(gauge, 4, scope=scope, le="+Inf")

    @freeze_time(time.time())
    @override_settings(SIGNON_PROVIDERS=[Provider(name="salsa", label="Salsa")])
    def test_count_user_identities_activity(self) -> None:
        for username, issuer, ages in (
            ("user1", "salsa", (0, 365)),
            ("user2", None, (4, 5)),
            ("user3", "salsa", (364,)),
            ("user5", "salsa", ()),
        ):
            user = self.playground.create_user(username)
            if issuer:
                Identity.objects.create(
                    user=user, issuer=issuer, subject=username, claims={}
                )
            for days in ages:
                wr = self.playground.create_workflow(created_by=user)
                wr.created_at = timezone.now() - timedelta(days=days)
                wr.save()

        gauge = self.collector().count_user_identities_activity()
        self.assertEqual(gauge.name, "user_identities_activity")

        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(gauge, 1, scope=scope, issuer="salsa", le="1")
        self.assertMetricEquals(gauge, 1, scope=scope, issuer="salsa", le="3")
        self.assertMetricEquals(gauge, 1, scope=scope, issuer="salsa", le="7")
        self.assertMetricEquals(gauge, 1, scope=scope, issuer="salsa", le="14")
        self.assertMetricEquals(gauge, 1, scope=scope, issuer="salsa", le="30")
        self.assertMetricEquals(gauge, 1, scope=scope, issuer="salsa", le="90")
        self.assertMetricEquals(gauge, 2, scope=scope, issuer="salsa", le="365")
        self.assertMetricEquals(
            gauge, 2, scope=scope, issuer="salsa", le="+Inf"
        )
        self.assertNoMetric(gauge, scope=scope, issuer="", le="1")

    @override_settings(SIGNON_PROVIDERS=[Provider(name="salsa", label="Salsa")])
    def test_count_user_identities(self) -> None:
        user = self.playground.get_default_user()
        Identity.objects.create(
            user=user, issuer="salsa", subject="foo", claims={}
        )
        Identity.objects.create(
            user=None, issuer="salsa", subject="bar", claims={}
        )
        gauge = self.collector().count_user_identities()
        self.assertEqual(gauge.name, "user_identities")
        self.assertMetricEquals(gauge, 1, active="1", issuer="salsa")
        self.assertMetricEquals(gauge, 0, active="0", issuer="salsa")

    def test_count_work_requests(self) -> None:
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.RUNNING,
            task_type=TaskTypes.SIGNING,
        )
        self.playground.create_work_request(
            task_name="noop",
            task_data={
                "host_architecture": "amd64",
                "backend": BackendType.UNSHARE,
            },
            status=WorkRequest.Statuses.RUNNING,
        )
        gauge = self.collector().count_work_requests()
        self.assertEqual(gauge.name, "work_requests")
        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(
            gauge,
            1,
            task_type=TaskTypes.WORKER,
            task_name="noop",
            scope=scope,
            status=WorkRequest.Statuses.PENDING,
            host_architecture="",
            backend="",
        )
        self.assertMetricEquals(
            gauge,
            1,
            task_type=TaskTypes.SIGNING,
            task_name="noop",
            scope=scope,
            status=WorkRequest.Statuses.RUNNING,
            host_architecture="",
            backend="",
        )
        self.assertMetricEquals(
            gauge,
            1,
            task_type=TaskTypes.WORKER,
            task_name="noop",
            scope=scope,
            status=WorkRequest.Statuses.RUNNING,
            host_architecture="amd64",
            backend=BackendType.UNSHARE,
        )
        self.assertMetricEquals(
            gauge,
            0,
            task_type=TaskTypes.WORKER,
            task_name="noop",
            scope=scope,
            status=WorkRequest.Statuses.RUNNING,
            host_architecture="amd64",
            backend=BackendType.QEMU,
        )

    def test_count_workers(self) -> None:
        self.playground.create_worker(
            worker_type=WorkerType.EXTERNAL,
            extra_dynamic_metadata={
                "system:host_architecture": "amd64",
                "system:architectures": ["amd64", "i386"],
            },
        )
        worker_pool = self.playground.create_worker_pool("test")
        worker2 = self.playground.create_worker(
            worker_type=WorkerType.EXTERNAL,
            worker_pool=worker_pool,
            extra_dynamic_metadata={
                "system:host_architecture": "i386",
                "system:architectures": ["amd64", "x32"],
            },
        )
        # Overrides dynamic:
        worker2.static_metadata = {"system:host_architecture": "amd64"}
        worker2.save()

        worker3 = self.playground.create_worker(
            worker_type=WorkerType.SIGNING,
        )
        worker3.mark_connected()
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.RUNNING,
            worker=worker3,
        )

        gauge = self.collector().count_workers()
        self.assertEqual(gauge.name, "workers")
        self.assertMetricEquals(
            gauge,
            1,
            connected="0",
            busy="0",
            worker_type=WorkerType.EXTERNAL,
            worker_pool="",
            host_architecture="amd64",
            architecture_amd64="1",
            architecture_i386="1",
            architecture_x32="0",
        )
        self.assertMetricEquals(
            gauge,
            1,
            connected="0",
            busy="0",
            worker_type=WorkerType.EXTERNAL,
            worker_pool=worker_pool.name,
            host_architecture="amd64",
            architecture_amd64="1",
            architecture_i386="0",
            architecture_x32="1",
        )
        self.assertMetricEquals(
            gauge,
            0,
            connected="0",
            busy="0",
            worker_type=WorkerType.EXTERNAL,
            worker_pool="",
            host_architecture="i386",
            architecture_amd64="0",
            architecture_i386="0",
            architecture_x32="0",
        )
        self.assertMetricEquals(
            gauge,
            1,
            connected="1",
            busy="1",
            worker_type=WorkerType.SIGNING,
            worker_pool="",
            host_architecture="",
            architecture_amd64="0",
            architecture_i386="0",
            architecture_x32="0",
        )
        self.assertMetricEquals(
            gauge,
            0,
            connected="0",
            busy="0",
            worker_type=WorkerType.SIGNING,
            worker_pool="",
            host_architecture="",
            architecture_amd64="0",
            architecture_i386="0",
            architecture_x32="0",
        )

    @freeze_time(time.time())
    def test_count_worker_pool_runtime(self) -> None:
        worker_pool = self.playground.create_worker_pool("test")
        worker_pool_2 = self.playground.create_worker_pool(
            "test2", provider_account=worker_pool.provider_account
        )

        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.instance_created_at = timezone.now() - timedelta(seconds=120)
        worker.save()
        worker2 = self.playground.create_worker(worker_pool=worker_pool)
        worker2.instance_created_at = timezone.now() - timedelta(seconds=60)
        worker2.save()
        # Not part of the pool:
        worker3 = self.playground.create_worker()
        worker3.instance_created_at = timezone.now() - timedelta(seconds=9999)
        worker3.save()

        WorkerPoolStatistics.objects.create(
            worker_pool=worker_pool,
            worker=worker,
            runtime=30,
        )
        WorkerPoolStatistics.objects.create(
            worker_pool=worker_pool,
            runtime=15,
        )
        WorkerPoolStatistics.objects.create(
            worker_pool=worker_pool_2,
            runtime=42,
        )

        gauge = self.collector().count_worker_pool_runtime()
        self.assertEqual(gauge.name, "worker_pool_runtime")
        self.assertMetricEquals(gauge, 120 + 15 + 60 + 30, worker_pool="test")
        self.assertMetricEquals(gauge, 42, worker_pool="test2")

    def test_count_workflow_templates(self) -> None:
        self.playground.create_workflow_template(name="test1", task_name="noop")
        gauge = self.collector().count_workflow_templates()
        self.assertEqual(gauge.name, "workflow_templates")
        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(gauge, 1, task_name="noop", scope=scope)
        self.assertMetricEquals(gauge, 0, task_name="sbuild", scope=scope)

    def test_count_workspaces(self) -> None:
        self.playground.get_default_workspace()
        self.playground.create_workspace(name="private", public=False)
        scope2 = self.playground.get_or_create_scope("scope2")
        self.playground.create_workspace(
            name="expiring", expiration_delay=timedelta(days=14), scope=scope2
        )

        gauge = self.collector().count_workspaces()
        self.assertEqual(gauge.name, "workspaces")
        scope = self.playground.get_default_scope().name
        self.assertMetricEquals(gauge, 1, private="0", expires="0", scope=scope)
        self.assertMetricEquals(gauge, 1, private="1", expires="0", scope=scope)
        self.assertMetricEquals(
            gauge, 1, private="0", expires="1", scope=scope2.name
        )
        self.assertMetricEquals(
            gauge, 0, private="0", expires="0", scope=scope2.name
        )
