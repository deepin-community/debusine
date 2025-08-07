# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utility functions for workflows."""
import functools
from collections.abc import Collection as AbcCollection
from collections.abc import Iterable, Sequence
from typing import Any, TYPE_CHECKING

from debusine.artifacts import SourcePackage
from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianPackageBuildLog,
    DebianSourcePackage,
    DebianSystemTarball,
    DebianUpload,
    DebusinePromise,
    get_source_package_name,
)
from debusine.client.models import LookupChildType
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    CollectionItem,
    TaskDatabase,
)
from debusine.server.collections.lookup import (
    LookupResult,
    lookup_multiple,
    lookup_single,
    reconstruct_lookup,
)
from debusine.tasks import BaseTask, TaskConfigError, get_environment
from debusine.tasks.models import (
    BackendType,
    ExtraRepository,
    LookupMultiple,
    LookupSingle,
)

if TYPE_CHECKING:
    from debusine.server.workflows import Workflow


@functools.lru_cache(maxsize=100)
def source_package(workflow: "Workflow[Any, Any]") -> Artifact:
    """
    Retrieve the source package artifact.

    If ``workflow.data.input`` exists, use
    ``workflow.data.input.source_artifact``, otherwise
    ``workflow.data.source_artifact``.

    If the source artifact is a :ref:`debian:upload <artifact-upload>`,
    returns its :ref:`debian:source-package <artifact-source-package>`.
    """
    if hasattr(workflow.data, "input"):
        lookup = workflow.data.input.source_artifact
        configuration_key = "input.source_artifact"
    else:
        assert hasattr(workflow.data, "source_artifact")
        lookup = workflow.data.source_artifact
        configuration_key = "source_artifact"

    artifact = lookup_single(
        lookup,
        workflow.workspace,
        user=workflow.work_request.created_by,
        workflow_root=workflow.work_request.get_workflow_root(),
        expect_type=LookupChildType.ARTIFACT,
    ).artifact
    return locate_debian_source_package(configuration_key, artifact)


@functools.lru_cache(maxsize=100)
def source_package_data(workflow: "Workflow[Any, Any]") -> DebianSourcePackage:
    """Return source package artifact data for the workflow."""
    return SourcePackage.create_data(source_package(workflow).data)


def lookup_result_artifact_category(result: LookupResult) -> str:
    """
    Get artifact category from result of looking up an artifact.

    The result may be either an artifact or a promise.
    """
    if (
        result.result_type == CollectionItem.Types.ARTIFACT
        and result.artifact is not None
    ):
        return result.artifact.category
    elif (
        result.result_type == CollectionItem.Types.BARE
        and result.collection_item is not None
        and result.collection_item.category == BareDataCategory.PROMISE
    ):
        return DebusinePromise(**result.collection_item.data).promise_category
    else:
        raise ValueError(
            f"Cannot determine artifact category for lookup result: {result}"
        )


class ArtifactHasNoArchitecture(Exception):
    """Raised if it's not possible to determine the artifact's architecture."""


def lookup_result_architecture(result: LookupResult) -> str:
    """Get architecture from result of looking up an artifact."""
    architecture: str | None

    if result.artifact is not None:
        artifact_data = result.artifact.create_data()
        match artifact_data:
            case DebianBinaryPackages():
                architecture = artifact_data.architecture
            case DebianBinaryPackage():
                architecture = artifact_data.deb_fields.get("Architecture")
            case DebianUpload():
                architecture = artifact_data.changes_fields.get("Architecture")
            case _:
                raise ArtifactHasNoArchitecture(f"{type(artifact_data)}")
    elif result.collection_item is not None:
        architecture = result.collection_item.data.get("architecture")
    else:
        raise ValueError(
            "Unexpected result: must have collection_item or artifact"
        )

    if not isinstance(architecture, str):
        raise ValueError(
            f"Cannot determine architecture for lookup result: {result}"
        )

    return architecture


class ArtifactHasNoBinaryPackageName(Exception):
    """Raised if it's not possible to determine the artifact's binary name."""


def lookup_result_binary_package_name(result: LookupResult) -> str:
    """Get binary package name from result of looking up an artifact."""
    binary_package_name: str | None

    if result.artifact is not None:
        artifact_data = result.artifact.create_data()
        match artifact_data:
            case DebianBinaryPackage():
                binary_package_name = artifact_data.deb_fields.get("Package")
            case _:
                raise ArtifactHasNoBinaryPackageName(f"{type(artifact_data)}")
    elif result.collection_item is not None:
        binary_package_name = result.collection_item.data.get(
            "binary_package_name"
        )
    else:
        raise ValueError(
            "Unexpected result: must have collection_item or artifact"
        )

    if not isinstance(binary_package_name, str):
        raise ValueError(
            f"Cannot determine binary package name for lookup result: {result}"
        )

    return binary_package_name


def filter_artifact_lookup_by_arch(
    workflow: "Workflow[Any, Any]",
    lookup: LookupMultiple,
    architectures: Iterable[str],
) -> LookupMultiple:
    """Filter an artifact lookup by architecture."""
    workflow_root = workflow.work_request.get_workflow_root()
    results = lookup_multiple(
        lookup,
        workflow.workspace,
        user=workflow.work_request.created_by,
        workflow_root=workflow_root,
        expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
    )
    relevant: list[LookupSingle] = []
    for result in results:
        arch_in_lookup = lookup_result_architecture(result)
        if arch_in_lookup in architectures:
            relevant.append(
                reconstruct_lookup(result, workflow_root=workflow_root)
            )

    return LookupMultiple.parse_obj(sorted(relevant))


def get_architectures(
    workflow: "Workflow[Any, Any]", lookup: LookupMultiple
) -> set[str]:
    """
    Return set with all the architectures in the artifacts from the lookup.

    The architectures are extracted from each lookup result using
    :py:func:`lookup_result_architecture`.
    """
    results = lookup_multiple(
        lookup,
        workflow.workspace,
        user=workflow.work_request.created_by,
        workflow_root=workflow.work_request.get_workflow_root(),
        expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
    )

    return {lookup_result_architecture(result) for result in results}


def locate_debian_source_package(
    configuration_key: str, artifact: Artifact
) -> Artifact:
    """
    Accept a debian:upload or debian:source-package in a workflow.

    Resolve to the :ref:`debian:source-package <artifact-source-package>`.
    """
    BaseTask.ensure_artifact_categories(
        configuration_key=configuration_key,
        category=artifact.category,
        expected=[ArtifactCategory.SOURCE_PACKAGE, ArtifactCategory.UPLOAD],
    )
    match artifact.category:
        case ArtifactCategory.SOURCE_PACKAGE:
            return artifact
        case ArtifactCategory.UPLOAD:
            return follow_artifact_relation(
                artifact,
                ArtifactRelation.Relations.EXTENDS,
                ArtifactCategory.SOURCE_PACKAGE,
            )
        case _ as unreachable:  # pragma: no cover
            raise AssertionError(f"Unexpected artifact category: {unreachable}")


def follow_artifact_relation(
    artifact: Artifact,
    relation_type: ArtifactRelation.Relations,
    category: ArtifactCategory,
) -> Artifact:
    """Follow relations from artifact to find an artifact of category."""
    try:
        relation = artifact.relations.get(
            type=relation_type, target__category=category
        )
    except ArtifactRelation.DoesNotExist:
        raise TaskConfigError(
            f"Unable to find an artifact of category {category.value} with "
            f'a relationship of type {relation_type} from "{artifact}"'
        )
    except ArtifactRelation.MultipleObjectsReturned:
        raise TaskConfigError(
            f"Multiple artifacts of category {category.value} with "
            f'a relationship of type {relation_type} from "{artifact}" '
            f"found"
        )
    return relation.target


def locate_debian_source_package_lookup(
    workflow: "Workflow[Any, Any]", configuration_key: str, lookup: LookupSingle
) -> LookupSingle:
    """
    Return a lookup to a debian:source-package.

    If the specified lookup returns a :ref:`debian:source-package
    <artifact-source-package>`, return it.  If it returns a
    :ref:`debian:upload <artifact-upload>`, find the related
    :ref:`debian:source-package <artifact-source-package>` and return a
    lookup to it.
    """
    artifact = lookup_single(
        lookup,
        workflow.workspace,
        user=workflow.work_request.created_by,
        workflow_root=workflow.work_request.get_workflow_root(),
        expect_type=LookupChildType.ARTIFACT,
    ).artifact
    if artifact.category == ArtifactCategory.UPLOAD:
        source_package = locate_debian_source_package(
            configuration_key, artifact
        )
        return f"{source_package.id}@artifacts"
    BaseTask.ensure_artifact_categories(
        configuration_key=configuration_key,
        category=artifact.category,
        expected=[ArtifactCategory.SOURCE_PACKAGE],
    )
    return lookup


def get_source_package_names(
    results: Sequence[LookupResult],
    *,
    configuration_key: str,
    artifact_expected_categories: AbcCollection[ArtifactCategory],
) -> list[str]:
    """
    Return a sorted list of source package names from results.

    It ensures that:

    - The :py:class:`LookupResult` objects contain either an artifact or
      promise.
    - Artifacts belong to the artifact_expected_categories.
    - If :py:class:`LookupResult` is a promise: extracts the name from the
      promise data ``source_package_name``.

    :param results: A sequence of :py:class:`LookupResult` objects
      representing artifacts to be processed. Each entry is expected to be
      either an artifact or a promise.
    :param configuration_key: A string used by
      :py:meth:`BaseTask.ensure_artifact_categories` for the exception
      message.
    :param artifact_expected_categories: valid :py:class:`ArtifactCategory`
      that artifacts must belong to.
    :return: A sorted list of source package names.
    """
    source_package_names = set()

    for result in results:
        # lookup_multiple expect_type: only artifacts or promises
        match result.result_type:
            case CollectionItem.Types.ARTIFACT:
                assert result.artifact is not None
                category = result.artifact.category

                BaseTask.ensure_artifact_categories(
                    configuration_key=configuration_key,
                    category=category,
                    expected=artifact_expected_categories,
                )
                artifact_data = result.artifact.create_data()

                assert isinstance(
                    artifact_data,
                    (
                        DebianSourcePackage,
                        DebianUpload,
                        DebianBinaryPackage,
                        DebianBinaryPackages,
                        DebianPackageBuildLog,
                    ),
                )
                source_package_names.add(get_source_package_name(artifact_data))

            case _:
                # Makes coverage happy
                # It's a promise.
                assert result.result_type == CollectionItem.Types.BARE
                assert result.collection_item is not None

                BaseTask.ensure_artifact_categories(
                    configuration_key=configuration_key,
                    category=result.collection_item.data["promise_category"],
                    expected=artifact_expected_categories,
                )

                if (
                    package_name := result.collection_item.data.get(
                        "source_package_name"
                    )
                ) is not None:
                    source_package_names.add(package_name)

    return sorted(source_package_names)


def get_available_architectures(
    workflow: "Workflow[Any, Any]", *, vendor: str, codename: str
) -> set[str]:
    """Get architectures available for use with this vendor/codename."""
    architectures = set()

    for result in lookup_multiple(
        LookupMultiple.parse_obj(
            {"collection": vendor, "data__codename": codename}
        ),
        workflow.workspace,
        user=workflow.work_request.created_by,
        default_category=CollectionCategory.ENVIRONMENTS,
        expect_type=LookupChildType.ARTIFACT,
    ):
        architectures.add(result.artifact.data.get("architecture"))

    if not architectures:
        raise TaskConfigError(
            f"Unable to find any environments for {vendor}:{codename}"
        )

    architectures.add("all")

    return architectures


def configure_for_overlay_suite(
    workflow: "Workflow[Any, Any]",
    *,
    extra_repositories: list[ExtraRepository] | None,
    vendor: str,
    codename: str,
    environment: LookupSingle,
    backend: BackendType,
    architecture: str,
) -> list[ExtraRepository] | None:
    """Return any needed extra repository to use an overlay suite."""
    match (vendor, codename):
        case ("debian", "experimental"):
            components = ["main", "contrib"]  # TODO: There must be a better way
        case _:
            return extra_repositories

    assert components

    if extra_repositories is None:
        extra_repositories = []

    task_database = TaskDatabase(workflow.work_request)
    environment_artifact = get_environment(
        task_database,
        environment,
        architecture,
        backend,
        default_category=CollectionCategory.ENVIRONMENTS,
    )
    assert isinstance(environment_artifact.data, DebianSystemTarball)
    mirror = environment_artifact.data.mirror

    return extra_repositories + [
        ExtraRepository(
            url=mirror,
            suite=codename,
            components=components,
        )
    ]
