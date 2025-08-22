# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Workflow to coordinate metadata updates for all archives in a workspace."""

from datetime import datetime

from django.db import connection
from django.db.models import Exists, F, Max, Q, QuerySet
from django.db.models.sql.constants import LOUTER
from django_cte import CTEQuerySet, With

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.db.models import Collection, CollectionItem
from debusine.server.tasks.models import GenerateSuiteIndexesData
from debusine.server.workflows.base import Workflow
from debusine.server.workflows.models import (
    UpdateSuitesData,
    WorkRequestWorkflowData,
)
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class UpdateSuitesWorkflow(
    Workflow[UpdateSuitesData, BaseDynamicTaskData],
    DefaultDynamicData[UpdateSuitesData],
):
    """Coordinate metadata updates for all archives in a workspace."""

    TASK_NAME = "update_suites"

    def _get_current_transaction_timestamp(self) -> datetime:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
            [now] = cursor.fetchone()
        assert isinstance(now, datetime)
        return now

    def _find_stale_suite_names(self) -> list[str]:
        """
        Return the names of all stale suites in this workspace.

        For this purpose, a suite is "stale" if any source or binary
        packages in it have been changed (collection items created or
        removed) since its most recent ``Release`` file was generated.
        """
        suites: QuerySet[Collection]
        if self.data.force_basic_indexes:
            suites = Collection.objects.all()
        else:
            # Use a couple of CTEs to ensure that PostgreSQL uses a
            # reasonable index to find index:Release items, and that it only
            # does the work of figuring out the latest Release creation time
            # once.
            collection_queryset = CTEQuerySet(
                Collection, using=Collection.objects._db
            )
            # Build a virtual table of previously-generated Release indexes.
            release_item = With(
                CollectionItem.objects.filter(
                    parent_category=CollectionCategory.SUITE,
                    child_type=CollectionItem.Types.ARTIFACT,
                    category=ArtifactCategory.REPOSITORY_INDEX,
                    name="index:Release",
                ),
                name="release_item",
            )
            # Annotate collections with the most recent Release index
            # creation time in each one.
            collection_generated = With(
                release_item.join(
                    collection_queryset,
                    id=release_item.col.parent_collection_id,
                    _join_type=LOUTER,
                )
                .values("id")
                .annotate(latest_created=Max(release_item.col.created_at)),
                name="collection_generated",
            )
            # Find suite names where collection items have been created or
            # removed more recently than their most recent Release index.
            suites = (
                collection_generated.join(
                    collection_queryset, id=collection_generated.col.id
                )
                .with_cte(release_item)
                .with_cte(collection_generated)
                .annotate(
                    changed=Exists(
                        CollectionItem.objects.filter(
                            parent_collection=collection_generated.col.id
                        )
                        .annotate(
                            latest_created=(
                                collection_generated.col.latest_created
                            )
                        )
                        .filter(
                            Q(latest_created__isnull=True)
                            | Q(created_at__gt=F("latest_created"))
                            | Q(removed_at__gt=F("latest_created"))
                        )
                    ),
                )
                .filter(changed=True)
            )

        return list(
            suites.filter(
                workspace=self.workspace, category=CollectionCategory.SUITE
            )
            .order_by("name")
            .values_list("name", flat=True)
        )

    def populate(self) -> None:
        """Create child work requests for all stale suites."""
        # TODO: It would be useful to be able to run this workflow for a
        # given timestamp in order to backfill history.  This might only
        # make sense if the workflow is also limited to a particular suite.
        now = self._get_current_transaction_timestamp()
        for suite_name in self._find_stale_suite_names():
            suite_collection = f"{suite_name}@{CollectionCategory.SUITE}"
            self.work_request_ensure_child(
                task_type=TaskTypes.SERVER,
                task_name="generatesuiteindexes",
                task_data=GenerateSuiteIndexesData(
                    suite_collection=suite_collection, generate_at=now
                ),
                task_data_filter=Q(
                    task_data__suite_collection=suite_collection
                ),
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Generate indexes for {suite_name}",
                    step=f"generate-suite-indexes-{suite_name}",
                ),
            )

    def get_label(self) -> str:
        """Return the task label."""
        return "update all stale suites"
