# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the lintian workflow."""
from collections.abc import Sequence
from typing import Any, ClassVar

from debusine.artifacts.models import ArtifactCategory
from debusine.db.context import context
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.workflows import LintianWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    LintianWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.tasks.lintian import Lintian
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    LookupMultiple,
    SbuildData,
    SbuildInput,
    TaskTypes,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class LintianWorkflowTests(TestCase):
    """Unit tests for :py:class:`LintianWorkflow`."""

    source_artifact: ClassVar[Artifact]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.source_artifact = cls.playground.create_source_artifact(
            name="hello"
        )

    @context.disable_permission_checks()
    def create_lintian_workflow(
        self,
        *,
        extra_task_data: dict[str, Any],
    ) -> LintianWorkflow:
        """Create a lintian workflow."""
        task_data = {
            "source_artifact": 20,
            "binary_artifacts": [
                "internal@collections/name:build-amd64",
                "internal@collections/name:build-i386",
            ],
            "vendor": "debian",
            "codename": "bookworm",
        }
        task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="lintian", task_data=task_data
        )
        return LintianWorkflow(wr)

    def orchestrate(
        self, task_data: LintianWorkflowData, architectures: Sequence[str]
    ) -> WorkRequest:
        """Create and orchestrate a LintianWorkflow."""

        class ExamplePipeline(
            TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Lintian workflow."""

            def populate(self) -> None:
                """Populate the pipeline."""
                sbuild = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="sbuild",
                    task_data=SbuildWorkflowData(
                        input=SbuildInput(
                            source_artifact=task_data.source_artifact
                        ),
                        target_distribution="debian:sid",
                        architectures=list(architectures) or ["all"],
                    ),
                )

                for arch in architectures:
                    child = sbuild.create_child(
                        task_name="sbuild",
                        task_data=SbuildData(
                            input=SbuildInput(
                                source_artifact=task_data.source_artifact
                            ),
                            host_architecture=arch,
                            environment="debian/match:codename=sid",
                        ),
                    )

                    self.provides_artifact(
                        child,
                        ArtifactCategory.BINARY_PACKAGE,
                        f"build-{arch}",
                        data={"architecture": arch},
                    )

                lintian = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="lintian",
                    task_data=task_data,
                )
                LintianWorkflow(lintian).populate()

        root = self.playground.create_workflow(task_name="examplepipeline")

        root.mark_running()
        orchestrate_workflow(root)

        return root

    @context.disable_permission_checks()
    def create_binary_package(
        self, srcpkg_name: str, srcpkg_version: str, architecture: str
    ) -> Artifact:
        """Create a minimal `debian:binary-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "deb_fields": {"Architecture": architecture},
                "deb_control_files": [],
            },
        )
        return artifact

    @preserve_task_registry()
    def test_populate(self) -> None:
        """Test populate."""
        architectures = ["amd64", "i386", "all"]

        self.create_binary_package("hello", "1.0.0", "arm64")

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                architectures=architectures,
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{arch}"
                        for arch in architectures
                    ]
                ),
                vendor="debian",
                codename="trixie",
            ),
            architectures=architectures,
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        lintians = workflow.children.filter(task_name="lintian")

        # architecture "all" does not have a lintian job
        self.assertEqual(lintians.count(), len(architectures) - 1)

        for architecture in architectures:
            if architecture == "all":
                continue

            lintian = lintians.get(
                workflow_data_json__step=f"lintian-{architecture}"
            )

            self.assertEqual(
                lintian.task_data,
                {
                    "backend": Lintian.DEFAULT_BACKEND,
                    "environment": "debian/match:codename=trixie",
                    "exclude_tags": [],
                    "fail_on_severity": "none",
                    "include_tags": [],
                    "input": {
                        "binary_artifacts": sorted(
                            [
                                f"internal@collections/name:build-{arch}"
                                for arch in (architecture, "all")
                            ]
                        ),
                        "source_artifact": self.source_artifact.id,
                    },
                    "output": {},
                    "target_distribution": "debian:trixie",
                },
            )

            self.assertEqual(
                lintian.workflow_data_json,
                {
                    "display_name": f"Lintian for {architecture}",
                    "step": f"lintian-{architecture}",
                },
            )

            self.assertQuerySetEqual(
                lintian.dependencies.all(),
                set(
                    WorkRequest.objects.filter(
                        task_name="sbuild",
                        task_data__host_architecture__in=(architecture, "all"),
                    )
                ),
                ordered=False,
            )

    @preserve_task_registry()
    def test_populate_without_architectures(self) -> None:
        """Test populate."""
        binary_artifact = self.create_binary_package("hello", "1.0.0", "all")

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
            ),
            architectures=("all",),
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        lintian = workflow.children.get(task_name="lintian")

        self.assertEqual(
            lintian.task_data,
            {
                "environment": "debian/match:codename=trixie",
                "exclude_tags": [],
                "fail_on_severity": "none",
                "include_tags": [],
                "input": {
                    "binary_artifacts": [f"{binary_artifact.id}@artifacts"],
                    "source_artifact": self.source_artifact.id,
                },
                "backend": BackendType.UNSHARE,
                "output": {},
                "target_distribution": "debian:trixie",
            },
        )

    @preserve_task_registry()
    def test_populate_upload(self) -> None:
        """The workflow accepts debian:upload source artifacts."""
        architectures = ["amd64", "i386", "all"]
        upload_artifacts = self.playground.create_upload_artifacts()

        self.create_binary_package("hello", "1.0.0", "arm64")

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                architectures=architectures,
                source_artifact=upload_artifacts.upload.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{arch}"
                        for arch in architectures
                    ]
                ),
                vendor="debian",
                codename="trixie",
            ),
            architectures=architectures,
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        lintians = workflow.children.filter(task_name="lintian")

        # architecture "all" does not have a lintian job
        self.assertEqual(lintians.count(), len(architectures) - 1)

        for architecture in architectures:
            if architecture == "all":
                continue

            lintian = lintians.get(
                workflow_data_json__step=f"lintian-{architecture}"
            )

            self.assertEqual(
                lintian.task_data,
                {
                    "backend": Lintian.DEFAULT_BACKEND,
                    "environment": "debian/match:codename=trixie",
                    "exclude_tags": [],
                    "fail_on_severity": "none",
                    "include_tags": [],
                    "input": {
                        "binary_artifacts": sorted(
                            [
                                f"internal@collections/name:build-{arch}"
                                for arch in (architecture, "all")
                            ]
                        ),
                        "source_artifact": (
                            f"{upload_artifacts.source.id}@artifacts"
                        ),
                    },
                    "output": {},
                    "target_distribution": "debian:trixie",
                },
            )

            self.assertEqual(
                lintian.workflow_data_json,
                {
                    "display_name": f"Lintian for {architecture}",
                    "step": f"lintian-{architecture}",
                },
            )

            self.assertQuerySetEqual(
                lintian.dependencies.all(),
                set(
                    WorkRequest.objects.filter(
                        task_name="sbuild",
                        task_data__host_architecture__in=(architecture, "all"),
                    )
                ),
                ordered=False,
            )

    @preserve_task_registry()
    def test_orchestrate_idempotent(self) -> None:
        """Calling orchestrate twice does not create new work requests."""
        binary = self.create_binary_package("hello", "1.0.0", "all")

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary.id]),
                vendor="debian",
                codename="trixie",
            ),
        )

        LintianWorkflow(wr).populate()

        children = set(wr.children.all())

        LintianWorkflow(wr).populate()

        self.assertQuerySetEqual(wr.children.all(), children, ordered=False)

    def test_compute_dynamic_data(self) -> None:
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact()
        )

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = LintianWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="hello_1.0-1"
            ),
        )

    def test_get_label(self) -> None:
        """Test get_label."""
        w = self.create_lintian_workflow(extra_task_data={})
        self.assertEqual(w.get_label(), "run lintian")
