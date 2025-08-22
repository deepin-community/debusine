# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debian:suite-lintian collections."""

import re
from datetime import datetime
from typing import Any

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from debusine.artifacts.local_artifact import LintianArtifact, SourcePackage
from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    CollectionItem,
    User,
    WorkRequest,
)
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)


class DebianSuiteLintianManager(CollectionManagerInterface):
    """Manage collection of category debian:suite-lintian."""

    COLLECTION_CATEGORY = CollectionCategory.SUITE_LINTIAN
    VALID_ARTIFACT_CATEGORIES = frozenset({ArtifactCategory.LINTIAN})

    def get_related_source_package(self, artifact: Artifact) -> Artifact:
        """
        Get the source package related to a Lintian analysis.

        Since it's possible (though not recommended) for a Lintian analysis
        to be run only on binary packages, we may need to search through
        multiple levels of artifact relations.  This gets particularly
        complex in the case of debian:upload.
        """
        return (
            Artifact.objects.filter(
                # Match a source package processed by this Lintian run.
                Q(
                    targeted_by__artifact=artifact,
                    targeted_by__type=ArtifactRelation.Relations.RELATES_TO,
                )
                # Match a source package that built a binary package or
                # packages processed by this Lintian run.
                | Q(
                    targeted_by__artifact__targeted_by__artifact=artifact,
                    targeted_by__artifact__targeted_by__type=(
                        ArtifactRelation.Relations.RELATES_TO
                    ),
                    targeted_by__artifact__category__in=(
                        ArtifactCategory.BINARY_PACKAGE,
                        ArtifactCategory.BINARY_PACKAGES,
                    ),
                    targeted_by__type=ArtifactRelation.Relations.BUILT_USING,
                )
                # Match a source package that built a binary package that
                # forms part of an upload processed by this Lintian run.
                | Q(
                    targeted_by__artifact__targeted_by__artifact__targeted_by__artifact=artifact,  # noqa: E501
                    targeted_by__artifact__targeted_by__artifact__targeted_by__type=(  # noqa: E501
                        ArtifactRelation.Relations.RELATES_TO
                    ),
                    targeted_by__artifact__targeted_by__artifact__category=(
                        ArtifactCategory.UPLOAD
                    ),
                    targeted_by__artifact__targeted_by__type=(
                        ArtifactRelation.Relations.EXTENDS
                    ),
                    targeted_by__artifact__category=(
                        ArtifactCategory.BINARY_PACKAGE
                    ),
                    targeted_by__type=ArtifactRelation.Relations.BUILT_USING,
                ),
                category=ArtifactCategory.SOURCE_PACKAGE,
            )
            .distinct()
            .get()
        )

    def get_lintian_architecture(self, package_filename: dict[str, str]) -> str:
        """
        Get the architecture name for a Lintian analysis.

        This isn't stored directly; we have to infer it from
        `package_filename`.  It may be "source" for a source analysis, "all"
        for a binary-all analysis, or a concrete architecture name for a
        binary-any analysis.
        """
        architectures = set()
        for filename in package_filename.values():
            if re.match(r".*\.dsc$", filename):
                architectures.add("source")
            elif m := re.match(r".*_(.*?)\.(?:deb|udeb)$", filename):
                architectures.add(m.group(1))
        if not architectures:
            raise ItemAdditionError(
                "Could not infer any architectures for analysis"
            )
        elif len(list(architectures)) > 1:
            raise ItemAdditionError(
                f"Analysis refers to multiple architectures: "
                f"{sorted(architectures)}"
            )
        return architectures.pop()

    def do_add_artifact(
        self,
        artifact: Artifact,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,
        name: str | None = None,
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """Add the artifact into the managed collection."""
        try:
            source = self.get_related_source_package(artifact)
        except Artifact.DoesNotExist:
            raise ItemAdditionError(
                f"{artifact!r} has no related source package"
            )
        except Artifact.MultipleObjectsReturned:
            raise ItemAdditionError(
                f"{artifact!r} has multiple related source packages"
            )
        source_package_data = SourcePackage.create_data(source.data)
        source_name = source_package_data.name
        source_version = source_package_data.version

        summary = LintianArtifact.create_data(artifact.data).summary
        architecture = self.get_lintian_architecture(summary.package_filename)

        data: dict[str, Any] = {
            "package": source_name,
            "version": source_version,
            "architecture": architecture,
        }
        if variables is not None and "derived_from_ids" in variables:
            derived_from_ids = variables["derived_from_ids"]
            assert isinstance(derived_from_ids, list)
            assert all(isinstance(item, int) for item in derived_from_ids)
            data["derived_from"] = derived_from_ids
        name = f"{source_name}_{source_version}_{architecture}"

        if replace:
            self.remove_items_by_name(
                name=name,
                child_types=[CollectionItem.Types.ARTIFACT],
                user=user,
                workflow=workflow,
            )

        try:
            return CollectionItem.objects.create_from_artifact(
                artifact,
                parent_collection=self.collection,
                name=name,
                data=data,
                created_at=created_at,
                created_by_user=user,
                created_by_workflow=workflow,
                replaced_by=replaced_by,
            )
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

    def do_remove_item(
        self,
        item: CollectionItem,
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Remove an item from the collection."""
        item.removed_by_user = user
        item.removed_by_workflow = workflow
        item.removed_at = timezone.now()
        item.save()

    def do_lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem based on the query.

        :param query: `latest:NAME_ARCHITECTURE` or
          `version:NAME_VERSION_ARCHITECTURE`.  If more than one possible
          CollectionItem matches the query (which is possible for `latest:`
          queries): return the most recently added one.
        """
        query_filter = Q(
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
            category=ArtifactCategory.LINTIAN,
        )

        if m := re.match(r"^latest:(.+?)_(.+)$", query):
            query_filter &= Q(
                data__package=m.group(1), data__architecture=m.group(2)
            )
        elif m := re.match(r"^version:(.+?)_(.+?)_(.+)$", query):
            query_filter &= Q(
                data__package=m.group(1),
                data__version=m.group(2),
                data__architecture=m.group(3),
            )
        else:
            raise LookupError(f'Unexpected lookup format: "{query}"')

        try:
            return (
                CollectionItem.objects.active()
                .filter(query_filter)
                .latest("created_at")
            )
        except CollectionItem.DoesNotExist:
            return None
