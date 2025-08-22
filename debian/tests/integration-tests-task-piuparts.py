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

Test Piuparts task.
"""

import subprocess
import textwrap
import unittest

from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationTaskPiupartsTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration test for the Piuparts task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured

    debusine-worker is not started (neither restarted) during the tests.
    """

    TASK_NAME = "piuparts"

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

    def test_piuparts_input_source_artifact(self) -> None:
        """Create piuparts job: input is "hello" source package."""
        with self.apt_indexes("bookworm") as apt_path:
            upload_artifact_id = self.create_artifact_upload(
                apt_path, ["hello"]
            )

        task_data = textwrap.dedent(
            f'''\
            input:
              binary_artifacts:
              - {upload_artifact_id}
            host_architecture: {self.architecture}
            # Needs piuparts >= 1.3.
            environment: debian/match:codename=trixie
            backend: unshare
            base_tgz: debian/match:format=tarball:codename=bookworm
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
