# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Autopkgtest workflow."""

from datetime import datetime, timedelta
from datetime import timezone as tz
from functools import cached_property
from typing import Any, TypeAlias

from django.db.models import Window
from django.db.models.fields.json import KT
from django.db.models.functions import Rank
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    DebianAutopkgtestResultStatus,
    TaskTypes,
)
from debusine.db.models import Artifact, CollectionItem, WorkRequest
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.base import WorkflowValidationError
from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.regression_tracking import (
    RegressionTrackingWorkflow,
)
from debusine.tasks.models import (
    ActionUpdateCollectionWithArtifacts,
    AutopkgtestData,
    AutopkgtestInput,
    BackendType,
    BaseDynamicTaskData,
    OutputData,
    RegressionAnalysis,
    RegressionAnalysisStatus,
)
from debusine.tasks.server import TaskDatabaseInterface


class AutopkgtestWorkflow(
    RegressionTrackingWorkflow[AutopkgtestWorkflowData, BaseDynamicTaskData]
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
        super().validate_input()
        # binary_artifacts is validated by accessing self.architectures.
        self.architectures

    def _has_current_reference_qa_result(self, architecture: str) -> bool:
        """
        Return True iff we have a current reference QA result.

        An autopkgtest analysis is outdated if:

        * either the underlying source or binary packages are outdated (i.e.
          have different version numbers) compared to what's available in
          the ``debian:suite`` collection
        * or the timestamp of the analysis is older than 30 days compared to
          the ``Date`` timestamp of the ``debian:suite`` collection

        Otherwise, it is current.
        """
        # This method is only called when update_qa_results is True, in
        # which case this is checked by a model validator.
        assert self.reference_qa_results is not None

        source_data = workflow_utils.source_package_data(self)
        latest_result = self.reference_qa_results.manager.lookup(
            f"latest:autopkgtest_{source_data.name}_{architecture}"
        )
        return (
            latest_result is not None
            and latest_result.data["version"] == source_data.version
            and (
                self.qa_suite_changed is None
                or datetime.fromtimestamp(
                    latest_result.data["timestamp"], tz=tz.utc
                )
                >= self.qa_suite_changed - timedelta(days=30)
            )
        )

    def _populate_single(self, architecture: str) -> None:
        """Create an autopkgtest work request for a single architecture."""
        assert self.work_request is not None

        if (
            self.data.update_qa_results
            and self._has_current_reference_qa_result(architecture)
        ):
            return

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
            try_variant="autopkgtest",
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
        workflow_data_kwargs: dict[str, Any] = {}
        if self.data.update_qa_results:
            # When updating reference results for regression tracking, task
            # failures never cause the parent workflow or dependent tasks to
            # fail.
            workflow_data_kwargs["allow_failure"] = True
        wr = self.work_request_ensure_child(
            task_name="autopkgtest",
            task_data=task_data,
            workflow_data=WorkRequestWorkflowData(
                display_name=f"autopkgtest {architecture}",
                step=f"autopkgtest-{architecture}",
                **workflow_data_kwargs,
            ),
        )
        self.requires_artifact(wr, source_artifact)
        self.requires_artifact(wr, filtered_binary_artifacts)
        self.requires_artifact(wr, filtered_context_artifacts)
        promise_name = f"{self.data.prefix}autopkgtest-{architecture}"
        self.provides_artifact(
            wr,
            ArtifactCategory.AUTOPKGTEST,
            f"{self.data.prefix}autopkgtest-{architecture}",
        )

        if self.data.update_qa_results:
            # Checked by a model validator.
            assert self.data.reference_qa_results is not None

            source_data = workflow_utils.source_package_data(self)

            # Back off if another workflow gets there first.
            self.skip_if_qa_result_changed(
                wr,
                package=source_data.name,
                architecture=architecture,
                promise_name=promise_name,
            )

            # Record results in the reference collection.
            action = ActionUpdateCollectionWithArtifacts(
                collection=self.data.reference_qa_results,
                variables={
                    "package": source_data.name,
                    "version": source_data.version,
                    "architecture": architecture,
                    "timestamp": int(
                        (self.qa_suite_changed or timezone.now()).timestamp()
                    ),
                    "work_request_id": wr.id,
                },
                artifact_filters={"category": ArtifactCategory.AUTOPKGTEST},
            )
            wr.add_event_reaction("on_success", action)
            wr.add_event_reaction("on_failure", action)

        if self.data.enable_regression_tracking:
            # Checked by a model validator.
            assert self.data.reference_prefix

            regression_analysis = WorkRequest.objects.create_workflow_callback(
                parent=self.work_request,
                step="regression-analysis",
                display_name=f"Regression analysis for {architecture}",
                visible=False,
            )
            try:
                self.requires_artifact(
                    regression_analysis,
                    f"internal@collections/name:"
                    f"{self.data.reference_prefix}autopkgtest-{architecture}",
                )
            except KeyError:
                pass
            regression_analysis.add_dependency(wr)

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

    @staticmethod
    def compare_qa_results_fine_grained(
        reference: Artifact | None, new: Artifact | None
    ) -> dict[str, str]:
        """Do a fine-grained comparison of two QA results."""
        reference_results = (
            {} if reference is None else reference.data["results"]
        )
        new_results = {} if new is None else new.data["results"]
        DARS: TypeAlias = DebianAutopkgtestResultStatus
        details = {}
        for test_name in set(reference_results) | set(new_results):
            match reference_results.get(test_name), new_results.get(test_name):
                case (
                    {"status": DARS.PASS | DARS.SKIP} | None,
                    {"status": DARS.FAIL},
                ):
                    details[test_name] = "regression"
                case (
                    {"status": DARS.FAIL | DARS.FLAKY},
                    {"status": DARS.PASS | DARS.SKIP} | None,
                ):
                    details[test_name] = "improvement"
                case _:
                    details[test_name] = "stable"
        return details

    def callback_regression_analysis(self) -> None:
        """
        Analyze regressions compared to reference results.

        This is called once for each architecture, but updates the whole
        analysis for all architectures each time.  This is partly for
        simplicity and robustness (we don't need to work out how to combine
        the new analysis with a previous one), and partly to make it easier
        to handle cases where there isn't a one-to-one mapping between the
        reference results and the new results.
        """
        # This workflow callback is only created when
        # enable_regression_tracking is true, in which case this is checked
        # by a model validator.
        assert self.reference_qa_results is not None

        source_data = workflow_utils.source_package_data(self)
        # Select the newest result for each architecture.
        reference_artifacts = {
            result.data["architecture"]: result.artifact
            for result in self.reference_qa_results.child_items.filter(
                child_type=CollectionItem.Types.ARTIFACT,
                artifact__isnull=False,
                data__task_name="autopkgtest",
                data__package=source_data.name,
                data__version=source_data.version,
                data__has_key="architecture",
            )
            .annotate(
                rank_by_architecture=Window(
                    Rank(),
                    partition_by=KT("data__architecture"),
                    order_by="-data__timestamp",
                )
            )
            .filter(rank_by_architecture=1)
            .select_related("artifact")
        }
        new_artifacts = {
            artifact.data["architecture"]: artifact
            for artifact in Artifact.objects.filter(
                created_by_work_request__in=(
                    self.work_request.children.unsuperseded()
                    .terminated()
                    .filter(task_type=TaskTypes.WORKER, task_name="autopkgtest")
                ),
                category=ArtifactCategory.AUTOPKGTEST,
                data__has_key="architecture",
            ).select_related("created_by_work_request")
        }

        output_data = self.work_request.output_data or OutputData()
        output_data.regression_analysis = {}
        for architecture in sorted(
            set(reference_artifacts) | set(new_artifacts)
        ):
            reference = reference_artifacts.get(architecture)
            reference_wr = (
                None if reference is None else reference.created_by_work_request
            )
            new = new_artifacts.get(architecture)
            new_wr = None if new is None else new.created_by_work_request

            status = self.compare_qa_results(reference_wr, new_wr)
            details = self.compare_qa_results_fine_grained(reference, new)
            if status == RegressionAnalysisStatus.STABLE:
                if "regression" in details.values():
                    status = RegressionAnalysisStatus.REGRESSION
                elif "improvement" in details.values():
                    status = RegressionAnalysisStatus.IMPROVEMENT
                else:
                    status = RegressionAnalysisStatus.STABLE

            output_data.regression_analysis[architecture] = RegressionAnalysis(
                original_url=(
                    None if reference is None else reference.get_absolute_url()
                ),
                new_url=(None if new is None else new.get_absolute_url()),
                status=status,
                details=details,
            )

        self.work_request.output_data = output_data
        self.work_request.save()

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
