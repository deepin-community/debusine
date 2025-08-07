# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Signing-side no-operation task."""

from pathlib import Path

from debusine.signing.tasks import BaseSigningTask
from debusine.signing.tasks.models import SigningNoopData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class SigningNoop(
    BaseSigningTask[SigningNoopData, BaseDynamicTaskData],
    DefaultDynamicData[SigningNoopData],
):
    """Task that runs on server-side Celery workers and returns a boolean."""

    TASK_NAME = "noop"
    TASK_VERSION = 1

    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Do nothing."""
        return self.data.result

    def get_label(self) -> str:
        """Return the task label."""
        return "noop"
