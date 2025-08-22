# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Debusine worker controller."""

import logging
import os
import shutil
import socket
from pathlib import Path

from utils import common
from utils.server import DebusineServer
from utils.waiter import Waiter

logger = logging.getLogger(__name__)


class Worker:
    """Worker management."""

    CONFIG_DIRECTORY = Path('/etc/debusine/worker')
    TOKEN_FILE = CONFIG_DIRECTORY / 'token'
    ACTIVATION_TOKEN_FILE = CONFIG_DIRECTORY / 'activation-token'

    @classmethod
    def set_up(cls, use_activation_token: bool = True) -> None:
        """
        Set up worker and waits for the token file.

        Steps:

        - stop a possible debusine-worker
        - copy the debusine-worker's config.ini file to the appropriate
          directory
        - if use_activation_token is true:
            - create the worker in the server's database with an activation
              token
            - configure the worker using that activation token
        - start debusine-worker
        - wait for the (non-activation) token file to appear
        """
        common.run(['systemctl', 'stop', 'debusine-worker'])

        # Make sure to delete the self.CONFIG_DIRECTORY
        if cls.CONFIG_DIRECTORY.exists():
            shutil.rmtree(cls.CONFIG_DIRECTORY)
        cls.CONFIG_DIRECTORY.mkdir(parents=True)
        shutil.chown(cls.CONFIG_DIRECTORY, 'debusine-worker')

        with (
            open(
                "/usr/share/doc/debusine-worker/examples/config.ini"
            ) as orig_config_file,
            open(cls.CONFIG_DIRECTORY / "config.ini", "w") as config_file,
        ):
            for line in orig_config_file:
                if line.startswith("api-url = "):
                    config_file.write(
                        f"api-url = https://{socket.getfqdn()}/api\n"
                    )
                else:
                    config_file.write(line)

        if use_activation_token:
            created_worker = DebusineServer.execute_command(
                "worker", "create", socket.getfqdn()
            )

            with open(cls.ACTIVATION_TOKEN_FILE, "w") as token_file:
                os.fchmod(token_file.fileno(), 0o600)
                token_file.write(created_worker.stdout)
            shutil.chown(cls.ACTIVATION_TOKEN_FILE, "debusine-worker")

        common.run(['systemctl', 'start', 'debusine-worker'])

        cls.wait_for_token_file()

    @classmethod
    def wait_for_token_file(cls) -> bool:
        """
        Wait for the /etc/debusine/worker/token file to appear.

        :return: True if the file appeared, False if it timed out (30 seconds)
        """
        return Waiter.wait_for_success(30, cls.TOKEN_FILE.exists)

    @classmethod
    def read_token(cls) -> str:
        """Return token: contents of cls.TOKEN_FILE."""
        with open(cls.TOKEN_FILE) as token_file:
            return token_file.read()
