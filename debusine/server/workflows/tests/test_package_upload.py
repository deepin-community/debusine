# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the package upload workflow."""

from collections.abc import Sequence
from typing import Any
from unittest.mock import call, patch

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianUpload,
    DebusinePromise,
    TaskTypes,
)
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Artifact,
    CollectionItem,
    TaskDatabase,
    WorkRequest,
    WorkflowTemplate,
    default_workspace,
)
from debusine.server.tasks.models import PackageUploadTarget
from debusine.server.workflows import (
    PackageUploadWorkflow,
    WorkflowValidationError,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    PackageUploadWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    BaseDynamicTaskData,
    LookupMultiple,
    SbuildData,
    SbuildInput,
)
from debusine.test.django import TestCase


class PackageUploadWorkflowTests(TestCase):
    """Unit tests for :py:class:`PackageUploadWorkflow`."""

    def create_package_upload_workflow(
        self,
        *,
        extra_task_data: dict[str, Any],
        validate: bool = True,
    ) -> PackageUploadWorkflow:
        """Create a package upload workflow."""
        task_data = {
            "target": "sftp://upload.example.org/queue/",
            "require_signature": False,
        }
        task_data.update(extra_task_data)
        wr = self.playground.create_workflow(
            task_name="package_upload", task_data=task_data, validate=validate
        )
        return PackageUploadWorkflow(wr)

    def create_source_package(self, *, name: str = "hello") -> Artifact:
        """Create a minimal `debian:source-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": name,
                "version": "1.0",
                "type": "dpkg",
                "dsc_fields": {},
            },
        )
        return artifact

    def create_source_binary_artifacts(
        self, architectures: Sequence[str] | None = None
    ) -> tuple[Artifact, list[Artifact]]:
        """Return one source and a list of binary packages (one per arch)."""
        if architectures is None:
            architectures = ("amd64",)
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )

        source = self.create_source_package()
        binaries = [
            self.playground.create_minimal_binary_packages_artifact(
                "hello", "1.0-1", "1.0-1", architecture
            )
            for architecture in architectures
        ]
        for binary in binaries:
            CollectionItem.objects.create_from_artifact(
                binary,
                parent_collection=collection,
                name=f"binary-{binary.data['architecture']}",
                created_by_user=self.playground.get_default_user(),
                data={},
                created_by_workflow=None,
            )

        return source, binaries

    def test_validate_input_source_binary_artifacts_not_set(self) -> None:
        """Invalid: source_artifact and binaries_artifacts not set."""
        w = self.create_package_upload_workflow(
            extra_task_data={}, validate=False
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            '"source_artifact" or "binary_artifacts" must be set',
        ):
            w.validate_input()

    def test_validate_input_source_package_vendor_codename_not_set(
        self,
    ) -> None:
        """Raise WorkflowValidationError: vendor and codename required."""
        source = self.create_source_package()

        w = self.create_package_upload_workflow(
            extra_task_data={"source_artifact": source.id},
            validate=False,
        )

        with self.assertRaisesRegex(
            WorkflowValidationError,
            '"vendor" and "codename" are required when '
            'source artifact category is debian:source-package',
        ):
            w.validate_input()

    def test_create_orchestrator(self) -> None:
        """A PackageUploadWorkflow can be instantiated."""
        source_artifact = self.create_source_package().id
        binary_artifacts = ["internal@collections/name:build-arm64"]
        target_distribution = "debian:bookworm"

        w = self.create_package_upload_workflow(
            extra_task_data={
                "source_artifact": source_artifact,
                "binary_artifacts": binary_artifacts,
                "target_distribution": target_distribution,
                "vendor": "debian",
                "codename": "bookworm",
            }
        )

        self.assertEqual(w.data.source_artifact, source_artifact)
        self.assertEqual(
            w.data.binary_artifacts, LookupMultiple.parse_obj(binary_artifacts)
        )

        self.assertEqual(w.data.target_distribution, target_distribution)

    def orchestrate(self, *, data: dict[str, Any]) -> WorkRequest:
        """Create a PackageUpload workflow and call orchestrate_workflow."""
        template = WorkflowTemplate.objects.create(
            name="package_upload",
            workspace=default_workspace(),
            task_name="package_upload",
        )

        wr = self.playground.create_workflow(
            template,
            task_data={**{"target": "sftp://upload.example.org/queue"}, **data},
        )

        wr.mark_running()
        orchestrate_workflow(wr)

        return wr

    def test_populate_makesourcepackageupload(self) -> None:
        """
        Workflow create two children work request.

        Work requests created:
        -makesourcepackageupload
        -package_upload
        """
        source = self.create_source_package()

        with patch.object(
            PackageUploadWorkflow,
            "requires_artifact",
            wraps=PackageUploadWorkflow.requires_artifact,
        ) as requires_artifact:
            wr = self.orchestrate(
                data={
                    "source_artifact": source.id,
                    "target_distribution": "debian:bookworm",
                    "since_version": "1.0",
                    "require_signature": False,
                    "vendor": "debian",
                    "codename": "bullseye",
                }
            )

        self.assertEqual(wr.children.count(), 2)

        [make_source] = wr.children.filter(task_name="makesourcepackageupload")

        self.assertEqual(
            make_source.workflow_data_json,
            {
                "display_name": "Make .changes file",
                "step": "source-package-upload",
            },
        )

        self.assertEqual(
            make_source.task_data,
            {
                "environment": "debian/match:codename=bullseye",
                "host_architecture": "amd64",
                "input": {"source_artifact": source.id},
                "since_version": "1.0",
                "target_distribution": "debian:bookworm",
            },
        )

        self.assertEqual(make_source.parent, wr)

        self.assert_work_request_event_reactions(
            make_source,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": "debian:upload"},
                    "collection": "internal@collections",
                    "created_at": None,
                    "name_template": "package-upload-source",
                    "variables": None,
                }
            ],
        )

        # Assert that self.requires_artifact() was called.
        # It cannot test the dependencies because source_artifact was
        # a real artifact and not a promise (so no dependencies were added)
        self.assertIn(
            call(make_source, source.id), requires_artifact.mock_calls
        )

        [package_upload] = wr.children.filter(task_name="packageupload")
        self.assertEqual(package_upload.task_type, TaskTypes.SERVER)
        self.assertEqual(
            package_upload.workflow_data_json,
            {
                "display_name": (
                    "Package upload internal@collections/"
                    "name:package-upload-source"
                ),
                "step": (
                    "package-upload-internal@collections/"
                    "name:package-upload-source"
                ),
            },
        )
        self.assertEqual(
            package_upload.task_data,
            {
                "input": {
                    "upload": "internal@collections/name:package-upload-source"
                },
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )

        self.assertQuerySetEqual(
            package_upload.dependencies.all(),
            list(
                WorkRequest.objects.filter(task_name="makesourcepackageupload")
            ),
        )

    def test_populate_source_is_package_upload(self) -> None:
        """Category source package is UPLOAD: skip makesourcepackageupload."""
        source = self.playground.create_upload_artifacts().upload

        wr = self.orchestrate(
            data={
                "source_artifact": source.id,
                "target_distribution": "debian:bookworm",
                "since_version": "1.0",
                "require_signature": False,
            }
        )

        self.assertEqual(wr.children.count(), 1)

        [package_upload] = wr.children.filter(task_name="packageupload")
        self.assertEqual(
            package_upload.workflow_data_json,
            {
                "display_name": f"Package upload {source.id}",
                "step": f"package-upload-{source.id}",
            },
        )
        self.assertEqual(
            package_upload.task_data,
            {
                "input": {"upload": source.id},
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )

    def test_populate_package_uploads_binaries_only(self) -> None:
        """The workflow create children package uploads: binaries only."""
        _, [binary] = self.create_source_binary_artifacts()

        wr = self.orchestrate(
            data={
                "binary_artifacts": [binary.id],
                "target_distribution": "debian:bookworm",
                "require_signature": False,
            },
        )

        self.assertEqual(
            wr.children.filter(task_name="packageupload").count(), 1
        )

        # Other properties of the created package is tested on different
        # unit tests

    def test_populate_merge_uploads(self) -> None:
        """Test mergeuploads task is created (merge_uploads=True)."""
        parent = self.playground.create_workflow()
        assert parent.internal_collection is not None

        architectures = ("amd64", "i386")
        source = self.create_source_package()
        for architecture in architectures:
            sbuild = parent.create_child(
                task_name="sbuild",
                task_data=SbuildData(
                    input=SbuildInput(source_artifact=source.id),
                    host_architecture=architecture,
                    environment="debian/match:codename=bookworm",
                ),
                workflow_data=WorkRequestWorkflowData(
                    display_name=architecture, step=architecture
                ),
            )
            self.playground.create_bare_data_item(
                parent.internal_collection,
                f"build-{architecture}",
                category=BareDataCategory.PROMISE,
                data=DebusinePromise(
                    promise_work_request_id=sbuild.id,
                    promise_workflow_id=parent.id,
                    promise_category=ArtifactCategory.UPLOAD,
                ),
            )

        wr = self.playground.create_workflow(
            task_name="package_upload",
            task_data={
                "source_artifact": source.id,
                "binary_artifacts": {
                    "collection": "internal@collections",
                    "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                    "name__startswith": "build-",
                },
                "require_signature": False,
                "target": "sftp://upload.example.org/queue",
                "target_distribution": "debian/match:codename=bookworm",
                "merge_uploads": True,
                "vendor": "debian",
                "codename": "bullseye",
            },
            parent=parent,
        )
        wr.mark_running()
        orchestrate_workflow(wr)

        self.assertEqual(
            wr.children.filter(task_name="mergeuploads").count(), 1
        )

        [merge_uploads] = wr.children.filter(task_name="mergeuploads")

        self.assertEqual(
            merge_uploads.task_data,
            {
                "input": {
                    "uploads": [
                        "internal@collections/name:package-upload-source"
                    ]
                    + [
                        f"internal@collections/name:build-{architecture}"
                        for architecture in architectures
                    ]
                },
            },
        )

        self.assertQuerySetEqual(
            merge_uploads.dependencies.all(),
            list(
                WorkRequest.objects.filter(
                    task_name__in={"makesourcepackageupload", "sbuild"}
                )
            ),
            ordered=False,
        )

        self.assertEqual(
            wr.children.filter(task_name="packageupload").count(), 1
        )
        [package_upload] = wr.children.filter(task_name="packageupload")
        self.assertEqual(
            package_upload.task_data,
            {
                "input": {
                    "upload": (
                        f"internal@collections/name:"
                        f"package-upload-merged-{wr.id}"
                    )
                },
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )

        # Replace one of the promises with an artifact and run the workflow
        # orchestrator again.  The result is stable: no new MergeUploads
        # work request is created.
        binary, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD,
            data=DebianUpload(
                type="dpkg",
                changes_fields={
                    "Architecture": architectures[0],
                    "Files": [{"name": f"hello_1.0_{architectures[0]}.deb"}],
                },
            ),
        )
        parent.internal_collection.manager.add_artifact(
            binary,
            user=self.playground.get_default_user(),
            workflow=parent,
            name=f"build-{architectures[0]}",
            replace=True,
        )
        orchestrate_workflow(wr)

        self.assertEqual(
            wr.children.filter(task_name="mergeuploads").count(), 1
        )

    def test_upload_debsign(self) -> None:
        """Upload signed artifacts."""
        source, [binary] = self.create_source_binary_artifacts()

        wr = self.orchestrate(
            data={
                "source_artifact": source.id,
                "binary_artifacts": [binary.id],
                "require_signature": False,
                "target_distribution": "debian/match:codename=bookworm",
                "merge_uploads": True,
                "key": "ABC123",
                "vendor": "debian",
                "codename": "bookworm",
            },
        )

        [signer] = wr.children.filter(
            task_type=TaskTypes.SIGNING, task_name="debsign"
        )
        self.assertEqual(
            signer.task_data,
            {
                "key": "ABC123",
                "unsigned": (
                    f"internal@collections/name:package-upload-merged-{wr.id}"
                ),
            },
        )
        identifier = f"internal@collections/name:package-upload-merged-{wr.id}"
        self.assertEqual(
            signer.workflow_data_json,
            {
                "display_name": f"Sign upload for {identifier}",
                "step": f"debsign-{identifier}",
            },
        )

        self.assertQuerySetEqual(
            signer.dependencies.all(),
            list(
                WorkRequest.objects.filter(
                    task_type=TaskTypes.WORKER, task_name="mergeuploads"
                )
            ),
        )
        signed_name = (
            f"package-upload-signed-"
            f"internal_collections_name_package-upload-merged-{wr.id}"
        )
        self.assert_work_request_event_reactions(
            signer,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": "debian:upload"},
                    "collection": "internal@collections",
                    "created_at": None,
                    "name_template": signed_name,
                    "variables": None,
                }
            ],
        )

        [uploader] = wr.children.filter(task_name="packageupload")
        self.assertEqual(
            uploader.task_data,
            {
                "input": {"upload": f"internal@collections/name:{signed_name}"},
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )

        self.assertEqual(
            uploader.workflow_data_json,
            {
                "display_name": (
                    f"Package upload "
                    f"internal@collections/name:package-upload-merged-{wr.id}"
                ),
                "step": (
                    f"package-upload-internal@collections/"
                    f"name:package-upload-merged-{wr.id}"
                ),
            },
        )

    def test_upload_externaldebsign(self) -> None:
        """Upload external signed artifacts."""
        source, [binary] = self.create_source_binary_artifacts()

        wr = self.orchestrate(
            data={
                "source_artifact": source.id,
                "binary_artifacts": [binary.id],
                "target_distribution": "debian/match:codename=bookworm",
                "merge_uploads": True,
                "require_signature": True,
                "vendor": "debian",
                "codename": "bookworm",
            },
        )

        [signer] = wr.children.filter(
            task_type=TaskTypes.WAIT, task_name="externaldebsign"
        )
        self.assertEqual(
            signer.task_data,
            {
                "unsigned": (
                    f"internal@collections/name:package-upload-merged-{wr.id}"
                )
            },
        )
        identifier = f"internal@collections/name:package-upload-merged-{wr.id}"
        self.assertEqual(
            signer.workflow_data_json,
            {
                "display_name": (
                    f"Wait for signature on upload for {identifier}"
                ),
                "step": f"external-debsign-{identifier}",
                "needs_input": True,
            },
        )

        self.assertQuerySetEqual(
            signer.dependencies.all(),
            list(
                WorkRequest.objects.filter(
                    task_type=TaskTypes.WORKER, task_name="mergeuploads"
                )
            ),
        )
        signed_name = (
            f"package-upload-signed-"
            f"internal_collections_name_package-upload-merged-{wr.id}"
        )
        self.assert_work_request_event_reactions(
            signer,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": "debian:upload"},
                    "collection": "internal@collections",
                    "created_at": None,
                    "name_template": signed_name,
                    "variables": None,
                }
            ],
        )

        [uploader] = wr.children.filter(task_name="packageupload")
        self.assertEqual(
            uploader.task_data,
            {
                "input": {"upload": f"internal@collections/name:{signed_name}"},
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )

    def test_upload_split_signing(self) -> None:
        """Upload artifacts: developer signs source, Debusine signs binary."""
        source, [binary] = self.create_source_binary_artifacts()

        wr = self.orchestrate(
            data={
                "source_artifact": source.id,
                "binary_artifacts": [binary.id],
                "require_signature": True,
                "target_distribution": "debian/match:codename=bookworm",
                "binary_key": "ABC123",
                "vendor": "debian",
                "codename": "bookworm",
            },
        )

        [source_signer] = wr.children.filter(
            task_type=TaskTypes.WAIT, task_name="externaldebsign"
        )
        source_lookup = "internal@collections/name:package-upload-source"
        self.assertEqual(source_signer.task_data, {"unsigned": source_lookup})
        self.assertEqual(
            source_signer.workflow_data_json,
            {
                "display_name": (
                    f"Wait for signature on upload for {source_lookup}"
                ),
                "step": f"external-debsign-{source_lookup}",
                "needs_input": True,
            },
        )
        self.assertQuerySetEqual(
            source_signer.dependencies.all(),
            list(
                WorkRequest.objects.filter(
                    task_type=TaskTypes.WORKER,
                    task_name="makesourcepackageupload",
                )
            ),
        )
        source_signed_name = (
            "package-upload-signed-"
            "internal_collections_name_package-upload-source"
        )
        self.assert_work_request_event_reactions(
            source_signer,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": "debian:upload"},
                    "collection": "internal@collections",
                    "created_at": None,
                    "name_template": source_signed_name,
                    "variables": None,
                }
            ],
        )

        [binary_signer] = wr.children.filter(
            task_type=TaskTypes.SIGNING, task_name="debsign"
        )
        binary_lookup = f"{binary.id}@artifacts"
        self.assertEqual(
            binary_signer.task_data,
            {"key": "ABC123", "unsigned": binary_lookup},
        )
        self.assertEqual(
            binary_signer.workflow_data_json,
            {
                "display_name": f"Sign upload for {binary_lookup}",
                "step": f"debsign-{binary_lookup}",
            },
        )
        self.assertQuerySetEqual(
            binary_signer.dependencies.all(), [source_signer]
        )
        binary_signed_name = f"package-upload-signed-{binary.id}_artifacts"
        self.assert_work_request_event_reactions(
            binary_signer,
            on_success=[
                {
                    "action": "update-collection-with-artifacts",
                    "artifact_filters": {"category": "debian:upload"},
                    "collection": "internal@collections",
                    "created_at": None,
                    "name_template": binary_signed_name,
                    "variables": None,
                }
            ],
        )

        [source_uploader, binary_uploader] = wr.children.filter(
            task_name="packageupload"
        ).order_by("id")
        self.assertEqual(
            source_uploader.task_data,
            {
                "input": {
                    "upload": f"internal@collections/name:{source_signed_name}"
                },
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )
        self.assertEqual(
            source_uploader.workflow_data_json,
            {
                "display_name": f"Package upload {source_lookup}",
                "step": f"package-upload-{source_lookup}",
            },
        )
        self.assertQuerySetEqual(
            source_uploader.dependencies.all(), [source_signer]
        )
        self.assertEqual(
            binary_uploader.task_data,
            {
                "input": {
                    "upload": f"internal@collections/name:{binary_signed_name}"
                },
                "target": "sftp://upload.example.org/queue",
                "delayed_days": None,
            },
        )
        self.assertEqual(
            binary_uploader.workflow_data_json,
            {
                "display_name": f"Package upload {binary_lookup}",
                "step": f"package-upload-{binary_lookup}",
            },
        )
        self.assertQuerySetEqual(
            binary_uploader.dependencies.all(),
            [binary_signer, source_uploader],
            ordered=False,
        )

    def test_upload_delayed(self) -> None:
        """Upload to a delayed queue."""
        source = self.create_source_package()

        wr = self.orchestrate(
            data={
                "source_artifact": source.id,
                "delayed_days": 3,
                "vendor": "debian",
                "codename": "bookworm",
            }
        )

        signed_name = (
            "package-upload-signed-"
            "internal_collections_name_package-upload-source"
        )
        uploader = wr.children.filter(task_name="packageupload").get()
        self.assertEqual(
            uploader.task_data,
            {
                "input": {"upload": f"internal@collections/name:{signed_name}"},
                "target": "sftp://upload.example.org/queue",
                "delayed_days": 3,
            },
        )

    def test_orchestrate_idempotent(self) -> None:
        """Calling orchestrate twice does not create new work requests."""
        source, binaries = self.create_source_binary_artifacts(
            architectures=("amd64", "i386")
        )
        binaries_collection = binaries[0].parent_collections.earliest("id")

        # Force a predictable default ordering.
        with patch(
            "debusine.db.models.collections.CollectionItem._meta.ordering",
            ["id"],
        ):
            wr = self.orchestrate(
                data={
                    "source_artifact": source.id,
                    "binary_artifacts": {
                        "collection": binaries_collection.id,
                        "name__startswith": "binary-",
                    },
                    "vendor": "debian",
                    "codename": "bookworm",
                    "merge_uploads": True,
                },
            )

        children = set(wr.children.all())

        # Force a different predictable default ordering.
        with patch(
            "debusine.db.models.collections.CollectionItem._meta.ordering",
            ["-id"],
        ):
            PackageUploadWorkflow(wr).populate()

        self.assertQuerySetEqual(wr.children.all(), children, ordered=False)

    def test_compute_dynamic_data_source_artifact(self) -> None:
        source_artifact = self.create_source_package(name="hello")

        wr = self.playground.create_workflow(
            task_name="package_upload",
            task_data=PackageUploadWorkflowData(
                source_artifact=source_artifact.id,
                target=pydantic.parse_obj_as(
                    PackageUploadTarget,
                    "sftp://example.org",
                ),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = PackageUploadWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello"),
        )

    def test_compute_dynamic_data_binary_artifacts(self) -> None:
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello"
            )
        )

        wr = self.playground.create_workflow(
            task_name="package_upload",
            task_data=PackageUploadWorkflowData(
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                source_artifact=None,
                target=pydantic.parse_obj_as(
                    PackageUploadTarget,
                    "sftp://example.org",
                ),
                vendor="debian",
                codename="trixie",
            ),
        )
        workflow = PackageUploadWorkflow(wr)

        self.assertEqual(
            workflow.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="hello"),
        )

    def test_get_label(self) -> None:
        """Test get_label()."""
        source = self.create_source_package()
        w = self.create_package_upload_workflow(
            extra_task_data={
                "source_artifact": source.id,
                "target_distribution": "debian:bookworm",
                "vendor": "debian",
                "codename": "bookworm",
            }
        )
        self.assertEqual(w.get_label(), "run package uploads")
