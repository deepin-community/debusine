# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Wait task to block until a user provides a signature for an upload."""

from debusine.server.tasks.wait import BaseWaitTask
from debusine.server.tasks.wait.models import (
    ExternalDebsignData,
    ExternalDebsignDynamicData,
)
from debusine.tasks.server import TaskDatabaseInterface


class ExternalDebsign(
    BaseWaitTask[ExternalDebsignData, ExternalDebsignDynamicData]
):
    """Task that requests and waits for an external signature for an upload."""

    TASK_VERSION = 1

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> ExternalDebsignDynamicData:
        """Resolve artifact lookups for this task."""
        return ExternalDebsignDynamicData(
            unsigned_id=task_database.lookup_single_artifact(
                self.data.unsigned
            ).id,
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return [self.dynamic_data.unsigned_id]

    def _execute(self) -> bool:
        """Do nothing, successfully."""
        return True

    def get_label(self) -> str:
        """Return the task label."""
        return f"wait for external debsign for {self.data.unsigned}"
