# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Piuparts workflow."""

from debusine.artifacts.models import ArtifactCategory
from debusine.client.models import LookupChildType
from debusine.db.models import WorkRequest
from debusine.server.collections.lookup import lookup_multiple
from debusine.server.workflows import Workflow, workflow_utils
from debusine.server.workflows.models import (
    PiupartsWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    PiupartsData,
    PiupartsDataInput,
)
from debusine.tasks.server import TaskDatabaseInterface


class PiupartsWorkflow(Workflow[PiupartsWorkflowData, BaseDynamicTaskData]):
    """
    Checks binary packages for all architectures of a target distribution.

    Creates piuparts tasks corresponding to the output of a set of
    build tasks for multiple architectures. This is meant to be part
    of a larger QA workflow such as Debian Pipeline.
    """

    # Workflow name (used by create-workflow-template)
    TASK_NAME = "piuparts"

    def __init__(self, work_request: WorkRequest) -> None:
        """Instantiate a Workflow with its database instance."""
        super().__init__(work_request)
        if self.data.backend == BackendType.AUTO:
            self.data.backend = BackendType.UNSHARE

    def populate(self) -> None:
        """Create a Piuparts task for each concrete architecture."""
        # piuparts will be run on the intersection of the provided
        # list of architectures (if any) and the architectures
        # provided in binary_artifacts
        architectures = workflow_utils.get_architectures(
            self, self.data.binary_artifacts
        )
        #  architectures: if set, only run on any of these architecture names
        if self.data.architectures is not None:
            architectures.intersection_update(self.data.architectures)

        if architectures == {"all"}:
            # If only Architecture: all binary packages are provided
            # in binary_artifacts, then piuparts will be run once for
            # arch-all on {arch_all_host_architecture}.
            pass
        else:
            # piuparts will be run grouping arch-all + arch-any
            # together. Not running "all" separately.
            architectures.discard("all")

        # Define piuparts environment
        base_tgz = f"{self.data.vendor}/match:codename={self.data.codename}"
        environment = self.data.environment or base_tgz
        backend = self.data.backend

        for architecture in sorted(architectures):
            # group arch-all + arch-any
            arch_subset_binary_artifacts = (
                workflow_utils.filter_artifact_lookup_by_arch(
                    self,
                    self.data.binary_artifacts,
                    (architecture, "all"),
                )
            )

            # The concrete architecture, or
            # {arch_all_host_architecture} if only Architecture: all
            # binary packages are being checked by this task
            host_architecture = (
                self.data.arch_all_host_architecture
                if architecture == "all"
                else architecture
            )

            extra_repositories = workflow_utils.configure_for_overlay_suite(
                self,
                extra_repositories=self.data.extra_repositories,
                vendor=self.data.vendor,
                codename=self.data.codename,
                environment=environment,
                backend=self.data.backend,
                architecture=host_architecture,
            )

            # Create work-request (idempotent)
            wr = self.work_request_ensure_child(
                task_name="piuparts",
                task_data=PiupartsData(
                    backend=backend,
                    environment=environment,
                    input=PiupartsDataInput(
                        binary_artifacts=arch_subset_binary_artifacts,
                    ),
                    host_architecture=host_architecture,
                    base_tgz=base_tgz,
                    extra_repositories=extra_repositories,
                ),
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Piuparts {architecture}",
                    step=f"piuparts-{architecture}",
                ),
            )

            # Any of the lookups in input.binary_artifacts may result
            # in promises, and in that case the workflow adds
            # corresponding dependencies. Binary promises must include
            # an architecture field in their data.
            self.requires_artifact(wr, arch_subset_binary_artifacts)

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: source package names of ``binary_artifacts`` separated
          by spaces
        """
        binaries = lookup_multiple(
            self.data.binary_artifacts,
            workflow_root=self.work_request.get_workflow_root(),
            workspace=self.workspace,
            user=self.work_request.created_by,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        )
        source_package_names = workflow_utils.get_source_package_names(
            binaries,
            configuration_key="binary_artifacts",
            artifact_expected_categories=(
                ArtifactCategory.BINARY_PACKAGE,
                ArtifactCategory.BINARY_PACKAGES,
                ArtifactCategory.UPLOAD,
            ),
        )

        return BaseDynamicTaskData(subject=" ".join(source_package_names))

    def get_label(self) -> str:
        """Return the task label."""
        return "run piuparts"
