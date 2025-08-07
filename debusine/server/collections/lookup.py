# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Lookups of items in collections."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, assert_never, overload

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q
from django.db.models.fields.json import KT

from debusine.artifacts.models import BareDataCategory, CollectionCategory
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    User,
    WorkRequest,
    Workspace,
)
from debusine.tasks.models import (
    LookupDict,
    LookupMultiple,
    LookupSingle,
    parse_lookup_string_segments,
)


@dataclass
class LookupResult:
    """
    The result of a collection item lookup.

    None of :py:class:`Artifact`, :py:class:`Collection`, or
    :py:class:`CollectionItem` are entirely suitable return types: a lookup
    that returns a bare item needs to return the collection item, but in the
    case of a single-segment lookup there's no collection item available,
    only an artifact or collection.

    The least bad option seems to be to define a custom return type
    returning all the different things callers might need.
    """

    result_type: CollectionItem.Types
    collection_item: CollectionItem | None = None
    artifact: Artifact | None = None
    collection: Collection | None = None


class LookupResultArtifact(LookupResult):
    """
    A collection item lookup containing an artifact.

    Used to assist type annotations.
    """

    result_type: Literal[CollectionItem.Types.ARTIFACT]
    artifact: Artifact


class LookupResultCollection(LookupResult):
    """
    A collection item lookup containing a collection.

    Used to assist type annotations.
    """

    result_type: Literal[CollectionItem.Types.COLLECTION]
    collection: Collection


def _lookup_single_first(
    lookup: str,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
) -> LookupResult:
    """
    Resolve the first segment of a string lookup.

    This is handled differently from the other segments, because the lookup
    doesn't happen in the context of a collection.
    """
    try:
        if m := re.match(r"^([0-9]+)@artifacts$", lookup):
            # TODO: filter using the current scope?
            # TODO: support user is None to mean the user in the current
            #       context?
            workspaces = Workspace.objects.can_display(user)
            workspace_filter = Q(workspace__in=workspaces)
            visible_artifacts = Artifact.objects.filter(workspace_filter)
            return LookupResult(
                result_type=CollectionItem.Types.ARTIFACT,
                artifact=visible_artifacts.get(id=int(m.group(1))),
            )
        elif m := re.match(r"^([0-9]+)@collections$", lookup):
            # TODO: filter using the current scope?
            # TODO: support user is None to mean the user in the current
            #       context?
            workspaces = Workspace.objects.can_display(user)
            workspace_filter = Q(workspace__in=workspaces)
            visible_collections = Collection.objects.filter(workspace_filter)
            return LookupResult(
                result_type=CollectionItem.Types.COLLECTION,
                collection=visible_collections.get(id=int(m.group(1))),
            )
        else:
            if lookup == "internal@collections":
                if workflow_root is None:
                    raise LookupError(
                        "internal@collections is only valid in the context of "
                        "a workflow"
                    )
                name = f"workflow-{workflow_root.id}"
                category = str(CollectionCategory.WORKFLOW_INTERNAL)
            elif "@" in lookup:
                name, category = lookup.rsplit("@", 1)
            elif default_category is not None:
                name = lookup
                category = default_category
            else:
                raise LookupError(
                    f"{lookup!r} does not specify a category and the context "
                    f"does not supply a default"
                )
            return LookupResult(
                result_type=CollectionItem.Types.COLLECTION,
                collection=workspace.get_collection(
                    user=user, category=category, name=name
                ),
            )
    except (Artifact.DoesNotExist, Collection.DoesNotExist):
        raise KeyError(f"{lookup!r} does not exist or is hidden")


@overload
def lookup_single(
    lookup: LookupSingle,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: Literal[LookupChildType.ARTIFACT],
) -> LookupResultArtifact: ...


@overload
def lookup_single(
    lookup: LookupSingle,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: Literal[LookupChildType.COLLECTION],
) -> LookupResultCollection: ...


@overload
def lookup_single(
    lookup: LookupSingle,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: LookupChildType = LookupChildType.ANY,
) -> LookupResult: ...


def lookup_single(
    lookup: LookupSingle,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: LookupChildType = LookupChildType.ANY,
) -> LookupResult:
    """
    Look up a collection item using a single lookup syntax.

    See :ref:`lookup-single`.

    :raises KeyError: if the lookup does not resolve to an item.
    :raises LookupError: if the lookup is invalid in some way.
    """
    if isinstance(lookup, int):
        if expect_type in (
            LookupChildType.ARTIFACT,
            LookupChildType.ARTIFACT_OR_PROMISE,
        ):
            lookup = f"{lookup}@artifacts"
        elif expect_type == LookupChildType.COLLECTION:
            lookup = f"{lookup}@collections"
        else:
            raise LookupError(
                "Integer lookups only work in contexts that expect an "
                "artifact or a collection"
            )

    if not lookup:
        raise LookupError("Empty lookup")

    segments = parse_lookup_string_segments(lookup)

    # Look up the first segment as a collection by name and category.
    result = _lookup_single_first(
        segments[0],
        workspace,
        user=user,
        default_category=default_category,
        workflow_root=workflow_root,
    )
    container: Collection | None = None

    # Resolve each subsequent segment by calling `lookup`.
    for i, segment in enumerate(segments[1:], start=1):
        container_name = "/".join(segments[:i])
        if result.result_type != CollectionItem.Types.COLLECTION:
            raise LookupError(
                f"{container_name!r} is of type"
                f" {result.result_type.name.lower()!r}"
                " instead of expected 'collection'"
            )
        container = result.collection
        assert container is not None
        if ":" not in segment:
            segment = f"name:{segment}"
        item = container.manager.lookup(segment)
        if item is None:
            raise KeyError(f"{container_name!r} has no item {segment!r}")
        result = LookupResult(
            result_type=CollectionItem.Types(item.child_type),
            collection_item=item,
            artifact=item.artifact,
            collection=item.collection,
        )

    if expect_type != LookupChildType.ANY:
        expected_result_types = {
            LookupChildType.BARE: [CollectionItem.Types.BARE],
            LookupChildType.ARTIFACT: [CollectionItem.Types.ARTIFACT],
            LookupChildType.ARTIFACT_OR_PROMISE: [
                CollectionItem.Types.ARTIFACT,
                CollectionItem.Types.BARE,
            ],
            LookupChildType.COLLECTION: [CollectionItem.Types.COLLECTION],
        }[expect_type]
        if result.result_type not in expected_result_types:
            raise LookupError(
                f"{lookup!r} is of type {result.result_type.name.lower()!r}"
                f" instead of expected {expect_type.name.lower()!r}"
            )

    return result


def _lookup_dict(
    lookup: LookupDict,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: LookupChildType = LookupChildType.ANY,
) -> list[LookupResult]:
    """
    Look up collection items using the dictionary lookup syntax.

    Results are returned in a predictable order (currently ordered by
    collection item ID, although this function only guarantees that the
    order will be stable between successive calls).

    See :ref:`lookup-dict`.
    """
    if (
        (expect_type == LookupChildType.ANY)
        or (expect_type == lookup.child_type)
        or (
            expect_type == LookupChildType.ARTIFACT_OR_PROMISE
            and lookup.child_type
            in (LookupChildType.ARTIFACT, LookupChildType.BARE)
        )
    ):
        # Valid request
        pass
    else:
        raise LookupError(
            f"Only lookups for type {expect_type.name.lower()!r} are allowed "
            f"here"
        )

    # Find the containing collection.
    collection = lookup_single(
        lookup.collection,
        workspace,
        user=user,
        default_category=default_category,
        workflow_root=workflow_root,
        expect_type=LookupChildType.COLLECTION,
    ).collection
    objects = CollectionItem.active_objects.filter(parent_collection=collection)

    # Prepare query conditions.  We don't need to check the workspace here;
    # it's good enough if the item is in a collection we can see.
    match lookup.child_type:
        case LookupChildType.BARE:
            objects = objects.filter(child_type=CollectionItem.Types.BARE)
        case LookupChildType.ARTIFACT:
            objects = objects.filter(child_type=CollectionItem.Types.ARTIFACT)
        case LookupChildType.ARTIFACT_OR_PROMISE:
            objects = objects.filter(
                Q(child_type=CollectionItem.Types.ARTIFACT)
                | Q(
                    child_type=CollectionItem.Types.BARE,
                    category=BareDataCategory.PROMISE,
                )
            )
        case LookupChildType.COLLECTION:
            objects = objects.filter(child_type=CollectionItem.Types.COLLECTION)
        case LookupChildType.ANY:
            pass
        case _ as unreachable:
            assert_never(unreachable)
    if lookup.category is not None:
        objects = objects.filter(category=lookup.category)
    if lookup.name_matcher is not None:
        objects = objects.filter(
            **{f"name__{lookup.name_matcher.kind}": lookup.name_matcher.value}
        )
    for key, matcher in lookup.data_matchers:
        annotation = f"data_text_{key}"
        objects = objects.annotate(**{annotation: KT(f"data__{key}")}).filter(
            **{f"{annotation}__{matcher.kind}": matcher.value}
        )
    for key, value in lookup.lookup_filters:
        objects = objects.filter(
            collection.manager.lookup_filter(
                key,
                value,
                workspace=workspace,
                user=user,
                workflow_root=workflow_root,
            )
        )
    objects = objects.select_related("artifact", "collection")

    # Execute the query.
    return [
        LookupResult(
            result_type=CollectionItem.Types(item.child_type),
            collection_item=item,
            artifact=item.artifact,
            collection=item.collection,
        )
        for item in objects.order_by("id")
    ]


@overload
def lookup_multiple(
    lookup: LookupMultiple,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: Literal[LookupChildType.ARTIFACT],
) -> Sequence[LookupResultArtifact]: ...


@overload
def lookup_multiple(
    lookup: LookupMultiple,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: Literal[LookupChildType.COLLECTION],
) -> Sequence[LookupResultCollection]: ...


@overload
def lookup_multiple(
    lookup: LookupMultiple,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: LookupChildType = LookupChildType.ANY,
) -> Sequence[LookupResult]: ...


def lookup_multiple(
    lookup: LookupMultiple,
    workspace: Workspace,
    *,
    user: User | AnonymousUser,
    default_category: CollectionCategory | None = None,
    workflow_root: WorkRequest | None = None,
    expect_type: LookupChildType = LookupChildType.ANY,
) -> Sequence[LookupResult]:
    """
    Look up collection items using a multiple lookup syntax.

    See :ref:`lookup-multiple`.

    :raises KeyError: if any of the lookups does not resolve to an item.
    :raises LookupError: if the lookup is invalid in some way.
    """
    results: list[LookupResult] = []
    for alternative in lookup:
        match alternative:
            case int() | str():
                results.append(
                    lookup_single(
                        alternative,
                        workspace,
                        user=user,
                        default_category=default_category,
                        workflow_root=workflow_root,
                        expect_type=expect_type,
                    )
                )
            case LookupDict():
                results.extend(
                    _lookup_dict(
                        alternative,
                        workspace,
                        user=user,
                        default_category=default_category,
                        workflow_root=workflow_root,
                        expect_type=expect_type,
                    )
                )
            case _ as unreachable:
                assert_never(unreachable)
    return tuple(results)


def _reconstruct_collection_lookup(
    collection_id: int, workflow_root: WorkRequest | None = None
) -> LookupSingle:
    """Reconstruct a lookup for a collection ID."""
    if (
        workflow_root is not None
        and collection_id == workflow_root.internal_collection_id
    ):
        return "internal@collections"
    else:
        return f"{collection_id}@collections"


def reconstruct_lookup(
    result: LookupResult, workflow_root: WorkRequest | None = None
) -> LookupSingle:
    """
    Reconstruct a lookup matching a given result.

    If the lookup result was from an item in a collection, then the returned
    lookup will resolve to the item with that name in that collection (which
    may not be the same as the original, if the item has been replaced; this
    is useful for promises that may be replaced by real artifacts).
    Otherwise, it will resolve to the same artifact or collection, providing
    that it still exists.
    """
    if (item := result.collection_item) is not None:
        collection_lookup = _reconstruct_collection_lookup(
            item.parent_collection_id, workflow_root=workflow_root
        )
        return f"{collection_lookup}/name:{item.name}"
    elif result.artifact is not None:
        return f"{result.artifact.id}@artifacts"
    else:
        assert result.collection is not None
        return _reconstruct_collection_lookup(
            result.collection.id, workflow_root=workflow_root
        )
