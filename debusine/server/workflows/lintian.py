# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""lintian workflow."""

from typing import Any

from debian.debian_support import Version
from django.utils import timezone

from debusine.artifacts import LintianArtifact
from debusine.artifacts.models import ArtifactCategory
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.models import (
    LintianWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.regression_tracking import (
    RegressionTrackingWorkflow,
)
from debusine.tasks.models import (
    ActionUpdateCollectionWithArtifacts,
    BackendType,
    BaseDynamicTaskData,
    LintianData,
    LintianFailOnSeverity,
    LintianInput,
    LintianOutput,
    LookupMultiple,
    LookupSingle,
)
from debusine.tasks.server import TaskDatabaseInterface


class LintianWorkflow(
    RegressionTrackingWorkflow[LintianWorkflowData, BaseDynamicTaskData]
):
    """Lintian workflow."""

    TASK_NAME = "lintian"

    def _has_current_reference_qa_result(self, architecture: str) -> bool:
        """
        Return True iff we have a current reference QA result.

        A lintian analysis is outdated if:

        * either the underlying source or binary packages are outdated (i.e.
          have different version numbers) compared to what's available in
          the ``debian:suite`` collection
        * or the lintian version used to perform the analysis is older than
          the version available in the ``debian:suite`` collection

        Otherwise, it is current.
        """
        # This method is only called when update_qa_results is True, in
        # which case these are checked by a model validator.
        assert self.qa_suite is not None
        assert self.reference_qa_results is not None

        source_data = workflow_utils.source_package_data(self)
        latest_result = self.reference_qa_results.manager.lookup(
            f"latest:lintian_{source_data.name}_{architecture}"
        )
        reference_lintian = self.qa_suite.manager.lookup(
            f"binary:lintian_{architecture}"
        )
        return (
            latest_result is not None
            and latest_result.artifact is not None
            and latest_result.data["version"] == source_data.version
            and (
                reference_lintian is None
                or Version(
                    LintianArtifact.create_data(
                        latest_result.artifact.data
                    ).summary.lintian_version
                )
                >= Version(reference_lintian.data["version"])
            )
        )

    def populate(self) -> None:
        """Create work requests."""
        architectures = workflow_utils.get_architectures(
            self, self.data.binary_artifacts
        )

        if (data_archs := self.data.architectures) is not None:
            architectures.intersection_update(data_archs)

        if architectures != {"all"}:
            architectures = architectures - {"all"}

        if not architectures and not self.data.binary_artifacts:
            # There are no architectures to run on, but that's because the
            # workflow was started with an empty `binary_artifacts`.  In
            # that case there's no point waiting for binary packages to be
            # built, so we might as well just analyze the source package.
            architectures = {"all"}

        # Pick a preferred architecture to produce the common source and
        # binary-all analysis artifacts, to avoid redundancy.  Note that we
        # still pass source and binary-all artifacts to all child work
        # requests so that `lintian` has the best tag coverage available.
        source_all_analysis_architecture: str | None = None
        if "all" in architectures:
            source_all_analysis_architecture = "all"
        elif self.data.arch_all_host_architecture in architectures:
            source_all_analysis_architecture = (
                self.data.arch_all_host_architecture
            )
        elif architectures:
            # Alphabetical sorting doesn't necessarily produce good results
            # here, but this is already a last-ditch fallback case so we
            # don't worry about it too much.  If it turns out to be wrong,
            # it's probably best to just set `arch_all_host_architecture`.
            source_all_analysis_architecture = sorted(architectures)[0]

        environment = f"{self.data.vendor}/match:codename={self.data.codename}"

        for arch in architectures:
            output = self.data.output.copy()
            if arch != source_all_analysis_architecture:
                output.source_analysis = False
                output.binary_all_analysis = False

            if (
                not output.source_analysis
                and not output.binary_all_analysis
                and not output.binary_any_analysis
            ):
                continue

            source_artifact = (
                workflow_utils.locate_debian_source_package_lookup(
                    self, "source_artifact", self.data.source_artifact
                )
            )
            filtered_binary_artifacts = (
                workflow_utils.filter_artifact_lookup_by_arch(
                    self, self.data.binary_artifacts, (arch, "all")
                )
            )

            self._populate_lintian(
                source_artifact=source_artifact,
                binary_artifacts=filtered_binary_artifacts,
                output=output,
                host_architecture=(
                    self.data.arch_all_host_architecture
                    if arch == "all"
                    else arch
                ),
                environment=environment,
                backend=self.data.backend,
                architecture=arch,
                include_tags=self.data.include_tags,
                exclude_tags=self.data.exclude_tags,
                fail_on_severity=self.data.fail_on_severity,
                target_distribution=f"{self.data.vendor}:{self.data.codename}",
            )

    def _populate_lintian(
        self,
        *,
        source_artifact: LookupSingle,
        binary_artifacts: LookupMultiple,
        output: LintianOutput,
        host_architecture: str,
        environment: str,
        backend: BackendType,
        include_tags: list[str],
        exclude_tags: list[str],
        fail_on_severity: LintianFailOnSeverity,
        architecture: str,
        target_distribution: str,
    ) -> None:
        """Create work request for Lintian for a specific architecture."""
        if (
            self.data.update_qa_results
            and self._has_current_reference_qa_result(architecture)
        ):
            return

        workflow_data_kwargs: dict[str, Any] = {}
        if self.data.update_qa_results:
            # When updating reference results for regression tracking, task
            # failures never cause the parent workflow or dependent tasks to
            # fail.
            workflow_data_kwargs["allow_failure"] = True
        wr = self.work_request_ensure_child(
            task_name="lintian",
            task_data=LintianData(
                input=LintianInput(
                    source_artifact=source_artifact,
                    binary_artifacts=binary_artifacts,
                ),
                output=output,
                host_architecture=host_architecture,
                environment=environment,
                backend=backend,
                include_tags=include_tags,
                exclude_tags=exclude_tags,
                fail_on_severity=fail_on_severity,
                target_distribution=target_distribution,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=f"Lintian for {architecture}",
                step=f"lintian-{architecture}",
                **workflow_data_kwargs,
            ),
        )
        self.requires_artifact(wr, source_artifact)
        self.requires_artifact(wr, binary_artifacts)

        if self.data.update_qa_results:
            # Checked by a model validator.
            assert self.data.reference_qa_results is not None

            source_data = workflow_utils.source_package_data(self)

            # Back off if another workflow gets there first.
            self.skip_if_qa_result_changed(
                wr, package=source_data.name, architecture=architecture
            )

            # Record results in the reference collection.
            action = ActionUpdateCollectionWithArtifacts(
                collection=self.data.reference_qa_results,
                variables={
                    "package": source_data.name,
                    "version": source_data.version,
                    # While this field is technically optional at the
                    # moment, it will be present in any newly-created
                    # artifacts.
                    "$architecture": "architecture",
                    "timestamp": int(
                        (self.qa_suite_changed or timezone.now()).timestamp()
                    ),
                    "work_request_id": wr.id,
                },
                artifact_filters={"category": ArtifactCategory.LINTIAN},
            )
            wr.add_event_reaction("on_success", action)
            wr.add_event_reaction("on_failure", action)

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
        return "run lintian"
