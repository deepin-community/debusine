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

Test autopkgtest related code.
"""

import subprocess
import unittest

import yaml

from debusine.artifacts.models import ArtifactCategory
from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationTaskAutopkgtestTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration test for the autopkgtest task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured
    """

    TASK_NAME = "autopkgtest"

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        # If debusine-server or nginx was launched just before the
        # integration-tests-autopkgtest.py is launched the debusine-server
        # might not be yet available. Let's wait for the debusine-server to be
        # reachable if it's not ready yet
        self.assertTrue(
            DebusineServer.wait_for_server_ready(),
            f"debusine-server should be available (in "
            f"{Configuration.get_base_url()}) before the integration tests "
            f"are run",
        )

        self.architecture = subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()

    def test_autopkgtest(self) -> None:
        """Create an autopkgtest job: download the artifact and build."""
        with self.apt_indexes("bookworm") as apt_path:
            source_artifact_id = self.create_artifact_source(apt_path, "hello")
            upload_artifact_id = self.create_artifact_upload(
                apt_path, ["hello"]
            )

        task_data = {
            "input": {
                "source_artifact": source_artifact_id,
                "binary_artifacts": [upload_artifact_id],
            },
            "host_architecture": self.architecture,
            "environment": "debian/match:codename=bookworm",
            "backend": "unshare",
        }

        work_request_id = Client.execute_command(
            "create-work-request",
            self.TASK_NAME,
            stdin=yaml.safe_dump(task_data),
        )["work_request_id"]

        # The worker should get the new work request and start executing it
        status = Client.wait_for_work_request_completed(
            work_request_id, "success"
        )
        if not status:
            self.print_work_request_debug_logs(work_request_id)
        self.assertTrue(status)

        work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        debian_autopkgtest_artifacts = 0
        for artifact in work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.AUTOPKGTEST:
                debian_autopkgtest_artifacts += 1

                # Expected testinfo.json in the artifact
                self.assertIn("testinfo.json", artifact["files"].keys())

                # The log has a reasonable Content-Type
                self.assertEqual(
                    artifact["files"]["log"]["content_type"],
                    "text/plain; charset=us-ascii",
                )

                # Check some of the data contents
                self.assertIn("results", artifact["data"])
                self.assertIn("source_package", artifact["data"])

        self.assertEqual(debian_autopkgtest_artifacts, 1)
