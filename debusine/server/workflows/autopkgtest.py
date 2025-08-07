# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Autopkgtest workflow."""

from functools import cached_property

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import WorkRequest
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.base import Workflow, WorkflowValidationError
from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    AutopkgtestData,
    AutopkgtestInput,
    BackendType,
    BaseDynamicTaskData,
)
from debusine.tasks.server import TaskDatabaseInterface


class AutopkgtestWorkflow(
    Workflow[AutopkgtestWorkflowData, BaseDynamicTaskData]
):
    """Run autopkgtests for a single source on a set of architectures."""

    TASK_NAME = "autopkgtest"

    def __init__(self, work_request: WorkRequest) -> None:
        """Instantiate a Workflow with its database instance."""
        super().__init__(work_request)
        if self.data.backend == BackendType.AUTO:
            self.data.backend = BackendType.UNSHARE

    @cached_property
    def architectures(self) -> set[str]:
        """Concrete architectures to run tests on."""
        assert self.work_request is not None
        assert self.workspace is not None

        try:
            architectures = workflow_utils.get_architectures(
                self, self.data.binary_artifacts
            )
        except ValueError as e:
            raise WorkflowValidationError(str(e)) from e

        # If the input only contains architecture-independent binary
        # packages, then run tests on {arch_all_host_architecture}.  If it
        # contains a mix of architecture-independent and
        # architecture-dependent binary packages, then running tests on any
        # of the concrete architectures will do.
        if architectures == {"all"}:
            architectures = {self.data.arch_all_host_architecture}
        architectures.discard("all")

        if self.data.architectures:
            architectures &= set(self.data.architectures)

        return architectures

    def validate_input(self) -> None:
        """Thorough validation of input data."""
        # binary_artifacts is validated by accessing self.architectures.
        self.architectures

    def _populate_single(self, architecture: str) -> None:
        """Create an autopkgtest work request for a single architecture."""
        assert self.work_request is not None

        source_artifact = workflow_utils.locate_debian_source_package_lookup(
            self, "source_artifact", self.data.source_artifact
        )
        filtered_binary_artifacts = (
            workflow_utils.filter_artifact_lookup_by_arch(
                self, self.data.binary_artifacts, (architecture, "all")
            )
        )
        filtered_context_artifacts = (
            workflow_utils.filter_artifact_lookup_by_arch(
                self, self.data.context_artifacts, (architecture, "all")
            )
        )

        environment = f"{self.data.vendor}/match:codename={self.data.codename}"
        extra_repositories = workflow_utils.configure_for_overlay_suite(
            self,
            extra_repositories=self.data.extra_repositories,
            vendor=self.data.vendor,
            codename=self.data.codename,
            environment=environment,
            backend=self.data.backend,
            architecture=architecture,
        )

        task_data = AutopkgtestData(
            input=AutopkgtestInput(
                source_artifact=source_artifact,
                binary_artifacts=filtered_binary_artifacts,
                context_artifacts=filtered_context_artifacts,
            ),
            host_architecture=architecture,
            environment=environment,
            backend=self.data.backend,
            include_tests=self.data.include_tests,
            exclude_tests=self.data.exclude_tests,
            extra_repositories=extra_repositories,
            debug_level=self.data.debug_level,
            extra_environment=self.data.extra_environment,
            needs_internet=self.data.needs_internet,
            fail_on=self.data.fail_on,
            timeout=self.data.timeout,
        )
        wr = self.work_request_ensure_child(
            task_name="autopkgtest",
            task_data=task_data,
            workflow_data=WorkRequestWorkflowData(
                display_name=f"autopkgtest {architecture}",
                step=f"autopkgtest-{architecture}",
            ),
        )
        self.requires_artifact(wr, source_artifact)
        self.requires_artifact(wr, filtered_binary_artifacts)
        self.requires_artifact(wr, filtered_context_artifacts)
        self.provides_artifact(
            wr,
            ArtifactCategory.AUTOPKGTEST,
            f"{self.data.prefix}autopkgtest-{architecture}",
        )

    def populate(self) -> None:
        """Create autopkgtest work requests for all architectures."""
        assert self.work_request is not None

        children = self.work_request.children.all()
        existing_architectures = {
            child.task_data["host_architecture"] for child in children
        }

        # We don't expect there to be a scenario where there are existing
        # work requests that we no longer need.  Leave an assertion so that
        # if this happens we can work out what to do in that corner case.
        if old_architectures := existing_architectures - self.architectures:
            raise AssertionError(
                f"Unexpected work requests found: {old_architectures}"
            )

        if new_architectures := self.architectures - existing_architectures:
            for architecture in new_architectures:
                self._populate_single(architecture)

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
        return "run autopkgtests"
