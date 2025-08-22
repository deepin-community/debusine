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

Test mmdebstrap related code.
"""

import unittest

from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationTaskMmDebstrapTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration test for the mmdebstrap task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured
    """

    TASK_NAME = "mmdebstrap"

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        # If debusine-server or nginx was launched just before the
        # integration-tests-task-mmdebstrap.py is launched the debusine-server
        # might not be yet available. Let's wait for the debusine-server to be
        # reachable if it's not ready yet
        self.assertTrue(
            DebusineServer.wait_for_server_ready(),
            f"debusine-server should be available (in "
            f"{Configuration.get_base_url()}) before the integration tests "
            f"are run",
        )

    def test_mmdebstrap(self) -> None:
        """Create a workflow to run mmdebstrap."""
        # This asserts that the workflow succeeds.
        self.create_system_images_mmdebstrap(["bookworm", "trixie"])
