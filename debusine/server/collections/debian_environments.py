# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debian:environments collections."""

import re
from typing import Any

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import Artifact, CollectionItem, User, WorkRequest
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)


class DebianEnvironmentsManager(CollectionManagerInterface):
    """Manage collection of category debian:environments."""

    COLLECTION_CATEGORY = CollectionCategory.ENVIRONMENTS
    VALID_ARTIFACT_CATEGORIES = frozenset(
        {
            ArtifactCategory.SYSTEM_TARBALL,
            ArtifactCategory.SYSTEM_IMAGE,
        }
    )

    def do_add_artifact(
        self,
        artifact: Artifact,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,
        name: str | None = None,  # noqa: U100
        replace: bool = False,
    ) -> CollectionItem:
        """
        Add the artifact into the managed collection.

        :param artifact: artifact to add
        :param user: user adding the artifact to the collection
        :param workflow: workflow adding the artifact to the collection
        :param variables: may include `codename` to set the codename of the
          distribution version, `variant` to indicate what kind of tarball
          or image this is, or `backend` to indicate which backend this is
          intended for
        :param replace: if True, replace an existing item with the same name
        """
        artifact_data = artifact.data

        if variables is not None and "codename" in variables:
            codename = variables["codename"]
        else:
            codename = artifact_data["codename"]
        if variables is not None and "variant" in variables:
            variant = variables["variant"]
        else:
            variant = None
        if variables is not None and "backend" in variables:
            backend = variables["backend"]
        else:
            backend = None
        architecture = artifact_data["architecture"]

        if artifact.category == ArtifactCategory.SYSTEM_TARBALL:
            format_ = "tarball"
        else:
            assert artifact.category == ArtifactCategory.SYSTEM_IMAGE
            format_ = "image"

        data = {
            "codename": codename,
            "architecture": architecture,
            "variant": variant,
            "backend": backend,
        }

        name_elements = [format_, codename, architecture]
        if variant is not None or backend is not None:
            name_elements.append(variant or "")
            name_elements.append(backend or "")
        name = ":".join(name_elements)

        if replace:
            try:
                old_artifact = (
                    CollectionItem.active_objects.filter(
                        parent_collection=self.collection,
                        child_type=CollectionItem.Types.ARTIFACT,
                        name=name,
                    )
                    .latest("created_at")
                    .artifact
                )
            except CollectionItem.DoesNotExist:
                pass
            else:
                assert old_artifact is not None
                self.remove_artifact(old_artifact, user=user, workflow=workflow)

        try:
            return CollectionItem.objects.create_from_artifact(
                artifact,
                parent_collection=self.collection,
                name=name,
                data=data,
                created_by_user=user,
                created_by_workflow=workflow,
            )
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

    def do_remove_artifact(
        self,
        artifact: Artifact,
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Remove the artifact from the collection."""
        # Retention logic will remove the artifact, now only marked as
        # removed
        CollectionItem.objects.filter(
            artifact=artifact, parent_collection=self.collection
        ).update(
            removed_by_user=user,
            removed_by_workflow=workflow,
            removed_at=timezone.now(),
        )

    def do_lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem based on the query.

        :param query: Examples "match:codename=CODENAME:architecture=ARCH",
          "match:format=tarball:architecture=ARCH:variant=VARIANT". If more
          than one possible CollectionItem matches the query: return the
          most recently added one.
        """
        if m := re.match(r"^match:(.+)$", query):
            filters_string = m.group(1)
        else:
            raise LookupError(f'Unexpected lookup format: "{query}"')

        filters = dict(item.split("=", 1) for item in filters_string.split(":"))
        query_filter = Q(parent_collection=self.collection) & Q(
            child_type=CollectionItem.Types.ARTIFACT
        )

        if filters.get("format") == "tarball":
            query_filter &= Q(category=ArtifactCategory.SYSTEM_TARBALL)
        elif filters.get("format") == "image":
            query_filter &= Q(category=ArtifactCategory.SYSTEM_IMAGE)

        if "codename" in filters:
            query_filter &= Q(data__codename=filters["codename"])
        if "architecture" in filters:
            query_filter &= Q(data__architecture=filters["architecture"])
        if "variant" in filters:
            query_filter &= Q(data__variant=filters["variant"] or None)
        if "backend" in filters:
            query_filter &= Q(data__backend=filters["backend"])

        try:
            return CollectionItem.active_objects.filter(query_filter).latest(
                "created_at"
            )
        except CollectionItem.DoesNotExist:
            return None
