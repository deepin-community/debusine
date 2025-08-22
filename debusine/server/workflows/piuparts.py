# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Piuparts workflow."""

from typing import Any

from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, BareDataCategory
from debusine.client.models import LookupChildType
from debusine.db.models import WorkRequest
from debusine.server.collections.lookup import lookup_multiple
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.models import (
    PiupartsWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.regression_tracking import (
    RegressionTrackingWorkflow,
)
from debusine.tasks.models import (
    ActionUpdateCollectionWithData,
    BackendType,
    BaseDynamicTaskData,
    PiupartsData,
    PiupartsDataInput,
)
from debusine.tasks.server import TaskDatabaseInterface


class PiupartsWorkflow(
    RegressionTrackingWorkflow[PiupartsWorkflowData, BaseDynamicTaskData]
):
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

    def _has_current_reference_qa_result(self, architecture: str) -> bool:
        """
        Return True iff we have a current reference QA result.

        A piuparts analysis is outdated if the underlying binary packages
        are outdated (i.e.  have smaller version numbers) compared to what's
        available in the ``debian:suite`` collection

        Otherwise, it is current.
        """
        # This method is only called when update_qa_results is True, in
        # which case these are checked by a model validator.
        assert self.qa_suite is not None
        assert self.reference_qa_results is not None

        source_data = workflow_utils.source_package_data(self)
        latest_result = self.reference_qa_results.manager.lookup(
            f"latest:piuparts_{source_data.name}_{architecture}"
        )
        return (
            latest_result is not None
            and latest_result.data["version"] == source_data.version
        )

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
            if (
                self.data.update_qa_results
                and self._has_current_reference_qa_result(architecture)
            ):
                continue

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
                try_variant="piuparts",
            )

            # Create work-request (idempotent)
            workflow_data_kwargs: dict[str, Any] = {}
            if self.data.update_qa_results:
                # When updating reference results for regression tracking,
                # task failures never cause the parent workflow or dependent
                # tasks to fail.
                workflow_data_kwargs["allow_failure"] = True
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
                    **workflow_data_kwargs,
                ),
            )

            # Any of the lookups in input.binary_artifacts may result
            # in promises, and in that case the workflow adds
            # corresponding dependencies. Binary promises must include
            # an architecture field in their data.
            self.requires_artifact(wr, arch_subset_binary_artifacts)

            if self.data.update_qa_results:
                # Checked by a model validator.
                assert self.data.reference_qa_results is not None

                source_data = workflow_utils.source_package_data(self)

                # Back off if another workflow gets there first.
                self.skip_if_qa_result_changed(
                    wr, package=source_data.name, architecture=architecture
                )

                # Record results in the reference collection.
                action = ActionUpdateCollectionWithData(
                    collection=self.data.reference_qa_results,
                    category=BareDataCategory.QA_RESULT,
                    data={
                        "package": source_data.name,
                        "version": source_data.version,
                        "architecture": architecture,
                        "timestamp": int(
                            (
                                self.qa_suite_changed or timezone.now()
                            ).timestamp()
                        ),
                        "work_request_id": wr.id,
                    },
                )
                wr.add_event_reaction("on_success", action)
                wr.add_event_reaction("on_failure", action)

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
