# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for Debusine dput-ng hooks."""

import logging
from unittest import mock

from dput.changes import Changes
from dput.exceptions import HookException

from debusine.client.dput_ng.hooks import (
    NoWorkflowError,
    check_workflow,
    choose_workflow,
    create_workflow,
)
from debusine.client.dput_ng.tests.test_utils import (
    FakeInterface,
    load_local_profile,
    mock_client_config,
)
from debusine.client.exceptions import DebusineError
from debusine.client.models import CreateWorkflowRequest
from debusine.test import TestCase
from debusine.test.test_utils import create_work_request_response


class TestChooseWorkflow(TestCase):
    """Test :py:func:`choose_workflow`."""

    def test_override(self) -> None:
        changes = Changes(string="Distribution: unstable\n")
        profile = {
            "debusine_workflows_by_distribution": {
                "unstable": "upload-to-unstable"
            },
            "debusine_workflow": "overridden",
        }

        self.assertEqual(choose_workflow(changes, profile), "overridden")

    def test_select_from_changes(self) -> None:
        changes = Changes(string="Distribution: unstable\n")
        profile = {
            "debusine_workflows_by_distribution": {
                "experimental": "upload-to-experimental",
                "unstable": "upload-to-unstable",
            }
        }

        self.assertEqual(
            choose_workflow(changes, profile), "upload-to-unstable"
        )

    def test_unknown(self) -> None:
        changes = Changes(string="Distribution: oldstable\n")
        profile = {
            "debusine_workflows_by_distribution": {
                "unstable": "upload-to-unstable"
            }
        }

        with self.assertRaisesRegex(
            NoWorkflowError,
            "No debusine workflow is configured for 'oldstable'",
        ):
            choose_workflow(changes, profile)


class TestCheckWorkflow(TestCase):
    """Test the pre-upload hook to check that we have a workflow."""

    def test_success(self) -> None:
        changes = Changes(string="Distribution: unstable\n")
        profile = {
            "debusine_workflows_by_distribution": {
                "unstable": "upload-to-unstable"
            }
        }

        check_workflow(changes, profile, FakeInterface())

    def test_failure(self) -> None:
        changes = Changes(string="Distribution: unknown\n")
        profile = {
            "debusine_workflows_by_distribution": {
                "unstable": "upload-to-unstable"
            }
        }

        with self.assertRaisesRegex(
            NoWorkflowError,
            "No debusine workflow is configured for 'unknown'",
        ) as raised:
            check_workflow(changes, profile, FakeInterface())
        self.assertIsInstance(raised.exception, HookException)


class TestCreateWorkflow(TestCase):
    """Test the post-upload hook to create a workflow."""

    def test_success(self) -> None:
        changes = Changes(string="Distribution: unstable\n")
        profile = load_local_profile("debusine.debian.net")
        profile["debusine_workspace"] = "base"
        profile["debusine_artifact_ids"] = {"changes": 42}

        with (
            mock_client_config(
                "https://debusine.example.net/api", "some-token"
            ),
            mock.patch(
                "debusine.client.debusine.Debusine.workflow_create",
                return_value=create_work_request_response(id=123),
            ) as mock_workflow_create,
            self.assertLogsContains(
                "Created workflow: "
                "https://debusine.example.net/debian/base/work-request/123/",
                logger="dput",
                level=logging.INFO,
            ),
        ):
            create_workflow(changes, profile, FakeInterface())

        mock_workflow_create.assert_called_once_with(
            CreateWorkflowRequest(
                template_name="upload-to-unstable",
                workspace="base",
                task_data={"source_artifact": 42},
            )
        )

    def test_no_workflow(self) -> None:
        changes = Changes(string="Distribution: unknown\n")
        profile = load_local_profile("debusine.debian.net")

        with mock.patch(
            "debusine.client.debusine.Debusine.workflow_create"
        ) as mock_workflow_create:
            create_workflow(changes, profile, FakeInterface())

        mock_workflow_create.assert_not_called()

    def test_non_default_workflow_data(self) -> None:
        changes = Changes(string="Distribution: unstable\n")
        profile = load_local_profile("debusine.debian.net")
        profile["debusine_workspace"] = "base"
        profile["debusine_artifact_ids"] = {"changes": 42}
        profile["debusine_workflow_data"]["enable_piuparts"] = False

        with (
            mock_client_config(
                "https://debusine.example.net/api", "some-token"
            ),
            mock.patch(
                "debusine.client.debusine.Debusine.workflow_create",
                return_value=create_work_request_response(id=123),
            ) as mock_workflow_create,
            self.assertLogsContains(
                "Created workflow: "
                "https://debusine.example.net/debian/base/work-request/123/",
                logger="dput",
                level=logging.INFO,
            ),
        ):
            create_workflow(changes, profile, FakeInterface())

        mock_workflow_create.assert_called_once_with(
            CreateWorkflowRequest(
                template_name="upload-to-unstable",
                workspace="base",
                task_data={"source_artifact": 42, "enable_piuparts": False},
            )
        )

    def test_workflow_create_error(self) -> None:
        """
        Exceptions from ``workflow_create`` are wrapped in a HookException.

        This is handled cleanly by the ``dput`` command line.
        """
        changes = Changes(string="Distribution: unstable\n")
        profile = load_local_profile("debusine.debian.net")
        profile["debusine_workspace"] = "base"
        profile["debusine_artifact_ids"] = {"changes": 42}
        error = DebusineError(
            {
                "title": "Something went wrong",
                "detail": "Something went badly wrong",
            }
        )

        with (
            mock_client_config(
                "https://debusine.example.net/api", "some-token"
            ),
            mock.patch(
                "debusine.client.debusine.Debusine.workflow_create",
                side_effect=error,
            ),
            self.assertRaises(HookException) as raised,
        ):
            create_workflow(changes, profile, FakeInterface())

        self.assertIsInstance(raised.exception, HookException)
        self.assertEqual(str(raised.exception), str(error))
        self.assertEqual(raised.exception.__cause__, error)
