# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debian_pipeline workflow."""

from django.contrib.auth.models import AnonymousUser

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

from debusine.assets import KeyPurpose
from debusine.client.models import LookupChildType
from debusine.db.models import WorkRequest
from debusine.server.collections.lookup import lookup_single
from debusine.server.tasks.models import PackageUploadTarget
from debusine.server.workflows import (
    Workflow,
    WorkflowValidationError,
    workflow_utils,
)
from debusine.server.workflows.base import orchestrate_workflow
from debusine.server.workflows.models import (
    DebianPipelineWorkflowData,
    MakeSignedSourceWorkflowData,
    PackageUploadWorkflowData,
    QAWorkflowData,
    SbuildWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    BackendType,
    BaseDynamicTaskData,
    LintianFailOnSeverity,
    LookupMultiple,
    LookupSingle,
    SbuildInput,
    TaskTypes,
)
from debusine.tasks.server import TaskDatabaseInterface


class DebianPipelineWorkflow(
    Workflow[DebianPipelineWorkflowData, BaseDynamicTaskData]
):
    """Debian pipeline workflow."""

    TASK_NAME = "debian_pipeline"

    def validate_input(self) -> None:
        """Thorough validation of input data."""
        if self.data.reverse_dependencies_autopkgtest_suite is not None:
            try:
                lookup_single(
                    self.data.reverse_dependencies_autopkgtest_suite,
                    self.workspace,
                    user=self.work_request.created_by,
                    workflow_root=self.work_request.get_workflow_root(),
                    expect_type=LookupChildType.COLLECTION,
                ).collection
            except LookupError as e:
                raise WorkflowValidationError(str(e)) from e

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: package name of ``source_artifact``
        """
        source_data = workflow_utils.source_package_data(self)
        return BaseDynamicTaskData(
            subject=source_data.name,
            parameter_summary=f"{source_data.name}_{source_data.version}",
        )

    def populate(self) -> None:
        """Create work requests."""
        if (data_archs := self.data.architectures) is not None:
            architectures = set(data_archs)
        else:
            architectures = workflow_utils.get_available_architectures(
                self, vendor=self.data.vendor, codename=self.data.codename
            )

        if (
            data_archs_allowlist := self.data.architectures_allowlist
        ) is not None:
            architectures.intersection_update(data_archs_allowlist)

        if (
            data_archs_denylist := self.data.architectures_denylist
        ) is not None:
            architectures.difference_update(data_archs_denylist)

        target_distribution = f"{self.data.vendor}:{self.data.codename}"

        effective_architectures = sorted(architectures)

        self._populate_sbuild(
            source_artifact=self.data.source_artifact,
            target_distribution=target_distribution,
            backend=self.data.sbuild_backend,
            architectures=effective_architectures,
            environment_variant=self.data.sbuild_environment_variant,
            signing_template_names=self.data.signing_template_names,
        )

        # sbuild workflow promises the build-{arch} artifacts
        binary_artifacts = LookupMultiple.parse_obj(
            {
                "collection": "internal@collections",
                "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                "name__startswith": "build-",
            }
        )

        to_upload = [(self.data.source_artifact, binary_artifacts, False)]

        if (
            self.data.enable_autopkgtest
            or self.data.enable_reverse_dependencies_autopkgtest
            or self.data.enable_lintian
            or self.data.enable_piuparts
        ):
            self._populate_qa(
                source_artifact=self.data.source_artifact,
                binary_artifacts=binary_artifacts,
                vendor=self.data.vendor,
                codename=self.data.codename,
                architectures=effective_architectures,
                arch_all_host_architecture=self.data.arch_all_host_architecture,
                enable_autopkgtest=self.data.enable_autopkgtest,
                autopkgtest_backend=self.data.autopkgtest_backend,
                enable_reverse_dependencies_autopkgtest=(
                    self.data.enable_reverse_dependencies_autopkgtest
                ),
                reverse_dependencies_autopkgtest_suite=(
                    self.data.reverse_dependencies_autopkgtest_suite
                ),
                enable_lintian=self.data.enable_lintian,
                lintian_backend=self.data.lintian_backend,
                lintian_fail_on_severity=self.data.lintian_fail_on_severity,
                enable_piuparts=self.data.enable_piuparts,
                piuparts_backend=self.data.piuparts_backend,
                piuparts_environment=self.data.piuparts_environment,
            )

        if (
            self.data.enable_make_signed_source
            and self.data.signing_template_names is not None
        ):
            if self.data.make_signed_source_purpose is None:
                raise WorkflowValidationError(
                    '"make_signed_source_purpose" must be set when '
                    'signing the source'
                )

            if self.data.make_signed_source_key is None:
                raise WorkflowValidationError(
                    '"make_signed_source_key" must be set when '
                    'signing the source'
                )

            for (
                upload_source_artifact,
                upload_binary_artifacts,
            ) in self._populate_make_signed_source(
                binary_artifacts=binary_artifacts,
                vendor=self.data.vendor,
                codename=self.data.codename,
                architectures=effective_architectures,
                purpose=self.data.make_signed_source_purpose,
                key=self.data.make_signed_source_key,
                sbuild_backend=self.data.sbuild_backend,
                signing_template_names=self.data.signing_template_names,
            ):
                to_upload.append(
                    (upload_source_artifact, upload_binary_artifacts, True)
                )

        if self.data.enable_upload:
            for (
                upload_source_artifact,
                upload_binary_artifacts,
                signed_source,
            ) in to_upload:
                self._populate_package_upload(
                    source_artifact=upload_source_artifact,
                    binary_artifacts=upload_binary_artifacts,
                    include_source=self.data.upload_include_source,
                    include_binaries=self.data.upload_include_binaries,
                    signed_source=signed_source,
                    merge_uploads=self.data.upload_merge_uploads,
                    since_version=self.data.upload_since_version,
                    key=self.data.upload_key,
                    require_signature=self.data.upload_require_signature,
                    target=self.data.upload_target,
                    delayed_days=self.data.upload_delayed_days,
                    vendor=self.data.vendor,
                    codename=self.data.codename,
                )

    def _populate_sbuild(
        self,
        *,
        source_artifact: LookupSingle,
        target_distribution: str,
        backend: BackendType,
        architectures: list[str],
        environment_variant: str | None,
        signing_template_names: dict[str, list[str]],
    ) -> None:
        """Create work request for sbuild workflow."""
        wr = self.work_request_ensure_child(
            task_name="sbuild",
            task_type=TaskTypes.WORKFLOW,
            task_data=SbuildWorkflowData(
                input=SbuildInput(source_artifact=source_artifact),
                target_distribution=target_distribution,
                backend=backend,
                architectures=architectures,
                arch_all_host_architecture=self.data.arch_all_host_architecture,
                environment_variant=environment_variant,
                signing_template_names=signing_template_names,
                extra_repositories=self.data.extra_repositories,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="sbuild",
                step="sbuild",
            ),
        )
        wr.mark_running()
        orchestrate_workflow(wr)

    def _populate_qa(
        self,
        *,
        source_artifact: LookupSingle,
        binary_artifacts: LookupMultiple,
        vendor: str,
        codename: str,
        architectures: list[str],
        arch_all_host_architecture: str,
        enable_autopkgtest: bool,
        autopkgtest_backend: BackendType,
        enable_reverse_dependencies_autopkgtest: bool,
        reverse_dependencies_autopkgtest_suite: LookupSingle | None,
        enable_lintian: bool,
        lintian_backend: BackendType,
        lintian_fail_on_severity: LintianFailOnSeverity,
        enable_piuparts: bool,
        piuparts_backend: BackendType,
        piuparts_environment: LookupSingle | None,
    ) -> None:
        """Create work request for qa workflow."""
        data = QAWorkflowData(
            source_artifact=source_artifact,
            binary_artifacts=binary_artifacts,
            vendor=vendor,
            codename=codename,
            architectures=architectures,
            arch_all_host_architecture=arch_all_host_architecture,
            extra_repositories=self.data.extra_repositories,
            enable_autopkgtest=enable_autopkgtest,
            autopkgtest_backend=autopkgtest_backend,
            enable_reverse_dependencies_autopkgtest=(
                enable_reverse_dependencies_autopkgtest
            ),
            reverse_dependencies_autopkgtest_suite=(
                reverse_dependencies_autopkgtest_suite
            ),
            enable_lintian=enable_lintian,
            lintian_backend=lintian_backend,
            lintian_fail_on_severity=lintian_fail_on_severity,
            enable_piuparts=enable_piuparts,
            piuparts_backend=piuparts_backend,
        )
        if piuparts_environment:
            data.piuparts_environment = piuparts_environment
        wr = self.work_request_ensure_child(
            task_name="qa",
            task_type=TaskTypes.WORKFLOW,
            task_data=data,
            workflow_data=WorkRequestWorkflowData(
                display_name="QA",
                step="qa",
            ),
        )
        # The qa workflow's children will have dependencies on the work
        # requests creating binary_artifacts, but the qa workflow itself
        # doesn't need that in order to populate itself.
        wr.mark_running()
        orchestrate_workflow(wr)

    def _populate_make_signed_source(
        self,
        *,
        vendor: str,
        codename: str,
        binary_artifacts: LookupMultiple,
        architectures: list[str],
        purpose: KeyPurpose,
        key: str,
        sbuild_backend: BackendType,
        signing_template_names: dict[str, list[str]],
    ) -> list[tuple[LookupSingle, LookupMultiple]]:
        """Create work request for make signed source workflow."""
        signing_artifacts: list[LookupSingle] = []
        to_upload: list[tuple[LookupSingle, LookupMultiple]] = []
        for arch, names in signing_template_names.items():
            for name in names:
                # The promises for the signing_artifacts are created
                # by Sbuild workflow
                signing_artifact = (
                    f"internal@collections/name:signing-template-{arch}-{name}"
                )
                lookup_single(
                    signing_artifact,
                    self.workspace,
                    user=self.work_request.created_by or AnonymousUser(),
                    workflow_root=self.work_request.get_workflow_root(),
                    expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
                )

                signing_artifacts.append(signing_artifact)
                to_upload.append(
                    (
                        f"internal@collections/name:"
                        f"signed-source-{arch}-{name}",
                        LookupMultiple.parse_obj(
                            {
                                "collection": "internal@collections",
                                "child_type": (
                                    LookupChildType.ARTIFACT_OR_PROMISE
                                ),
                                "name__startswith": (
                                    f"signed-source-{arch}-{name}|build-"
                                ),
                            }
                        ),
                    )
                )

        signing_template_artifacts = LookupMultiple.parse_obj(signing_artifacts)
        wr = self.work_request_ensure_child(
            task_name="make_signed_source",
            task_type=TaskTypes.WORKFLOW,
            task_data=MakeSignedSourceWorkflowData(
                binary_artifacts=binary_artifacts,
                signing_template_artifacts=signing_template_artifacts,
                vendor=vendor,
                codename=codename,
                architectures=architectures,
                purpose=purpose,
                key=key,
                sbuild_backend=sbuild_backend,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="make signed source", step="make_signed_source"
            ),
        )
        # The make_signed_source workflow's children will have dependencies
        # on the work requests creating binary_artifacts and
        # signing_artifacts, but the make_signed_source workflow itself
        # doesn't need that in order to populate itself.
        wr.mark_running()
        orchestrate_workflow(wr)

        return to_upload

    def _populate_package_upload(
        self,
        *,
        binary_artifacts: LookupMultiple,
        source_artifact: LookupSingle,
        include_source: bool,
        include_binaries: bool,
        signed_source: bool,
        merge_uploads: bool,
        since_version: str | None,
        key: str | None,
        require_signature: bool,
        target: str,
        delayed_days: int | None,
        vendor: str,
        codename: str,
    ) -> None:
        """Create work request for package upload workflow."""
        wr = self.work_request_ensure_child(
            task_name="package_upload",
            task_type=TaskTypes.WORKFLOW,
            task_data=PackageUploadWorkflowData(
                source_artifact=(
                    source_artifact if include_source or signed_source else None
                ),
                binary_artifacts=(
                    binary_artifacts
                    if include_binaries
                    else LookupMultiple.parse_obj(())
                ),
                merge_uploads=merge_uploads,
                since_version=since_version,
                target_distribution=self.data.upload_target_distribution,
                key=key,
                require_signature=require_signature,
                target=pydantic.parse_obj_as(PackageUploadTarget, target),
                delayed_days=delayed_days,
                vendor=vendor,
                codename=codename,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name="package upload", step="package_upload"
            ),
        )

        # The package_upload workflow's children will have dependencies on
        # the work requests creating source_artifact and binary_artifacts,
        # but the package_upload workflow itself doesn't normally need that
        # in order to populate itself.  An exception is if we're uploading
        # signed source with binaries, in which case the make_signed_source
        # workflow will only be able to create even the necessary promises
        # once the signed source package has been assembled.
        if signed_source and include_binaries and binary_artifacts:
            self.requires_artifact(wr, source_artifact)

        if wr.status == WorkRequest.Statuses.PENDING:
            wr.mark_running()
            orchestrate_workflow(wr)

    def get_label(self) -> str:
        """Return the task label."""
        return "run Debian pipeline"
