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

Test simplesystemimagebuild related code.
"""

import subprocess
import unittest

import yaml

from debusine.artifacts.models import ArtifactCategory
from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer
from utils.worker import Worker


class IntegrationTaskSimpleSystemImageBuildTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration test for the simplesystemimagebuild task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured
    """

    TASK_NAME = "simplesystemimagebuild"

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        # If debusine-server or nginx was launched just before the
        # integration-tests-simplesystemimagebuild.py is launched the
        # debusine-server might not be yet available. Let's wait for the
        # debusine-server to be reachable if it's not ready yet
        self.assertTrue(
            DebusineServer.wait_for_server_ready(),
            f"debusine-server should be available (in "
            f"{Configuration.get_base_url()}) before the integration tests "
            f"are run",
        )

        self.worker = Worker()

    def test_simplesystemimagebuild(self) -> None:
        """Create a job to build a system image. Verify it built."""
        task_data = {
            "bootstrap_options": {
                "architecture": subprocess.check_output(
                    ["dpkg", "--print-architecture"], text=True
                ).strip(),
                "variant": "minbase",
            },
            "bootstrap_repositories": [
                {
                    "mirror": "http://deb.debian.org/debian",
                    "suite": "bookworm",
                    "components": ["main"],
                }
            ],
            "disk_image": {
                "filename": "debian",
                "format": "qcow2",
                # This is considerably smaller than the default.  It's
                # amd64-specific, but this test is only run on amd64 anyway.
                "kernel_package": "linux-image-cloud-amd64",
                "partitions": [
                    {
                        "size": 1,
                        "filesystem": "ext4",
                    },
                ],
            },
        }

        work_request_id = Client.execute_command(
            "create-work-request",
            self.TASK_NAME,
            stdin=yaml.safe_dump(task_data),
        )["work_request_id"]

        # The worker should get the new work request and start executing it
        status = Client.wait_for_work_request_completed(
            work_request_id, "success", timeout=2400
        )
        if not status:
            self.print_work_request_debug_logs(work_request_id)
        self.assertTrue(status)

        work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        debian_system_image_artifacts = 0
        for artifact in work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.SYSTEM_IMAGE:
                debian_system_image_artifacts += 1

                self.assertIn("debian.qcow2", artifact["files"].keys())

        self.assertEqual(debian_system_image_artifacts, 1)
