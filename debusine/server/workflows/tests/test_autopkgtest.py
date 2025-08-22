# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the autopkgtest workflow."""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from django.test import override_settings
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebusinePromise,
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
from debusine.server.scheduler import schedule
from debusine.server.workflows import (
    AutopkgtestWorkflow,
    SbuildWorkflow,
    WorkflowValidationError,
    workflow_utils,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    BaseWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks.models import (
    AutopkgtestNeedsInternet,
    BackendType,
    BaseDynamicTaskData,
    ExtraRepository,
    LookupMultiple,
    OutputData,
    RegressionAnalysis,
    RegressionAnalysisStatus,
    SbuildData,
    SbuildInput,
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

    def add_qa_result(
        self,
        qa_results: Collection,
        package: str,
        version: str,
        architecture: str,
        *,
        timestamp: datetime | None = None,
    ) -> CollectionItem:
        """Add an autopkgtest result to a ``debian:qa-results`` collection."""
        return qa_results.manager.add_artifact(
            self.playground.create_artifact(
                category=ArtifactCategory.AUTOPKGTEST
            )[0],
            user=self.playground.get_default_user(),
            variables={
                "package": package,
                "version": version,
                "architecture": architecture,
                "timestamp": int((timestamp or timezone.now()).timestamp()),
                "work_request_id": self.playground.create_work_request(
                    task_name="autopkgtest", result=WorkRequest.Results.SUCCESS
                ).id,
            },
        )

    def orchestrate(
        self,
        task_data: AutopkgtestWorkflowData,
        architectures: Sequence[str],
        parent: WorkRequest | None = None,
        pipeline_task_name: str = "examplepipeline",
    ) -> WorkRequest:
        """Create and orchestrate an AutopkgtestWorkflow."""

        class ExamplePipeline(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Pipeline workflow that runs sbuild and autopkgtest."""

            TASK_NAME = pipeline_task_name

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
                        architectures=["all", *architectures],
                    ),
                    status=WorkRequest.Statuses.PENDING,
                )
                source_artifact = (
                    workflow_utils.locate_debian_source_package_lookup(
                        SbuildWorkflow(sbuild),
                        "input.source_artifact",
                        task_data.source_artifact,
                    )
                )
                sbuild.mark_running()
                for architecture in ("all", *architectures):
                    child = sbuild.create_child(
                        task_name="sbuild",
                        task_data=SbuildData(
                            input=SbuildInput(source_artifact=source_artifact),
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
                sbuild.unblock_workflow_children()

                autopkgtest = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="autopkgtest",
                    task_data=task_data,
                    status=WorkRequest.Statuses.PENDING,
                )
                autopkgtest.mark_running()
                AutopkgtestWorkflow(autopkgtest).populate()

        root = self.playground.create_workflow(
            task_name=pipeline_task_name, parent=parent
        )

        root.mark_running()
        orchestrate_workflow(root)

        return root

    def assertRunsWorkflowCallback(self, parent: WorkRequest) -> None:
        """Assert that the scheduler runs a workflow callback."""
        with self.captureOnCommitCallbacks() as on_commit:
            [workflow_callback] = schedule()
            self.assertEqual(workflow_callback.task_type, TaskTypes.INTERNAL)
            self.assertEqual(workflow_callback.task_name, "workflow")
            self.assertEqual(workflow_callback.parent, parent)
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            for callback in on_commit:
                callback()

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

    def test_validate_input_bad_qa_suite(self) -> None:
        """validate_input raises errors in looking up a suite."""
        w = self.create_autopkgtest_workflow(
            extra_task_data={"qa_suite": "nonexistent@debian:suite"}
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:suite' does not exist or is hidden",
        ):
            w.validate_input()

    def test_validate_input_bad_reference_qa_results(self) -> None:
        """validate_input raises errors in looking up reference QA results."""
        w = self.create_autopkgtest_workflow(
            extra_task_data={
                "reference_qa_results": "nonexistent@debian:qa-results"
            }
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:qa-results' does not exist or is hidden",
        ):
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

    def test_has_current_reference_qa_result_no_match(self) -> None:
        """_has_current_reference_qa_result: no matching result."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(sid_qa_results, "other", "1.0-1", "amd64")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )

        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
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
            AutopkgtestWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_different_version(self) -> None:
        """_has_current_reference_qa_result: result for different version."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-2"
        )

        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
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
            AutopkgtestWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_too_old(self) -> None:
        """_has_current_reference_qa_result: result is too old."""
        now = timezone.now()
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(
            sid_qa_results,
            "hello",
            "1.0-1",
            "amd64",
            timestamp=now - timedelta(days=31),
        )
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        source_item = sid_suite.manager.add_artifact(
            source_artifact,
            user=self.playground.get_default_user(),
            variables={"component": "main", "section": "devel"},
        )
        source_item.created_at = now
        source_item.save()

        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
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
            AutopkgtestWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    def test_has_current_reference_qa_result_current(self) -> None:
        """_has_current_reference_qa_result: current result."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )

        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
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
            AutopkgtestWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    @preserve_task_registry()
    def test_populate(self) -> None:
        """The workflow populates child work requests."""
        architectures = ("amd64", "i386")
        source_artifact = self.playground.create_source_artifact()

        root = self.orchestrate(
            task_data=AutopkgtestWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{architecture}"
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
            architectures=architectures,
        )

        autopkgtest = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW, task_name="autopkgtest", parent=root
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
            self.assert_work_request_event_reactions(
                child,
                on_success=[
                    {
                        "action": "update-collection-with-artifacts",
                        "collection": "internal@collections",
                        "name_template": f"autopkgtest-{architecture}",
                        "variables": None,
                        "artifact_filters": {
                            "category": ArtifactCategory.AUTOPKGTEST
                        },
                        "created_at": None,
                    }
                ],
            )
            self.assertQuerySetEqual(
                child.dependencies.all(),
                list(
                    WorkRequest.objects.filter(
                        task_type=TaskTypes.WORKER,
                        task_name="sbuild",
                        task_data__host_architecture__in={architecture, "all"},
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

    @preserve_task_registry()
    def test_populate_upload(self) -> None:
        """The workflow accepts debian:upload source artifacts."""
        architectures = ("amd64", "i386")
        upload_artifacts = self.playground.create_upload_artifacts()

        root = self.orchestrate(
            task_data=AutopkgtestWorkflowData(
                source_artifact=upload_artifacts.upload.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{architecture}"
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
            architectures=architectures,
        )

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
            self.assert_work_request_event_reactions(
                child,
                on_success=[
                    {
                        "action": "update-collection-with-artifacts",
                        "collection": "internal@collections",
                        "name_template": f"autopkgtest-{architecture}",
                        "variables": None,
                        "artifact_filters": {
                            "category": ArtifactCategory.AUTOPKGTEST
                        },
                        "created_at": None,
                    }
                ],
            )
            self.assertQuerySetEqual(
                child.dependencies.all(),
                list(
                    WorkRequest.objects.filter(
                        task_type=TaskTypes.WORKER,
                        task_name="sbuild",
                        task_data__host_architecture__in={architecture, "all"},
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
        self.playground.create_debian_environment(
            codename="experimental", variant="autopkgtest"
        )
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

    def test_populate_has_current_reference_qa_result(self) -> None:
        """The workflow does nothing with a current reference QA result."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(sid_qa_results, "hello", "1.0-1", "amd64")

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
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        AutopkgtestWorkflow(wr).populate()

        self.assertQuerySetEqual(wr.children.all(), [])

    def test_populate_previous_reference_qa_result_too_old(self) -> None:
        """The workflow produces a reference QA result if it is outdated."""
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        old_qa_result = self.add_qa_result(
            sid_qa_results,
            "hello",
            "1.0-1",
            "amd64",
            timestamp=timezone.now() - timedelta(days=31),
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
        source_item = sid_suite.manager.add_artifact(
            source_artifact,
            user=self.playground.get_default_user(),
            variables={"component": "main", "section": "devel"},
        )
        wr = self.playground.create_workflow(
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        AutopkgtestWorkflow(wr).populate()

        [child] = wr.children.all()
        self.assertEqual(child.status, WorkRequest.Statuses.BLOCKED)
        self.assertEqual(child.task_type, TaskTypes.WORKER)
        self.assertEqual(child.task_name, "autopkgtest")
        self.assertEqual(
            child.task_data,
            {
                "input": {
                    "source_artifact": source_artifact.id,
                    "binary_artifacts": [f"{binary_artifact.id}@artifacts"],
                    "context_artifacts": [],
                },
                "host_architecture": "amd64",
                "environment": "debian/match:codename=sid",
                "extra_repositories": None,
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
        qa_result_action = {
            "action": "update-collection-with-artifacts",
            "collection": f"sid@{CollectionCategory.QA_RESULTS}",
            "name_template": None,
            "variables": {
                "package": "hello",
                "version": "1.0-1",
                "architecture": "amd64",
                "timestamp": int(source_item.created_at.timestamp()),
                "work_request_id": child.id,
            },
            "artifact_filters": {"category": ArtifactCategory.AUTOPKGTEST},
            "created_at": None,
        }
        self.assert_work_request_event_reactions(
            child,
            on_assignment=[
                {
                    "action": "skip-if-lookup-result-changed",
                    "lookup": (
                        f"sid@{CollectionCategory.QA_RESULTS}/"
                        f"latest:autopkgtest_hello_amd64"
                    ),
                    "collection_item_id": old_qa_result.id,
                    "promise_name": "reference-qa-result|autopkgtest-amd64",
                }
            ],
            on_failure=[qa_result_action],
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "collection": "internal@collections",
                    "name_template": ("reference-qa-result|autopkgtest-amd64"),
                    "variables": None,
                    "artifact_filters": {
                        "category": ArtifactCategory.AUTOPKGTEST
                    },
                    "created_at": None,
                },
                qa_result_action,
            ],
        )

        # Completing the work request stores the QA result.
        self.assertTrue(child.mark_pending())
        result, _ = self.playground.create_artifact(
            category=ArtifactCategory.AUTOPKGTEST, work_request=child
        )
        self.assertTrue(child.mark_completed(WorkRequest.Results.SUCCESS))
        self.assertEqual(
            lookup_single(
                f"sid@{CollectionCategory.QA_RESULTS}/"
                f"latest:autopkgtest_hello_amd64",
                child.workspace,
                user=child.created_by,
                expect_type=LookupChildType.ARTIFACT,
            ).artifact,
            result,
        )

    def test_populate_reference_qa_result_backs_off(self) -> None:
        """Reference tasks are skipped if another workflow got there first."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.playground.create_debian_environment(
            codename="sid", variant="autopkgtest"
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
            task_name="autopkgtest",
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        AutopkgtestWorkflow(wr).populate()

        racing_qa_result = self.add_qa_result(
            sid_qa_results, "hello", "1.0-1", "amd64"
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
                    f"'sid@{CollectionCategory.QA_RESULTS}/"
                    f"latest:autopkgtest_hello_amd64' changed"
                )
            ),
        )
        self.assertEqual(
            lookup_single(
                f"sid@{CollectionCategory.QA_RESULTS}/"
                f"latest:autopkgtest_hello_amd64",
                child.workspace,
                user=child.created_by,
                expect_type=LookupChildType.ARTIFACT,
            ).collection_item,
            racing_qa_result,
        )

    @preserve_task_registry()
    def test_callback_regression_analysis(self) -> None:
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        self.playground.create_collection("sid", CollectionCategory.QA_RESULTS)
        architectures = [
            "amd64",
            "arm64",
            "i386",
            "ppc64el",
            "riscv64",
            "s390x",
        ]
        source_artifact = self.playground.create_source_artifact()

        root = self.playground.create_workflow()
        root.mark_running()
        reference = self.orchestrate(
            task_data=AutopkgtestWorkflowData(
                prefix="reference-qa-result|",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
                architectures=architectures,
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{arch}"
                        for arch in architectures
                        if arch != "ppc64el"
                    ]
                ),
                vendor="debian",
                codename="sid",
            ),
            architectures=architectures,
            parent=root,
            pipeline_task_name="referencepipeline",
        )
        new = self.orchestrate(
            task_data=AutopkgtestWorkflowData(
                reference_prefix="reference-qa-result|",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                enable_regression_tracking=True,
                architectures=architectures,
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{arch}"
                        for arch in architectures
                    ]
                ),
                vendor="debian",
                codename="sid",
            ),
            architectures=architectures,
            parent=root,
        )

        # Unblock the autopkgtest tasks.
        for sbuild in reference.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        ).children.filter(task_type=TaskTypes.WORKER, task_name="sbuild"):
            self.assertTrue(sbuild.mark_completed(WorkRequest.Results.SUCCESS))
        for sbuild in new.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        ).children.filter(task_type=TaskTypes.WORKER, task_name="sbuild"):
            self.assertTrue(sbuild.mark_completed(WorkRequest.Results.SUCCESS))

        reference_autopkgtest_workflow = reference.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="autopkgtest"
        )
        reference_tasks = {
            wr.task_data["host_architecture"]: wr
            for wr in reference_autopkgtest_workflow.children.filter(
                task_type=TaskTypes.WORKER, task_name="autopkgtest"
            )
        }
        reference_results = {
            architecture: self.playground.create_artifact(
                category=ArtifactCategory.AUTOPKGTEST,
                data={
                    "results": {
                        name: {"status": status}
                        for name, status in statuses.items()
                    },
                    "architecture": architecture,
                },
                workspace=reference.workspace,
                work_request=reference_tasks[architecture],
            )[0]
            for architecture, statuses in (
                ("amd64", {"upstream": "PASS", "old": "PASS"}),
                ("arm64", {"upstream": "PASS"}),
                ("i386", {"upstream": "FAIL"}),
                ("riscv64", {"upstream": "FAIL", "performance": "FAIL"}),
                ("s390x", {"upstream": "FAIL"}),
            )
        }
        new_autopkgtest_workflow = new.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="autopkgtest"
        )
        new_tasks = {
            wr.task_data["host_architecture"]: wr
            for wr in new_autopkgtest_workflow.children.filter(
                task_type=TaskTypes.WORKER, task_name="autopkgtest"
            )
        }
        new_results = {
            architecture: self.playground.create_artifact(
                category=ArtifactCategory.AUTOPKGTEST,
                data={
                    "results": {
                        name: {"status": status}
                        for name, status in statuses.items()
                    },
                    "architecture": architecture,
                },
                workspace=new.workspace,
                work_request=new_tasks[architecture],
            )[0]
            for architecture, statuses in (
                ("amd64", {"upstream": "PASS"}),
                ("arm64", {"upstream": "FAIL"}),
                ("i386", {"upstream": "PASS"}),
                ("ppc64el", {"new": "PASS"}),
                ("riscv64", {"upstream": "FAIL", "performance": "PASS"}),
                ("s390x", {"upstream": "PASS", "new": "FAIL"}),
            )
        }

        self.assertIsNone(new_autopkgtest_workflow.output_data)
        self.assertEqual(schedule(), [])
        self.assertTrue(
            reference_tasks["amd64"].mark_completed(WorkRequest.Results.SUCCESS)
        )
        self.assertEqual(schedule(), [])
        self.assertTrue(
            new_tasks["amd64"].mark_completed(WorkRequest.Results.SUCCESS)
        )
        self.assertRunsWorkflowCallback(new_autopkgtest_workflow)

        new_autopkgtest_workflow.refresh_from_db()
        assert new_autopkgtest_workflow.output_data is not None
        self.assertEqual(
            new_autopkgtest_workflow.output_data.regression_analysis,
            {
                "amd64": RegressionAnalysis(
                    original_url=(
                        f"/debusine/System/artifact"
                        f"/{reference_results['amd64'].id}/"
                    ),
                    new_url=(
                        f"/debusine/System/artifact"
                        f"/{new_results['amd64'].id}/"
                    ),
                    status=RegressionAnalysisStatus.STABLE,
                    details={"upstream": "stable", "old": "stable"},
                )
            },
        )

        for architecture, reference_result, new_result in (
            ("arm64", WorkRequest.Results.SUCCESS, WorkRequest.Results.FAILURE),
            ("i386", WorkRequest.Results.FAILURE, WorkRequest.Results.SUCCESS),
            ("ppc64el", None, WorkRequest.Results.SUCCESS),
            (
                "riscv64",
                WorkRequest.Results.FAILURE,
                WorkRequest.Results.FAILURE,
            ),
            ("s390x", WorkRequest.Results.FAILURE, WorkRequest.Results.FAILURE),
        ):
            if reference_result is not None:
                self.assertTrue(
                    reference_tasks[architecture].mark_completed(
                        reference_result
                    )
                )
            self.assertTrue(new_tasks[architecture].mark_completed(new_result))

        self.assertRunsWorkflowCallback(new_autopkgtest_workflow)

        new_autopkgtest_workflow.refresh_from_db()
        assert new_autopkgtest_workflow.output_data is not None
        self.assertEqual(
            new_autopkgtest_workflow.output_data.regression_analysis,
            {
                architecture: RegressionAnalysis(
                    original_url=(
                        f"/debusine/System/artifact"
                        f"/{reference_results[architecture].id}/"
                        if architecture != "ppc64el"
                        else None
                    ),
                    new_url=(
                        f"/debusine/System/artifact"
                        f"/{new_results[architecture].id}/"
                    ),
                    status=status,
                    details=details,
                )
                for architecture, status, details in (
                    (
                        "amd64",
                        RegressionAnalysisStatus.STABLE,
                        {"upstream": "stable", "old": "stable"},
                    ),
                    (
                        "arm64",
                        RegressionAnalysisStatus.REGRESSION,
                        {"upstream": "regression"},
                    ),
                    (
                        "i386",
                        RegressionAnalysisStatus.IMPROVEMENT,
                        {"upstream": "improvement"},
                    ),
                    (
                        "ppc64el",
                        RegressionAnalysisStatus.NO_RESULT,
                        {"new": "stable"},
                    ),
                    (
                        "riscv64",
                        RegressionAnalysisStatus.IMPROVEMENT,
                        {"upstream": "stable", "performance": "improvement"},
                    ),
                    (
                        "s390x",
                        RegressionAnalysisStatus.REGRESSION,
                        {"upstream": "improvement", "new": "regression"},
                    ),
                )
            },
        )

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
