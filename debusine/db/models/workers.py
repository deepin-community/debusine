# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db workers."""

import copy
import hashlib
from typing import Any, Generic, Optional, TYPE_CHECKING, TypeVar, cast

from django.db import IntegrityError, models, transaction
from django.db.models import (
    CheckConstraint,
    Count,
    Exists,
    F,
    JSONField,
    Max,
    OuterRef,
    Q,
    QuerySet,
)
from django.db.models.expressions import Case, When
from django.db.models.functions import Extract, Greatest, Now
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from debusine.db.models import WorkRequest
from debusine.db.models.auth import Token
from debusine.db.models.worker_pools import WorkerPool
from debusine.tasks.models import WorkerType

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta
else:
    TypedModelMeta = object

A = TypeVar("A")


class WorkerQuerySet(QuerySet["Worker", A], Generic[A]):
    """Custom QuerySet for Worker."""

    def connected(self) -> "WorkerQuerySet[Any]":
        """Return connected workers."""
        return self.filter(connected_at__isnull=False).order_by('connected_at')

    def active(self) -> "WorkerQuerySet[Any]":
        """Exclude inactive workers from worker pools."""
        return self.exclude(
            worker_pool__isnull=False, instance_created_at__isnull=True
        )

    def waiting_for_work_request(self) -> "WorkerQuerySet[Any]":
        """
        Return workers that can be assigned a new work request.

        The workers with fewer associated pending or running work requests
        than their concurrency level could take more work right now and are
        thus waiting for a work request.

        Worker's token must be enabled.
        """
        # Import here to prevent circular imports
        from debusine.db.models.work_requests import WorkRequest

        running_work_request_count = Count(
            'assigned_work_requests',
            filter=Q(
                assigned_work_requests__status__in=[
                    WorkRequest.Statuses.RUNNING,
                    WorkRequest.Statuses.PENDING,
                ]
            ),
        )
        workers = (
            self.filter(connected_at__isnull=False)
            .order_by(F('worker_pool').asc(nulls_first=True), 'connected_at')
            .annotate(count_running=running_work_request_count)
            .filter(count_running__lt=F("concurrency"))
            .filter(Q(worker_type=WorkerType.CELERY) | Q(token__enabled=True))
        )

        return workers

    def with_idle_time(self) -> "WorkerQuerySet[Any]":
        """
        Annotate workers with `idle_time`.

        `Worker.idle_time` will be 0 for workers currently assigned a running
        work request, or the number of seconds the worker has been spending
        without a running work request assigned.
        """
        tasks = WorkRequest.objects.filter(worker=OuterRef("pk"))
        return self.annotate(
            idle_time=Case(
                When(
                    Exists(tasks.filter(status=WorkRequest.Statuses.RUNNING)),
                    then=0,
                ),
                When(
                    Exists(tasks),
                    then=Extract(
                        Now() - Max(tasks.values("completed_at")), "epoch"
                    ),
                ),
                default=Extract(
                    Now() - Greatest("registered_at", "instance_created_at"),
                    "epoch",
                ),
            )
        )

    def get_worker_by_token_key_or_none(
        self, token_key: str
    ) -> Optional["Worker"]:
        """Return a Worker identified by its associated secret token."""
        try:
            token_hash = hashlib.sha256(token_key.encode()).hexdigest()
            return cast("Worker", self.get(token__hash=token_hash))
        except Worker.DoesNotExist:
            return None

    def get_worker_or_none(self, worker_name: str) -> Optional["Worker"]:
        """Return the worker with worker_name or None."""
        try:
            return cast("Worker", self.get(name=worker_name))
        except Worker.DoesNotExist:
            return None


class WorkerManager(models.Manager["Worker"]):
    """Manager for Worker model."""

    @staticmethod
    def _generate_unique_name(name: str, counter: int) -> str:
        """Return name slugified adding "-counter" if counter != 1."""
        new_name = slugify(name.replace('.', '-'))

        if counter != 1:
            new_name += f'-{counter}'

        return new_name

    def create_with_fqdn(
        self,
        fqdn: str,
        token: Token,
        worker_type: WorkerType = WorkerType.EXTERNAL,
        worker_pool: WorkerPool | None = None,
    ) -> "Worker":
        """Return a new Worker with its name based on fqdn, with token."""
        counter = 1

        while True:
            name = self._generate_unique_name(fqdn, counter)
            try:
                with transaction.atomic():
                    return self.create(
                        name=name,
                        token=token,
                        worker_type=worker_type,
                        registered_at=timezone.now(),
                        worker_pool=worker_pool,
                    )
            except IntegrityError:
                counter += 1

    def create_pool_members(
        self,
        worker_pool: WorkerPool,
        count: int,
    ) -> None:
        """
        Create count new External Workers in worker_pool.

        Use throw-away activation tokens; when workers start, they'll
        exchange their activation tokens for full worker tokens.
        """
        created = 0
        index = 1
        while created < count:
            with transaction.atomic():
                try:
                    self.create(
                        name=f"{worker_pool.name}-{index:03}",
                        activation_token=(
                            Token.objects.create_worker_activation()
                        ),
                        worker_type=WorkerType.EXTERNAL,
                        worker_pool=worker_pool,
                        registered_at=timezone.now(),
                    )
                    created += 1
                except IntegrityError:
                    pass
            index += 1

    def get_or_create_celery(self) -> "Worker":
        """Return a new Worker representing the Celery task queue."""
        try:
            return self.get(name="celery", worker_type=WorkerType.CELERY)
        except Worker.DoesNotExist:
            return self.create(
                name="celery",
                worker_type=WorkerType.CELERY,
                registered_at=timezone.now(),
            )

    def mark_all_disconnected(self) -> None:
        """
        Mark all non-Celery workers as disconnected.

        When debusine-server starts, depending on how it exited, workers
        might still be registered as connected in the database.  To avoid
        confusion, mark them as disconnected until they explicitly connect
        again.
        """
        Worker.objects.connected().exclude(
            worker_type=WorkerType.CELERY
        ).update(connected_at=None)


class Worker(models.Model):
    """Database model of a worker."""

    name = models.SlugField(
        unique=True,
        help_text='Human readable name of the worker based on the FQDN',
    )
    registered_at = models.DateTimeField()
    connected_at = models.DateTimeField(blank=True, null=True)
    instance_created_at = models.DateTimeField(blank=True, null=True)

    # This is the token used by the Worker to authenticate
    # Users have their own tokens - this is specific to a single worker.
    token = models.OneToOneField(
        Token, null=True, on_delete=models.PROTECT, related_name="worker"
    )
    # This is used to grant workers the ability to bootstrap authentication
    # for themselves, in situations where their provisioning data may be
    # revealed to untrusted code later.  The activation token will be
    # deleted once it has been used.
    activation_token = models.OneToOneField(
        Token,
        null=True,
        on_delete=models.SET_NULL,
        related_name="activating_worker",
    )
    # Set by debusine-admin edit_worker_metadata.
    # Note: contents will be displayed to users in the web UI
    static_metadata = JSONField(default=dict, blank=True)
    # Information about features supported by workers.
    # Note: contents will be displayed to users in the web UI
    dynamic_metadata = JSONField(default=dict, blank=True)
    # Information needed to track cloud workers.
    # Note: contents will be displayed to users in the web UI
    dynamic_metadata_updated_at = models.DateTimeField(blank=True, null=True)

    worker_type = models.CharField(
        max_length=8,
        choices=WorkerType.choices,
        default=WorkerType.EXTERNAL,
        editable=False,
    )
    # Only Celery workers currently support concurrency levels greater than
    # 1.
    concurrency = models.PositiveIntegerField(
        default=1,
        help_text="Number of tasks this worker can run simultaneously",
    )

    worker_pool = models.ForeignKey(
        WorkerPool, blank=True, null=True, on_delete=models.PROTECT
    )
    worker_pool_data = JSONField(null=True, blank=True)

    objects = WorkerManager.from_queryset(WorkerQuerySet)()

    class Meta(TypedModelMeta):
        constraints = [
            # Non-Celery workers must have a token.
            CheckConstraint(
                name="%(app_label)s_%(class)s_celery_or_token",
                check=Q(worker_type=WorkerType.CELERY)
                | Q(activation_token__isnull=False)
                | Q(token__isnull=False),
            )
        ]

    def mark_disconnected(self) -> None:
        """Update and save relevant Worker fields after disconnecting."""
        self.connected_at = None
        self.save()

    def running_work_requests(self) -> QuerySet["WorkRequest"]:
        """Return queryset of work requests running on this worker."""
        return self.assigned_work_requests.filter(
            status=WorkRequest.Statuses.RUNNING
        ).order_by("id")

    def mark_connected(self) -> None:
        """Update and save relevant Worker fields after connecting."""
        self.connected_at = timezone.now()
        self.save()

    def connected(self) -> bool:
        """Return True if the Worker is connected."""
        return self.connected_at is not None

    def is_busy(self) -> bool:
        """
        Return True if the Worker is busy with work requests.

        A Worker is busy if it has as many running or pending work requests
        as its concurrency level.
        """
        # Import here to prevent circular imports
        from debusine.db.models.work_requests import WorkRequest

        return (
            WorkRequest.objects.running(worker=self)
            | WorkRequest.objects.pending(worker=self)
        ).count() >= self.concurrency

    def metadata(self) -> dict[str, Any]:
        """
        Return all metadata with static_metadata and dynamic_metadata merged.

        If the same key is in static_metadata and dynamic_metadata:
        static_metadata takes priority.
        """
        return {
            **copy.deepcopy(self.dynamic_metadata),
            **copy.deepcopy(self.static_metadata),
        }

    def set_dynamic_metadata(self, metadata: dict[str, Any]) -> None:
        """Save metadata and update dynamic_metadata_updated_at."""
        self.dynamic_metadata = metadata
        self.dynamic_metadata_updated_at = timezone.now()
        self.save()

    def __str__(self) -> str:
        """Return the id and name of the Worker."""
        return f"Id: {self.id} Name: {self.name}"

    def get_absolute_url(self) -> str:
        """Return an absolute URL to view this worker."""
        return reverse("workers:detail", kwargs={"name": self.name})
