# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Sbuild Workflow."""

import re
from functools import cached_property

from debian.debian_support import DpkgArchTable

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianSourcePackage,
    get_source_package_name,
)
from debusine.client.models import LookupChildType
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.base import Workflow, WorkflowValidationError
from debusine.server.workflows.models import (
    SbuildWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks import get_environment
from debusine.tasks.models import (
    ActionRetryWithDelays,
    ActionUpdateCollectionWithArtifacts,
    ActionUpdateCollectionWithData,
    BackendType,
    BaseDynamicTaskData,
    SbuildBuildComponent,
    SbuildBuildDepResolver,
    SbuildData,
)
from debusine.tasks.server import TaskDatabaseInterface


class SbuildWorkflow(Workflow[SbuildWorkflowData, BaseDynamicTaskData]):
    """Build a source package for all architectures of a target distribution."""

    TASK_NAME = "sbuild"

    def __init__(self, work_request: "WorkRequest"):
        """Instantiate a Workflow with its database instance."""
        super().__init__(work_request)
        if self.data.backend == BackendType.AUTO:
            self.data.backend = BackendType.UNSHARE

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: package name of ``input.source_artifact``
        """
        source_data = workflow_utils.source_package_data(self)
        return BaseDynamicTaskData(
            subject=source_data.name,
            parameter_summary=f"{source_data.name}_{source_data.version}",
        )

    @cached_property
    def binary_names(self) -> list[str]:
        """Binary names that may be built."""
        return re.split(
            r"\s*,\s*",
            workflow_utils.source_package_data(self).dsc_fields.get(
                "Binary", ""
            ),
        )

    @cached_property
    def architectures(self) -> set[str]:
        """Architectures to build."""
        workflow_arches = set(self.data.architectures)

        package_arches = set(
            workflow_utils.source_package_data(self)
            .dsc_fields.get("Architecture", "")
            .split()
        )
        if not package_arches:
            raise WorkflowValidationError("package architecture list is empty")

        # Intersect architectures with dsc_fields["Architecture"]
        architectures_to_build = set()
        # Support architecture wildcards (any, <os>-any, any-<cpu>)
        # https://www.debian.org/doc/debian-policy/ch-customized-programs.html#architecture-wildcards
        arch_table = DpkgArchTable.load_arch_table()

        for package_arch in package_arches:
            for workflow_arch in workflow_arches:
                if workflow_arch == "all":
                    # all matches any according to DpkgArchTable, but that's
                    # not useful here.
                    if package_arch == "all":
                        architectures_to_build.add("all")
                elif arch_table.matches_architecture(
                    workflow_arch, package_arch
                ):
                    architectures_to_build.add(workflow_arch)

        if not architectures_to_build:
            raise WorkflowValidationError(
                "None of the workflow architectures are supported"
                " by this package:"
                f" workflow: {', '.join(sorted(workflow_arches))}"
                f" package: {', '.join(sorted(package_arches))}"
            )

        return architectures_to_build

    def _get_host_architecture(self, architecture: str) -> str:
        """
        Get the host architecture to build a given architecture.

        `architecture` may be "all".
        """
        # TODO (#358): possibly support XS-Build-Indep-Architecture
        return (
            self.data.arch_all_host_architecture
            if architecture == "all"
            else architecture
        )

    def _get_environment_lookup(self) -> str:
        """
        Build the environment lookup.

        :py:func:`get_environment` will fill in additional constraints.
        """
        vendor, codename = self.data.target_distribution.split(":", 1)

        lookup = f"{vendor}/match:codename={codename}"
        if self.data.environment_variant:
            lookup += f":variant={self.data.environment_variant}"

        return lookup

    def _get_environment(self, architecture: str) -> Artifact:
        """
        Lookup an environment to build on the given architecture.

        This is only used for validation that we have all the environments
        we need; the actual tasks will do their own lookups.
        """
        lookup = self._get_environment_lookup()
        host_architecture = self._get_host_architecture(architecture)

        # set in BaseServerTask.__init__() along with work_request
        assert self.workspace is not None
        assert self.work_request is not None
        try:
            env_id = get_environment(
                TaskDatabase(self.work_request),
                lookup,
                architecture=host_architecture,
                # TODO: This duplicates logic from
                # BaseTaskWithExecutor.backend.
                backend=(
                    "unshare"
                    if self.data.backend == "auto"
                    else self.data.backend
                ),
                default_category=CollectionCategory.ENVIRONMENTS,
                try_variant="sbuild",
            ).id
        except KeyError:
            raise WorkflowValidationError(
                "environment not found for"
                f" {self.data.target_distribution!r} {architecture!r}"
                f" ({lookup!r})"
            )
        return Artifact.objects.get(id=env_id)

    def validate_input(self) -> None:
        """Thorough validation of input data."""
        # Validate target_distribution
        if ":" not in self.data.target_distribution:
            raise WorkflowValidationError(
                "target_distribution must be in vendor:codename format"
            )

        # Artifact and architectures are validated by accessing
        # self.architectures
        self.architectures

        vendor, codename = self.data.target_distribution.split(":", 1)

        # Make sure we have environments for all architectures we need
        for arch in self.architectures:
            # Attempt to get the environment to validate its lookup
            self._get_environment(arch)

    def populate(self) -> None:
        """Create sbuild WorkRequests for all architectures."""
        assert self.work_request is not None
        children = self.work_request.children.all()
        existing_arches: set[str] = set()
        for child in children:
            if child.task_data["build_components"] == [
                SbuildBuildComponent.ALL
            ]:
                existing_arches.add("all")
            else:
                existing_arches.add(child.task_data["host_architecture"])

        # Idempotence
        # It is unlikely that there are workrequests already created that we do
        # not need anymore, and I cannot think of a scenario when that may
        # happen. Still, I'm leaving an assertion to catch it, so that if it
        # happens it can be detected and studied to figure out what corner case
        # has been hit and how to handle it
        assert not existing_arches - self.architectures

        if arches := self.architectures - existing_arches:
            for architecture in sorted(arches):
                self._populate_single(
                    architecture, environment=self._get_environment_lookup()
                )

    def _populate_single(self, architecture: str, *, environment: str) -> None:
        """Create a single sbuild WorkRequest."""
        assert self.work_request is not None
        host_architecture = self._get_host_architecture(architecture)
        input_ = self.data.input.copy()

        input_.source_artifact = (
            workflow_utils.locate_debian_source_package_lookup(
                self, "input.source_artifact", self.data.input.source_artifact
            )
        )

        source_artifact = lookup_single(
            input_.source_artifact,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.ARTIFACT,
        ).artifact
        assert source_artifact.category == ArtifactCategory.SOURCE_PACKAGE
        debian_source_package = DebianSourcePackage(**source_artifact.data)

        vendor, codename = self.data.target_distribution.split(":", 1)
        extra_repositories = workflow_utils.configure_for_overlay_suite(
            self,
            extra_repositories=self.data.extra_repositories,
            vendor=vendor,
            codename=codename,
            environment=environment,
            backend=self.data.backend,
            architecture=host_architecture,
            try_variant="sbuild",
        )

        build_dep_resolver: SbuildBuildDepResolver | None = None
        aspcud_criteria: str | None = None
        if (vendor, codename) == ("debian", "experimental"):
            # https://salsa.debian.org/dsa-team/mirror/dsa-puppet/-/blob/production/modules/buildd/templates/sbuild.conf.erb
            # TODO: Implement this entirely in task-configuration
            build_dep_resolver = SbuildBuildDepResolver.ASPCUD
            aspcud_criteria = (
                "-count(down),-count(changed,APT-Release:=/experimental/),"
                "-removed,-changed,-new"
            )

        task_data = SbuildData(
            input=input_,
            host_architecture=host_architecture,
            environment=environment,
            backend=self.data.backend,
            build_components=[
                (
                    SbuildBuildComponent.ALL
                    if architecture == "all"
                    else SbuildBuildComponent.ANY
                )
            ],
            build_profiles=self.data.build_profiles,
            extra_repositories=extra_repositories,
            binnmu=self.data.binnmu,
            build_dep_resolver=build_dep_resolver,
            aspcud_criteria=aspcud_criteria,
        )
        wr = self.work_request_ensure_child(
            task_name="sbuild",
            task_data=task_data,
            workflow_data=WorkRequestWorkflowData(
                display_name=f"Build {architecture}",
                step=f"build-{architecture}",
            ),
        )

        self.provides_artifact(
            wr,
            ArtifactCategory.UPLOAD,
            f"{self.data.prefix}build-{architecture}",
            data={
                "binary_names": self.binary_names,
                "architecture": architecture,
                "source_package_name": get_source_package_name(
                    debian_source_package
                ),
            },
        )
        self.provides_artifact(
            wr,
            ArtifactCategory.PACKAGE_BUILD_LOG,
            f"{self.data.prefix}buildlog-{architecture}",
            data={"architecture": architecture},
        )

        try:
            build_logs_collection_id = self.lookup_singleton_collection(
                CollectionCategory.PACKAGE_BUILD_LOGS
            ).id
        except KeyError:
            build_logs_collection_id = None

        if build_logs_collection_id is not None:
            variables = {
                "work_request_id": wr.id,
                "vendor": vendor,
                "codename": codename,
                "architecture": host_architecture,
                "srcpkg_name": workflow_utils.source_package_data(self).name,
                "srcpkg_version": workflow_utils.source_package_data(
                    self
                ).version,
            }
            wr.add_event_reaction(
                "on_creation",
                ActionUpdateCollectionWithData(
                    collection=build_logs_collection_id,
                    category=BareDataCategory.PACKAGE_BUILD_LOG,
                    data=variables,
                ),
            )
            wr.add_event_reaction(
                "on_success",
                ActionUpdateCollectionWithArtifacts(
                    collection=build_logs_collection_id,
                    variables=variables,
                    artifact_filters={
                        "category": ArtifactCategory.PACKAGE_BUILD_LOG
                    },
                ),
            )

        if self.data.retry_delays is not None:
            wr.add_event_reaction(
                "on_failure",
                ActionRetryWithDelays(delays=self.data.retry_delays),
            )

        for binary_package_name in self.data.signing_template_names.get(
            architecture, []
        ):
            self.provides_artifact(
                wr,
                ArtifactCategory.BINARY_PACKAGE,
                f"{self.data.prefix}signing-template-{architecture}-"
                f"{binary_package_name}",
                data={
                    "architecture": architecture,
                    "binary_package_name": binary_package_name,
                },
                artifact_filters={
                    "data__deb_fields__Package": binary_package_name
                },
            )
