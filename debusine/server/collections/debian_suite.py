# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debian:suite collections."""

import re
from collections.abc import Generator
from datetime import datetime
from typing import Any

from debian.debian_support import Version
from django.db import IntegrityError
from django.db.models.fields.json import KT
from django.utils import timezone

from debusine.artifacts.local_artifact import BinaryPackage, SourcePackage
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


def make_source_prefix(source_package_name: str) -> str:
    """
    Make the prefix for a source package name in a Debian repository's pool.

    This is the "<first letter of source name>" / "<first 4 letters of
    source name>" segment, as described in
    https://lists.debian.org/debian-devel/2000/10/msg01340.html.
    """
    if source_package_name.startswith("lib"):
        return source_package_name[:4]
    else:
        return source_package_name[:1]


def make_pool_filename(
    source_package_name: str, component: str, base_name: str
) -> str:
    """
    Make a file name in a Debian repository's pool.

    See https://lists.debian.org/debian-devel/2000/10/msg01340.html for an
    explanation of the structure.
    """
    prefix = make_source_prefix(source_package_name)
    return f"pool/{component}/{prefix}/{source_package_name}/{base_name}"


class DebianSuiteManager(CollectionManagerInterface):
    """Manage collection of category debian:suite."""

    COLLECTION_CATEGORY = CollectionCategory.SUITE
    VALID_ARTIFACT_CATEGORIES = frozenset(
        {
            ArtifactCategory.SOURCE_PACKAGE,
            ArtifactCategory.BINARY_PACKAGE,
            ArtifactCategory.REPOSITORY_INDEX,
        }
    )

    def make_constraints(
        self, item: CollectionItem, source_package_name: str, component: str
    ) -> Generator[CollectionItemMatchConstraint, None, None]:
        """Yield constraints for an item added to this collection."""
        assert item.artifact is not None
        containing_archives = Collection.objects.filter(
            category=CollectionCategory.ARCHIVE,
            child_items__removed_at__isnull=True,
            child_items__collection=self.collection,
        )
        for file_in_artifact in item.artifact.fileinartifact_set.all():
            pool_filename = make_pool_filename(
                source_package_name, component, file_in_artifact.path
            )
            file = file_in_artifact.file
            hash_digest = (
                f"{file.current_hash_algorithm}:{file.hash_digest.hex()}"
            )
            # Each file name in pool/ must only refer to at most one
            # concrete file in the suite at a given time.
            yield CollectionItemMatchConstraint(
                collection=self.collection,
                collection_item_id=item.id,
                constraint_name="pool-file",
                key=pool_filename,
                value=hash_digest,
            )
            # The pool/ directory is shared between suites in the same
            # archive, so we also need archive-level constraints to ensure
            # that different suites in the same archive don't contain the
            # same pool file with different contents.
            for archive in containing_archives:
                yield CollectionItemMatchConstraint(
                    collection=archive,
                    collection_item_id=item.id,
                    constraint_name="pool-file",
                    key=pool_filename,
                    value=hash_digest,
                )

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
        required_variables: dict[str, tuple[str, ...]] = {
            ArtifactCategory.SOURCE_PACKAGE: ("component", "section"),
            ArtifactCategory.BINARY_PACKAGE: (
                "component",
                "section",
                "priority",
            ),
            ArtifactCategory.REPOSITORY_INDEX: ("path",),
        }
        for required_variable in required_variables[artifact.category]:
            if required_variable not in (variables or {}):
                raise ItemAdditionError(
                    f'Adding {artifact.category} to {CollectionCategory.SUITE} '
                    f'requires "{required_variable}"'
                )
        assert variables is not None

        if artifact.category == ArtifactCategory.SOURCE_PACKAGE:
            source_package_data = SourcePackage.create_data(artifact.data)
            data = {
                "package": source_package_data.name,
                "version": source_package_data.version,
                "component": variables["component"],
                "section": variables["section"],
            }
            name = "{package}_{version}".format(**data)
            source_package_name = source_package_data.name
        elif artifact.category == ArtifactCategory.BINARY_PACKAGE:
            binary_package_data = BinaryPackage.create_data(artifact.data)
            data = {
                "srcpkg_name": binary_package_data.srcpkg_name,
                "srcpkg_version": binary_package_data.srcpkg_version,
                "package": binary_package_data.deb_fields["Package"],
                "version": binary_package_data.deb_fields["Version"],
                "architecture": binary_package_data.deb_fields["Architecture"],
                "component": variables["component"],
                "section": variables["section"],
                "priority": variables["priority"],
            }
            name = "{package}_{version}_{architecture}".format(**data)
            source_package_name = binary_package_data.srcpkg_name
        else:
            assert artifact.category == ArtifactCategory.REPOSITORY_INDEX
            data = {"path": variables["path"]}
            name = "index:{path}".format(**data)

        if replace:
            self.remove_items_by_name(
                name=name,
                child_types=[CollectionItem.Types.ARTIFACT],
                user=user,
                workflow=workflow,
            )

        try:
            item = CollectionItem.objects.create_from_artifact(
                artifact,
                parent_collection=self.collection,
                name=name,
                data=data,
                created_at=created_at,
                created_by_user=user,
                created_by_workflow=workflow,
                replaced_by=replaced_by,
            )
            if replaced_by is None and artifact.category in {
                ArtifactCategory.SOURCE_PACKAGE,
                ArtifactCategory.BINARY_PACKAGE,
            }:
                CollectionItemMatchConstraint.objects.bulk_create(
                    self.make_constraints(
                        item, source_package_name, data["component"]
                    )
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
        """Remove an item from the collection."""
        item.removed_by_user = user
        item.removed_by_workflow = workflow
        item.removed_at = timezone.now()
        item.save()
        if (
            item.artifact is not None
            and (
                item.artifact.category
                in {
                    ArtifactCategory.SOURCE_PACKAGE,
                    ArtifactCategory.BINARY_PACKAGE,
                }
            )
            and self.collection.data.get("may_reuse_versions", False)
        ):
            # This deletes both suite and archive constraints.
            CollectionItemMatchConstraint.objects.filter(
                collection_item_id=item.id, constraint_name="pool-file"
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
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
        )
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
        elif m := re.match(r"^index:(.+)$", query):
            qs = qs.annotate(path=KT("data__path")).filter(
                category=ArtifactCategory.REPOSITORY_INDEX,
                path=m.group(1),
            )
            sort_by_version = False
        else:
            raise LookupError(f'Unexpected lookup format: "{query}"')

        # Ideally the sorting would be done in the database, but that
        # requires the debversion extension and installing that requires a
        # superuser, which is cumbersome since it means we can't do it in a
        # Django migration as currently configured.  We don't expect a
        # single suite to contain many active versions of a single package,
        # so doing the sorting in Python should be fine in practice.
        items = list(qs)
        if sort_by_version:
            items = sorted(
                items, key=lambda item: Version(item.data["version"])
            )
        if not items:
            return None
        return items[-1]
