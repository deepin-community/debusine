# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the piuparts workflow."""

from typing import Any

from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Collection,
    CollectionItem,
    TaskDatabase,
    WorkRequest,
)
from debusine.db.models.work_requests import SkipWorkRequest
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows import WorkflowValidationError
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    PiupartsWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.piuparts import PiupartsWorkflow
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks.models import (
    BaseDynamicTaskData,
    LookupMultiple,
    OutputData,
    SbuildData,
    SbuildInput,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class PiupartsWorkflowTests(TestCase):
    """Unit tests for :py:class:`PiupartsWorkflow`."""

    def create_piuparts_workflow(
        self,
        *,
        extra_task_data: dict[str, Any] | None = None,
    ) -> PiupartsWorkflow:
        """Create a piuparts workflow."""
        task_data = {
            "source_artifact": 1,
            "binary_artifacts": [39, 46, 53],
            "vendor": "debian",
            "codename": "bookworm",
        }
        if extra_task_data is not None:
            task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="piuparts", task_data=task_data, validate=False
        )
        return PiupartsWorkflow(wr)

    def add_qa_result(
        self,
        qa_results: Collection,
        package: str,
        version: str,
        architecture: str,
    ) -> CollectionItem:
        """Add a piuparts result to a ``debian:qa-results`` collection."""
        return qa_results.manager.add_bare_data(
            BareDataCategory.QA_RESULT,
            user=self.playground.get_default_user(),
            data={
                "package": package,
                "version": version,
                "architecture": architecture,
                "timestamp": int(timezone.now().timestamp()),
                "work_request_id": self.playground.create_work_request(
                    task_name="piuparts", result=WorkRequest.Results.SUCCESS
                ).id,
            },
        )

    def test_validate_input(self) -> None:
        """validate_input passes a valid case."""
        w = self.create_piuparts_workflow()

        w.validate_input()

    def test_validate_input_bad_qa_suite(self) -> None:
        """validate_input raises errors in looking up a suite."""
        w = self.create_piuparts_workflow(
            extra_task_data={"qa_suite": "nonexistent@debian:suite"}
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:suite' does not exist or is hidden",
        ):
            w.validate_input()

    def test_validate_input_bad_reference_qa_results(self) -> None:
        """validate_input raises errors in looking up reference QA results."""
        w = self.create_piuparts_workflow(
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
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.add_qa_result(sid_qa_results, "other", "1.0-1", "amd64")
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello", srcpkg_version="1.0-1"
            )
        )

        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertFalse(
            PiupartsWorkflow(wr)._has_current_reference_qa_result("amd64")
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
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello", srcpkg_version="1.0-2"
            )
        )

        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertFalse(
            PiupartsWorkflow(wr)._has_current_reference_qa_result("amd64")
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
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello", srcpkg_version="1.0-1"
            )
        )

        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        self.assertTrue(
            PiupartsWorkflow(wr)._has_current_reference_qa_result("amd64")
        )

    @preserve_task_registry()
    def test_populate(self) -> None:
        """Test populate."""
        # Expected architectures as per intersection of architectures of
        # binary_artifacts and requested architectures
        source = self.playground.create_source_artifact(
            name="hello", version="1.0.0"
        )
        binaries_all = self.playground.create_minimal_binary_packages_artifact(
            "hello", "1.0.0", "1.0.0", "all"
        )
        binaries_amd64 = (
            self.playground.create_minimal_binary_packages_artifact(
                "hello", "1.0.0", "1.0.0", "amd64"
            )
        )
        binaries_i386 = self.playground.create_minimal_binary_packages_artifact(
            "hello", "1.0.0", "1.0.0", "i386"
        )

        o = self.playground.create_workflow(
            task_name="piuparts",
            task_data={
                "source_artifact": source.id,
                "binary_artifacts": [
                    binaries_all.id,
                    binaries_amd64.id,
                    binaries_i386.id,
                ],
                "vendor": "debian",
                "codename": "bookworm",
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            },
        )
        PiupartsWorkflow(o).populate()

        piuparts_wrs = WorkRequest.objects.filter(
            task_type=TaskTypes.WORKER,
            task_name="piuparts",
            parent=o,
        )
        self.assertEqual(piuparts_wrs.count(), 2)

        piuparts_wr = piuparts_wrs.get(
            task_data__input__binary_artifacts__contains=(
                f"{binaries_amd64.id}@artifacts",
            )
        )
        self.assertEqual(
            piuparts_wr.task_data,
            {
                "backend": "unshare",
                "input": {
                    "binary_artifacts": sorted(
                        [
                            f"{binaries_all.id}@artifacts",
                            f"{binaries_amd64.id}@artifacts",
                        ]
                    )
                },
                "host_architecture": "amd64",
                "base_tgz": "debian/match:codename=bookworm",
                "environment": "debian/match:codename=bookworm",
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            },
        )

        self.assertEqual(piuparts_wr.event_reactions_json, {})

        piuparts_wr = piuparts_wrs.get(
            task_data__input__binary_artifacts__contains=(
                f"{binaries_i386.id}@artifacts",
            )
        )
        self.assertEqual(
            piuparts_wr.task_data,
            {
                "backend": "unshare",
                "input": {
                    "binary_artifacts": sorted(
                        [
                            f"{binaries_all.id}@artifacts",
                            f"{binaries_i386.id}@artifacts",
                        ]
                    )
                },
                "host_architecture": "i386",
                "base_tgz": "debian/match:codename=bookworm",
                "environment": "debian/match:codename=bookworm",
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            },
        )

        self.assertEqual(piuparts_wr.event_reactions_json, {})

        # If only Architecture: all binary packages are provided
        # in binary_artifacts, then piuparts will be run once for
        # arch-all on {arch_all_host_architecture}.
        o = self.playground.create_workflow(
            task_name="piuparts",
            task_data={
                "source_artifact": source.id,
                "binary_artifacts": [
                    binaries_all.id,
                    binaries_amd64.id,
                    binaries_i386.id,
                ],
                "vendor": "debian",
                "codename": "bookworm",
                "architectures": ["all"],
                # Override the environment
                "environment": "debian/match:codename=trixie",
            },
        )
        PiupartsWorkflow(o).populate()

        piuparts_wrs = WorkRequest.objects.filter(
            task_type=TaskTypes.WORKER,
            task_name="piuparts",
            parent=o,
        )

        self.assertEqual(piuparts_wrs.count(), 1)

        piuparts_wr = piuparts_wrs.get(
            task_data__input__binary_artifacts__contains=(
                f"{binaries_all.id}@artifacts",
            )
        )
        self.assertEqual(
            piuparts_wr.task_data,
            {
                "backend": "unshare",
                "input": {
                    "binary_artifacts": [
                        f"{binaries_all.id}@artifacts",
                    ]
                },
                "host_architecture": "amd64",
                "base_tgz": "debian/match:codename=bookworm",
                "environment": "debian/match:codename=trixie",
                "extra_repositories": None,
            },
        )

    def test_populate_experimental(self) -> None:
        """The workflow handles overlay distributions."""
        self.playground.create_debian_environment(
            codename="experimental", variant="piuparts"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact()
        )
        workflow = self.create_piuparts_workflow(
            extra_task_data={
                "binary_artifacts": [binary_artifact.id],
                "vendor": "debian",
                "codename": "experimental",
            }
        )
        workflow.populate()
        children = list(workflow.work_request.children.all())
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
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        PiupartsWorkflow(wr).populate()

        self.assertQuerySetEqual(wr.children.all(), [])

    def test_populate_no_previous_reference_qa_result(self) -> None:
        """The workflow produces a reference QA result if there is none."""
        sid_suite = self.playground.create_collection(
            "sid", CollectionCategory.SUITE
        )
        self.playground.create_collection("sid", CollectionCategory.QA_RESULTS)

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
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        PiupartsWorkflow(wr).populate()

        [child] = wr.children.all()
        self.assertEqual(child.status, WorkRequest.Statuses.BLOCKED)
        self.assertEqual(child.task_type, TaskTypes.WORKER)
        self.assertEqual(child.task_name, "piuparts")
        self.assertEqual(
            child.task_data,
            {
                "backend": "unshare",
                "base_tgz": "debian/match:codename=sid",
                "environment": "debian/match:codename=sid",
                "extra_repositories": None,
                "host_architecture": "amd64",
                "input": {
                    "binary_artifacts": [f"{binary_artifact.id}@artifacts"]
                },
            },
        )
        qa_result_data = {
            "package": "hello",
            "version": "1.0-1",
            "architecture": "amd64",
            "timestamp": int(source_item.created_at.timestamp()),
            "work_request_id": child.id,
        }
        qa_result_action = {
            "action": "update-collection-with-data",
            "category": BareDataCategory.QA_RESULT,
            "collection": f"sid@{CollectionCategory.QA_RESULTS}",
            "created_at": None,
            "data": qa_result_data,
            "name_template": None,
        }
        self.assert_work_request_event_reactions(
            child,
            on_assignment=[
                {
                    "action": "skip-if-lookup-result-changed",
                    "lookup": (
                        f"sid@{CollectionCategory.QA_RESULTS}/"
                        f"latest:piuparts_hello_amd64"
                    ),
                    "collection_item_id": None,
                    "promise_name": None,
                }
            ],
            on_failure=[qa_result_action],
            on_success=[qa_result_action],
        )

        # Completing the work request stores the QA result.
        self.assertTrue(child.mark_pending())
        self.assertTrue(child.mark_completed(WorkRequest.Results.SUCCESS))
        self.assertEqual(
            lookup_single(
                f"sid@{CollectionCategory.QA_RESULTS}/"
                f"latest:piuparts_hello_amd64",
                child.workspace,
                user=child.created_by,
                expect_type=LookupChildType.BARE,
            ).collection_item.data,
            {
                **qa_result_data,
                "task_name": "piuparts",
                "result": WorkRequest.Results.SUCCESS,
            },
        )

    def test_populate_reference_qa_result_backs_off(self) -> None:
        """Reference tasks are skipped if another workflow got there first."""
        self.playground.create_collection("sid", CollectionCategory.SUITE)
        sid_qa_results = self.playground.create_collection(
            "sid", CollectionCategory.QA_RESULTS
        )
        self.playground.create_debian_environment(
            codename="sid", variant="piuparts"
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
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="sid",
                qa_suite=f"sid@{CollectionCategory.SUITE}",
                reference_qa_results=f"sid@{CollectionCategory.QA_RESULTS}",
                update_qa_results=True,
            ),
        )

        PiupartsWorkflow(wr).populate()

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
                    f"latest:piuparts_hello_amd64' changed"
                )
            ),
        )
        self.assertEqual(
            lookup_single(
                f"sid@{CollectionCategory.QA_RESULTS}/"
                f"latest:piuparts_hello_amd64",
                child.workspace,
                user=child.created_by,
                expect_type=LookupChildType.BARE,
            ).collection_item,
            racing_qa_result,
        )

    def test_compute_dynamic_data_binary_package(self) -> None:
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello"
            )
        )
        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = PiupartsWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello"),
        )

    def test_compute_dynamic_data_binary_packages(self) -> None:
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact = (
            self.playground.create_minimal_binary_packages_artifact(
                srcpkg_name="hello"
            )
        )
        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = PiupartsWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello"),
        )

    def test_compute_dynamic_data_multiple_source_packages(self) -> None:
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact_1 = (
            self.playground.create_minimal_binary_packages_artifact(
                srcpkg_name="hello"
            )
        )
        binary_artifact_2 = (
            self.playground.create_minimal_binary_packages_artifact(
                srcpkg_name="linux-base"
            )
        )

        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [binary_artifact_1.id, binary_artifact_2.id]
                ),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = PiupartsWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello linux-base"),
        )

    def test_compute_dynamic_data_collection_raise_error(self) -> None:
        source_artifact = self.playground.create_source_artifact(name="hello")
        binary_artifact = (
            self.playground.create_debian_environments_collection()
        )
        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [f"{binary_artifact.id}@collections"]
                ),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = PiupartsWorkflow(wr)

        with self.assertRaisesRegex(
            LookupError,
            f"^'{binary_artifact.id}@collections' is of type 'collection' "
            f"instead of expected 'artifact_or_promise'",
        ):
            workflow.compute_dynamic_data(TaskDatabase(wr)),

    def test_compute_dynamic_data_binary_promises(self) -> None:
        source_artifact = self.playground.create_source_artifact()

        with preserve_task_registry():

            class ExamplePipeline(
                SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
            ):
                """
                Pipeline workflow that provides a promise (e.g. by Sbuild).

                Piuparts will use the promise.
                """

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
                            architectures=["amd64"],
                        ),
                    )
                    sbuild = sbuild.create_child(
                        task_name="sbuild",
                        task_data=SbuildData(
                            input=SbuildInput(
                                source_artifact=source_artifact.id
                            ),
                            host_architecture="amd64",
                            environment="debian/match:codename=sid",
                        ),
                    )
                    self.provides_artifact(
                        sbuild,
                        ArtifactCategory.BINARY_PACKAGE,
                        "build-arm64",
                        data={"source_package_name": "linux-base"},
                    )
                    self.provides_artifact(
                        sbuild,
                        ArtifactCategory.BINARY_PACKAGE,
                        "build-amd64",
                        data={
                            "architecture": "amd64",
                            "source_package_name": "hello",
                        },
                    )
                    self.provides_artifact(
                        sbuild,
                        ArtifactCategory.BINARY_PACKAGE,
                        "build-i386",
                        data={
                            "architecture": "i386",
                        },
                    )

                    self.work_request.create_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="piuparts",
                        task_data=PiupartsWorkflowData(
                            source_artifact=source_artifact.id,
                            binary_artifacts=LookupMultiple.parse_obj(
                                [
                                    "internal@collections/name:build-amd64",
                                    "internal@collections/name:build-arm64",
                                    "internal@collections/name:build-i386",
                                ]
                            ),
                            vendor="debian",
                            codename="sid",
                        ),
                    )

            root = self.playground.create_workflow(task_name="examplepipeline")
            root.mark_running()
            orchestrate_workflow(root)

            piuparts = WorkRequest.objects.get(
                task_type=TaskTypes.WORKFLOW,
                task_name="piuparts",
            )

            computed_data = PiupartsWorkflow(piuparts).compute_dynamic_data(
                TaskDatabase(piuparts)
            )
            self.assertEqual(
                computed_data, BaseDynamicTaskData(subject="hello linux-base")
            )

    def test_get_label(self) -> None:
        """Test get_label()."""
        w = self.create_piuparts_workflow(extra_task_data={})
        self.assertEqual(w.get_label(), "run piuparts")
