# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the package_publish workflow."""

from collections.abc import Sequence
from typing import Any, ClassVar

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import (
    Collection,
    TaskDatabase,
    User,
    WorkRequest,
    Workspace,
)
from debusine.server.workflows import PackagePublishWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    PackagePublishWorkflowData,
    SbuildWorkflowData,
)
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


class PackagePublishWorkflowTests(TestCase):
    """Unit tests for :py:class:`PackagePublishWorkflow`."""

    user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()

    def create_suite_collection(
        self, name: str, *, workspace: Workspace | None = None
    ) -> Collection:
        """Create a `debian:suite` collection."""
        return self.playground.create_collection(
            name=name, category=CollectionCategory.SUITE, workspace=workspace
        )

    def create_package_publish_workflow(
        self, task_data: dict[str, Any]
    ) -> PackagePublishWorkflow:
        """Create a package_publish workflow."""
        wr = self.playground.create_workflow(
            task_name="package_publish", task_data=task_data
        )
        return PackagePublishWorkflow(wr)

    def test_create_orchestrator(self) -> None:
        """A PackagePublishWorkflow can be instantiated."""
        source_artifact = 1
        binary_artifacts = ["internal@collections/name:build-amd64"]
        target_suite = f"bookworm@{CollectionCategory.SUITE}"

        workflow = self.create_package_publish_workflow(
            {
                "source_artifact": source_artifact,
                "binary_artifacts": binary_artifacts,
                "target_suite": target_suite,
            }
        )

        self.assertEqual(workflow.data.source_artifact, source_artifact)
        self.assertEqual(
            workflow.data.binary_artifacts,
            LookupMultiple.parse_obj(binary_artifacts),
        )
        self.assertEqual(workflow.data.target_suite, target_suite)

    def orchestrate(
        self,
        task_data: PackagePublishWorkflowData,
        architectures: Sequence[str],
        *,
        workspace: Workspace | None = None,
    ) -> WorkRequest:
        """Create and orchestrate a PackagePublishWorkflow."""

        class ExamplePipeline(
            TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Example pipeline for package publishing."""

            def populate(self) -> None:
                """Populate the pipeline."""
                sbuild = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="sbuild",
                    task_data=SbuildWorkflowData(
                        input=SbuildInput(source_artifact=1),
                        target_distribution="debian:sid",
                        architectures=list(architectures) or ["all"],
                    ),
                )

                for arch in architectures:
                    child = sbuild.create_child(
                        task_name="sbuild",
                        task_data=SbuildData(
                            input=SbuildInput(source_artifact=1),
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

                package_publish = self.work_request.create_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="package_publish",
                    task_data=task_data,
                )
                PackagePublishWorkflow(package_publish).populate()

        template = self.playground.create_workflow_template(
            name="examplepipeline-template",
            task_name="examplepipeline",
            workspace=workspace,
        )
        root = self.playground.create_workflow(task_name="examplepipeline")
        self.assertEqual(root.workspace, template.workspace)

        root.mark_running()
        orchestrate_workflow(root)

        return root

    @preserve_task_registry()
    def test_populate_source_and_binary(self) -> None:
        """Test population with both source and binary artifacts."""
        with context.disable_permission_checks():
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )
        target_suite = self.create_suite_collection("bookworm")
        architectures = ("amd64", "i386", "all")

        root = self.orchestrate(
            task_data=PackagePublishWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{arch}"
                        for arch in architectures
                    ]
                ),
                target_suite=(
                    f"{target_suite.name}@{CollectionCategory.SUITE}"
                ),
            ),
            architectures=architectures,
        )
        package_publish = root.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="package_publish"
        )
        copy_collection_items = package_publish.children.get(
            task_name="copycollectionitems"
        )

        self.assertEqual(
            copy_collection_items.task_data,
            {
                "copies": [
                    {
                        "source_items": [
                            source_artifact.id,
                            *(
                                f"internal@collections/name:build-{arch}"
                                for arch in architectures
                            ),
                        ],
                        "target_collection": target_suite.id,
                        "unembargo": False,
                        "replace": False,
                        "variables": {},
                    }
                ]
            },
        )

    @preserve_task_registry()
    def test_populate_source_only(self) -> None:
        """Test population with only a source artifact."""
        with context.disable_permission_checks():
            source_artifact = self.playground.create_source_artifact(
                name="hello"
            )
        target_suite = self.create_suite_collection("bookworm")

        root = self.orchestrate(
            task_data=PackagePublishWorkflowData(
                source_artifact=source_artifact.id,
                target_suite=(
                    f"{target_suite.name}@{CollectionCategory.SUITE}"
                ),
            ),
            architectures=[],
        )
        package_publish = root.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="package_publish"
        )
        copy_collection_items = package_publish.children.get(
            task_name="copycollectionitems"
        )

        self.assertEqual(
            copy_collection_items.task_data,
            {
                "copies": [
                    {
                        "source_items": [source_artifact.id],
                        "target_collection": target_suite.id,
                        "unembargo": False,
                        "replace": False,
                        "variables": {},
                    }
                ]
            },
        )

    @preserve_task_registry()
    def test_populate_package_build_logs(self) -> None:
        """Test population with binary artifacts and copying build logs."""
        with context.disable_permission_checks():
            source_workspace = self.playground.create_workspace(name="source")
            self.playground.create_group_role(
                source_workspace, Workspace.Roles.OWNER, users=[self.user]
            )
            target_workspace = self.playground.create_workspace(name="target")
            self.playground.create_group_role(
                target_workspace, Workspace.Roles.OWNER, users=[self.user]
            )
        source_workspace.set_inheritance([target_workspace])
        target_suite = self.create_suite_collection(
            "bookworm", workspace=target_workspace
        )
        source_build_logs_collection = (
            self.playground.create_singleton_collection(
                CollectionCategory.PACKAGE_BUILD_LOGS,
                workspace=source_workspace,
            )
        )
        target_build_logs_collection = (
            self.playground.create_singleton_collection(
                CollectionCategory.PACKAGE_BUILD_LOGS,
                workspace=target_workspace,
            )
        )
        architectures = ("amd64", "i386", "all")
        binary_artifacts_lookup = [
            f"internal@collections/name:build-{arch}" for arch in architectures
        ]

        root = self.orchestrate(
            task_data=PackagePublishWorkflowData(
                binary_artifacts=LookupMultiple.parse_obj(
                    binary_artifacts_lookup
                ),
                target_suite=(
                    f"{target_suite.name}@{CollectionCategory.SUITE}"
                ),
                unembargo=True,
            ),
            architectures=architectures,
            workspace=source_workspace,
        )
        package_publish = root.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="package_publish"
        )
        copy_collection_items = package_publish.children.get(
            task_name="copycollectionitems"
        )

        self.assertEqual(
            copy_collection_items.task_data,
            {
                "copies": [
                    {
                        "source_items": binary_artifacts_lookup,
                        "target_collection": target_suite.id,
                        "unembargo": True,
                        "replace": False,
                        "variables": {},
                    },
                    {
                        "source_items": [
                            {
                                "collection": source_build_logs_collection.id,
                                "lookup_filters": [
                                    [
                                        "same_work_request",
                                        binary_artifacts_lookup,
                                    ]
                                ],
                            }
                        ],
                        "target_collection": target_build_logs_collection.id,
                        "unembargo": True,
                        "replace": False,
                    },
                ]
            },
        )

    @preserve_task_registry()
    def test_populate_package_task_history(self) -> None:
        """Test population with binary artifacts and copying build logs."""
        with context.disable_permission_checks():
            source_workspace = self.playground.create_workspace(name="source")
            self.playground.create_group_role(
                source_workspace, Workspace.Roles.OWNER, users=[self.user]
            )
            target_workspace = self.playground.create_workspace(name="target")
            self.playground.create_group_role(
                target_workspace, Workspace.Roles.OWNER, users=[self.user]
            )
        source_workspace.set_inheritance([target_workspace])
        target_suite = self.create_suite_collection(
            "bookworm", workspace=target_workspace
        )
        source_task_history_collection = (
            self.playground.create_singleton_collection(
                CollectionCategory.TASK_HISTORY,
                workspace=source_workspace,
            )
        )
        target_task_history_collection = (
            self.playground.create_singleton_collection(
                CollectionCategory.TASK_HISTORY,
                workspace=target_workspace,
            )
        )
        architectures = ("amd64", "i386", "all")
        binary_artifacts_lookup = [
            f"internal@collections/name:build-{arch}" for arch in architectures
        ]

        root = self.orchestrate(
            task_data=PackagePublishWorkflowData(
                binary_artifacts=LookupMultiple.parse_obj(
                    binary_artifacts_lookup
                ),
                target_suite=(
                    f"{target_suite.name}@{CollectionCategory.SUITE}"
                ),
                unembargo=True,
            ),
            architectures=architectures,
            workspace=source_workspace,
        )
        package_publish = root.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="package_publish"
        )
        copy_collection_items = package_publish.children.get(
            task_name="copycollectionitems"
        )

        self.assertEqual(
            copy_collection_items.task_data,
            {
                "copies": [
                    {
                        "source_items": binary_artifacts_lookup,
                        "target_collection": target_suite.id,
                        "unembargo": True,
                        "replace": False,
                        "variables": {},
                    },
                    {
                        "source_items": [
                            {
                                "collection": source_task_history_collection.id,
                                "lookup_filters": [
                                    ["same_workflow", binary_artifacts_lookup]
                                ],
                            }
                        ],
                        "target_collection": target_task_history_collection.id,
                        "unembargo": True,
                        "replace": False,
                    },
                ]
            },
        )

    def test_compute_dynamic_data_binary_package(self) -> None:
        upload_artifact = (
            self.playground.create_upload_artifacts(src_name="hello")
        ).upload

        wr = self.playground.create_workflow(
            task_name="package_publish",
            task_data=PackagePublishWorkflowData(
                binary_artifacts=LookupMultiple.parse_obj([upload_artifact.id]),
                target_suite=f"bookworm@{CollectionCategory.SUITE}",
            ),
        )
        workflow = PackagePublishWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello"),
        )

    def test_get_label(self) -> None:
        """Test get_label()."""
        w = self.create_package_publish_workflow(
            {
                "source_artifact": 2,
                "target_suite": f"bookworm@{CollectionCategory.SUITE}",
            }
        )
        self.assertEqual(
            w.get_label(), f"publish to bookworm@{CollectionCategory.SUITE}"
        )
