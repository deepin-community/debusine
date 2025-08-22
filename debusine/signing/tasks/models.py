# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used by debusine signing tasks."""

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.assets import KeyPurpose
from debusine.signing.models import SigningMode
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


class SignRepositoryIndexData(BaseTaskData):
    """In-memory task data for the SignRepositoryIndex task."""

    suite_collection: LookupSingle
    unsigned: LookupSingle
    mode: SigningMode
    signed_name: str

    @pydantic.validator("mode")
    @classmethod
    def validate_mode(cls, mode: SigningMode) -> SigningMode:
        """Only allow detached or clear signatures."""
        if mode not in {SigningMode.DETACHED, SigningMode.CLEAR}:
            raise ValueError(
                f"Signing mode must be 'detached' or 'clear'; got '{mode}'"
            )
        return mode

    # With pydantic >= 2.0.2, use `signed_name: str =
    # pydantic.Field(pattern=...)` instead.
    @pydantic.validator("signed_name")
    @classmethod
    def validate_signed_name(cls, signed_name: str) -> str:
        """Require the signed name to be a single path segment."""
        if "/" in signed_name:
            raise ValueError(
                f"Signed name must be a single path segment; got "
                f"{signed_name!r}"
            )
        return signed_name


class SignRepositoryIndexDynamicData(BaseDynamicTaskData):
    """Dynamic data for the SignRepositoryIndex task."""

    signing_keys: list[str]
    unsigned_id: int
