# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""qa workflow."""

from debusine.client.models import LookupChildType
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows import Workflow, workflow_utils
from debusine.server.workflows.base import (
    WorkflowValidationError,
    orchestrate_workflow,
)
from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    LintianWorkflowData,
    PiupartsWorkflowData,
    QAWorkflowData,
    ReverseDependenciesAutopkgtestWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    LintianFailOnSeverity,
    LookupMultiple,
    LookupSingle,
    TaskTypes,
)
from debusine.tasks.server import TaskDatabaseInterface


class QAWorkflow(Workflow[QAWorkflowData, BaseDynamicTaskData]):
    """QA workflow."""

    TASK_NAME = "qa"

    def validate_input(self) -> None:
        """Thorough validation of input data."""
        if self.data.reverse_dependencies_autopkgtest_suite is not None:
            try:
                lookup_single(
                    self.data.reverse_dependencies_autopkgtest_suite,
                    self.workspace,
                    user=self.work_request.created_by,
                    workflow_root=self.work_request.get_workflow_root(),
                    expect_type=LookupChildType.COLLECTION,
                ).collection
            except LookupError as e:
                raise WorkflowValidationError(str(e)) from e

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

    def populate(self) -> None:
        """Create work requests."""
        if (data_archs := self.data.architectures) is not None:
            architectures = set(data_archs)
        else:
            architectures = workflow_utils.get_available_architectures(
                self, vendor=self.data.vendor, codename=self.data.codename
            )

        if (
            data_archs_allowlist := self.data.architectures_allowlist
        ) is not None:
            architectures.intersection_update(data_archs_allowlist)

        if (
            data_archs_denylist := self.data.architectures_denylist
        ) is not None:
            architectures.difference_update(data_archs_denylist)

        effective_architectures = sorted(architectures)

        filtered_binary_artifacts = (
            workflow_utils.filter_artifact_lookup_by_arch(
                self, self.data.binary_artifacts, architectures
            )
        )

        if self.data.enable_autopkgtest:
            self._populate_autopkgtest(
                source_artifact=self.data.source_artifact,
                binary_artifacts=filtered_binary_artifacts,
                vendor=self.data.vendor,
                codename=self.data.codename,
                backend=self.data.autopkgtest_backend,
                architectures=effective_architectures,
                arch_all_host_architecture=self.data.arch_all_host_architecture,
            )

        if self.data.enable_reverse_dependencies_autopkgtest:
            # Checked by
            # QAWorkflowData.check_reverse_dependencies_autopkgtest_consistency.
            assert self.data.reverse_dependencies_autopkgtest_suite is not None
            self._populate_reverse_dependencies_autopkgtest(
                source_artifact=self.data.source_artifact,
                binary_artifacts=filtered_binary_artifacts,
                suite_collection=(
                    self.data.reverse_dependencies_autopkgtest_suite
                ),
                vendor=self.data.vendor,
                codename=self.data.codename,
                backend=self.data.autopkgtest_backend,
                architectures=effective_architectures,
                arch_all_host_architecture=self.data.arch_all_host_architecture,
            )

        if self.data.enable_lintian:
            self._populate_lintian(
                source_artifact=self.data.source_artifact,
                binary_artifacts=filtered_binary_artifacts,
                vendor=self.data.vendor,
                codename=self.data.codename,
                backend=self.data.lintian_backend,
                architectures=effective_architectures,
                fail_on_severity=self.data.lintian_fail_on_severity,
            )

        if self.data.enable_piuparts:
            self._populate_piuparts(
                binary_artifacts=filtered_binary_artifacts,
                vendor=self.data.vendor,
                codename=self.data.codename,
                architectures=effective_architectures,
                backend=self.data.piuparts_backend,
                environment=self.data.piuparts_environment,
                arch_all_host_architecture=self.data.arch_all_host_architecture,
            )

    def _populate_autopkgtest(
        self,
        *,
        source_artifact: LookupSingle,
        binary_artifacts: LookupMultiple,
        vendor: str,
        codename: str,
        backend: BackendType,
        architectures: list[str],
        arch_all_host_architecture: str,
    ) -> None:
        """Create work request for autopkgtest workflow."""
        wr = self.work_request_ensure_child(
            task_name="autopkgtest",
            task_type=TaskTypes.WORKFLOW,
            task_data=AutopkgtestWorkflowData(
                source_artifact=source_artifact,
                binary_artifacts=binary_artifacts,
                vendor=vendor,
                codename=codename,
                backend=backend,
                architectures=architectures,
                arch_all_host_architecture=arch_all_host_architecture,
                extra_repositories=self.data.extra_repositories,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="autopkgtest",
                step="autopkgtest",
            ),
        )
        # The autopkgtest workflow's children will have dependencies on the
        # work requests creating source_artifact and binary_artifacts, but the
        # autopkgtest workflow itself doesn't need that in order to populate
        # itself.
        wr.mark_running()
        orchestrate_workflow(wr)

    def _populate_reverse_dependencies_autopkgtest(
        self,
        *,
        source_artifact: LookupSingle,
        binary_artifacts: LookupMultiple,
        suite_collection: LookupSingle,
        vendor: str,
        codename: str,
        backend: BackendType,
        architectures: list[str],
        arch_all_host_architecture: str,
    ) -> None:
        """Create work request for reverse_dependencies_autopkgtest workflow."""
        wr = self.work_request_ensure_child(
            task_name="reverse_dependencies_autopkgtest",
            task_type=TaskTypes.WORKFLOW,
            task_data=ReverseDependenciesAutopkgtestWorkflowData(
                source_artifact=source_artifact,
                binary_artifacts=binary_artifacts,
                suite_collection=suite_collection,
                vendor=vendor,
                codename=codename,
                backend=backend,
                architectures=architectures,
                arch_all_host_architecture=arch_all_host_architecture,
                extra_repositories=self.data.extra_repositories,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="autopkgtests of reverse-dependencies",
                step="reverse-dependencies-autopkgtest",
            ),
        )
        # The reverse_dependencies_autopkgtest workflow's descendants will have
        # dependencies on the work requests creating source_artifact and
        # binary_artifacts, but the reverse_dependencies_autopkgtest workflow
        # itself doesn't need that in order to populate itself.
        wr.mark_running()
        orchestrate_workflow(wr)

    def _populate_lintian(
        self,
        *,
        source_artifact: LookupSingle,
        binary_artifacts: LookupMultiple,
        vendor: str,
        codename: str,
        backend: BackendType,
        architectures: list[str],
        fail_on_severity: LintianFailOnSeverity,
    ) -> None:
        """Create work request for lintian workflow."""
        wr = self.work_request_ensure_child(
            task_name="lintian",
            task_type=TaskTypes.WORKFLOW,
            task_data=LintianWorkflowData(
                source_artifact=source_artifact,
                binary_artifacts=binary_artifacts,
                vendor=vendor,
                codename=codename,
                backend=backend,
                architectures=architectures,
                fail_on_severity=fail_on_severity,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="lintian",
                step="lintian",
            ),
        )
        # The lintian workflow's descendants will have dependencies on the work
        # requests creating source_artifact and binary_artifacts, but the
        # lintian workflow itself doesn't need that in order to populate
        # itself.
        wr.mark_running()
        orchestrate_workflow(wr)

    def _populate_piuparts(
        self,
        *,
        binary_artifacts: LookupMultiple,
        vendor: str,
        codename: str,
        architectures: list[str],
        backend: BackendType,
        environment: LookupSingle | None,
        arch_all_host_architecture: str,
    ) -> None:
        data = PiupartsWorkflowData(
            binary_artifacts=binary_artifacts,
            vendor=vendor,
            codename=codename,
            architectures=architectures,
            backend=backend,
            arch_all_host_architecture=arch_all_host_architecture,
            extra_repositories=self.data.extra_repositories,
        )
        if environment is not None:
            data.environment = environment
        wr = self.work_request_ensure_child(
            task_name="piuparts",
            task_type=TaskTypes.WORKFLOW,
            task_data=data,
            workflow_data=WorkRequestWorkflowData(
                display_name="piuparts",
                step="piuparts",
            ),
        )
        # The piuparts workflow's descendants will have dependencies on the
        # work requests creating binary_artifacts, but the piuparts workflow
        # itself doesn't need that in order to populate itself.
        wr.mark_running()
        orchestrate_workflow(wr)

    def get_label(self) -> str:
        """Return the task label."""
        return "run QA"
