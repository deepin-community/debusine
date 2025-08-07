# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the task that provisions cloud compute resources."""

from unittest import mock

from debusine.server.tasks import CloudProvisioning
from debusine.tasks.models import TaskTypes
from debusine.test.django import TestCase


class CloudProvisioningTests(TestCase):
    """Tests for :py:class:`CreateExperimentWorkspace`."""

    def _task(self) -> CloudProvisioning:
        """Create a CloudProvisioning task."""
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.SERVER, task_name="cloud_provisioning"
        )
        task = work_request.get_task()
        self.assertIsInstance(task, CloudProvisioning)
        assert isinstance(task, CloudProvisioning)
        task.set_work_request(work_request)
        return task

    def test_get_label(self) -> None:
        """Test get_label."""
        self.assertEqual(
            self._task().get_label(), "provisioning of cloud instances"
        )

    def test_call_provision(self) -> None:
        """The task only calls the provision function."""
        task = self._task()
        with mock.patch(
            "debusine.server.tasks.cloud_provisioning.provision"
        ) as p:
            task.execute()
        p.assert_called()
