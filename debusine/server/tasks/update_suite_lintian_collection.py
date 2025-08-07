# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Server-side task to update a debian:suite-lintian collection."""

import copy
from collections import defaultdict
from typing import Any

from django.db.models import QuerySet

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import Artifact, CollectionItem, WorkRequest
from debusine.server.tasks.models import UpdateSuiteLintianCollectionData
from debusine.server.tasks.update_derived_collection import (
    UpdateDerivedCollection,
)
from debusine.tasks.models import EventReactions, LintianData


class UpdateSuiteLintianCollection(
    UpdateDerivedCollection[UpdateSuiteLintianCollectionData],
):
    """Task that updates a debian:suite-lintian collection from debian:suite."""

    TASK_VERSION = 1

    BASE_COLLECTION_CATEGORIES = (CollectionCategory.SUITE,)
    DERIVED_COLLECTION_CATEGORIES = (CollectionCategory.SUITE_LINTIAN,)

    def find_expected_derived_items(
        self, relevant_base_items: QuerySet[CollectionItem]
    ) -> dict[str, set[int]]:
        """
        Find the derived item names that should exist given these base items.

        The returned dictionary maps derived item names to sets of base item
        IDs.
        """
        # First, group the base items by source name, source version, and
        # architecture.
        derived_items_by_arch: dict[str, dict[str, set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for item in relevant_base_items:
            if item.category == ArtifactCategory.SOURCE_PACKAGE:
                source_name = item.data["package"]
                source_version = item.data["version"]
                architecture = "source"
            else:
                assert item.category == ArtifactCategory.BINARY_PACKAGE
                source_name = item.data["srcpkg_name"]
                source_version = item.data["srcpkg_version"]
                architecture = item.data["architecture"]
            derived_items_by_arch[f"{source_name}_{source_version}"][
                architecture
            ].add(item.id)

        # Use the grouped base items to decide which derived items we need.
        # We only need a separate `*_source` item if we have a source
        # package with no corresponding binary packages; otherwise, source
        # packages are base items for each per-architecture derived item.
        derived_items = defaultdict(set)
        for (
            source_name_version,
            item_ids_by_arch,
        ) in derived_items_by_arch.items():
            for architecture, item_ids in item_ids_by_arch.items():
                if architecture != "source":
                    derived_items[f"{source_name_version}_{architecture}"] = (
                        item_ids | item_ids_by_arch.get("source", set())
                    )
            if item_ids_by_arch.keys() == {"source"}:
                derived_items[f"{source_name_version}_source"] = (
                    item_ids_by_arch["source"]
                )

        return derived_items

    def make_child_work_request(
        self,
        base_item_ids: set[int],
        child_task_data: dict[str, Any] | None,
        force: bool,
    ) -> WorkRequest | None:
        """Make a work request to create a new derived item with this name."""
        assert self.work_request is not None
        assert self.workspace is not None

        task_data: dict[str, Any] = copy.deepcopy(child_task_data or {})

        base_artifacts_by_category = defaultdict(set)
        for artifact in Artifact.objects.filter(
            collection_items__id__in=base_item_ids
        ).only("id", "category"):
            base_artifacts_by_category[artifact.category].add(artifact.id)
        source_artifact_ids = sorted(
            base_artifacts_by_category.get(
                ArtifactCategory.SOURCE_PACKAGE, set()
            )
        )
        binary_artifacts_ids = sorted(
            base_artifacts_by_category.get(
                ArtifactCategory.BINARY_PACKAGE, set()
            )
        )
        if not source_artifact_ids and not binary_artifacts_ids:
            raise ValueError(
                f"Base items must include at least one "
                f"{ArtifactCategory.SOURCE_PACKAGE} or "
                f"{ArtifactCategory.BINARY_PACKAGE} artifact"
            )
        if len(source_artifact_ids) > 1:
            raise ValueError(
                f"Base items must include at most one "
                f"{ArtifactCategory.SOURCE_PACKAGE} artifact: "
                f"{source_artifact_ids}"
            )
        task_data.setdefault("input", {})
        if source_artifact_ids:
            task_data["input"]["source_artifact"] = source_artifact_ids[0]
        task_data["input"]["binary_artifacts"] = [
            artifact_id for artifact_id in binary_artifacts_ids
        ]

        params: dict[str, Any] = {
            "task_name": "lintian",
            "task_data": model_to_json_serializable_dict(
                LintianData(**task_data), exclude_unset=True
            ),
            "workspace": self.workspace,
            "created_by": self.work_request.created_by,
            "parent": self.work_request,
            "event_reactions_json": EventReactions.parse_obj(
                {
                    "on_success": [
                        {
                            "action": "update-collection-with-artifacts",
                            "artifact_filters": {
                                "category": ArtifactCategory.LINTIAN
                            },
                            "collection": self.data.derived_collection,
                        }
                    ]
                }
            ).dict(exclude_unset=True),
        }
        if force or not WorkRequest.objects.filter(**params).exists():
            return WorkRequest.objects.create(**params)
        else:
            return None

    def get_label(self) -> str:
        """Return the task label."""
        return "sync derived lintian collection"
