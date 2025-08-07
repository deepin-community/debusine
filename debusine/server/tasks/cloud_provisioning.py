# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Task to orchestrate provisioning of cloud workers.

See docs/reference/devel-blueprints/dynamic-compute.rst
"""

from debusine.server.provisioning import provision
from debusine.server.tasks import BaseServerTask
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData


class CloudProvisioning(
    BaseServerTask[BaseTaskData, BaseDynamicTaskData],
    DefaultDynamicData[BaseTaskData],
):
    """Task that provisions/tears down cloud instances."""

    TASK_VERSION = 1
    TASK_NAME = "cloud_provisioning"

    def _execute(self) -> bool:
        """Execute the task."""
        assert self.work_request is not None

        provision()

        return True

    def get_label(self) -> str:
        """Return the task label."""
        return "provisioning of cloud instances"
