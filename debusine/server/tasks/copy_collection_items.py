# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to copy items into target collections."""

import shutil
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings

from debusine.artifacts.models import BareDataCategory
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    FileInArtifact,
    User,
    WorkRequest,
    Workspace,
)
from debusine.server.collections.lookup import (
    LookupResult,
    lookup_multiple,
    lookup_single,
)
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import CopyCollectionItemsData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class CannotCopy(Exception):
    """A given source item cannot be copied to a given target collection."""


class CopyCollectionItems(
    BaseServerTask[CopyCollectionItemsData, BaseDynamicTaskData],
    DefaultDynamicData[CopyCollectionItemsData],
):
    """Task that copies items into target collections."""

    TASK_VERSION = 1

    @staticmethod
    def copy_artifact(
        artifact: Artifact, target_workspace: Workspace
    ) -> Artifact:
        """Copy an artifact to a new workspace."""
        new_artifact = Artifact.objects.create(
            category=artifact.category,
            workspace=target_workspace,
            data=artifact.data,
            expiration_delay=artifact.expiration_delay,
            # The new artifact has a fresh created_at, but we keep
            # created_by and created_by_work_request from the old artifact
            # since that seems more likely to be useful.
            created_by=artifact.created_by,
            created_by_work_request=artifact.created_by_work_request,
            original_artifact=artifact,
        )
        for file_in_artifact in artifact.fileinartifact_set.select_related(
            "file"
        ):
            if not file_in_artifact.complete:
                raise CannotCopy(
                    f"Cannot copy incomplete {file_in_artifact.path!r} from "
                    f"artifact ID {artifact.id}"
                )
            file = file_in_artifact.file
            if not target_workspace.scope.download_file_stores(file).exists():
                # We must copy the file contents.
                try:
                    source_file_backend = (
                        artifact.workspace.scope.download_file_backend(file)
                    )
                except IndexError:
                    raise CannotCopy(
                        f"Cannot copy {file_in_artifact.path!r} from artifact "
                        f"ID {artifact.id}: not in any available file store"
                    )
                target_file_backend = (
                    target_workspace.scope.upload_file_backend(file)
                )
                with (
                    source_file_backend.get_stream(file) as source_file,
                    tempfile.NamedTemporaryFile(
                        prefix="file-copying-",
                        dir=settings.DEBUSINE_UPLOAD_DIRECTORY,
                    ) as temp_file,
                ):
                    shutil.copyfileobj(source_file, temp_file)
                    temp_file.flush()
                    target_file_backend.add_file(Path(temp_file.name), file)
            FileInArtifact.objects.create(
                artifact=new_artifact,
                path=file_in_artifact.path,
                file=file,
                complete=True,
                content_type=file_in_artifact.content_type,
            )
        return new_artifact

    @classmethod
    def copy_item(
        cls,
        source_item: LookupResult,
        target_collection: Collection,
        *,
        unembargo: bool = False,
        replace: bool = False,
        name_template: str | None = None,
        variables: dict[str, Any] | None = None,
        user: User,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Copy a single item into a target collection."""
        match source_item.result_type:
            case CollectionItem.Types.BARE:
                assert source_item.collection_item is not None
                source_workspace = (
                    source_item.collection_item.parent_collection.workspace
                )
                reference_data = {}
            case CollectionItem.Types.ARTIFACT:
                assert source_item.artifact is not None
                source_workspace = source_item.artifact.workspace
                reference_data = source_item.artifact.data
            case CollectionItem.Types.COLLECTION:  # pragma: no cover
                raise CannotCopy("Cannot copy entire collections")
            case _ as unreachable:
                raise AssertionError(
                    f"Unexpected lookup result type: {unreachable}"
                )

        target_workspace = target_collection.workspace

        if not unembargo:
            if not source_workspace.public and target_workspace.public:
                raise CannotCopy(
                    f"Copying from {source_workspace} to {target_workspace} "
                    f"requires unembargo=True"
                )

        merged_variables = (
            source_item.collection_item.data
            if source_item.collection_item is not None
            else {}
        ) | (variables or {})
        try:
            expanded_variables = CollectionItem.expand_variables(
                merged_variables, reference_data
            )
        except (KeyError, ValueError):
            raise CannotCopy(f"Cannot expand variables: {variables}")

        if name_template is not None:
            item_name = CollectionItem.expand_name(
                name_template, expanded_variables
            )
            item_variables = None
        else:
            item_name = None
            item_variables = expanded_variables

        match source_item.result_type:
            case CollectionItem.Types.BARE:
                assert source_item.collection_item is not None
                target_collection.manager.add_bare_data(
                    category=BareDataCategory(
                        source_item.collection_item.category
                    ),
                    user=user,
                    workflow=workflow,
                    # To match update-collections-with-data, pass variables
                    # even if they were used to compute the item name.
                    data=expanded_variables,
                    name=item_name,
                    replace=replace,
                )
            case CollectionItem.Types.ARTIFACT:
                assert source_item.artifact is not None
                new_artifact = cls.copy_artifact(
                    source_item.artifact, target_workspace
                )
                target_collection.manager.add_artifact(
                    new_artifact,
                    user=user,
                    workflow=workflow,
                    variables=item_variables,
                    name=item_name,
                    replace=replace,
                )
            case _ as unreachable:
                raise AssertionError(
                    f"Unexpected lookup result type: {unreachable}"
                )

    def _execute(self) -> bool:
        """Execute the task."""
        assert self.work_request is not None
        assert self.workspace is not None

        for copies in self.data.copies:
            source_items = lookup_multiple(
                copies.source_items,
                self.workspace,
                user=self.work_request.created_by,
                workflow_root=self.work_request.get_workflow_root(),
                expect_type=LookupChildType.ANY,
            )
            target_collection = lookup_single(
                lookup=copies.target_collection,
                workspace=self.work_request.workspace,
                user=self.work_request.created_by,
                workflow_root=self.work_request.get_workflow_root(),
                expect_type=LookupChildType.COLLECTION,
            ).collection
            for source_item in source_items:
                self.copy_item(
                    source_item,
                    target_collection,
                    unembargo=copies.unembargo,
                    replace=copies.replace,
                    name_template=copies.name_template,
                    variables=copies.variables,
                    user=self.work_request.created_by,
                    workflow=self.work_request.parent,
                )

        return True

    def get_label(self) -> str:
        """Return the task label."""
        target_collections = ", ".join(
            str(copies.target_collection) for copies in self.data.copies
        )
        return f"copy to {target_collections}"
