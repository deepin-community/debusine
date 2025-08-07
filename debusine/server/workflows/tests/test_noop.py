# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the NoopWorkflow class."""
from django.test import TestCase

from debusine.db.models import WorkRequest, default_workspace
from debusine.server.workflows import NoopWorkflow
from debusine.server.workflows.models import BaseWorkflowData


class NoopWorkflowTests(TestCase):
    """Unit tests for NoopWorkflow class."""

    def test_create(self) -> None:
        """Test instantiating a NoopWorkflow."""
        wr = WorkRequest(task_data={}, workspace=default_workspace())
        wf = NoopWorkflow(wr)
        self.assertEqual(wf.data, BaseWorkflowData())

    def test_label(self) -> None:
        """Test get_label."""
        wr = WorkRequest(task_data={}, workspace=default_workspace())
        wf = NoopWorkflow(wr)
        self.assertEqual(wf.get_label(), "noop")
