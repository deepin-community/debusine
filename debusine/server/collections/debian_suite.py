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
from typing import Any

from debian.debian_support import Version
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from debusine.artifacts.local_artifact import BinaryPackage, SourcePackage
from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Artifact,
    CollectionItem,
    CollectionItemMatchConstraint,
    User,
    WorkRequest,
)
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)


def make_pool_filename(
    source_package_name: str, component: str, base_name: str
) -> str:
    """
    Make a file name in a Debian repository's pool.

    See https://lists.debian.org/debian-devel/2000/10/msg01340.html for an
    explanation of the structure.
    """
    if source_package_name.startswith("lib"):
        prefix = source_package_name[:4]
    else:
        prefix = source_package_name[:1]
    return f"pool/{component}/{prefix}/{source_package_name}/{base_name}"


class DebianSuiteManager(CollectionManagerInterface):
    """Manage collection of category debian:suite."""

    COLLECTION_CATEGORY = CollectionCategory.SUITE
    VALID_ARTIFACT_CATEGORIES = frozenset(
        {ArtifactCategory.SOURCE_PACKAGE, ArtifactCategory.BINARY_PACKAGE}
    )

    def add_source_package(
        self,
        artifact: Artifact,
        *,
        user: User,
        component: str,
        section: str,
        replace: bool = False,
    ) -> CollectionItem:
        """
        Add a source package artifact to the managed collection.

        :param artifact: artifact to add
        :param user: user adding the artifact to the collection
        :param component: the component (e.g. `main` or `non-free`) for this
          package
        :param section: the section (e.g. `python`) for this package
        :param replace: if True, replace an existing source package of the
          same name and version
        """
        if artifact.category != ArtifactCategory.SOURCE_PACKAGE:
            raise ItemAdditionError(
                f'add_source_package requires a '
                f'{ArtifactCategory.SOURCE_PACKAGE} artifact, not '
                f'"{artifact.category}"'
            )

        return self.do_add_artifact(
            artifact,
            user=user,
            variables={"component": component, "section": section},
            replace=replace,
        )

    def add_binary_package(
        self,
        artifact: Artifact,
        *,
        user: User,
        component: str,
        section: str,
        priority: str,
        replace: bool = False,
    ) -> CollectionItem:
        """
        Add a binary package artifact to the managed collection.

        :param artifact: artifact to add
        :param user: user adding the artifact to the collection
        :param component: the component (e.g. `main` or `non-free`) for this
          package
        :param section: the section (e.g. `python`) for this package
        :param priority: the priority (e.g. `optional`) for this package
        :param replace: if True, replace an existing binary package of the
          same name, version, and architecture
        """
        if artifact.category != ArtifactCategory.BINARY_PACKAGE:
            raise ItemAdditionError(
                f'add_binary_package requires a '
                f'{ArtifactCategory.BINARY_PACKAGE} artifact, not '
                f'"{artifact.category}"'
            )

        return self.do_add_artifact(
            artifact,
            user=user,
            variables={
                "component": component,
                "section": section,
                "priority": priority,
            },
            replace=replace,
        )

    def make_constraints(
        self, item: CollectionItem, source_package_name: str, component: str
    ) -> Generator[CollectionItemMatchConstraint, None, None]:
        """Yield constraints for an item added to this collection."""
        assert item.artifact is not None
        for file_in_artifact in item.artifact.fileinartifact_set.all():
            pool_filename = make_pool_filename(
                source_package_name, component, file_in_artifact.path
            )
            file = file_in_artifact.file
            hash_digest = (
                f"{file.current_hash_algorithm}:{file.hash_digest.hex()}"
            )
            yield CollectionItemMatchConstraint(
                collection=self.collection,
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
    ) -> CollectionItem:
        """Add the artifact into the managed collection."""
        if variables is None or "component" not in variables:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.SUITE} requires a component"
            )
        if "section" not in variables:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.SUITE} requires a section"
            )
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
        else:
            assert artifact.category == ArtifactCategory.BINARY_PACKAGE
            if "priority" not in variables:
                raise ItemAdditionError(
                    f"Adding a binary package to {CollectionCategory.SUITE} "
                    f"requires a priority"
                )
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
                self.remove_artifact(old_artifact, user=user)

        try:
            item = CollectionItem.objects.create_from_artifact(
                artifact,
                parent_collection=self.collection,
                name=name,
                data=data,
                created_by_user=user,
                created_by_workflow=workflow,
            )
            CollectionItemMatchConstraint.objects.bulk_create(
                self.make_constraints(
                    item, source_package_name, data["component"]
                )
            )
            return item
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
        items = CollectionItem.active_objects.select_for_update().filter(
            artifact=artifact, parent_collection=self.collection
        )
        item_ids = {item.id for item in items.only("id")}
        items.update(
            removed_by_user=user,
            removed_by_workflow=workflow,
            removed_at=timezone.now(),
        )
        if self.collection.data.get("may_reuse_versions", False):
            CollectionItemMatchConstraint.objects.filter(
                collection_item_id__in=item_ids, constraint_name="pool-file"
            ).delete()

    def do_lookup(self, query: str) -> CollectionItem | None:
        """
        Return one CollectionItem based on the query.

        :param query: `source:NAME`, `source-version:NAME_VERSION`,
          `binary:NAME_ARCHITECTURE`, or
          `binary-version:NAME_VERSION_ARCHITECTURE`.  If more than one
          possible CollectionItem matches the query (which is possible for
          `source:` and `binary:` queries): return the one with the highest
          version.
        """
        query_filter = Q(
            parent_collection=self.collection,
            child_type=CollectionItem.Types.ARTIFACT,
        )

        if m := re.match(r"^source:(.+)$", query):
            query_filter &= Q(
                category=ArtifactCategory.SOURCE_PACKAGE,
                data__package=m.group(1),
            )
        elif m := re.match(r"^source-version:(.+?)_(.+)$", query):
            query_filter &= Q(
                category=ArtifactCategory.SOURCE_PACKAGE,
                data__package=m.group(1),
                data__version=m.group(2),
            )
        elif m := re.match(r"^binary:(.+?)_(.+)$", query):
            query_filter &= Q(
                category=ArtifactCategory.BINARY_PACKAGE,
                data__package=m.group(1),
                data__architecture=m.group(2),
            )
        elif m := re.match(r"^binary-version:(.+?)_(.+?)_(.+)$", query):
            query_filter &= Q(
                category=ArtifactCategory.BINARY_PACKAGE,
                data__package=m.group(1),
                data__version=m.group(2),
                data__architecture=m.group(3),
            )
        else:
            raise LookupError(f'Unexpected lookup format: "{query}"')

        # Ideally the sorting would be done in the database, but that
        # requires the debversion extension and installing that requires a
        # superuser, which is cumbersome since it means we can't do it in a
        # Django migration as currently configured.  We don't expect a
        # single suite to contain many active versions of a single package,
        # so doing the sorting in Python should be fine in practice.
        items = sorted(
            CollectionItem.active_objects.filter(query_filter),
            key=lambda item: Version(item.data["version"]),
        )
        if not items:
            return None
        return items[-1]
