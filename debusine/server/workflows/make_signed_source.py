# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Make signed source workflow."""

from functools import cached_property

from debusine.artifacts.models import ArtifactCategory, TaskTypes
from debusine.assets import KeyPurpose
from debusine.client.models import LookupChildType
from debusine.db.models import WorkRequest
from debusine.server.collections.lookup import (
    lookup_multiple,
    reconstruct_lookup,
)
from debusine.server.workflows import Workflow, workflow_utils
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    MakeSignedSourceWorkflowData,
    SbuildWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.signing.tasks.models import SignData
from debusine.tasks.models import (
    ActionUpdateCollectionWithArtifacts,
    AssembleSignedSourceData,
    BackendType,
    BaseDynamicTaskData,
    ExtractForSigningData,
    ExtractForSigningInput,
    LookupDict,
    LookupMultiple,
    LookupSingle,
    SbuildInput,
)
from debusine.tasks.server import TaskDatabaseInterface


class MakeSignedSourceWorkflow(
    Workflow[MakeSignedSourceWorkflowData, BaseDynamicTaskData]
):
    """Make signed sources."""

    TASK_NAME = "make_signed_source"

    def __init__(self, work_request: WorkRequest):
        """Instantiate a Workflow with its database instance."""
        super().__init__(work_request)

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data.

        :subject: source package names separated by spaces
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
                ArtifactCategory.UPLOAD,
            ),
        )
        return BaseDynamicTaskData(subject=" ".join(source_package_names))

    @cached_property
    def architectures(self) -> set[str]:
        """Architectures to run on."""
        return (
            workflow_utils.get_architectures(
                self, self.data.signing_template_artifacts
            )
            .intersection(
                workflow_utils.get_architectures(
                    self, self.data.binary_artifacts
                )
            )
            .intersection(self.data.architectures)
        )

    def populate(self) -> None:
        """Create work requests and sub-workflows."""
        workflow_root = self.work_request.get_workflow_root()
        environment = f"{self.data.vendor}/match:codename={self.data.codename}"

        signing_templates = lookup_multiple(
            self.data.signing_template_artifacts,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=workflow_root,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        )

        for arch in self.architectures:
            signing_template_lookups_by_name = {
                workflow_utils.lookup_result_binary_package_name(
                    result
                ): reconstruct_lookup(result, workflow_root=workflow_root)
                for result in signing_templates
                if workflow_utils.lookup_result_architecture(result) == arch
            }

            binary_artifacts_lookups = (
                workflow_utils.filter_artifact_lookup_by_arch(
                    self,
                    self.data.binary_artifacts,
                    (arch, "all"),
                )
            )

            for signing_template_name, signing_template_lookup in sorted(
                signing_template_lookups_by_name.items()
            ):
                # filter_artifact_lookup_by_arch always returns a parsed list of
                # single lookups.
                assert not isinstance(signing_template_lookup, LookupDict)

                extract_for_signing_wr, extracted_lookup = (
                    self._populate_extract_for_signing(
                        template_artifact=signing_template_lookup,
                        binary_artifacts=binary_artifacts_lookups,
                        environment=environment,
                        architecture=arch,
                        template_name=signing_template_name,
                    )
                )

                sign_wr, signed = self._populate_sign(
                    purpose=self.data.purpose,
                    unsigned=extracted_lookup,
                    key=self.data.key,
                    extract_for_signing_wr=extract_for_signing_wr,
                    architecture=arch,
                    template_name=signing_template_name,
                )

                assembled = self._populate_assemble_signed_source(
                    environment=environment,
                    template=signing_template_lookup,
                    signed=signed,
                    sign_wr=sign_wr,
                    architecture=arch,
                    template_name=signing_template_name,
                )

                target_distribution = f"{self.data.vendor}:{self.data.codename}"

                sbuild_architectures = sorted(self.architectures | {"all"})
                self._populate_sbuild_workflow(
                    assembled=assembled,
                    extra_binary_artifacts=binary_artifacts_lookups,
                    target_distribution=target_distribution,
                    backend=self.data.sbuild_backend,
                    architectures=sbuild_architectures,
                    architecture=arch,
                    template_name=signing_template_name,
                )

    def _populate_extract_for_signing(
        self,
        *,
        template_artifact: LookupSingle,
        binary_artifacts: LookupMultiple,
        environment: str,
        architecture: str,
        template_name: str,
    ) -> tuple[WorkRequest, LookupMultiple]:
        """
        Create work request for ExtractForSigning.

        :returns: A tuple of the ExtractForSigning work request and a lookup
          of the extracted artifacts in the collection.
        """
        wr = self.work_request_ensure_child(
            task_name="extractforsigning",
            task_data=ExtractForSigningData(
                environment=environment,
                input=ExtractForSigningInput(
                    template_artifact=template_artifact,
                    binary_artifacts=binary_artifacts,
                ),
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=(
                    f"Extract for signing ({template_name}/{architecture})"
                ),
                step=f"extract-for-signing-{architecture}-{template_name}",
            ),
        )
        self.requires_artifact(wr, template_artifact)
        self.requires_artifact(wr, binary_artifacts)

        wr.add_event_reaction(
            "on_success",
            ActionUpdateCollectionWithArtifacts(
                collection="internal@collections",
                name_template=(
                    "extracted-for-signing-{architecture}-{binary_package_name}"
                ),
                variables={
                    # Extract the binary package name from the artifact
                    # data, since the workflow doesn't know it up-front.
                    "$binary_package_name": "binary_package_name",
                    "architecture": architecture,
                },
                artifact_filters={"category": ArtifactCategory.SIGNING_INPUT},
            ),
        )
        lookup = LookupMultiple.parse_obj(
            {
                "collection": "internal@collections",
                "child_type": LookupChildType.ARTIFACT,
                "category": ArtifactCategory.SIGNING_INPUT,
                "name__startswith": "extracted-for-signing-",
                "data__architecture": architecture,
            }
        )
        return wr, lookup

    def _populate_sign(
        self,
        *,
        purpose: KeyPurpose,
        unsigned: LookupMultiple,
        key: str,
        extract_for_signing_wr: WorkRequest,
        architecture: str,
        template_name: str,
    ) -> tuple[WorkRequest, LookupMultiple]:
        """
        Create work request for Sign.

        :returns: A tuple of the Sign work request and a lookup of the
          signed artifacts in the collection.
        """
        wr = self.work_request_ensure_child(
            task_type=TaskTypes.SIGNING,
            task_name="sign",
            task_data=SignData(purpose=purpose, unsigned=unsigned, key=key),
            workflow_data=WorkRequestWorkflowData(
                display_name=f"Sign ({template_name}/{architecture})",
                step=f"sign-{architecture}-{template_name}",
            ),
        )

        wr.add_dependency(extract_for_signing_wr)

        wr.add_event_reaction(
            "on_success",
            ActionUpdateCollectionWithArtifacts(
                collection="internal@collections",
                name_template=(
                    "signed-{architecture}-{template_name}-"
                    "{binary_package_name}"
                ),
                variables={
                    # Extract the binary package name from the artifact
                    # data, since the workflow doesn't know it up-front.
                    "$binary_package_name": "binary_package_name",
                    "architecture": architecture,
                    "template_name": template_name,
                },
                artifact_filters={"category": ArtifactCategory.SIGNING_OUTPUT},
            ),
        )
        lookup = LookupMultiple.parse_obj(
            {
                "collection": "internal@collections",
                "child_type": LookupChildType.ARTIFACT,
                "category": ArtifactCategory.SIGNING_OUTPUT,
                "name__startswith": f"signed-{architecture}-{template_name}-",
                "data__architecture": architecture,
            }
        )
        return wr, lookup

    def _populate_assemble_signed_source(
        self,
        *,
        environment: str,
        template: LookupSingle,
        signed: LookupMultiple,
        sign_wr: WorkRequest,
        architecture: str,
        template_name: str,
    ) -> LookupSingle:
        """
        Create work request for assembling the signed source.

        :returns: Lookup of the assembled signed artifact
        """
        wr = self.work_request_ensure_child(
            task_name="assemblesignedsource",
            task_data=AssembleSignedSourceData(
                environment=environment, template=template, signed=signed
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=(
                    f"Assemble signed source ({template_name}/{architecture})"
                ),
                step=f"assemble-signed-source-{architecture}-{template_name}",
            ),
        )

        wr.add_dependency(sign_wr)

        artifact_name = f"signed-source-{architecture}-{template_name}"
        self.provides_artifact(wr, ArtifactCategory.UPLOAD, artifact_name)
        return f"internal@collections/name:{artifact_name}"

    def _populate_sbuild_workflow(
        self,
        assembled: LookupSingle,
        extra_binary_artifacts: LookupMultiple,
        target_distribution: str,
        backend: BackendType,
        architectures: list[str],
        architecture: str,
        template_name: str,
    ) -> None:
        """Populate SbuildWorkflow."""
        wr = self.work_request_ensure_child(
            task_type=TaskTypes.WORKFLOW,
            task_name="sbuild",
            task_data=SbuildWorkflowData(
                prefix=f"signed-source-{architecture}-{template_name}|",
                input=SbuildInput(
                    source_artifact=assembled,
                    extra_binary_artifacts=extra_binary_artifacts,
                ),
                target_distribution=target_distribution,
                backend=backend,
                architectures=architectures,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=f"Sbuild ({template_name}/{architecture})",
                step=f"sbuild-{architecture}-{template_name}",
            ),
        )

        self.requires_artifact(wr, assembled)

        if wr.status == WorkRequest.Statuses.PENDING:
            wr.mark_running()
            orchestrate_workflow(wr)

    def get_label(self) -> str:
        """Return the task label."""
        return "run sign source"
