# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Debusine server controller."""

import hashlib
import logging

import requests
import yaml

from utils import common
from utils.common import Configuration, RunResult
from utils.waiter import Waiter

logger = logging.getLogger(__name__)


class DebusineServer:
    """Interacts with the debusine-server: execute debusine-admin commands."""

    @staticmethod
    def wait_for_server_ready() -> bool:
        """
        Wait up to 30 seconds for the server to be ready.

        Return True/False depending on if the server is ready or not.
        """

        def is_ready() -> bool:
            """Return True if {self._api_url}/ is available."""
            response = requests.get(f"{Configuration.get_base_url()}/")
            return response.status_code < 400

        return Waiter.wait_for_success(30, is_ready)

    @staticmethod
    def restart() -> RunResult:
        """Restart (via systemctl) debusine-server."""
        return common.run(["systemctl", "restart", "debusine-server"])

    @staticmethod
    def execute_command(
        command: str,
        *args: str,
        user: str = Configuration.DEBUSINE_SERVER_USER,
        stdin: str | None = None,
    ) -> RunResult:
        """Execute a debusine server management command."""
        cmd = ["debusine-admin"] + [command] + [*args]
        result = common.run_as(user, cmd, stdin=stdin)

        if result.stdout:
            logger.info("stdout: %s", result.stdout)
        if result.stderr:
            logger.info("stderr: %s", result.stderr)

        return result

    @classmethod
    def verify_worker(
        cls,
        token: str,
        connected: bool,
        enabled: bool,
        list_workers: str | None = None,
    ) -> bool:
        """
        Return True if ``worker list`` has token with the specified state.

        :param token: token that is being verified
        :param connected: True if it is expected that the worker is connected
        :param enabled: True if it is expected that the worker's token enabled
        :param list_workers: output of the command ``worker list --yaml`` or
          None.  If None it executes ``worker list --yaml``
        :return: True if a token is connected and enabled as per connected
          and enabled parameters
        """
        if list_workers is None:
            list_workers_output = cls.execute_command(
                "worker", "list", "--yaml"
            ).stdout

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        for worker in yaml.safe_load(list_workers_output):
            if worker["hash"] == token_hash:
                return (
                    bool(worker["connected"]) == connected
                    and worker["enabled"] == enabled
                )

        return False

    @classmethod
    def wait_for_worker_connected(cls, token: str) -> bool:
        """Wait up to 30 seconds for the worker to be connected."""

        def token_is_connected() -> bool:
            return cls.verify_worker(token, connected=True, enabled=True)

        return Waiter.wait_for_success(30, token_is_connected)
