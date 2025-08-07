# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Wait task to delay until a given time is reached."""

from debusine.server.tasks.wait import BaseWaitTask
from debusine.server.tasks.wait.models import DelayData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class Delay(
    BaseWaitTask[DelayData, BaseDynamicTaskData], DefaultDynamicData[DelayData]
):
    """Task that delays until a given time is reached."""

    TASK_VERSION = 1

    def _execute(self) -> bool:
        """Never called; the scheduler completes these tasks directly."""
        raise NotImplementedError()

    def get_label(self) -> str:
        """Return the task label."""
        return f"delay until {self.data.delay_until}"
