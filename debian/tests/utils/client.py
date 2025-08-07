# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Debusine client controller."""
import json
import logging
import os
import stat
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from utils import common
from utils.common import RunResult

logger = logging.getLogger(__name__)


class Client:
    """Manage debusine Client."""

    @staticmethod
    def execute_command(
        command: str,
        *args: Any,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> RunResult:
        """
        Execute a debusine client command.

        :return: dictionary with the YAML-parsed standard output of the command
        """
        cmd = ['debusine'] + [command] + list(map(str, args))
        logger.info("execute_command: %s", cmd)
        result = common.run(cmd, stdin=stdin, timeout=timeout)

        logger.info("stdout: %s", result.stdout)
        logger.info("stderr: %s", result.stderr)

        return result

    @classmethod
    def wait_for_work_request_completed(
        cls, work_request_id: int, expected_result: str, timeout: int = 1800
    ) -> bool:
        """
        Wait for a work request to finish and have the expected result.

        It uses debusine on-work-request-completed to wait for the work
        request to finish.

        :timeout: seconds before raising TimeoutExpired
        :return: True if it completed with "success", False if completed
          with something else. If it not completed before timeout it
          raises TimeoutExpired.
        """
        # The method create a shell script that will kill its parent.
        # The parent of the shell script is "debusine on-work-request-completed"
        # which waits until the children kills it.

        # delete=False and close the file: because if the file is open
        # bash does not execute it:
        # bash: /tmp/tmpmkgu_i0w: bin/bash: bad interpreter: Text file busy
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, prefix="debusine-integration-tests-"
        )
        temp_file.close()

        on_work_request_completed_command = Path(temp_file.name)

        # Result file to check that the work request was the correct one
        # and was completed.
        result_file = tempfile.NamedTemporaryFile(mode="w+")

        on_work_request_completed_command.write_text(
            textwrap.dedent(
                f"""\
            #!/bin/sh

            if [ "$1" = "{work_request_id}" ]
            then
                echo -n $1,$2 > {result_file.name}
                kill $PPID  # Kill debusine.client on-work-request-completed
            fi
            """
            )
        )

        os.chmod(on_work_request_completed_command, stat.S_IXUSR | stat.S_IRUSR)

        last_completed_at = tempfile.NamedTemporaryFile(mode="w")
        json.dump(
            {"last_completed_at": "1970-01-01T00:00:00.00000"},
            last_completed_at,
        )
        last_completed_at.flush()

        # Wait for a work request to finish
        cls.execute_command(
            "on-work-request-completed",
            "--last-completed-at",
            last_completed_at.name,
            on_work_request_completed_command,
            timeout=timeout,
        )

        # The work request completed, delete the script
        on_work_request_completed_command.unlink()

        # Verify that the work request is the expected one
        work_request_completed_id, result = (
            Path(result_file.name).read_text().split(",")
        )

        assert (
            int(work_request_completed_id) == work_request_id
        ), "Unexpected work request finished"

        return result == expected_result
