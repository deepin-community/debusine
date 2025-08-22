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

Test DebDiff task.
"""

import subprocess
import unittest

import yaml

from debusine.artifacts.models import ArtifactCategory
from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationTaskDebDiffTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration test for the DebDiff task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured

    debusine-worker is not started (neither restarted) during the tests.
    """

    TASK_NAME = "debdiff"

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
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

        self.architecture = subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()

    def test_debdiff_source(self) -> None:
        """Create debdiff job: input are two dsc files."""
        with self.apt_indexes("bookworm") as apt_path:
            source_artifact_id1 = self.create_artifact_source(apt_path, "hello")
            source_artifact_id2 = self.create_artifact_source(
                apt_path, "hello-traditional"
            )

        task_data = {
            "input": {
                "source_artifacts": [source_artifact_id1, source_artifact_id2],
            },
            "environment": "debian/match:codename=bookworm",
            "host_architecture": self.architecture,
            "backend": "unshare",
        }

        # Client submits a work-request
        work_request_id = Client.execute_command(
            "create-work-request",
            self.TASK_NAME,
            stdin=yaml.safe_dump(task_data),
        )["work_request_id"]

        # The worker should get the new work request and start executing it,
        # wait for success
        self.assertTrue(
            Client.wait_for_work_request_completed(work_request_id, "success")
        )

        show_work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        debian_debdiff_artifacts = 0

        for artifact in show_work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.DEBDIFF:
                debian_debdiff_artifacts += 1

                # Expected debdiff.txt
                self.assertCountEqual(artifact["files"].keys(), ["debdiff.txt"])

        # 1 artifact for each job
        self.assertEqual(debian_debdiff_artifacts, 1)

    def test_debdiff_binary(self) -> None:
        """Create debdiff job: input are two changes files."""
        with self.apt_indexes("bookworm") as apt_path:
            binary_artifact_id1 = self.create_artifact_upload(
                apt_path, ["hello"]
            )
            binary_artifact_id2 = self.create_artifact_upload(
                apt_path, ["hello-traditional"]
            )

        task_data = {
            "input": {
                "binary_artifacts": [
                    [binary_artifact_id1],
                    [binary_artifact_id2],
                ],
            },
            "environment": "debian/match:codename=bookworm",
            "host_architecture": self.architecture,
            "backend": "unshare",
        }

        # Client submits a work-request
        work_request_id = Client.execute_command(
            "create-work-request",
            self.TASK_NAME,
            stdin=yaml.safe_dump(task_data),
        )["work_request_id"]

        # The worker should get the new work request and start executing it,
        # wait for success
        self.assertTrue(
            Client.wait_for_work_request_completed(work_request_id, "success")
        )

        show_work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        debian_debdiff_artifacts = 0

        for artifact in show_work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.DEBDIFF:
                debian_debdiff_artifacts += 1

                # Expected debdiff.txt
                self.assertCountEqual(artifact["files"].keys(), ["debdiff.txt"])

        # 1 artifact for each job
        self.assertEqual(debian_debdiff_artifacts, 1)
