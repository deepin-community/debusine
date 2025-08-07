# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the reverse_dependencies_autopkgtest workflow."""

from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from debusine.artifacts.local_artifact import BinaryPackages, Upload
from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianSourcePackage,
)
from debusine.client.models import LookupChildType
from debusine.db.context import context
from debusine.db.models import Artifact, Collection, TaskDatabase, WorkRequest
from debusine.server.collections.debian_suite import DebianSuiteManager
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows import (
    ReverseDependenciesAutopkgtestWorkflow,
    WorkflowValidationError,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    ReverseDependenciesAutopkgtestWorkflowData,
    SbuildWorkflowData,
)
from debusine.server.workflows.reverse_dependencies_autopkgtest import (
    NoBinaryNames,
    _BinaryPackage,
    _SourcePackage,
)
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    AutopkgtestNeedsInternet,
    BackendType,
    BaseDynamicTaskData,
    ExtraRepository,
    LookupMultiple,
    LookupSingle,
    SbuildData,
    SbuildInput,
    TaskTypes,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class ReverseDependenciesAutopkgtestWorkflowTests(TestCase):
    """Unit tests for :py:class:`ReverseDependenciesAutopkgtestWorkflow`."""

    sid: ClassVar[Collection]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.sid = cls.playground.create_collection(
            name="sid", category=CollectionCategory.SUITE
        )

    def create_rdep_autopkgtest_workflow(
        self,
        extra_task_data: dict[str, Any] | None = None,
        parent: WorkRequest | None = None,
        validate: bool = True,
    ) -> ReverseDependenciesAutopkgtestWorkflow:
        """Create a reverse_dependencies_autopkgtest workflow."""
        task_data = {
            "source_artifact": 1,
            "binary_artifacts": ["internal@collections/name:build-amd64"],
            "suite_collection": "sid@debian:suite",
            "vendor": "debian",
            "codename": "sid",
        }
        if extra_task_data is not None:
            task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="reverse_dependencies_autopkgtest",
            task_data=task_data,
            parent=parent,
            validate=validate,
        )
        return ReverseDependenciesAutopkgtestWorkflow(wr)

    @context.disable_permission_checks()
    def create_source_package(
        self,
        *,
        name: str,
        version: str,
        dsc_fields: dict[str, Any] | None = None,
        add_to_collection: Collection | None = None,
    ) -> Artifact:
        """Create a `debian:source-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data=DebianSourcePackage(
                name=name,
                version=version,
                type="dpkg",
                dsc_fields={
                    "Package": name,
                    "Version": version,
                    **(dsc_fields or {}),
                },
            ),
        )
        if add_to_collection is not None:
            assert isinstance(add_to_collection.manager, DebianSuiteManager)
            add_to_collection.manager.add_source_package(
                artifact,
                user=self.playground.get_default_user(),
                component="main",
                section="devel",
            )
        return artifact

    @context.disable_permission_checks()
    def create_binary_package(
        self,
        *,
        source_package: Artifact,
        name: str | None = None,
        version: str | None = None,
        architecture: str = "all",
        deb_fields: dict[str, Any] | None = None,
        add_to_collection: Collection | None = None,
    ) -> Artifact:
        """Create a `debian:binary-package` artifact."""
        source_package_data = source_package.create_data()
        assert isinstance(source_package_data, DebianSourcePackage)
        if name is None:
            name = source_package_data.name
        if version is None:
            version = source_package_data.version
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data=DebianBinaryPackage(
                srcpkg_name=source_package_data.name,
                srcpkg_version=source_package_data.version,
                deb_fields={
                    "Package": name,
                    "Version": version,
                    "Architecture": architecture,
                    **(deb_fields or {}),
                },
                deb_control_files=[],
            ),
        )
        if add_to_collection is not None:
            assert isinstance(add_to_collection.manager, DebianSuiteManager)
            add_to_collection.manager.add_binary_package(
                artifact,
                user=self.playground.get_default_user(),
                component="main",
                section="devel",
                priority="optional",
            )
        return artifact

    def test_create_orchestrator(self) -> None:
        """A ReverseDependenciesAutopkgtestWorkflow can be instantiated."""
        self.playground.create_collection(
            name="trixie", category=CollectionCategory.SUITE
        )
        source_artifact = 2
        binary_artifacts = ["internal@collections/name:build-arm64"]
        suite_collection = "trixie@debian:suite"
        vendor = "debian"
        codename = "trixie"
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={
                "source_artifact": source_artifact,
                "binary_artifacts": binary_artifacts,
                "suite_collection": suite_collection,
                "vendor": vendor,
                "codename": codename,
            }
        )

        self.assertEqual(workflow.data.source_artifact, source_artifact)
        self.assertEqual(
            workflow.data.binary_artifacts,
            LookupMultiple.parse_obj(binary_artifacts),
        )
        self.assertEqual(workflow.data.suite_collection, suite_collection)
        self.assertEqual(workflow.data.vendor, vendor)
        self.assertEqual(workflow.data.codename, codename)
        self.assertEqual(workflow.data.backend, BackendType.UNSHARE)

    def test_create_orchestrator_explicit_backend(self) -> None:
        """A ReverseDependenciesAutopkgtestWorkflow can take a backend."""
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"backend": "incus-lxc"}
        )

        self.assertEqual(workflow.data.backend, BackendType.INCUS_LXC)

    def test_validate_input(self) -> None:
        """validate_input passes a valid case."""
        workflow = self.create_rdep_autopkgtest_workflow()

        workflow.validate_input()

    def test_validate_input_bad_suite_collection(self) -> None:
        """validate_input raises errors in looking up suite_collection."""
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"suite_collection": "nonexistent@debian:suite"},
            validate=False,
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            "'nonexistent@debian:suite' does not exist or is hidden",
        ):
            workflow.validate_input()

    def test_get_binary_names_promise(self) -> None:
        """Get binary names from a promise."""
        workflow = self.create_rdep_autopkgtest_workflow()
        assert workflow.work_request.internal_collection is not None
        self.playground.create_bare_data_item(
            workflow.work_request.internal_collection,
            "build-amd64",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": workflow.work_request.id + 1,
                "promise_workflow_id": workflow.work_request.id,
                "promise_category": ArtifactCategory.UPLOAD,
                "binary_names": ["hello", "hello-dev"],
            },
        )

        self.assertEqual(workflow.get_binary_names(), {"hello", "hello-dev"})

    def test_get_binary_names_binary_package(self) -> None:
        """Get binary names from a `debian:binary-package` artifact."""
        temp_dir = self.create_temporary_directory()
        with context.disable_permission_checks():
            binary_package = self.playground.create_artifact_from_local(
                self.playground.create_binary_package(temp_dir, name="single")
            )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"binary_artifacts": [binary_package.id]}
        )

        self.assertEqual(workflow.get_binary_names(), {"single"})

    def test_get_binary_names_binary_packages(self) -> None:
        """Get binary names from a `debian:binary-packages` artifact."""
        temp_dir = self.create_temporary_directory()
        paths = [
            temp_dir / "hello_1.0_amd64.deb",
            temp_dir / "hello-dev_1.0_amd64.deb",
        ]
        for path in paths:
            self.playground.write_deb_file(
                path, source_name="hello", source_version="1.0"
            )
        with context.disable_permission_checks():
            binary_packages = self.playground.create_artifact_from_local(
                BinaryPackages.create(
                    srcpkg_name="hello",
                    srcpkg_version="1.0",
                    version="1.0",
                    architecture="amd64",
                    files=paths,
                )
            )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"binary_artifacts": [binary_packages.id]}
        )

        self.assertEqual(workflow.get_binary_names(), {"hello", "hello-dev"})

    def test_get_binary_names_upload(self) -> None:
        """Get binary names from a `debian:upload` artifact."""
        temp_dir = self.create_temporary_directory()
        changes_path = temp_dir / "hello_1.0_amd64.changes"
        self.write_changes_file(
            changes_path, [], binaries=["hello", "hello-dev"]
        )
        with context.disable_permission_checks():
            upload = self.playground.create_artifact_from_local(
                Upload.create(changes_file=changes_path)
            )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"binary_artifacts": [upload.id]}
        )

        self.assertEqual(workflow.get_binary_names(), {"hello", "hello-dev"})

    def test_get_binary_names_bad_artifact_category(self) -> None:
        """Cannot get binary names from a non-binary artifact category."""
        temp_dir = self.create_temporary_directory()
        with context.disable_permission_checks():
            source_package = self.playground.create_artifact_from_local(
                self.playground.create_source_package(temp_dir)
            )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"binary_artifacts": [source_package.id]}
        )

        with self.assertRaisesRegex(
            NoBinaryNames,
            f"Artifact of category {ArtifactCategory.SOURCE_PACKAGE} has no "
            f"binary packages",
        ):
            workflow.get_binary_names()

    def test_get_reverse_dependencies_bad_source_artifact_category(
        self,
    ) -> None:
        """The source artifact must be a `debian:source-package`."""
        with context.disable_permission_checks():
            artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.TEST
            )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={"source_artifact": artifact.id}
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^source_artifact: unexpected artifact category: 'debusine:test'. "
            r"Valid categories: \['debian:source-package', 'debian:upload'\]$",
        ):
            workflow.get_reverse_dependencies()

    def assert_reverse_dependencies(
        self,
        workflow: ReverseDependenciesAutopkgtestWorkflow,
        lookups: Mapping[LookupSingle, Iterable[LookupSingle]],
    ) -> None:
        """Assert that reverse-dependencies of a workflow are correct."""
        expected = set()
        for source_lookup, binary_lookups in lookups.items():
            source = lookup_single(
                source_lookup,
                workflow.workspace,
                user=workflow.work_request.created_by,
                default_category=CollectionCategory.SUITE,
                workflow_root=workflow.work_request.get_workflow_root(),
                expect_type=LookupChildType.ARTIFACT,
            ).artifact
            source_data = source.create_data()
            assert isinstance(source_data, DebianSourcePackage)
            expected_source = _SourcePackage(
                suite_collection=workflow.data.suite_collection,
                name=source_data.name,
                version=source_data.version,
            )
            expected_binaries = set()
            for binary_lookup in binary_lookups:
                binary = lookup_single(
                    binary_lookup,
                    workflow.workspace,
                    user=workflow.work_request.created_by,
                    default_category=CollectionCategory.SUITE,
                    workflow_root=workflow.work_request.get_workflow_root(),
                    expect_type=LookupChildType.ARTIFACT,
                ).artifact
                binary_data = binary.create_data()
                assert isinstance(binary_data, DebianBinaryPackage)
                expected_binaries.add(
                    _BinaryPackage(
                        suite_collection=workflow.data.suite_collection,
                        name=binary_data.deb_fields["Package"],
                        version=binary_data.deb_fields["Version"],
                        architecture=binary_data.deb_fields["Architecture"],
                    )
                )
            expected.add((expected_source, frozenset(expected_binaries)))
        self.assertEqual(
            workflow.get_reverse_dependencies(), frozenset(expected)
        )

    def test_get_reverse_dependencies(self) -> None:
        """Get reverse dependencies of a source package and its binaries."""
        trixie = self.playground.create_collection(
            name="trixie", category=CollectionCategory.SUITE
        )

        pre_depends = self.create_source_package(
            name="pre-depends",
            version="1.0",
            dsc_fields={"Testsuite": "autopkgtest"},
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=pre_depends,
            name="python3-pre-depends",
            version="1:1.0",
            deb_fields={"Pre-Depends": "python3-urllib3:any"},
            add_to_collection=self.sid,
        )

        depends = self.create_source_package(
            name="depends",
            version="2.32.3+dfsg-1",
            dsc_fields={
                "Testsuite": "autopkgtest-pkg-python, autopkgtest-pkg-pybuild"
            },
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=depends,
            name="python3-depends",
            deb_fields={"Depends": "python3-urllib3 (>= 1.21.1), python3:any"},
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=depends,
            name="depends-doc",
            deb_fields={"Depends": "libjs-sphinxdoc"},
            add_to_collection=self.sid,
        )

        similar_depends = self.create_source_package(
            name="similar-depends",
            version="1",
            dsc_fields={"Testsuite": "autopkgtest-pkg-pybuild"},
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=similar_depends,
            name="python3-similar-depends",
            deb_fields={"Depends": "python3-urllib3-plugin"},
            add_to_collection=self.sid,
        )

        for version, add_to_collection in (("1", trixie), ("2", self.sid)):
            trigger = self.create_source_package(
                name="trigger",
                version=version,
                dsc_fields={
                    "Testsuite": "autopkgtest",
                    "Testsuite-Triggers": "python3-urllib3",
                },
                add_to_collection=add_to_collection,
            )
            self.create_binary_package(
                source_package=trigger, add_to_collection=add_to_collection
            )

        previous_version = self.create_source_package(
            name="python-urllib3",
            version="2.0.7-1",
            dsc_fields={
                "Testsuite": "autopkgtest",
                "Testsuite-Triggers": "python3-urllib3",
            },
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=previous_version, name="python3-urllib3"
        )

        unrelated = self.create_source_package(
            name="unrelated",
            version="1",
            dsc_fields={"Testsuite": "autopkgtest"},
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=unrelated, add_to_collection=add_to_collection
        )

        untested = self.create_source_package(
            name="untested", version="1", add_to_collection=self.sid
        )
        self.create_binary_package(
            source_package=untested,
            deb_fields={"Depends": "python3-urllib3"},
            add_to_collection=self.sid,
        )

        unsuitable_testsuite = self.create_source_package(
            name="unsuitable-testsuite",
            version="1",
            dsc_fields={
                "Testsuite": "autopkgtest-unsuitable",
                "Testsuite-Triggers": "python3-urllib3",
            },
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=unsuitable_testsuite, add_to_collection=self.sid
        )

        source_package = self.create_source_package(
            name="python-urllib3", version="2.0.7-2"
        )
        binary_package = self.create_binary_package(
            source_package=source_package, name="python3-urllib3"
        )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={
                "source_artifact": source_package.id,
                "binary_artifacts": [binary_package.id],
                "suite_collection": self.sid.id,
            }
        )

        self.assert_reverse_dependencies(
            workflow,
            {
                "sid/source-version:pre-depends_1.0": (
                    "sid/binary-version:python3-pre-depends_1:1.0_all",
                ),
                "sid/source-version:depends_2.32.3+dfsg-1": (
                    "sid/binary-version:python3-depends_2.32.3+dfsg-1_all",
                    "sid/binary-version:depends-doc_2.32.3+dfsg-1_all",
                ),
                "sid/source-version:trigger_2": (
                    "sid/binary-version:trigger_2_all",
                ),
            },
        )

        workflow.data.packages_denylist = ["depends"]
        self.assert_reverse_dependencies(
            workflow,
            {
                "sid/source-version:pre-depends_1.0": (
                    "sid/binary-version:python3-pre-depends_1:1.0_all",
                ),
                "sid/source-version:trigger_2": (
                    "sid/binary-version:trigger_2_all",
                ),
            },
        )

        workflow.data.packages_denylist = []
        workflow.data.packages_allowlist = ["depends"]
        self.assert_reverse_dependencies(
            workflow,
            {
                "sid/source-version:depends_2.32.3+dfsg-1": (
                    "sid/binary-version:python3-depends_2.32.3+dfsg-1_all",
                    "sid/binary-version:depends-doc_2.32.3+dfsg-1_all",
                ),
            },
        )

        upload_artifacts = self.playground.create_upload_artifacts(
            src_name="python-urllib3",
            version="2.0.7-2",
            binaries=["python3-urllib3"],
        )
        workflow = self.create_rdep_autopkgtest_workflow(
            extra_task_data={
                "source_artifact": upload_artifacts.upload.id,
                "binary_artifacts": [upload_artifacts.binaries[0].id],
                "suite_collection": self.sid.id,
            }
        )
        self.assert_reverse_dependencies(
            workflow,
            {
                "sid/source-version:pre-depends_1.0": (
                    "sid/binary-version:python3-pre-depends_1:1.0_all",
                ),
                "sid/source-version:depends_2.32.3+dfsg-1": (
                    "sid/binary-version:python3-depends_2.32.3+dfsg-1_all",
                    "sid/binary-version:depends-doc_2.32.3+dfsg-1_all",
                ),
                "sid/source-version:trigger_2": (
                    "sid/binary-version:trigger_2_all",
                ),
            },
        )

    def test_populate(self) -> None:
        """The workflow populates child work requests."""
        depends = self.create_source_package(
            name="depends",
            version="1.0",
            dsc_fields={"Testsuite": "autopkgtest"},
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=depends,
            deb_fields={"Depends": "hello"},
            add_to_collection=self.sid,
        )
        trigger = self.create_source_package(
            name="trigger",
            version="1.0",
            dsc_fields={
                "Testsuite": "autopkgtest",
                "Testsuite-Triggers": "hello",
            },
            add_to_collection=self.sid,
        )
        self.create_binary_package(
            source_package=trigger, add_to_collection=self.sid
        )
        source_artifact = self.create_source_package(
            name="hello", version="1.0", dsc_fields={"Binary": "hello"}
        )
        architectures = ("amd64", "i386")

        with preserve_task_registry():

            class ExamplePipeline(
                TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
            ):
                """Pipeline workflow that runs sbuild and rdep-autopkgtest."""

                def populate(self) -> None:
                    """Populate the pipeline."""
                    sid = Collection.objects.get(
                        name="sid", category=CollectionCategory.SUITE
                    )
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
                            data={
                                "binary_names": ["hello"],
                                "architecture": architecture,
                            },
                        )

                    rdep_autopkgtest = self.work_request.create_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="reverse_dependencies_autopkgtest",
                        task_data=ReverseDependenciesAutopkgtestWorkflowData(
                            source_artifact=source_artifact.id,
                            binary_artifacts=LookupMultiple.parse_obj(
                                [
                                    f"internal@collections/"
                                    f"name:build-{architecture}"
                                    for architecture in ("all", *architectures)
                                ]
                            ),
                            suite_collection=sid.id,
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
                    ReverseDependenciesAutopkgtestWorkflow(
                        rdep_autopkgtest
                    ).populate()

            root = self.playground.create_workflow(task_name="examplepipeline")

            root.mark_running()
            orchestrate_workflow(root)

            rdep_autopkgtest = WorkRequest.objects.get(
                task_type=TaskTypes.WORKFLOW,
                task_name="reverse_dependencies_autopkgtest",
                parent=root,
            )
            children = list(
                WorkRequest.objects.filter(parent=rdep_autopkgtest).order_by(
                    "task_data__prefix"
                )
            )
            self.maxDiff = None
            for child, source in zip(children, ("depends_1.0", "trigger_1.0")):
                self.assertEqual(child.status, WorkRequest.Statuses.RUNNING)
                self.assertEqual(child.task_type, TaskTypes.WORKFLOW)
                self.assertEqual(child.task_name, "autopkgtest")
                self.assertEqual(
                    child.task_data,
                    {
                        "prefix": f"{source}|",
                        "source_artifact": (
                            f"{self.sid.id}@collections/source-version:{source}"
                        ),
                        "binary_artifacts": [
                            f"{self.sid.id}@collections/"
                            f"binary-version:{source}_all"
                        ],
                        "context_artifacts": [
                            f"internal@collections/name:build-{architecture}"
                            for architecture in ("all", *architectures)
                        ],
                        "vendor": "debian",
                        "codename": "sid",
                        "backend": BackendType.UNSHARE,
                        "architectures": [],
                        "arch_all_host_architecture": "amd64",
                        "debug_level": 0,
                        "extra_repositories": [
                            {
                                "url": "http://example.com/",
                                "suite": "bookworm",
                                "components": ["main"],
                            }
                        ],
                    },
                )

                grandchild = WorkRequest.objects.get(parent=child)
                self.assertEqual(
                    grandchild.status, WorkRequest.Statuses.BLOCKED
                )
                self.assertEqual(grandchild.task_type, TaskTypes.WORKER)
                self.assertEqual(grandchild.task_name, "autopkgtest")
                self.assertEqual(
                    grandchild.task_data,
                    {
                        "input": {
                            "source_artifact": (
                                f"{self.sid.id}@collections/"
                                f"source-version:{source}"
                            ),
                            "binary_artifacts": [
                                f"{self.sid.id}@collections/name:{source}_all"
                            ],
                            "context_artifacts": [
                                f"internal@collections/"
                                f"name:build-{architecture}"
                                for architecture in ("all", "amd64")
                            ],
                        },
                        "host_architecture": "amd64",
                        "environment": "debian/match:codename=sid",
                        "backend": BackendType.UNSHARE,
                        "include_tests": [],
                        "exclude_tests": [],
                        "debug_level": 0,
                        "extra_environment": {},
                        "extra_repositories": [
                            {
                                "url": "http://example.com/",
                                "suite": "bookworm",
                                "components": ["main"],
                            }
                        ],
                        "needs_internet": AutopkgtestNeedsInternet.RUN,
                        "fail_on": {},
                        "timeout": None,
                    },
                )
                self.assertEqual(
                    grandchild.event_reactions_json,
                    {
                        "on_creation": [],
                        "on_failure": [],
                        "on_success": [
                            {
                                "action": "update-collection-with-artifacts",
                                "collection": "internal@collections",
                                "name_template": f"{source}|autopkgtest-amd64",
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
                    grandchild.dependencies.all(),
                    list(
                        WorkRequest.objects.filter(
                            task_type=TaskTypes.WORKER,
                            task_name="sbuild",
                            task_data__host_architecture__in={"all", "amd64"},
                        )
                    ),
                )
                self.assertEqual(
                    grandchild.workflow_data_json,
                    {
                        "display_name": "autopkgtest amd64",
                        "step": "autopkgtest-amd64",
                    },
                )

            # Population is idempotent.
            ReverseDependenciesAutopkgtestWorkflow(rdep_autopkgtest).populate()
            children = list(WorkRequest.objects.filter(parent=rdep_autopkgtest))
            self.assertEqual(len(children), 2)

    def test_compute_dynamic_data(self) -> None:
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact()
        )
        wr = self.playground.create_workflow(
            task_name="reverse_dependencies_autopkgtest",
            task_data=ReverseDependenciesAutopkgtestWorkflowData(
                source_artifact=source_artifact.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
                suite_collection="sid@debian:suite",
            ),
        )
        workflow = ReverseDependenciesAutopkgtestWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(
                subject="hello", parameter_summary="hello_1.0-1"
            ),
        )

    def test_label(self) -> None:
        """Test get_label."""
        w = self.create_rdep_autopkgtest_workflow()
        self.assertEqual(
            w.get_label(), "run autopkgtests of reverse-dependencies"
        )
