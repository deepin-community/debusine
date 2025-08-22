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

from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianLintian,
    DebianLintianSummary,
    TaskTypes,
)
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    TaskDatabase,
    WorkRequest,
)
from debusine.db.models.work_requests import SkipWorkRequest
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows import LintianWorkflow, WorkflowValidationError
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    LintianWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks.lintian import Lintian
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    LintianFailOnSeverity,
    LintianOutput,
    LookupMultiple,
    OutputData,
    SbuildData,
    SbuildInput,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class LintianWorkflowTests(TestCase):
    """Unit tests for :py:class:`LintianWorkflow`."""

    source_artifact: ClassVar[Artifact]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.source_artifact = cls.playground.create_source_artifact(
            name="hello"
        )

    def create_lintian_workflow(
        self,
        *,
        extra_task_data: dict[str, Any] | None = None,
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
        if extra_task_data is not None:
            task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="lintian", task_data=task_data, validate=False
        )
        return LintianWorkflow(wr)

    def orchestrate(
        self, task_data: LintianWorkflowData, architectures: Sequence[str]
    ) -> WorkRequest:
        """Create and orchestrate a LintianWorkflow."""

        class ExamplePipeline(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
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

    def add_lintian_binary(self, suite: Collection, version: str) -> None:
        """Add a lintian binary package to a suite."""
        artifact = self.playground.create_minimal_binary_package_artifact(
            "lintian", version, "lintian", version, "all"
        )
        suite.manager.add_artifact(
            artifact,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

    def add_qa_result(
        self,
        qa_results: Collection,
        package: str,
        version: str,
        architecture: str,
        lintian_version: str,
    ) -> CollectionItem:
        """Add a lintian result to a ``debian:qa-results`` collection."""
        return qa_results.manager.add_artifact(
            self.playground.create_artifact(
                category=ArtifactCategory.LINTIAN,
                data=DebianLintian(
                    architecture=architecture,
                    summary=DebianLintianSummary(
                        tags_count_by_severity={},
                        package_filename={},
                        tags_found=[],
                        overridden_tags_found=[],
                        lintian_version=lintian_version,
                        distribution="debian:sid",
                    ),
                ),
            )[0],
            user=self.playground.get_default_user(),
            variables={
                "package": package,
                "version": version,
                "architecture": architecture,
                "timestamp": int(timezone.now().timestamp()),
                "work_request_id": self.playground.create_work_request(
                    task_name="lintian", result=WorkRequest.Results.SUCCESS
                ).id,
            },
        )

    def test_validate_input(self) -> None:
        """validate_input passes a valid case."""
        w = self.create_lintian_workflow()

        w.validate_input()

    def test_validate_input_bad_qa_suite(self) -> None:
        """validate_input raises errors in looking up a suite."""
        w = self.create_lintian_workflow(
            extra_task_data={"qa_suite": "nonexistent@debian:suite"}
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:suite' does not exist or is hidden",
        ):
            w.validate_input()

    def test_validate_input_bad_reference_qa_results(self) -> None:
        """validate_input raises errors in looking up reference QA results."""
        w = self.create_lintian_workflow(
            extra_task_data={
                "reference_qa_results": "nonexistent@debian:qa-results"
            }
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:qa-results' does not exist or is hidden",
        ):
            w.validate_input()

    def test_has_current_reference_qa_result_no_match(self) -> None:
        """_has_current_reference_qa_result: no matching result."""
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(sid_suite, "2.116.3")
        self.add_qa_result(sid_qa_results, "other", "1.0-1", "amd64", "2.116.3")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertFalse(
            LintianWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_different_version(self) -> None:
        """_has_current_reference_qa_result: result for different version."""
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(sid_suite, "2.116.3")
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64", "2.116.3")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-2"
        )

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertFalse(
            LintianWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_old_lintian_version(self) -> None:
        """_has_current_reference_qa_result: run with old lintian version."""
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(sid_suite, "2.116.3")
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64", "2.15.0")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertFalse(
            LintianWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_no_lintian_version(self) -> None:
        """_has_current_reference_qa_result: no reference lintian version."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64", "2.116.3")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertTrue(
            LintianWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_current(self) -> None:
        """_has_current_reference_qa_result: current result."""
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(sid_suite, "2.116.3")
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64", "2.116.3")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )

        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertTrue(
            LintianWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    @preserve_task_registry()
    def test_populate(self) -> None:
        """Test populate."""
        architectures = ["amd64", "i386", "all"]

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
                    "fail_on_severity": LintianFailOnSeverity.ERROR,
                    "host_architecture": architecture,
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
                    "output": (
                        {"source_analysis": False, "binary_all_analysis": False}
                        if architecture != "amd64"
                        else {}
                    ),
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
    def test_populate_source_only(self) -> None:
        """Test populate with no binary artifacts."""
        root = self.orchestrate(
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([]),
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
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=trixie",
                "exclude_tags": [],
                "fail_on_severity": LintianFailOnSeverity.ERROR,
                "host_architecture": "amd64",
                "include_tags": [],
                "input": {
                    "binary_artifacts": [],
                    "source_artifact": self.source_artifact.id,
                },
                "output": {},
                "target_distribution": "debian:trixie",
            },
        )

    @preserve_task_registry()
    def test_populate_without_architectures(self) -> None:
        """Test populate with only arch-indep packages."""
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "hello", "1.0.0", "all"
            )
        )

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
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=trixie",
                "exclude_tags": [],
                "fail_on_severity": LintianFailOnSeverity.ERROR,
                "host_architecture": "amd64",
                "include_tags": [],
                "input": {
                    "binary_artifacts": [f"{binary_artifact.id}@artifacts"],
                    "source_artifact": self.source_artifact.id,
                },
                "output": {},
                "target_distribution": "debian:trixie",
            },
        )

    @preserve_task_registry()
    def test_populate_without_architectures_arch_all_host_architecture(
        self,
    ) -> None:
        """Workflow honours `arch_all_host_architecture`."""
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "hello", "1.0.0", "all"
            )
        )

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
                arch_all_host_architecture="s390x",
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
                "backend": BackendType.UNSHARE,
                "environment": "debian/match:codename=trixie",
                "exclude_tags": [],
                "fail_on_severity": LintianFailOnSeverity.ERROR,
                "host_architecture": "s390x",
                "include_tags": [],
                "input": {
                    "binary_artifacts": [f"{binary_artifact.id}@artifacts"],
                    "source_artifact": self.source_artifact.id,
                },
                "output": {},
                "target_distribution": "debian:trixie",
            },
        )

    @preserve_task_registry()
    def test_populate_mixed_without_arch_all_host_architecture(self) -> None:
        """Mixed all/any binaries but without the usual arch-all arch."""
        binary_artifacts = {
            "all": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "hello", "1.0.0", "all"
            ),
            "i386": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "libhello1", "1.0.0", "i386"
            ),
        }

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [binary_artifacts["all"].id, binary_artifacts["i386"].id]
                ),
                vendor="debian",
                codename="trixie",
                arch_all_host_architecture="amd64",
            ),
            architectures=("all", "i386"),
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        self.assertQuerySetEqual(
            workflow.children.filter(task_name="lintian")
            .order_by("task_data__host_architecture")
            .values_list("task_data", flat=True),
            [
                {
                    "backend": BackendType.UNSHARE,
                    "environment": "debian/match:codename=trixie",
                    "exclude_tags": [],
                    "fail_on_severity": LintianFailOnSeverity.ERROR,
                    "host_architecture": "i386",
                    "include_tags": [],
                    "input": {
                        "binary_artifacts": [
                            f"{binary_artifacts['all'].id}@artifacts",
                            f"{binary_artifacts['i386'].id}@artifacts",
                        ],
                        "source_artifact": self.source_artifact.id,
                    },
                    "output": {},
                    "target_distribution": "debian:trixie",
                }
            ],
        )

    @preserve_task_registry()
    def test_populate_arch_dep_without_arch_all_host_architecture(self) -> None:
        """``Architecture: any`` binaries without the usual arch-all arch."""
        binary_artifacts = {
            "i386": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "libhello1", "1.0.0", "i386"
            ),
            "s390x": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "libhello1", "1.0.0", "s390x"
            ),
        }

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [binary_artifacts["i386"].id, binary_artifacts["s390x"].id]
                ),
                vendor="debian",
                codename="trixie",
                arch_all_host_architecture="amd64",
            ),
            architectures=("i386", "s390x"),
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        self.assertQuerySetEqual(
            workflow.children.filter(task_name="lintian")
            .order_by("task_data__host_architecture")
            .values_list("task_data", flat=True),
            [
                {
                    "backend": BackendType.UNSHARE,
                    "environment": "debian/match:codename=trixie",
                    "exclude_tags": [],
                    "fail_on_severity": LintianFailOnSeverity.ERROR,
                    "host_architecture": architecture,
                    "include_tags": [],
                    "input": {
                        "binary_artifacts": [
                            f"{binary_artifacts[architecture].id}@artifacts"
                        ],
                        "source_artifact": self.source_artifact.id,
                    },
                    "output": (
                        {"source_analysis": False, "binary_all_analysis": False}
                        if architecture != "i386"
                        else {}
                    ),
                    "target_distribution": "debian:trixie",
                }
                for architecture in ("i386", "s390x")
            ],
        )

    @preserve_task_registry()
    def test_populate_with_no_architecture_overlap(self) -> None:
        """If architectures and binary artifacts are disjoint, do nothing."""
        root = self.orchestrate(
            task_data=LintianWorkflowData(
                architectures=["i386"],
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    ["internal@collections/name:build-amd64"]
                ),
                vendor="debian",
                codename="trixie",
            ),
            architectures=["amd64"],
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        self.assertFalse(workflow.children.filter(task_name="lintian").exists())

    @preserve_task_registry()
    def test_populate_no_binary_any_analysis(self) -> None:
        """Disabling binary-any analysis skips no-op work requests."""
        binary_artifacts = {
            "all": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "hello", "1.0.0", "all"
            ),
            "amd64": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "libhello1", "1.0.0", "amd64"
            ),
            "i386": self.playground.create_minimal_binary_package_artifact(
                "hello", "1.0.0", "libhello1", "1.0.0", "i386"
            ),
        }

        root = self.orchestrate(
            task_data=LintianWorkflowData(
                source_artifact=self.source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        binary_artifacts["all"].id,
                        binary_artifacts["amd64"].id,
                        binary_artifacts["i386"].id,
                    ]
                ),
                vendor="debian",
                codename="trixie",
                arch_all_host_architecture="amd64",
                output=LintianOutput(binary_any_analysis=False),
            ),
            architectures=("all", "amd64", "i386"),
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        self.assertQuerySetEqual(
            workflow.children.filter(task_name="lintian")
            .order_by("task_data__host_architecture")
            .values_list("task_data", flat=True),
            [
                {
                    "backend": BackendType.UNSHARE,
                    "environment": "debian/match:codename=trixie",
                    "exclude_tags": [],
                    "fail_on_severity": LintianFailOnSeverity.ERROR,
                    "host_architecture": "amd64",
                    "include_tags": [],
                    "input": {
                        "binary_artifacts": [
                            f"{binary_artifacts['all'].id}@artifacts",
                            f"{binary_artifacts['amd64'].id}@artifacts",
                        ],
                        "source_artifact": self.source_artifact.id,
                    },
                    "output": {"binary_any_analysis": False},
                    "target_distribution": "debian:trixie",
                }
            ],
        )

    @preserve_task_registry()
    def test_populate_upload(self) -> None:
        """The workflow accepts debian:upload source artifacts."""
        architectures = ["amd64", "i386", "all"]
        upload_artifacts = self.playground.create_upload_artifacts()

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
                    "fail_on_severity": LintianFailOnSeverity.ERROR,
                    "host_architecture": architecture,
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
                    "output": (
                        {"source_analysis": False, "binary_all_analysis": False}
                        if architecture != "amd64"
                        else {}
                    ),
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
    def test_populate_has_current_reference_qa_result(self) -> None:
        """The workflow does nothing with a current reference QA result."""
        trixie_suite = self.playground.create_collection(
            "trixie", CollectionCategory.SUITE
        )
        trixie_qa_results = self.playground.create_collection(
            "trixie", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(trixie_suite, "2.116.3")

        architectures = ["amd64", "i386", "all"]
        for architecture in architectures:
            self.add_qa_result(
                trixie_qa_results,
                "hello",
                self.source_artifact.data["version"],
                architecture,
                "2.116.3",
            )

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
                qa_suite=f"trixie@{CollectionCategory.SUITE}",
                reference_qa_results=f"trixie@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
            architectures=architectures,
        )

        workflow = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="lintian",
            parent=root,
        )

        self.assertQuerySetEqual(workflow.children.all(), [])

    @preserve_task_registry()
    def test_populate_no_previous_reference_qa_result(self) -> None:
        """The workflow produces a reference QA result if there is none."""
        trixie_suite = self.playground.create_collection(
            "trixie", CollectionCategory.SUITE
        )
        self.playground.create_collection(
            "trixie", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(trixie_suite, "2.116.3")

        architectures = ["amd64", "i386", "all"]

        source_item = trixie_suite.manager.add_artifact(
            self.source_artifact,
            user=self.playground.get_default_user(),
            variables={"component": "main", "section": "devel"},
        )

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
                qa_suite=f"trixie@{CollectionCategory.SUITE}",
                reference_qa_results=f"trixie@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
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

        results: dict[str, Artifact] = {}
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
                    "fail_on_severity": LintianFailOnSeverity.ERROR,
                    "host_architecture": architecture,
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
                    "output": (
                        {"source_analysis": False, "binary_all_analysis": False}
                        if architecture != "amd64"
                        else {}
                    ),
                    "target_distribution": "debian:trixie",
                },
            )

            self.assertEqual(
                lintian.workflow_data_json,
                {
                    "allow_failure": True,
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

            qa_result_action = {
                "action": "update-collection-with-artifacts",
                "collection": f"trixie@{CollectionCategory.QA_RESULTS}",
                "name_template": None,
                "variables": {
                    "package": "hello",
                    "version": self.source_artifact.data["version"],
                    "$architecture": "architecture",
                    "timestamp": int(source_item.created_at.timestamp()),
                    "work_request_id": lintian.id,
                },
                "artifact_filters": {"category": ArtifactCategory.LINTIAN},
                "created_at": None,
            }
            self.assert_work_request_event_reactions(
                lintian,
                on_assignment=[
                    {
                        "action": "skip-if-lookup-result-changed",
                        "lookup": (
                            f"trixie@{CollectionCategory.QA_RESULTS}/"
                            f"latest:lintian_hello_{architecture}"
                        ),
                        "collection_item_id": None,
                        "promise_name": None,
                    }
                ],
                on_failure=[qa_result_action],
                on_success=[qa_result_action],
            )

            # Completing the work request stores the QA result.
            self.assertTrue(lintian.mark_pending())
            artifact_architectures = (
                ["source", "all", architecture]
                if architecture == "amd64"
                else [architecture]
            )
            for artifact_architecture in artifact_architectures:
                results[artifact_architecture] = (
                    self.playground.create_artifact(
                        category=ArtifactCategory.LINTIAN,
                        data={"architecture": artifact_architecture},
                        work_request=lintian,
                    )[0]
                )
            self.assertTrue(lintian.mark_completed(WorkRequest.Results.SUCCESS))

        for architecture, result in results.items():
            self.assertEqual(
                lookup_single(
                    f"trixie@{CollectionCategory.QA_RESULTS}/"
                    f"latest:lintian_hello_{architecture}",
                    lintian.workspace,
                    user=lintian.created_by,
                    expect_type=LookupChildType.ARTIFACT,
                ).artifact,
                result,
            )

    def test_populate_reference_qa_result_backs_off(self) -> None:
        """Reference tasks are skipped if another workflow got there first."""
        trixie_suite = self.playground.create_collection(
            "trixie", CollectionCategory.SUITE
        )
        trixie_qa_results = self.playground.create_collection(
            "trixie", CollectionCategory.QA_RESULTS
        )
        self.add_lintian_binary(trixie_suite, "2.116.3")
        self.playground.create_debian_environment(
            codename="trixie", variant="lintian"
        )

        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello",
                srcpkg_version="1.0-1",
                architecture="amd64",
            )
        )
        wr = self.playground.create_workflow(
            task_name="lintian",
            task_data=LintianWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
                qa_suite=f"trixie@{CollectionCategory.SUITE}",
                reference_qa_results=f"trixie@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        LintianWorkflow(wr).populate()

        racing_qa_result = self.add_qa_result(
            trixie_qa_results, "hello", "1.0-1", "amd64", "2.116.3"
        )
        [child] = wr.children.all()
        self.assertTrue(child.mark_pending())

        with self.assertRaises(SkipWorkRequest):
            child.assign_worker(self.playground.create_worker())

        self.assertEqual(child.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(child.result, WorkRequest.Results.SKIPPED)
        self.assertEqual(
            child.output_data,
            OutputData(
                skip_reason=(
                    f"Result of lookup "
                    f"'trixie@{CollectionCategory.QA_RESULTS}/"
                    f"latest:lintian_hello_amd64' changed"
                )
            ),
        )
        self.assertEqual(
            lookup_single(
                f"trixie@{CollectionCategory.QA_RESULTS}/"
                f"latest:lintian_hello_amd64",
                child.workspace,
                user=child.created_by,
                expect_type=LookupChildType.ARTIFACT,
            ).collection_item,
            racing_qa_result,
        )

    @preserve_task_registry()
    def test_orchestrate_idempotent(self) -> None:
        """Calling orchestrate twice does not create new work requests."""
        binary = self.playground.create_minimal_binary_package_artifact(
            "hello", "1.0.0", "hello", "1.0.0", "all"
        )

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
