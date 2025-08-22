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

Test Blhc task.
"""

import textwrap
import unittest

from debusine.artifacts.models import ArtifactCategory
from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationTaskBlhcTests(IntegrationTestHelpersMixin, unittest.TestCase):
    """
    Integration test for the Blhc task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured

    debusine-worker is not started (neither restarted) during the tests.
    """

    TASK_NAME = "blhc"

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

    def test_blhc_input_source_artifact(self) -> None:
        """Create blhc job: input is an empty build log."""
        artifact_id = self.create_artifact_build_logs("foo.build", "")

        task_data = textwrap.dedent(
            f'''\
            input:
              artifact: {artifact_id}
            environment: debian/match:codename=bookworm
            backend: unshare
            '''
        )

        # Client submits a work-request
        work_request_id = Client.execute_command(
            "create-work-request", self.TASK_NAME, stdin=task_data
        )["work_request_id"]

        # The worker should get the new work request and start executing it,
        # wait for success
        self.assertTrue(
            Client.wait_for_work_request_completed(work_request_id, "success")
        )

        show_work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        debian_blhc_artifacts = 0

        for artifact in show_work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.BLHC:
                debian_blhc_artifacts += 1

                # Expected blhc.txt
                self.assertCountEqual(artifact["files"].keys(), ["blhc.txt"])

        # 1 artifact for each job
        self.assertEqual(debian_blhc_artifacts, 1)
