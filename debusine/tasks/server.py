# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Server-side database interaction for worker tasks.

Worker tasks mostly run on external workers, but they also run some
pre-dispatch code on the server which needs to access the database.  To
avoid having to import Django from :py:mod:`debusine.tasks`, we define an
interface that is implemented by server code.
"""

from abc import ABC, abstractmethod
from collections.abc import Collection as AbcCollection
from dataclasses import dataclass
from typing import Any, overload

from debusine.artifacts.models import (
    ArtifactCategory,
    ArtifactData,
    CollectionCategory,
)
from debusine.client.models import RelationType
from debusine.tasks.models import LookupMultiple, LookupSingle


@dataclass
class ArtifactInfo:
    """Information about an artifact."""

    id: int
    category: str
    data: ArtifactData


class MultipleArtifactInfo(tuple[ArtifactInfo, ...]):
    """Information about multiple artifacts."""

    def get_ids(self) -> list[int]:
        """Return the ID of each artifact."""
        return [item.id for item in self]


@dataclass
class CollectionInfo:
    """Information about a collection."""

    id: int
    category: str
    data: dict[str, Any]


class TaskDatabaseInterface(ABC):
    """Interface for interacting with the database from worker tasks."""

    @overload
    @abstractmethod
    def lookup_single_artifact(
        self,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
    ) -> ArtifactInfo: ...

    @overload
    @abstractmethod
    def lookup_single_artifact(
        self,
        lookup: None,
        default_category: CollectionCategory | None = None,
    ) -> None: ...

    @abstractmethod
    def lookup_single_artifact(
        self,
        lookup: LookupSingle | None,
        default_category: CollectionCategory | None = None,
    ) -> ArtifactInfo | None:
        """Look up a single artifact using :ref:`lookup-single`."""

    @abstractmethod
    def lookup_multiple_artifacts(
        self,
        lookup: LookupMultiple | None,
        default_category: CollectionCategory | None = None,
    ) -> MultipleArtifactInfo:
        """Look up multiple artifacts using :ref:`lookup-multiple`."""

    @abstractmethod
    def find_related_artifacts(
        self,
        artifact_ids: AbcCollection[int],
        target_category: ArtifactCategory,
        relation_type: RelationType = RelationType.RELATES_TO,
    ) -> MultipleArtifactInfo:
        """Find artifacts via relations."""

    @overload
    @abstractmethod
    def lookup_single_collection(
        self,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
    ) -> CollectionInfo: ...

    @overload
    @abstractmethod
    def lookup_single_collection(
        self,
        lookup: None,
        default_category: CollectionCategory | None = None,
    ) -> None: ...

    @abstractmethod
    def lookup_single_collection(
        self,
        lookup: LookupSingle | None,
        default_category: CollectionCategory | None = None,
    ) -> CollectionInfo | None:
        """Look up a single collection using :ref:`lookup-single`."""

    def lookup_singleton_collection(
        self, category: CollectionCategory
    ) -> CollectionInfo | None:
        """Look up a singleton collection for `category`, if it exists."""
        try:
            return self.lookup_single_collection(f"_@{category}")
        except KeyError:
            return None

    @abstractmethod
    def get_server_setting(self, setting: str) -> str:
        """Look up a Django setting (strings only)."""
