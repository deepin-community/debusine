# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for pools of workers."""

import logging
from functools import cached_property
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import JSONField, QuerySet
from django.urls import reverse
from django.utils import timezone

from debusine.db.context import context
from debusine.db.models.scopes import Scope
from debusine.server.worker_pools import (
    ScopeWorkerPoolLimits,
    WorkerPoolInterface,
    WorkerPoolLimits,
    WorkerPoolSpecifications,
    provider_interface,
    worker_pool_specifications_model,
)

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.db.models.workers import Worker
else:
    TypedModelMeta = object

logger = logging.getLogger(__name__)


class WorkerPoolManager(models.Manager["WorkerPool"]):
    """Manager for WorkerPool model."""

    def enabled(self) -> QuerySet["WorkerPool"]:
        """Return connected workers."""
        return WorkerPool.objects.filter(enabled=True)


class WorkerPool(models.Model):
    """Database model of a worker pool."""

    name = models.SlugField(
        unique=True,
        help_text='Human readable name of the worker pool',
    )
    provider_account = models.ForeignKey("Asset", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)
    architectures = JSONField(default=list)
    tags = JSONField(default=list, blank=True)
    specifications = JSONField(default=dict)
    instance_wide = models.BooleanField(default=True)
    ephemeral = models.BooleanField(default=False)
    limits = JSONField(default=dict, blank=True)

    registered_at = models.DateTimeField()

    objects = WorkerPoolManager()

    def __str__(self) -> str:
        """Return the id and name of the WorkerPool."""
        return f"Id: {self.id} Name: {self.name}"

    def get_absolute_url(self) -> str:
        """Return an absolute URL to view this WorkerPool."""
        return reverse("worker-pools:detail", kwargs={"name": self.name})

    @property
    def limits_model(self) -> WorkerPoolLimits:
        """Return the pydantic model for limits."""
        return WorkerPoolLimits.parse_obj(self.limits)

    @property
    def specifications_model(self) -> WorkerPoolSpecifications:
        """Return the pydantic model for specifications."""
        model = worker_pool_specifications_model(self.specifications)
        provider_model = self.provider_account.data_model
        assert hasattr(provider_model, "provider_type")
        if provider_model.provider_type != model.provider_type:
            raise ValueError(
                f"specifications for worker_pool {self.name} do not have a "
                f"provider_account with a matching provider_type."
            )
        return model

    @property
    def workers_running(self) -> QuerySet["Worker"]:
        """Return the Worker instances that are currently running."""
        return self.worker_set.filter(instance_created_at__isnull=False)

    @property
    def workers_stopped(self) -> QuerySet["Worker"]:
        """Return the Worker instances that are currently stopped."""
        return self.worker_set.filter(instance_created_at__isnull=True)

    @cached_property
    def provider_interface(self) -> WorkerPoolInterface:
        """Return a WorkerPoolInterface instance for this pool."""
        return provider_interface(self)

    def launch_workers(self, count: int) -> None:
        """Launch count additional worker instances."""
        from debusine.db.models.auth import Token
        from debusine.db.models.workers import Worker

        available = self.workers_stopped.count()
        if available < count:
            Worker.objects.create_pool_members(self, count - available)

        launched = 0
        while launched < count:
            with transaction.atomic():
                worker = (
                    self.workers_stopped.select_for_update(skip_locked=True)
                    .order_by("name")
                    .first()
                )
                if worker is None:  # pragma: no cover
                    # Locked or deleted since we expanded the pool, above
                    return

                old_activation_token = worker.activation_token
                worker.activation_token = (
                    Token.objects.create_worker_activation()
                )
                worker.save()
                if old_activation_token is not None:
                    old_activation_token.delete()
                if worker.token is not None:
                    worker.token.disable()

                self.provider_interface.launch_worker(worker)
                launched += 1

    def terminate_worker(self, worker: "Worker") -> None:
        """Terminate the specified worker instance."""
        # Import here to prevent circular imports
        from debusine.db.models.work_requests import (
            CannotRetry,
            WorkRequest,
            WorkRequestRetryReason,
        )

        if worker.worker_pool != self:
            raise ValueError(
                f"pool {self} cannot terminate worker"
                f" for pool {worker.worker_pool}"
            )

        # Commit this early to avoid scheduling any more work on the worker
        with transaction.atomic():
            if worker.activation_token is not None:
                worker.activation_token.disable()
            if worker.token is not None:
                worker.token.disable()

        if worker.instance_created_at is not None:
            WorkerPoolStatistics.objects.create(
                worker_pool=self,
                worker=worker,
                runtime=int(
                    (
                        timezone.now() - worker.instance_created_at
                    ).total_seconds()
                ),
            )

        # Trigger worker termination
        self.provider_interface.terminate_worker(worker)

        # Retry any work requests that were previously running on the worker
        for running in WorkRequest.objects.running(worker=worker):
            running.mark_aborted()
            try:
                with context.disable_permission_checks():
                    running.retry(reason=WorkRequestRetryReason.WORKER_FAILED)
            except CannotRetry as e:
                logger.debug(  # noqa: G200
                    "Cannot retry previously-running work request: %s", e
                )

    def clean(self) -> None:
        """
        Ensure that data is valid for this worker pool.

        :raise ValidationError: for invalid data.
        """
        try:
            self.limits_model
            self.specifications_model
        except pydantic.ValidationError as e:
            raise ValidationError(message=str(e)) from e


class ScopeWorkerPool(models.Model):
    """Through table for linking a WorkerPool to a Scope."""

    worker_pool = models.ForeignKey(WorkerPool, on_delete=models.CASCADE)
    scope = models.ForeignKey(Scope, on_delete=models.CASCADE)

    priority = models.IntegerField(default=0)
    limits = JSONField(default=dict, blank=True)

    @property
    def limits_model(self) -> ScopeWorkerPoolLimits:
        """Return the pydantic model for limits."""
        return ScopeWorkerPoolLimits.parse_obj(self.limits)

    def __str__(self) -> str:
        """Return the id and name of the ScopeWorkerPool."""
        return (
            f"Id: {self.id} WorkerPool: {self.worker_pool.name} "
            f"Scope: {self.scope.name}"
        )


class WorkerPoolTaskExecutionStatistics(models.Model):
    """Time spent executing tasks in a scope, stored at completion."""

    worker_pool = models.ForeignKey(WorkerPool, on_delete=models.CASCADE)
    worker = models.ForeignKey(
        "Worker", null=True, blank=True, on_delete=models.SET_NULL
    )
    scope = models.ForeignKey(Scope, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    runtime = models.IntegerField()

    class Meta(TypedModelMeta):
        indexes = [
            models.Index(
                "timestamp", name="%(app_label)s_worker_pool_exec_ts_idx"
            ),
        ]


class WorkerPoolStatistics(models.Model):
    """Running time for historical worker instances, stored at shutdown."""

    worker_pool = models.ForeignKey(WorkerPool, on_delete=models.CASCADE)
    worker = models.ForeignKey(
        "Worker", null=True, blank=True, on_delete=models.SET_NULL
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    runtime = models.IntegerField()

    class Meta(TypedModelMeta):
        indexes = [
            models.Index(
                "timestamp", name="%(app_label)s_worker_pool_stat_ts_idx"
            ),
        ]
