# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the qa workflow."""
from collections.abc import Sequence
from typing import Any

from django.test import override_settings

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianSourcePackage,
    TaskTypes,
)
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.collections import DebianSuiteManager
from debusine.server.scheduler import schedule
from debusine.server.workflows import (
    QAWorkflow,
    WorkflowValidationError,
    workflow_utils,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    QAWorkflowData,
    SbuildWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    LintianFailOnSeverity,
    LookupMultiple,
    SbuildData,
    SbuildInput,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class QAWorkflowTests(TestCase):
    """Unit tests for :py:class:`QAWorkflow`."""

    def create_qa_workflow(
        self,
        *,
        extra_task_data: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> QAWorkflow:
        """Create a qa workflow."""
        task_data = {
            "source_artifact": 2,
            "binary_artifacts": [3],
            "package_build_logs": [4],
            "vendor": "debian",
            "codename": "bookworm",
        }
        if extra_task_data is not None:
            task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="qa", task_data=task_data, validate=validate
        )
        return QAWorkflow(wr)

    def orchestrate(
        self,
        extra_data: dict[str, Any],
        *,
        architectures: Sequence[str] | None = None,
        sbuild_workflow_source_artifact: Artifact | None = None,
    ) -> WorkRequest:
        """Create a QAWorkflow and call orchestrate_workflow."""

        class ExamplePipeline(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Example workflow."""

            def populate(self) -> None:
                """Populate the pipeline."""
                source_artifact_id = (
                    sbuild_workflow_source_artifact.id
                    if sbuild_workflow_source_artifact is not None
                    else 20
                )
                sbuild = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="sbuild",
                    task_data=SbuildWorkflowData(
                        input=SbuildInput(source_artifact=source_artifact_id),
                        target_distribution="debian:sid",
                        architectures=list(architectures or ["all"]),
                    ),
                )

                if architectures is not None:
                    for arch in architectures:
                        child = sbuild.create_child(
                            task_name="sbuild",
                            task_data=SbuildData(
                                input=SbuildInput(
                                    source_artifact=source_artifact_id
                                ),
                                host_architecture=arch,
                                environment="debian/match:codename=sid",
                            ),
                        )

                        self.provides_artifact(
                            child,
                            ArtifactCategory.UPLOAD,
                            f"build-{arch}",
                            data={
                                "binary_names": ["hello"],
                                "architecture": arch,
                                "source_package_name": "hello",
                            },
                        )
                        self.provides_artifact(
                            child,
                            ArtifactCategory.PACKAGE_BUILD_LOG,
                            f"log-{arch}",
                            data={
                                "architecture": arch,
                                "source_package_name": "hello",
                            },
                        )

                data = {
                    "source_artifact": source_artifact_id,
                    "binary_artifacts": [30, 31],
                    "package_build_logs": [32, 33],
                    "enable_blhc": False,
                    "vendor": "debian",
                    "codename": "bookworm",
                }

                qa = self.work_request_ensure_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="qa",
                    task_data=QAWorkflowData(**{**data, **extra_data}),
                    workflow_data=WorkRequestWorkflowData(
                        display_name="DebDiff workflow", step="debdiff-workflow"
                    ),
                )
                QAWorkflow(qa).populate()
                self.work_request.mark_running()

        root = self.playground.create_workflow(task_name="examplepipeline")

        root.mark_running()
        orchestrate_workflow(root)

        return root.children.get(task_name="qa")

    def create_binary_packages(
        self,
        srcpkg_name: str,
        srcpkg_version: str,
        version: str,
        architecture: str,
    ) -> Artifact:
        """Create a minimal `debian:binary-packages` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGES,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "version": version,
                "architecture": architecture,
                "packages": [],
            },
        )
        return artifact

    def test_validate_input(self) -> None:
        """validate_input passes a valid case."""
        workflow = self.create_qa_workflow()

        workflow.validate_input()

    def test_validate_input_bad_qa_suite(self) -> None:
        """validate_input raises errors in looking up a suite."""
        workflow = self.create_qa_workflow(
            extra_task_data={
                "qa_suite": "nonexistent@debian:suite",
                "enable_reverse_dependencies_autopkgtest": True,
            },
            validate=False,
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:suite' does not exist or is hidden",
        ):
            workflow.validate_input()

    def test_compute_dynamic_data(self) -> None:
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        wr = self.playground.create_workflow(
            task_name="qa",
            task_data=QAWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    ["internal@collections/name:build-amd64"]
                ),
                package_build_logs=LookupMultiple.parse_obj(
                    ["internal@collections/name:log-amd64"]
                ),
                vendor="debian",
                codename="sid",
            ),
        )
        workflow = QAWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="hello_1.0-1"
            ),
        )

    def test_compute_dynamic_data_source_artifact_wrong_category(self) -> None:
        """`source_artifact` must be a source-package or upload."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )
        wr = self.playground.create_workflow(
            task_name="qa",
            task_data=QAWorkflowData(
                source_artifact=artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    ["internal@collections/name:build-amd64"]
                ),
                package_build_logs=LookupMultiple.parse_obj(
                    ["internal@collections/name:log-amd64"]
                ),
                vendor="debian",
                codename="sid",
            ),
        )
        workflow = QAWorkflow(wr)

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^source_artifact: unexpected artifact category: 'debusine:test'. "
            r"Valid categories: \['debian:source-package', 'debian:upload'\]$",
        ):
            workflow.compute_dynamic_data(TaskDatabase(wr))

    def test_source_package_upload(self) -> None:
        """`source_artifact` can be an upload."""
        upload_artifacts = self.playground.create_upload_artifacts()
        wr = self.playground.create_workflow(
            task_name="qa",
            task_data=QAWorkflowData(
                source_artifact=upload_artifacts.upload.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    ["internal@collections/name:build-amd64"]
                ),
                enable_blhc=False,
                vendor="debian",
                codename="sid",
            ),
        )
        workflow = QAWorkflow(wr)
        self.assertEqual(
            workflow_utils.source_package(workflow), upload_artifacts.source
        )

    @preserve_task_registry()
    def test_populate(self) -> None:
        """Test populate."""
        architectures = ["amd64"]
        source_artifact = self.playground.create_source_artifact(name="hello")

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "architectures": ["amd64"],
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            },
            architectures=architectures,
        )

        autopkgtest = workflow.children.get(
            task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
        )

        assert workflow.parent is not None
        self.assertEqual(autopkgtest.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            autopkgtest.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "prefix": "",
                "qa_suite": None,
                "reference_qa_results": None,
                "source_artifact": source_artifact.id,
                "update_qa_results": False,
                "vendor": "debian",
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            },
        )
        self.assertEqual(
            autopkgtest.workflow_data_json,
            {"display_name": "autopkgtest", "step": "autopkgtest"},
        )
        self.assertQuerySetEqual(autopkgtest.dependencies.all(), [])

        # AutopkgtestWorkflow.populate() was called and created its tasks
        self.assertQuerySetEqual(
            autopkgtest.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "autopkgtest",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

        self.assertFalse(
            workflow.children.filter(
                task_name="reverse_dependencies_autopkgtest",
                task_type=TaskTypes.WORKFLOW,
            ).exists()
        )

        lintian = workflow.children.get(
            task_name="lintian", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(lintian.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            lintian.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "fail_on_severity": LintianFailOnSeverity.ERROR,
                "prefix": "",
                "qa_suite": None,
                "reference_qa_results": None,
                "source_artifact": source_artifact.id,
                "update_qa_results": False,
                "vendor": "debian",
            },
        )
        self.assertEqual(
            lintian.workflow_data_json,
            {"display_name": "lintian", "step": "lintian"},
        )
        self.assertQuerySetEqual(lintian.dependencies.all(), [])
        # LintianWorkflow.populate() was called and created its tasks
        self.assertQuerySetEqual(
            lintian.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "lintian",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

        piuparts = workflow.children.get(
            task_name="piuparts", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(piuparts.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            piuparts.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "prefix": "",
                "qa_suite": None,
                "reference_qa_results": None,
                "source_artifact": source_artifact.id,
                "update_qa_results": False,
                "vendor": "debian",
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            },
        )

        self.assertEqual(
            piuparts.workflow_data_json,
            {"display_name": "piuparts", "step": "piuparts"},
        )
        self.assertQuerySetEqual(piuparts.dependencies.all(), [])

        # PiupartsWorkflow.populate() was called and created its tasks
        self.assertQuerySetEqual(
            piuparts.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "piuparts",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

    @preserve_task_registry()
    def test_populate_use_available_architectures(self) -> None:
        """
        Test populate uses available architectures.

        The user didn't specify "architectures", so QAWorkflow checks
        available architectures and "all" and uses them.
        """
        for arch in ["amd64", "i386"]:
            self.playground.create_debian_environment(architecture=arch)
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact = self.create_binary_packages(
            "hello", "hello", "1.0.0", "amd64"
        )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": [binary_artifact.id],
            }
        )

        autopkgtest = workflow.children.get(
            task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(
            autopkgtest.task_data["architectures"], ["all", "amd64", "i386"]
        )

    @preserve_task_registry()
    def test_populate_disable_autopkgtest_lintian_piuparts(self) -> None:
        """Populate does not create autopkgtest, lintian nor piuparts."""
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact = self.create_binary_packages(
            "hello", "hello", "1.0.0", "amd64"
        )
        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": [binary_artifact.id],
                "architectures": ["amd64"],
                "enable_autopkgtest": False,
                "enable_lintian": False,
                "enable_piuparts": False,
            },
        )

        self.assertFalse(
            workflow.children.filter(
                task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
            ).exists()
        )
        self.assertFalse(
            workflow.children.filter(
                task_name="reverse_dependencies_autopkgtest",
                task_type=TaskTypes.WORKFLOW,
            ).exists()
        )
        self.assertFalse(
            workflow.children.filter(
                task_name="lintian", task_type=TaskTypes.WORKFLOW
            ).exists()
        )
        self.assertFalse(
            workflow.children.filter(
                task_name="piuparts", task_type=TaskTypes.WORKFLOW
            ).exists()
        )

    @preserve_task_registry()
    def test_populate_enable_reverse_dependencies_autopkgtest(self) -> None:
        """Populate can enable reverse_dependencies_autopkgtest."""
        architectures = ["amd64"]
        source_artifact = self.playground.create_source_artifact(name="hello")
        sid = self.playground.create_collection(
            name="sid", category=CollectionCategory.SUITE
        )
        dep_source_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data=DebianSourcePackage(
                name="depends",
                version="1",
                type="dpkg",
                dsc_fields={
                    "Package": "depends",
                    "Version": "1",
                    "Testsuite": "autopkgtest",
                },
            ),
        )
        sid.manager.add_artifact(
            dep_source_artifact,
            user=self.playground.get_default_user(),
            variables={"component": "main", "section": "devel"},
        )
        dep_binary_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data=DebianBinaryPackage(
                srcpkg_name="depends",
                srcpkg_version="1",
                deb_fields={
                    "Package": "depends",
                    "Version": "1",
                    "Architecture": "all",
                    "Depends": "hello",
                },
                deb_control_files=[],
            ),
        )
        sid.manager.add_artifact(
            dep_binary_artifact,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "architectures": ["amd64"],
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "enable_reverse_dependencies_autopkgtest": True,
            },
            architectures=architectures,
        )

        rdep_autopkgtest = workflow.children.get(
            task_name="reverse_dependencies_autopkgtest",
            task_type=TaskTypes.WORKFLOW,
        )

        assert workflow.parent is not None
        self.assertEqual(rdep_autopkgtest.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            rdep_autopkgtest.task_data,
            {
                "prefix": "",
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "reference_qa_results": None,
                "update_qa_results": False,
                "vendor": "debian",
                "codename": "bookworm",
                "backend": BackendType.AUTO,
                "architectures": ["amd64"],
                "arch_all_host_architecture": "amd64",
                "extra_repositories": None,
            },
        )
        self.assertEqual(
            rdep_autopkgtest.workflow_data_json,
            {
                "display_name": "autopkgtests of reverse-dependencies",
                "step": "reverse-dependencies-autopkgtest",
            },
        )
        self.assertQuerySetEqual(rdep_autopkgtest.dependencies.all(), [])

        # ReverseDependenciesAutopkgtestWorkflow.populate() was called and
        # created its sub-workflow.
        autopkgtest = rdep_autopkgtest.children.get()
        self.assertEqual(autopkgtest.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            autopkgtest.task_data["source_artifact"],
            "sid@debian:suite/source-version:depends_1",
        )
        self.assertQuerySetEqual(
            autopkgtest.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "autopkgtest",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

    @preserve_task_registry()
    def test_populate_update_qa_results(self) -> None:
        """Populate can update QA results."""
        architectures = ["amd64"]
        source_artifact = self.playground.create_source_artifact(name="hello")
        sid = self.playground.create_collection(
            name="sid", category=CollectionCategory.SUITE
        )
        self.playground.create_collection(
            name="sid", category=CollectionCategory.QA_RESULTS
        )
        dep_source_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data=DebianSourcePackage(
                name="depends",
                version="1",
                type="dpkg",
                dsc_fields={
                    "Package": "depends",
                    "Version": "1",
                    "Testsuite": "autopkgtest",
                },
            ),
        )
        sid.manager.add_artifact(
            dep_source_artifact,
            user=self.playground.get_default_user(),
            variables={"component": "main", "section": "devel"},
        )
        dep_binary_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data=DebianBinaryPackage(
                srcpkg_name="depends",
                srcpkg_version="1",
                deb_fields={
                    "Package": "depends",
                    "Version": "1",
                    "Architecture": "all",
                    "Depends": "hello",
                },
                deb_control_files=[],
            ),
        )
        sid.manager.add_artifact(
            dep_binary_artifact,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        workflow = self.orchestrate(
            extra_data={
                "prefix": "reference-qa-result|",
                "source_artifact": source_artifact.id,
                # This would normally be the corresponding binary artifacts
                # in qa_suite, but that doesn't really matter for testing
                # purposes.
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "architectures": ["amd64"],
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "reference_qa_results": f"sid@{CollectionCategory.QA_RESULTS}",
                "update_qa_results": True,
                "enable_reverse_dependencies_autopkgtest": True,
            },
            architectures=architectures,
        )

        autopkgtest = workflow.children.get(
            task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
        )

        assert workflow.parent is not None
        self.assertEqual(autopkgtest.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            autopkgtest.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "prefix": "reference-qa-result|",
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "reference_qa_results": f"sid@{CollectionCategory.QA_RESULTS}",
                "source_artifact": source_artifact.id,
                "update_qa_results": True,
                "vendor": "debian",
                "extra_repositories": None,
            },
        )
        self.assertEqual(
            autopkgtest.workflow_data_json,
            {"display_name": "autopkgtest", "step": "autopkgtest"},
        )
        self.assertQuerySetEqual(autopkgtest.dependencies.all(), [])

        # AutopkgtestWorkflow.populate() was called and created its tasks.
        self.assertQuerySetEqual(
            autopkgtest.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "autopkgtest",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

        rdep_autopkgtest = workflow.children.get(
            task_name="reverse_dependencies_autopkgtest",
            task_type=TaskTypes.WORKFLOW,
        )

        assert workflow.parent is not None
        self.assertEqual(rdep_autopkgtest.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            rdep_autopkgtest.task_data,
            {
                "prefix": "reference-qa-result|",
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "reference_qa_results": f"sid@{CollectionCategory.QA_RESULTS}",
                "update_qa_results": True,
                "vendor": "debian",
                "codename": "bookworm",
                "backend": BackendType.AUTO,
                "architectures": ["amd64"],
                "arch_all_host_architecture": "amd64",
                "extra_repositories": None,
            },
        )
        self.assertEqual(
            rdep_autopkgtest.workflow_data_json,
            {
                "display_name": "autopkgtests of reverse-dependencies",
                "step": "reverse-dependencies-autopkgtest",
            },
        )
        self.assertQuerySetEqual(rdep_autopkgtest.dependencies.all(), [])

        # ReverseDependenciesAutopkgtestWorkflow.populate() was called and
        # created its sub-workflow.
        sub_autopkgtest = rdep_autopkgtest.children.get()
        self.assertEqual(sub_autopkgtest.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            sub_autopkgtest.task_data["source_artifact"],
            "sid@debian:suite/source-version:depends_1",
        )
        self.assertQuerySetEqual(
            sub_autopkgtest.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "autopkgtest",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

        lintian = workflow.children.get(
            task_name="lintian", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(lintian.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            lintian.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "fail_on_severity": LintianFailOnSeverity.ERROR,
                "prefix": "reference-qa-result|",
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "reference_qa_results": f"sid@{CollectionCategory.QA_RESULTS}",
                "source_artifact": source_artifact.id,
                "update_qa_results": True,
                "vendor": "debian",
            },
        )
        self.assertEqual(
            lintian.workflow_data_json,
            {"display_name": "lintian", "step": "lintian"},
        )
        self.assertQuerySetEqual(lintian.dependencies.all(), [])

        # LintianWorkflow.populate() was called and created its tasks.
        self.assertQuerySetEqual(
            lintian.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "lintian",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                )
            ],
        )

        piuparts = workflow.children.get(
            task_name="piuparts", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(piuparts.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            piuparts.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "prefix": "reference-qa-result|",
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "reference_qa_results": f"sid@{CollectionCategory.QA_RESULTS}",
                "source_artifact": source_artifact.id,
                "update_qa_results": True,
                "vendor": "debian",
                "extra_repositories": None,
            },
        )

        self.assertEqual(
            piuparts.workflow_data_json,
            {"display_name": "piuparts", "step": "piuparts"},
        )
        self.assertQuerySetEqual(piuparts.dependencies.all(), [])

    @preserve_task_registry()
    def test_populate_architectures_allowed(self) -> None:
        """Populate uses architectures and architectures_allowed."""
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact_amd = self.create_binary_packages(
            "hello", "hello", "1.0.0", "amd64"
        )
        binary_artifact_i386 = self.create_binary_packages(
            "hello", "hello", "1.0.0", "i386"
        )
        binary_artifact_arm64 = self.create_binary_packages(
            "hello", "hello", "1.0.0", "arm64"
        )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": [
                    binary_artifact_amd.id,
                    binary_artifact_i386.id,
                    binary_artifact_arm64.id,
                ],
                "architectures": ["amd64", "i386"],
                "architectures_allowlist": ["i386"],
            },
        )

        autopkgtest = workflow.children.get(
            task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            autopkgtest.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["i386"],
                "backend": BackendType.AUTO,
                "binary_artifacts": [f"{binary_artifact_i386.id}@artifacts"],
                "codename": "bookworm",
                "prefix": "",
                "qa_suite": None,
                "reference_qa_results": None,
                "source_artifact": source_artifact.id,
                "update_qa_results": False,
                "vendor": "debian",
                "extra_repositories": None,
            },
        )

    @preserve_task_registry()
    def test_architectures_deny(self) -> None:
        """Populate uses architectures and architectures_denylist."""
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact_amd = self.create_binary_packages(
            "hello", "hello", "1.0.0", "amd64"
        )
        binary_artifact_i386 = self.create_binary_packages(
            "hello", "hello", "1.0.0", "i386"
        )
        binary_artifact_arm64 = self.create_binary_packages(
            "hello", "hello", "1.0.0", "arm64"
        )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": [
                    binary_artifact_amd.id,
                    binary_artifact_i386.id,
                    binary_artifact_arm64.id,
                ],
                "architectures": ["amd64", "i386"],
                "architectures_denylist": ["i386"],
            },
            architectures=["amd64", "i386"],
        )

        autopkgtest = workflow.children.get(
            task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            autopkgtest.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": [f"{binary_artifact_amd.id}@artifacts"],
                "codename": "bookworm",
                "prefix": "",
                "qa_suite": None,
                "reference_qa_results": None,
                "source_artifact": source_artifact.id,
                "update_qa_results": False,
                "vendor": "debian",
                "extra_repositories": None,
            },
        )

    @preserve_task_registry()
    def test_populate_subworkflow_configuration(self) -> None:
        """Populate configures sub-workflows according to specified data."""
        self.playground.create_debian_environment()
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact = self.create_binary_packages(
            "hello", "hello", "1.0.0", "amd64"
        )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": [binary_artifact.id],
                "autopkgtest_backend": BackendType.INCUS_VM,
                "lintian_backend": BackendType.UNSHARE,
                "lintian_fail_on_severity": LintianFailOnSeverity.PEDANTIC,
                "piuparts_backend": BackendType.INCUS_LXC,
                "piuparts_environment": "debian/match:codename=trixie",
            }
        )

        autopkgtest = workflow.children.get(
            task_name="autopkgtest", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(autopkgtest.task_data["backend"], BackendType.INCUS_VM)

        lintian = workflow.children.get(
            task_name="lintian", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(lintian.task_data["backend"], BackendType.UNSHARE)
        self.assertEqual(
            lintian.task_data["fail_on_severity"],
            LintianFailOnSeverity.PEDANTIC,
        )

        piuparts = workflow.children.get(
            task_name="piuparts", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(piuparts.task_data["backend"], BackendType.INCUS_LXC)
        self.assertEqual(
            piuparts.task_data["environment"], "debian/match:codename=trixie"
        )

    @preserve_task_registry()
    def test_populate_debdiff(self) -> None:
        """Populate creates a debdiff workflow."""
        self.playground.create_collection(
            name="bookworm",
            category=CollectionCategory.SUITE,
        )
        trixie = self.playground.create_collection(
            name="trixie",
            category=CollectionCategory.SUITE,
        )
        origin_source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0.0-1"
        )
        trixie.manager.add_artifact(
            origin_source_artifact,
            user=self.playground.get_default_user(),
            variables={"component": "main", "section": "devel"},
        )
        origin_binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello",
                srcpkg_version="1.0.0-1",
                version="1.0.0-1",
                architecture="amd64",
            )
        )
        trixie.manager.add_artifact(
            origin_binary_artifact,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        source_artifact = self.playground.create_source_artifact(name="hello")
        workflow = self.orchestrate(
            architectures=["amd64"],
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": LookupMultiple.parse_obj(
                    ["internal@collections/name:build-amd64"]
                ),
                "architectures": ["amd64"],
                "enable_autopkgtest": False,
                "enable_lintian": False,
                "enable_piuparts": False,
                "enable_debdiff": True,
                "vendor": "debian",
                "codename": "bookworm",
                "qa_suite": "trixie@debian:suite",
            },
            sbuild_workflow_source_artifact=source_artifact,
        )
        workflow.mark_running()

        assert workflow.parent is not None
        sbuild_workflow = workflow.parent.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKFLOW
        )
        self.assertTrue(sbuild_workflow.mark_running())

        sbuild_worker_amd64 = sbuild_workflow.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKER
        )
        self.assertTrue(sbuild_worker_amd64.mark_pending())

        debdiff_workflow = workflow.children.get(
            task_name="debdiff", task_type=TaskTypes.WORKFLOW
        )

        # No children and blocked: it needs the binary artifacts to be able
        # to create the debdiff tasks
        self.assertEqual(debdiff_workflow.children.count(), 0)
        self.assertEqual(debdiff_workflow.status, WorkRequest.Statuses.BLOCKED)
        self.assertQuerySetEqual(
            debdiff_workflow.dependencies.all(), [sbuild_worker_amd64]
        )

        # Create build artifacts and complete sbuild to unblock the debdiff
        # workflow.
        upload = self.playground.create_upload_artifacts(
            src_name="hello",
            version="1.1.0-1",
            binary=True,
            source=False,
            bin_architecture="amd64",
            workspace=sbuild_worker_amd64.workspace,
            work_request=sbuild_worker_amd64,
        )

        sbuild_worker_amd64.refresh_from_db()
        self.assertTrue(
            sbuild_worker_amd64.mark_completed(WorkRequest.Results.SUCCESS)
        )

        debdiff_workflow.refresh_from_db()
        self.assertEqual(debdiff_workflow.status, WorkRequest.Statuses.PENDING)
        with self.captureOnCommitCallbacks() as on_commit:
            self.assertIn(workflow.parent, schedule())
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            for callback in on_commit:
                callback()

        debdiff_workflow.refresh_from_db()
        self.assertEqual(debdiff_workflow.status, WorkRequest.Statuses.RUNNING)
        [debdiff_source_work_request, debdiff_binary_work_request] = (
            debdiff_workflow.children.order_by("id")
        )

        self.assertEqual(debdiff_source_work_request.task_name, "debdiff")
        self.assertEqual(
            debdiff_source_work_request.workflow_data_json,
            {
                "display_name": "DebDiff for source package",
                "step": "debdiff-source",
            },
        )
        self.assertEqual(
            debdiff_source_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "extra_flags": [],
                "host_architecture": "amd64",
                "input": {
                    "source_artifacts": [
                        "trixie@debian:suite/name:hello_1.0.0-1",
                        source_artifact.id,
                    ]
                },
            },
        )

        self.assertEqual(
            debdiff_binary_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "extra_flags": [],
                "host_architecture": "amd64",
                "input": {
                    "binary_artifacts": [
                        ["trixie@debian:suite/name:hello_1.0.0-1_amd64"],
                        [upload.binaries[0].id],
                    ]
                },
            },
        )

    @preserve_task_registry()
    def test_populate_blhc(self) -> None:
        """Populate enables blhc."""
        architectures = ["amd64"]
        source_artifact = self.playground.create_source_artifact(name="hello")
        sid = self.playground.create_collection(
            name="sid", category=CollectionCategory.SUITE
        )
        assert isinstance(sid.manager, DebianSuiteManager)

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "package_build_logs": ["internal@collections/name:log-amd64"],
                "architectures": ["amd64"],
                "qa_suite": f"sid@{CollectionCategory.SUITE}",
                "enable_blhc": True,
            },
            architectures=architectures,
        )

        blhc_workflow = workflow.children.get(
            task_name="blhc", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(blhc_workflow.children.count(), 1)
        self.assertEqual(blhc_workflow.status, WorkRequest.Statuses.RUNNING)

        [blhc_work_request] = blhc_workflow.children.all()

        self.assertEqual(blhc_work_request.task_name, "blhc")
        self.assertEqual(
            blhc_work_request.workflow_data_json,
            {
                "display_name": "build log hardening check for amd64",
                "step": "blhc-amd64",
            },
        )
        self.assertEqual(
            blhc_work_request.task_data,
            {
                "input": {"artifact": "internal@collections/name:log-amd64"},
                "extra_flags": [],
            },
        )

    def test_get_label(self) -> None:
        """Test get_label()."""
        w = self.create_qa_workflow()
        self.assertEqual(w.get_label(), "run QA")
