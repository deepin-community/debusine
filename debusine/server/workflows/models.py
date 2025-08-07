# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Pydantic models used by debusine workflows."""

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from debusine.assets import KeyPurpose

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.server.tasks.models import PackageUploadTarget
from debusine.tasks.models import (
    ActionRetryWithDelays,
    AutopkgtestFailOn,
    AutopkgtestNeedsInternet,
    AutopkgtestTimeout,
    BackendType,
    BaseTaskData,
    ExtraRepository,
    LintianFailOnSeverity,
    LintianOutput,
    LookupMultiple,
    LookupSingle,
    SbuildBinNMU,
    SbuildInput,
    empty_lookup_multiple,
)


class BaseWorkflowData(BaseTaskData):
    """
    Base class for workflow data.

    Workflow data is encoded as JSON in the database, and it is modeled as a
    pydantic data structure in memory for both ease of access and validation.
    """


class WorkRequestManualUnblockAction(StrEnum):
    """An action taken on a review of a manual unblock request."""

    ACCEPT = "accept"
    REJECT = "reject"


class WorkRequestManualUnblockLog(pydantic.BaseModel):
    """A log entry for a review of a manual unblock request."""

    user_id: int
    timestamp: datetime
    notes: str | None = None
    action: WorkRequestManualUnblockAction | None = None


class WorkRequestManualUnblockData(pydantic.BaseModel):
    """Data for a manual unblock request."""

    log: list[WorkRequestManualUnblockLog] = pydantic.Field(
        default_factory=list
    )


class WorkRequestWorkflowData(pydantic.BaseModel):
    """Data structure for WorkRequest.workflow_data."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    #: If the work request fails, if True the workflow can continue, if false
    #: it is interrupted
    allow_failure: bool = pydantic.Field(default=False, allow_mutation=False)

    #: name of the step in the visual representation of the workflow
    display_name: str | None = pydantic.Field(
        default=None, allow_mutation=False
    )

    #: internal identifier used to differentiate multiple workflow callbacks
    #: inside a single workflow. It allows the orchestrator to encode the plan
    #: about what it is supposed to do at this point in the workflow.
    step: str | None = pydantic.Field(default=None, allow_mutation=False)

    #: Name of the group within this workflow containing this work request.
    group: str | None = pydantic.Field(default=None, allow_mutation=False)

    #: For workflows created from a workflow template, the name of that
    #: template, cached here for convenience.
    workflow_template_name: str | None = pydantic.Field(
        default=None, allow_mutation=False
    )

    manual_unblock: WorkRequestManualUnblockData | None = None

    #: Whether this task requires user input.  Only relevant for WAIT tasks.
    needs_input: bool | None = None

    #: The number of times this task has been retried automatically.
    retry_count: int = pydantic.Field(default=0)


class SbuildWorkflowData(BaseWorkflowData):
    """Sbuild workflow data."""

    prefix: str = ""
    input: SbuildInput
    target_distribution: str
    # If AUTO is used, default to BackendType.UNSHARE
    backend: BackendType = BackendType.AUTO
    architectures: list[str] = pydantic.Field(
        min_items=1,
        unique_items=True,
    )
    arch_all_host_architecture: str = "amd64"
    environment_variant: str | None = None
    extra_repositories: list[ExtraRepository] | None = None
    binnmu: SbuildBinNMU | None = None
    build_profiles: list[str] | None = None
    retry_delays: list[str] | None = pydantic.Field(default=None, min_items=1)
    signing_template_names: dict[str, list[str]] = {}

    @pydantic.validator("retry_delays")
    @classmethod
    def validate_retry_delays(
        cls, values: list[str] | None
    ) -> list[str] | None:
        """Check items in `retry_delays` field."""
        for v in values or []:
            if ActionRetryWithDelays._delay_re.match(v) is None:
                raise ValueError(
                    f"Item in retry_delays must be an integer followed by "
                    f"m, h, d, or w; got {v!r}"
                )
        return values


class UpdateEnvironmentsWorkflowTarget(BaseWorkflowData):
    """A target for an update_environments workflow."""

    codenames: str | list[str]
    codename_aliases: dict[str, list[str]] = pydantic.Field(
        default_factory=dict
    )
    variants: str | list[str] = pydantic.Field(default_factory=list)
    backends: str | list[str] = pydantic.Field(default_factory=list)
    architectures: list[str] = pydantic.Field(min_items=1, unique_items=True)
    mmdebstrap_template: dict[str, Any] | None = None
    simplesystemimagebuild_template: dict[str, Any] | None = None


class UpdateEnvironmentsWorkflowData(BaseWorkflowData):
    """update_environments workflow data."""

    vendor: str
    targets: list[UpdateEnvironmentsWorkflowTarget] = pydantic.Field(
        min_items=1
    )


class PackageUploadWorkflowData(BaseWorkflowData):
    """`package_upload` workflow data."""

    prefix: str = ""

    source_artifact: LookupSingle | None
    binary_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )
    merge_uploads: bool = False
    since_version: str | None = None
    target_distribution: str | None = None
    key: str | None = None
    require_signature: bool = True
    target: PackageUploadTarget
    delayed_days: int | None = None
    vendor: str | None = None
    codename: str | None = None


class MakeSignedSourceWorkflowData(BaseWorkflowData):
    """`make_signed_source` workflow data."""

    prefix: str = ""

    binary_artifacts: LookupMultiple
    signing_template_artifacts: LookupMultiple

    vendor: str
    codename: str
    architectures: list[str]
    purpose: KeyPurpose
    key: str
    sbuild_backend: BackendType = BackendType.AUTO


class PiupartsWorkflowData(BaseWorkflowData):
    """`piuparts` workflow data."""

    binary_artifacts: LookupMultiple

    vendor: str
    codename: str
    architectures: list[str] | None = None
    backend: BackendType = BackendType.AUTO
    environment: LookupSingle | None = None
    arch_all_host_architecture: str = "amd64"
    extra_repositories: list[ExtraRepository] | None = None


class AutopkgtestWorkflowData(BaseWorkflowData):
    """`autopkgtest` workflow data."""

    prefix: str = ""

    source_artifact: LookupSingle
    binary_artifacts: LookupMultiple
    context_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )

    vendor: str
    codename: str
    backend: BackendType = BackendType.AUTO
    architectures: list[str] = pydantic.Field(default_factory=list)
    arch_all_host_architecture: str = "amd64"
    extra_repositories: list[ExtraRepository] | None = None

    include_tests: list[str] = pydantic.Field(default_factory=list)
    exclude_tests: list[str] = pydantic.Field(default_factory=list)
    debug_level: int = pydantic.Field(default=0, ge=0, le=3)
    extra_environment: dict[str, str] = pydantic.Field(default_factory=dict)
    needs_internet: AutopkgtestNeedsInternet = AutopkgtestNeedsInternet.RUN
    fail_on: AutopkgtestFailOn = pydantic.Field(
        default_factory=AutopkgtestFailOn
    )
    timeout: AutopkgtestTimeout | None = None


class ReverseDependenciesAutopkgtestWorkflowData(BaseWorkflowData):
    """`reverse_dependencies_autopkgtest` workflow data."""

    source_artifact: LookupSingle
    binary_artifacts: LookupMultiple
    context_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )
    suite_collection: LookupSingle

    vendor: str
    codename: str
    backend: BackendType = BackendType.AUTO
    architectures: list[str] = []
    arch_all_host_architecture: str = "amd64"
    packages_allowlist: list[str] | None = None
    packages_denylist: list[str] = []
    extra_repositories: list[ExtraRepository] | None = None

    debug_level: int = pydantic.Field(default=0, ge=0, le=3)


class LintianWorkflowData(BaseWorkflowData):
    """`lintian` workflow data."""

    source_artifact: LookupSingle
    binary_artifacts: LookupMultiple

    vendor: str
    codename: str
    backend: BackendType = BackendType.UNSHARE

    architectures: list[str] | None = None
    output: LintianOutput = pydantic.Field(default_factory=LintianOutput)

    include_tags: list[str] = pydantic.Field(default_factory=list)
    exclude_tags: list[str] = pydantic.Field(default_factory=list)
    fail_on_severity: LintianFailOnSeverity = LintianFailOnSeverity.NONE


class QAWorkflowData(BaseWorkflowData):
    """`qa` workflow data."""

    source_artifact: LookupSingle
    binary_artifacts: LookupMultiple

    vendor: str
    codename: str
    architectures: list[str] | None = None
    extra_repositories: list[ExtraRepository] | None = None

    architectures_allowlist: list[str] | None = None
    architectures_denylist: list[str] | None = None

    arch_all_host_architecture: str = "amd64"

    enable_autopkgtest: bool = True
    autopkgtest_backend: BackendType = BackendType.AUTO

    enable_reverse_dependencies_autopkgtest: bool = False
    reverse_dependencies_autopkgtest_suite: LookupSingle | None = None

    enable_lintian: bool = True
    lintian_backend: BackendType = BackendType.AUTO
    lintian_fail_on_severity: LintianFailOnSeverity = LintianFailOnSeverity.NONE

    enable_piuparts: bool = True
    piuparts_backend: BackendType = BackendType.AUTO
    piuparts_environment: LookupSingle | None = None

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def check_reverse_dependencies_autopkgtest_consistency(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Check consistency of reverse-dependencies-autopkgtest options."""
        if (
            values.get("enable_reverse_dependencies_autopkgtest")
            and values.get("reverse_dependencies_autopkgtest_suite") is None
        ):
            raise ValueError(
                '"reverse_dependencies_autopkgtest_suite" is required if '
                '"enable_reverse_dependencies_autopkgtest" is set'
            )
        return values


class DebianPipelineWorkflowData(BaseWorkflowData):
    """`debian_pipeline` workflow data."""

    source_artifact: LookupSingle

    vendor: str
    codename: str

    architectures: list[str] | None = None
    architectures_allowlist: list[str] | None = None
    architectures_denylist: list[str] | None = None
    arch_all_host_architecture: str = "amd64"
    extra_repositories: list[ExtraRepository] | None = None

    signing_template_names: dict[str, list[str]] = {}

    sbuild_backend: BackendType = BackendType.AUTO
    sbuild_environment_variant: str | None = None

    enable_autopkgtest: bool = True
    autopkgtest_backend: BackendType = BackendType.AUTO

    enable_reverse_dependencies_autopkgtest: bool = False
    reverse_dependencies_autopkgtest_suite: LookupSingle | None = None

    enable_lintian: bool = True
    lintian_backend: BackendType = BackendType.AUTO
    lintian_fail_on_severity: LintianFailOnSeverity = LintianFailOnSeverity.NONE

    enable_piuparts: bool = True
    piuparts_backend: BackendType = BackendType.AUTO
    piuparts_environment: LookupSingle | None = None

    enable_make_signed_source: bool = False
    make_signed_source_purpose: KeyPurpose | None = None
    make_signed_source_key: str | None = None

    enable_upload: bool = False

    upload_key: str | None = None
    upload_require_signature: bool = True
    upload_include_source: bool = True
    upload_include_binaries: bool = True
    upload_merge_uploads: bool = True
    upload_since_version: str | None = None
    upload_target_distribution: str | None = None
    upload_target: str = (
        "ftp://anonymous@ftp.upload.debian.org/pub/UploadQueue/"
    )
    upload_delayed_days: int | None = None

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def check_reverse_dependencies_autopkgtest_consistency(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Check consistency of reverse-dependencies-autopkgtest options."""
        if (
            values.get("enable_reverse_dependencies_autopkgtest")
            and values.get("reverse_dependencies_autopkgtest_suite") is None
        ):
            raise ValueError(
                '"reverse_dependencies_autopkgtest_suite" is required if '
                '"enable_reverse_dependencies_autopkgtest" is set'
            )
        return values


class PackagePublishWorkflowData(BaseWorkflowData):
    """`package_publish` workflow data."""

    source_artifact: LookupSingle | None = None
    binary_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )
    target_suite: LookupSingle
    unembargo: bool = False
    replace: bool = False
    suite_variables: dict[str, Any] = {}

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def check_one_of_source_or_binary(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Ensure a source or binary artifact is present."""
        if (
            values.get("source_artifact") is None
            and values.get("binary_artifacts") == empty_lookup_multiple()
        ):
            raise ValueError(
                '"source_artifact" or "binary_artifacts" must be set'
            )
        return values


class ExperimentWorkspaceData(BaseWorkflowData):
    """``create_experiment_workspace`` workflow data."""

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
