# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Empty Workflow, used for testing."""

from debusine.server.workflows.base import Workflow
from debusine.server.workflows.models import BaseWorkflowData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class NoopWorkflow(
    Workflow[BaseWorkflowData, BaseDynamicTaskData],
    DefaultDynamicData[BaseWorkflowData],
):
    """Workflow that does nothing."""

    TASK_NAME = "noop"

    def populate(self) -> None:
        """Do nothing."""

    def get_label(self) -> str:
        """Return the task label."""
        return "noop"
