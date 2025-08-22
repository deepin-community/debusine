# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used by debusine server-side tasks."""

import re
from datetime import datetime
from typing import Any

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.tasks.models import (
    BaseDynamicTaskData,
    BaseTaskData,
    LookupMultiple,
    LookupSingle,
)


class ServerNoopData(BaseTaskData):
    """In-memory task data for the ServerNoop task."""

    exception: bool = False
    result: bool = False


class APTMirrorData(BaseTaskData):
    """In-memory task data for the APTMirror task."""

    collection: str
    url: pydantic.AnyUrl
    suite: str
    components: list[str] | None = None
    # TODO: This should ideally be optional, but discovery is harder than it
    # ought to be.  See https://bugs.debian.org/848194#49.
    architectures: list[str]
    signing_key: str | None = None

    @pydantic.root_validator
    @classmethod
    def check_suite_components_consistency(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Components are only allowed/required for non-flat repositories."""
        if values["suite"].endswith("/"):
            if values.get("components") is not None:
                raise ValueError(
                    'Flat repositories (where suite ends with "/") must not '
                    'have components'
                )
        else:
            if values.get("components") is None:
                raise ValueError(
                    'Non-flat repositories (where suite does not end with '
                    '"/") must have components'
                )
        return values


class UpdateDerivedCollectionData(BaseTaskData):
    """In-memory task data for the UpdateDerivedCollection task."""

    base_collection: LookupSingle
    derived_collection: LookupSingle
    child_task_data: dict[str, Any] | None = None
    force: bool = False


class UpdateSuiteLintianCollectionData(UpdateDerivedCollectionData):
    """In-memory task data for the UpdateSuiteLintianCollection task."""


class PackageUploadInput(BaseTaskData):
    """Input for the PackageUpload task."""

    upload: LookupSingle


class PackageUploadTarget(pydantic.AnyUrl):
    """
    Target URL for the PackageUpload task.

    While this may contain a user and password, note that these are not
    currently secret.
    """

    allowed_schemes = {"ftp", "sftp"}


class PackageUploadData(BaseTaskData):
    """In-memory task data for the PackageUpload task."""

    input: PackageUploadInput
    target: PackageUploadTarget
    delayed_days: int | None = None


class PackageUploadDynamicData(BaseDynamicTaskData):
    """Dynamic data for the PackageUpload task."""

    input_upload_id: int


class CopyCollectionItemsCopies(BaseTaskData):
    """Specification of a set of copies for the CopyCollectionItems task."""

    source_items: LookupMultiple
    target_collection: LookupSingle
    unembargo: bool = False
    replace: bool = False
    name_template: str | None = None
    variables: dict[str, Any] | None = None


class CopyCollectionItemsData(BaseTaskData):
    """In-memory task data for the CopyCollectionItems task."""

    copies: list[CopyCollectionItemsCopies] = pydantic.Field(min_items=1)


class CreateExperimentWorkspaceData(BaseTaskData):
    """In-memory task data for CreateExperimentWorkspace."""

    experiment_name: str
    public: bool = True
    owner_group: str | None = None
    workflow_template_names: list[str] = []
    expiration_delay: int | None = 60

    @pydantic.validator("experiment_name")
    @classmethod
    def validate_experiment_name(cls, data: str) -> str:
        """
        Validate the experiment name.

        Valid characters are the sames as workspace/group names, minus the '-'
        """
        # In pydantic 1.10 this could be done with constr, but mypy currently
        # complains about their syntax
        if not re.match(r"^[A-Za-z][A-Za-z0-9+._]*$", data):
            raise ValueError("experiment name contains invalid characters")
        return data


class GenerateSuiteIndexesData(BaseTaskData):
    """In-memory task data for GenerateSuiteIndexes."""

    suite_collection: LookupSingle
    generate_at: datetime


class GenerateSuiteIndexesDynamicData(BaseDynamicTaskData):
    """Dynamic data for the GenerateSuiteIndexes task."""

    suite_collection_id: int
