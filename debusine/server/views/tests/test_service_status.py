# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the service status view."""
from typing import Any

from django.test import override_settings
from django.urls import reverse

from debusine.db.models import WorkRequest
from debusine.tasks.models import WorkerType
from debusine.test.django import TestCase


class ServiceStatusViewTests(TestCase):
    """Tests for ServiceStatusView."""

    def assertServiceStatus(self, expected: dict[str, Any]) -> None:
        """Assert that the service-status API response matches expected."""
        response = self.client.get(reverse("api:service-status"))
        response_data = response.json()
        self.assertEqual(response_data, expected)

    def test_output_bland(self) -> None:
        """Test output when there's no data to report."""
        self.assertServiceStatus(
            {
                "external_workers_arch": {},
                "external_workers_host_arch": {},
                "tasks": {
                    "Internal": {"pending": 0, "running": 0},
                    "Server": {"pending": 0, "running": 0},
                    "Signing": {"pending": 0, "running": 0},
                    "Wait": {"pending": 0, "running": 0},
                    "Worker": {"pending": 0, "running": 0},
                    "Workflow": {"pending": 0, "running": 0},
                },
                "worker_tasks": {
                    "celery": {"pending": 0, "running": 0},
                    "external": {"pending": 0, "running": 0},
                    "signing": {"pending": 0, "running": 0},
                },
                "worker_tasks_arch": {},
                "workers": {
                    "celery": {
                        "busy": 0,
                        "connected": 0,
                        "idle": 0,
                        "registered": 0,
                    },
                    "external": {
                        "busy": 0,
                        "connected": 0,
                        "idle": 0,
                        "registered": 0,
                    },
                    "signing": {
                        "busy": 0,
                        "connected": 0,
                        "idle": 0,
                        "registered": 0,
                    },
                },
            }
        )

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=True)
    def test_output_example(self) -> None:
        """Test output when there's no data to report."""
        # 1 connected amd64 worker
        worker_amd64_1 = self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "amd64",
                "system:architectures": ["amd64", "i386"],
            }
        )
        worker_amd64_1.mark_connected()

        # 2 arm64 workers, 1 connected
        self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arm64",
                "system:architectures": ["arm64"],
            }
        ).mark_connected()
        self.playground.create_worker(
            extra_dynamic_metadata={
                "system:host_architecture": "arm64",
                "system:architectures": ["arm64"],
            }
        )
        # 1 celery worker
        self.playground.create_worker(
            worker_type=WorkerType.CELERY,
        ).mark_connected()
        # 1 signing worker
        self.playground.create_worker(
            worker_type=WorkerType.SIGNING,
        ).mark_connected()

        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "amd64"},
            status=WorkRequest.Statuses.PENDING,
        )
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "amd64"},
            status=WorkRequest.Statuses.RUNNING,
            worker=worker_amd64_1,
        )
        self.playground.create_work_request(
            task_name="sbuild",
            task_data={"host_architecture": "arm64"},
            status=WorkRequest.Statuses.PENDING,
        )

        self.assertServiceStatus(
            {
                "external_workers_arch": {
                    "amd64": {
                        "busy": 1,
                        "connected": 1,
                        "idle": 0,
                        "registered": 1,
                    },
                    "arm64": {
                        "busy": 0,
                        "connected": 1,
                        "idle": 1,
                        "registered": 2,
                    },
                    "i386": {
                        "busy": 1,
                        "connected": 1,
                        "idle": 0,
                        "registered": 1,
                    },
                },
                "external_workers_host_arch": {
                    "amd64": {
                        "busy": 1,
                        "connected": 1,
                        "idle": 0,
                        "registered": 1,
                    },
                    "arm64": {
                        "busy": 0,
                        "connected": 1,
                        "idle": 1,
                        "registered": 2,
                    },
                },
                "tasks": {
                    "Internal": {"pending": 0, "running": 0},
                    "Server": {"pending": 0, "running": 0},
                    "Signing": {"pending": 0, "running": 0},
                    "Wait": {"pending": 0, "running": 0},
                    "Worker": {"pending": 1, "running": 1},
                    "Workflow": {"pending": 0, "running": 0},
                },
                "worker_tasks": {
                    "celery": {"pending": 0, "running": 0},
                    "external": {"pending": 1, "running": 1},
                    "signing": {"pending": 0, "running": 0},
                },
                "worker_tasks_arch": {
                    "amd64": {"pending": 1, "running": 1},
                    "arm64": {"pending": 1, "running": 0},
                    "i386": {"pending": 0, "running": 0},
                },
                "workers": {
                    "celery": {
                        "busy": 0,
                        "connected": 1,
                        "idle": 1,
                        "registered": 1,
                    },
                    "external": {
                        "busy": 1,
                        "connected": 2,
                        "idle": 1,
                        "registered": 3,
                    },
                    "signing": {
                        "busy": 0,
                        "connected": 1,
                        "idle": 1,
                        "registered": 1,
                    },
                },
            }
        )
