# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used for artifact data."""

import abc
import enum
import urllib.parse
from typing import Any, Literal, cast

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.assets import KeyPurpose


class BaseArtifactDataModel(pydantic.BaseModel):
    """Base pydantic model for bare/artifact data and their components."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    def dict(self, **kwargs: Any) -> dict[str, Any]:
        """Use aliases by default when serializing."""
        kwargs.setdefault("by_alias", True)
        return super().dict(**kwargs)


class BareDataCategory(enum.StrEnum):
    """Possible categories for bare data in collection items."""

    PACKAGE_BUILD_LOG = "debian:package-build-log"
    PROMISE = "debusine:promise"
    HISTORICAL_TASK_RUN = "debusine:historical-task-run"
    TASK_CONFIGURATION = "debusine:task-configuration"

    # Only implemented in tests.
    TEST = "debusine:test"


class DebusinePromise(BaseArtifactDataModel):
    """Pydantic model for debusine promise data."""

    promise_work_request_id: int
    promise_workflow_id: int
    promise_category: str

    class Config(BaseArtifactDataModel.Config):
        """Allow extra fields."""

        extra = pydantic.Extra.allow

    @pydantic.root_validator(pre=True)
    @classmethod
    def check_no_extra_names_starting_promise(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        r"""Forbid extra field names starting with 'promise\\_'."""
        allowed_promise_fields = cls.__fields__.keys()

        for key in values:
            if key.startswith("promise_") and key not in allowed_promise_fields:
                raise ValueError(
                    f"Field name '{key}' starting with "
                    f"'promise_' is not allowed."
                )

        return values


class RuntimeStatistics(BaseArtifactDataModel):
    """Runtime statistics about a completed task."""

    #: Runtime duration of task, in seconds.
    duration: int | None = None

    #: Amount of CPU time used (both user and system), in seconds.
    cpu_time: int | None = None

    #: Maximum disk space used, in bytes.
    disk_space: int | None = None

    #: Maximum RAM used, in bytes.
    memory: int | None = None

    #: Available disk space when the task started, in bytes; may be rounded.
    available_disk_space: int | None = None

    #: Available RAM when the task started, in bytes; may be rounded.
    available_memory: int | None = None

    #: Number of CPU cores on the worker that ran the task.
    cpu_count: int | None = None


def _validate_task_type(value: str) -> str:
    """Ensure value is a valid TASK_TYPE."""
    from debusine.tasks.models import TaskTypes

    # From python 3.13:
    # if value not in TaskTypes:
    try:
        TaskTypes(value)
    except ValueError:
        raise ValueError(f"{value!r} is not a valid task type")

    return value


def _validate_no_colons(value: str | None) -> str | None:
    """
    Check that value, if set, contains no colons.

    This is used, for example, when colons in a value would break the framing
    of the computed CollectionItem name.
    """
    if value is not None and ":" in value:
        raise ValueError(f"{value!r} may not contain ':'")
    return value


class DebusineHistoricalTaskRun(BaseArtifactDataModel):
    """Pydantic model for historical task runs."""

    # Really TaskTypes, but for layering reasons we don't want to import
    # from debusine.tasks here.
    task_type: str
    task_name: str
    subject: str | None = None
    context: str | None = None
    timestamp: int
    work_request_id: int
    # Really WorkRequest.Results, but for layering reasons we don't want to
    # import from debusine.db here.
    result: str
    runtime_statistics: RuntimeStatistics

    _validate_task_type = pydantic.validator("task_type", allow_reuse=True)(
        _validate_task_type
    )


class DebusineTaskConfiguration(BaseArtifactDataModel):
    """Pydantic model for task configuration."""

    # Really TaskTypes, but for layering reasons we don't want to import
    # from debusine.tasks here.
    task_type: str | None = None
    task_name: str | None = None
    subject: str | None = None
    context: str | None = None
    template: str | None = None

    use_templates: list[str] = []
    delete_values: list[str] = []
    default_values: dict[str, Any] = {}
    override_values: dict[str, Any] = {}
    lock_values: list[str] = []
    comment: str = ""

    def name(self) -> str:
        """Return the bare data item name for this task configuration."""
        if self.template is not None:
            return f"template:{self.template}"
        else:
            context = urllib.parse.quote(self.context) if self.context else ''
            return (
                f"{self.task_type}:{self.task_name}"
                f":{self.subject or ''}:{context}"
            )

    @staticmethod
    def get_lookup_names(
        task_type: str,
        task_name: str,
        subject: str | None,
        context: str | None,
    ) -> list[str]:
        """Compute names to look up to get a task's configuration items."""
        names: list[str] = []
        context_encoded = urllib.parse.quote(context) if context else ''
        # Global (subject=None, context=None)
        names.append(f"{task_type}:{task_name}::")
        # Context level (subject=None, context != None)
        if context_encoded:
            names.append(f"{task_type}:{task_name}::{context_encoded}")
        # Subject level (subject != None, context=None)
        if subject:
            names.append(f"{task_type}:{task_name}:{subject}:")
        # Specific-combination level (subject != None, context != None)
        if subject and context_encoded:
            names.append(f"{task_type}:{task_name}:{subject}:{context_encoded}")
        return names

    _validate_task_type = pydantic.validator("task_type", allow_reuse=True)(
        _validate_task_type
    )
    _validate_task_name = pydantic.validator("task_name", allow_reuse=True)(
        _validate_no_colons
    )
    _validate_subject = pydantic.validator("subject", allow_reuse=True)(
        _validate_no_colons
    )
    _validate_template = pydantic.validator("template", allow_reuse=True)(
        _validate_no_colons
    )

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def template_or_task_match(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Ensure we have either template or the other 4 match fields."""
        if values.get("template"):
            present: list[str] = [
                name
                for name in ("task_type", "task_name", "subject", "context")
                if values.get(name)
            ]
            if present:
                raise ValueError(
                    f"{', '.join(present)} should be empty if template is set"
                )
        else:
            missing: list[str] = [
                name
                for name in ("task_type", "task_name")
                if not values.get(name)
            ]
            if missing:
                raise ValueError(
                    f"{', '.join(missing)} should be set for non-template"
                    " task configuration entries"
                )
        return values


class ArtifactCategory(enum.StrEnum):
    """Possible artifact categories."""

    WORK_REQUEST_DEBUG_LOGS = "debusine:work-request-debug-logs"
    PACKAGE_BUILD_LOG = "debian:package-build-log"
    UPLOAD = "debian:upload"
    SOURCE_PACKAGE = "debian:source-package"
    BINARY_PACKAGE = "debian:binary-package"
    BINARY_PACKAGES = "debian:binary-packages"
    LINTIAN = "debian:lintian"
    AUTOPKGTEST = "debian:autopkgtest"
    SYSTEM_TARBALL = "debian:system-tarball"
    BLHC = "debian:blhc"
    DEBDIFF = "debian:debdiff"
    SYSTEM_IMAGE = "debian:system-image"
    SIGNING_INPUT = "debusine:signing-input"
    SIGNING_OUTPUT = "debusine:signing-output"

    # Only implemented in tests.
    TEST = "debusine:test"


class CollectionCategory(enum.StrEnum):
    """Possible collection categories."""

    ENVIRONMENTS = "debian:environments"
    SUITE = "debian:suite"
    SUITE_LINTIAN = "debian:suite-lintian"
    PACKAGE_BUILD_LOGS = "debian:package-build-logs"
    WORKFLOW_INTERNAL = "debusine:workflow-internal"
    TASK_CONFIGURATION = "debusine:task-configuration"
    TASK_HISTORY = "debusine:task-history"

    # Only implemented in tests.
    TEST = "debusine:test"


SINGLETON_COLLECTION_CATEGORIES = {
    CollectionCategory.PACKAGE_BUILD_LOGS,
    CollectionCategory.TASK_HISTORY,
}


class ArtifactData(BaseArtifactDataModel, abc.ABC):
    """
    Base class for artifact data.

    Artifact data is encoded as JSON in the database and in the API, and it is
    modeled as a pydantic data structure in memory for both ease of access and
    validation.
    """

    @abc.abstractmethod
    def get_label(self) -> str | None:
        """
        Return a short human-readable label for the artifact.

        :return: None if no label could be computed from artifact data
        """


class EmptyArtifactData(ArtifactData):
    """Placeholder type for artifacts that have empty data."""

    def get_label(self) -> None:
        """Return a short human-readable label for the artifact."""
        return None


class DoseDistCheckHyphenize(BaseArtifactDataModel):
    """Fix dose3 field convention that uses Python-incompatible hyphens."""

    class Config:
        """Replace all _ by - in fields."""

        alias_generator = lambda x: x.replace("_", "-")  # noqa: E731


class DoseDistCheckBasePkg(BaseArtifactDataModel):
    """Data for dose-distcheck package, common fields."""

    package: str
    version: str
    architecture: str | None = None


class DoseDistCheckDepchainPkg(DoseDistCheckBasePkg):
    """Data for dose-distcheck dependency chain package."""

    depends: str | None = None


class DoseDistCheckDepchain(BaseArtifactDataModel):
    """Data for dose-distcheck dependency chain."""

    depchain: list[DoseDistCheckDepchainPkg]


class DoseDistCheckReasonMissingPkg(
    DoseDistCheckBasePkg, DoseDistCheckHyphenize
):
    """Data for dose-distcheck reason, missing variant, missing package."""

    unsat_dependency: str | None = None


class DoseDistCheckReasonMissingExt(BaseArtifactDataModel):
    """Actual data for dose-distcheck reason, missing variant."""

    pkg: DoseDistCheckReasonMissingPkg
    depchains: list[DoseDistCheckDepchain] = []


class DoseDistCheckReasonMissing(BaseArtifactDataModel):
    """Data for dose-distcheck reason, missing variant."""

    missing: DoseDistCheckReasonMissingExt


class DoseDistCheckReasonConflictPkg(
    DoseDistCheckBasePkg, DoseDistCheckHyphenize
):
    """Data for dose-distcheck reason, conflict variant, conflicting package."""

    unsat_conflict: str | None = None


class DoseDistCheckReasonConflictExt(BaseArtifactDataModel):
    """Actual data for dose-distcheck reason, conflict variant."""

    pkg1: DoseDistCheckReasonConflictPkg
    pkg2: DoseDistCheckReasonConflictPkg
    depchain1: list[DoseDistCheckDepchain] = []
    depchain2: list[DoseDistCheckDepchain] = []


class DoseDistCheckReasonConflict(BaseArtifactDataModel):
    """Data for dose-distcheck reason, conflict variant."""

    conflict: DoseDistCheckReasonConflictExt


class DoseDistCheckPackage(DoseDistCheckBasePkg):
    """Data for dose-distcheck main package."""

    essential: bool | None = None
    type: str | None = None
    source: str | None = None
    status: Literal["broken"] | Literal["ok"]
    success: str | None = None
    reasons: list[DoseDistCheckReasonMissing | DoseDistCheckReasonConflict]


class DoseDistCheck(DoseDistCheckHyphenize):
    """Dose3 output, limited to dose-debcheck as invoked by sbuild."""

    # original spec (<1.0):
    # https://gitlab.com/irill/dose3/-/blob/master/doc/debcheck/proposals/distcheck.yaml
    # proposed fix: https://gitlab.com/irill/dose3/-/issues/19

    output_version: str
    native_architecture: str | None = None
    foreign_architecture: str | None = None
    host_architecture: str | None = None

    report: list[DoseDistCheckPackage]

    total_packages: int
    broken_packages: int
    background_packages: int
    foreground_packages: int


class DebianPackageBuildLog(ArtifactData):
    """Data for debian:package-build-log artifacts."""

    source: str
    version: str
    filename: str
    bd_uninstallable: DoseDistCheck | None = None

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return self.filename.removesuffix(".build")


class DebianUpload(ArtifactData):
    """Data for debian:upload artifacts."""

    type: Literal["dpkg"]
    changes_fields: dict[str, Any]

    def get_label(self) -> str | None:
        """Return a short human-readable label for the artifact."""
        source = self.changes_fields.get("Source")
        version = self.changes_fields.get("Version")
        if source and version:
            return f"{source}_{version}"
        if files := self.changes_fields.get("Files"):
            for file in files:
                if (name := file["name"]).endswith(".changes"):
                    break
            else:
                name = files[0]["name"]
            assert isinstance(name, str)
            return name
        return None

    @staticmethod
    def _changes_architectures(
        changes_fields: dict[str, Any],
    ) -> frozenset[str]:
        return frozenset(changes_fields["Architecture"].split())

    @staticmethod
    def _changes_filenames(changes_fields: dict[str, Any]) -> list[str]:
        return [file["name"] for file in changes_fields["Files"]]

    @pydantic.validator("changes_fields")
    @classmethod
    def metadata_mandatory_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Validate that changes_fields contains Architecture and Files."""
        if "Architecture" not in data:
            raise ValueError("changes_fields must contain Architecture")
        if "Files" not in data:
            raise ValueError("changes_fields must contain Files")
        return data

    @pydantic.validator("changes_fields")
    @classmethod
    def metadata_contains_debs_if_binary(
        cls, data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Validate that binary uploads reference binaries.

        And that source uploads don't contain any.
        """
        archs = cls._changes_architectures(data)

        filenames = cls._changes_filenames(data)
        binaries = [
            file for file in filenames if file.endswith((".deb", ".udeb"))
        ]

        if archs == frozenset({"source"}) and binaries:
            raise ValueError(
                f"Unexpected binary packages {binaries} found in source-only "
                f"upload."
            )
        elif archs - frozenset({"source"}) and not binaries:
            raise ValueError(
                f"No .debs found in {sorted(filenames)} which is expected to "
                f"contain binaries for {', '.join(archs)}"
            )
        return data

    @pydantic.validator("changes_fields")
    @classmethod
    def metadata_contains_dsc_if_source(
        cls, data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Validate that source uploads contain one and only one source package.

        And that binary-only uploads don't contain any.
        """
        archs = cls._changes_architectures(data)

        filenames = cls._changes_filenames(data)
        sources = [file for file in filenames if file.endswith(".dsc")]
        archs = cls._changes_architectures(data)

        if "source" in archs and len(sources) != 1:
            raise ValueError(
                f"Expected to find one and only one source package in source "
                f"upload. Found {sources}."
            )
        elif "source" not in archs and sources:
            raise ValueError(
                f"Binary uploads cannot contain source packages. "
                f"Found: {sources}."
            )
        return data


class DebianSourcePackage(ArtifactData):
    """Data for debian:source-package artifacts."""

    name: str
    version: str
    type: Literal["dpkg"]
    dsc_fields: dict[str, Any]

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return f"{self.name}_{self.version}"


class DebianBinaryPackage(ArtifactData):
    """Data for debian:binary-package artifacts."""

    srcpkg_name: str
    srcpkg_version: str
    deb_fields: dict[str, Any]
    deb_control_files: list[str]

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return "_".join(
            (
                self.deb_fields["Package"],
                self.deb_fields["Version"],
                self.deb_fields["Architecture"],
            )
        )


class DebianBinaryPackages(ArtifactData):
    """Data for debian:binary-packages artifacts."""

    srcpkg_name: str
    srcpkg_version: str
    version: str
    architecture: str
    packages: list[str]

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return f"{self.srcpkg_name}_{self.srcpkg_version}"


class DebianSystemTarball(ArtifactData):
    """Data for debian:system-tarball artifacts."""

    filename: str
    vendor: str
    codename: str
    mirror: pydantic.AnyUrl
    variant: str | None
    pkglist: dict[str, str]
    architecture: str
    with_dev: bool
    with_init: bool

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return self.filename


class DebianSystemImage(DebianSystemTarball):
    """Data for debian:system-image artifacts."""

    image_format: Literal["raw", "qcow2"]
    filesystem: str
    size: int
    boot_mechanism: Literal["efi", "bios"]


class DebianLintianSeverity(enum.StrEnum):
    """Possible values for lintian tag severities."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PEDANTIC = "pedantic"
    EXPERIMENTAL = "experimental"
    OVERRIDDEN = "overridden"
    CLASSIFICATION = "classification"


class DebianLintianSummary(BaseArtifactDataModel):
    """Summary of lintian results."""

    tags_count_by_severity: dict[DebianLintianSeverity, int]
    package_filename: dict[str, str]
    tags_found: list[str]
    overridden_tags_found: list[str]
    lintian_version: str
    distribution: str


class DebianLintian(ArtifactData):
    """Data for debian:lintian artifacts."""

    summary: DebianLintianSummary

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        if self.summary.package_filename:
            return (
                f"lintian: {', '.join(sorted(self.summary.package_filename))}"
            )
        else:
            return "lintian (empty)"


class DebianAutopkgtestResultStatus(enum.StrEnum):
    """Possible values for status."""

    PASS = "PASS"
    FAIL = "FAIL"
    FLAKY = "FLAKY"
    SKIP = "SKIP"


class DebianAutopkgtestResult(BaseArtifactDataModel):
    """A single result for an autopkgtest test."""

    status: DebianAutopkgtestResultStatus
    details: str | None = None


class DebianAutopkgtestSource(BaseArtifactDataModel):
    """The source package for an autopkgtest run."""

    name: str
    version: str
    url: pydantic.AnyUrl


class DebianAutopkgtest(ArtifactData):
    """Data for debian:autopkgtest artifacts."""

    results: dict[str, DebianAutopkgtestResult]
    cmdline: str
    source_package: DebianAutopkgtestSource
    architecture: str
    distribution: str

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return self.source_package.name


class DebusineSigningInput(ArtifactData):
    """Input to a Sign task."""

    trusted_certs: list[str] | None = None
    binary_package_name: str | None = None

    def get_label(self) -> None:
        """Return a short human-readable label for the artifact."""
        return None


class SigningResult(BaseArtifactDataModel):
    """Result of signing a single file."""

    file: str
    output_file: str | None = None
    error_message: str | None = None

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def check_exactly_one_result(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Ensure exactly one of output_file and error_message is present."""
        if (values.get("output_file") is None) == (
            values.get("error_message") is None
        ):
            raise ValueError(
                "Exactly one of output_file and error_message must be set"
            )
        return values


class DebusineSigningOutput(ArtifactData):
    """Output of a Sign task."""

    purpose: KeyPurpose
    fingerprint: str
    results: list[SigningResult]
    binary_package_name: str | None = None

    def get_label(self) -> None:
        """Return a short human-readable label for the artifact."""
        return None


class DebDiff(ArtifactData):
    """Data for debian:debdiff artifacts."""

    original: str
    new: str

    def get_label(self) -> str:
        """Return a short human-readable label for the artifact."""
        return f"debdiff {self.original} {self.new}"


def get_source_package_name(
    artifact_data: (
        DebianSourcePackage
        | DebianUpload
        | DebianBinaryPackage
        | DebianBinaryPackages
        | DebianPackageBuildLog
    ),
) -> str:
    """Retrieve the source name of a package."""
    match artifact_data:
        case DebianSourcePackage():
            return artifact_data.name
        case DebianUpload():
            return cast(str, artifact_data.changes_fields["Source"])
        case DebianBinaryPackage() | DebianBinaryPackages():
            return artifact_data.srcpkg_name
        case DebianPackageBuildLog():
            return artifact_data.source

    raise TypeError(f"Unexpected type: {type(artifact_data).__name__}")


def get_binary_package_name(artifact_data: DebianBinaryPackage) -> str | None:
    """Retrieve the binary package name."""
    match artifact_data:
        case DebianBinaryPackage():
            return artifact_data.deb_fields.get("Package")

    raise TypeError(f"Unexpected type: {type(artifact_data).__name__}")
