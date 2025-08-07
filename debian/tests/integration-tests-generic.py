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
Debusine generic integration tests.

Does not test any sbuild related code.
"""

import argparse
import logging
import os
import shutil
import sys
import textwrap
import unittest
from pathlib import Path

import lxml.html
import requests
import yaml

from utils.client import Client
from utils.common import Configuration
from utils.server import DebusineServer
from utils.worker import Worker

logger = logging.getLogger(__name__)


class IntegrationGenericTests(unittest.TestCase):
    """
    Integration generic tests (excludes sbuild specific tests).

    These tests assume:
    - debusine-server is running
    - debusine-client is correctly configured
    - "sudo -u debusine-server debusine-admin COMMAND" works

    Note that these tests (via Worker) delete /etc/debusine/worker contents,
    stop and start debusine-worker, create new tokens in the debusine-server
    """

    TASK_NAME = 'noop'

    def setUp(self) -> None:
        """Initialize test."""
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

        self._worker = Worker()

    def connect_worker(self) -> None:
        """
        Connect the worker to the debusine-server.

        Steps:
        - set up worker (it fetches a disabled token file)
        - enable the token
        - wait for the worker to be connected
        """
        self._worker.set_up()

        # Debusine admin enables the worker's token
        DebusineServer.execute_command(
            'manage_worker', 'enable', Worker.read_token()
        )

        # Worker re-connect automatically
        self.assertTrue(
            DebusineServer.wait_for_worker_connected(Worker.read_token())
        )

    def test_secret_key_file_not_readable_fails(self) -> None:
        """Key file cannot be read: the server does not start."""
        key_file = "/var/lib/debusine/server/key"
        tmp_key_file = "/var/lib/debusine/server/key.tmp"

        shutil.move(key_file, tmp_key_file)
        self.addCleanup(DebusineServer.restart)
        self.addCleanup(shutil.move, tmp_key_file, key_file)

        # Cannot start because key file cannot be read
        self.assertEqual(DebusineServer.restart().returncode, 1)

    def test_worker_is_not_connected(self) -> None:
        """The worker is not connected: the token is not enabled yet."""
        self._worker.set_up()

        self.assertTrue(
            DebusineServer.verify_worker(
                self._worker.read_token(),
                connected=False,
                enabled=False,
            )
        )

    def test_worker_connects(self) -> None:
        """The admin enables the worker and it gets connected."""
        self.connect_worker()

        self.assertTrue(
            DebusineServer.verify_worker(
                Worker.read_token(), connected=True, enabled=True
            )
        )

    def test_create_work_requests_invalid(self) -> None:
        """Create an invalid work request."""
        # Client submits an invalid work-request (status will be 'error')
        # It is not valid because task_data is not valid

        self.connect_worker()

        create_work_request_output = Client.execute_command(
            "create-work-request", self.TASK_NAME, stdin="foo: bar"
        )

        # Work Request is registered
        self.assertEqual(create_work_request_output["result"], "failure")

        # Specific "title" and "detail" are tested in the unit tests
        self.assertIn("title", create_work_request_output["error"])
        self.assertIn("detail", create_work_request_output["error"])

    def test_create_work_request_valid(self) -> None:
        """Create a valid work request."""
        self.connect_worker()

        task_data = textwrap.dedent(
            '''\
                result:
                    true
                '''
        )
        self.assert_create_work_request(
            task_data=task_data,
            expected_status="completed",
            expected_result="success",
        )

    def test_debusine_admin_print_permission_error_message(self) -> None:
        """debusine-admin is invoked with wrong user: print error message."""
        # This test is to catch invalid permission
        # (in the logs files for example). Since the test for the secret_key
        # happens before we change the secret_key permissions so it fails
        # later.
        secret_key = "/var/lib/debusine/server/key"
        self.addCleanup(shutil.chown, secret_key, Path(secret_key).owner())
        temp_user = "postgres"
        shutil.chown(secret_key, temp_user)

        result = DebusineServer.execute_command("list_tokens", user=temp_user)

        self.assertEqual(
            "Permission error: Unable to configure handler 'debug.log'. "
            "Check that the user running debusine-admin has access to the"
            " file \"/var/log/debusine/server/debug.log\".\n",
            result.stderr,
        )
        self.assertEqual(result.returncode, 3)

    def assert_create_work_request(
        self, *, task_data: str, expected_status: str, expected_result: str
    ) -> None:
        """
        Submit via debusine client a work request to the server.

        Create work request, assert creation and wait for the work request
        to be completed. Assert expected_status and expected_result.
        """
        create_work_request_output = Client.execute_command(
            'create-work-request', self.TASK_NAME, stdin=task_data
        )

        # Work Request is registered
        self.assertEqual(create_work_request_output['result'], 'success')
        self.assertIn('registered', create_work_request_output['message'])

        work_request_id = create_work_request_output['work_request_id']

        # The client will check the status of the created request
        show_work_request_output = Client.execute_command(
            'show-work-request', work_request_id
        )

        # Task name is as submitted
        self.assertEqual(show_work_request_output['task_name'], self.TASK_NAME)

        # Task data is as submitted
        self.assertEqual(
            show_work_request_output['task_data'], yaml.safe_load(task_data)
        )

        # The worker should get the new work request and start executing it
        Client.wait_for_work_request_completed(work_request_id, expected_result)

        # Assert expected_result and expected_status
        show_work_request_output = Client.execute_command(
            "show-work-request", work_request_id
        )

        self.assertEqual(show_work_request_output["status"], expected_status)
        self.assertEqual(show_work_request_output["result"], expected_result)

    def test_web_login(self) -> None:
        """
        Ensure the web interface is accessible after login.

        Catches unit-tests-escaping issues like
        https://salsa.debian.org/freexian-team/debusine/-/issues/563
        """
        s = requests.session()
        r = s.get(f"{Configuration.get_base_url()}/-/login")
        html = lxml.html.fromstring(r.content)
        csrftoken = html.xpath(
            """//input[@name="csrfmiddlewaretoken"]/@value"""
        )[0]
        with open(os.environ["AUTOPKGTEST_TMP"] + "/test-password.txt") as f:
            password = f.read()[:-1]
        r = s.post(
            f"{Configuration.get_base_url()}/-/login/",
            {
                "username": "test-user",
                "password": password,
                "csrfmiddlewaretoken": csrftoken,
            },
            # Referer required in https
            headers={"Referer": f"{Configuration.get_base_url()}/-/login"},
        )
        html = lxml.html.fromstring(r.content)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            html.xpath("""//ul[@id='navbar-right']/li/a/text()""")[0].strip(),
            "test-user",
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generic integration tests for debusine (no sbuild)',
    )

    parser.add_argument(
        '--log-level',
        help='Minimum log level. Overrides log-level (in [General] section) '
        'from config.ini',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
    )

    debusine_args, unittest_args = parser.parse_known_args()

    logging.basicConfig(level=debusine_args.log_level)

    unittest.main(argv=[sys.argv[0]] + unittest_args)
