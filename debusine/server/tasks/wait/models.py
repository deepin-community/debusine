# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used by debusine wait tasks."""

from datetime import datetime

from debusine.tasks.models import (
    BaseDynamicTaskData,
    BaseTaskData,
    LookupSingle,
)


class DelayData(BaseTaskData):
    """In-memory task data for the Delay task."""

    delay_until: datetime


class ExternalDebsignData(BaseTaskData):
    """In-memory task data for the ExternalDebsign task."""

    unsigned: LookupSingle


class ExternalDebsignDynamicData(BaseDynamicTaskData):
    """Dynamic data for the ExternalDebsign task."""

    unsigned_id: int
