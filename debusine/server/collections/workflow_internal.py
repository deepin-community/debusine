# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Manager for debusine:workflow-internal collections."""

from typing import Any

from django.db import IntegrityError
from django.utils import timezone

from debusine.artifacts.models import BareDataCategory, CollectionCategory
from debusine.db.models import Artifact, CollectionItem, User, WorkRequest
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)


class WorkflowInternalManager(CollectionManagerInterface):
    """
    Manage collection of category debusine:workflow-internal.

    This collection stores runtime data of a workflow.  Bare items can be
    used to store arbitrary JSON data, while artifact items can help to
    share artifacts between all the tasks (and help retain them for
    long-running workflows).
    """

    COLLECTION_CATEGORY = CollectionCategory.WORKFLOW_INTERNAL
    VALID_BARE_DATA_CATEGORIES = None
    VALID_ARTIFACT_CATEGORIES = None

    def do_add_bare_data(
        self,
        category: BareDataCategory,
        *,
        user: User,
        workflow: WorkRequest | None = None,
        data: dict[str, Any] | None = None,  # noqa: U100
        name: str | None = None,
        replace: bool = False,
    ) -> CollectionItem:
        """Add bare data into the managed collection."""
        if name is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.WORKFLOW_INTERNAL} requires "
                f"an item name"
            )

        if data is not None and category != BareDataCategory.PROMISE:
            # Only PROMISE can have data keys starting with promise_
            for key in data:
                if key.startswith("promise_"):
                    raise ItemAdditionError(
                        f'Fields starting with "promise_" are not '
                        f'allowed for category "{category}"'
                    )

        if replace:
            self.remove_bare_data(name, user=user)

        try:
            return CollectionItem.objects.create_from_bare_data(
                category,
                parent_collection=self.collection,
                created_by_user=user,
                created_by_workflow=workflow,
                name=name,
                data=data or {},
            )
        except IntegrityError as exc:
            raise ItemAdditionError(str(exc))

    def do_remove_bare_data(
        self,
        name: str,
        *,
        user: User | None = None,
        workflow: WorkRequest | None = None,
    ) -> None:
        """Remove a bare data item from the collection."""
        CollectionItem.active_objects.filter(
            name=name, parent_collection=self.collection
        ).update(
            removed_by_user=user,
            removed_by_workflow=workflow,
            removed_at=timezone.now(),
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
        """
        Add the artifact into the managed collection.

        If replace=True the artifact can replace an artifact or bare data.
        """
        if name is None:
            raise ItemAdditionError(
                f"Adding to {CollectionCategory.WORKFLOW_INTERNAL} requires "
                f"an item name"
            )

        if replace:
            self.do_remove_child_types(
                [CollectionItem.Types.ARTIFACT, CollectionItem.Types.BARE],
                name=name,
                user=user,
                workflow=workflow,
            )

        if variables is not None:
            for key, value in variables.items():
                # Forbid names starting with 'promise_'
                if key.startswith("promise_"):
                    raise ItemAdditionError(
                        'Keys starting with "promise_" '
                        'are not allowed in variables'
                    )
        try:
            return CollectionItem.objects.create_from_artifact(
                artifact,
                parent_collection=self.collection,
                created_by_user=user,
                created_by_workflow=workflow,
                name=name,
                data=variables or {},
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
        CollectionItem.active_objects.filter(
            artifact=artifact, parent_collection=self.collection
        ).update(
            removed_by_user=user,
            removed_by_workflow=workflow,
            removed_at=timezone.now(),
        )
