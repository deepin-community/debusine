# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the debdiff workflow."""
from collections.abc import Iterable
from typing import Any, ClassVar
from unittest.mock import call, patch

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Artifact,
    Collection,
    TaskDatabase,
    WorkRequest,
    Workspace,
)
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows import DebDiffWorkflow, workflow_utils
from debusine.server.workflows.base import (
    WorkflowRunError,
    orchestrate_workflow,
)
from debusine.server.workflows.debdiff import Architecture
from debusine.server.workflows.models import (
    BaseWorkflowData,
    DebDiffWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.tasks.models import (
    BaseDynamicTaskData,
    DebDiffFlags,
    LookupMultiple,
    LookupSingle,
    SbuildData,
    SbuildInput,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class DebDiffWorkflowTests(TestCase):
    """Unit tests for :py:class:`DebDiffWorkflow`."""

    origin_source_artifact: ClassVar[Artifact]
    origin_binary_artifacts: ClassVar[dict[Architecture, Artifact]]

    new_source_artifact: ClassVar[Artifact]
    new_binary_artifacts: ClassVar[dict[Architecture, Artifact]]

    debian_collection: ClassVar[Collection]

    workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()

        cls.workspace = cls.playground.get_default_workspace()

        cls.debian_collection = cls.playground.create_collection(
            name="bookworm",
            category=CollectionCategory.SUITE,
        )

        cls.origin_source_artifact = cls.playground.create_source_artifact(
            name="hello", version="1.0.0"
        )
        cls.new_source_artifact = cls.playground.create_source_artifact(
            name="hello", version="2.0.0", architectures={"all", "amd64"}
        )

        cls.origin_binary_artifacts = {}
        cls.new_binary_artifacts = {}

        for arch in ["amd64", "arm64", "all"]:
            cls.origin_binary_artifacts[Architecture(arch)] = (
                cls.playground.create_minimal_binary_package_artifact(
                    package="hello",
                    srcpkg_name="hello",
                    srcpkg_version="1.0.0",
                    version="1.0.0",
                    architecture=arch,
                )
            )
            cls.new_binary_artifacts[Architecture(arch)] = (
                cls.playground.create_minimal_binary_package_artifact(
                    package="hello",
                    srcpkg_name="hello",
                    srcpkg_version="2.0.0",
                    version="2.0.0",
                    architecture=arch,
                )
            )

    def orchestrate(
        self,
        *,
        task_data: DebDiffWorkflowData,
        sbuild_architectures: list[str],
        source_artifact: Artifact,
        target_distribution: str,
    ) -> WorkRequest:
        """Create and orchestrate a DebDiffWorkflow."""

        class ExamplePipeline(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Pipeline workflow."""

            def populate(self) -> None:
                """Populate the pipeline."""
                for arch in sbuild_architectures:
                    sbuild = self.work_request_ensure_child(
                        task_type=TaskTypes.WORKER,
                        task_name="sbuild",
                        task_data=SbuildData(
                            input=SbuildInput(
                                source_artifact=source_artifact.id
                            ),
                            environment=target_distribution,
                            host_architecture="amd64",
                        ),
                        workflow_data=WorkRequestWorkflowData(
                            display_name=f"Build {arch}", step=f"build-{arch}"
                        ),
                    )
                    self.provides_artifact(
                        sbuild,
                        ArtifactCategory.BINARY_PACKAGE,
                        name=f"build-{arch}",
                        data={
                            "binary_package_name": source_artifact.data["name"],
                            "architecture": arch,
                        },
                    )

                debdiff = self.work_request_ensure_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="debdiff",
                    task_data=task_data,
                    workflow_data=WorkRequestWorkflowData(
                        display_name="debdiff",
                        step="debdiff",
                    ),
                )
                debdiff.mark_running()
                orchestrate_workflow(debdiff)

        root = self.playground.create_workflow(task_name="examplepipeline")

        root.mark_running()
        orchestrate_workflow(root)

        return root

    def create_debdiff_workflow(
        self,
        *,
        add_origin_source_artifact: bool = True,
        add_origin_binary_artifacts_archs: Iterable[str] | None = None,
        add_new_binary_artifacts_archs: Iterable[str] | None = None,
        override_binary_artifacts: list[LookupSingle] | None = None,
        arch_all_host_architecture: str | None = None,
        original: str | None = None,
        extra_flags: list[DebDiffFlags] | None = None,
    ) -> WorkRequest:
        """
        Create and schedule a DebDiff workflow.

        :param add_origin_source_artifact: whether to add
          self.add_origin_source_artifact in the "original" collection
        :param add_origin_binary_artifacts_archs: add each architecture from
          self.origin_binary_artifacts into the "original" collection.
          If None: adds all of them
        :param add_new_binary_artifacts_archs: add each architecture from
          self.new_binary_artifacts in the DebDiff's "task_data"
          If None: adds all of them
        :param override_binary_artifacts: if not None: for binary_artifacts
          use this LookupSingle (ignoring add_new_binary_artifacts_archs)
        :param arch_all_host_architecture: if None: does nothing. Else add
           "arch_all_host_architecture" in the "task_data"
        :param original: collection to use as "original". If it's None, use
          self.debian_collection
        :param extra_flags: if not None add "extra_flags" in the task data
        """
        if add_origin_source_artifact:
            self.debian_collection.manager.add_artifact(
                self.origin_source_artifact,
                user=self.playground.get_default_user(),
                variables={"component": "main", "section": "devel"},
            )

        if add_origin_binary_artifacts_archs is None:
            add_origin_binary_artifacts_archs = (
                self.origin_binary_artifacts.keys()
            )

        if add_new_binary_artifacts_archs is None:
            add_new_binary_artifacts_archs = self.new_binary_artifacts.keys()

        for arch in add_origin_binary_artifacts_archs:
            self.debian_collection.manager.add_artifact(
                self.origin_binary_artifacts[Architecture(arch)],
                user=self.playground.get_default_user(),
                variables={
                    "component": "main",
                    "section": "devel",
                    "priority": "optional",
                },
            )

        binary_artifacts: list[LookupSingle] = []
        if override_binary_artifacts is None:
            # Use the default binary artifacts for all the architectures
            for arch in add_new_binary_artifacts_archs:
                binary_artifacts.append(
                    self.new_binary_artifacts[Architecture(arch)].id
                )

        else:
            # Use the override_binary_artifacts.
            # Calculate the relevant archs based on the passed artifacts
            binary_artifacts = override_binary_artifacts

        sbuild_archs = self.archs_for_binaries(binary_artifacts)

        task_data: dict[str, Any] = {
            "source_artifact": self.new_source_artifact.id,
            "binary_artifacts": binary_artifacts,
            "original": original if original else str(self.debian_collection),
            "vendor": "debian",
            "codename": "bookworm",
        }

        if arch_all_host_architecture is not None:
            task_data["arch_all_host_architecture"] = arch_all_host_architecture

        if extra_flags is not None:
            task_data["extra_flags"] = extra_flags

        example_pipeline = self.orchestrate(
            task_data=DebDiffWorkflowData(**task_data),
            sbuild_architectures=list(sbuild_archs),
            source_artifact=self.new_source_artifact,
            target_distribution="debian:bookworm",
        )
        work_request = example_pipeline.children.get(
            task_name="debdiff", task_type=TaskTypes.WORKFLOW
        )
        return work_request

    def archs_for_binaries(self, binaries: list[LookupSingle]) -> set[str]:
        archs = set()
        for lookup in binaries:
            archs.add(
                workflow_utils.lookup_result_architecture(
                    lookup_single(
                        lookup,
                        self.workspace,
                        user=self.playground.get_default_user(),
                        expect_type=LookupChildType.ARTIFACT,
                    )
                )
            )

        return archs

    @classmethod
    def new_binary_artifact_lookup(cls, arch: str) -> str:
        return f"{cls.new_binary_artifacts[Architecture(arch)].id}@artifacts"

    @preserve_task_registry()
    def test_populate(self) -> None:
        # Add a binary package with older version: it will be discarded
        old_package_1 = self.playground.create_minimal_binary_package_artifact(
            package="libhello0",
            srcpkg_name="hello",
            srcpkg_version="0.1.0",
            version="0.1.0",
            architecture="amd64",
        )
        self.debian_collection.manager.add_artifact(
            old_package_1,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )
        old_package_2 = self.playground.create_minimal_binary_package_artifact(
            package="libhello0",
            srcpkg_name="hello",
            srcpkg_version="0.2.0",
            version="0.2.0",
            architecture="amd64",
        )
        self.debian_collection.manager.add_artifact(
            old_package_2,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        # Add another binary package with same version but new architecture
        new_architecture = (
            self.playground.create_minimal_binary_package_artifact(
                package="hello",
                srcpkg_name="hello",
                srcpkg_version="1.0.0",
                version="1.0.0",
                architecture="riscv64",
            )
        )
        self.debian_collection.manager.add_artifact(
            new_architecture,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        # Add a binary package that is ignored because its srcpkg_version
        # is too new (when known, it's not using the most recent binary package
        # but one with a matching srcpkg_version)
        too_new = self.playground.create_minimal_binary_package_artifact(
            package="hello",
            srcpkg_name="hello",
            srcpkg_version="3.0.0",
            version="3.0.0",
            architecture="amd64",
        )
        self.debian_collection.manager.add_artifact(
            too_new,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        # Create workflow
        with patch.object(
            DebDiffWorkflow,
            "requires_artifact",
            wraps=DebDiffWorkflow.requires_artifact,
        ) as requires_artifact:
            work_request = self.create_debdiff_workflow(
                arch_all_host_architecture="arm64",
                extra_flags=[DebDiffFlags.IGNORE_SPACE],
            )

        debdiff_work_requests = work_request.children.all()
        bookworm = Collection.objects.get(name="bookworm")
        bookworm_lookup = f"{bookworm.name}@{bookworm.category}"

        # Check work request to compare the source packages
        source_work_request = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-source"
        )
        self.assertEqual(
            source_work_request.workflow_data.display_name,
            "DebDiff for source package",
        )

        self.assertEqual(
            source_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "arm64",
                "extra_flags": [DebDiffFlags.IGNORE_SPACE],
                "input": {
                    "source_artifacts": [
                        f"{bookworm_lookup}/name:hello_1.0.0",
                        self.new_source_artifact.id,
                    ]
                },
            },
        )

        self.assertIn(
            call(
                source_work_request,
                f"{bookworm_lookup}/name:hello_1.0.0",
            ),
            requires_artifact.call_args_list,
        )
        self.assertIn(
            call(source_work_request, self.new_source_artifact.id),
            requires_artifact.call_args_list,
        )

        # Check work requests to compare the binary packages
        self.assertEqual(
            debdiff_work_requests.filter(
                workflow_data_json__step__startswith="debdiff-binaries"
            ).count(),
            3,
        )

        amd64_work_request = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-binaries-amd64"
        )

        self.assertEqual(
            amd64_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "amd64",
                "extra_flags": [DebDiffFlags.IGNORE_SPACE],
                "input": {
                    "binary_artifacts": [
                        [
                            f"{bookworm_lookup}/name:hello_1.0.0_all",
                            f"{bookworm_lookup}/name:hello_1.0.0_amd64",
                        ],
                        [
                            self.new_binary_artifact_lookup("amd64"),
                        ],
                    ]
                },
            },
        )

        self.assertIn(
            call(
                amd64_work_request,
                LookupMultiple.parse_obj(
                    [
                        f"{bookworm_lookup}/name:hello_1.0.0_all",
                        f"{bookworm_lookup}/name:hello_1.0.0_amd64",
                    ]
                ),
            ),
            requires_artifact.call_args_list,
        )
        self.assertIn(
            call(
                amd64_work_request,
                LookupMultiple.parse_obj(
                    [
                        self.new_binary_artifact_lookup("amd64"),
                    ]
                ),
            ),
            requires_artifact.call_args_list,
        )

    @preserve_task_registry()
    def test_populate_binary_artifacts_upload(self) -> None:
        upload_artifacts = self.playground.create_upload_artifacts(
            binaries=["hello", "bye"],
            bin_architecture="amd64",
        )

        work_request = self.create_debdiff_workflow(
            override_binary_artifacts=[upload_artifacts.upload.id]
        )

        debdiff_work_requests = work_request.children.all()

        work_request_source = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-source"
        )
        self.assertEqual(
            work_request_source.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "extra_flags": [],
                "host_architecture": "amd64",
                "input": {
                    "source_artifacts": [
                        "bookworm@debian:suite/name:hello_1.0.0",
                        self.new_source_artifact.id,
                    ]
                },
            },
        )

        work_request_binary = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-binaries-amd64"
        )

        self.assertEqual(
            work_request_binary.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "extra_flags": [],
                "host_architecture": "amd64",
                "input": {
                    "binary_artifacts": [
                        [
                            "bookworm@debian:suite/name:hello_1.0.0_all",
                            "bookworm@debian:suite/name:hello_1.0.0_amd64",
                        ],
                        sorted(
                            [
                                artifact.id
                                for artifact in upload_artifacts.binaries
                            ]
                        ),
                    ]
                },
            },
        )

    @preserve_task_registry()
    def test_get_new_packages_by_name_arch_invalid_category(self) -> None:
        # Valid artifact categories are tested as part of other tests, such as
        # test_populate_binary_artifacts_upload(). The invalid category
        # path should never be reached in production
        source_artifact = self.playground.create_source_artifact()
        work_request = self.create_debdiff_workflow()
        workflow = DebDiffWorkflow(work_request)

        # Make workflow.data.binary_artifacts contain an artifact of an
        # unexpected category
        workflow.data.binary_artifacts = LookupMultiple.parse_obj(
            [source_artifact.id]
        )
        with self.assertRaisesRegex(
            ValueError, "Unexpected category 'debian:source-package'"
        ):
            workflow._get_new_packages_by_name_arch()

    @preserve_task_registry()
    def test_populate_new_binary_artifacts_only_all_arch(self) -> None:
        work_request = self.create_debdiff_workflow(
            add_new_binary_artifacts_archs=["all"],
            arch_all_host_architecture="arm64",
        )

        debdiff_work_requests = work_request.children.all()

        # Check work requests to compare the binary packages
        self.assertEqual(
            debdiff_work_requests.filter(
                workflow_data_json__step__startswith="debdiff-binaries"
            ).count(),
            1,
        )

        all_work_request = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-binaries-all"
        )

        bookworm = Collection.objects.get(name="bookworm")
        bookworm_lookup = f"{bookworm.name}@{bookworm.category}"

        self.assertEqual(
            all_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "arm64",
                "extra_flags": [],
                "input": {
                    "binary_artifacts": [
                        [
                            f"{bookworm_lookup}/name:hello_1.0.0_all",
                            f"{bookworm_lookup}/name:hello_1.0.0_amd64",
                            f"{bookworm_lookup}/name:hello_1.0.0_arm64",
                        ],
                        [
                            self.new_binary_artifact_lookup("all"),
                        ],
                    ]
                },
            },
        )

    @preserve_task_registry()
    def test_populate_origin_binary_artifacts_only_all_arch(self) -> None:
        # Add coverage for an "all" architecture binary package in the
        # debian_collection but not in the new artifacts
        self.debian_collection.manager.add_artifact(
            self.playground.create_minimal_binary_package_artifact(
                package="bye", version="1.0.0", architecture="all"
            ),
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        # Add coverage for a not "all" architecture binary package in the
        # debian_collection but not in the new artifacts
        self.debian_collection.manager.add_artifact(
            self.playground.create_minimal_binary_package_artifact(
                package="greeting", version="1.0.0", architecture="amd64"
            ),
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        work_request = self.create_debdiff_workflow(
            add_origin_binary_artifacts_archs=["all"]
        )

        debdiff_work_requests = work_request.children.all()

        # Check work requests to compare the binary packages
        self.assertEqual(
            debdiff_work_requests.filter(
                workflow_data_json__step__startswith="debdiff-binaries"
            ).count(),
            3,
        )

        all_work_request = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-binaries-all"
        )

        bookworm = Collection.objects.get(name="bookworm")
        bookworm_lookup = f"{bookworm.name}@{bookworm.category}"

        self.assertEqual(
            all_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "amd64",
                "extra_flags": [],
                "input": {
                    "binary_artifacts": [
                        [
                            f"{bookworm_lookup}/name:hello_1.0.0_all",
                        ],
                        [
                            self.new_binary_artifact_lookup("all"),
                        ],
                    ]
                },
            },
        )

    @preserve_task_registry()
    def test_populate_origin_binary_artifacts_no_all_arch(self) -> None:
        work_request = self.create_debdiff_workflow(
            add_origin_binary_artifacts_archs=["amd64", "arm64"]
        )

        debdiff_work_requests = work_request.children.all()

        # Check work requests to compare the binary packages
        self.assertEqual(
            debdiff_work_requests.filter(
                workflow_data_json__step__startswith="debdiff-binaries"
            ).count(),
            3,
        )

        amd64_work_request = debdiff_work_requests.get(
            workflow_data_json__step="debdiff-binaries-amd64"
        )

        bookworm = Collection.objects.get(name="bookworm")
        bookworm_lookup = f"{bookworm.name}@{bookworm.category}"

        self.assertEqual(
            amd64_work_request.task_data,
            {
                "environment": "debian/match:codename=bookworm",
                "host_architecture": "amd64",
                "extra_flags": [],
                "input": {
                    "binary_artifacts": [
                        [
                            f"{bookworm_lookup}/name:hello_1.0.0_amd64",
                        ],
                        [
                            self.new_binary_artifact_lookup("amd64"),
                        ],
                    ]
                },
            },
        )

    @preserve_task_registry()
    def test_populate_new_package(self) -> None:
        """Run populate with a package not found in original collection."""
        work_request = self.create_debdiff_workflow(
            add_origin_source_artifact=False,
            add_origin_binary_artifacts_archs=[],
        )

        # Source package neither binary packages could be found in the
        # original collection. It's a new package, and there isn't any
        # DebDiff task created.
        self.assertEqual(work_request.children.count(), 0)

    @preserve_task_registry()
    def test_populate_package_without_source_in_original(self) -> None:
        # Add a binary package that has the same name but is older
        # (to exercise returning only the newest)
        older = self.playground.create_minimal_binary_package_artifact(
            package="hello",
            srcpkg_name="hello",
            srcpkg_version="0.1.0",
            version="0.1.0",
            architecture="amd64",
        )
        self.debian_collection.manager.add_artifact(
            older,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )
        even_older = self.playground.create_minimal_binary_package_artifact(
            package="hello",
            srcpkg_name="hello",
            srcpkg_version="0.0.1",
            version="0.0.1",
            architecture="amd64",
        )
        self.debian_collection.manager.add_artifact(
            even_older,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        debdiff_workflow = self.create_debdiff_workflow(
            add_origin_source_artifact=False,
        )

        debdiff_work_requests = debdiff_workflow.children.all()

        # Check work requests to compare the binary packages
        self.assertEqual(
            debdiff_work_requests.filter(
                workflow_data_json__step__startswith="debdiff-binaries"
            ).count(),
            3,
        )

        # The version 0.1.0 is not used, only 1.0.0 versions
        for debdiff_work_request in debdiff_work_requests:
            for binary_artifact in debdiff_work_request.task_data["input"][
                "binary_artifacts"
            ][0]:
                self.assertIn("1.0.0", binary_artifact)

    @preserve_task_registry()
    def test_compute_dynamic_data_from_source(self) -> None:
        work_request = self.create_debdiff_workflow()
        workflow = DebDiffWorkflow(work_request)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(work_request)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="bookworm: 1.0.0 → 2.0.0"
            ),
        )

    @preserve_task_registry()
    def test_compute_dynamic_data_from_binary(self) -> None:
        # Add a binary package with older version: it will be discarded
        old_package = self.playground.create_minimal_binary_package_artifact(
            package="libhello0",
            srcpkg_name="hello",
            srcpkg_version="0.1.0",
            version="0.1.0",
            architecture="amd64",
        )
        self.debian_collection.manager.add_artifact(
            old_package,
            user=self.playground.get_default_user(),
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        work_request = self.create_debdiff_workflow(
            add_origin_source_artifact=False
        )

        workflow = DebDiffWorkflow(work_request)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(work_request)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="bookworm: 1.0.0 → 2.0.0"
            ),
        )

    @preserve_task_registry()
    def test_compute_dynamic_data_package_not_in_origin_collection(
        self,
    ) -> None:
        work_request = self.create_debdiff_workflow(
            add_origin_source_artifact=False,
            add_origin_binary_artifacts_archs=[],
        )

        workflow = DebDiffWorkflow(work_request)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(work_request)),
            BaseDynamicTaskData(
                subject="hello",
                parameter_summary="bookworm: missing → 2.0.0",
            ),
        )

    def test_package_arch_pairs_artifact_debian_binary_package(self) -> None:
        temp_dir = self.create_temporary_directory()

        artifact = self.playground.create_artifact_from_local(
            self.playground.create_binary_package(
                temp_dir, name="hello", architecture="arm64"
            )
        )

        lookup_result = lookup_single(
            f"{artifact.id}@artifacts",
            workspace=self.workspace,
            user=self.playground.get_default_user(),
        )

        self.assertEqual(
            DebDiffWorkflow._package_arch_pairs(lookup_result),
            [("hello", "arm64")],
        )

    def test_package_arch_pairs_artifact_raise_value_error(self) -> None:
        self.playground.create_bare_data_item(
            self.debian_collection,
            "build-amd64",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": 2,
                "promise_workflow_id": 1,
                "promise_category": ArtifactCategory.TEST,
            },
        )

        lookup_result = lookup_single(
            f"{self.debian_collection}/name:build-amd64",
            self.workspace,
            user=self.playground.get_default_user(),
        )

        with self.assertRaisesRegex(
            ValueError,
            r"^DebDiffWorkflow requires binary artifacts \(not promises\)$",
        ):
            DebDiffWorkflow._package_arch_pairs(lookup_result)

    def test_package_arch_pairs_artifact_unsupported_category(self) -> None:
        artifact = self.playground.create_source_artifact()

        lookup_result = lookup_single(
            f"{artifact.id}@artifacts",
            workspace=self.workspace,
            user=self.playground.get_default_user(),
        )

        with self.assertRaisesRegex(
            ValueError, rf"Unexpected category {artifact.category}"
        ):
            DebDiffWorkflow._package_arch_pairs(lookup_result)

    def test_package_arch_pairs_artifact_upload(self) -> None:
        # Introspects the relations
        upload_artifacts = self.playground.create_upload_artifacts(
            binaries=["bye", "hello"],
            bin_architecture="all",
        )

        lookup_result = lookup_single(
            f"{upload_artifacts.upload.id}@artifacts",
            workspace=self.workspace,
            user=self.playground.get_default_user(),
        )
        artifact_amd64 = upload_artifacts.binaries[0]
        artifact_amd64.data["deb_fields"]["Architecture"] = "amd64"
        artifact_amd64.save()

        artifact_1 = upload_artifacts.binaries[1]

        self.assertEqual(
            DebDiffWorkflow._package_arch_pairs(lookup_result),
            sorted(
                [
                    (artifact_amd64.data["deb_fields"]["Package"], "amd64"),
                    (artifact_1.data["deb_fields"]["Package"], "all"),
                ]
            ),
        )

    @preserve_task_registry()
    def test_validate_input(self) -> None:
        # validate_input() is called and does not raise
        self.create_debdiff_workflow()

    @preserve_task_registry()
    def test_validate_input_invalid_original(self) -> None:
        with self.assertRaisesRegex(WorkflowRunError, "non-existing"):
            self.create_debdiff_workflow(original="non-existing")

    @preserve_task_registry()
    def test_get_label(self) -> None:
        w = self.create_debdiff_workflow()
        self.assertEqual(w.get_label(), "debdiff")
