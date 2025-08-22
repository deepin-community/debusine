# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debdiff workflow."""
from collections import defaultdict
from collections.abc import Sequence
from typing import NewType

from debian.debian_support import Version

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianSourcePackage,
)
from debusine.client.models import LookupChildType
from debusine.db.models import Artifact, ArtifactRelation, WorkRequest
from debusine.server.collections.lookup import (
    LookupResult,
    lookup_multiple,
    lookup_single,
    reconstruct_lookup,
)
from debusine.server.workflows import Workflow, workflow_utils
from debusine.server.workflows.models import (
    DebDiffWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.workflow_utils import (
    lookup_result_architecture,
    lookup_result_binary_package_name,
)
from debusine.tasks.models import (
    BaseDynamicTaskData,
    DebDiffData,
    DebDiffFlags,
    DebDiffInput,
    LookupMultiple,
    LookupSingle,
)
from debusine.tasks.server import TaskDatabaseInterface

PackageName = NewType("PackageName", str)
Architecture = NewType("Architecture", str)
PackageArchLookupMap = dict[PackageName, dict[Architecture, LookupSingle]]


class DebDiffWorkflow(Workflow[DebDiffWorkflowData, BaseDynamicTaskData]):
    """DebDiff workflow."""

    TASK_NAME = "debdiff"

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: package name of ``source_artifact``
        """
        new_source_data = workflow_utils.source_package_data(self)
        version_new = new_source_data.version

        debian_suite = lookup_single(
            self.data.original,
            workspace=self.workspace,
            user=self.work_request.created_by,
            expect_type=LookupChildType.COLLECTION,
        ).collection

        original_source_package = debian_suite.manager.lookup(
            f"source:{new_source_data.name}"
        )

        version_original = None
        if original_source_package is not None:
            assert original_source_package.artifact is not None
            version_original = Version(
                DebianSourcePackage(
                    **original_source_package.artifact.data
                ).version
            )
        else:
            original_binary_packages = (
                self._get_binary_packages_from_source_package(
                    debian_suite=self.data.original,
                    source_package_name=new_source_data.name,
                )
            )

            if original_binary_packages:
                for original_binary_package in original_binary_packages:
                    assert original_binary_package.artifact is not None
                    version = Version(
                        DebianBinaryPackage(
                            **original_binary_package.artifact.data
                        ).srcpkg_version
                    )
                    if version_original is None or version > version_original:
                        version_original = version

        if version_original is not None:
            parameter_summary = (
                f"{debian_suite.name}: {version_original} → {version_new}"
            )
        else:
            parameter_summary = f"{debian_suite.name}: missing → {version_new}"

        return BaseDynamicTaskData(
            subject=new_source_data.name,
            parameter_summary=parameter_summary,
        )

    def populate(self) -> None:
        """Create work requests."""
        new_source_data = workflow_utils.source_package_data(self)

        environment = f"{self.data.vendor}/match:codename={self.data.codename}"

        # Populate DebDiff tasks for the source package
        debian_suite = lookup_single(
            self.data.original,
            workspace=self.workspace,
            user=self.work_request.created_by,
            expect_type=LookupChildType.COLLECTION,
        ).collection

        original_source = debian_suite.manager.lookup(
            f"source:{new_source_data.name}"
        )
        original_source_version: str | None = None
        if original_source is not None:
            original_source_version = original_source.data["version"]
            original_source_lookup = (
                f"{self.data.original}/name:{original_source.name}"
            )
            self._populate_source_artifact_debdiff(
                original=original_source_lookup,
                new=self.data.source_artifact,
                host_architecture=self.data.arch_all_host_architecture,
                environment=environment,
                extra_flags=self.data.extra_flags,
            )

        # Populate DebDiff tasks for the binary packages
        original_binaries_arch_lookup = (
            self._get_original_packages_by_name_version_arch(
                new_source_data.name, original_source_version
            )
        )
        new_binaries_arch_lookup = self._get_new_packages_by_name_arch()

        for arch in self._get_architectures(new_binaries_arch_lookup):
            self._populate_binary_for_arch(
                architecture=arch,
                orig_map=original_binaries_arch_lookup,
                new_map=new_binaries_arch_lookup,
                environment=environment,
                extra_flags=self.data.extra_flags,
            )

    def _populate_binary_for_arch(
        self,
        *,
        architecture: Architecture,
        orig_map: PackageArchLookupMap,
        new_map: PackageArchLookupMap,
        environment: LookupSingle,
        extra_flags: list[DebDiffFlags],
    ) -> None:
        new_relevants = {
            arch_lookup[architecture]
            for arch_lookup in new_map.values()
            if architecture in arch_lookup
        }

        original_relevants: list[LookupSingle] = []
        for old_pkg_name, old_arch_map in orig_map.items():
            # Architectures are considered compatible when they are equal,
            # or when one is "all" and the other is a concrete architecture
            # (to account for changes in a binary package's declared
            # architecture across versions).
            original_relevants += [
                old_lookup
                for old_arch, old_lookup in old_arch_map.items()
                if old_pkg_name in new_map
                and (architecture == "all" or old_arch in ("all", architecture))
            ]

        if original_relevants and new_relevants:
            host_arch = (
                self.data.arch_all_host_architecture
                if architecture == "all"
                else architecture
            )
            self._populate_binary_artifact_debdiff(
                originals=LookupMultiple.parse_obj(sorted(original_relevants)),
                news=LookupMultiple.parse_obj(sorted(new_relevants)),
                architecture=architecture,
                host_architecture=host_arch,
                environment=environment,
                extra_flags=extra_flags,
            )

    def _get_binary_packages_from_source_package(
        self,
        debian_suite: LookupSingle,
        source_package_name: str,
        source_package_version: str | None = None,
    ) -> Sequence[LookupResult]:
        kwargs = {
            "collection": debian_suite,
            "child_type": LookupChildType.ARTIFACT,
            "data__srcpkg_name": source_package_name,
            "category": ArtifactCategory.BINARY_PACKAGE,
        }

        if source_package_version is not None:
            kwargs["data__srcpkg_version"] = source_package_version

        lookup = LookupMultiple.parse_obj(kwargs)

        return lookup_multiple(
            lookup,
            self.workspace,
            user=self.work_request.created_by,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
            default_category=CollectionCategory.SUITE,
        )

    def _get_original_packages_by_name_version_arch(
        self,
        srcpkg_name: str,
        srcpkg_version: str | None,
    ) -> PackageArchLookupMap:
        """
        Return mapping of original binary packages by name and arch.

        Only packages with the highest Version. E.g. if there are the following
        packages: hello/2.0.0, libhello2/2.0.0 and libhello1/1.0.0 the last
        one will not be returned.
        """
        original_binary_results = self._get_binary_packages_from_source_package(
            debian_suite=self.data.original,
            source_package_name=srcpkg_name,
            source_package_version=srcpkg_version,
        )

        packages_by_srcpkg_version: defaultdict[
            Version, list[tuple[PackageName, Architecture, LookupSingle]]
        ] = defaultdict(list)

        for lookup_result in original_binary_results:
            arch = Architecture(lookup_result_architecture(lookup_result))
            pkg_name = PackageName(
                lookup_result_binary_package_name(lookup_result)
            )
            assert lookup_result.artifact is not None
            version = Version(
                DebianBinaryPackage(
                    **lookup_result.artifact.data
                ).srcpkg_version
            )

            assert lookup_result.collection_item is not None
            lookup_str = (
                f"{self.data.original}/name:"
                f"{lookup_result.collection_item.name}"
            )
            packages_by_srcpkg_version[version].append(
                (pkg_name, arch, lookup_str)
            )

        # Return packages with only the highest version
        highest_srcpkg_version = max(packages_by_srcpkg_version, default=None)
        result: PackageArchLookupMap = defaultdict(dict)
        if highest_srcpkg_version is not None:
            for pkg_name, arch, lookup in packages_by_srcpkg_version[
                highest_srcpkg_version
            ]:
                result[pkg_name][arch] = lookup
        return result

    @staticmethod
    def _extract_package_name_and_architecture(
        artifact: Artifact,
    ) -> tuple[PackageName, Architecture]:
        """Expect artifact of type BINARY_PACKAGE."""
        data = artifact.create_data()
        assert isinstance(data, DebianBinaryPackage)

        architecture = Architecture(data.deb_fields.get("Architecture", ""))

        package_name = PackageName(data.deb_fields.get("Package", ""))

        return package_name, architecture

    @classmethod
    def _package_arch_pairs(
        cls,
        lookup_result: LookupResult,
    ) -> list[tuple[PackageName, Architecture]]:
        """Return pairs of package names and architectures for a lookup."""
        if lookup_result.artifact is None:
            raise ValueError(
                "DebDiffWorkflow requires binary artifacts (not promises)"
            )

        match lookup_result.artifact.category:
            case ArtifactCategory.BINARY_PACKAGE:
                architecture = Architecture(
                    lookup_result_architecture(lookup_result)
                )
                pkg_name = PackageName(
                    lookup_result_binary_package_name(lookup_result)
                )
                return [(pkg_name, architecture)]

            case ArtifactCategory.UPLOAD:
                pairs: list[tuple[PackageName, Architecture]] = []
                for relation in lookup_result.artifact.relations.filter(
                    type=ArtifactRelation.Relations.EXTENDS,
                    target__category=ArtifactCategory.BINARY_PACKAGE,
                ).select_related("target"):
                    package_name, architecture = (
                        cls._extract_package_name_and_architecture(
                            relation.target
                        )
                    )
                    pairs.append((package_name, architecture))

                return sorted(pairs)

            # https://github.com/nedbat/coveragepy/issues/1860 (fixed in
            # coverage 7.6.2)
            case _ as artifact_category:  # pragma: no cover
                raise ValueError(f"Unexpected category {artifact_category}")

    def _get_new_packages_by_name_arch(
        self,
    ) -> PackageArchLookupMap:
        """Return mapping of new binary packages by name and arch."""
        workflow_root = self.work_request.get_workflow_root()

        new_binary_results = lookup_multiple(
            self.data.binary_artifacts,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=workflow_root,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        )
        packages_by_name_arch: defaultdict[
            PackageName, dict[Architecture, LookupSingle]
        ] = defaultdict(dict)

        for result in new_binary_results:
            # DebDiffWorkflow gets populated once the build is ready
            assert result.artifact is not None

            artifact = result.artifact

            match artifact.category:
                case ArtifactCategory.BINARY_PACKAGE:
                    for pkg_name, arch in self._package_arch_pairs(result):
                        packages_by_name_arch[pkg_name][arch] = (
                            reconstruct_lookup(
                                result, workflow_root=workflow_root
                            )
                        )

                case ArtifactCategory.UPLOAD:
                    for relation in artifact.relations.filter(
                        type=ArtifactRelation.Relations.EXTENDS,
                        target__category=ArtifactCategory.BINARY_PACKAGE,
                    ).select_related("target"):
                        pkg_name, arch = (
                            self._extract_package_name_and_architecture(
                                relation.target
                            )
                        )
                        packages_by_name_arch[pkg_name][
                            arch
                        ] = relation.target.id

                # https://github.com/nedbat/coveragepy/issues/1860 (fixed in
                # coverage 7.6.2)
                case _ as artifact_category:  # pragma: no cover
                    raise ValueError(
                        f"Unexpected category {artifact_category!r}"
                    )

        return packages_by_name_arch

    def _get_architectures(
        self,
        packages_by_name_arch: dict[
            PackageName, dict[Architecture, LookupSingle]
        ],
    ) -> set[Architecture]:
        """Return all architectures present in package mapping."""
        archs: set[Architecture] = set()
        for arch_map in packages_by_name_arch.values():
            archs.update(arch_map.keys())
        return archs

    def _populate_source_artifact_debdiff(
        self,
        *,
        original: LookupSingle,
        new: LookupSingle,
        host_architecture: str,
        environment: LookupSingle,
        extra_flags: list[DebDiffFlags],
    ) -> WorkRequest:
        wr = self.work_request_ensure_child(
            task_name="debdiff",
            task_data=DebDiffData(
                input=DebDiffInput(
                    source_artifacts=[original, new],
                ),
                environment=environment,
                host_architecture=host_architecture,
                extra_flags=extra_flags,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="DebDiff for source package",
                step="debdiff-source",
            ),
        )
        self.requires_artifact(wr, original)
        self.requires_artifact(wr, new)

        return wr

    def _populate_binary_artifact_debdiff(
        self,
        *,
        originals: LookupMultiple,
        news: LookupMultiple,
        architecture: str,
        host_architecture: str,
        environment: LookupSingle,
        extra_flags: list[DebDiffFlags],
    ) -> WorkRequest:
        wr = self.work_request_ensure_child(
            task_name="debdiff",
            task_data=DebDiffData(
                input=DebDiffInput(
                    binary_artifacts=[originals, news],
                ),
                environment=environment,
                host_architecture=host_architecture,
                extra_flags=extra_flags,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=f"DebDiff for binary packages ({architecture})",
                step=f"debdiff-binaries-{architecture}",
            ),
        )
        self.requires_artifact(wr, originals)
        self.requires_artifact(wr, news)

        return wr
