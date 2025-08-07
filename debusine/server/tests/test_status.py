# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for queue and worker pool summaries."""

from itertools import product

from django.test import override_settings

from debusine.db.models import WorkRequest
from debusine.server.status import (
    QueueStatus,
    TaskQueueSummary,
    WorkerStatus,
    WorkerSummary,
)
from debusine.tasks.models import (
    MmDebstrapBootstrapOptions,
    MmDebstrapData,
    SystemBootstrapRepository,
    TaskTypes,
    WorkerType,
)
from debusine.test.django import TestCase


# We want to be in control of the scheduling status of tasks, for these tests
@override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
class WorkerStatusTests(TestCase):
    """Tests for WorkerStatus."""

    def assertTasks(
        self, status: QueueStatus, task_type: TaskTypes, state: str, value: int
    ) -> None:
        """Assert tasks in status for task_type have state == value."""
        type_tasks = status.tasks.get(task_type, TaskQueueSummary())
        self.assertEqual(getattr(type_tasks, state), value)

    def assertWorkers(
        self,
        status: QueueStatus,
        worker_type: WorkerType,
        state: str,
        value: int,
    ) -> None:
        """Assert workers in status for worker_type have state == value."""
        type_status = status.workers.get(worker_type, WorkerSummary())
        self.assertEqual(getattr(type_status, state), value)

    def assertWorkerTasks(
        self,
        status: QueueStatus,
        worker_type: WorkerType,
        state: str,
        value: int,
    ) -> None:
        """Assert worker_tasks in status for worker_type have state == value."""
        type_tasks = status.worker_tasks.get(worker_type, TaskQueueSummary())
        self.assertEqual(getattr(type_tasks, state), value)

    def assertExternalWorkersArch(
        self, status: QueueStatus, arch: str, state: str, value: int
    ) -> None:
        """Assert external_workers_arch for arch have state == value."""
        arch_status = status.external_workers_arch.get(arch, WorkerSummary())
        self.assertEqual(getattr(arch_status, state), value)

    def assertExternalWorkersHostArch(
        self, status: QueueStatus, arch: str, state: str, value: int
    ) -> None:
        """Assert external_workers_host_arch for arch have state == value."""
        arch_status = status.external_workers_host_arch.get(
            arch, WorkerSummary()
        )
        self.assertEqual(getattr(arch_status, state), value)

    def assertWorkerTasksArch(
        self, status: QueueStatus, arch: str, state: str, value: int
    ) -> None:
        """Assert worker_tasks_arch in status for arch have state == value."""
        arch_tasks = status.worker_tasks_arch.get(arch, TaskQueueSummary())
        self.assertEqual(getattr(arch_tasks, state), value)

    def test_counts_tasks_by_type(self) -> None:
        """Test that a tasks counts tasks by type."""
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.RUNNING,
            task_type=TaskTypes.SIGNING,
        )

        status = WorkerStatus.get_status()
        self.assertTasks(status, TaskTypes.WORKER, "pending", 1)
        self.assertTasks(status, TaskTypes.WORKER, "running", 0)
        self.assertTasks(status, TaskTypes.SIGNING, "pending", 0)
        self.assertTasks(status, TaskTypes.SIGNING, "running", 1)
        self.assertTasks(status, TaskTypes.INTERNAL, "pending", 0)
        self.assertTasks(status, TaskTypes.INTERNAL, "running", 0)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "pending", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "running", 0)
        self.assertWorkerTasks(status, WorkerType.SIGNING, "pending", 0)
        self.assertWorkerTasks(status, WorkerType.SIGNING, "running", 1)
        self.assertWorkerTasks(status, WorkerType.CELERY, "pending", 0)
        self.assertWorkerTasks(status, WorkerType.CELERY, "running", 0)

    def test_worker_counts_against_all_archs(self) -> None:
        """Test that a worker counts against all architectures it supports."""
        worker = self.playground.create_worker(
            worker_type=WorkerType.EXTERNAL,
            extra_dynamic_metadata={
                "system:host_architecture": "amd64",
                "system:architectures": ["amd64", "i386"],
            },
        )
        worker.mark_connected()

        status = WorkerStatus.get_status()
        self.assertExternalWorkersArch(status, "amd64", "connected", 1)
        self.assertExternalWorkersArch(status, "i386", "connected", 1)
        # and by host_arch:
        self.assertExternalWorkersHostArch(status, "amd64", "connected", 1)
        self.assertExternalWorkersHostArch(status, "i386", "connected", 0)
        # and by type:
        self.assertWorkers(status, WorkerType.EXTERNAL, "connected", 1)

    def test_work_request_counts_host_architecture_data(self) -> None:
        """
        Test that a work request is counted against its host_architecture.

        For a WorkRequest with host_architecture specified in task_data.
        """
        for task_type, wr_status in product(
            [TaskTypes.WORKER, TaskTypes.SIGNING],
            [WorkRequest.Statuses.PENDING, WorkRequest.Statuses.RUNNING],
        ):
            self.playground.create_work_request(
                task_type=task_type,
                task_name="noop",
                task_data={"host_architecture": "amd64"},
                status=wr_status,
            )

        status = WorkerStatus.get_status()
        self.assertWorkerTasksArch(status, "amd64", "pending", 1)
        self.assertWorkerTasksArch(status, "amd64", "running", 1)
        self.assertTasks(status, TaskTypes.WORKER, "pending", 1)
        self.assertTasks(status, TaskTypes.WORKER, "running", 1)
        self.assertTasks(status, TaskTypes.SIGNING, "pending", 1)
        self.assertTasks(status, TaskTypes.SIGNING, "running", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "pending", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "running", 1)
        self.assertWorkerTasks(status, WorkerType.SIGNING, "pending", 1)
        self.assertWorkerTasks(status, WorkerType.SIGNING, "running", 1)

    def test_work_request_filters_by_task_type(self) -> None:
        """Test that worker_tasks_arch only includes WORKER work requests."""

    def test_work_request_counts_host_architecture_method(self) -> None:
        """
        Test that a work request is counted against its host_architecture.

        For a WorkRequest with host_architecture not specified in task_data.
        Instead the Tasks's .host_architecture() method is called to determine
        the architecture.
        """
        task_data = MmDebstrapData(
            bootstrap_options=MmDebstrapBootstrapOptions(architecture="amd64"),
            bootstrap_repositories=[
                SystemBootstrapRepository(
                    mirror="https://deb.debian.org/debian", suite="stable"
                )
            ],
        )
        expected_architecture = task_data.bootstrap_options.architecture

        self.playground.create_work_request(
            task_name="mmdebstrap",
            task_data=task_data,
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="mmdebstrap",
            task_data=task_data,
            status=WorkRequest.Statuses.RUNNING,
        )

        status = WorkerStatus.get_status()
        self.assertWorkerTasksArch(status, expected_architecture, "pending", 1)
        self.assertWorkerTasksArch(status, expected_architecture, "running", 1)
        self.assertTasks(status, TaskTypes.WORKER, "pending", 1)
        self.assertTasks(status, TaskTypes.WORKER, "running", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "pending", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "running", 1)

    def test_work_request_non_architecture_constrained(self) -> None:
        """
        Test that a work request without an architecture counts as arch:None.

        Work request without "host_architecture" in task_data and
        host_architecture() returns None.
        """
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.RUNNING,
        )

        status = WorkerStatus.get_status()

        actual = status.worker_tasks_arch
        expected = {None: TaskQueueSummary(pending=1, running=1)}
        self.assertEqual(actual, expected)

        # Counts by type too:
        self.assertTasks(status, TaskTypes.WORKER, "pending", 1)
        self.assertTasks(status, TaskTypes.WORKER, "running", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "pending", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "running", 1)

    def test_work_request_fails_to_configure(self) -> None:
        """
        Test that a work request that fails to configure counts as arch:None.

        Work request without "host_architecture" in task_data and fails to
        configure. Will immediately fail, without even getting scheduled.
        """
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"foo": "bar"},
            status=WorkRequest.Statuses.PENDING,
        )

        status = WorkerStatus.get_status()

        actual = status.worker_tasks_arch
        expected = {None: TaskQueueSummary(pending=1, running=0)}
        self.assertEqual(actual, expected)

        # Counts by type too:
        self.assertTasks(status, TaskTypes.WORKER, "pending", 1)
        self.assertTasks(status, TaskTypes.WORKER, "running", 0)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "pending", 1)
        self.assertWorkerTasks(status, WorkerType.EXTERNAL, "running", 0)

    def test_get_status_no_pending_work_requests(self) -> None:
        """
        get_status: no pending work requests.

        Empty dictionary because no pending work requests and no workers.
        """
        status = WorkerStatus.get_status()
        self.assertEqual(status.external_workers_arch, {})
        self.assertEqual(status.worker_tasks_arch, {})
        for task_type in TaskTypes:
            self.assertEqual(status.tasks[task_type], TaskQueueSummary())
        for worker_type in WorkerType:
            self.assertEqual(status.workers[worker_type], WorkerSummary())
            self.assertEqual(
                status.worker_tasks[worker_type], TaskQueueSummary()
            )

    def test_disabled_workers_ignored(self) -> None:
        """Workers are not counted if they are disabled."""
        self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch1",
                "system:architectures": ["arch1", "arch2"],
            }
        )

        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch3",
                "system:architectures": ["arch3"],
            }
        )
        assert worker.token is not None
        worker.token.disable()

        status = WorkerStatus.get_status()
        self.assertNotIn("arch3", status.external_workers_arch)
        self.assertNotIn("arch3", status.external_workers_host_arch)
        self.assertWorkers(status, WorkerType.EXTERNAL, "registered", 1)

    def test_count_registered_workers(self) -> None:
        """Registered and connected workers are counted."""
        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch1",
                "system:architectures": ["arch1", "arch2"],
            }
        )
        worker.mark_connected()

        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch3",
                "system:architectures": ["arch3"],
            }
        )

        status = WorkerStatus.get_status()
        self.assertExternalWorkersArch(status, "arch1", "registered", 1)
        self.assertExternalWorkersArch(status, "arch2", "registered", 1)
        self.assertExternalWorkersArch(status, "arch3", "registered", 1)
        self.assertExternalWorkersArch(status, "arch1", "connected", 1)
        self.assertExternalWorkersArch(status, "arch2", "connected", 1)
        self.assertExternalWorkersArch(status, "arch3", "connected", 0)
        # and by host_arch:
        self.assertExternalWorkersHostArch(status, "arch1", "registered", 1)
        self.assertExternalWorkersHostArch(status, "arch2", "registered", 0)
        self.assertExternalWorkersHostArch(status, "arch3", "registered", 1)
        self.assertExternalWorkersHostArch(status, "arch1", "connected", 1)
        self.assertExternalWorkersHostArch(status, "arch2", "connected", 0)
        self.assertExternalWorkersHostArch(status, "arch3", "connected", 0)
        # and by type:
        self.assertWorkers(status, WorkerType.EXTERNAL, "registered", 2)
        self.assertWorkers(status, WorkerType.EXTERNAL, "connected", 1)

    def test_count_registered_workers_by_type(self) -> None:
        """Registered workers are counted by type."""
        self.playground.create_worker(
            worker_type=WorkerType.EXTERNAL
        ).mark_connected()
        self.playground.create_worker(worker_type=WorkerType.EXTERNAL)
        self.playground.create_worker(
            worker_type=WorkerType.SIGNING
        ).mark_connected()
        self.playground.create_worker(
            worker_type=WorkerType.CELERY
        ).mark_connected()

        status = WorkerStatus.get_status()
        self.assertWorkers(status, WorkerType.EXTERNAL, "registered", 2)
        self.assertWorkers(status, WorkerType.SIGNING, "registered", 1)
        self.assertWorkers(status, WorkerType.CELERY, "registered", 1)
        self.assertWorkers(status, WorkerType.EXTERNAL, "connected", 1)
        self.assertWorkers(status, WorkerType.SIGNING, "connected", 1)
        self.assertWorkers(status, WorkerType.CELERY, "connected", 1)

    def test_count_busy_workers_running(self) -> None:
        """Assigned RUNNING tasks count as a busy worker."""
        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch1",
                "system:architectures": ["arch1", "arch2"],
            }
        )
        worker.mark_connected()

        self.playground.create_work_request(
            task_name="noop",
            task_data={"host_architecture": "arch1"},
            status=WorkRequest.Statuses.RUNNING,
            worker=worker,
        )

        status = WorkerStatus.get_status()
        self.assertExternalWorkersArch(status, "arch1", "busy", 1)
        # the arch2 slot is also busy:
        self.assertExternalWorkersArch(status, "arch2", "busy", 1)
        # host_arch is busy:
        self.assertExternalWorkersHostArch(status, "arch1", "busy", 1)
        # and by type:
        self.assertWorkers(status, WorkerType.EXTERNAL, "busy", 1)

    def test_count_busy_workers_pending(self) -> None:
        """Assigned PENDING tasks count as a busy worker."""
        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch1",
                "system:architectures": ["arch1", "arch2"],
            }
        )
        worker.mark_connected()

        self.playground.create_work_request(
            task_name="noop",
            task_data={"host_architecture": "arch1"},
            status=WorkRequest.Statuses.PENDING,
            worker=worker,
        )

        status = WorkerStatus.get_status()
        self.assertExternalWorkersArch(status, "arch1", "busy", 1)
        # the arch2 slot is also busy:
        self.assertExternalWorkersArch(status, "arch2", "busy", 1)
        # host_arch is busy:
        self.assertExternalWorkersHostArch(status, "arch1", "busy", 1)
        # and by type:
        self.assertWorkers(status, WorkerType.EXTERNAL, "busy", 1)

    def test_count_idle_workers(self) -> None:
        """Connected workers without assigned tasks are idle."""
        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch1",
                "system:architectures": ["arch1"],
            }
        )
        worker.mark_connected()
        worker = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arch2",
                "system:architectures": ["arch2"],
            }
        )
        status = WorkerStatus.get_status()
        self.assertExternalWorkersArch(status, "arch1", "idle", 1)
        self.assertExternalWorkersArch(status, "arch2", "registered", 1)
        self.assertExternalWorkersArch(status, "arch2", "idle", 0)
        # host_arch should be identical:
        self.assertExternalWorkersHostArch(status, "arch1", "idle", 1)
        self.assertExternalWorkersHostArch(status, "arch2", "registered", 1)
        self.assertExternalWorkersHostArch(status, "arch2", "idle", 0)
        # and by type:
        self.assertWorkers(status, WorkerType.EXTERNAL, "registered", 2)
        self.assertWorkers(status, WorkerType.EXTERNAL, "idle", 1)

    def test_external_workers_arch_populated_for_all_task_archs(self) -> None:
        """We populate external_workers_arch for all queued architectures."""
        self.playground.create_work_request(
            task_name="noop",
            task_data={"host_architecture": "arch1"},
            status=WorkRequest.Statuses.PENDING,
        )
        status = WorkerStatus.get_status()
        self.assertIn("arch1", status.external_workers_arch)

    def test_worker_tasks_populated_for_all_worker_archs(self) -> None:
        """We populate worker_tasks_arch for all known worker architectures."""
        self.playground.create_worker(
            extra_dynamic_metadata={"system:architectures": ["arch1"]}
        )
        status = WorkerStatus.get_status()
        self.assertIn("arch1", status.worker_tasks_arch)

    def test_missing_system_architecture(self) -> None:
        """Workers without system:architecture log a warning."""
        self.playground.create_worker().mark_connected()
        with self.assertLogs("debusine.server.status") as cm:
            WorkerStatus.get_status()
        self.assertEqual(
            cm.output,
            [
                "WARNING:debusine.server.status:Worker computer-lan missing "
                "system:host_architecture"
            ],
        )

    def test_full_example(self) -> None:
        """Test the full data output of a non-trivial example state."""
        # Create three work requests of type sbuild
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "amd64"},
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "amd64"},
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "arm64"},
            status=WorkRequest.Statuses.PENDING,
        )

        # Create three connected workers
        worker_amd64_1 = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "amd64",
                "system:architectures": ["amd64", "i386"],
            }
        )
        worker_amd64_1.mark_connected()

        worker_arm64_1 = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arm64",
                "system:architectures": ["arm64"],
            }
        )
        worker_arm64_1.mark_connected()

        worker_arm64_2 = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arm64",
                "system:architectures": ["arm64"],
            }
        )
        worker_arm64_2.mark_connected()

        # Create non-connected worker (will not appear as available
        # worker in the count for arm64)
        self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arm64",
                "system:architectures": ["arm64"],
            }
        )
        # And a celery worker
        self.playground.create_worker(
            worker_type=WorkerType.CELERY,
        ).mark_connected()

        # Create a running task
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "arm64"},
            status=WorkRequest.Statuses.RUNNING,
            worker=worker_arm64_1,
        )

        # Create a running wait task
        self.playground.create_work_request(
            task_type=TaskTypes.WAIT,
            task_name="externaldebsign",
            status=WorkRequest.Statuses.RUNNING,
        )

        actual = WorkerStatus.get_status()

        expected = QueueStatus(
            tasks={
                TaskTypes.WORKER: TaskQueueSummary(pending=1, running=1),
                TaskTypes.SERVER: TaskQueueSummary(),
                TaskTypes.INTERNAL: TaskQueueSummary(),
                TaskTypes.WORKFLOW: TaskQueueSummary(),
                TaskTypes.SIGNING: TaskQueueSummary(),
                TaskTypes.WAIT: TaskQueueSummary(running=1),
            },
            workers={
                WorkerType.CELERY: WorkerSummary(
                    registered=1, connected=1, idle=1
                ),
                WorkerType.EXTERNAL: WorkerSummary(
                    registered=4, connected=3, idle=2, busy=1
                ),
                WorkerType.SIGNING: WorkerSummary(),
            },
            worker_tasks={
                WorkerType.CELERY: TaskQueueSummary(),
                WorkerType.EXTERNAL: TaskQueueSummary(pending=1, running=1),
                WorkerType.SIGNING: TaskQueueSummary(),
            },
            external_workers_arch={
                "amd64": WorkerSummary(
                    registered=1, connected=1, idle=1, busy=0
                ),
                "arm64": WorkerSummary(
                    registered=3, connected=2, idle=1, busy=1
                ),
                "i386": WorkerSummary(
                    registered=1, connected=1, idle=1, busy=0
                ),
            },
            external_workers_host_arch={
                "amd64": WorkerSummary(
                    registered=1, connected=1, idle=1, busy=0
                ),
                "arm64": WorkerSummary(
                    registered=3, connected=2, idle=1, busy=1
                ),
            },
            worker_tasks_arch={
                "amd64": TaskQueueSummary(pending=2, running=0),
                "arm64": TaskQueueSummary(pending=1, running=1),
                "i386": TaskQueueSummary(pending=0, running=0),
            },
        )

        self.assertEqual(actual, expected)
