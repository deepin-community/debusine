# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Reverse-dependencies autopkgtest workflow."""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property, partial
from itertools import chain
from typing import Any, Self

from debian.deb822 import PkgRelation
from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import (
    BooleanField,
    F,
    Func,
    JSONField,
    OuterRef,
    Q,
    Value,
)
from django.db.models.fields.json import KT

from debusine.artifacts.models import (
    ArtifactCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianSourcePackage,
    DebianUpload,
)
from debusine.client.models import LookupChildType
from debusine.db.models import Collection, CollectionItem, WorkRequest
from debusine.server.collections.lookup import lookup_multiple, lookup_single
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.base import (
    Workflow,
    WorkflowValidationError,
    orchestrate_workflow,
)
from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    ReverseDependenciesAutopkgtestWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    LookupMultiple,
    LookupSingle,
    TaskTypes,
)
from debusine.tasks.server import TaskDatabaseInterface

_comma_sep_re = re.compile(r"\s*,\s*")


class NoBinaryNames(Exception):
    """Raised if a lookup result has no binary names."""


class ToJSONB(Func):
    """Convert any SQL value to `jsonb`."""

    function = "to_jsonb"
    output_field = JSONField()


class JSONBPathExists(Func):
    """Check whether a JSON path returns any item for the given JSON value."""

    function = "jsonb_path_exists"
    output_field = BooleanField()


@dataclass(frozen=True, kw_only=True)
class _SourcePackage:
    """A representation of a source package."""

    suite_collection: LookupSingle
    name: str
    version: str

    @classmethod
    def from_artifact_data(
        cls, suite_collection: LookupSingle, artifact_data: dict[str, Any]
    ) -> Self:
        """Construct a representation of a source package."""
        data = DebianSourcePackage(**artifact_data)
        return cls(
            suite_collection=suite_collection,
            name=data.name,
            version=data.version,
        )

    def to_lookup(self) -> str:
        """Return a lookup for this source package."""
        # We could just use artifact IDs, but this is more informative.
        collection = (
            f"{self.suite_collection}@collections"
            if isinstance(self.suite_collection, int)
            else self.suite_collection
        )
        return f"{collection}/source-version:{self.name}_{self.version}"


@dataclass(frozen=True, kw_only=True)
class _BinaryPackage:
    """A representation of a binary package."""

    suite_collection: LookupSingle
    name: str
    version: str
    architecture: str

    @classmethod
    def from_artifact_data(
        cls, suite_collection: LookupSingle, artifact_data: dict[str, Any]
    ) -> Self:
        """Construct a representation of a binary package."""
        data = DebianBinaryPackage(**artifact_data)
        return cls(
            suite_collection=suite_collection,
            name=data.deb_fields["Package"],
            version=data.deb_fields["Version"],
            architecture=data.deb_fields["Architecture"],
        )

    def to_lookup(self) -> str:
        """Return a lookup for this binary package."""
        # We could just use artifact IDs, but this is more informative.
        collection = (
            f"{self.suite_collection}@collections"
            if isinstance(self.suite_collection, int)
            else self.suite_collection
        )
        return (
            f"{collection}/"
            f"binary-version:{self.name}_{self.version}_{self.architecture}"
        )


def _lookup_multiple_to_list(
    lookup: LookupMultiple,
) -> list[int | str | dict[str, Any]]:
    """
    Export a :py:class:`LookupMultiple` as a list.

    This is helpful when combining more than one such lookup.
    """
    exported = lookup.export()
    return exported if isinstance(exported, list) else [exported]


class ReverseDependenciesAutopkgtestWorkflow(
    Workflow[ReverseDependenciesAutopkgtestWorkflowData, BaseDynamicTaskData]
):
    """Run autopkgtest for all the reverse-deps of a package in a suite."""

    TASK_NAME = "reverse_dependencies_autopkgtest"

    def __init__(self, work_request: "WorkRequest") -> None:
        """Instantiate a Workflow with its database instance."""
        super().__init__(work_request)
        if self.data.backend == BackendType.AUTO:
            self.data.backend = BackendType.UNSHARE

    @cached_property
    def suite_collection(self) -> Collection:
        """The collection to search for reverse-dependencies."""
        return lookup_single(
            self.data.suite_collection,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.COLLECTION,
        ).collection

    def validate_input(self) -> None:
        """Thorough validation of input data."""
        # Validate that we can look up self.data.suite_collection.
        try:
            self.suite_collection
        except LookupError as e:
            raise WorkflowValidationError(str(e)) from e

    def get_binary_names(self) -> set[str]:
        """Return names of binary artifacts whose rdeps we want to test."""
        binary_names: set[str] = set()
        for result in lookup_multiple(
            self.data.binary_artifacts,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        ):
            if result.artifact is not None:
                binary_data = result.artifact.create_data()
                match binary_data:
                    case DebianBinaryPackage():
                        binary_names.add(binary_data.deb_fields["Package"])
                    case DebianBinaryPackages():
                        binary_names.update(binary_data.packages)
                    case DebianUpload():
                        binary_names.update(
                            binary_data.changes_fields["Binary"].split()
                        )
                    case _:
                        raise NoBinaryNames(
                            f"Artifact of category {result.artifact.category} "
                            f"has no binary packages"
                        )
            else:
                assert result.collection_item is not None
                binary_names.update(result.collection_item.data["binary_names"])
        return binary_names

    def get_reverse_dependencies(
        self,
    ) -> frozenset[tuple[_SourcePackage, frozenset[_BinaryPackage]]]:
        """
        Find source/binary packages that depend on the given package.

        :return: a list of tuples, each of which is a source package and its
          list of associated binary packages.
        """
        source_artifact = lookup_single(
            self.data.source_artifact,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.ARTIFACT,
        ).artifact
        source_artifact = workflow_utils.locate_debian_source_package(
            "source_artifact", source_artifact
        )
        source_artifact_data = source_artifact.create_data()
        assert isinstance(source_artifact_data, DebianSourcePackage)

        suite_artifacts = partial(
            CollectionItem.active_objects.all().artifacts_in_collection,
            parent_collection=self.suite_collection,
        )

        binary_names = self.get_binary_names()
        # This regex is executed by PostgreSQL, not Python, although as it
        # happens everything that we need is common to both implementations.
        # See:
        # https://www.postgresql.org/docs/current/functions-matching.html#FUNCTIONS-POSIX-REGEXP
        depends_pattern = (
            fr"(?:^|\W)"
            fr"(?:{'|'.join(re.escape(name) for name in binary_names)})"
            fr"(?:\W|$)"
        )
        depends_pattern_json = json.dumps(depends_pattern)

        # Find possible source packages and their corresponding binaries
        # that may count as reverse-dependencies for which we should run
        # tests.  It's difficult to express the exact conditions in SQL, but
        # we try to at least reduce the set of rows to something more
        # manageable so that we can do detailed checks in Python.
        candidates = suite_artifacts(
            category=ArtifactCategory.SOURCE_PACKAGE,
        ).annotate(
            package=KT("data__package"),
            version=KT("data__version"),
            source_data=F("artifact__data"),
            binary_data=ToJSONB(
                ArraySubquery(
                    suite_artifacts(
                        category=ArtifactCategory.BINARY_PACKAGE,
                    )
                    .annotate(
                        source_name=KT("data__srcpkg_name"),
                        source_version=KT("data__srcpkg_version"),
                    )
                    .filter(
                        source_name=OuterRef("package"),
                        source_version=OuterRef("version"),
                    )
                    .values("artifact__data")
                ),
            ),
            testsuite=KT("source_data__dsc_fields__Testsuite"),
            testsuite_triggers=KT(
                "source_data__dsc_fields__Testsuite-Triggers"
            ),
        )

        # The name is not equal to the name of the source package being
        # tested.
        candidates = candidates.exclude(package=source_artifact_data.name)
        # The name is not listed in packages_denylist.
        candidates = candidates.exclude(package__in=self.data.packages_denylist)
        # The name is listed in packages_allowlist (only tested if the field
        # is present and set).
        if self.data.packages_allowlist is not None:
            candidates = candidates.filter(
                package__in=self.data.packages_allowlist
            )
        # It has binary packages built from it.
        candidates = candidates.exclude(binary_data=[])
        # Either any of its binary packages depend on any of the given
        # binary package names, or any of the given binary package names are
        # in its Testsuite-Triggers field.
        any_binary_matches_jsonpath = (
            # JSON path queries only have rather basic support in Django,
            # but they seem to be the easiest way to query for properties
            # that hold for any of the associated binary packages.
            f'$[*].deb_fields ? ('
            f'@."Pre-Depends" like_regex {depends_pattern_json} '
            f'|| @."Depends" like_regex {depends_pattern_json}'
            f')'
        )
        candidates = candidates.annotate(
            any_binary_matches=JSONBPathExists(
                "binary_data", Value(any_binary_matches_jsonpath)
            )
        ).filter(
            Q(any_binary_matches=True)
            | Q(testsuite_triggers__regex=depends_pattern)
        )
        # Its Testsuite field has an item either equal to "autopkgtest" or
        # starting with "autopkgtest-pkg".
        candidates = candidates.filter(
            # Imprecise, since this is usually close enough; we'll
            # double-check it in Python.
            testsuite__contains="autopkgtest"
        )

        revdeps: set[tuple[_SourcePackage, frozenset[_BinaryPackage]]] = set()
        for source_data, binaries_data in candidates.values_list(
            "source_data", "binary_data"
        ).iterator():
            # Check that the Testsuite field is suitable.
            if not any(
                testsuite == "autopkgtest"
                or testsuite.startswith("autopkgtest-pkg")
                for testsuite in _comma_sep_re.split(
                    # The query above ensures that this exists.
                    source_data["dsc_fields"]["Testsuite"]
                )
            ):
                continue

            # Double-check Pre-Depends, Depends, and Testsuite-Triggers;
            # it's hard to do full package relationship field parsing in
            # SQL.
            all_dependencies: list[list["PkgRelation.ParsedRelation"]] = []
            for binary_data in binaries_data:
                # The query above ensures that this exists.
                deb_fields = binary_data["deb_fields"]
                if "Pre-Depends" in deb_fields:
                    all_dependencies.extend(
                        PkgRelation.parse_relations(deb_fields["Pre-Depends"])
                    )
                if "Depends" in deb_fields:
                    all_dependencies.extend(
                        PkgRelation.parse_relations(deb_fields["Depends"])
                    )
            if "Testsuite-Triggers" in source_data["dsc_fields"]:
                all_dependencies.extend(
                    PkgRelation.parse_relations(
                        source_data["dsc_fields"]["Testsuite-Triggers"]
                    )
                )
            # TODO: Maybe we should do more accurate tests on
            # version/architecture restrictions etc.?  For now, this is
            # easy, but it may be a problem if it results in superfluous
            # autopkgtests that fail.
            all_dependency_names = {
                dep["name"] for dep in chain.from_iterable(all_dependencies)
            }
            if not all_dependency_names.intersection(binary_names):
                continue

            source = _SourcePackage.from_artifact_data(
                self.data.suite_collection, source_data
            )
            binaries = frozenset(
                _BinaryPackage.from_artifact_data(
                    self.data.suite_collection, binary_data
                )
                for binary_data in binaries_data
            )
            revdeps.add((source, binaries))

        return frozenset(revdeps)

    def _populate_single(
        self, source: _SourcePackage, binaries: Iterable[_BinaryPackage]
    ) -> None:
        """Create an autopkgtest sub-workflow for a single source package."""
        assert self.work_request is not None

        context_lookup = LookupMultiple.parse_obj(
            _lookup_multiple_to_list(self.data.binary_artifacts)
            + _lookup_multiple_to_list(self.data.context_artifacts)
        )

        wr = self.work_request_ensure_child(
            task_type=TaskTypes.WORKFLOW,
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix=f"{source.name}_{source.version}|",
                source_artifact=source.to_lookup(),
                binary_artifacts=LookupMultiple.parse_obj(
                    sorted([binary.to_lookup() for binary in binaries])
                ),
                context_artifacts=context_lookup,
                vendor=self.data.vendor,
                codename=self.data.codename,
                backend=self.data.backend,
                architectures=self.data.architectures,
                arch_all_host_architecture=self.data.arch_all_host_architecture,
                extra_repositories=self.data.extra_repositories,
                debug_level=self.data.debug_level,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=f"autopkgtests for {source.name}/{source.version}",
                step=f"autopkgtests-{source.name}/{source.version}",
            ),
        )
        # The sub-workflow adds appropriate dependencies, and provides
        # results in the internal collection.
        wr.mark_running()
        orchestrate_workflow(wr)

    def populate(self) -> None:
        """Create autopkgtest sub-workflows for all reverse-dependencies."""
        for source, binaries in self.get_reverse_dependencies():
            self._populate_single(source, binaries)

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: package name of ``source_artifact``
        """
        source_data = workflow_utils.source_package_data(self)
        return BaseDynamicTaskData(
            subject=source_data.name,
            parameter_summary=f"{source_data.name}_{source_data.version}",
        )

    def get_label(self) -> str:
        """Return the task label."""
        # TODO: copy the source package information in dynamic task data and
        # use them here if available
        return "run autopkgtests of reverse-dependencies"
