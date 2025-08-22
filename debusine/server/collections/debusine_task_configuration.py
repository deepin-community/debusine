# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The collection manager for debusine:task-configuration collections."""

import itertools
from collections.abc import Generator, Iterable
from datetime import datetime
from typing import Any

from django.db import IntegrityError
from django.utils import timezone

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineTaskConfiguration,
    TaskTypes,
)
from debusine.db.models import Collection, CollectionItem, User, WorkRequest
from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
)
from debusine.tasks import TaskConfigError


class DebusineTaskConfigurationManager(CollectionManagerInterface):
    """Manage collection of category debusine:task-configuration."""

    COLLECTION_CATEGORY = CollectionCategory.TASK_CONFIGURATION
    VALID_BARE_DATA_CATEGORIES = frozenset(
        {BareDataCategory.TASK_CONFIGURATION}
    )

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
                f"Adding to {CollectionCategory.TASK_CONFIGURATION} requires "
                f"data"
            )
        if name is not None:
            raise ItemAdditionError(
                f"Cannot use an explicit item name when adding to "
                f"{CollectionCategory.TASK_CONFIGURATION}"
            )

        config = DebusineTaskConfiguration(**data)
        name = config.name()

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


def lookup_config_by_name(
    config_collection: Collection, name: str
) -> DebusineTaskConfiguration | None:
    """Lookup a DebusineTaskConfiguration object by name."""
    try:
        item = config_collection.child_items.get(
            name=name, child_type=CollectionItem.Types.BARE
        )
    except CollectionItem.DoesNotExist:
        return None

    try:
        return DebusineTaskConfiguration(**item.data)
    except ValueError as exc:
        raise TaskConfigError(
            "debusine:task-configuration item does not validate", exc
        )


def lookup_templates(
    config_collection: Collection,
    entry: DebusineTaskConfiguration,
    stack: tuple[str, ...] = (),
) -> Generator[DebusineTaskConfiguration, None, None]:
    """
    Generate Task Configuration objects for a list of template names.

    :param config_collection: collection to use for lookups
    :param entry: entry with the list of template names to lookup
    :param stack: template names we are currently resolving, used to detect
                  circular loops
    """
    for name in entry.use_templates:
        if name in stack:
            raise TaskConfigError(
                f"{entry.name()}: template lookup cycle detected: "
                + "→".join(stack + (name,))
            )
        if (
            template_entry := lookup_config_by_name(
                config_collection, f"template:{name}"
            )
        ) is None:
            raise TaskConfigError(
                f"{entry.name()} references missing template {name!r}"
            )
        if template_entry.use_templates:
            yield from lookup_templates(
                config_collection, template_entry, stack + (name,)
            )
        yield template_entry


def list_configuration(
    config_collection: Collection, name: str
) -> Generator[DebusineTaskConfiguration, None, None]:
    """
    Lookup DebusineTaskConfiguration objects for an item name.

    The resulting sequence also contains referenced template elements.
    """
    if (entry := lookup_config_by_name(config_collection, name)) is None:
        return

    yield entry

    # Look up referenced templates
    yield from lookup_templates(config_collection, entry)


def build_configuration(
    items: Iterable[DebusineTaskConfiguration],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Turn config items into configuration to apply to task data."""
    default_values: dict[str, Any] = {}
    override_values: dict[str, Any] = {}
    locked_values = set()

    for config_item in items:
        # Drop all the entries referenced in `delete_values` (except
        # locked values)
        for key in config_item.delete_values:
            if key in locked_values:
                continue
            default_values.pop(key, None)
            override_values.pop(key, None)

        # Merge the default/override values in the response
        # (except locked values)
        for key, value in config_item.default_values.items():
            if key in locked_values:
                continue
            default_values[key] = value
        for key, value in config_item.override_values.items():
            if key in locked_values:
                continue
            override_values[key] = value

        # Update the set of locked values
        locked_values.update(config_item.lock_values)

    return default_values, override_values


def apply_configuration(
    task_data: dict[str, Any],
    config_collection: Collection,
    task_type: TaskTypes,
    task_name: str,
    subject: str | None,
    context: str | None,
) -> None:
    """
    Apply task configuration to task_data.

    :param task_data: the task data dict to configure, modified in place
    :param task_type: the task type for configuration lookup
    :param task_name: the task name for configuration lookup
    :param subject: subject for configuration lookup
    :param context: configuration context for configuration lookup
    """
    # Look up debusine:task-configuration items
    lookup_names = DebusineTaskConfiguration.get_lookup_names(
        task_type, task_name, subject, context
    )
    config_items = itertools.chain.from_iterable(
        list_configuration(config_collection, name) for name in lookup_names
    )

    # Turn items into a configuration to apply
    default_values, override_values = build_configuration(config_items)

    # Apply default values (add missing values, but also replace explicit
    # None values)
    for k, v in default_values.items():
        if task_data.get(k) is None:
            task_data[k] = v

    # Apply overrides
    task_data.update(override_values)
