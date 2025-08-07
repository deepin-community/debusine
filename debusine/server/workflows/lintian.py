# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""lintian workflow."""
from debusine.db.models import WorkRequest
from debusine.server.workflows import Workflow, workflow_utils
from debusine.server.workflows.models import (
    LintianWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
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


class LintianWorkflow(Workflow[LintianWorkflowData, BaseDynamicTaskData]):
    """Lintian workflow."""

    TASK_NAME = "lintian"

    def populate(self) -> None:
        """Create work requests."""
        architectures = workflow_utils.get_architectures(
            self, self.data.binary_artifacts
        )

        if (data_archs := self.data.architectures) is not None:
            architectures.intersection_update(data_archs)

        if architectures != {"all"}:
            architectures = architectures - {"all"}

        environment = f"{self.data.vendor}/match:codename={self.data.codename}"

        for arch in architectures:
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
                output=self.data.output,
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
        environment: str,
        backend: BackendType,
        include_tags: list[str],
        exclude_tags: list[str],
        fail_on_severity: LintianFailOnSeverity,
        architecture: str,
        target_distribution: str,
    ) -> WorkRequest:
        """Create work request for Lintian for a specific architecture."""
        wr = self.work_request_ensure_child(
            task_name="lintian",
            task_data=LintianData(
                input=LintianInput(
                    source_artifact=source_artifact,
                    binary_artifacts=binary_artifacts,
                ),
                output=output,
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
            ),
        )
        self.requires_artifact(wr, source_artifact)
        self.requires_artifact(wr, binary_artifacts)

        return wr

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
