# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Base support for wait tasks."""

from abc import ABCMeta
from typing import TypeVar

from debusine.artifacts.models import TaskTypes
from debusine.server.tasks.base import BaseServerTask
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData

TD = TypeVar("TD", bound=BaseTaskData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class BaseWaitTask(BaseServerTask[TD, DTD], metaclass=ABCMeta):
    """Base class for wait tasks."""

    TASK_TYPE = TaskTypes.WAIT
