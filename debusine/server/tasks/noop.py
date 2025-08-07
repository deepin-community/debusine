# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Server-side no-operation task."""

from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import ServerNoopData
from debusine.tasks.models import BaseDynamicTaskData
from debusine.tasks.server import TaskDatabaseInterface


class ServerNoop(BaseServerTask[ServerNoopData, BaseDynamicTaskData]):
    """Task that runs on server-side Celery workers and returns a boolean."""

    TASK_VERSION = 1

    def _execute(self) -> bool:
        """Act as specified by the client."""
        if self.data.exception:
            raise RuntimeError("Client requested an exception")
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
