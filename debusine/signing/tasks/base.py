# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Base support for tasks that run on signing workers."""

from abc import ABCMeta
from typing import TypeVar

from django.db import close_old_connections, transaction

from debusine.tasks import BaseExternalTask
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData, TaskTypes

TD = TypeVar("TD", bound=BaseTaskData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class BaseSigningTask(BaseExternalTask[TD, DTD], metaclass=ABCMeta):
    """Base class for tasks that run on signing workers."""

    TASK_TYPE = TaskTypes.SIGNING

    def execute(self) -> bool:
        """
        Execute task in a transaction.

        Close database connections before and after running the task to
        avoid problems with long-running worker threads.
        """
        close_old_connections()
        try:
            with transaction.atomic():
                return super().execute()
        finally:
            close_old_connections()
