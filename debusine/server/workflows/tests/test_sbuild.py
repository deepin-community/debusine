# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the SbuildWorkflow class."""
from typing import Any

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.db.models import (
    Artifact,
    CollectionItem,
    TaskDatabase,
    WorkRequest,
    WorkflowTemplate,
    default_workspace,
)
from debusine.server.collections.tests.utils import CollectionTestMixin
from debusine.server.workflows import (
    SbuildWorkflow,
    WorkflowValidationError,
    workflow_utils,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import SbuildWorkflowData
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    ActionTypes,
    BackendType,
    BaseDynamicTaskData,
    SbuildInput,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import create_system_tarball_data


class SbuildWorkflowTests(CollectionTestMixin, TestCase):
    """Unit tests for SbuildWorkflow class."""

    def create_sbuild_template(
        self, name: str = "sbuild", task_data: dict[str, Any] | None = None
    ) -> WorkflowTemplate:
        """Create a sbuild WorkflowTemplate."""
        return WorkflowTemplate.objects.create(
            name=name,
            workspace=default_workspace(),
            task_name="sbuild",
            task_data=task_data or {},
        )

    def test_create_orchestrator(self) -> None:
        """Test instantiating a SbuildWorkflow."""
        task_data: dict[str, Any] = {
            "input": {
                "source_artifact": 42,
            },
            "target_distribution": "debian:bookworm",
            "architectures": ["all", "amd64"],
        }
        wr = WorkRequest(
            task_data=task_data,
            workspace=default_workspace(),
        )
        w = SbuildWorkflow(wr)
        self.assertEqual(w.data.input.source_artifact, 42)
        self.assertEqual(w.data.target_distribution, "debian:bookworm")
        self.assertEqual(w.data.backend, BackendType.UNSHARE)
        self.assertEqual(w.data.architectures, ["all", "amd64"])
        self.assertIsNone(w.data.extra_repositories)
        self.assertIsNone(w.data.binnmu)

        task_data["binnmu"] = {
            "changelog": "Rebuild for 64bit time_t",
            "suffix": "+b1",
        }
        task_data["extra_repositories"] = [
            {
                "url": "http://example.net/foo",
                "suite": "bookworm",
                "components": ["main", "non-free"],
            },
        ]
        wr = WorkRequest(
            task_data=task_data,
            workspace=default_workspace(),
        )
        w = SbuildWorkflow(wr)
        assert w.data.binnmu is not None
        self.assertEqual(w.data.binnmu.suffix, "+b1")
        assert w.data.extra_repositories is not None
        self.assertEqual(w.data.extra_repositories[0].suite, "bookworm")

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data includes a parameter summary."""
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1", architectures={"all"}
        )
        self.playground.create_debian_environment(codename="bookworm")
        wr = self.playground.create_workflow(
            task_name="sbuild",
            task_data=SbuildWorkflowData(
                input=SbuildInput(source_artifact=source_artifact.id),
                target_distribution="debian:bookworm",
                architectures=["all"],
            ),
        )
        workflow = SbuildWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="hello_1.0-1"
            ),
        )

    def test_validate_input(self) -> None:
        """Test that validate_input passes a valid case."""
        tarball_item = self.playground.create_debian_environment(
            variant="sbuild"
        )

        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": "all"}
        )

        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {
                    "source_artifact": source.pk,
                },
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64"],
            },
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)

        o = SbuildWorkflow(wr)
        self.assertEqual(workflow_utils.source_package(o), source)
        self.assertEqual(o.architectures, {"all"})
        self.assertEqual(o._get_environment("amd64"), tarball_item.artifact)
        o.validate_input()

        # environment_variant
        tarball, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(
                codename="bookworm", variant="buildd"
            ),
        )
        # add with explicit :variant
        tarball_item = self.playground.create_debian_environment(
            environment=tarball_item.artifact,
            variables={"variant": "buildd", "backend": "unshare"},
        )

        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {
                    "source_artifact": source.pk,
                },
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64"],
                "environment_variant": "buildd",
            },
        )
        o = SbuildWorkflow(wr)
        self.assertEqual(o._get_environment("amd64"), tarball_item.artifact)
        o.validate_input()

    def test_computation_of_architecture_intersection(self) -> None:
        """Test computation of architectures to build."""
        template = self.create_sbuild_template()

        dsc_archs = {
            "any": {"amd64", "arm64"},
            "all": {
                "all",
            },
            "any all": {"all", "amd64", "arm64"},
            "arm64": {
                "arm64",
            },
            "linux-any": {"amd64", "arm64"},
            "gnu-any-any": {"amd64", "arm64"},
        }
        for dsc_arch, expected_archs in dsc_archs.items():
            source = self.create_source_package(
                "hello", "1.0", dsc_fields={"Architecture": dsc_arch}
            )

            for wr_archs in (["amd64"], ["all"], ["arm64", "all"]):
                with self.subTest("", dsc_arch=dsc_arch, wr_archs=wr_archs):
                    o = SbuildWorkflow(
                        self.playground.create_workflow(
                            template,
                            task_data={
                                "input": {
                                    "source_artifact": source.pk,
                                },
                                "target_distribution": "debian:bookworm",
                                "architectures": wr_archs,
                            },
                            validate=False,
                        ),
                    )
                    archs = expected_archs.intersection(wr_archs)
                    if archs:
                        self.assertSetEqual(o.architectures, archs)
                    else:
                        with self.assertRaisesRegex(
                            WorkflowValidationError,
                            "None of the workflow architectures are supported"
                            " by this package",
                        ):
                            o.architectures

    def test_create_work_request_unshare(self) -> None:
        """Test creating a Sbuild workflow workrequest with unshare."""
        environments = self.playground.create_debian_environments_collection()
        tarballs = {
            arch: self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL,
                data=create_system_tarball_data(
                    codename="bookworm", variant="apt", architecture=arch
                ),
            )[0]
            for arch in ("amd64", "s390x")
        }
        for arch in ("amd64", "s390x"):
            environments.manager.add_artifact(
                tarballs[arch],
                user=self.playground.get_default_user(),
                variables={"backend": "unshare"},
            )
        default_workspace().collections.filter(
            category=CollectionCategory.PACKAGE_BUILD_LOGS
        ).delete()

        # Create a source package
        source = self.create_source_package(
            "hello",
            "1.0",
            dsc_fields={
                "Binary": "hello, hello-dev",
                "Architecture": "all amd64 s390x",
            },
        )

        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {
                    "source_artifact": source.pk,
                },
                "backend": "unshare",
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64", "s390x"],
            },
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        expected_children = [
            (
                "all",
                {
                    "build_components": ["all"],
                    "build_profiles": None,
                    "host_architecture": "amd64",
                },
            ),
            (
                "amd64",
                {
                    "build_components": ["any"],
                    "build_profiles": None,
                    "host_architecture": "amd64",
                },
            ),
            (
                "s390x",
                {
                    "build_components": ["any"],
                    "build_profiles": None,
                    "host_architecture": "s390x",
                },
            ),
        ]

        children = list(WorkRequest.objects.filter(parent=wr).order_by("id"))
        self.assertEqual(len(children), len(expected_children))

        for child, expected_child in zip(children, expected_children):
            expected_arch, expected_data = expected_child
            self.assertEqual(child.status, WorkRequest.Statuses.PENDING)
            self.assertEqual(child.task_type, TaskTypes.WORKER)
            self.assertEqual(child.task_name, "sbuild")
            self.assertEqual(
                child.task_data,
                {
                    'backend': 'unshare',
                    'environment': 'debian/match:codename=bookworm',
                    'input': {'source_artifact': source.pk},
                    'binnmu': None,
                    'extra_repositories': None,
                    'build_dep_resolver': None,
                    'aspcud_criteria': None,
                    **expected_data,
                },
            )
            promise = CollectionItem.objects.get(
                parent_collection=wr.internal_collection,
                child_type=CollectionItem.Types.BARE,
                category=BareDataCategory.PROMISE,
                name=f"build-{expected_arch}",
            )
            self.assertEqual(
                promise.data,
                {
                    "promise_work_request_id": child.id,
                    "promise_workflow_id": wr.id,
                    "promise_category": ArtifactCategory.UPLOAD,
                    "binary_names": ["hello", "hello-dev"],
                    "source_package_name": "hello",
                    "architecture": expected_arch,
                },
            )
            self.assert_work_request_event_reactions(
                child,
                on_success=[
                    {
                        "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                        "artifact_filters": {
                            "category": ArtifactCategory.UPLOAD
                        },
                        "collection": "internal@collections",
                        "created_at": None,
                        "name_template": f"build-{expected_arch}",
                        "variables": {
                            "binary_names": ["hello", "hello-dev"],
                            "source_package_name": "hello",
                            "architecture": expected_arch,
                        },
                    },
                    {
                        "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                        "artifact_filters": {
                            "category": ArtifactCategory.PACKAGE_BUILD_LOG
                        },
                        "collection": "internal@collections",
                        "name_template": f"buildlog-{expected_arch}",
                        "variables": {
                            "architecture": expected_arch,
                        },
                        "created_at": None,
                    },
                ],
            )
            self.assertQuerySetEqual(child.dependencies.all(), [])
            self.assertEqual(
                child.workflow_data_json,
                {
                    "display_name": f"Build {expected_arch}",
                    "step": f"build-{expected_arch}",
                },
            )

        # populate() idempotence
        orchestrator = SbuildWorkflow(wr)
        orchestrator.populate()
        children = list(WorkRequest.objects.filter(parent=wr))
        self.assertEqual(len(children), len(expected_children))

    def test_create_work_request_arch_all_host_architecture(self) -> None:
        """Test using a non-default `arch_all_host_architecture`."""
        environments = self.playground.create_debian_environments_collection()
        tarballs = {
            arch: self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL,
                data=create_system_tarball_data(
                    codename="bookworm", variant="apt", architecture=arch
                ),
            )[0]
            for arch in ("amd64", "s390x")
        }
        for arch in ("amd64", "s390x"):
            environments.manager.add_artifact(
                tarballs[arch],
                user=self.playground.get_default_user(),
                variables={"backend": "unshare"},
            )
        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": "all amd64 s390x"}
        )
        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {
                    "source_artifact": source.pk,
                },
                "backend": "unshare",
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64", "s390x"],
                "arch_all_host_architecture": "s390x",
            },
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        expected_children = [
            (
                "all",
                {
                    "build_components": ["all"],
                    "build_profiles": None,
                    "host_architecture": "s390x",
                },
            ),
            (
                "amd64",
                {
                    "build_components": ["any"],
                    "build_profiles": None,
                    "host_architecture": "amd64",
                },
            ),
            (
                "s390x",
                {
                    "build_components": ["any"],
                    "build_profiles": None,
                    "host_architecture": "s390x",
                },
            ),
        ]

        children = list(WorkRequest.objects.filter(parent=wr).order_by("id"))
        self.assertEqual(len(children), len(expected_children))

    def test_create_work_request_retain_build_logs(self) -> None:
        """Test retaining build logs in a collection."""
        environments = self.playground.create_debian_environments_collection()
        tarball, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(
                codename="bookworm", variant="apt", architecture="amd64"
            ),
        )
        environments.manager.add_artifact(
            tarball,
            user=self.playground.get_default_user(),
            variables={"backend": "unshare"},
        )
        build_logs = default_workspace().get_singleton_collection(
            user=self.playground.get_default_user(),
            category=CollectionCategory.PACKAGE_BUILD_LOGS,
        )

        # Create a source package
        source = self.create_source_package(
            "hello",
            "1.0",
            dsc_fields={
                "Binary": "hello, hello-dev",
                "Architecture": "amd64",
            },
        )

        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {"source_artifact": source.pk},
                "backend": "unshare",
                "target_distribution": "debian:bookworm",
                "architectures": ["amd64"],
            },
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        child = WorkRequest.objects.get(parent=wr)
        self.assertEqual(child.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(child.task_type, TaskTypes.WORKER)
        self.assertEqual(child.task_name, "sbuild")
        self.assertEqual(
            child.task_data,
            {
                "input": {"source_artifact": source.pk},
                "host_architecture": "amd64",
                "build_components": ["any"],
                "build_profiles": None,
                "binnmu": None,
                "extra_repositories": None,
                "build_dep_resolver": None,
                "aspcud_criteria": None,
                "backend": "unshare",
                "environment": "debian/match:codename=bookworm",
            },
        )
        promise = CollectionItem.objects.get(
            parent_collection=wr.internal_collection,
            child_type=CollectionItem.Types.BARE,
            category=BareDataCategory.PROMISE,
            name="build-amd64",
        )
        self.assertEqual(
            promise.data,
            {
                "promise_work_request_id": child.id,
                "promise_workflow_id": wr.id,
                "promise_category": ArtifactCategory.UPLOAD,
                "binary_names": ["hello", "hello-dev"],
                "architecture": "amd64",
                "source_package_name": "hello",
            },
        )
        self.assert_work_request_event_reactions(
            child,
            on_creation=[
                {
                    "action": ActionTypes.UPDATE_COLLECTION_WITH_DATA,
                    "collection": build_logs.id,
                    "category": BareDataCategory.PACKAGE_BUILD_LOG,
                    "name_template": None,
                    "data": {
                        "work_request_id": child.id,
                        "vendor": "debian",
                        "codename": "bookworm",
                        "architecture": "amd64",
                        "srcpkg_name": "hello",
                        "srcpkg_version": "1.0",
                    },
                    "created_at": None,
                },
            ],
            on_unblock=[],
            on_success=[
                {
                    "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                    "artifact_filters": {"category": ArtifactCategory.UPLOAD},
                    "collection": "internal@collections",
                    "created_at": None,
                    "name_template": "build-amd64",
                    "variables": {
                        "binary_names": ["hello", "hello-dev"],
                        "architecture": "amd64",
                        "source_package_name": "hello",
                    },
                },
                {
                    "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                    "artifact_filters": {
                        "category": ArtifactCategory.PACKAGE_BUILD_LOG
                    },
                    "collection": "internal@collections",
                    "name_template": "buildlog-amd64",
                    "variables": {
                        "architecture": "amd64",
                    },
                    "created_at": None,
                },
                {
                    "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                    "collection": build_logs.id,
                    "name_template": None,
                    "variables": {
                        "work_request_id": child.id,
                        "vendor": "debian",
                        "codename": "bookworm",
                        "architecture": "amd64",
                        "srcpkg_name": "hello",
                        "srcpkg_version": "1.0",
                    },
                    "artifact_filters": {
                        "category": ArtifactCategory.PACKAGE_BUILD_LOG
                    },
                    "created_at": None,
                },
            ],
            on_failure=[],
        )
        self.assertQuerySetEqual(child.dependencies.all(), [])
        self.assertEqual(
            child.workflow_data_json,
            {"display_name": "Build amd64", "step": "build-amd64"},
        )

    def test_create_work_request_retry_delays(self) -> None:
        """Test adding automatic retries with delays."""
        environments = self.playground.create_debian_environments_collection()
        tarball, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(
                codename="bookworm", variant="apt", architecture="amd64"
            ),
        )
        environments.manager.add_artifact(
            tarball,
            user=self.playground.get_default_user(),
            variables={"backend": "unshare"},
        )
        source = self.create_source_package(
            "hello",
            "1.0",
            dsc_fields={
                "Binary": "hello, hello-dev",
                "Architecture": "amd64",
            },
        )
        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {"source_artifact": source.pk},
                "backend": "unshare",
                "target_distribution": "debian:bookworm",
                "architectures": ["amd64"],
                "retry_delays": ["1h", "1d"],
            },
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        child = WorkRequest.objects.get(parent=wr)
        self.assertEqual(child.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(child.task_type, TaskTypes.WORKER)
        self.assertEqual(child.task_name, "sbuild")
        self.assertEqual(
            child.task_data,
            {
                "input": {"source_artifact": source.pk},
                "host_architecture": "amd64",
                "build_components": ["any"],
                "build_profiles": None,
                "binnmu": None,
                "extra_repositories": None,
                "build_dep_resolver": None,
                "aspcud_criteria": None,
                "backend": "unshare",
                "environment": "debian/match:codename=bookworm",
            },
        )
        self.assertIn(
            {
                "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                "artifact_filters": {"category": ArtifactCategory.UPLOAD},
                "collection": "internal@collections",
                "created_at": None,
                "name_template": "build-amd64",
                "variables": {
                    "binary_names": ["hello", "hello-dev"],
                    "architecture": "amd64",
                    "source_package_name": "hello",
                },
            },
            child.event_reactions_json["on_success"],
        )
        self.assertIn(
            {
                "action": ActionTypes.RETRY_WITH_DELAYS,
                "delays": ["1h", "1d"],
            },
            child.event_reactions_json["on_failure"],
        )

    def test_create_work_request_signing_template_names(self) -> None:
        """Test signing template handling."""
        environments = self.playground.create_debian_environments_collection()
        tarball, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(
                codename="bookworm", variant="apt", architecture="amd64"
            ),
        )
        environments.manager.add_artifact(
            tarball,
            user=self.playground.get_default_user(),
            variables={"backend": "unshare"},
        )
        source = self.create_source_package(
            "hello",
            "1.0",
            dsc_fields={
                "Binary": "hello-unsigned",
                "Architecture": "amd64",
            },
        )
        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {"source_artifact": source.pk},
                "backend": "unshare",
                "target_distribution": "debian:bookworm",
                "architectures": ["amd64"],
                "signing_template_names": {"amd64": ["hello-unsigned"]},
            },
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        child = WorkRequest.objects.get(parent=wr)
        self.assertEqual(child.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(child.task_type, TaskTypes.WORKER)
        self.assertEqual(child.task_name, "sbuild")
        self.assertIn(
            {
                "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                "artifact_filters": {"category": ArtifactCategory.UPLOAD},
                "collection": "internal@collections",
                "created_at": None,
                "name_template": "build-amd64",
                "variables": {
                    "binary_names": ["hello-unsigned"],
                    "architecture": "amd64",
                    "source_package_name": "hello",
                },
            },
            child.event_reactions_json["on_success"],
        )
        self.assertIn(
            {
                "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                "artifact_filters": {
                    "category": ArtifactCategory.BINARY_PACKAGE,
                    "data__deb_fields__Package": "hello-unsigned",
                },
                "collection": "internal@collections",
                "created_at": None,
                "name_template": "signing-template-amd64-hello-unsigned",
                "variables": {
                    "architecture": "amd64",
                    "binary_package_name": "hello-unsigned",
                },
            },
            child.event_reactions_json["on_success"],
        )

    def test_source_package_error_handling(self) -> None:
        """Test source_package error handling."""
        template = self.create_sbuild_template()

        def create_orchestrator(
            source: Artifact,
            target_distribution: str = "debian:bookworm",
            architectures: list[str] = ["amd64"],
            **kwargs: Any,
        ) -> SbuildWorkflow:
            workflow = self.playground.create_workflow(
                template,
                task_data={
                    "input": {
                        "source_artifact": source.pk,
                    },
                    "target_distribution": target_distribution,
                    "architectures": architectures,
                    **kwargs,
                },
                validate=False,
            )
            orchestrator = SbuildWorkflow(workflow)
            return orchestrator

        invalid_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )
        orchestrator = create_orchestrator(invalid_artifact)
        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.source_artifact: unexpected artifact category: "
            r"'debusine:test'. Valid categories: "
            r"\['debian:source-package', 'debian:upload'\]$",
        ):
            workflow_utils.source_package_data(orchestrator)

        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": ""}
        )
        orchestrator = create_orchestrator(source)
        with self.assertRaisesRegex(
            WorkflowValidationError, "package architecture list is empty"
        ):
            orchestrator.architectures

        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": "any-armhf"}
        )
        orchestrator = create_orchestrator(
            source, architectures=["kfreebsd-i386"]
        )
        with self.assertRaisesRegex(
            WorkflowValidationError,
            "None of the workflow architectures are supported by this package",
        ):
            orchestrator.architectures

        # wildcards support
        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": "any-amd64"}
        )
        orchestrator = create_orchestrator(
            source, architectures=["i386", "hurd-amd64"]
        )
        self.assertEqual(orchestrator.architectures, {"hurd-amd64"})

        # wildcards are for packages, not workflows
        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": "amd64"}
        )
        orchestrator = create_orchestrator(source, architectures=["any"])
        with self.assertRaisesRegex(
            WorkflowValidationError,
            "None of the workflow architectures are supported by this package",
        ):
            orchestrator.architectures

        orchestrator = create_orchestrator(source, backend="qemu")

        self.playground.create_debian_environments_collection()
        with self.assertRaisesRegex(
            WorkflowValidationError, "environment not found for"
        ):
            orchestrator._get_environment("any")

        orchestrator = create_orchestrator(source, target_distribution="Ubuntu")
        with self.assertRaisesRegex(
            WorkflowValidationError,
            "target_distribution must be in vendor:codename format",
        ):
            orchestrator.validate_input()

    def test_populate_upload(self) -> None:
        """The workflow accepts debian:upload artifacts."""
        upload_artifacts = self.playground.create_upload_artifacts()
        self.playground.create_debian_environment(codename="bookworm")

        wf = self.playground.create_workflow(
            task_name="sbuild",
            task_data=SbuildWorkflowData(
                input=SbuildInput(
                    source_artifact=upload_artifacts.upload.id,
                ),
                target_distribution="debian:bookworm",
                architectures=["amd64"],
            ),
        )
        SbuildWorkflow(wf).populate()

        children = list(WorkRequest.objects.filter(parent=wf))
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(
            child.task_data["input"]["source_artifact"],
            f"{upload_artifacts.source.id}@artifacts",
        )

    def test_populate_experimental(self) -> None:
        """The workflow handles overlay distributions."""
        source_artifact = self.playground.create_source_artifact(
            architectures={"all"}
        )
        self.playground.create_debian_environment(
            codename="experimental", variant="sbuild"
        )
        wr = self.playground.create_workflow(
            task_name="sbuild",
            task_data=SbuildWorkflowData(
                input=SbuildInput(
                    source_artifact=source_artifact.id,
                ),
                target_distribution="debian:experimental",
                arch_all_host_architecture="amd64",
                architectures=["all"],
            ),
        )
        SbuildWorkflow(wr).populate()
        children = list(wr.children.all())
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(len(child.task_data["extra_repositories"]), 1)
        repo = child.task_data["extra_repositories"][0]
        self.assertEqual(repo["suite"], "experimental")

        self.assertEqual(child.task_data["build_dep_resolver"], "aspcud")
        self.assertEqual(
            child.task_data["aspcud_criteria"],
            (
                "-count(down),-count(changed,APT-Release:=/experimental/),"
                "-removed,-changed,-new"
            ),
        )

    def test_label(self) -> None:
        """Test get_label."""
        source = self.create_source_package(
            "hello", "1.0", dsc_fields={"Architecture": "all"}
        )
        self.playground.create_debian_environment(codename="bookworm")
        template = self.create_sbuild_template()
        wr = self.playground.create_workflow(
            template,
            task_data={
                "input": {
                    "source_artifact": source.id,
                },
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64"],
            },
        )

        workflow = SbuildWorkflow(wr)
        self.assertEqual(workflow.get_label(), "sbuild")
