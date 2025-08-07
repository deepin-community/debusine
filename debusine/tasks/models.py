# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used by debusine tasks."""

import re
from collections.abc import Iterator
from datetime import datetime
from enum import Enum, StrEnum, auto
from itertools import groupby
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias, Union

import debian.deb822 as deb822

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

import yaml

from debusine.artifacts.models import BareDataCategory, RuntimeStatistics
from debusine.client.models import LookupChildType
from debusine.utils import DjangoChoicesEnum


class TaskTypes(DjangoChoicesEnum):
    """Possible values for task_type."""

    WORKER = "Worker"
    SERVER = "Server"
    INTERNAL = "Internal"
    WORKFLOW = "Workflow"
    SIGNING = "Signing"
    WAIT = "Wait"


class WorkerType(DjangoChoicesEnum):
    """The type of a Worker."""

    EXTERNAL = "external"
    CELERY = "celery"
    SIGNING = "signing"


class BackendType(StrEnum):
    """Possible values for backend."""

    AUTO = "auto"
    UNSHARE = "unshare"
    INCUS_LXC = "incus-lxc"
    INCUS_VM = "incus-vm"
    QEMU = "qemu"


class AutopkgtestNeedsInternet(StrEnum):
    """Possible values for needs_internet."""

    RUN = "run"
    TRY = "try"
    SKIP = "skip"


class LintianPackageType(Enum):
    """Possible package types."""

    SOURCE = auto()
    BINARY_ALL = auto()
    BINARY_ANY = auto()


class LintianFailOnSeverity(StrEnum):
    """Possible values for fail_on_severity."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PEDANTIC = "pedantic"
    EXPERIMENTAL = "experimental"
    OVERRIDDEN = "overridden"
    NONE = "none"


class BaseTaskDataModel(pydantic.BaseModel):
    """Stricter pydantic defaults for task data models."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    def dict(self, **kwargs: Any) -> dict[str, Any]:
        """Use aliases by default when serializing."""
        kwargs.setdefault("by_alias", True)
        return super().dict(**kwargs)


# Lookups resulting in exactly one collection item.
LookupSingle: TypeAlias = int | str


def build_lookup_string_segments(*segments: str) -> str:
    """Build a string lookup from segments."""
    return "/".join(segments)


def parse_lookup_string_segments(lookup: str) -> list[str]:
    """Parse a string lookup into segments."""
    return lookup.split("/")


def build_key_value_lookup_segment(
    lookup_type: str, filters: dict[str, str]
) -> str:
    """Build a lookup segment consisting of a series of `key=value` filters."""
    filters_string = ":".join(
        f"{key}={value}" for key, value in filters.items()
    )
    return f"{lookup_type}:{filters_string}"


def parse_key_value_lookup_segment(segment: str) -> tuple[str, dict[str, str]]:
    """Parse a lookup segment consisting of a series of `key=value` filters."""
    # TODO: This is currently only used by the debian:environments
    # collection.  If we generalize this, then we should consider whether we
    # need to add some kind of quoting capability.
    lookup_type, *filters_segments = segment.split(":")
    return lookup_type, dict(item.split("=", 1) for item in filters_segments)


class CollectionItemMatcherKind(StrEnum):
    """Possible values for CollectionItemMatcher.kind."""

    CONTAINS = "contains"
    ENDSWITH = "endswith"
    EXACT = "exact"
    STARTSWITH = "startswith"


class CollectionItemMatcher(BaseTaskDataModel):
    """A matcher for collection item name or per-item data fields."""

    kind: CollectionItemMatcherKind
    value: Any


class ExtraRepository(BaseTaskDataModel):
    """extra_repositories for Debian tasks."""

    url: pydantic.AnyUrl
    suite: str
    components: list[str] | None = None
    signing_key: str | None = None

    @pydantic.validator("suite")
    @classmethod
    def suite_regex(cls, suite: str) -> str:
        """Ensure that suite matches a regex."""
        # Replace with an Annotated[str] in pydantic 2
        if not re.match(r"^(\w|[./ -])+$", suite):
            raise ValueError(f"Invalid suite {suite}, must match (\\w|[./ -])+")
        return suite

    @pydantic.validator("components")
    @classmethod
    def components_regex(cls, components: list[str] | None) -> list[str] | None:
        """Ensure that suite matches a regex."""
        if not components:
            return None
        # Replace with an Annotated[str] in pydantic 2
        for component in components:
            if not re.match(r"^(\w|-)+$", component):
                raise ValueError(
                    f"Invalid component {component}, must match (\\w|-)+"
                )
        return components

    @pydantic.root_validator
    @classmethod
    def flat_repository(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Check if suite is a flat repository."""
        suite = values.get("suite")
        components = values.get("components")
        if suite and suite.endswith("/"):
            if components is not None:
                raise ValueError(
                    "Components cannot be specified for a flat "
                    "repository (where the suite ends with /)"
                )
        elif not components:
            raise ValueError("Components must be specified")
        return values

    def as_oneline_source(self, signed_by_filename: str | None = None) -> str:
        """Render a one-line sources.list entry."""
        parts = ["deb"]
        if self.signing_key:
            if not signed_by_filename:
                raise ValueError("No signed_by_filename specified")
            parts.append(f"[signed-by={signed_by_filename}]")
        parts.append(self.url)
        parts.append(self.suite)
        if self.components:
            parts += self.components
        return ' '.join(parts)

    def as_deb822_source(self, signed_by_filename: str | None = None) -> str:
        """Render a Deb822 sources.list.d entry."""
        source = deb822.Deb822()
        source["Types"] = "deb"
        source["URIs"] = self.url
        source["Suites"] = self.suite
        if self.components:
            source["Components"] = " ".join(self.components)
        if self.signing_key:
            if signed_by_filename:
                source["Signed-By"] = signed_by_filename
            else:
                source["Signed-By"] = "\n" + "\n".join(
                    " " + (line or ".")
                    for line in self.signing_key.splitlines()
                )
        return source.dump()


class LookupDict(BaseTaskDataModel):
    """Dictionary lookups for collection items."""

    class Config(BaseTaskDataModel.Config):
        frozen = True

    collection: LookupSingle
    child_type: LookupChildType = LookupChildType.ARTIFACT
    category: str | None = None
    name_matcher: CollectionItemMatcher | None = None
    # Logically a mapping, but we turn it into a tuple so that lookups are
    # hashable.
    data_matchers: tuple[tuple[str, CollectionItemMatcher], ...] = ()
    # Logically a mapping, but we turn it into a tuple so that lookups are
    # hashable.  In future some filters may want other value types, but they
    # need to be hashable and our current use cases involve subordinate
    # lookups, so just declare them to be lookups for now.
    lookup_filters: tuple[
        tuple[str, Union[LookupSingle, "LookupMultiple"]], ...
    ] = ()

    @pydantic.root_validator(pre=True, allow_reuse=True)
    @classmethod
    def normalize_matchers(cls, values: dict[str, Any]) -> dict[str, Any]:
        """
        Transform the lookup syntax into a form more convenient for pydantic.

        `name`, `name__*`, `data__KEY`, and `data__KEY__*` keys in `values`
        are transformed into :py:class:`CollectionItemMatcher` instances
        stored in `name_matcher` and `data_matchers`.  `lookup__KEY` keys
        are transformed into entries in `lookup_filters`.  Conflicting
        lookup suffixes are rejected.
        """

        def split_words(s: str) -> list[str]:
            return s.split("__")

        def matcher_prefix(s: str) -> tuple[str, ...]:
            match split_words(s):
                case ["name", *_]:
                    return ("name",)
                case ["data", data_key, *_]:
                    return ("data", data_key)
                case ["lookup", lookup_key]:
                    return ("lookup", lookup_key)
                case _:
                    return ()

        data_matchers: dict[str, CollectionItemMatcher] = dict(
            values.get("data_matchers", ())
        )
        lookup_filters: dict[str, LookupSingle | LookupMultiple] = dict(
            values.get("lookup_filters", ())
        )

        for key, group in groupby(
            sorted(values, key=matcher_prefix), matcher_prefix
        ):
            if not key:
                # Only keys with `name`, `data`, or `lookup` as their first
                # segment need transformation.
                continue
            matcher_names = list(group)
            if len(matcher_names) > 1:
                raise ValueError(
                    f"Conflicting matchers: {sorted(matcher_names)}"
                )
            assert len(matcher_names) == 1
            matcher_name = matcher_names[0]

            data_key: str | None = None
            match (words := split_words(matcher_name)):
                case ["name"]:
                    kind = "exact"
                case ["name", kind] if kind != "exact":
                    pass
                case ["data", data_key]:
                    kind = "exact"
                case ["data", data_key, kind] if kind != "exact":
                    pass
                case ["lookup", lookup_key]:
                    lookup_filters[lookup_key] = pydantic.parse_obj_as(
                        LookupSingle | LookupMultiple,  # type: ignore[arg-type]
                        values[matcher_name],
                    )
                    for k in matcher_names:
                        del values[k]
                    continue
                case _:
                    # We filter out keys with first segments other than
                    # `name`, `data`, or `lookup` earlier, so anything that
                    # reaches here must be a malformed lookup.
                    raise ValueError(f"Unrecognized matcher: {matcher_name}")

            matcher = CollectionItemMatcher(
                kind=CollectionItemMatcherKind(kind), value=values[matcher_name]
            )
            if words[0] == "name":
                values["name_matcher"] = matcher
            else:
                assert words[0] == "data"
                assert data_key is not None
                data_matchers[data_key] = matcher
            for k in matcher_names:
                del values[k]

        if data_matchers:
            values["data_matchers"] = tuple(sorted(data_matchers.items()))
        if lookup_filters:
            values["lookup_filters"] = tuple(sorted(lookup_filters.items()))

        return values

    def export(self) -> dict[str, Any]:
        """
        Export the usual input representation of this lookup.

        This reverses the transformations applied by
        :py:meth:`normalize_matchers`.
        """
        value: dict[str, Any] = {"collection": self.collection}
        if self.child_type != LookupChildType.ARTIFACT:
            value["child_type"] = str(self.child_type)
        if self.category is not None:
            value["category"] = self.category
        if self.name_matcher is not None:
            if self.name_matcher.kind == CollectionItemMatcherKind.EXACT:
                value["name"] = self.name_matcher.value
            else:
                value[f"name__{self.name_matcher.kind}"] = (
                    self.name_matcher.value
                )
        for key, matcher in self.data_matchers:
            if matcher.kind == CollectionItemMatcherKind.EXACT:
                value[f"data__{key}"] = matcher.value
            else:
                value[f"data__{key}__{matcher.kind}"] = matcher.value
        for key, filter_value in self.lookup_filters:
            if isinstance(filter_value, LookupMultiple):
                value[f"lookup__{key}"] = filter_value.export()
            else:
                value[f"lookup__{key}"] = filter_value
        return value


class LookupMultiple(BaseTaskDataModel):
    """Lookups resulting in multiple collection items."""

    class Config(BaseTaskDataModel.Config):
        frozen = True

    # LookupMultiple parses a list instead of an object/dict; we turn this
    # into a tuple so that lookups are hashable
    __root__: tuple[LookupSingle | LookupDict, ...]

    @pydantic.validator("__root__", pre=True, allow_reuse=True)
    @classmethod
    def normalize(cls, values: Any) -> tuple[Any, ...]:
        """Normalize into a list of multiple matchers."""
        if isinstance(values, dict):
            return (values,)
        elif isinstance(values, (tuple, list)):
            return tuple(values)
        else:
            raise ValueError(
                "Lookup of multiple collection items must be a dictionary or "
                "a list"
            )

    def __iter__(  # type: ignore[override]
        self,
    ) -> Iterator[LookupSingle | LookupDict]:
        """Iterate over individual lookups."""
        return iter(self.__root__)

    def __bool__(self) -> bool:
        """Return True if and only if this lookup is non-empty."""
        return bool(self.__root__)

    def export(self) -> dict[str, Any] | list[int | str | dict[str, Any]]:
        """
        Export the usual input representation of this lookup.

        This reverses the transformations applied by :py:meth:`normalize`.
        """
        if len(self.__root__) == 1 and isinstance(self.__root__[0], LookupDict):
            return self.__root__[0].export()
        else:
            return [
                lookup.export() if isinstance(lookup, LookupDict) else lookup
                for lookup in self
            ]


# Resolve circular reference to LookupMultiple.
LookupDict.update_forward_refs()


class NotificationDataEmail(BaseTaskDataModel):
    """Channel data for email notifications."""

    from_: pydantic.EmailStr | None = pydantic.Field(default=None, alias="from")
    to: list[pydantic.EmailStr] | None
    cc: list[pydantic.EmailStr] = pydantic.Field(default_factory=list)
    subject: str | None = None


class ActionTypes(StrEnum):
    """Possible values for EventReaction actions."""

    SEND_NOTIFICATION = "send-notification"
    UPDATE_COLLECTION_WITH_ARTIFACTS = "update-collection-with-artifacts"
    UPDATE_COLLECTION_WITH_DATA = "update-collection-with-data"
    RETRY_WITH_DELAYS = "retry-with-delays"
    RECORD_IN_TASK_HISTORY = "record-in-task-history"


class ActionSendNotification(BaseTaskDataModel):
    """Action for sending a notification."""

    action: Literal[ActionTypes.SEND_NOTIFICATION] = (
        ActionTypes.SEND_NOTIFICATION
    )
    channel: str
    data: NotificationDataEmail | None = None


class ActionUpdateCollectionWithArtifacts(BaseTaskDataModel):
    """Action for updating a collection with artifacts."""

    action: Literal[ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS] = (
        ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS
    )
    collection: LookupSingle
    name_template: str | None = None
    variables: dict[str, Any] | None = None
    artifact_filters: dict[str, Any]


class ActionUpdateCollectionWithData(BaseTaskDataModel):
    """Action for updating a collection with bare data."""

    action: Literal[ActionTypes.UPDATE_COLLECTION_WITH_DATA] = (
        ActionTypes.UPDATE_COLLECTION_WITH_DATA
    )
    collection: LookupSingle
    category: BareDataCategory
    name_template: str | None = None
    data: dict[str, Any] | None = None


class ActionRetryWithDelays(BaseTaskDataModel):
    """Action for retrying a work request with delays."""

    _delay_re = re.compile(r"^([0-9]+)([mhdw])$")

    action: Literal[ActionTypes.RETRY_WITH_DELAYS] = (
        ActionTypes.RETRY_WITH_DELAYS
    )
    delays: list[str] = pydantic.Field(min_items=1)

    @pydantic.validator("delays")
    @classmethod
    def validate_delays(cls, values: list[str]) -> list[str]:
        """Check items in `delays` field."""
        for v in values:
            if cls._delay_re.match(v) is None:
                raise ValueError(
                    f"Item in delays must be an integer followed by "
                    f"m, h, d, or w; got {v!r}"
                )
        return values


class ActionRecordInTaskHistory(BaseTaskDataModel):
    """Action for recording the task run in a task-history collection."""

    action: Literal[ActionTypes.RECORD_IN_TASK_HISTORY] = (
        ActionTypes.RECORD_IN_TASK_HISTORY
    )
    subject: str | None = None
    context: str | None = None


EventReaction = Annotated[
    Union[
        ActionSendNotification,
        ActionUpdateCollectionWithArtifacts,
        ActionUpdateCollectionWithData,
        ActionRetryWithDelays,
        ActionRecordInTaskHistory,
    ],
    pydantic.Field(discriminator="action"),
]


class EventReactions(BaseTaskDataModel):
    """Structure for event reactions."""

    on_creation: list[EventReaction] = []
    on_unblock: list[EventReaction] = []
    on_success: list[EventReaction] = []
    on_failure: list[EventReaction] = []


class BaseTaskData(BaseTaskDataModel):
    """
    Base class for task data.

    Task data is encoded as JSON in the database and in the API, and it is
    modeled as a pydantic data structure in memory for both ease of access and
    validation.
    """

    #: debusine:task-configuration collection to use to configure the task
    task_configuration: LookupSingle | None = None


class BaseTaskDataWithExecutor(BaseTaskData):
    """Base task data with fields used to configure executors."""

    backend: BackendType = BackendType.AUTO
    environment: LookupSingle | None = None


class BaseTaskDataWithExtraRepositories(BaseTaskData):
    """Base task data with fields used to configure extra_repositories."""

    extra_repositories: list[ExtraRepository] | None = None


class BaseDynamicTaskData(BaseTaskDataModel):
    """
    Base class for dynamic task data.

    This is computed by the scheduler when dispatching a task to a worker.
    It may involve resolving artifact lookups.
    """

    #: Brief human-readable summary of the most important parameters to this
    #: work request.
    parameter_summary: str | None = None

    #: An abstract string value representing the subject of the task, for
    #: the purpose of recording statistics.  It is meant to group possible
    #: inputs into groups that we expect to behave similarly.
    subject: str | None = None

    #: An abstract string value representing the runtime context in which
    #: the task is executed, for the purpose of recording statistics.  It is
    #: meant to represent some of the task parameters that can significantly
    #: alter the runtime behaviour of the task.
    runtime_context: str | None = None

    #: Result of the collection lookup of the task_configuration field
    task_configuration_id: int | None = None

    #: Name of the configuration context
    # This is a stable string representation of the main input artifacts and
    # parameters, that does not change across similar future invocations of the
    # same task, and is used to look up configuration parameters.
    configuration_context: str | None = None


class BaseDynamicTaskDataWithExecutor(BaseDynamicTaskData):
    """Dynamic task data for executors."""

    environment_id: int | None = None


class OutputDataError(BaseTaskDataModel):
    """An error encountered when running a task."""

    message: str
    code: str


class OutputData(BaseTaskDataModel):
    """Data produced when a task is completed."""

    runtime_statistics: RuntimeStatistics | None = None
    errors: list[OutputDataError] | None = None


class NoopData(BaseTaskData):
    """In memory task data for the Noop task."""

    result: bool = True


def empty_lookup_multiple() -> LookupMultiple:
    """Return an empty :py:class:`LookupMultiple`."""
    return LookupMultiple.parse_obj(())


class AutopkgtestInput(BaseTaskDataModel):
    """Input for an autopkgtest task."""

    source_artifact: LookupSingle
    binary_artifacts: LookupMultiple
    context_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )


class AutopkgtestFailOn(BaseTaskDataModel):
    """Possible values for fail_on."""

    failed_test: bool = True
    flaky_test: bool = False
    skipped_test: bool = False


class AutopkgtestTimeout(BaseTaskDataModel):
    """Timeout specifications for an autopkgtest task."""

    global_: int | None = pydantic.Field(alias="global", ge=0)
    factor: int | None = pydantic.Field(ge=0)
    short: int | None = pydantic.Field(ge=0)
    install: int | None = pydantic.Field(ge=0)
    test: int | None = pydantic.Field(ge=0)
    copy_: int | None = pydantic.Field(alias="copy", ge=0)


class AutopkgtestData(
    BaseTaskDataWithExecutor, BaseTaskDataWithExtraRepositories
):
    """In memory task data for the Autopkgtest task."""

    input: AutopkgtestInput
    host_architecture: str
    include_tests: list[str] = pydantic.Field(default_factory=list)
    exclude_tests: list[str] = pydantic.Field(default_factory=list)
    debug_level: int = pydantic.Field(default=0, ge=0, le=3)
    use_packages_from_base_repository: bool = False
    extra_environment: dict[str, str] = pydantic.Field(default_factory=dict)
    needs_internet: AutopkgtestNeedsInternet = AutopkgtestNeedsInternet.RUN
    fail_on: AutopkgtestFailOn = pydantic.Field(
        default_factory=AutopkgtestFailOn
    )
    timeout: AutopkgtestTimeout | None = None
    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle


class AutopkgtestDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the Autopkgtest task."""

    # BaseDynamicTaskDataWithExecutor declares this as optional, but it's
    # required here.
    environment_id: int

    input_source_artifact_id: int
    input_binary_artifacts_ids: list[int]
    input_context_artifacts_ids: list[int] = pydantic.Field(
        default_factory=list
    )


class LintianInput(BaseTaskDataModel):
    """Input for a lintian task."""

    source_artifact: LookupSingle | None = None
    binary_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def check_one_of_source_or_binary(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Ensure a source or binary artifact is present."""
        if values.get("source_artifact") is None and not values.get(
            "binary_artifacts"
        ):
            raise ValueError(
                'One of source_artifact or binary_artifacts must be set'
            )
        return values


class LintianOutput(BaseTaskDataModel):
    """Output configuration for a Lintian task."""

    source_analysis: bool = True
    binary_all_analysis: bool = True
    binary_any_analysis: bool = True


class LintianData(BaseTaskDataWithExecutor):
    """In memory task data for the Lintian task."""

    input: LintianInput
    output: LintianOutput = pydantic.Field(default_factory=LintianOutput)
    target_distribution: str = "debian:unstable"
    # Passed to --tags to lintian
    include_tags: list[str] = pydantic.Field(default_factory=list)
    # Passed to --suppress-tags to lintian
    exclude_tags: list[str] = pydantic.Field(default_factory=list)
    # If the analysis emits tags of this severity or higher, the task will
    # return 'failure' instead of 'success'
    fail_on_severity: LintianFailOnSeverity = LintianFailOnSeverity.NONE


class LintianDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the Lintian task."""

    input_source_artifact_id: int | None = None
    input_binary_artifacts_ids: list[int] = pydantic.Field(default_factory=list)


class BlhcInput(BaseTaskDataModel):
    """Input for a blhc task."""

    artifact: LookupSingle


class BlhcFlags(StrEnum):
    """Possible values for extra_flags."""

    ALL = "--all"
    BINDNOW = "--bindnow"
    BUILDD = "--buildd"
    COLOR = "--color"
    DEBIAN = "--debian"
    LINE_NUMBERS = "--line-numbers"
    PIE = "--pie"


class BlhcData(BaseTaskDataWithExecutor):
    """In memory task data for the Blhc task."""

    input: BlhcInput
    # Passed to blhc
    extra_flags: list[BlhcFlags] = pydantic.Field(default_factory=list)


class BlhcDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the Blhc task."""

    input_artifact_id: int


class DebDiffInput(BaseTaskDataModel):
    """Input for a debdiff task."""

    source_artifacts: list[LookupSingle] | None = pydantic.Field(
        default=None, min_items=2, max_items=2
    )
    binary_artifacts: list[LookupMultiple] | None = pydantic.Field(
        default=None, min_items=2, max_items=2
    )

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def check_one_of_source_or_binary(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Ensure only source or binary artifacts are present."""
        if (values.get("source_artifacts") is None) == (
            values.get("binary_artifacts") is None
        ):
            raise ValueError(
                'Exactly one of source_artifacts'
                'or binary_artifacts must be set'
            )
        return values


class DebDiffFlags(StrEnum):
    """Possible values for extra_flags."""

    DIRS = "--dirs"
    NOCONTROL = "--nocontrol"
    WDIFF = "--wdiff-source-control"
    SHOW_MOVED = "--show-moved"
    DIFFSTAT = "--diffstat"
    PATCHES = "--apply-patches"
    IGNORE_SPACE = "--ignore-space"


class DebDiffData(BaseTaskDataWithExecutor):
    """In memory task data for the DebDiff task."""

    input: DebDiffInput
    # Passed to debdiff
    extra_flags: list[DebDiffFlags] = pydantic.Field(default_factory=list)
    host_architecture: str
    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle


class DebDiffDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the DebDiff task."""

    # BaseTaskDynamicDataWithExecutor declares this as optional, but it's
    # required here.
    environment_id: int

    input_source_artifacts_ids: list[int] | None = pydantic.Field(
        default=None, min_items=2, max_items=2
    )
    # TODO: Once we depend on pydantic 2.9.2-1 add a check:
    # pydantic.Field(default=None, min_items=2, max_items=2)
    input_binary_artifacts_ids: list[list[int]] | None = None


class MmDebstrapVariant(StrEnum):
    """Variants supported by `mmdebstrap`."""

    BUILDD = "buildd"
    MINBASE = "minbase"
    DASH = "-"
    APT = "apt"
    CUSTOM = "custom"
    DEBOOTSTRAP = "debootstrap"
    ESSENTIAL = "essential"
    EXTRACT = "extract"
    IMPORTANT = "important"
    REQUIRED = "required"
    STANDARD = "standard"


class DebootstrapVariant(StrEnum):
    """Variants supported by `debootstrap`."""

    BUILDD = "buildd"
    MINBASE = "minbase"


class SystemBootstrapOptions(BaseTaskDataModel):
    """Structure of SystemBootstrap options."""

    architecture: str
    # Specializations of this model for individual tasks should restrict
    # this to particular values.
    variant: str | None = None
    extra_packages: list[str] = pydantic.Field(default_factory=list)
    use_signed_by: bool = True


class SystemBootstrapRepositoryType(StrEnum):
    """Possible values for repository types."""

    DEB = "deb"
    DEB_SRC = "deb-src"


class SystemBootstrapRepositoryCheckSignatureWith(StrEnum):
    """Possible values for check_signature_with."""

    SYSTEM = "system"
    EXTERNAL = "external"
    NO_CHECK = "no-check"


class SystemBootstrapRepositoryKeyring(BaseTaskDataModel):
    """Description of a repository keyring."""

    url: pydantic.AnyUrl | pydantic.FileUrl
    sha256sum: str = ""
    install: bool = False

    @pydantic.validator("url")
    @classmethod
    def validate_url(
        cls, url: pydantic.AnyUrl | pydantic.FileUrl
    ) -> pydantic.AnyUrl | pydantic.FileUrl:
        """
        Reject file:// URLs outside /usr/(local/)share/keyrings/.

        We don't want to allow reading arbitrary paths.
        """
        if (
            url.scheme == "file"
            and url.path is not None
            and not (
                Path(url.path).resolve().is_relative_to("/usr/share/keyrings")
            )
            and not (
                Path(url.path)
                .resolve()
                .is_relative_to("/usr/local/share/keyrings")
            )
        ):
            raise ValueError(
                "file:// URLs for keyrings must be under /usr/share/keyrings/ "
                "or /usr/local/share/keyrings/"
            )
        return url


class SystemBootstrapRepository(BaseTaskDataModel):
    """Description of one repository in SystemBootstrapData."""

    mirror: str
    suite: str
    types: list[SystemBootstrapRepositoryType] = pydantic.Field(
        default_factory=lambda: [SystemBootstrapRepositoryType.DEB],
        min_items=1,
        unique_items=True,
    )
    components: list[str] | None = pydantic.Field(
        default=None, unique_items=True
    )
    check_signature_with: SystemBootstrapRepositoryCheckSignatureWith = (
        SystemBootstrapRepositoryCheckSignatureWith.SYSTEM
    )
    keyring_package: str | None = None
    keyring: SystemBootstrapRepositoryKeyring | None = None

    @pydantic.root_validator(allow_reuse=True)
    @classmethod
    def _check_external_keyring(cls, values: Any) -> Any:
        """Require keyring if check_signature_with is external."""
        if (
            values.get("check_signature_with")
            == SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL
        ):
            if values.get("keyring") is None:
                raise ValueError(
                    "repository requires 'keyring': "
                    "'check_signature_with' is set to 'external'"
                )
        return values


class SystemBootstrapData(BaseTaskData):
    """Base for in-memory class data for SystemBootstrap tasks."""

    bootstrap_options: SystemBootstrapOptions
    bootstrap_repositories: list[SystemBootstrapRepository] = pydantic.Field(
        min_items=1
    )
    customization_script: str | None = None


class MmDebstrapBootstrapOptions(SystemBootstrapOptions):
    """Structure of MmDebstrap options."""

    variant: MmDebstrapVariant | None = None
    use_signed_by: bool = True


class MmDebstrapData(SystemBootstrapData):
    """In memory task data for the MmDebstrap task."""

    bootstrap_options: MmDebstrapBootstrapOptions


class DebootstrapBootstrapOptions(SystemBootstrapOptions):
    """Structure of debootstrap options."""

    variant: DebootstrapVariant | None = None


class DiskImageFormat(StrEnum):
    """Possible disk image formats."""

    RAW = "raw"
    QCOW2 = "qcow2"


class Partition(BaseTaskDataModel):
    """Partition definition."""

    size: int
    filesystem: str
    mountpoint: str = "none"


class DiskImage(BaseTaskDataModel):
    """Disk image definition."""

    format: DiskImageFormat
    filename: str = "image"
    kernel_package: str | None = None
    bootloader: str | None = None
    partitions: list[Partition] = pydantic.Field(min_items=1)


class SystemImageBuildData(SystemBootstrapData):
    """Base for in-memory class data for SystemImageBuild tasks."""

    bootstrap_options: DebootstrapBootstrapOptions
    disk_image: DiskImage


class PiupartsDataInput(BaseTaskDataModel):
    """Input for a piuparts task."""

    binary_artifacts: LookupMultiple


class PiupartsData(BaseTaskDataWithExecutor, BaseTaskDataWithExtraRepositories):
    """In memory task data for the Piuparts task."""

    input: PiupartsDataInput
    host_architecture: str
    base_tgz: LookupSingle
    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle


class PiupartsDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the Piuparts task."""

    # BaseTaskDynamicDataWithExecutor declares this as optional, but it's
    # required here.
    environment_id: int

    input_binary_artifacts_ids: list[int]
    base_tgz_id: int


class SbuildInput(BaseTaskDataModel):
    """Input for a sbuild task."""

    source_artifact: LookupSingle
    extra_binary_artifacts: LookupMultiple = pydantic.Field(
        default_factory=empty_lookup_multiple
    )


class SbuildBinNMU(BaseTaskDataModel):
    """binmu for a sbuild task."""

    # --make-binNMU
    changelog: str
    # --append-to-version
    suffix: str
    # --binNMU-timestamp, default to now
    timestamp: datetime | None = None
    # --maintainer, defaults to uploader
    maintainer: pydantic.NameEmail | None = None


class SbuildBuildComponent(StrEnum):
    """Possible values for build_components."""

    ANY = "any"
    ALL = "all"
    SOURCE = "source"


class SbuildData(BaseTaskDataWithExecutor, BaseTaskDataWithExtraRepositories):
    """In memory task data for the Sbuild task."""

    input: SbuildInput
    host_architecture: str
    build_components: list[SbuildBuildComponent] = pydantic.Field(
        default_factory=lambda: [SbuildBuildComponent.ANY],
    )
    binnmu: SbuildBinNMU | None = None
    build_profiles: list[str] | None = None
    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle

    @pydantic.root_validator
    @classmethod
    def check_binnmu_against_components(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Binnmus are incompatible with architecture-independent builds."""
        if values.get("binnmu") is not None:
            if SbuildBuildComponent.ALL in values.get("build_components", []):
                raise ValueError(
                    "Cannot build architecture-independent packages in a binNMU"
                )
        return values


class SbuildDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the Sbuild task."""

    input_source_artifact_id: int
    input_extra_binary_artifacts_ids: list[int] = pydantic.Field(
        default_factory=list
    )
    binnmu_maintainer: str | None = None


class ImageCacheUsageLogEntry(BaseTaskDataModel):
    """Entry in ImageCacheUsageLog for cached executor images."""

    filename: str
    backend: str | None = None
    timestamp: datetime

    @pydantic.validator("timestamp")
    @classmethod
    def timestamp_is_aware(cls, timestamp: datetime) -> datetime:
        """Ensure that the timestamp is TZ-aware."""
        tzinfo = timestamp.tzinfo
        if tzinfo is None or tzinfo.utcoffset(timestamp) is None:
            raise ValueError("timestamp is TZ-naive")
        return timestamp


class ImageCacheUsageLog(BaseTaskDataModel):
    """Usage log for cached executor images."""

    version: int = 1
    backends: set[str] = pydantic.Field(default_factory=set)
    usage: list[ImageCacheUsageLogEntry] = pydantic.Field(default_factory=list)

    @pydantic.validator("version")
    @classmethod
    def version_is_known(cls, version: int) -> int:
        """Ensure that the version is known."""
        if version != 1:
            raise ValueError(f"Unknown usage log version {version}")
        return version


class ExtractForSigningInput(BaseTaskDataModel):
    """Input for the ExtractForSigning task."""

    template_artifact: LookupSingle
    binary_artifacts: LookupMultiple


class ExtractForSigningData(BaseTaskDataWithExecutor):
    """In-memory task data for the ExtractForSigning task."""

    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle

    input: ExtractForSigningInput

    @pydantic.validator("backend")
    @classmethod
    def backend_is_auto(cls, backend: str) -> str:
        """Ensure that the backend is "auto"."""
        if backend != BackendType.AUTO:
            raise ValueError(
                f'ExtractForSigning only accepts backend "auto", not '
                f'"{backend}"'
            )
        return backend


class ExtractForSigningDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the ExtractForSigning task."""

    # BaseDynamicTaskDataWithExecutor declares this as optional, but it's
    # required here.
    environment_id: int

    input_template_artifact_id: int
    input_binary_artifacts_ids: list[int]


class AssembleSignedSourceData(BaseTaskDataWithExecutor):
    """In-memory task data for the AssembleSignedSource task."""

    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle

    template: LookupSingle
    signed: LookupMultiple

    @pydantic.validator("backend")
    @classmethod
    def backend_is_auto(cls, backend: str) -> str:
        """Ensure that the backend is "auto"."""
        if backend != BackendType.AUTO:
            raise ValueError(
                f'AssembleSignedSource only accepts backend "auto", not '
                f'"{backend}"'
            )
        return backend


class AssembleSignedSourceDynamicData(BaseDynamicTaskDataWithExecutor):
    """Dynamic data for the AssembleSignedSource task."""

    # BaseDynamicTaskDataWithExecutor declares this as optional, but it's
    # required here.
    environment_id: int

    template_id: int
    signed_ids: list[int]


class MakeSourcePackageUploadInput(BaseTaskDataModel):
    """Input for a MakeSourcePackageUpload task."""

    source_artifact: LookupSingle


class MakeSourcePackageUploadData(BaseTaskDataWithExecutor):
    """In memory task data for the MakeSourcePackageUpload task."""

    # BaseTaskDataWithExecutor declares this as optional, but it's required
    # here.
    environment: LookupSingle

    input: MakeSourcePackageUploadInput
    since_version: str | None
    target_distribution: str | None


class MakeSourcePackageUploadDynamicData(BaseDynamicTaskDataWithExecutor):
    """Expanded dynamic data for the MakeSourcePackageUpload task."""

    # BaseDynamicTaskDataWithExecutor declares this as optional, but it's
    # required here.
    environment_id: int

    input_source_artifact_id: int


class MergeUploadsInput(BaseTaskDataModel):
    """Input for a MergeUploads task."""

    uploads: LookupMultiple


class MergeUploadsData(BaseTaskData):
    """In memory task data for the MergeUploads task."""

    # No longer used.
    backend: BackendType = BackendType.AUTO
    # No longer used.
    environment: LookupSingle | None = None

    input: MergeUploadsInput


class MergeUploadsDynamicData(BaseDynamicTaskData):
    """Expanded dynamic data for the MergeUploads task."""

    # No longer used.
    environment_id: int | None = None

    input_uploads_ids: list[int]


# Workarounds for https://github.com/yaml/pyyaml/issues/722
yaml.SafeDumper.add_multi_representer(
    StrEnum,
    yaml.representer.SafeRepresenter.represent_str,
)
