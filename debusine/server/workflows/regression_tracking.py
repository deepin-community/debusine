# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common code for workflows that handle regression tracking."""

from abc import ABCMeta
from datetime import datetime
from functools import cached_property
from typing import TypeAlias, TypeVar

from django.db.models import Max
from django.db.models.functions import Greatest

from debusine.client.models import LookupChildType
from debusine.db.models import Collection, CollectionItem, WorkRequest
from debusine.server.collections.lookup import lookup_single
from debusine.server.workflows.base import Workflow, WorkflowValidationError
from debusine.server.workflows.models import RegressionTrackingWorkflowData
from debusine.tasks.models import (
    ActionSkipIfLookupResultChanged,
    BaseDynamicTaskData,
    RegressionAnalysisStatus,
)

WD = TypeVar("WD", bound=RegressionTrackingWorkflowData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class RegressionTrackingWorkflow(Workflow[WD, DTD], metaclass=ABCMeta):
    """Common code for a workflow that handles regression tracking."""

    @cached_property
    def qa_suite(self) -> Collection | None:
        """Suite that we are tracking regressions against, if configured."""
        if self.data.qa_suite is None:
            return None
        return lookup_single(
            self.data.qa_suite,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.COLLECTION,
        ).collection

    @cached_property
    def qa_suite_changed(self) -> datetime | None:
        """The latest time the ``qa_suite`` collection was changed."""
        # This method is only called when update_qa_results is True, in
        # which case this is checked by a model validator.
        assert self.qa_suite is not None

        changed = CollectionItem.objects.filter(
            parent_collection=self.qa_suite
        ).aggregate(changed=Greatest(Max("created_at"), Max("removed_at")))[
            "changed"
        ]
        assert isinstance(changed, (datetime, type(None)))
        return changed

    @cached_property
    def reference_qa_results(self) -> Collection | None:
        """Collection of reference results of QA tasks, if configured."""
        if self.data.reference_qa_results is None:
            return None
        return lookup_single(
            self.data.reference_qa_results,
            self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.COLLECTION,
        ).collection

    def validate_input(self) -> None:
        """Thorough validation of input data."""
        # Validate that we can look up self.data.qa_suite and
        # self.data.reference_qa_results.
        try:
            self.qa_suite
            self.reference_qa_results
        except LookupError as e:
            raise WorkflowValidationError(str(e)) from e

    def skip_if_qa_result_changed(
        self,
        work_request: WorkRequest,
        *,
        package: str,
        architecture: str,
        promise_name: str | None = None,
    ) -> None:
        """Skip this work request if another workflow won the race."""
        lookup = (
            f"{self.data.reference_qa_results}/"
            f"latest:{work_request.task_name}_{package}_{architecture}"
        )
        try:
            item = lookup_single(
                lookup,
                self.workspace,
                user=self.work_request.created_by,
                workflow_root=self.work_request.get_workflow_root(),
            ).collection_item
        except KeyError:
            item = None
        work_request.add_event_reaction(
            "on_assignment",
            ActionSkipIfLookupResultChanged(
                lookup=lookup,
                collection_item_id=None if item is None else item.id,
                promise_name=promise_name,
            ),
        )

    @staticmethod
    def compare_qa_results(
        reference: WorkRequest | None, new: WorkRequest | None
    ) -> RegressionAnalysisStatus:
        """
        Compare work requests providing two QA results.

        Return a status depending on the two work requests:

        * ``no-result``: when the comparison has not been completed yet
          (usually because we lack one of the two required QA results)
        * ``error``: when the comparison (or one of the required QA tasks)
          errored out
        * ``improvement``: when the new QA result is better than the
          original QA result
        * ``stable``: when the new QA result is neither better nor worse
          than the original QA result
        * ``regression``: when the new QA result is worse than the original
          QA result

        If the status is ``stable``, then the caller may perform a
        finer-grained analysis.
        """
        # Some aliases to make the match statement more readable.
        WR: TypeAlias = WorkRequest
        S: TypeAlias = WorkRequest.Statuses
        R: TypeAlias = WorkRequest.Results

        match (reference, new):
            case (None, _) | (_, None):
                return RegressionAnalysisStatus.NO_RESULT
            case WR(status=S.COMPLETED, result=R.SUCCESS | R.SKIPPED), WR(
                status=S.COMPLETED, result=R.FAILURE | R.ERROR
            ):
                return RegressionAnalysisStatus.REGRESSION
            case WR(status=S.COMPLETED, result=R.FAILURE | R.ERROR), WR(
                status=S.COMPLETED, result=R.SUCCESS | R.SKIPPED
            ):
                return RegressionAnalysisStatus.IMPROVEMENT
            case WR(status=S.COMPLETED, result=R.SUCCESS | R.SKIPPED), WR(
                status=S.COMPLETED, result=R.SUCCESS | R.SKIPPED
            ):
                return RegressionAnalysisStatus.STABLE
            case WR(status=S.COMPLETED, result=reference_result), WR(
                status=S.COMPLETED, result=new_result
            ) if (reference_result == new_result):
                return RegressionAnalysisStatus.STABLE
            case _:
                return RegressionAnalysisStatus.ERROR
