# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Package publishing workflow."""

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.client.models import LookupChildType
from debusine.server.collections.lookup import lookup_multiple, lookup_single
from debusine.server.tasks.models import (
    CopyCollectionItemsCopies,
    CopyCollectionItemsData,
)
from debusine.server.workflows import Workflow, workflow_utils
from debusine.server.workflows.models import (
    PackagePublishWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import BaseDynamicTaskData, LookupMultiple, TaskTypes
from debusine.tasks.server import TaskDatabaseInterface


class PackagePublishWorkflow(
    Workflow[PackagePublishWorkflowData, BaseDynamicTaskData]
):
    """Publish packages to a target suite."""

    TASK_NAME = "package_publish"

    def populate(self) -> None:
        """Create work requests."""
        target_suite = lookup_single(
            lookup=self.data.target_suite,
            workspace=self.work_request.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.COLLECTION,
        ).collection
        target_workspace = target_suite.workspace

        copies = [
            CopyCollectionItemsCopies(
                source_items=LookupMultiple.parse_obj(
                    (
                        [self.data.source_artifact]
                        if self.data.source_artifact is not None
                        else []
                    )
                    + list(self.data.binary_artifacts)
                ),
                # Identify the target collection by ID to avoid strange
                # behaviour if the lookup result changes between now and
                # when the child task executes, e.g. due to creating a
                # collection earlier in the inheritance chain.  Implementing
                # https://salsa.debian.org/freexian-team/debusine/-/issues/495
                # may allow us to do better here.
                target_collection=target_suite.id,
                unembargo=self.data.unembargo,
                replace=self.data.replace,
                variables=self.data.suite_variables,
            )
        ]

        if self.data.binary_artifacts.__root__:
            try:
                source_build_logs_collection = self.lookup_singleton_collection(
                    CollectionCategory.PACKAGE_BUILD_LOGS
                )
            except KeyError:
                source_build_logs_collection = None
            try:
                target_build_logs_collection = self.lookup_singleton_collection(
                    CollectionCategory.PACKAGE_BUILD_LOGS,
                    workspace=target_workspace,
                )
            except KeyError:
                target_build_logs_collection = None
            if (
                source_build_logs_collection is not None
                and target_build_logs_collection is not None
                and source_build_logs_collection != target_build_logs_collection
            ):
                copies.append(
                    CopyCollectionItemsCopies(
                        source_items=LookupMultiple.parse_obj(
                            {
                                "collection": source_build_logs_collection.id,
                                "lookup__same_work_request": (
                                    self.data.binary_artifacts
                                ),
                            }
                        ),
                        target_collection=target_build_logs_collection.id,
                        unembargo=self.data.unembargo,
                        replace=self.data.replace,
                    )
                )

            try:
                source_task_history_collection = (
                    self.lookup_singleton_collection(
                        CollectionCategory.TASK_HISTORY
                    )
                )
            except KeyError:
                source_task_history_collection = None
            try:
                target_task_history_collection = (
                    self.lookup_singleton_collection(
                        CollectionCategory.TASK_HISTORY,
                        workspace=target_workspace,
                    )
                )
            except KeyError:
                target_task_history_collection = None
            if (
                source_task_history_collection is not None
                and target_task_history_collection is not None
                and source_task_history_collection
                != target_task_history_collection
            ):
                copies.append(
                    CopyCollectionItemsCopies(
                        source_items=LookupMultiple.parse_obj(
                            {
                                "collection": source_task_history_collection.id,
                                "lookup__same_workflow": (
                                    self.data.binary_artifacts
                                ),
                            }
                        ),
                        target_collection=target_task_history_collection.id,
                        unembargo=self.data.unembargo,
                        replace=self.data.replace,
                    )
                )

        wr = self.work_request_ensure_child(
            task_type=TaskTypes.SERVER,
            task_name="copycollectionitems",
            task_data=CopyCollectionItemsData(copies=copies),
            workflow_data=WorkRequestWorkflowData(
                display_name="Copy collection items",
                step="copy-collection-items",
            ),
        )
        self.requires_artifact(wr, copies[0].source_items)

    def build_dynamic_data(
        self,
        task_database: TaskDatabaseInterface,  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data for this workflow.

        :subject: package names of ``binary_artifacts`` separated by spaces
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
            artifact_expected_categories=(ArtifactCategory.UPLOAD,),
        )

        return BaseDynamicTaskData(subject=" ".join(source_package_names))

    def get_label(self) -> str:
        """Return the task label."""
        return f"publish to {self.data.target_suite}"
