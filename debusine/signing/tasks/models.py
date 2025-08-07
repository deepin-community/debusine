# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used by debusine signing tasks."""

from debusine.assets import KeyPurpose
from debusine.tasks.models import (
    BaseDynamicTaskData,
    BaseTaskData,
    LookupMultiple,
    LookupSingle,
)


class SigningNoopData(BaseTaskData):
    """In-memory task data for the SigningNoop task."""

    result: bool = False


class GenerateKeyData(BaseTaskData):
    """In-memory task data for the GenerateKey task."""

    purpose: KeyPurpose
    description: str


class SignData(BaseTaskData):
    """In-memory task data for the Sign task."""

    purpose: KeyPurpose
    unsigned: LookupMultiple
    key: str


class SignDynamicData(BaseDynamicTaskData):
    """Dynamic data for the Sign task."""

    unsigned_ids: list[int]
    unsigned_binary_package_names: list[str | None] = []


class DebsignData(BaseTaskData):
    """In-memory task data for the Debsign task."""

    unsigned: LookupSingle
    key: str


class DebsignDynamicData(BaseDynamicTaskData):
    """Dynamic data for the Debsign task."""

    unsigned_id: int
