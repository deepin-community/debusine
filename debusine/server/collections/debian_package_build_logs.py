# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debian:package-build-logs collections."""

from datetime import datetime
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.db.models import IntegerField, Q
from django.db.models.functions import Cast
from django.db.models.lookups import In
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.db.models import (
    Artifact,
    CollectionItem,
    User,
    WorkRequest,
    Workspace,
)
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)
from debusine.server.collections.lookup import lookup_multiple
from debusine.tasks.models import LookupMultiple, LookupSingle


class DebianPackageBuildLogsManager(CollectionManagerInterface):
    """Manage collection of category debian:package-build-logs."""

    COLLECTION_CATEGORY = CollectionCategory.PACKAGE_BUILD_LOGS
    VALID_BARE_DATA_CATEGORIES = frozenset({BareDataCategory.PACKAGE_BUILD_LOG})
    VALID_ARTIFACT_CATEGORIES = frozenset({ArtifactCategory.PACKAGE_BUILD_LOG})

    def do_add_bare_data(
        self,
        category: BareDataCategory,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        data: dict[str, Any] | None = None,
        name: str | None = None,  # noqa: U100
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """Add bare data into the managed collection."""
        if data is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.PACKAGE_BUILD_LOGS} requires "
                f"data"
            )

        work_request_id = data["work_request_id"]
        vendor = data["vendor"]
        codename = data["codename"]
        architecture = data["architecture"]
        srcpkg_name = data["srcpkg_name"]
        srcpkg_version = data["srcpkg_version"]

        name_elements = [
            vendor,
            codename,
            architecture,
            srcpkg_name,
            srcpkg_version,
            str(work_request_id),
        ]
        name = "_".join(name_elements)

        if replace:
            self.remove_items_by_name(
                name=name,
                child_types=[CollectionItem.Types.BARE],
                user=user,
                workflow=workflow,
            )

        try:
            return CollectionItem.objects.create_from_bare_data(
                category,
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

    def do_add_artifact(
        self,
        artifact: Artifact,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        variables: dict[str, Any] | None = None,
        name: str | None = None,  # noqa: U100
        replace: bool = False,
        created_at: datetime | None = None,
        replaced_by: CollectionItem | None = None,
    ) -> CollectionItem:
        """Add the artifact into the managed collection."""
        if variables is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.PACKAGE_BUILD_LOGS} requires "
                f"variables"
            )
        artifact_data = artifact.data

        work_request_id = variables["work_request_id"]
        vendor = variables["vendor"]
        codename = variables["codename"]
        architecture = variables["architecture"]
        if "srcpkg_name" in variables:
            srcpkg_name = variables["srcpkg_name"]
        else:
            srcpkg_name = artifact_data["source"]
        if "srcpkg_version" in variables:
            srcpkg_version = variables["srcpkg_version"]
        else:
            srcpkg_version = artifact_data["version"]

        data = {
            "work_request_id": work_request_id,
            "vendor": vendor,
            "codename": codename,
            "architecture": architecture,
            "srcpkg_name": srcpkg_name,
            "srcpkg_version": srcpkg_version,
        }
        work_request = WorkRequest.objects.get(id=work_request_id)
        if work_request.worker is not None:
            data["worker"] = work_request.worker.name

        name_elements = [
            vendor,
            codename,
            architecture,
            srcpkg_name,
            srcpkg_version,
            str(work_request_id),
        ]
        name = "_".join(name_elements)

        if replace:
            self.remove_items_by_name(
                name=name,
                child_types=[
                    CollectionItem.Types.BARE,
                    CollectionItem.Types.ARTIFACT,
                ],
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

    def do_lookup_filter(
        self,
        key: str,
        value: LookupSingle | LookupMultiple,
        *,
        workspace: Workspace,
        user: User | AnonymousUser,
        workflow_root: WorkRequest | None = None,
    ) -> Q:
        """
        Return :py:class:`CollectionItem` conditions for a lookup filter.

        :param key: For `same_work_request`, return conditions matching
          build logs that were created by the same work request as any of
          the resulting artifacts.
        """
        if key == "same_work_request":
            if isinstance(value, LookupSingle):
                value = LookupMultiple.parse_obj([value])
            items = lookup_multiple(
                value, workspace, user=user, workflow_root=workflow_root
            )
            work_request_ids = {
                item.artifact.created_by_work_request_id
                for item in items
                if (
                    item.artifact is not None
                    and item.artifact.created_by_work_request_id is not None
                )
            }
            return Q(
                In(
                    Cast("data__work_request_id", IntegerField()),
                    work_request_ids,
                )
            )
        else:
            raise LookupError(f'Unexpected lookup filter format: "{key}"')
