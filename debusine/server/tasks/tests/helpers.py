# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common test-helper code involving server tasks."""

from typing import TypeVar

from debusine.server.tasks.base import BaseServerTask
from debusine.server.tasks.wait import BaseWaitTask
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData
from debusine.tasks.tests.helper_mixin import SampleBaseTask

TD = TypeVar("TD", bound=BaseTaskData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class SampleBaseServerTask(SampleBaseTask[TD, DTD], BaseServerTask[TD, DTD]):
    """Common test implementation of BaseServerTask methods."""


class SampleBaseWaitTask(SampleBaseTask[TD, DTD], BaseWaitTask[TD, DTD]):
    """Common test implementation of BaseWaitTask methods."""
