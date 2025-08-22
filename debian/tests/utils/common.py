# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common utility methods."""

import logging
import socket
import subprocess
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class Configuration:
    """Configuration setup for the integration tests."""

    DEBUSINE_SERVER_USER = 'debusine-server'

    @classmethod
    def get_base_url(cls) -> str:
        """Return the base URL for the test server."""
        return f"https://{socket.getfqdn()}"


class RunResult:
    """Encapsulate the result of executing a command."""

    def __init__(
        self, stdout: str, stderr: str, returncode: int, cmd: list[str]
    ) -> None:
        """Initialize RunResult."""
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.cmd = cmd

        self._parsed_stdout_contents: Any = None
        self._parsed_stdout: bool = False

    def __getitem__(self, key: str) -> Any:
        """Return item from parsed stdout from the command."""
        if not self._parsed_stdout:
            # Parse it only once and only if trying to use __getitem__.
            # Some cmd run by RunResult might not return valid Yaml
            # (so the yaml should only be parsed if it's used)
            try:
                self._parsed_stdout_contents = yaml.safe_load(self.stdout)
                self._parsed_stdout = True
            except yaml.YAMLError as exc:
                raise KeyError(
                    f'KeyError: "{key}" cannot be found: error parsing\n'
                    f"{self.cmd=}\n"
                    f"{self.stdout=}\n"
                    f"{self.stderr=}\n"
                    f"{self.returncode=}\n"
                    f"{exc=}"
                )

        if self._parsed_stdout is None:
            raise KeyError(
                f'KeyError: "{key}" cannot be found: stdout is None\n'
                f"{self.cmd=}\n"
                f"{self.stdout=}\n"
                f"{self.stderr=}\n"
                f"{self.returncode=}"
            )
        elif key not in self._parsed_stdout_contents:
            raise KeyError(
                f'KeyError: "{key}" does not exist.\n'
                f"{self.cmd=}\n"
                f"{self.stdout=}\n"
                f"{self._parsed_stdout_contents=}\n"
                f"{self.stderr=}\n"
                f"{self.returncode=}"
            )

        return self._parsed_stdout_contents[key]


def run_as(user: str, cmd: list[str], *, stdin: str | None = None) -> RunResult:
    """Run cmd as user using sudo."""
    cmd = ['sudo', '-u', user] + cmd

    return run(cmd, stdin=stdin)


def run(
    cmd: list[str], *, stdin: str | None = None, timeout: float | None = None
) -> RunResult:
    """Run cmd with stdin, return subprocess.CompletedProcess."""
    logger.debug('Exec: %s (stdin: %s)', cmd, stdin)

    completed_process = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        input=stdin,
        timeout=timeout,
    )

    return RunResult(
        completed_process.stdout,
        completed_process.stderr,
        completed_process.returncode,
        cmd,
    )
