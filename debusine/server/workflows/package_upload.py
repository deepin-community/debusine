# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Package upload workflow."""

from functools import cached_property

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

from debusine.artifacts.models import ArtifactCategory, TaskTypes
from debusine.client.models import LookupChildType
from debusine.db.models import WorkRequest
from debusine.server.collections.lookup import (
    lookup_multiple,
    lookup_single,
    reconstruct_lookup,
)
from debusine.server.tasks.models import (
    PackageUploadData,
    PackageUploadInput,
    PackageUploadTarget,
)
from debusine.server.tasks.wait.models import ExternalDebsignData
from debusine.server.workflows import (
    Workflow,
    WorkflowValidationError,
    workflow_utils,
)
from debusine.server.workflows.models import (
    PackageUploadWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.signing.tasks.models import DebsignData
from debusine.tasks.models import (
    BaseDynamicTaskData,
    LookupMultiple,
    LookupSingle,
    MakeSourcePackageUploadData,
    MakeSourcePackageUploadInput,
    MergeUploadsData,
    MergeUploadsInput,
    empty_lookup_multiple,
)
from debusine.tasks.server import TaskDatabaseInterface


class PackageUploadWorkflow(
    Workflow[PackageUploadWorkflowData, BaseDynamicTaskData]
):
    """Run package uploads for a set of architectures."""

    TASK_NAME = "package_upload"

    def __init__(self, work_request: "WorkRequest"):
        """Instantiate a Workflow with its database instance."""
        super().__init__(work_request)

        # list of (lookup, contains-binaries)
        self._artifacts_pending_upload: list[tuple[LookupSingle, bool]] = []

    @cached_property
    def source_category(self) -> str | None:
        """Category of source artifact or promise."""
        if self.data.source_artifact is None:
            return None

        return workflow_utils.lookup_result_artifact_category(
            lookup_single(
                self.data.source_artifact,
                self.workspace,
                user=self.work_request.created_by,
                workflow_root=self.work_request.get_workflow_root(),
                expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
            )
        )

    def validate_input(self) -> None:
        """Raise WorkflowValidationError if needed."""
        if (
            self.data.source_artifact is None
            and self.data.binary_artifacts == empty_lookup_multiple()
        ):
            raise WorkflowValidationError(
                '"source_artifact" or "binary_artifacts" must be set'
            )

        if self.source_category == ArtifactCategory.SOURCE_PACKAGE and (
            self.data.vendor is None or self.data.codename is None
        ):
            # MakeSourcePackageUpload will be used, but self.data.vendor or
            # self.data.codename are not set.
            raise WorkflowValidationError(
                f'"vendor" and "codename" are required when source '
                f'artifact category is {ArtifactCategory.SOURCE_PACKAGE}'
            )

    def populate(self) -> None:
        """Create package upload work requests."""
        workflow_root = self.work_request.get_workflow_root()
        artifacts_pending_upload: list[tuple[LookupSingle, bool]] = []

        if (source_artifact := self.data.source_artifact) is not None:
            since_version = self.data.since_version
            target_distribution = self.data.target_distribution

            if self.source_category == ArtifactCategory.SOURCE_PACKAGE:
                package_upload_source = (
                    self._populate_make_source_package_upload(
                        source_artifact,
                        since_version=since_version,
                        target_distribution=target_distribution,
                    )
                )
                artifacts_pending_upload.append((package_upload_source, False))

            else:
                artifacts_pending_upload.append((source_artifact, False))

        # Upload binary_artifacts
        results = lookup_multiple(
            self.data.binary_artifacts,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=workflow_root,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        )

        artifacts_pending_upload += sorted(
            (reconstruct_lookup(result, workflow_root=workflow_root), True)
            for result in results
        )

        self._artifacts_pending_upload = artifacts_pending_upload

        self._populate_package_uploads()

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: source package names (separated by spaces) of
          ``binary_artifacts`` and ``source_artifact``
        """
        binaries = lookup_multiple(
            self.data.binary_artifacts,
            workflow_root=self.work_request.get_workflow_root(),
            workspace=self.workspace,
            user=self.work_request.created_by,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        )
        source_package_names = workflow_utils.get_source_package_names(
            binaries,
            configuration_key="binary_artifacts",
            artifact_expected_categories=(
                ArtifactCategory.BINARY_PACKAGE,
                ArtifactCategory.BINARY_PACKAGES,
                ArtifactCategory.UPLOAD,
            ),
        )

        if self.data.source_artifact is not None:
            source_artifact = lookup_single(
                self.data.source_artifact,
                workflow_root=self.work_request.get_workflow_root(),
                workspace=self.workspace,
                user=self.work_request.created_by,
                expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
            )

            source_artifact_names = workflow_utils.get_source_package_names(
                [source_artifact],
                configuration_key="source_artifact",
                artifact_expected_categories=(
                    ArtifactCategory.SOURCE_PACKAGE,
                    ArtifactCategory.UPLOAD,
                ),
            )
            source_package_names = sorted(
                set(source_package_names) | set(source_artifact_names)
            )

        return BaseDynamicTaskData(subject=" ".join(source_package_names))

    def _populate_make_source_package_upload(
        self,
        lookup: LookupSingle,
        *,
        since_version: str | None,
        target_distribution: str | None,
    ) -> LookupSingle:
        """
        Create work request for MakeSourcePackageUpload.

        :returns: Lookup of the signed artifact in the collection
        """
        wr = self.work_request_ensure_child(
            task_name="makesourcepackageupload",
            task_data=MakeSourcePackageUploadData(
                input=MakeSourcePackageUploadInput(source_artifact=lookup),
                target_distribution=target_distribution,
                since_version=since_version,
                environment=(
                    f"{self.data.vendor}/match:"
                    f"codename={self.data.codename}"
                ),
                host_architecture=self.data.arch_all_host_architecture,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="Make .changes file",
                step="source-package-upload",
            ),
        )
        self.requires_artifact(wr, lookup)

        artifact_name = "package-upload-source"
        self.provides_artifact(wr, ArtifactCategory.UPLOAD, artifact_name)
        return f"internal@collections/name:{artifact_name}"

    def _populate_signer(
        self,
        signer_identifier: str,
        unsigned_artifact: LookupSingle,
        key: str | None,
        signed_source_artifact: LookupSingle | None,
    ) -> LookupSingle:
        """
        Create a signer: Debsign or ExternalDebsign depending on self.data.

        :param signer_identifier: identifier for the end user of what is going
          to be signed
        :param unsigned_artifact: unsigned artifact for the signer
        :param key: key to be used by the signer. If None, will use
          ExternalDebsign
        :param signed_source_artifact: if not None, signing this artifact
          requires this other signature to have been completed first
        :returns: lookup of the signed artifact
        """
        if key is not None:
            signer = self.work_request_ensure_child(
                task_type=TaskTypes.SIGNING,
                task_name="debsign",
                task_data=DebsignData(unsigned=unsigned_artifact, key=key),
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Sign upload for {signer_identifier}",
                    step=f"debsign-{signer_identifier}",
                ),
            )

        else:
            signer = self.work_request_ensure_child(
                task_type=TaskTypes.WAIT,
                task_name="externaldebsign",
                task_data=ExternalDebsignData(unsigned=unsigned_artifact),
                workflow_data=WorkRequestWorkflowData(
                    display_name=(
                        "Wait for signature on "
                        f"upload for {signer_identifier}"
                    ),
                    step=f"external-debsign-{signer_identifier}",
                    needs_input=True,
                ),
            )

        self.requires_artifact(signer, unsigned_artifact)
        if signed_source_artifact is not None:
            self.requires_artifact(signer, signed_source_artifact)

        name_signed_artifact = (
            f"package-upload-signed-{unsigned_artifact}".replace(":", "_")
            .replace("@", "_")
            .replace("/", "_")
        )

        self.provides_artifact(
            signer,
            ArtifactCategory.UPLOAD,
            name_signed_artifact,
        )

        return f"internal@collections/name:{name_signed_artifact}"

    def _populate_package_uploads(self) -> None:
        """
        Create work request(s) to upload the artifacts.

        Depending on self.data.merge_uploads will:
        -Create one work request for each self._artifacts_pending_upload
        -Create a single work request of type MergeUpload
        """
        if self.data.merge_uploads:
            wr = self.work_request_ensure_child(
                task_name="mergeuploads",
                task_data=MergeUploadsData(
                    input=MergeUploadsInput(
                        uploads=LookupMultiple.parse_obj(
                            [
                                upload
                                for upload, _ in self._artifacts_pending_upload
                            ]
                        )
                    ),
                ),
                workflow_data=WorkRequestWorkflowData(
                    display_name="Merge uploads",
                    step="merge-uploads",
                ),
            )

            any_binaries = False
            for upload, contains_binaries in self._artifacts_pending_upload:
                self.requires_artifact(wr, upload)
                any_binaries = any_binaries or contains_binaries

            merged_artifact_name = (
                f"package-upload-merged-{self.work_request.id}"
            )
            self.provides_artifact(
                wr, ArtifactCategory.UPLOAD, merged_artifact_name
            )

            self._artifacts_pending_upload = [
                (
                    f"internal@collections/name:{merged_artifact_name}",
                    any_binaries,
                )
            ]

        source_upload: LookupSingle | None = None
        source_uploader: WorkRequest | None = None
        for upload, contains_binaries in self._artifacts_pending_upload:
            identifier = str(upload)

            key = self.data.key
            split_binary_signing = False
            if key is None and (
                self.data.binary_key is not None and contains_binaries
            ):
                key = self.data.binary_key
                split_binary_signing = True
            if key is not None or self.data.require_signature:
                upload = self._populate_signer(
                    identifier,
                    upload,
                    key=key,
                    signed_source_artifact=(
                        source_upload if split_binary_signing else None
                    ),
                )
                if not contains_binaries:
                    # There is at most one source artifact to upload.
                    assert source_upload is None
                    source_upload = upload

            uploader = self.work_request_ensure_child(
                task_name="packageupload",
                task_data=PackageUploadData(
                    input=PackageUploadInput(upload=upload),
                    target=pydantic.parse_obj_as(
                        PackageUploadTarget, self.data.target
                    ),
                    delayed_days=self.data.delayed_days,
                ),
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Package upload {identifier}",
                    step=f"package-upload-{identifier}",
                ),
                task_type=TaskTypes.SERVER,
            )
            if not contains_binaries:
                # There is at most one source artifact to upload.
                assert source_uploader is None
                source_uploader = uploader

            self.requires_artifact(uploader, upload)
            if split_binary_signing and source_uploader is not None:
                uploader.add_dependency(source_uploader)

    def get_label(self) -> str:
        """Return the task label."""
        return "run package uploads"
