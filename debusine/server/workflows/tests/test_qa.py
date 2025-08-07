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

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianSourcePackage,
)
from debusine.db.context import context
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.collections.debian_suite import DebianSuiteManager
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
)
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.tasks import TaskConfigError
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
    ) -> WorkRequest:
        """Create a QAWorkflow and call orchestrate_workflow."""

        class ExamplePipeline(
            TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Example workflow."""

            def populate(self) -> None:
                """Populate the pipeline."""
                sbuild = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="sbuild",
                    task_data=SbuildWorkflowData(
                        input=SbuildInput(source_artifact=20),
                        target_distribution="debian:sid",
                        architectures=list(architectures or ["all"]),
                    ),
                )

                if architectures is not None:
                    for arch in architectures:
                        child = sbuild.create_child(
                            task_name="sbuild",
                            task_data=SbuildData(
                                input=SbuildInput(source_artifact=20),
                                host_architecture=arch,
                                environment="debian/match:codename=sid",
                            ),
                        )

                        self.provides_artifact(
                            child,
                            ArtifactCategory.BINARY_PACKAGE,
                            f"build-{arch}",
                            data={
                                "binary_names": ["hello"],
                                "architecture": arch,
                                "source_package_name": "hello",
                            },
                        )

                data = {
                    "source_artifact": 20,
                    "binary_artifacts": [30, 31],
                    "vendor": "debian",
                    "codename": "bookworm",
                }

                qa = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="qa",
                    task_data={**data, **extra_data},
                )
                QAWorkflow(qa).populate()

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

    def test_validate_input_bad_rdep_autopkgtest_suite(self) -> None:
        """validate_input raises errors in looking up a suite."""
        workflow = self.create_qa_workflow(
            extra_task_data={
                "enable_reverse_dependencies_autopkgtest": True,
                "reverse_dependencies_autopkgtest_suite": (
                    "nonexistent@debian:suite"
                ),
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
        with context.disable_permission_checks():
            architectures = ["amd64"]
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )

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
                "source_artifact": source_artifact.id,
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
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "codename": "bookworm",
                "fail_on_severity": "none",
                "source_artifact": source_artifact.id,
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
        with context.disable_permission_checks():
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )
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
        with context.disable_permission_checks():
            architectures = ["amd64"]
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )
            sid = self.playground.create_collection(
                name="sid", category=CollectionCategory.SUITE
            )
            assert isinstance(sid.manager, DebianSuiteManager)
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
            sid.manager.add_source_package(
                dep_source_artifact,
                user=self.playground.get_default_user(),
                component="main",
                section="devel",
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
            sid.manager.add_binary_package(
                dep_binary_artifact,
                user=self.playground.get_default_user(),
                component="main",
                section="devel",
                priority="optional",
            )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "architectures": ["amd64"],
                "enable_reverse_dependencies_autopkgtest": True,
                "reverse_dependencies_autopkgtest_suite": (
                    f"sid@{CollectionCategory.SUITE}"
                ),
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
                "source_artifact": source_artifact.id,
                "binary_artifacts": ["internal@collections/name:build-amd64"],
                "suite_collection": f"sid@{CollectionCategory.SUITE}",
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
    def test_populate_architectures_allowed(self) -> None:
        """Populate uses architectures and architectures_allowed."""
        with context.disable_permission_checks():
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )
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
                "source_artifact": source_artifact.id,
                "vendor": "debian",
                "extra_repositories": None,
            },
        )

    @preserve_task_registry()
    def test_architectures_deny(self) -> None:
        """Populate uses architectures and architectures_denylist."""
        with context.disable_permission_checks():
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )
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
                "source_artifact": source_artifact.id,
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
                "lintian_fail_on_severity": "pedantic",
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
        self.assertEqual(lintian.task_data["fail_on_severity"], "pedantic")

        piuparts = workflow.children.get(
            task_name="piuparts", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(piuparts.task_data["backend"], BackendType.INCUS_LXC)
        self.assertEqual(
            piuparts.task_data["environment"], "debian/match:codename=trixie"
        )

    def test_get_label(self) -> None:
        """Test get_label()."""
        w = self.create_qa_workflow()
        self.assertEqual(w.get_label(), "run QA")
