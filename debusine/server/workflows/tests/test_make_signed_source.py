# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the make_signed_source workflow."""
from collections.abc import Sequence
from typing import Any

from django.test import override_settings

from debusine.artifacts.models import (
    ArtifactCategory,
    DebusineSigningInput,
    DebusineSigningOutput,
)
from debusine.assets import KeyPurpose
from debusine.client.models import LookupChildType
from debusine.db.context import context
from debusine.db.models import Artifact, TaskDatabase, WorkRequest
from debusine.server.collections.lookup import lookup_multiple, lookup_single
from debusine.server.scheduler import schedule
from debusine.server.workflows import MakeSignedSourceWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    MakeSignedSourceWorkflowData,
    SbuildWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.tasks.models import (
    BaseDynamicTaskData,
    LookupMultiple,
    SbuildInput,
    TaskTypes,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class MakeSignedSourceWorkflowTests(TestCase):
    """Unit tests for :py:class:`MakeSignedSourceWorkflow`."""

    def create_make_signed_source_workflow(
        self,
        *,
        extra_task_data: dict[str, Any],
    ) -> MakeSignedSourceWorkflow:
        """Create a make_signed_source workflow."""
        task_data = {
            "binary_artifacts": [
                "internal@collections/name:build-amd64",
                "internal@collections/name:build-i386",
            ],
            "signing_template_artifacts": [200, 201],
            "architectures": ["amd64", "i386"],
            "vendor": "debian",
            "codename": "bookworm",
            "purpose": "uefi",
            "key": "ABC123",
        }
        task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="make_signed_source", task_data=task_data
        )
        return MakeSignedSourceWorkflow(wr)

    def create_signing_template_artifact(self, architecture: str) -> Artifact:
        """
        Create a signing template artifact.

        :param architecture: CollectionItem.data["architecture"]
        """
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0.0",
                "deb_fields": {
                    "Package": "hello",
                    "Version": "1.0.0",
                    "Architecture": architecture,
                },
                "deb_control_files": [],
            },
        )
        return artifact

    def orchestrate(
        self,
        task_data: MakeSignedSourceWorkflowData,
        architectures: Sequence[str],
        create_sbuild_promises: bool,
        extra_sbuild_architectures: Sequence[str] | None = None,
    ) -> WorkRequest:
        """
        Create and orchestrate a MakeSignedSourceWorkflow.

        :param task_data: data for the workflow
        :param architectures: for each architecture, depending on "mode" param,
          creates an sbuild child promising a binary SIGNING_INPUT and
          BINARY_PACKAGE promising artifacts or only the artifacts (see
          create_promises / create_artifacts)
        :param create_sbuild_promises: if True, create an `sbuild`
          sub-workflow
        """
        source_artifact = self.playground.create_source_artifact()

        class ExamplePipeline(
            TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Pipeline workflow."""

            def populate(self) -> None:
                """Populate the pipeline."""
                if create_sbuild_promises:
                    sbuild_architectures = [
                        *architectures,
                        *(extra_sbuild_architectures or []),
                    ]
                    sbuild = self.work_request_ensure_child(
                        task_type=TaskTypes.WORKFLOW,
                        task_name="sbuild",
                        task_data=SbuildWorkflowData(
                            input=SbuildInput(
                                source_artifact=source_artifact.id
                            ),
                            target_distribution="debian:bookworm",
                            architectures=sbuild_architectures,
                            signing_template_names={
                                arch: [f"hello-{arch}-signed-template"]
                                for arch in sbuild_architectures
                            },
                        ),
                        workflow_data=WorkRequestWorkflowData(
                            display_name="sbuild", step="sbuild"
                        ),
                    )
                    sbuild.mark_running()
                    orchestrate_workflow(sbuild)

                make_signed_source = self.work_request_ensure_child(
                    task_type=TaskTypes.WORKFLOW,
                    task_name="make_signed_source",
                    task_data=task_data,
                    workflow_data=WorkRequestWorkflowData(
                        display_name="make signed source",
                        step="make_signed_source",
                    ),
                )
                make_signed_source.mark_running()
                orchestrate_workflow(make_signed_source)

        root = self.playground.create_workflow(task_name="examplepipeline")

        root.mark_running()
        orchestrate_workflow(root)

        return root

    def test_compute_dynamic_data(self) -> None:
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello"
            )
        )
        upload_artifact = self.playground.create_upload_artifacts(
            src_name="linux-base"
        ).upload
        signing_artifact = self.create_signing_template_artifact("amd64")

        wr = self.playground.create_workflow(
            task_name="make_signed_source",
            task_data=MakeSignedSourceWorkflowData(
                binary_artifacts=LookupMultiple.parse_obj(
                    [binary_artifact.id, upload_artifact.id]
                ),
                vendor="debian",
                codename="trixie",
                signing_template_artifacts=LookupMultiple.parse_obj(
                    [signing_artifact.id]
                ),
                architectures=["amd64"],
                purpose=KeyPurpose("uefi"),
                key="ABC123",
            ),
        )
        workflow = MakeSignedSourceWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello linux-base"),
        )

    @preserve_task_registry()
    def test_populate(self) -> None:
        """Test populate."""
        # Expected architectures as per intersection of architectures
        # of signing_template_artifacts and binary_artifacts
        architectures = ["amd64", "i386"]

        root = self.orchestrate(
            task_data=MakeSignedSourceWorkflowData(
                architectures=architectures,
                binary_artifacts=LookupMultiple.parse_obj(
                    [
                        f"internal@collections/name:build-{arch}"
                        for arch in (*architectures, "arm64")
                    ]
                ),
                signing_template_artifacts=LookupMultiple.parse_obj(
                    [
                        {
                            "collection": "internal@collections",
                            "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                            "name__startswith": f"signing-template-{arch}-",
                        }
                        for arch in architectures
                    ]
                ),
                vendor="debian",
                codename="bookworm",
                purpose=KeyPurpose.UEFI,
                key="ABC123",
            ),
            architectures=architectures,
            create_sbuild_promises=True,
            extra_sbuild_architectures=("arm64",),
        )

        initial_sbuild_workflow = root.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        )
        make_signed_source = root.children.get(
            task_type=TaskTypes.WORKFLOW, task_name="make_signed_source"
        )

        extract_for_signings = make_signed_source.children.filter(
            task_name="extractforsigning"
        )
        self.assertEqual(extract_for_signings.count(), len(architectures))

        signs = make_signed_source.children.filter(
            task_type=TaskTypes.SIGNING, task_name="sign"
        )
        self.assertEqual(signs.count(), len(architectures))

        assemble_signed_sources = make_signed_source.children.filter(
            task_name="assemblesignedsource"
        )
        self.assertEqual(assemble_signed_sources.count(), len(architectures))

        sbuild_workflows = make_signed_source.children.filter(
            task_type=TaskTypes.WORKFLOW, task_name="sbuild"
        )
        self.assertEqual(sbuild_workflows.count(), len(architectures))

        for arch in architectures:
            template_name = f"hello-{arch}-signed-template"
            extract_for_signing = extract_for_signings.get(
                task_data__input__template_artifact__startswith=(
                    f"internal@collections/name:signing-template-{arch}-"
                )
            )
            self.assertEqual(
                extract_for_signing.task_data,
                {
                    "environment": "debian/match:codename=bookworm",
                    "input": {
                        "binary_artifacts": [
                            f"internal@collections/name:build-{arch}"
                        ],
                        "template_artifact": (
                            f"internal@collections/"
                            f"name:signing-template-{arch}-{template_name}"
                        ),
                    },
                },
            )
            self.assertEqual(
                extract_for_signing.workflow_data_json,
                {
                    "display_name": (
                        f"Extract for signing ({template_name}/{arch})"
                    ),
                    "step": f"extract-for-signing-{arch}-{template_name}",
                },
            )
            self.assertEqual(
                extract_for_signing.event_reactions_json,
                {
                    "on_creation": [],
                    "on_failure": [],
                    "on_success": [
                        {
                            "action": "update-collection-with-artifacts",
                            "artifact_filters": {
                                "category": "debusine:signing-input"
                            },
                            "collection": "internal@collections",
                            "name_template": (
                                "extracted-for-signing-{architecture}-"
                                "{binary_package_name}"
                            ),
                            "variables": {
                                "$binary_package_name": "binary_package_name",
                                "architecture": arch,
                            },
                        }
                    ],
                    "on_unblock": [],
                },
            )

            unsigned = LookupMultiple.parse_obj(
                {
                    "collection": "internal@collections",
                    "child_type": LookupChildType.ARTIFACT,
                    "category": ArtifactCategory.SIGNING_INPUT,
                    "name__startswith": "extracted-for-signing-",
                    "data__architecture": arch,
                }
            )
            sign = signs.get(
                task_data__unsigned=unsigned.dict(exclude_unset=True)[
                    "__root__"
                ]
            )
            self.assertEqual(
                {
                    key: value
                    for key, value in sign.task_data.items()
                    if key != "unsigned"
                },
                {"key": "ABC123", "purpose": "uefi"},
            )
            self.assertEqual(
                sign.workflow_data_json,
                {
                    "display_name": f"Sign ({template_name}/{arch})",
                    "step": f"sign-{arch}-{template_name}",
                },
            )

            self.assertEqual(
                sign.event_reactions_json,
                {
                    "on_creation": [],
                    "on_failure": [],
                    "on_success": [
                        {
                            "action": "update-collection-with-artifacts",
                            "artifact_filters": {
                                "category": "debusine:signing-output"
                            },
                            "collection": "internal@collections",
                            "name_template": (
                                "signed-{architecture}-{template_name}-"
                                "{binary_package_name}"
                            ),
                            "variables": {
                                "$binary_package_name": "binary_package_name",
                                "architecture": arch,
                                "template_name": template_name,
                            },
                        }
                    ],
                    "on_unblock": [],
                },
            )

            signed = LookupMultiple.parse_obj(
                {
                    "collection": "internal@collections",
                    "child_type": LookupChildType.ARTIFACT,
                    "category": ArtifactCategory.SIGNING_OUTPUT,
                    "name__startswith": f"signed-{arch}-{template_name}-",
                    "data__architecture": arch,
                }
            )
            assemble_signed_source = assemble_signed_sources.get(
                task_data__signed=signed.dict(exclude_unset=True)["__root__"]
            )

            self.assertEqual(
                {
                    key: value
                    for key, value in assemble_signed_source.task_data.items()
                    if key != "signed"
                },
                {
                    "environment": "debian/match:codename=bookworm",
                    "template": (
                        f"internal@collections/"
                        f"name:signing-template-{arch}-{template_name}"
                    ),
                },
            )
            self.assertEqual(
                assemble_signed_source.workflow_data_json,
                {
                    "display_name": (
                        f"Assemble signed source ({template_name}/{arch})"
                    ),
                    "step": f"assemble-signed-source-{arch}-{template_name}",
                },
            )
            self.assertEqual(
                assemble_signed_source.event_reactions_json,
                {
                    "on_creation": [],
                    "on_failure": [],
                    "on_success": [
                        {
                            "action": "update-collection-with-artifacts",
                            "artifact_filters": {"category": "debian:upload"},
                            "collection": "internal@collections",
                            "name_template": (
                                f"signed-source-{arch}-{template_name}"
                            ),
                            "variables": None,
                        }
                    ],
                    "on_unblock": [],
                },
            )

            sbuild_workflow = sbuild_workflows.get(
                task_data__input__source_artifact=(
                    f"internal@collections/"
                    f"name:signed-source-{arch}-{template_name}"
                )
            )
            self.assertEqual(
                sbuild_workflow.status, WorkRequest.Statuses.BLOCKED
            )

            self.assertEqual(
                sbuild_workflow.task_data,
                {
                    "prefix": f"signed-source-{arch}-{template_name}|",
                    "architectures": ["all", "amd64", "i386"],
                    "backend": "auto",
                    "input": {
                        "source_artifact": (
                            f"internal@collections/"
                            f"name:signed-source-{arch}-{template_name}"
                        ),
                        "extra_binary_artifacts": [
                            f"internal@collections/name:build-{arch}"
                        ],
                    },
                    "target_distribution": "debian:bookworm",
                },
            )
            self.assertEqual(
                sbuild_workflow.workflow_data_json,
                {
                    "display_name": f"Sbuild ({template_name}/{arch})",
                    "step": f"sbuild-{arch}-{template_name}",
                },
            )
            self.assertEqual(sbuild_workflow.event_reactions_json, {})

            # Completing each work request adds items to the internal
            # collection that can be used by the next work request.
            initial_sbuild = initial_sbuild_workflow.children.get(
                task_data__host_architecture=arch
            )
            initial_sbuild.mark_completed(WorkRequest.Results.SUCCESS)
            signing_input, _ = self.playground.create_artifact(
                category=ArtifactCategory.SIGNING_INPUT,
                data=DebusineSigningInput(binary_package_name="hello"),
                workspace=extract_for_signing.workspace,
                work_request=extract_for_signing,
            )
            extract_for_signing.refresh_from_db()
            extract_for_signing.mark_completed(WorkRequest.Results.SUCCESS)
            self.assertEqual(
                [
                    result.artifact
                    for result in lookup_multiple(
                        unsigned,
                        sign.workspace,
                        user=sign.created_by,
                        workflow_root=root,
                        expect_type=LookupChildType.ARTIFACT,
                    )
                ],
                [signing_input],
            )

            signing_output, _ = self.playground.create_artifact(
                category=ArtifactCategory.SIGNING_OUTPUT,
                data=DebusineSigningOutput(
                    purpose=KeyPurpose.UEFI, fingerprint="ABC123", results=[]
                ),
                workspace=sign.workspace,
                work_request=sign,
            )
            sign.refresh_from_db()
            sign.mark_completed(WorkRequest.Results.SUCCESS)
            self.assertEqual(
                [
                    result.artifact
                    for result in lookup_multiple(
                        LookupMultiple.parse_obj(
                            assemble_signed_source.task_data["signed"]
                        ),
                        assemble_signed_source.workspace,
                        user=assemble_signed_source.created_by,
                        workflow_root=root,
                        expect_type=LookupChildType.ARTIFACT,
                    )
                ],
                [signing_output],
            )

            upload_artifacts = self.playground.create_upload_artifacts(
                src_name=f"hello-{arch}-signed",
                version="1.0.0",
                source=True,
                binary=False,
                workspace=assemble_signed_source.workspace,
                work_request=assemble_signed_source,
            )
            assemble_signed_source.refresh_from_db()
            assemble_signed_source.mark_completed(WorkRequest.Results.SUCCESS)
            self.assertEqual(
                lookup_single(
                    sbuild_workflow.task_data["input"]["source_artifact"],
                    sbuild_workflow.workspace,
                    user=sbuild_workflow.created_by,
                    workflow_root=root,
                    expect_type=LookupChildType.ARTIFACT,
                ).artifact,
                upload_artifacts.upload,
            )

            # The sbuild sub-workflow is now marked pending, and will be
            # picked up by the scheduler.
            sbuild_workflow.refresh_from_db()
            self.assertEqual(
                sbuild_workflow.status, WorkRequest.Statuses.PENDING
            )

            # The scheduler would run on commit, but run it explicitly now
            # so that we can test what it does.
            with self.captureOnCommitCallbacks() as on_commit:
                self.assertEqual(schedule(), [root])
            with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
                for callback in on_commit:
                    callback()
            sbuild_workflow.refresh_from_db()
            self.assertEqual(
                sbuild_workflow.status, WorkRequest.Statuses.RUNNING
            )

    @preserve_task_registry()
    def test_populate_with_artifacts(self) -> None:
        """
        Test populate: only children for specified architectures are generated.

        task_data["architectures"] specifies only one architecture.
        """
        with context.disable_permission_checks():
            architectures = ["amd64"]

            binary_artifact = (
                self.playground.create_minimal_binary_package_artifact(
                    "hello", "1.0.0", "hello", "1.0.0", "amd64"
                )
            )
            upload_artifact = self.playground.create_upload_artifacts(
                src_name="coreutils", version="9.1"
            ).upload
            signing_artifact = self.create_signing_template_artifact("amd64")

            self.playground.create_minimal_binary_package_artifact(
                "hello2", "1.0.0", "hello2", "1.0.0", "i386"
            )
            self.create_signing_template_artifact("i386")

        root = self.orchestrate(
            task_data=MakeSignedSourceWorkflowData(
                binary_artifacts=LookupMultiple.parse_obj(
                    [binary_artifact.id, upload_artifact.id]
                ),
                signing_template_artifacts=LookupMultiple.parse_obj(
                    [signing_artifact.id]
                ),
                vendor="debian",
                codename="bookworm",
                purpose=KeyPurpose.UEFI,
                key="ABC123",
                architectures=architectures,
            ),
            architectures=architectures,
            create_sbuild_promises=False,
        )

        make_signed_source = WorkRequest.objects.get(
            task_type=TaskTypes.WORKFLOW,
            task_name="make_signed_source",
            parent=root,
        )

        extract_for_signings = make_signed_source.children.filter(
            task_name="extractforsigning"
        )
        self.assertEqual(extract_for_signings.count(), len(architectures))

    def test_get_label(self) -> None:
        """Test get_label()."""
        w = self.create_make_signed_source_workflow(extra_task_data={})
        self.assertEqual(w.get_label(), "run sign source")
