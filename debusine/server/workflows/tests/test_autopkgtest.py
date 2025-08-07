# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the autopkgtest workflow."""

from collections.abc import Iterable
from typing import Any

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    DebusinePromise,
)
from debusine.db.context import context
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.workflows import (
    AutopkgtestWorkflow,
    WorkflowValidationError,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    BaseWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.tasks.models import (
    AutopkgtestNeedsInternet,
    BackendType,
    BaseDynamicTaskData,
    ExtraRepository,
    LookupMultiple,
    SbuildData,
    SbuildInput,
    TaskTypes,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class AutopkgtestWorkflowTests(TestCase):
    """Unit tests for :py:class:`AutopkgtestWorkflow`."""

    def create_autopkgtest_workflow(
        self,
        extra_task_data: dict[str, Any] | None = None,
        parent: WorkRequest | None = None,
    ) -> AutopkgtestWorkflow:
        """Create an autopkgtest workflow."""
        task_data = {
            "source_artifact": 1,
            "binary_artifacts": ["internal@collections/name:build-amd64"],
            "vendor": "debian",
            "codename": "sid",
        }
        if extra_task_data is not None:
            task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=task_data,
            parent=parent,
            validate=False,
        )
        return AutopkgtestWorkflow(wr)

    def create_binary_upload(
        self, architecture: str, filenames: list[str]
    ) -> Artifact:
        """Create a minimal `debian:upload` artifact with binaries."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD,
            data={
                "type": "dpkg",
                "changes_fields": {
                    "Architecture": architecture,
                    "Files": [{"name": filename} for filename in filenames],
                },
            },
        )
        return artifact

    @context.disable_permission_checks()
    def add_uploads(
        self, work_request: WorkRequest, architectures: Iterable[str]
    ) -> None:
        """Add multiple uploads to a workflow's internal collection."""
        internal_collection = work_request.internal_collection
        assert internal_collection is not None
        for architecture in architectures:
            upload = self.create_binary_upload(
                architecture, [f"hello_1.0-1_{architecture}.deb"]
            )
            internal_collection.manager.add_artifact(
                upload,
                user=work_request.created_by,
                workflow=work_request,
                name=f"build-{architecture}",
                variables={"architecture": architecture},
            )

    def test_create_orchestrator(self) -> None:
        """An AutopkgtestWorkflow can be instantiated."""
        source_artifact = 2
        binary_artifacts = ["internal@collections/name:build-arm64"]
        vendor = "debian"
        codename = "trixie"
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "source_artifact": source_artifact,
                "binary_artifacts": binary_artifacts,
                "vendor": vendor,
                "codename": codename,
            }
        )

        self.assertEqual(w.data.source_artifact, source_artifact)
        self.assertEqual(
            w.data.binary_artifacts, LookupMultiple.parse_obj(binary_artifacts)
        )
        self.assertEqual(w.data.vendor, vendor)
        self.assertEqual(w.data.codename, codename)
        self.assertEqual(w.data.backend, BackendType.UNSHARE)

    def test_create_orchestrator_explicit_backend(self) -> None:
        """An AutopkgtestWorkflow can be instantiated with a backend."""
        w = self.create_autopkgtest_workflow(
            extra_task_data={"backend": "incus-lxc"}
        )

        self.assertEqual(w.data.backend, BackendType.INCUS_LXC)

    def test_architectures(self) -> None:
        """Workflow uses architectures from binary_artifacts."""
        architectures = ("amd64", "arm64", "i386")
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "binary_artifacts": [
                    f"internal@collections/name:build-{architecture}"
                    for architecture in architectures
                ]
            }
        )
        self.add_uploads(w.work_request, architectures)

        self.assertCountEqual(w.architectures, architectures)

    def test_architectures_arch_indep_and_arch_dep(self) -> None:
        """Workflow handles arch-indep plus arch-dep binary_artifacts."""
        architectures = ("all", "arm64", "armhf")
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "binary_artifacts": [
                    f"internal@collections/name:build-{architecture}"
                    for architecture in architectures
                ]
            }
        )
        self.add_uploads(w.work_request, architectures)

        self.assertEqual(w.architectures, {"arm64", "armhf"})

    def test_architectures_arch_indep_only(self) -> None:
        """Workflow handles only having arch-indep binary_artifacts."""
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "binary_artifacts": ["internal@collections/name:build-all"]
            }
        )
        self.add_uploads(w.work_request, ["all"])

        self.assertEqual(w.architectures, {"amd64"})

    def test_architectures_arch_indep_only_arch_all_host_architecture(
        self,
    ) -> None:
        """Workflow honours `arch_all_host_architecture`."""
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "binary_artifacts": ["internal@collections/name:build-all"],
                "arch_all_host_architecture": "s390x",
            }
        )
        self.add_uploads(w.work_request, ["all"])

        self.assertEqual(w.architectures, {"s390x"})

    def test_architectures_intersect_task_data(self) -> None:
        """Setting architectures in task data constrains the set."""
        architectures = ("amd64", "arm64", "i386")
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "binary_artifacts": [
                    f"internal@collections/name:build-{architecture}"
                    for architecture in architectures
                ],
                "architectures": ["amd64", "i386"],
            }
        )
        self.add_uploads(w.work_request, architectures)

        self.assertEqual(w.architectures, {"amd64", "i386"})

    def test_validate_input(self) -> None:
        """validate_input passes a valid case."""
        w = self.create_autopkgtest_workflow()
        self.add_uploads(w.work_request, ("amd64",))

        w.validate_input()

    def test_validate_input_architecture_errors(self) -> None:
        """validate_input raises errors in computing architectures."""
        w = self.create_autopkgtest_workflow()
        assert w.work_request.internal_collection is not None
        # Create a promise with no architecture.
        self.playground.create_bare_data_item(
            w.work_request.internal_collection,
            "build-amd64",
            category=BareDataCategory.PROMISE,
            data=DebusinePromise(
                promise_work_request_id=w.work_request.id + 1,
                promise_workflow_id=w.work_request.id,
                promise_category=ArtifactCategory.UPLOAD,
            ),
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "Cannot determine architecture for lookup result",
        ):
            w.validate_input()

    def test_populate(self) -> None:
        """The workflow populates child work requests."""
        architectures = ("amd64", "i386")
        source_artifact = self.playground.create_source_artifact()

        with preserve_task_registry():

            class ExamplePipeline(
                TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
            ):
                """Pipeline workflow that runs sbuild and autopkgtest."""

                def populate(self) -> None:
                    """Populate the pipeline."""
                    sbuild = self.work_request.create_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="sbuild",
                        task_data=SbuildWorkflowData(
                            input=SbuildInput(
                                source_artifact=source_artifact.id
                            ),
                            target_distribution="debian:sid",
                            architectures=["all", *architectures],
                        ),
                    )
                    for architecture in ("all", *architectures):
                        child = sbuild.create_child(
                            task_name="sbuild",
                            task_data=SbuildData(
                                input=SbuildInput(
                                    source_artifact=source_artifact.id
                                ),
                                host_architecture=architecture,
                                environment="debian/match:codename=sid",
                            ),
                        )
                        self.provides_artifact(
                            child,
                            ArtifactCategory.UPLOAD,
                            f"build-{architecture}",
                            data={"architecture": architecture},
                        )

                    autopkgtest = self.work_request.create_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="autopkgtest",
                        task_data=AutopkgtestWorkflowData(
                            source_artifact=source_artifact.id,
                            binary_artifacts=LookupMultiple.parse_obj(
                                [
                                    f"internal@collections/"
                                    f"name:build-{architecture}"
                                    for architecture in ("all", *architectures)
                                ]
                            ),
                            vendor="debian",
                            codename="sid",
                            extra_repositories=[
                                ExtraRepository.parse_obj(
                                    {
                                        "url": "http://example.com/",
                                        "suite": "bookworm",
                                        "components": ["main"],
                                    }
                                )
                            ],
                        ),
                    )
                    AutopkgtestWorkflow(autopkgtest).populate()

            root = self.playground.create_workflow(task_name="examplepipeline")

            root.mark_running()
            orchestrate_workflow(root)

            autopkgtest = WorkRequest.objects.get(
                task_type=TaskTypes.WORKFLOW,
                task_name="autopkgtest",
                parent=root,
            )
            children = list(
                WorkRequest.objects.filter(parent=autopkgtest).order_by(
                    "task_data__host_architecture"
                )
            )
            self.assertEqual(len(children), len(architectures))
            for child, architecture in zip(children, architectures):
                self.assertEqual(child.status, WorkRequest.Statuses.BLOCKED)
                self.assertEqual(child.task_type, TaskTypes.WORKER)
                self.assertEqual(child.task_name, "autopkgtest")
                self.assertEqual(
                    child.task_data,
                    {
                        "input": {
                            "source_artifact": autopkgtest.task_data[
                                "source_artifact"
                            ],
                            "binary_artifacts": sorted(
                                [
                                    f"internal@collections/"
                                    f"name:build-{architecture}",
                                    "internal@collections/name:build-all",
                                ]
                            ),
                            "context_artifacts": [],
                        },
                        "host_architecture": architecture,
                        "environment": "debian/match:codename=sid",
                        "extra_repositories": [
                            {
                                "url": "http://example.com/",
                                "suite": "bookworm",
                                "components": ["main"],
                            }
                        ],
                        "backend": "unshare",
                        "include_tests": [],
                        "exclude_tests": [],
                        "debug_level": 0,
                        "extra_environment": {},
                        "needs_internet": AutopkgtestNeedsInternet.RUN,
                        "fail_on": {},
                        "timeout": None,
                    },
                )
                self.assertEqual(
                    child.event_reactions_json,
                    {
                        "on_creation": [],
                        "on_failure": [],
                        "on_success": [
                            {
                                "action": "update-collection-with-artifacts",
                                "collection": "internal@collections",
                                "name_template": f"autopkgtest-{architecture}",
                                "variables": None,
                                "artifact_filters": {
                                    "category": ArtifactCategory.AUTOPKGTEST
                                },
                            }
                        ],
                        "on_unblock": [],
                    },
                )
                self.assertQuerySetEqual(
                    child.dependencies.all(),
                    list(
                        WorkRequest.objects.filter(
                            task_type=TaskTypes.WORKER,
                            task_name="sbuild",
                            task_data__host_architecture__in={
                                architecture,
                                "all",
                            },
                        )
                    ),
                )
                self.assertEqual(
                    child.workflow_data_json,
                    {
                        "display_name": f"autopkgtest {architecture}",
                        "step": f"autopkgtest-{architecture}",
                    },
                )

            # Population is idempotent.
            AutopkgtestWorkflow(autopkgtest).populate()
            children = list(WorkRequest.objects.filter(parent=autopkgtest))
            self.assertEqual(len(children), len(architectures))

    def test_populate_upload(self) -> None:
        """The workflow accepts debian:upload source artifacts."""
        architectures = ("amd64", "i386")
        upload_artifacts = self.playground.create_upload_artifacts()

        with preserve_task_registry():

            class ExamplePipeline(
                TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
            ):
                """Pipeline workflow that runs sbuild and autopkgtest."""

                def populate(self) -> None:
                    """Populate the pipeline."""
                    sbuild = self.work_request.create_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="sbuild",
                        task_data=SbuildWorkflowData(
                            input=SbuildInput(
                                source_artifact=upload_artifacts.upload.id
                            ),
                            target_distribution="debian:sid",
                            architectures=["all", *architectures],
                        ),
                    )
                    for architecture in ("all", *architectures):
                        child = sbuild.create_child(
                            task_name="sbuild",
                            task_data=SbuildData(
                                input=SbuildInput(
                                    source_artifact=(
                                        f"{upload_artifacts.source.id}"
                                        f"@artifacts"
                                    )
                                ),
                                host_architecture=architecture,
                                environment="debian/match:codename=sid",
                            ),
                        )
                        self.provides_artifact(
                            child,
                            ArtifactCategory.UPLOAD,
                            f"build-{architecture}",
                            data={"architecture": architecture},
                        )

                    autopkgtest = self.work_request.create_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="autopkgtest",
                        task_data=AutopkgtestWorkflowData(
                            source_artifact=upload_artifacts.upload.id,
                            binary_artifacts=LookupMultiple.parse_obj(
                                [
                                    f"internal@collections/"
                                    f"name:build-{architecture}"
                                    for architecture in ("all", *architectures)
                                ]
                            ),
                            vendor="debian",
                            codename="sid",
                            extra_repositories=[
                                ExtraRepository.parse_obj(
                                    {
                                        "url": "http://example.com/",
                                        "suite": "bookworm",
                                        "components": ["main"],
                                    }
                                )
                            ],
                        ),
                    )
                    AutopkgtestWorkflow(autopkgtest).populate()

            root = self.playground.create_workflow(task_name="examplepipeline")

            root.mark_running()
            orchestrate_workflow(root)

            autopkgtest = WorkRequest.objects.get(
                task_type=TaskTypes.WORKFLOW,
                task_name="autopkgtest",
                parent=root,
            )
            children = list(
                WorkRequest.objects.filter(parent=autopkgtest).order_by(
                    "task_data__host_architecture"
                )
            )
            self.assertEqual(len(children), len(architectures))
            for child, architecture in zip(children, architectures):
                self.assertEqual(child.status, WorkRequest.Statuses.BLOCKED)
                self.assertEqual(child.task_type, TaskTypes.WORKER)
                self.assertEqual(child.task_name, "autopkgtest")
                self.assertEqual(
                    child.task_data,
                    {
                        "input": {
                            "source_artifact": (
                                f"{upload_artifacts.source.id}@artifacts"
                            ),
                            "binary_artifacts": sorted(
                                [
                                    f"internal@collections/"
                                    f"name:build-{architecture}",
                                    "internal@collections/name:build-all",
                                ]
                            ),
                            "context_artifacts": [],
                        },
                        "host_architecture": architecture,
                        "environment": "debian/match:codename=sid",
                        "extra_repositories": [
                            {
                                "url": "http://example.com/",
                                "suite": "bookworm",
                                "components": ["main"],
                            }
                        ],
                        "backend": "unshare",
                        "include_tests": [],
                        "exclude_tests": [],
                        "debug_level": 0,
                        "extra_environment": {},
                        "needs_internet": AutopkgtestNeedsInternet.RUN,
                        "fail_on": {},
                        "timeout": None,
                    },
                )
                self.assertEqual(
                    child.event_reactions_json,
                    {
                        "on_creation": [],
                        "on_failure": [],
                        "on_success": [
                            {
                                "action": "update-collection-with-artifacts",
                                "collection": "internal@collections",
                                "name_template": f"autopkgtest-{architecture}",
                                "variables": None,
                                "artifact_filters": {
                                    "category": ArtifactCategory.AUTOPKGTEST
                                },
                            }
                        ],
                        "on_unblock": [],
                    },
                )
                self.assertQuerySetEqual(
                    child.dependencies.all(),
                    list(
                        WorkRequest.objects.filter(
                            task_type=TaskTypes.WORKER,
                            task_name="sbuild",
                            task_data__host_architecture__in={
                                architecture,
                                "all",
                            },
                        )
                    ),
                )
                self.assertEqual(
                    child.workflow_data_json,
                    {
                        "display_name": f"autopkgtest {architecture}",
                        "step": f"autopkgtest-{architecture}",
                    },
                )

            # Population is idempotent.
            AutopkgtestWorkflow(autopkgtest).populate()
            children = list(WorkRequest.objects.filter(parent=autopkgtest))
            self.assertEqual(len(children), len(architectures))

    def test_populate_experimental(self) -> None:
        """The workflow handles overlay distributions."""
        source_artifact = self.playground.create_source_artifact()
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact()
        )
        self.playground.create_debian_environment(codename="experimental")
        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="experimental",
            ),
        )
        AutopkgtestWorkflow(wr).populate()
        children = list(wr.children.all())
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(len(child.task_data["extra_repositories"]), 1)
        repo = child.task_data["extra_repositories"][0]
        self.assertEqual(repo["suite"], "experimental")

    def test_compute_dynamic_data(self) -> None:
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact()
        )
        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = AutopkgtestWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="hello_1.0-1"
            ),
        )

    def test_label(self) -> None:
        """Test get_label."""
        w = self.create_autopkgtest_workflow()
        self.assertEqual(w.get_label(), "run autopkgtests")
