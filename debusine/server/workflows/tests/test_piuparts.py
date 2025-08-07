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

from debusine.artifacts.models import ArtifactCategory
from debusine.db.context import context
from debusine.db.models import TaskDatabase, WorkRequest
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    PiupartsWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.piuparts import PiupartsWorkflow
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.tasks.models import (
    BaseDynamicTaskData,
    LookupMultiple,
    SbuildData,
    SbuildInput,
    TaskTypes,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class PiupartsWorkflowTests(TestCase):
    """Unit tests for :py:class:`PiupartsWorkflow`."""

    def create_piuparts_workflow(
        self,
        *,
        extra_task_data: dict[str, Any],
    ) -> PiupartsWorkflow:
        """Create a piuparts workflow."""
        task_data = {
            "binary_artifacts": [39, 46, 53],
            "vendor": "debian",
            "codename": "bookworm",
        }
        task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="piuparts", task_data=task_data
        )
        return PiupartsWorkflow(wr)

    @preserve_task_registry()
    def test_populate(self) -> None:
        """Test populate."""
        with context.disable_permission_checks():
            # Expected architectures as per intersection of architectures
            # of binary_artifacts and requested architectures
            binaries_all = (
                self.playground.create_minimal_binary_packages_artifact(
                    "hello", "1.0.0", "1.0.0", "all"
                )
            )
            binaries_amd64 = (
                self.playground.create_minimal_binary_packages_artifact(
                    "hello", "1.0.0", "1.0.0", "amd64"
                )
            )
            binaries_i386 = (
                self.playground.create_minimal_binary_packages_artifact(
                    "hello", "1.0.0", "1.0.0", "i386"
                )
            )

            o = self.playground.create_workflow(
                task_name="piuparts",
                task_data={
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
                    "binary_artifacts": [
                        f"{binaries_all.id}@artifacts",
                        f"{binaries_amd64.id}@artifacts",
                    ]
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
                    "binary_artifacts": [
                        f"{binaries_all.id}@artifacts",
                        f"{binaries_i386.id}@artifacts",
                    ]
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
        self.playground.create_debian_environment(codename="experimental")
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

    def test_compute_dynamic_data_binary_package(self) -> None:
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello"
            )
        )
        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
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
        binary_artifact = (
            self.playground.create_minimal_binary_packages_artifact(
                srcpkg_name="hello"
            )
        )
        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
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
        binary_artifact = (
            self.playground.create_debian_environments_collection()
        )
        wr = self.playground.create_workflow(
            task_name="piuparts",
            task_data=PiupartsWorkflowData(
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
                TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
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

    @context.disable_permission_checks()
    def test_get_label(self) -> None:
        """Test get_label()."""
        w = self.create_piuparts_workflow(extra_task_data={})
        self.assertEqual(w.get_label(), "run piuparts")
