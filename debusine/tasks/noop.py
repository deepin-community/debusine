# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""no operation task. Waits and returns. Used in the integration tests."""

from pathlib import Path

from debusine.tasks import BaseExternalTask
from debusine.tasks.models import BaseDynamicTaskData, NoopData
from debusine.tasks.server import TaskDatabaseInterface


class Noop(BaseExternalTask[NoopData, BaseDynamicTaskData]):
    """
    Task that returns a boolean (execute() depending on the result field).

    Used for integration testing.
    """

    TASK_VERSION = 1

    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Return self.data.result (was sent by the client)."""
        return self.data.result

    def get_label(self) -> str:
        """Return the task label."""
        return "noop"

    def build_dynamic_data(
        self,
        task_database: TaskDatabaseInterface,  # noqa: U100
    ) -> BaseDynamicTaskData:
        """Resolve artifact lookups for this task."""
        return BaseDynamicTaskData()
