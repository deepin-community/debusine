# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""OpenMetrics statistics for Debusine."""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from functools import cache
from itertools import product
from typing import Any, cast

from django.conf import settings
from django.db.models import (
    BooleanField,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    Model,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.expressions import Combinable
from django.db.models.functions import Extract
from django.utils import timezone
from prometheus_client.core import (
    GaugeMetricFamily,
    HistogramMetricFamily,
    Metric,
    Timestamp,
)
from prometheus_client.registry import Collector

from debusine.artifacts.local_artifact import LocalArtifact
from debusine.artifacts.models import CollectionCategory, TaskTypes
from debusine.assets.models import AssetCategory, asset_categories
from debusine.db.models import (
    Artifact,
    Asset,
    Collection,
    FileStore,
    Group,
    Identity,
    Scope,
    Token,
    User,
    WorkRequest,
    Worker,
    WorkerPool,
    WorkerPoolStatistics,
    WorkflowTemplate,
    Workspace,
)
from debusine.tasks import BaseTask
from debusine.tasks.executors import executor_backends
from debusine.tasks.models import WorkerType

BUCKET_DAYS = (1, 3, 7, 14, 30, 90, 365)


class ZeroGauge(GaugeMetricFamily):
    """A GaugeMetricFamily that can fill missing values with 0."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labels: Sequence[str],
        **kwargs: Any,
    ) -> None:
        """Initialize a ZeroGauge."""
        super().__init__(name, documentation, labels=labels, **kwargs)
        self._seen: set[tuple[str, ...]] = set()

    def add_metric(
        self,
        labels: Sequence[str],
        value: float,
        timestamp: Timestamp | float | None = None,
    ) -> None:
        """Record a metric."""
        super().add_metric(labels, value, timestamp)
        self._seen.add(tuple(labels))

    def fill_zeros(self, *label_values: Iterable[str]) -> None:
        """Generate zero metrics for any label values we haven't seen."""
        for labels in product(*label_values):
            if labels not in self._seen:
                self.add_metric(labels, 0)
                self._seen.add(labels)


class DebusineCollector(Collector):
    """Collect Debusine's metrics from the database."""

    def collect(self) -> Iterable[Metric]:
        """Collect all our metrics."""
        self._scopes.cache_clear()
        self._worker_pools.cache_clear()

        yield self.count_by_category(
            Artifact, categories=LocalArtifact.artifact_categories()
        )
        yield self.count_assets()
        yield self.count_by_category(
            Collection, categories=list(CollectionCategory)
        )
        yield self.count_groups()
        yield self.count_tokens()
        yield self.count_user_identities()
        yield self.count_user_identities_activity()
        yield self.count_users()
        yield self.count_user_activity()
        yield self.count_work_requests()
        yield self.count_workers()
        yield self.count_worker_pool_runtime()
        yield self.count_workflow_templates()
        yield self.count_workspaces()
        yield from self.measure_file_stores()

    @cache
    def _scopes(self) -> Sequence[str]:
        """Return all known scope names."""
        return list(Scope.objects.values_list("name", flat=True))

    @cache
    def _worker_pools(self, include_null: bool = False) -> Sequence[str]:
        """Return all known worker pool names."""
        names = list(WorkerPool.objects.values_list("name", flat=True))
        if include_null:
            names.append("")
        return names

    def count_by_category(
        self, model: type[Model], categories: Sequence[str]
    ) -> ZeroGauge:
        """Collect the number of objects in model by category and scope."""
        gauge = ZeroGauge(
            str(model._meta.verbose_name_plural),
            f"Number of {model._meta.object_name}s",
            labels=("category", "scope"),
        )
        for stat in model._meta.base_manager.values(
            "category", "workspace__scope__name"
        ).annotate(count=Count("id")):
            gauge.add_metric(
                (stat["category"], stat["workspace__scope__name"] or ""),
                stat["count"],
            )
        gauge.fill_zeros(categories, self._scopes())
        return gauge

    def count_assets(self) -> Metric:
        """Count the number of assets by category and scope."""
        unscoped_categories = {AssetCategory.CLOUD_PROVIDER_ACCOUNT}
        scoped_categories = set(asset_categories()) - unscoped_categories
        metric = self.count_by_category(
            Asset, categories=tuple(scoped_categories)
        )
        metric.fill_zeros(tuple(unscoped_categories), ("",))
        return metric

    def measure_file_stores(self) -> Iterable[Metric]:
        """Measure the size of FileStores."""
        file_store_size = GaugeMetricFamily(
            "file_store_size",
            "total_size of FileStores",
            labels=("backend", "name"),
        )
        file_store_max_size = GaugeMetricFamily(
            "file_store_max_size",
            "max_size of FileStores",
            labels=("backend", "name"),
        )
        for store in FileStore.objects.all():
            file_store_size.add_metric(
                (store.backend, store.name), store.total_size
            )
            if store.max_size:
                file_store_max_size.add_metric(
                    (store.backend, store.name), store.max_size
                )
        yield file_store_size
        yield file_store_max_size

    def count_groups(self) -> Metric:
        """Count the number of known groups."""
        gauge = ZeroGauge(
            "groups", "Number of Groups", labels=("ephemeral", "scope")
        )
        for stat in Group.objects.values("ephemeral", "scope__name").annotate(
            count=Count("id")
        ):
            gauge.add_metric(
                (str(int(stat["ephemeral"])), stat["scope__name"]),
                stat["count"],
            )
        gauge.fill_zeros(("0", "1"), self._scopes())
        return gauge

    def count_tokens(self) -> Metric:
        """Count the number of issued tokens."""
        gauge = ZeroGauge(
            "tokens", "Number of Tokens", labels=("enabled", "user", "worker")
        )
        for stat in (
            Token.objects.values("enabled")
            .annotate(
                user_token=ExpressionWrapper(
                    Q(user__isnull=False), output_field=BooleanField()
                ),
                worker_token=ExpressionWrapper(
                    Q(worker__isnull=False), output_field=BooleanField()
                ),
            )
            .annotate(count=Count("id"))
        ):
            gauge.add_metric(
                (
                    str(int(stat["enabled"])),
                    str(int(stat["user_token"])),
                    str(int(stat["worker_token"])),
                ),
                stat["count"],
            )
        gauge.fill_zeros(("0", "1"), ("0", "1"), ("0", "1"))
        return gauge

    def count_users(self) -> Metric:
        """Count the number of known users."""
        gauge = ZeroGauge("users", "Number of Users", labels=("active",))
        for stat in (
            User.objects.exclude(username="_system")
            .values("is_active")
            .annotate(count=Count("id"))
        ):
            gauge.add_metric((str(int(stat["is_active"])),), stat["count"])
        gauge.fill_zeros(("0", "1"))
        return gauge

    def _histogram_buckets(self) -> dict[str, Count]:
        """Generate histogram buckets."""
        buckets = {}
        now = timezone.now()
        for days in BUCKET_DAYS:
            buckets[f'b{days}d'] = Count(
                "created_by",
                distinct=True,
                filter=Q(created_at__gte=now - timedelta(days=days)),
            )
        buckets['binf'] = Count("created_by", distinct=True)
        return buckets

    def _histogram_count(
        self, histogram: HistogramMetricFamily, labels: tuple[str, ...]
    ) -> None:
        """Count user activity into histogram."""
        for stat in (
            WorkRequest.objects.filter(
                task_type=TaskTypes.WORKFLOW, parent__isnull=True
            )
            .values(*labels)
            .annotate(**self._histogram_buckets())
        ):
            buckets: list[tuple[str, float]] = []
            for days in BUCKET_DAYS:
                buckets.append((str(days), stat[f'b{days}d']))
            buckets.append(('+Inf', stat['binf']))
            histogram.add_metric(
                tuple(stat[label] for label in labels),
                buckets=buckets,
                sum_value=None,
            )

    def count_user_activity(self) -> Metric:
        """Count user activity."""
        histogram = HistogramMetricFamily(
            "user_activity",
            "Number of users who have created a workflow in the bucket",
            labels=("scope",),
        )
        self._histogram_count(histogram, labels=("workspace__scope__name",))
        return histogram

    def count_user_identities(self) -> Metric:
        """Count the number of known user identities."""
        gauge = ZeroGauge(
            "user_identities",
            "Number of User Identities",
            labels=("active", "issuer"),
        )
        for stat in (
            Identity.objects.filter(user__isnull=False)
            .values("user__is_active", "issuer")
            .annotate(count=Count("id"))
        ):
            gauge.add_metric(
                (str(int(stat["user__is_active"])), stat["issuer"]),
                stat["count"],
            )
        issuers = [
            provider.name
            for provider in getattr(settings, "SIGNON_PROVIDERS", ())
        ]
        gauge.fill_zeros(("0", "1"), issuers)
        return gauge

    def count_user_identities_activity(self) -> Metric:
        """Count user identity activity."""
        histogram = HistogramMetricFamily(
            "user_identities_activity",
            "Number of SSO Users who have created a workflow in the bucket",
            labels=("issuer", "scope"),
        )
        self._histogram_count(
            histogram,
            ("created_by__identities__issuer", "workspace__scope__name"),
        )
        return histogram

    def count_work_requests(self) -> Metric:
        """Count the number of known work requests."""
        gauge = ZeroGauge(
            "work_requests",
            "Number of WorkRequests",
            labels=(
                "task_type",
                "task_name",
                "scope",
                "status",
                "host_architecture",
                "backend",
            ),
        )
        seen_architectures = set()
        for stat in WorkRequest.objects.values(
            "task_type",
            "task_name",
            "workspace__scope__name",
            "status",
            "task_data__host_architecture",
            "task_data__backend",
        ).annotate(count=Count("id")):
            gauge.add_metric(
                (
                    stat["task_type"],
                    stat["task_name"],
                    stat["workspace__scope__name"],
                    stat["status"],
                    stat["task_data__host_architecture"] or "",
                    stat["task_data__backend"] or "",
                ),
                stat["count"],
            )
            if stat["task_data__host_architecture"]:
                seen_architectures.add(stat["task_data__host_architecture"])
        for task_type in TaskTypes:
            architectures: Iterable[str] = ("",)
            backends: Iterable[str] = ("",)
            if task_type == TaskTypes.WORKER:
                architectures = seen_architectures
                backends = executor_backends()
            gauge.fill_zeros(
                (task_type,),
                BaseTask.task_names(task_type),
                self._scopes(),
                WorkRequest.Statuses,
                architectures,
                backends,
            )

        return gauge

    def count_workers(self) -> Metric:
        """Count the number of known workers."""
        name = "workers"
        description = "Number of Workers"
        labels = [
            "connected",
            "busy",
            "worker_type",
            "worker_pool",
            "host_architecture",
        ]
        architectures: set[str] = set()
        host_architectures: set[str] = set()
        for archs, host_arch in (
            Worker.objects.values_list(
                "static_metadata__system:architectures",
                "static_metadata__system:host_architecture",
            )
            .distinct(
                "static_metadata__system:architectures",
                "static_metadata__system:host_architecture",
            )
            .union(
                Worker.objects.values_list(
                    "dynamic_metadata__system:architectures",
                    "dynamic_metadata__system:host_architecture",
                ).distinct(
                    "dynamic_metadata__system:architectures",
                    "dynamic_metadata__system:host_architecture",
                )
            )
        ):
            if archs:
                for arch in archs:
                    architectures.add(arch)
            if host_arch:
                host_architectures.add(host_arch)

        for arch in architectures:
            labels.append(f"architecture_{arch}")

        gauge = ZeroGauge(name, description, labels=labels)

        for stat in (
            Worker.objects.values(
                "worker_type",
                "worker_pool__name",
                "static_metadata__system:host_architecture",
                "dynamic_metadata__system:host_architecture",
                "static_metadata__system:architectures",
                "dynamic_metadata__system:architectures",
            )
            .annotate(
                connected=ExpressionWrapper(
                    Q(connected_at__isnull=False), output_field=BooleanField()
                ),
                busy=Exists(
                    WorkRequest.objects.filter(
                        status__in=(
                            WorkRequest.Statuses.RUNNING,
                            WorkRequest.Statuses.PENDING,
                        ),
                        worker=OuterRef("pk"),
                    )
                ),
            )
            .annotate(count=Count("id"))
        ):
            metric_labels = [
                str(int(stat["connected"])),
                str(int(stat["busy"])),
                stat["worker_type"],
                stat["worker_pool__name"] or "",
                stat["static_metadata__system:host_architecture"]
                or stat["dynamic_metadata__system:host_architecture"]
                or "",
            ]
            stat_archs = (
                stat["static_metadata__system:architectures"]
                or stat["dynamic_metadata__system:architectures"]
                or []
            )
            for arch in architectures:
                metric_labels.append(str(int(arch in stat_archs)))
            gauge.add_metric(metric_labels, stat["count"])
        for worker_type in WorkerType:
            label_values: list[Iterable[str]] = [
                # connected, busy, worker_type
                ("0", "1"),
                ("0", "1"),
                (worker_type,),
            ]
            if worker_type == WorkerType.EXTERNAL:
                # worker_pool, host_architecture, architecture_*
                label_values += [
                    self._worker_pools(include_null=True),
                    host_architectures,
                ]
                label_values += [("0", "1")] * len(architectures)
            else:
                label_values += [("",), ("",)]
                label_values += [("0",)] * len(architectures)
            gauge.fill_zeros(*label_values)
        return gauge

    def count_worker_pool_runtime(self) -> Metric:
        """Count the time spent running tasks per worker pool."""
        gauge = ZeroGauge(
            "worker_pool_runtime",
            "Execution time of WorkerPools",
            labels=("worker_pool",),
        )
        now = timezone.now()
        runtime = cast(
            Combinable, now - cast(datetime, F("instance_created_at"))
        )
        # We are summing two separate joins, so do it in subqueries to
        # avoid cartesian explosion
        for stat in WorkerPool.objects.values("name").annotate(
            runtime_past=Subquery(
                WorkerPoolStatistics.objects.filter(worker_pool=OuterRef("pk"))
                .values("worker_pool")
                .annotate(sum=Sum("runtime"))
                .values("sum")
            ),
            runtime_live=Subquery(
                Worker.objects.filter(
                    worker_pool=OuterRef("pk"),
                    instance_created_at__isnull=False,
                )
                .values("worker_pool")
                .annotate(
                    sum=Sum(
                        ExpressionWrapper(
                            Extract(runtime, "epoch"),
                            output_field=IntegerField(),
                        )
                    )
                )
                .values("sum")
            ),
        ):
            gauge.add_metric(
                (stat["name"],),
                (stat["runtime_past"] or 0) + (stat["runtime_live"] or 0),
            )
        gauge.fill_zeros(self._worker_pools())
        return gauge

    def count_workflow_templates(self) -> Metric:
        """Count the number of workflow templates."""
        gauge = ZeroGauge(
            "workflow_templates",
            "Number of WorkflowTemplates",
            labels=("task_name", "scope"),
        )
        for stat in WorkflowTemplate.objects.values(
            "task_name",
            "workspace__scope__name",
        ).annotate(count=Count("id")):
            gauge.add_metric(
                (stat["task_name"], stat["workspace__scope__name"]),
                stat["count"],
            )
        gauge.fill_zeros(
            BaseTask.task_names(TaskTypes.WORKFLOW), self._scopes()
        )
        return gauge

    def count_workspaces(self) -> Metric:
        """Count the number of workspaces."""
        gauge = ZeroGauge(
            "workspaces",
            "Number of Workspaces",
            labels=("private", "expires", "scope"),
        )
        for stat in (
            Workspace.objects.values("public", "scope__name")
            .annotate(
                expires=ExpressionWrapper(
                    Q(expiration_delay__isnull=False),
                    output_field=BooleanField(),
                )
            )
            .annotate(count=Count("id"))
        ):
            gauge.add_metric(
                (
                    str(int(stat["public"])),
                    str(int(stat["expires"])),
                    stat["scope__name"],
                ),
                stat["count"],
            )
        gauge.fill_zeros(("0", "1"), ("0", "1"), self._scopes())
        return gauge
