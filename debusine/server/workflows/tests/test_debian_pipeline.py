# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the debian pipeline workflow."""
from typing import Any, ClassVar

from django.test import override_settings

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.assets import KeyPurpose
from debusine.client.models import LookupChildType
from debusine.db.context import context
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.scheduler import schedule
from debusine.server.workflows import (
    DebianPipelineWorkflow,
    WorkflowValidationError,
    workflow_utils,
)
from debusine.server.workflows.base import (
    WorkflowRunError,
    orchestrate_workflow,
)
from debusine.server.workflows.models import DebianPipelineWorkflowData
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    CollectionItemMatcherKind,
    TaskTypes,
)
from debusine.test.django import TestCase


class DebianPipelineWorkflowTests(TestCase):
    """Unit tests for :py:class:`DebianPipelineWorkflow`."""

    source_artifact: ClassVar[Artifact]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.source_artifact = cls.playground.create_source_artifact(
            name="hello"
        )

    def create_debian_pipeline_workflow(
        self,
        *,
        extra_task_data: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> DebianPipelineWorkflow:
        """Create a debian pipeline workflow."""
        task_data = {
            "source_artifact": 10,
            "vendor": "debian",
            "codename": "bookworm",
        }
        if extra_task_data is not None:
            task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="debian_pipeline", task_data=task_data, validate=validate
        )
        return DebianPipelineWorkflow(wr)

    def orchestrate(self, extra_data: dict[str, Any]) -> WorkRequest:
        """Create and orchestrate a DebianPipelineWorkflow."""
        data = {
            "source_artifact": 10,
            "vendor": "debian",
            "codename": "bookworm",
            "enable_autopkgtest": False,
            "enable_lintian": False,
            "enable_piuparts": False,
            **extra_data,
        }

        wr = self.playground.create_workflow(
            task_name="debian_pipeline",
            task_data=DebianPipelineWorkflowData(**data),
        )
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        return wr

    def test_validate_input(self) -> None:
        """validate_input passes a valid case."""
        workflow = self.create_debian_pipeline_workflow()

        workflow.validate_input()

    def test_validate_input_bad_suite_collection(self) -> None:
        """validate_input raises errors in looking up a suite."""
        workflow = self.create_debian_pipeline_workflow(
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
        """Dynamic data includes a parameter summary."""
        source_artifact = self.playground.create_source_artifact(
            name="hello", version="1.0-1"
        )
        wr = self.playground.create_workflow(
            task_name="debian_pipeline",
            task_data=DebianPipelineWorkflowData(
                source_artifact=source_artifact.id,
                vendor="debian",
                codename="sid",
            ),
        )
        workflow = DebianPipelineWorkflow(wr)

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
            task_name="debian_pipeline",
            task_data=DebianPipelineWorkflowData(
                source_artifact=artifact.id, vendor="debian", codename="sid"
            ),
        )
        workflow = DebianPipelineWorkflow(wr)

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
            task_name="debian_pipeline",
            task_data=DebianPipelineWorkflowData(
                source_artifact=upload_artifacts.upload.id,
                vendor="debian",
                codename="sid",
            ),
        )
        workflow = DebianPipelineWorkflow(wr)
        self.assertEqual(
            workflow_utils.source_package(workflow), upload_artifacts.source
        )

    def test_populate_use_available_architectures(self) -> None:
        """
        Test populate use available architectures.

        The user didn't specify "architectures", DebianPipelineWorkflow
        checks available architectures and "all" and use them.
        """
        with context.disable_permission_checks():
            collection = self.playground.create_collection(
                "debian",
                CollectionCategory.ENVIRONMENTS,
                workspace=self.playground.get_default_workspace(),
            )
            for arch in ["amd64", "i386"]:
                artifact, _ = self.playground.create_artifact(
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data={"codename": "bookworm", "architecture": arch},
                )
                collection.manager.add_artifact(
                    artifact, user=self.playground.get_default_user()
                )

        workflow = self.orchestrate(
            extra_data={
                "source_artifact": self.source_artifact.id,
                "vendor": "debian",
                "codename": "bookworm",
            }
        )

        sbuild = workflow.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            sbuild.task_data["architectures"], ["all", "amd64", "i386"]
        )

    def test_populate_sbuild(self) -> None:
        """Test populate create sbuild."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
            }
        )

        sbuild = workflow.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(sbuild.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(
            sbuild.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "backend": BackendType.AUTO,
                "environment_variant": None,
                "extra_repositories": None,
                "input": {"source_artifact": self.source_artifact.id},
                "target_distribution": "debian:bookworm",
                "signing_template_names": {},
            },
        )
        self.assertEqual(
            sbuild.workflow_data_json,
            {"display_name": "sbuild", "step": "sbuild"},
        )

        # SbuildWorkflow.populate() was called and created its tasks
        self.assertTrue(sbuild.children.exists())
        for child in sbuild.children.all():
            self.assertEqual(child.status, WorkRequest.Statuses.PENDING)

        self.assertFalse(
            workflow.children.filter(
                task_name="qa", task_type=TaskTypes.WORKFLOW
            ).exists()
        )

    def assert_qa(
        self, workflow: WorkRequest, task_data: dict[str, Any]
    ) -> None:
        """Assert workflow has a sub-workflow qa with task_data."""
        qa = workflow.children.get(task_name="qa", task_type=TaskTypes.WORKFLOW)
        self.assertEqual(qa.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(qa.task_data, task_data)

        self.assertEqual(
            qa.workflow_data_json, {"display_name": "QA", "step": "qa"}
        )

        # QaWorkflow.populate() was called and created its tasks
        self.assertTrue(qa.children.exists())
        for child in qa.children.all():
            # Children are sub-workflows, so go straight to RUNNING.
            self.assertEqual(child.status, WorkRequest.Statuses.RUNNING)

        self.assertQuerySetEqual(
            qa.children.earliest("id").dependencies.all(), []
        )

    def test_populate_qa_with_sbuild_subset(self) -> None:
        """`sbuild` may only create a subset of the requested architectures."""
        self.assertEqual(
            self.source_artifact.data["dsc_fields"]["Architecture"], "any"
        )

        workflow = self.orchestrate(
            extra_data={
                "architectures": ["all", "amd64"],
                "source_artifact": f"{self.source_artifact.id}@artifacts",
                "enable_lintian": True,
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
            }
        )

        sbuild = workflow.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            sbuild.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["all", "amd64"],
                "backend": BackendType.AUTO,
                "environment_variant": None,
                "input": {
                    "source_artifact": f"{self.source_artifact.id}@artifacts"
                },
                "target_distribution": "debian:bookworm",
                "signing_template_names": {},
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
            sbuild.workflow_data_json,
            {"display_name": "sbuild", "step": "sbuild"},
        )

        self.assertTrue(sbuild.children.exists())
        self.assert_qa(
            workflow,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["all", "amd64"],
                "autopkgtest_backend": BackendType.AUTO,
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "bookworm",
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ],
                "enable_autopkgtest": False,
                "enable_lintian": True,
                "enable_piuparts": False,
                "enable_reverse_dependencies_autopkgtest": False,
                "lintian_backend": BackendType.AUTO,
                "lintian_fail_on_severity": "none",
                "piuparts_backend": BackendType.AUTO,
                "reverse_dependencies_autopkgtest_suite": None,
                "source_artifact": f"{self.source_artifact.id}@artifacts",
                "vendor": "debian",
            },
        )

    def test_populate_qa_lintian(self) -> None:
        """Test populate create qa: lintian enabled."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
                "enable_lintian": True,
            }
        )
        self.assert_qa(
            workflow,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "autopkgtest_backend": BackendType.AUTO,
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "bookworm",
                "extra_repositories": None,
                "enable_autopkgtest": False,
                "enable_lintian": True,
                "enable_piuparts": False,
                "enable_reverse_dependencies_autopkgtest": False,
                "lintian_backend": BackendType.AUTO,
                "lintian_fail_on_severity": "none",
                "piuparts_backend": BackendType.AUTO,
                "reverse_dependencies_autopkgtest_suite": None,
                "source_artifact": self.source_artifact.id,
                "vendor": "debian",
            },
        )

    def test_populate_qa_autopkgtest(self) -> None:
        """Test populate create qa: autopkgtest enabled."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
                "enable_autopkgtest": True,
            }
        )

        self.assert_qa(
            workflow,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "autopkgtest_backend": BackendType.AUTO,
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "bookworm",
                "extra_repositories": None,
                "enable_autopkgtest": True,
                "enable_lintian": False,
                "enable_piuparts": False,
                "enable_reverse_dependencies_autopkgtest": False,
                "lintian_backend": BackendType.AUTO,
                "lintian_fail_on_severity": "none",
                "piuparts_backend": BackendType.AUTO,
                "reverse_dependencies_autopkgtest_suite": None,
                "source_artifact": self.source_artifact.id,
                "vendor": "debian",
            },
        )

    def test_populate_qa_piuparts(self) -> None:
        """Test populate create qa: piuparts enabled."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
                "enable_piuparts": True,
            }
        )

        self.assert_qa(
            workflow,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "autopkgtest_backend": BackendType.AUTO,
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "bookworm",
                "extra_repositories": None,
                "enable_autopkgtest": False,
                "enable_lintian": False,
                "enable_piuparts": True,
                "enable_reverse_dependencies_autopkgtest": False,
                "lintian_backend": BackendType.AUTO,
                "lintian_fail_on_severity": "none",
                "piuparts_backend": BackendType.AUTO,
                "reverse_dependencies_autopkgtest_suite": None,
                "source_artifact": self.source_artifact.id,
                "vendor": "debian",
            },
        )

    def test_populate_qa_piuparts_configuration(self) -> None:
        """Test populate create qa: piuparts non-default configuration."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
                "enable_piuparts": True,
                "piuparts_backend": BackendType.INCUS_LXC,
                "piuparts_environment": "debian/match:codename=trixie",
            }
        )

        self.assert_qa(
            workflow,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["amd64"],
                "autopkgtest_backend": BackendType.AUTO,
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "bookworm",
                "extra_repositories": None,
                "enable_autopkgtest": False,
                "enable_lintian": False,
                "enable_piuparts": True,
                "enable_reverse_dependencies_autopkgtest": False,
                "lintian_backend": BackendType.AUTO,
                "lintian_fail_on_severity": "none",
                "piuparts_backend": BackendType.INCUS_LXC,
                "piuparts_environment": "debian/match:codename=trixie",
                "reverse_dependencies_autopkgtest_suite": None,
                "source_artifact": self.source_artifact.id,
                "vendor": "debian",
            },
        )

    def test_populate_architectures_allow_deny(self) -> None:
        """Populate uses architectures, architectures_allow/deny."""
        workflow = self.orchestrate(
            extra_data={
                "source_artifact": self.source_artifact.id,
                "architectures": ["amd64", "i386", "arm64"],
                "architectures_allowlist": ["amd64", "i386"],
                "architectures_denylist": ["amd64"],
            }
        )

        sbuild = workflow.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(
            sbuild.task_data,
            {
                "arch_all_host_architecture": "amd64",
                "architectures": ["i386"],
                "backend": BackendType.AUTO,
                "environment_variant": None,
                "extra_repositories": None,
                "input": {"source_artifact": self.source_artifact.id},
                "target_distribution": "debian:bookworm",
                "signing_template_names": {},
            },
        )

    def test_populate_make_signed_source_missing_signed_source_purpose(
        self,
    ) -> None:
        """Populate raise WorkflowRunError: missing required field."""
        msg = (
            'Orchestrator failed: "make_signed_source_purpose" must be '
            'set when signing the source'
        )
        with self.assertRaisesRegex(WorkflowRunError, msg):
            self.orchestrate(
                extra_data={
                    "source_artifact": self.source_artifact.id,
                    "architectures": ["amd64"],
                    "enable_make_signed_source": True,
                    "signing_template_names": {"amd64": ["hello"]},
                }
            )

    def test_populate_make_signed_source_missing_make_signed_source_key(
        self,
    ) -> None:
        """Populate raise WorkflowRunError: missing required field."""
        msg = (
            'Orchestrator failed: "make_signed_source_key" must be set '
            'when signing the source'
        )
        with self.assertRaisesRegex(WorkflowRunError, msg):
            self.orchestrate(
                extra_data={
                    "source_artifact": self.source_artifact.id,
                    "architectures": ["amd64"],
                    "enable_make_signed_source": True,
                    "make_signed_source_purpose": KeyPurpose.OPENPGP,
                    "signing_template_names": {"amd64": ["hello"]},
                }
            )

    def test_populate_make_signed_source(self) -> None:
        """Populate create a make_signed_source workflow."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
                "enable_make_signed_source": True,
                "make_signed_source_purpose": KeyPurpose.OPENPGP,
                "signing_template_names": {"amd64": ["hello"]},
                "make_signed_source_key": "ABC123",
            }
        )

        expected_binary_artifacts = [
            {
                "collection": "internal@collections",
                "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                "name_matcher": {
                    "kind": CollectionItemMatcherKind.STARTSWITH,
                    "value": "build-",
                },
            }
        ]
        make_signed_source = workflow.children.get(
            task_name="make_signed_source", task_type=TaskTypes.WORKFLOW
        )
        self.assertEqual(
            make_signed_source.task_data,
            {
                "architectures": ["amd64"],
                "binary_artifacts": expected_binary_artifacts,
                "codename": "bookworm",
                "key": "ABC123",
                "purpose": "openpgp",
                "sbuild_backend": BackendType.AUTO,
                "signing_template_artifacts": [
                    "internal@collections/name:signing-template-amd64-hello"
                ],
                "vendor": "debian",
            },
        )

        self.assertEqual(
            make_signed_source.workflow_data_json,
            {
                "display_name": "make signed source",
                "step": "make_signed_source",
            },
        )
        self.assertQuerySetEqual(make_signed_source.dependencies.all(), [])

        # MakeSignedSource.populate() was called and created its tasks.
        # They are all blocked, with the first one depending on sbuild and
        # the others depending on the previous one in sequence.
        self.assertQuerySetEqual(
            make_signed_source.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "extractforsigning",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                ),
                (
                    TaskTypes.SIGNING,
                    "sign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "extractforsigning",
                ),
                (
                    TaskTypes.WORKER,
                    "assemblesignedsource",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.SIGNING,
                    "sign",
                ),
                (
                    TaskTypes.WORKFLOW,
                    "sbuild",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "assemblesignedsource",
                ),
            ],
        )

    def test_populate_package_upload(self) -> None:
        """Populate create an upload_package workflow."""
        workflow = self.orchestrate(
            extra_data={
                "source_artifact": self.source_artifact.id,
                "architectures": ["amd64", "i386"],
                "enable_upload": True,
                "vendor": "debian",
                "codename": "trixie",
                "upload_merge_uploads": False,
                "upload_since_version": "1.1",
                "upload_target": "ftp://user@example.org/pub/UploadQueue1/",
                "upload_target_distribution": "debian:testing",
            }
        )

        package_upload = workflow.children.get(
            task_name="package_upload", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            package_upload.task_data,
            {
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "trixie",
                "delayed_days": None,
                "key": None,
                "merge_uploads": False,
                "require_signature": True,
                "since_version": "1.1",
                "source_artifact": self.source_artifact.id,
                "target": "ftp://user@example.org/pub/UploadQueue1/",
                "target_distribution": "debian:testing",
                "vendor": "debian",
            },
        )

        self.assertEqual(
            package_upload.workflow_data_json,
            {"display_name": "package upload", "step": "package_upload"},
        )
        self.assertQuerySetEqual(package_upload.dependencies.all(), [])

        # PackageUpload.populate() was called and created its tasks.
        # MakeSourcePackageUpload only requires the source artifact and so
        # can start immediately.  Everything else is blocked.
        self.assertQuerySetEqual(
            package_upload.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            # Work requests to construct, sign, and upload the source package.
            [
                (
                    TaskTypes.WORKER,
                    "makesourcepackageupload",
                    WorkRequest.Statuses.PENDING,
                    None,
                    None,
                ),
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "makesourcepackageupload",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ]
            # Work requests to sign and upload each of the two binary packages.
            + [
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ]
            * 2,
        )

    def test_populate_package_upload_signed_source(self) -> None:
        """Populate creates package_upload workflows for signed source."""
        workflow = self.orchestrate(
            extra_data={
                "source_artifact": self.source_artifact.id,
                "architectures": ["amd64", "i386"],
                "enable_make_signed_source": True,
                "make_signed_source_purpose": KeyPurpose.OPENPGP,
                "signing_template_names": {"amd64": ["hello"]},
                "make_signed_source_key": "ABC123",
                "enable_upload": True,
                "vendor": "debian",
                "codename": "trixie",
                "upload_merge_uploads": False,
                "upload_since_version": "1.1",
                "upload_target": "ftp://user@example.org/pub/UploadQueue1/",
                "upload_target_distribution": "debian:testing",
            }
        )

        initial_sbuild_workflow = workflow.children.get(
            task_name="sbuild", task_type=TaskTypes.WORKFLOW
        )
        initial_sbuild_amd64 = initial_sbuild_workflow.children.get(
            task_name="sbuild",
            task_type=TaskTypes.WORKER,
            task_data__host_architecture="amd64",
        )
        make_signed_source = workflow.children.get(
            task_name="make_signed_source", task_type=TaskTypes.WORKFLOW
        )
        extract_for_signing = make_signed_source.children.get(
            task_name="extractforsigning", task_type=TaskTypes.WORKER
        )
        sign = make_signed_source.children.get(
            task_name="sign", task_type=TaskTypes.SIGNING
        )
        assemble_signed_source = make_signed_source.children.get(
            task_name="assemblesignedsource", task_type=TaskTypes.WORKER
        )
        package_upload_source, package_upload_signed_amd64 = (
            workflow.children.filter(
                task_name="package_upload", task_type=TaskTypes.WORKFLOW
            ).order_by("id")
        )

        self.assertEqual(
            package_upload_source.task_data,
            {
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "trixie",
                "delayed_days": None,
                "key": None,
                "merge_uploads": False,
                "require_signature": True,
                "since_version": "1.1",
                "source_artifact": self.source_artifact.id,
                "target": "ftp://user@example.org/pub/UploadQueue1/",
                "target_distribution": "debian:testing",
                "vendor": "debian",
            },
        )

        self.assertEqual(
            package_upload_source.workflow_data_json,
            {"display_name": "package upload", "step": "package_upload"},
        )
        self.assertQuerySetEqual(package_upload_source.dependencies.all(), [])

        # PackageUpload.populate() was called and created its tasks.
        # MakeSourcePackageUpload only requires the source artifact and so
        # can start immediately.  Everything else is blocked.
        self.assertQuerySetEqual(
            package_upload_source.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            # Work requests to construct, sign, and upload the source package.
            [
                (
                    TaskTypes.WORKER,
                    "makesourcepackageupload",
                    WorkRequest.Statuses.PENDING,
                    None,
                    None,
                ),
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "makesourcepackageupload",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ]
            # Work requests to sign and upload each of the two binary packages.
            + [
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ]
            * 2,
        )

        self.assertEqual(
            package_upload_signed_amd64.task_data,
            {
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "signed-source-amd64-hello|build-",
                        },
                    }
                ],
                "codename": "trixie",
                "delayed_days": None,
                "key": None,
                "merge_uploads": False,
                "require_signature": True,
                "since_version": "1.1",
                "source_artifact": (
                    "internal@collections/name:signed-source-amd64-hello"
                ),
                "target": "ftp://user@example.org/pub/UploadQueue1/",
                "target_distribution": "debian:testing",
                "vendor": "debian",
            },
        )

        self.assertEqual(
            package_upload_signed_amd64.workflow_data_json,
            {"display_name": "package upload", "step": "package_upload"},
        )

        # The workflow can't orchestrate uploads of the signed source
        # package and its binaries until the signed source package has been
        # assembled.
        self.assertEqual(
            package_upload_signed_amd64.status, WorkRequest.Statuses.BLOCKED
        )
        self.assertQuerySetEqual(
            package_upload_signed_amd64.dependencies.all(),
            [assemble_signed_source],
        )
        self.assertQuerySetEqual(package_upload_signed_amd64.children.all(), [])

        # Assembling the signed source package causes the remaining uploads
        # to be orchestrated.
        self.assertTrue(
            initial_sbuild_amd64.mark_completed(WorkRequest.Results.SUCCESS)
        )
        self.playground.create_signing_input_artifact(
            binary_package_name="hello-signed",
            workspace=extract_for_signing.workspace,
            work_request=extract_for_signing,
        )
        extract_for_signing.refresh_from_db()
        self.assertTrue(
            extract_for_signing.mark_completed(WorkRequest.Results.SUCCESS)
        )
        self.playground.create_signing_output_artifact(
            purpose=KeyPurpose.OPENPGP,
            fingerprint="ABC123",
            binary_package_name="hello-signed",
            workspace=sign.workspace,
            work_request=sign,
        )
        sign.refresh_from_db()
        self.assertTrue(sign.mark_completed(WorkRequest.Results.SUCCESS))
        self.playground.create_upload_artifacts(
            src_name="hello-signed-template",
            source=True,
            binary=False,
            workspace=assemble_signed_source.workspace,
            work_request=assemble_signed_source,
        )
        assemble_signed_source.refresh_from_db()
        self.assertTrue(
            assemble_signed_source.mark_completed(WorkRequest.Results.SUCCESS)
        )
        package_upload_signed_amd64.refresh_from_db()
        self.assertEqual(
            package_upload_signed_amd64.status, WorkRequest.Statuses.PENDING
        )
        with self.captureOnCommitCallbacks() as on_commit:
            self.assertIn(workflow, schedule())
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            for callback in on_commit:
                callback()

        package_upload_signed_amd64.refresh_from_db()
        self.assertEqual(
            package_upload_signed_amd64.status, WorkRequest.Statuses.RUNNING
        )
        self.assertQuerySetEqual(
            package_upload_signed_amd64.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                # Work requests to sign and upload the source package.
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.PENDING,
                    None,
                    None,
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
                # Work requests to sign and upload the binary package.
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ],
        )

    def test_populate_upload_package_no_include_source_binaries(self) -> None:
        """Populate create upload_workflow without source neither binaries."""
        workflow = self.orchestrate(
            extra_data={
                "architectures": ["amd64"],
                "source_artifact": self.source_artifact.id,
                "enable_upload": True,
                "vendor": "debian",
                "codename": "trixie",
                "upload_merge_uploads": False,
                "upload_since_version": "1.1",
                "upload_target": "ftp://user@example.org/pub/UploadQueue1/",
                "upload_include_source": False,
                "upload_include_binaries": False,
            }
        )

        package_upload = workflow.children.get(
            task_name="package_upload", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            package_upload.task_data,
            {
                "binary_artifacts": [],
                "codename": "trixie",
                "delayed_days": None,
                "key": None,
                "merge_uploads": False,
                "require_signature": True,
                "since_version": "1.1",
                "source_artifact": None,
                "target": "ftp://user@example.org/pub/UploadQueue1/",
                "target_distribution": None,
                "vendor": "debian",
            },
        )

        self.assertEqual(
            package_upload.workflow_data_json,
            {"display_name": "package upload", "step": "package_upload"},
        )

    def test_populate_package_upload_delayed(self) -> None:
        """Populate an upload_package workflow for a delayed upload."""
        workflow = self.orchestrate(
            extra_data={
                "source_artifact": self.source_artifact.id,
                "architectures": ["amd64", "i386"],
                "enable_upload": True,
                "vendor": "debian",
                "codename": "trixie",
                "upload_merge_uploads": False,
                "upload_since_version": "1.1",
                "upload_target": "ftp://user@example.org/pub/UploadQueue1/",
                "upload_target_distribution": "debian:testing",
                "upload_delayed_days": 3,
            }
        )

        package_upload = workflow.children.get(
            task_name="package_upload", task_type=TaskTypes.WORKFLOW
        )

        self.assertEqual(
            package_upload.task_data,
            {
                "binary_artifacts": [
                    {
                        "collection": "internal@collections",
                        "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        "name_matcher": {
                            "kind": CollectionItemMatcherKind.STARTSWITH,
                            "value": "build-",
                        },
                    }
                ],
                "codename": "trixie",
                "delayed_days": 3,
                "key": None,
                "merge_uploads": False,
                "require_signature": True,
                "since_version": "1.1",
                "source_artifact": self.source_artifact.id,
                "target": "ftp://user@example.org/pub/UploadQueue1/",
                "target_distribution": "debian:testing",
                "vendor": "debian",
            },
        )

        self.assertEqual(
            package_upload.workflow_data_json,
            {"display_name": "package upload", "step": "package_upload"},
        )
        self.assertQuerySetEqual(package_upload.dependencies.all(), [])

        # PackageUpload.populate() was called and created its tasks.
        # MakeSourcePackageUpload only requires the source artifact and so
        # can start immediately.  Everything else is blocked.
        self.assertQuerySetEqual(
            package_upload.children.order_by("id").values_list(
                "task_type",
                "task_name",
                "status",
                "dependencies__task_type",
                "dependencies__task_name",
            ),
            [
                (
                    TaskTypes.WORKER,
                    "makesourcepackageupload",
                    WorkRequest.Statuses.PENDING,
                    None,
                    None,
                ),
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "makesourcepackageupload",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ]
            + [
                (
                    TaskTypes.WAIT,
                    "externaldebsign",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WORKER,
                    "sbuild",
                ),
                (
                    TaskTypes.SERVER,
                    "packageupload",
                    WorkRequest.Statuses.BLOCKED,
                    TaskTypes.WAIT,
                    "externaldebsign",
                ),
            ]
            * 2,
        )

    def test_get_label(self) -> None:
        """Test get_label."""
        w = self.create_debian_pipeline_workflow()
        self.assertEqual(w.get_label(), "run Debian pipeline")
