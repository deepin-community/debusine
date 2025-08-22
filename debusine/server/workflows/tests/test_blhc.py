# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the blhc workflow."""
from typing import Any, ClassVar, NewType

from debusine.artifacts.models import ArtifactCategory, TaskTypes
from debusine.db.models import Artifact, TaskDatabase, WorkRequest, Workspace
from debusine.server.workflows import BlhcWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    BlhcWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks.models import (
    BaseDynamicTaskData,
    BlhcFlags,
    LookupSingle,
    SbuildData,
    SbuildInput,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry

Architecture = NewType("Architecture", str)


class BlhcWorkflowTests(TestCase):
    """Unit tests for :py:class:`BlhcWorkflow`."""

    package_build_log_artifacts: ClassVar[dict[Architecture, Artifact]]
    source_artifact: ClassVar[Artifact]

    workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()

        cls.workspace = cls.playground.get_default_workspace()

        cls.source_artifact = cls.playground.create_source_artifact(
            name="hello", version="1.0.0"
        )

        cls.package_build_log_artifacts = {}
        for arch in ["amd64", "arm64"]:
            artifact = cls.playground.create_build_log_artifact(build_arch=arch)

            cls.package_build_log_artifacts[Architecture(arch)] = artifact

    def orchestrate(
        self,
        *,
        task_data: BlhcWorkflowData,
        source_artifact: Artifact,
    ) -> WorkRequest:
        """Create and orchestrate a BlhcWorkflow."""

        class ExamplePipeline(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Pipeline workflow."""

            def populate(self) -> None:
                """Populate the pipeline."""
                for arch in ["amd64", "arm64"]:
                    sbuild = self.work_request_ensure_child(
                        task_type=TaskTypes.WORKER,
                        task_name="sbuild",
                        task_data=SbuildData(
                            input=SbuildInput(
                                source_artifact=source_artifact.id
                            ),
                            environment="debian:trixie",
                            host_architecture=arch,
                        ),
                        workflow_data=WorkRequestWorkflowData(
                            display_name=f"Build {arch}", step=f"build-{arch}"
                        ),
                    )
                    self.provides_artifact(
                        sbuild,
                        ArtifactCategory.PACKAGE_BUILD_LOG,
                        name=f"log-{arch}",
                        data={
                            "architecture": arch,
                        },
                    )

                blhc = self.work_request_ensure_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="blhc",
                    task_data=task_data,
                    workflow_data=WorkRequestWorkflowData(
                        display_name="blhc",
                        step="blhc",
                    ),
                )
                blhc.mark_running()
                orchestrate_workflow(blhc)

        root = self.playground.create_workflow(task_name="examplepipeline")

        root.mark_running()
        orchestrate_workflow(root)

        return root

    def create_blhc_workflow(
        self,
        *,
        package_build_logs: list[LookupSingle] | None = None,
        extra_flags: list[BlhcFlags] | None = None,
    ) -> WorkRequest:
        """
        Create and schedule a Blhc workflow.

        :param package_build_logs: build logs for the workflow. If None
          use all the expected promises by setUpTestData()
        :param extra_flags: extra flags for the individual Blhc tasks
        """
        if package_build_logs is None:
            package_build_logs = []
            for package_build_log in self.package_build_log_artifacts.values():
                package_build_logs.append(package_build_log.id)

        task_data: dict[str, Any] = {
            "package_build_logs": package_build_logs,
            "extra_flags": [] if extra_flags is None else extra_flags,
        }

        example_pipeline = self.orchestrate(
            task_data=BlhcWorkflowData(**task_data),
            source_artifact=self.source_artifact,
        )
        work_request = example_pipeline.children.get(
            task_name="blhc", task_type=TaskTypes.WORKFLOW
        )
        return work_request

    @preserve_task_registry()
    def test_populate(self) -> None:
        blhc_workflow_work_request = self.create_blhc_workflow(
            extra_flags=[BlhcFlags.BINDNOW, BlhcFlags.LINE_NUMBERS]
        )

        blhc_work_requests = blhc_workflow_work_request.children.all()

        # One work request per architecture
        self.assertEqual(
            blhc_work_requests.count(), len(self.package_build_log_artifacts)
        )

        amd64_work_request = blhc_work_requests.get(
            workflow_data_json__step="blhc-amd64"
        )
        self.assertEqual(
            amd64_work_request.workflow_data_json,
            {
                "display_name": "build log hardening check for amd64",
                "step": "blhc-amd64",
            },
        )
        self.assertEqual(
            amd64_work_request.workflow_data.display_name,
            "build log hardening check for amd64",
        )

        amd64_artifact = self.package_build_log_artifacts[Architecture("amd64")]
        self.assertEqual(
            amd64_work_request.task_data,
            {
                "input": {"artifact": f"{amd64_artifact.id}@artifacts"},
                "extra_flags": [BlhcFlags.BINDNOW, BlhcFlags.LINE_NUMBERS],
            },
        )

    @preserve_task_registry()
    def test_compute_dynamic_data(self) -> None:
        package_build_log_1 = self.playground.create_build_log_artifact(
            source="hello"
        )
        package_build_log_2 = self.playground.create_build_log_artifact(
            source="hello-traditional"
        )

        work_request = self.create_blhc_workflow(
            package_build_logs=[
                f"{package_build_log_1.id}@artifacts",
                f"{package_build_log_2.id}@artifacts",
            ]
        )
        workflow = BlhcWorkflow(work_request)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(work_request)),
            BaseDynamicTaskData(subject="hello hello-traditional"),
        )

    @preserve_task_registry()
    def test_validate_input(self) -> None:
        # validate_input() is called and does not raise
        self.create_blhc_workflow()

    @preserve_task_registry()
    def test_get_label(self) -> None:
        w = self.create_blhc_workflow()
        self.assertEqual(w.get_label(), "run blhc")
