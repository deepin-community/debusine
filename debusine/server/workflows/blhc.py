# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""blhc workflow."""
from collections.abc import Sequence

from debusine.artifacts.models import ArtifactCategory
from debusine.client.models import LookupChildType
from debusine.server.collections.lookup import (
    LookupResult,
    lookup_multiple,
    reconstruct_lookup,
)
from debusine.server.workflows import workflow_utils
from debusine.server.workflows.base import Workflow
from debusine.server.workflows.models import (
    BlhcWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.workflow_utils import lookup_result_architecture
from debusine.tasks.models import (
    BaseDynamicTaskData,
    BlhcData,
    BlhcFlags,
    BlhcInput,
    LookupSingle,
)
from debusine.tasks.server import TaskDatabaseInterface


class BlhcWorkflow(Workflow[BlhcWorkflowData, BaseDynamicTaskData]):
    """Blhc workflow."""

    TASK_NAME = "blhc"

    def _lookup_package_build_logs(self) -> Sequence[LookupResult]:
        return lookup_multiple(
            self.data.package_build_logs,
            workflow_root=self.work_request.get_workflow_root(),
            workspace=self.workspace,
            user=self.work_request.created_by,
            expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
        )

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: distinct ``source`` field (separated by spaces) from each of
          the artifacts' data
        """
        source_package_names = workflow_utils.get_source_package_names(
            self._lookup_package_build_logs(),
            configuration_key="package_build_logs",
            artifact_expected_categories=(ArtifactCategory.PACKAGE_BUILD_LOG,),
        )
        return BaseDynamicTaskData(subject=" ".join(source_package_names))

    def populate(self) -> None:
        """Create work requests."""
        for package_build_log in self._lookup_package_build_logs():
            architecture = lookup_result_architecture(package_build_log)
            self._populate_single(
                package_build_log=reconstruct_lookup(package_build_log),
                architecture=architecture,
                extra_flags=self.data.extra_flags,
            )

    def _populate_single(
        self,
        *,
        package_build_log: LookupSingle,
        architecture: str,
        extra_flags: list[BlhcFlags],
    ) -> None:
        wr = self.work_request_ensure_child(
            task_name="blhc",
            task_data=BlhcData(
                input=BlhcInput(artifact=package_build_log),
                extra_flags=extra_flags,
            ),
            workflow_data=WorkRequestWorkflowData(
                display_name=f"build log hardening check for {architecture}",
                step=f"blhc-{architecture}",
            ),
        )
        self.requires_artifact(wr, package_build_log)

    def get_label(self) -> str:
        """Return the workflow label."""
        return "run blhc"
