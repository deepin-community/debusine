#!/usr/bin/env python3

# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""
Debusine integration tests.

Test Sbuild task.
"""

import logging
import subprocess
import unittest
from pathlib import Path
from typing import Any

import yaml

from debusine.artifacts.models import ArtifactCategory
from debusine.tasks.models import LookupSingle
from utils.client import Client
from utils.common import Configuration, launch_tests
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer
from utils.worker import Worker

logger = logging.getLogger(__name__)


class IntegrationTaskSbuildTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration test for the sbuild task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured
    - "sudo -u debusine-server debusine-admin commands" works
    - there is one artifact of category "debian:system-tarball" for "bookworm"
    - environment variable AUTOPKGTEST_TMP is set

    debusine-worker is not started (neither restarted) during the tests.
    """

    TASK_NAME = 'sbuild'

    def setUp(self) -> None:
        """Initialize test."""
        self._sbuild_changes_file_path: Path | None = None

        # If debusine-server or nginx was launched just before the
        # integration-tests.py is launched the debusine-server might not be
        # yet available. Let's wait for the debusine-server to be
        # reachable if it's not ready yet
        self.assertTrue(
            DebusineServer.wait_for_server_ready(),
            f"debusine-server should be available (in "
            f"{Configuration.get_base_url()}) before the integration tests "
            f"are run",
        )

        self.distribution = "stable"
        self.architecture = subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()

        self._worker = Worker()

    def test_work_request_sbuild_unshare(self) -> None:
        """Create an sbuild job: use "unshare" backend."""
        with self.apt_indexes("bookworm") as apt_path:
            artifact_id = self.create_artifact_source(apt_path, "hello")

        task_data = self.create_task_data_from_artifact(
            architecture=self.architecture,
            source_artifact_id=artifact_id,
            backend="unshare",
            environment="debian/match:codename=bookworm",
        )

        self.submit_verify_work_request(task_data)

    def submit_verify_work_request(self, task_data: str) -> None:
        """Client submit a sbuild work request and assert results."""
        # Client submits a work-request
        work_request_id = Client.execute_command(
            "create-work-request", self.TASK_NAME, stdin=task_data
        )["work_request_id"]

        # The worker should get the new work request and start executing it
        self.assertTrue(
            Client.wait_for_work_request_completed(work_request_id, "success")
        )

        work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        debian_binary_packages_artifacts = 0
        debian_upload_artifacts = 0
        for artifact in work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.BINARY_PACKAGES:
                debian_binary_packages_artifacts += 1

                self.assertEqual(artifact["data"]["srcpkg_name"], "hello")
                self.assertEqual(
                    artifact["data"]["architecture"], self.architecture
                )

                version = artifact["data"]["version"]
                self.assertCountEqual(
                    artifact["files"].keys(),
                    [
                        f"hello_{version}_{self.architecture}.deb",
                        f"hello-dbgsym_{version}_{self.architecture}.deb",
                    ],
                )
            elif artifact["category"] == ArtifactCategory.UPLOAD:
                debian_upload_artifacts += 1

                self.assertEqual(artifact["data"]["type"], "dpkg")
                self.assertEqual(
                    artifact["data"]["changes_fields"]["Source"], "hello"
                )

                version = artifact["data"]["changes_fields"]["Version"]
                self.assertCountEqual(
                    artifact["files"].keys(),
                    [
                        f"hello_{version}_{self.architecture}.deb",
                        f"hello-dbgsym_{version}_{self.architecture}.deb",
                        f"hello_{version}_{self.architecture}.buildinfo",
                        f"hello_{version}_{self.architecture}.changes",
                    ],
                )

        self.assertEqual(debian_binary_packages_artifacts, 1)
        self.assertEqual(debian_upload_artifacts, 1)

    @staticmethod
    def create_task_data_from_artifact(
        *,
        architecture: str,
        source_artifact_id: int,
        backend: str,
        environment: LookupSingle | None = None,
        distribution: str | None = None,
    ) -> str:
        """Return valid task_data for an sbuild work request from artifact."""
        data: dict[str, Any] = {
            "build_components": ["any", "all"],
            "host_architecture": architecture,
            "input": {"source_artifact": source_artifact_id},
            "backend": backend,
        }

        if distribution is not None:
            data["distribution"] = distribution

        if environment is not None:
            data["environment"] = environment

        return yaml.safe_dump(data)


if __name__ == '__main__':
    launch_tests("Task sbuild integration tests for debusine")
