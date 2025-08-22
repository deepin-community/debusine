# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debian:archive collections."""

import re
from datetime import datetime
from typing import Any

from debian.debian_support import Version
from django.db import IntegrityError, connection
from django.db.models.fields.json import KT
from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    CollectionItemMatchConstraint,
    User,
    WorkRequest,
)
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)


class DebianArchiveManager(CollectionManagerInterface):
    """Manage collection of category debian:archive."""

    COLLECTION_CATEGORY = CollectionCategory.ARCHIVE
    VALID_ARTIFACT_CATEGORIES = frozenset({ArtifactCategory.REPOSITORY_INDEX})
    VALID_COLLECTION_CATEGORIES = frozenset({CollectionCategory.SUITE})

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
        if "path" not in (variables or {}):
            raise ItemAdditionError(
                f'Adding {artifact.category} to {CollectionCategory.ARCHIVE} '
                f'requires "path"'
            )
        assert variables is not None

        path = variables["path"]
        if path.startswith("dists/"):
            raise ItemAdditionError(
                "Archive-level repository indexes may not have paths under "
                "dists/"
            )
        data = {"path": path}
        name = "index:{path}".format(**data)

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

    def do_add_collection(
        self,
        collection: Collection,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,  # noqa: U100
        name: str | None = None,  # noqa: U100
        replace: bool = False,  # noqa: U100
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """Add the collection into the managed collection."""
        # Require suites to be in the same workspace as the archive.  Since
        # archives are singleton collections, this has the effect of
        # ensuring that a suite can be in at most one archive.
        if collection.workspace != self.collection.workspace:
            assert collection.category == CollectionCategory.SUITE
            raise ItemAdditionError(
                "Suite must be in the same workspace as the archive"
            )

        try:
            item = CollectionItem.objects.create_from_collection(
                collection,
                parent_collection=self.collection,
                name=collection.name,
                data={},
                created_at=created_at,
                created_by_user=user,
                created_by_workflow=workflow,
                replaced_by=replaced_by,
            )
            # The pool/ directory is shared between suites in the same
            # archive, so we need to copy the suite-level constraints
            # created by DebianSuiteManager.make_constraints to ensure that
            # different suites in the same archive don't contain the same
            # pool file with different contents.
            #
            # bulk_create doesn't support subqueries, so this needs raw SQL
            # in order to perform reasonably if the new suite already
            # contains many packages.
            if item.removed_at is None:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """\
                        INSERT INTO db_collectionitemmatchconstraint
                            (collection_id, collection_item_id, constraint_name,
                             key, value)
                        SELECT
                            %s, collection_item_id, constraint_name, key, value
                        FROM db_collectionitemmatchconstraint
                        WHERE
                            collection_id = %s
                            AND constraint_name = 'pool-file'
                        """,
                        [self.collection.id, collection.id],
                    )
            return item
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

    def do_remove_item(
        self,
        item: CollectionItem,
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Remove an item from the managed collection."""
        item.removed_by_user = user
        item.removed_by_workflow = workflow
        item.removed_at = timezone.now()
        item.save()
        if item.collection is not None and self.collection.data.get(
            "may_reuse_versions", False
        ):
            CollectionItemMatchConstraint.objects.filter(
                collection_item_id__in=CollectionItem.objects.filter(
                    parent_collection=item.collection
                ),
                constraint_name="pool-file",
            ).delete()

    def do_lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem based on the query.

        :param query: `source:NAME`, `source-version:NAME_VERSION`,
          `binary:NAME_ARCHITECTURE`,
          `binary-version:NAME_VERSION_ARCHITECTURE`, or `index:PATH`.  If
          more than one possible CollectionItem matches the query (which is
          possible for `source:` and `binary:` queries): return the one with
          the highest version.
        """
        qs = CollectionItem.objects.active().filter(
            child_type=CollectionItem.Types.ARTIFACT
        )
        via_suite: bool | str = True
        sort_by_version = True

        if m := re.match(r"^source:(.+)$", query):
            qs = qs.annotate(package=KT("data__package")).filter(
                category=ArtifactCategory.SOURCE_PACKAGE,
                package=m.group(1),
            )
        elif m := re.match(r"^source-version:(.+?)_(.+)$", query):
            qs = qs.annotate(
                package=KT("data__package"), version=KT("data__version")
            ).filter(
                category=ArtifactCategory.SOURCE_PACKAGE,
                package=m.group(1),
                version=m.group(2),
            )
        elif m := re.match(r"^binary:(.+?)_(.+)$", query):
            qs = qs.annotate(
                package=KT("data__package"),
                architecture=KT("data__architecture"),
            ).filter(
                category=ArtifactCategory.BINARY_PACKAGE,
                package=m.group(1),
                architecture__in={m.group(2), "all"},
            )
        elif m := re.match(r"^binary-version:(.+?)_(.+?)_(.+)$", query):
            qs = qs.annotate(
                package=KT("data__package"),
                version=KT("data__version"),
                architecture=KT("data__architecture"),
            ).filter(
                category=ArtifactCategory.BINARY_PACKAGE,
                package=m.group(1),
                version=m.group(2),
                architecture__in={m.group(3), "all"},
            )
        elif m := re.match(r"^index:dists/([^/]+)/(.+)$", query):
            qs = qs.annotate(path=KT("data__path")).filter(
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.REPOSITORY_INDEX,
                path=m.group(2),
            )
            via_suite = m.group(1)
            sort_by_version = False
        elif m := re.match(r"^index:(.+)$", query):
            qs = qs.annotate(path=KT("data__path")).filter(
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.REPOSITORY_INDEX,
                path=m.group(1),
            )
            via_suite = False
            sort_by_version = False
        else:
            raise LookupError(f'Unexpected lookup format: "{query}"')

        # Ideally the sorting would be done in the database, but that
        # requires the debversion extension and installing that requires a
        # superuser, which is cumbersome since it means we can't do it in a
        # Django migration as currently configured.  We don't expect a
        # single suite to contain many active versions of a single package,
        # so doing the sorting in Python should be fine in practice.
        if via_suite:
            suite_qs = (
                CollectionItem.objects.active()
                .filter(
                    parent_collection=self.collection,
                    child_type=CollectionItem.Types.COLLECTION,
                    category=CollectionCategory.SUITE,
                )
                .values("collection")
            )
            if isinstance(via_suite, str):
                suite_qs = suite_qs.filter(name=via_suite)
            qs = qs.filter(parent_collection__in=suite_qs)
        else:
            qs = qs.filter(parent_collection=self.collection)
        items = list(qs)
        if sort_by_version:
            items = sorted(
                items, key=lambda item: Version(item.data["version"])
            )
        if not items:
            return None
        return items[-1]
