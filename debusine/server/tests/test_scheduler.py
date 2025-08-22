# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for scheduler."""

import inspect
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TYPE_CHECKING
from unittest import mock

from celery import states
from celery.contrib.testing.app import TestApp, setup_default_app
from channels.db import database_sync_to_async
from django.db import transaction
from django.db.models import JSONField, QuerySet
from django.db.models.functions import Cast
from django.test import override_settings
from django.utils import timezone
from django_celery_results.models import TaskResult

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSourcePackage,
    TaskTypes,
)
from debusine.assets import KeyPurpose
from debusine.db.models import (
    Collection,
    CollectionItem,
    TaskDatabase,
    Token,
    WorkRequest,
    Worker,
    default_workspace,
)
from debusine.db.tests.utils import RunInParallelTransaction
from debusine.project.celery import TaskWithBetterArgumentRepresentation
from debusine.server.scheduler import (
    _work_request_changed,
    _worker_changed,
    schedule,
    schedule_for_worker,
)
from debusine.server.tasks import ServerNoop
from debusine.server.tasks.tests.helpers import SampleBaseWaitTask
from debusine.server.tasks.wait.models import DelayData
from debusine.server.workflows.models import WorkRequestWorkflowData
from debusine.signing.tasks.models import GenerateKeyData
from debusine.tasks import Lintian, Sbuild
from debusine.tasks.models import (
    ActionSkipIfLookupResultChanged,
    BaseDynamicTaskData,
    BaseTaskData,
    EventReactions,
    LintianData,
    LintianInput,
    LookupSingle,
    OutputData,
    OutputDataError,
    WorkerType,
)
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabaseBase
from debusine.test.django import (
    PlaygroundTestCase,
    TestCase,
    TransactionTestCase,
)
from debusine.test.test_utils import (
    create_system_tarball_data,
    preserve_task_registry,
)

if TYPE_CHECKING:
    from debusine.test.playground import Playground


class FakeTaskDatabase(FakeTaskDatabaseBase, TaskDatabase):
    """Use TaskDatabase's configure."""

    def __init__(self, *, work_request: WorkRequest, **kwargs: Any):
        """Also store a WorkRequest reference."""
        super().__init__(**kwargs)
        self.work_request = work_request


class SchedulerTestCase(PlaygroundTestCase):
    """Additional methods to help test scheduler related functions."""

    SAMPLE_TASK_DATA = {
        "input": {
            "source_artifact": 10,
        },
        "environment": "debian/match:codename=sid",
        "host_architecture": "amd64",
        "build_components": [
            "any",
            "all",
        ],
    }

    single_lookups: dict[
        tuple[LookupSingle, CollectionCategory | None], ArtifactInfo
    ] = {
        # source_artifact
        (10, None): ArtifactInfo(
            id=10,
            category=ArtifactCategory.SOURCE_PACKAGE,
            data=DebianSourcePackage(
                name="hello",
                version="1.0-1",
                type="dpkg",
                dsc_fields={"Package": "hello", "Version": "1.0-1"},
            ),
        ),
        # environment
        (
            "debian/match:codename=sid:architecture=amd64:format=tarball:"
            "backend=unshare:variant=",
            CollectionCategory.ENVIRONMENTS,
        ): ArtifactInfo(
            id=2,
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(),
        ),
    }

    playground: "Playground"

    def patch_task_database(self) -> None:
        """Patch :py:class:`debusine.server.scheduler.TaskDatabase`."""
        task_database_patcher = mock.patch(
            "debusine.db.models.task_database.TaskDatabase",
            side_effect=lambda work_request: FakeTaskDatabase(
                work_request=work_request,
                single_lookups=self.single_lookups,
                settings={"DEBUSINE_FQDN": "debusine.example.net"},
            ),
        )
        task_database_patcher.start()
        self.addCleanup(task_database_patcher.stop)

    @classmethod
    def _create_worker(
        cls, *, enabled: bool, worker_type: WorkerType = WorkerType.EXTERNAL
    ) -> Worker:
        """Create and return a Worker."""
        token = Token.objects.create(enabled=enabled)
        worker = Worker.objects.create_with_fqdn('worker.lan', token=token)

        worker.dynamic_metadata = {
            "system:worker_type": worker_type,
            "system:host_architecture": "amd64",
            "system:architectures": ["amd64"],
            "sbuild:version": Sbuild.TASK_VERSION,
            "sbuild:available": True,
            "executor:unshare:available": True,
        }

        worker.mark_connected()

        return worker

    def create_sample_sbuild_work_request(
        self, assign_to: Worker | None = None
    ) -> WorkRequest:
        """Create and return a WorkRequest."""
        work_request = self.playground.create_work_request(task_name='sbuild')
        work_request.task_data = dict(self.SAMPLE_TASK_DATA)
        work_request.save()

        if assign_to:
            work_request.assign_worker(assign_to)

        return work_request

    def set_tasks_allowlist(self, allowlist: list[str]) -> None:
        """Set tasks_allowlist in worker's static metadata."""
        assert hasattr(self, "worker")
        self.worker.static_metadata["tasks_allowlist"] = allowlist
        self.worker.save()

    def set_tasks_denylist(self, denylist: list[str]) -> None:
        """Set tasks_denylist in worker's static metadata."""
        assert hasattr(self, "worker")
        self.worker.static_metadata["tasks_denylist"] = denylist
        self.worker.save()


class SchedulerTests(SchedulerTestCase, TestCase):
    """Test schedule() function."""

    def setUp(self) -> None:
        """Initialize test case."""
        super().setUp()
        self.work_request_1 = self.create_sample_sbuild_work_request()
        self.work_request_2 = self.create_sample_sbuild_work_request()
        self.patch_task_database()

    def test_no_work_request_available(self) -> None:
        """schedule() returns nothing when no work request is available."""
        self._create_worker(enabled=True)
        WorkRequest.objects.all().delete()

        self.assertEqual(schedule(), [])

    def test_only_unhandled_work_requests_available(self) -> None:
        """schedule() returns nothing when no work request is available."""
        self._create_worker(enabled=True)
        user = self.playground.get_default_user()
        WorkRequest.objects.all().delete()

        WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=user,
            status=WorkRequest.Statuses.PENDING,
            task_type=TaskTypes.INTERNAL,
            task_name="unhandled",
            task_data={},
        )

        self.assertEqual(schedule(), [])

    def test_synchronization_points(self) -> None:
        """schedule() marks pending synchronization points as completed."""
        WorkRequest.objects.all().delete()

        parent = self.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        )

        pending = [
            WorkRequest.objects.create_synchronization_point(
                parent=parent, step=f"pending{i}"
            )
            for i in range(2)
        ]
        for wr in pending:
            wr.status = WorkRequest.Statuses.PENDING
            wr.save()
        WorkRequest.objects.create_synchronization_point(
            parent=parent,
            step="completed",
            status=WorkRequest.Statuses.COMPLETED,
        )

        self.assertCountEqual(schedule(), pending)
        for wr in pending:
            wr.refresh_from_db()
            self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
            self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_worker_connected_without_a_work_request(self) -> None:
        """schedule() goes over all workers."""
        self._create_worker(enabled=True)
        self._create_worker(enabled=True)

        result = schedule()

        self.assertCountEqual(
            result, {self.work_request_1, self.work_request_2}
        )

    def test_worker_connected_with_a_work_request(self) -> None:
        """schedule() skips a worker with assigned work requests."""
        # Create worker_1 and assigns to work_request_1
        worker_1 = self._create_worker(enabled=True)
        self.work_request_1.assign_worker(worker_1)

        # schedule() has no worker available
        self.assertEqual(schedule(), [])

    def test_worker_not_connected(self) -> None:
        """schedule() skips the workers that are not connected."""
        worker = self._create_worker(enabled=True)
        worker.connected_at = None
        worker.save()

        self.assertEqual(schedule(), [])

    def test_worker_not_enabled(self) -> None:
        """schedule() skips workers that are not enabled."""
        worker = self._create_worker(enabled=False)
        worker.mark_connected()

        self.assertEqual(schedule(), [])

    def create_wait_delay_task(
        self,
        delay_offset: timedelta,
        status: WorkRequest.Statuses = WorkRequest.Statuses.PENDING,
    ) -> WorkRequest:
        """Create a Wait/Delay task in a workflow."""
        parent = self.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        )
        return parent.create_child(
            task_name="delay",
            status=status,
            task_type=TaskTypes.WAIT,
            task_data=DelayData(delay_until=timezone.now() + delay_offset),
            workflow_data=WorkRequestWorkflowData(needs_input=False),
        )

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_wait_delay_task_past(self) -> None:
        """schedule() marks expired Wait/Delay tasks as completed."""
        work_request = self.create_wait_delay_task(timedelta(seconds=-1))

        result = schedule()

        self.assertEqual(result, [work_request])
        work_request.refresh_from_db()
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.SUCCESS)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_wait_delay_task_future(self) -> None:
        """schedule() leaves non-expired Wait/Delay tasks running."""
        work_request = self.create_wait_delay_task(timedelta(hours=1))

        result = schedule()

        self.assertEqual(result, [work_request])
        work_request.refresh_from_db()
        self.assertEqual(work_request.status, WorkRequest.Statuses.RUNNING)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_wait_delay_task_blocked(self) -> None:
        """schedule() skips blocked Wait/Delay tasks."""
        work_request = self.create_wait_delay_task(
            timedelta(seconds=-1), status=WorkRequest.Statuses.BLOCKED
        )

        result = schedule()

        self.assertEqual(result, [])
        work_request.refresh_from_db()
        self.assertEqual(work_request.status, WorkRequest.Statuses.BLOCKED)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    @preserve_task_registry()
    def test_wait_other_task(self) -> None:
        """schedule() leaves non-Delay Wait tasks running."""

        class WaitNoop(SampleBaseWaitTask[BaseTaskData, BaseDynamicTaskData]):
            TASK_VERSION = 1

            def _execute(self) -> bool:
                raise NotImplementedError()

        parent = self.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        )
        work_request = parent.create_child(
            task_name="waitnoop",
            status=WorkRequest.Statuses.PENDING,
            task_type=TaskTypes.WAIT,
            workflow_data=WorkRequestWorkflowData(needs_input=False),
        )

        result = schedule()

        self.assertEqual(result, [work_request])
        work_request.refresh_from_db()
        self.assertEqual(work_request.status, WorkRequest.Statuses.RUNNING)


class ScheduleForWorkerTests(SchedulerTestCase, TestCase):
    """Test schedule_for_worker()."""

    def setUp(self) -> None:
        """Initialize test case."""
        super().setUp()
        self.worker = self._create_worker(enabled=True)
        self.patch_task_database()

    def test_no_work_request(self) -> None:
        """schedule_for_worker() returns None when nothing to do."""
        self.assertIsNone(schedule_for_worker(self.worker))

    def test_skips_assigned_work_requests(self) -> None:
        """schedule_for_worker() doesn't pick assigned work requests."""
        # Create a running work request
        worker_2 = self._create_worker(enabled=True)
        work_request_1 = self.create_sample_sbuild_work_request()
        work_request_1.assign_worker(worker_2)

        result = schedule_for_worker(self.worker)

        self.assertIsNone(result)

    def test_skips_non_pending_work_requests(self) -> None:
        """schedule_for_worker() skips non-pending work requests."""
        # Create a completed work request
        worker_2 = self._create_worker(enabled=True)
        work_request_1 = self.create_sample_sbuild_work_request(
            assign_to=worker_2
        )
        work_request_1.mark_running()
        work_request_1.mark_completed(WorkRequest.Results.SUCCESS)
        # Create a running work request
        work_request_2 = self.create_sample_sbuild_work_request(
            assign_to=worker_2
        )
        work_request_2.mark_running()
        # Create an aborted work request
        work_request_2 = self.create_sample_sbuild_work_request()
        work_request_2.mark_aborted()

        result = schedule_for_worker(self.worker)

        self.assertIsNone(result)

    def test_skips_work_request_based_on_can_run_on(self) -> None:
        """schedule_for_worker() calls task->can_run_on."""
        self.create_sample_sbuild_work_request()
        # The request is for unstable-amd64, so can_run_on will return False
        self.worker.dynamic_metadata = {
            "system.worker_type": WorkerType.EXTERNAL
        }
        self.worker.save()

        result = schedule_for_worker(self.worker)

        self.assertIsNone(result)

    def test_skips_work_request_if_lookup_result_changed(self) -> None:
        """Skip work request if an action requests it."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        lookup = f"test@{CollectionCategory.TEST}/name:foo"
        work_request = self.create_sample_sbuild_work_request()
        work_request.event_reactions = EventReactions(
            on_assignment=[
                ActionSkipIfLookupResultChanged(
                    lookup=lookup, collection_item_id=None
                )
            ]
        )
        work_request.save()
        CollectionItem.objects.create_from_artifact(
            self.playground.create_artifact()[0],
            parent_collection=collection,
            name="foo",
            data={},
            created_by_user=self.playground.get_default_user(),
        )

        with self.assertLogs(
            "debusine.server.scheduler", level=logging.DEBUG
        ) as log:
            result = schedule_for_worker(self.worker)
        work_request.refresh_from_db()

        self.assertIn(
            f"DEBUG:debusine.server.scheduler:"
            f"Skipping work request {work_request} due to on_assignment action",
            log.output,
        )
        self.assertIsNone(result)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.SKIPPED)
        self.assertEqual(
            work_request.output_data,
            OutputData(skip_reason=f"Result of lookup {lookup!r} changed"),
        )

    def test_with_pending_unassigned_work_request(self) -> None:
        """schedule_for_worker() picks up the pending work request."""
        with override_settings(DISABLE_AUTOMATIC_SCHEDULING=True):
            work_request = self.create_sample_sbuild_work_request()

        with self.captureOnCommitCallbacks() as on_commit:
            result = schedule_for_worker(self.worker)

        assert isinstance(result, WorkRequest)
        self.assertEqual(result, work_request)
        self.assertEqual(result.worker, self.worker)

        # Assigning the work request does not cause another invocation of the
        # scheduler.
        self.assertEqual(on_commit, [])

    def test_with_failing_configure_call(self) -> None:
        """schedule_for_worker() skips invalid data and marks it as an error."""
        work_request = self.create_sample_sbuild_work_request()
        work_request.task_data = {}  # invalid: it's missing required properties
        work_request.save()

        with self.assertLogs(
            "debusine.server.scheduler", level=logging.WARNING
        ) as log:
            result = schedule_for_worker(self.worker)
        work_request.refresh_from_db()

        expected_message = (
            "Failed to configure: 3 validation errors for SbuildData"
        )
        self.assertIn(
            f"ERROR:debusine.server.scheduler:"
            f"Error assigning work request "
            f"{work_request.task_type}/{work_request.task_name} "
            f"({work_request.id}): {expected_message}",
            "\n".join(log.output),
        )
        self.assertIsNone(result)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.ERROR)
        assert work_request.output_data is not None
        self.assertIsInstance(work_request.output_data, OutputData)
        assert work_request.output_data.errors is not None
        self.assertEqual(len(work_request.output_data.errors), 1)
        self.assertTrue(
            work_request.output_data.errors[0].message.startswith(
                expected_message
            )
        )
        self.assertEqual(
            work_request.output_data.errors[0].code, "configure-failed"
        )

    def test_with_failing_compute_dynamic_data(self) -> None:
        """schedule_for_worker() handles failure to compute dynamic data."""
        work_request = self.create_sample_sbuild_work_request()
        work_request.task_data["backend"] = "unshare"
        work_request.task_data["environment"] = "bad-lookup"
        work_request.save()
        self.worker.dynamic_metadata["executor:unshare:available"] = True

        with self.assertLogs(
            "debusine.server.scheduler", level=logging.WARNING
        ) as log:
            result = schedule_for_worker(self.worker)
        work_request.refresh_from_db()

        expected_message = (
            f"Failed to compute dynamic data: "
            f"('bad-lookup', {CollectionCategory.ENVIRONMENTS!r})"
        )
        self.assertEqual(
            log.output,
            [
                f"ERROR:debusine.server.scheduler:"
                f"Error assigning work request "
                f"{work_request.task_type}/{work_request.task_name} "
                f"({work_request.id}): {expected_message}"
            ],
        )

        self.assertIsNone(result)
        self.assertEqual(work_request.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(work_request.result, WorkRequest.Results.ERROR)
        self.assertEqual(
            work_request.output_data,
            OutputData(
                errors=[
                    OutputDataError(
                        message=expected_message, code="dynamic-data-failed"
                    )
                ]
            ),
        )

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_computes_dynamic_data_with_worker_host_architecture(self) -> None:
        """schedule_for_worker() uses worker arch to compute dynamic data."""
        self.worker.dynamic_metadata.update(
            {
                "system:host_architecture": "i386",
                "system:architectures": ["amd64", "i386"],
                "lintian:version": Lintian.TASK_VERSION,
            }
        )
        # Lintian tasks don't care what architecture they run on, so they
        # defer to the worker's host architecture.
        work_request = self.playground.create_work_request(
            task_name="lintian",
            task_data=LintianData(
                input=LintianInput(source_artifact=1),
                environment="debian/match:codename=bookworm",
            ),
        )
        self.single_lookups = {
            (1, None): ArtifactInfo(
                id=1,
                category=ArtifactCategory.SOURCE_PACKAGE,
                data=DebianSourcePackage(
                    name="hello",
                    version="1.0-1",
                    type="dpkg",
                    dsc_fields={"Package": "hello", "Version": "1.0-1"},
                ),
            ),
            (
                "debian/match:codename=bookworm:architecture=i386:"
                "format=tarball:backend=unshare:variant=",
                CollectionCategory.ENVIRONMENTS,
            ): ArtifactInfo(
                id=2,
                category=ArtifactCategory.SYSTEM_TARBALL,
                data=create_system_tarball_data(),
            ),
        }
        task_config_collection = Collection.objects.get(
            workspace=self.playground.get_default_workspace(),
            name="default",
            category=CollectionCategory.TASK_CONFIGURATION,
        )

        result = schedule_for_worker(self.worker)

        assert isinstance(result, WorkRequest)
        self.assertEqual(result, work_request)
        self.assertEqual(result.worker, self.worker)
        self.assertEqual(
            result.dynamic_task_data,
            {
                "input_source_artifact_id": 1,
                "input_binary_artifacts_ids": [],
                "environment_id": 2,
                "subject": "hello",
                "runtime_context": "binary-all+binary-any+source:sid",
                "configuration_context": "sid",
                "task_configuration_id": task_config_collection.pk,
            },
        )

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_skips_tasks_in_deny_list(self) -> None:
        """schedule_for_worker() doesn't schedule task in denylist."""
        self.create_sample_sbuild_work_request()  # is sbuild task
        self.set_tasks_denylist(["foobar", "sbuild", "baz"])

        self.assertIsNone(schedule_for_worker(self.worker))

    def test_skips_tasks_not_in_allow_list(self) -> None:
        """schedule_for_worker() doesn't schedule task in denylist."""
        self.create_sample_sbuild_work_request()  # is sbuild task
        self.set_tasks_allowlist(["foobar", "baz"])

        self.assertIsNone(schedule_for_worker(self.worker))

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_allowlist_takes_precedence_over_denylist(self) -> None:
        """A task that is in both allow/denylist is allowed."""
        work_request = (
            self.create_sample_sbuild_work_request()
        )  # is sbuild task
        self.set_tasks_allowlist(["sbuild"])
        self.set_tasks_denylist(["sbuild"])

        result = schedule_for_worker(self.worker)

        self.assertEqual(work_request, result)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_highest_priority_work_request_is_picked_first(self) -> None:
        """schedule_for_worker() picks the highest-priority work request."""
        list_of_work_requests = []
        for priority_base, priority_adjustment in (
            (0, 0),
            (0, 10),
            (10, 0),
            (10, 1),
        ):
            work_request = self.create_sample_sbuild_work_request()
            work_request.priority_base = priority_base
            work_request.priority_adjustment = priority_adjustment
            work_request.save()
            list_of_work_requests.append(work_request)

        result = schedule_for_worker(self.worker)

        self.assertEqual(list_of_work_requests[3], result)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_oldest_work_request_is_picked_first(self) -> None:
        """schedule_for_worker() picks the oldest work request."""
        now = timezone.now()
        list_of_work_requests = []
        for offset in (1, 0, 2):  # The second object created will be the oldest
            work_request = self.create_sample_sbuild_work_request()
            work_request.created_at = now + timedelta(seconds=offset)
            work_request.save()
            list_of_work_requests.append(work_request)

        result = schedule_for_worker(self.worker)

        self.assertEqual(list_of_work_requests[1], result)

    def test_signing_work_request(self) -> None:
        """schedule_for_worker() assigns a signing work request."""
        signing_worker = self._create_worker(
            enabled=True, worker_type=WorkerType.SIGNING
        )
        with override_settings(DISABLE_AUTOMATIC_SCHEDULING=True):
            work_request = self.playground.create_work_request(
                task_type=TaskTypes.SIGNING,
                task_name="generatekey",
                task_data=GenerateKeyData(
                    purpose=KeyPurpose.UEFI, description="A UEFI key"
                ),
            )

        result = schedule_for_worker(signing_worker)

        assert isinstance(result, WorkRequest)
        self.assertEqual(result, work_request)
        self.assertEqual(result.worker, signing_worker)

    def assert_schedule_for_worker_return_none(
        self, status: WorkRequest.Statuses
    ) -> None:
        """Schedule_for_worker returns None if it has WorkRequest in status."""
        work_request = self.create_sample_sbuild_work_request(
            assign_to=self.worker
        )
        self.create_sample_sbuild_work_request()

        work_request.status = status
        work_request.save()

        self.assertIsNone(schedule_for_worker(self.worker))

    def test_schedule_for_worker_already_pending(self) -> None:
        """Do not assign a WorkRequest if the Worker has enough pending."""
        self.assert_schedule_for_worker_return_none(
            WorkRequest.Statuses.PENDING
        )

    def test_schedule_for_worker_already_running(self) -> None:
        """Do not assign a WorkRequest if the Worker has enough running."""
        self.assert_schedule_for_worker_return_none(
            WorkRequest.Statuses.RUNNING
        )

    @mock.patch("debusine.server.scheduler.run_server_task.apply_async")
    def test_schedule_for_worker_concurrency(
        self, mock_apply_async: mock.MagicMock  # noqa: U100
    ) -> None:
        """Consider a Worker's concurrency when assigning a WorkRequest."""
        worker = Worker.objects.get_or_create_celery()
        worker.concurrency = 3
        worker.dynamic_metadata = {
            "system:worker_type": WorkerType.CELERY,
            "server:servernoop:version": ServerNoop.TASK_VERSION,
        }

        for _ in range(3):
            work_request = self.playground.create_work_request(
                task_type=TaskTypes.SERVER, task_name="servernoop"
            )
            self.assertEqual(work_request, schedule_for_worker(worker))

        work_request = self.playground.create_work_request(
            task_type=TaskTypes.SERVER, task_name="servernoop"
        )
        self.assertIsNone(schedule_for_worker(worker))

    def test_work_request_changed_has_kwargs(self) -> None:
        """
        Check method _work_request_changed has kwargs argument.

        If it doesn't have the signal connection fails if DEBUG=0
        """
        self.assertTrue(_method_has_kwargs(_work_request_changed))

    def test_worker_changed_has_kwargs(self) -> None:
        """
        Check method _worker_changed has kwargs argument.

        If it doesn't have the signal connection fails if DEBUG=0
        """
        self.assertTrue(_method_has_kwargs(_worker_changed))


class ScheduleForWorkerTransactionTests(SchedulerTestCase, TransactionTestCase):
    """Test schedule_for_worker()."""

    def setUp(self) -> None:
        """Initialize test case."""
        super().setUp()
        self.worker = self._create_worker(enabled=True)
        self.patch_task_database()

    @override_settings(
        DISABLE_AUTOMATIC_SCHEDULING=False, CELERY_TASK_ALWAYS_EAGER=True
    )
    async def test_schedule_from_work_request_changed(self) -> None:
        """End to end: from WorkRequest.save(), notification to schedule()."""
        work_request_1 = await database_sync_to_async(
            self.create_sample_sbuild_work_request
        )()

        self.assertIsNone(work_request_1.worker)

        # When work_request_1 got created the function
        # _work_request_changed() was called (because it is connected
        # to the signal post_save on the WorkRequest). It called then
        # schedule() which assigned the work_request_1 to worker
        await work_request_1.arefresh_from_db()
        work_request_1_worker = await database_sync_to_async(
            lambda: work_request_1.worker
        )()

        self.assertEqual(work_request_1.worker, work_request_1_worker)

        # Create another work request
        work_request_2 = await database_sync_to_async(
            self.create_sample_sbuild_work_request
        )()
        self.assertIsNone(work_request_2.worker)

        await work_request_2.arefresh_from_db()
        work_request_2_worker = await database_sync_to_async(
            lambda: work_request_2.worker
        )()

        # schedule() got called but there isn't any available worker
        # (work_request_1 is assigned to self.worker and has not finished)
        self.assertIsNone(work_request_2_worker)

        # Mark work_request_1 as done transitioning to RUNNING, then
        # mark_completed(). schedule() got called when work_request_1 had
        # changed and then it assigned work_request_2 to self.worker
        self.assertTrue(
            await database_sync_to_async(work_request_1.mark_running)()
        )
        await database_sync_to_async(work_request_1.mark_completed)(
            WorkRequest.Results.SUCCESS
        )

        await work_request_2.arefresh_from_db()
        work_request_2_worker = await database_sync_to_async(
            lambda: work_request_2.worker
        )()

        # Assert that work_request_2 got assigned to self.worker
        self.assertEqual(work_request_2_worker, self.worker)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=False)
    async def test_schedule_from_worker_changed(self) -> None:
        """End to end: from Worker.save() to schedule_for_worker()."""
        await database_sync_to_async(self.worker.mark_disconnected)()

        work_request_1 = await database_sync_to_async(
            self.create_sample_sbuild_work_request
        )()

        # The Worker is not connected: the work_request_1.worker is None
        self.assertIsNone(work_request_1.worker)

        # _worker_changed() will be executed via the post_save signal
        # from Worker. It will call schedule_for_worker()
        await database_sync_to_async(self.worker.mark_connected)()

        await work_request_1.arefresh_from_db()

        work_request_1_worker = await database_sync_to_async(
            lambda: work_request_1.worker
        )()

        self.assertEqual(work_request_1_worker, self.worker)

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_when_worker_is_locked(self) -> None:
        """schedule_for_worker() fails when it fails to lock the Worker."""
        self.create_sample_sbuild_work_request()

        thread = RunInParallelTransaction(
            lambda: Worker.objects.select_for_update().get(id=self.worker.id)
        )
        thread.start_transaction()

        try:
            self.assertIsNone(schedule_for_worker(self.worker))
        finally:
            thread.stop_transaction()

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_when_work_request_is_locked(self) -> None:
        """schedule_for_worker() fails when it fails to lock the WorkRequest."""
        work_request = self.create_sample_sbuild_work_request()

        thread = RunInParallelTransaction(
            lambda: WorkRequest.objects.select_for_update().get(
                id=work_request.id
            )
        )
        thread.start_transaction()

        try:
            self.assertIsNone(schedule_for_worker(self.worker))
        finally:
            thread.stop_transaction()

    def assert_dispatches_celery_task(
        self,
        mock_apply_async: mock.MagicMock,
        work_request: WorkRequest,
        task_id_prefix: str,
    ) -> None:
        """Assert that a work request is dispatched to a Celery task."""
        mock_apply_async.assert_called_once()
        self.assertEqual(
            mock_apply_async.call_args.kwargs["args"], (work_request.id,)
        )
        self.assertRegex(
            mock_apply_async.call_args.kwargs["task_id"],
            fr"^{task_id_prefix}_{work_request.id}_"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        )

    def find_workflow_task_results(self) -> QuerySet[TaskResult]:
        task_results = (
            TaskResult.objects.filter(
                task_name="debusine.server.workflows.celery.run_workflow_task"
            )
            .annotate(task_args_array=Cast("task_args", JSONField()))
            .order_by("date_created")
        )
        assert isinstance(task_results, QuerySet)
        return task_results

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_server_task(self) -> None:
        """schedule_for_worker() dispatches server tasks to Celery."""
        worker = Worker.objects.get_or_create_celery()
        worker.dynamic_metadata = {
            "system:worker_type": WorkerType.CELERY,
            "server:servernoop:version": ServerNoop.TASK_VERSION,
        }
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.SERVER, task_name="servernoop"
        )

        with mock.patch(
            "debusine.server.scheduler.run_server_task.apply_async"
        ) as mock_apply_async:
            result = schedule_for_worker(worker)

        self.assertEqual(work_request, result)
        self.assert_dispatches_celery_task(
            mock_apply_async, work_request, "servernoop"
        )

    def test_workflow_callbacks(self) -> None:
        """schedule() dispatches workflow callbacks to Celery."""
        WorkRequest.objects.all().delete()
        parent = self.playground.create_workflow()
        parent.mark_running()
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )

        with mock.patch(
            "debusine.server.scheduler.run_workflow_task.apply_async"
        ) as mock_apply_async:
            self.assertEqual(schedule(), [wr])

        self.assert_dispatches_celery_task(
            mock_apply_async, wr, "internal_workflow"
        )
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.RUNNING)

    def test_workflow_callback_another_workflow_running(self) -> None:
        """Callbacks for multiple workflows may run concurrently."""
        test_app = TestApp(
            set_as_current=True,
            broker="memory://",
            task_cls=TaskWithBetterArgumentRepresentation,
        )
        self.enterContext(setup_default_app(test_app))

        WorkRequest.objects.all().delete()
        workflows = [self.playground.create_workflow() for _ in range(3)]
        for workflow in workflows:
            workflow.mark_running()
        wrs = [
            WorkRequest.objects.create_workflow_callback(
                parent=workflow, step="test"
            )
            for workflow in workflows[:2]
        ]

        with transaction.atomic():
            self.assertEqual(schedule(), wrs)

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[wr.id] for wr in wrs[:2]],
        )
        self.assertQuerySetEqual(
            WorkRequest.objects.filter(id__in={wr.id for wr in wrs})
            .order_by("id")
            .values_list("status", flat=True),
            [WorkRequest.Statuses.RUNNING] * len(wrs),
        )

        wrs.append(
            WorkRequest.objects.create_workflow_callback(
                parent=workflows[-1], step="test"
            )
        )
        # A running work request in the same parent workflow that isn't a
        # workflow callback doesn't stop the workflow callback running.
        self.playground.create_work_request(
            parent=workflows[-1], assign_new_worker=True, mark_running=True
        )

        with transaction.atomic():
            self.assertCountEqual(schedule(), [wrs[-1]])
        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[wr.id] for wr in wrs],
        )
        self.assertQuerySetEqual(
            WorkRequest.objects.filter(id__in={wr.id for wr in wrs})
            .order_by("id")
            .values_list("status", flat=True),
            [WorkRequest.Statuses.RUNNING] * len(wrs),
        )

    def test_workflow_callback_same_workflow_running(self) -> None:
        """Callbacks for the same workflow may not run concurrently."""
        test_app = TestApp(
            set_as_current=True,
            broker="memory://",
            task_cls=TaskWithBetterArgumentRepresentation,
        )
        self.enterContext(setup_default_app(test_app))

        WorkRequest.objects.all().delete()
        workflow = self.playground.create_workflow()
        workflow.mark_running()
        wrs = [
            WorkRequest.objects.create_workflow_callback(
                parent=workflow, step=f"test{i}"
            )
            for i in range(2)
        ]

        with transaction.atomic():
            self.assertEqual(schedule(), [wrs[0]])

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[wrs[0].id]],
        )
        self.assertQuerySetEqual(
            WorkRequest.objects.filter(id__in={wr.id for wr in wrs})
            .order_by("id")
            .values_list("status", flat=True),
            [WorkRequest.Statuses.RUNNING, WorkRequest.Statuses.PENDING],
        )

        wrs.append(
            WorkRequest.objects.create_workflow_callback(
                parent=workflow, step="test2"
            )
        )

        with transaction.atomic():
            self.assertEqual(schedule(), [])

        workflow_task_results = self.find_workflow_task_results().filter(
            status=states.PENDING
        )
        self.assertQuerySetEqual(
            workflow_task_results.values_list("task_args_array", flat=True),
            [[wrs[0].id]],
        )
        self.assertQuerySetEqual(
            WorkRequest.objects.filter(id__in={wr.id for wr in wrs})
            .order_by("id")
            .values_list("status", flat=True),
            [
                WorkRequest.Statuses.RUNNING,
                WorkRequest.Statuses.PENDING,
                WorkRequest.Statuses.PENDING,
            ],
        )

        # Completing the Celery task (albeit artificially) allows the
        # scheduler to proceed.
        self.assertTrue(
            WorkRequest.objects.get(id=wrs[0].id).mark_completed(
                WorkRequest.Results.SUCCESS
            )
        )
        workflow_task_result = workflow_task_results.get()
        workflow_task_result.status = states.SUCCESS
        workflow_task_result.save()

        with transaction.atomic():
            self.assertEqual(schedule(), [wrs[1]])

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[wrs[1].id]],
        )
        self.assertQuerySetEqual(
            WorkRequest.objects.filter(id__in={wr.id for wr in wrs})
            .order_by("id")
            .values_list("status", flat=True),
            [
                WorkRequest.Statuses.COMPLETED,
                WorkRequest.Statuses.RUNNING,
                WorkRequest.Statuses.PENDING,
            ],
        )

    def test_workflows(self) -> None:
        """schedule() dispatches workflows to Celery."""
        WorkRequest.objects.all().delete()
        wr = self.playground.create_workflow(task_name="noop")

        with mock.patch(
            "debusine.server.scheduler.run_workflow_task.apply_async"
        ) as mock_apply_async:
            self.assertEqual(schedule(), [wr])

        self.assert_dispatches_celery_task(
            mock_apply_async, wr, "workflow_noop"
        )
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequest.Statuses.RUNNING)

    def test_sub_workflow(self) -> None:
        """schedule() orchestrates pending sub-workflows via their root."""
        root_wr = self.playground.create_workflow(task_name="noop")
        sub_wr = self.playground.create_workflow(parent=root_wr)
        # Mark the root workflow running, to simulate the situation where it
        # can only do part of its work and has to be called back later.
        root_wr.mark_running()

        with mock.patch(
            "debusine.server.scheduler.run_workflow_task.apply_async",
            side_effect=lambda *args, **kwargs: sub_wr.mark_running(),
        ) as mock_apply_async:
            schedule()

        self.assert_dispatches_celery_task(
            mock_apply_async, root_wr, "workflow_noop"
        )
        # The (fake) orchestrator was called and marked the sub-workflow as
        # running.
        self.assertEqual(sub_wr.status, WorkRequest.Statuses.RUNNING)

    def test_groups_workflows_by_root(self) -> None:
        """schedule() groups pending workflows by their root workflow."""
        root_wr = self.playground.create_workflow(task_name="noop")
        sub_wr = self.playground.create_workflow(parent=root_wr)

        with mock.patch(
            "debusine.server.scheduler.run_workflow_task.apply_async"
        ) as mock_apply_async:
            schedule()

        # Only the root workflow's orchestrator is called.
        self.assert_dispatches_celery_task(
            mock_apply_async, root_wr, "workflow_noop"
        )
        root_wr.refresh_from_db()
        sub_wr.refresh_from_db()
        self.assertEqual(root_wr.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(sub_wr.status, WorkRequest.Statuses.PENDING)

    def test_sub_workflow_another_workflow_running(self) -> None:
        """Sub-workflows of different root workflows may run concurrently."""
        test_app = TestApp(
            set_as_current=True,
            broker="memory://",
            task_cls=TaskWithBetterArgumentRepresentation,
        )
        self.enterContext(setup_default_app(test_app))

        WorkRequest.objects.all().delete()
        roots = [self.playground.create_workflow() for _ in range(3)]
        # Mark the root workflows running, to simulate the situation where
        # they can only do part of their work and have to be called back
        # later.
        for root in roots:
            self.assertTrue(root.mark_running())
        for root in roots[:2]:
            child = self.playground.create_workflow(parent=root)
            self.assertTrue(child.mark_running())
            self.playground.create_workflow(parent=child)

        with transaction.atomic():
            self.assertEqual(schedule(), roots[:2])

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[root.id] for root in roots[:2]],
        )

        self.playground.create_workflow(parent=roots[-1])
        # A running work request in the same parent workflow that isn't a
        # sub-workflow doesn't stop the sub-workflow running.
        self.playground.create_work_request(
            parent=roots[-1], assign_new_worker=True, mark_running=True
        )

        with transaction.atomic():
            self.assertCountEqual(schedule(), [roots[-1]])

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[root.id] for root in roots],
        )

    def test_sub_workflow_same_workflow_running(self) -> None:
        """Sub-workflows of the same root workflow may not run concurrently."""
        test_app = TestApp(
            set_as_current=True,
            broker="memory://",
            task_cls=TaskWithBetterArgumentRepresentation,
        )
        self.enterContext(setup_default_app(test_app))

        WorkRequest.objects.all().delete()
        root = self.playground.create_workflow()
        root.mark_running()
        for _ in range(2):
            self.playground.create_workflow(parent=root)

        with transaction.atomic():
            self.assertEqual(schedule(), [root])

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[root.id]],
        )

        self.playground.create_workflow(parent=root)

        with transaction.atomic():
            self.assertEqual(schedule(), [])

        workflow_task_results = self.find_workflow_task_results().filter(
            status=states.PENDING
        )
        self.assertQuerySetEqual(
            workflow_task_results.values_list("task_args_array", flat=True),
            [[root.id]],
        )

        # Completing the Celery task (albeit artificially) allows the
        # scheduler to proceed.
        workflow_task_result = workflow_task_results.get()
        workflow_task_result.status = states.SUCCESS
        workflow_task_result.save()

        with transaction.atomic():
            self.assertEqual(schedule(), [root])

        self.assertQuerySetEqual(
            self.find_workflow_task_results()
            .filter(status=states.PENDING)
            .values_list("task_args_array", flat=True),
            [[root.id]],
        )


def _method_has_kwargs(method: Callable[..., Any]) -> bool:
    """
    Check whether method has kwargs suitable for being a signal.

    This is enforced by Django when settings.DEBUG=True. Tests are
    executed with settings.DEBUG=False so the test re-implements Django
    validation to make sure that `**kwargs` is a parameter in the method
    (otherwise is detected only in production).
    """
    parameters = inspect.signature(method).parameters

    return any(  # pragma: no cover
        p for p in parameters.values() if p.kind == p.VAR_KEYWORD
    )
