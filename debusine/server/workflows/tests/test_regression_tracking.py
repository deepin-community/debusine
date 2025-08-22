# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common tests for workflows that handle regression tracking."""

from typing import TypeAlias

from debusine.db.models import WorkRequest
from debusine.server.workflows.regression_tracking import (
    RegressionTrackingWorkflow,
)
from debusine.tasks.models import RegressionAnalysisStatus
from debusine.test.django import TestCase


class RegressionTrackingWorkflowTests(TestCase):
    """Common tests for workflows that handle regression tracking."""

    def test_compare_qa_results(self) -> None:
        S: TypeAlias = WorkRequest.Statuses
        R: TypeAlias = WorkRequest.Results
        RAS: TypeAlias = RegressionAnalysisStatus
        for ref_status, ref_result, new_status, new_result, expected in (
            (None, None, None, None, RAS.NO_RESULT),
            (None, None, S.COMPLETED, R.SUCCESS, RAS.NO_RESULT),
            (S.COMPLETED, R.SUCCESS, None, None, RAS.NO_RESULT),
            (S.COMPLETED, R.SUCCESS, S.COMPLETED, R.SUCCESS, RAS.STABLE),
            (S.COMPLETED, R.SUCCESS, S.COMPLETED, R.FAILURE, RAS.REGRESSION),
            (S.COMPLETED, R.SUCCESS, S.COMPLETED, R.ERROR, RAS.REGRESSION),
            (S.COMPLETED, R.SUCCESS, S.COMPLETED, R.SKIPPED, RAS.STABLE),
            (S.COMPLETED, R.FAILURE, S.COMPLETED, R.SUCCESS, RAS.IMPROVEMENT),
            (S.COMPLETED, R.FAILURE, S.COMPLETED, R.FAILURE, RAS.STABLE),
            (S.COMPLETED, R.FAILURE, S.COMPLETED, R.ERROR, RAS.ERROR),
            (S.COMPLETED, R.FAILURE, S.COMPLETED, R.SKIPPED, RAS.IMPROVEMENT),
            (S.COMPLETED, R.ERROR, S.COMPLETED, R.SUCCESS, RAS.IMPROVEMENT),
            (S.COMPLETED, R.ERROR, S.COMPLETED, R.ERROR, RAS.STABLE),
            (S.COMPLETED, R.ERROR, S.COMPLETED, R.FAILURE, RAS.ERROR),
            (S.COMPLETED, R.ERROR, S.COMPLETED, R.SKIPPED, RAS.IMPROVEMENT),
            (S.COMPLETED, R.SKIPPED, S.COMPLETED, R.SUCCESS, RAS.STABLE),
            (S.COMPLETED, R.SKIPPED, S.COMPLETED, R.FAILURE, RAS.REGRESSION),
            (S.COMPLETED, R.SKIPPED, S.COMPLETED, R.ERROR, RAS.REGRESSION),
            (S.COMPLETED, R.SKIPPED, S.COMPLETED, R.SKIPPED, RAS.STABLE),
            (S.ABORTED, None, S.COMPLETED, R.SUCCESS, RAS.ERROR),
            (S.COMPLETED, R.SUCCESS, S.ABORTED, None, RAS.ERROR),
        ):
            with self.subTest(
                ref_status=ref_status,
                ref_result=ref_result,
                new_status=new_status,
                new_result=new_result,
            ):
                if ref_status is None:
                    reference = None
                else:
                    reference = self.playground.create_work_request()
                    reference.status = ref_status
                    if ref_result is not None:
                        reference.result = ref_result
                    reference.save()
                if new_status is None:
                    new = None
                else:
                    new = self.playground.create_work_request()
                    new.status = new_status
                    if new_result is not None:
                        new.result = new_result
                    new.save()
                self.assertEqual(
                    RegressionTrackingWorkflow.compare_qa_results(
                        reference, new
                    ),
                    expected,
                )
