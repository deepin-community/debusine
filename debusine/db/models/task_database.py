# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Database interaction for worker tasks on the server.

This TaskDatabase extension is used when Task code is run server-side with
database access.
"""

from collections.abc import Collection as AbcCollection
from typing import overload

from django.conf import settings

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.client.models import LookupChildType, RelationType
from debusine.db.models.artifacts import Artifact
from debusine.db.models.collections import Collection
from debusine.db.models.work_requests import WorkRequest
from debusine.tasks.models import LookupMultiple, LookupSingle
from debusine.tasks.server import (
    ArtifactInfo,
    CollectionInfo,
    MultipleArtifactInfo,
    TaskDatabaseInterface,
)


class TaskDatabase(TaskDatabaseInterface):
    """Implementation of database interaction in worker tasks."""

    def __init__(self, work_request: WorkRequest) -> None:
        """Construct a :py:class:`TaskDatabase`."""
        self.work_request = work_request

    @staticmethod
    def _make_artifact_info(artifact: "Artifact") -> ArtifactInfo:
        """Extract basic information from an artifact."""
        return ArtifactInfo(
            id=artifact.id,
            category=artifact.category,
            data=artifact.create_data(),
        )

    @staticmethod
    def _make_collection_info(collection: "Collection") -> CollectionInfo:
        """Extract basic information from a collection."""
        return CollectionInfo(
            id=collection.id, category=collection.category, data=collection.data
        )

    @overload
    def lookup_single_artifact(
        self,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
    ) -> ArtifactInfo: ...

    @overload
    def lookup_single_artifact(
        self,
        lookup: None,
        default_category: CollectionCategory | None = None,
    ) -> None: ...

    def lookup_single_artifact(
        self,
        lookup: LookupSingle | None,
        default_category: CollectionCategory | None = None,
    ) -> ArtifactInfo | None:
        """
        Look up a single artifact.

        :param lookup: A :ref:`lookup-single`.
        :param default_category: If the first segment of a string lookup
          (which normally identifies a collection) does not specify a
          category, use this as the default category.
        :return: Information about the artifact, or None if the provided
          lookup is None (for convenience in some call sites).
        :raises KeyError: if the lookup does not resolve to an item.
        :raises LookupError: if the lookup is invalid in some way, or does
          not resolve to an artifact.
        """
        # Import here to prevent circular imports
        from debusine.server.collections.lookup import lookup_single

        return (
            None
            if lookup is None
            else self._make_artifact_info(
                lookup_single(
                    lookup=lookup,
                    workspace=self.work_request.workspace,
                    user=self.work_request.created_by,
                    default_category=default_category,
                    workflow_root=self.work_request.get_workflow_root(),
                    expect_type=LookupChildType.ARTIFACT,
                ).artifact
            )
        )

    def lookup_multiple_artifacts(
        self,
        lookup: LookupMultiple | None,
        default_category: CollectionCategory | None = None,
    ) -> MultipleArtifactInfo:
        """
        Look up multiple artifacts.

        :param lookup: A :ref:`lookup-multiple`.
        :param default_category: If the first segment of a string lookup
          (which normally identifies a collection) does not specify a
          category, use this as the default category.
        :return: Information about each artifact.
        :raises KeyError: if any of the lookups does not resolve to an item.
        :raises LookupError: if any of the lookups is invalid in some way,
          or does not resolve to an artifact.
        """
        # Import here to prevent circular imports
        from debusine.server.collections.lookup import lookup_multiple

        return MultipleArtifactInfo(
            []
            if lookup is None
            else [
                self._make_artifact_info(result.artifact)
                for result in lookup_multiple(
                    lookup=lookup,
                    workspace=self.work_request.workspace,
                    user=self.work_request.created_by,
                    default_category=default_category,
                    workflow_root=self.work_request.get_workflow_root(),
                    expect_type=LookupChildType.ARTIFACT,
                )
            ]
        )

    def find_related_artifacts(
        self,
        artifact_ids: AbcCollection[int],
        target_category: ArtifactCategory,
        relation_type: RelationType = RelationType.RELATES_TO,
    ) -> MultipleArtifactInfo:
        """
        Find artifacts via relations.

        For each of the artifacts in ``artifact_ids``, find all artifacts of
        ``target_category`` that are related to the initial artifacts with a
        relation of type ``relation_type``.

        :param artifact_ids: The IDs of artifacts to start from.
        :param target_category: The category of artifacts to find.
        :param relation_type: The type of relation to follow.
        :return: Information about each artifact.
        """
        return MultipleArtifactInfo(
            [
                self._make_artifact_info(artifact)
                for artifact in Artifact.objects.filter(
                    targeted_by__artifact__in=artifact_ids,
                    category=target_category,
                    targeted_by__type=relation_type,
                )
            ]
        )

    @overload
    def lookup_single_collection(
        self,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
    ) -> CollectionInfo: ...

    @overload
    def lookup_single_collection(
        self,
        lookup: None,
        default_category: CollectionCategory | None = None,
    ) -> None: ...

    def lookup_single_collection(
        self,
        lookup: LookupSingle | None,
        default_category: CollectionCategory | None = None,
    ) -> CollectionInfo | None:
        """
        Look up a single collection.

        :param lookup: A :ref:`lookup-single`.
        :param default_category: If the first segment of a string lookup
          (which normally identifies a collection) does not specify a
          category, use this as the default category.
        :return: Information about the collection, or None if the provided
          lookup is None (for convenience in some call sites).
        :raises KeyError: if the lookup does not resolve to an item.
        :raises LookupError: if the lookup is invalid in some way, or does
          not resolve to a collection.
        """
        # Import here to prevent circular imports
        from debusine.server.collections.lookup import lookup_single

        return (
            None
            if lookup is None
            else self._make_collection_info(
                lookup_single(
                    lookup=lookup,
                    workspace=self.work_request.workspace,
                    user=self.work_request.created_by,
                    default_category=default_category,
                    workflow_root=self.work_request.get_workflow_root(),
                    expect_type=LookupChildType.COLLECTION,
                ).collection
            )
        )

    def get_server_setting(self, setting: str) -> str:
        """Look up a Django setting (strings only)."""
        value = getattr(settings, setting)
        assert isinstance(value, str)
        return value
