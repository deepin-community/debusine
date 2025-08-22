# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Workflow to update environments."""

import copy
from dataclasses import dataclass
from typing import Any, Literal, assert_never

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.server.workflows.base import Workflow
from debusine.server.workflows.models import (
    UpdateEnvironmentsWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    ActionTypes,
    ActionUpdateCollectionWithArtifacts,
    BaseDynamicTaskData,
    BaseTaskData,
    MmDebstrapData,
    SystemImageBuildData,
)
from debusine.tasks.server import TaskDatabaseInterface


@dataclass(frozen=True)
class ActionVariables:
    """Variables to pass to an update-collection-with-artifacts action."""

    codename: str
    variant: str | None
    backend: str | None

    def to_dict(self) -> dict[str, str]:
        """Make a dict for an update-collection-with-artifacts action."""
        variables = {"codename": self.codename}
        if self.variant is not None:
            variables["variant"] = self.variant
        if self.backend is not None:
            variables["backend"] = self.backend
        return variables


@dataclass(frozen=True)
class ExistingChild:
    """Relevant data from an existing child work request."""

    group: str | None
    architecture: str
    # Variables associated with each update-collection-with-artifacts
    # action.
    action_variables: tuple[ActionVariables, ...]


class UpdateEnvironmentsWorkflow(
    Workflow[UpdateEnvironmentsWorkflowData, BaseDynamicTaskData]
):
    """Build tarballs/images and add them to an environments collection."""

    TASK_NAME = "update_environments"

    def _find_children(
        self, task_name: Literal["mmdebstrap", "simplesystemimagebuild"]
    ) -> set[ExistingChild]:
        """
        Find existing child work requests.

        These are represented as a set of :py:class:`ExistingChild` objects
        for each matching child work request.
        """
        assert self.work_request is not None
        existing = set()
        for child in self.work_request.children.filter(
            task_type=TaskTypes.WORKER,
            task_name=task_name,
        ):
            task_data: MmDebstrapData | SystemImageBuildData
            match task_name:
                case "mmdebstrap":
                    task_data = MmDebstrapData(**child.task_data)
                case "simplesystemimagebuild":
                    task_data = SystemImageBuildData(**child.task_data)
                case _ as unreachable:
                    assert_never(unreachable)
            group = child.workflow_data.group
            action_variables = [
                ActionVariables(
                    codename=action.variables["codename"],
                    variant=action.variables.get("variant"),
                    backend=action.variables.get("backend"),
                )
                for action in child.event_reactions.on_success
                if (
                    action.action
                    == ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS
                    and action.variables is not None
                )
            ]
            existing.add(
                ExistingChild(
                    group=group,
                    architecture=task_data.bootstrap_options.architecture,
                    action_variables=tuple(action_variables),
                )
            )
        return existing

    def _make_action_variables(
        self,
        *,
        codenames: list[str],
        variants: list[str | None],
        backends: list[str],
    ) -> list[ActionVariables]:
        """
        Return collection update action variables for a child work request.

        Each item in the returned list should be used to build an event
        reaction that adds artifacts to a collection.
        """
        action_variables: list[ActionVariables] = []
        variants_or_none: list[str | None] = list(variants)
        if not variants_or_none:
            variants_or_none.append(None)
        backends_or_none: list[str | None] = list(backends)
        if not backends_or_none:
            backends_or_none.append(None)
        for codename in codenames:
            for variant in variants_or_none:
                for backend in backends_or_none:
                    action_variables.append(
                        ActionVariables(
                            codename=codename, variant=variant, backend=backend
                        )
                    )
        return action_variables

    def _add_child(
        self,
        group: str,
        task_name: str,
        task_data_class: type[BaseTaskData],
        template: dict[str, Any],
        *,
        category: ArtifactCategory,
        display_name_prefix: str,
        vendor: str,
        codename: str,
        action_variables: list[ActionVariables],
        architecture: str,
    ) -> None:
        """Create a child work request."""
        assert self.work_request is not None
        task_data = copy.deepcopy(template)
        task_data.setdefault("bootstrap_options", {})[
            "architecture"
        ] = architecture
        for repository in task_data.get("bootstrap_repositories", []):
            repository.setdefault("suite", codename)

        step = f"{task_name}-{codename}-{architecture}"
        display_name = f"{display_name_prefix} for {codename}/{architecture}"

        wr = self.work_request_ensure_child(
            task_name=task_name,
            task_data=task_data_class(**task_data),
            workflow_data=WorkRequestWorkflowData(
                step=step, display_name=display_name, group=group
            ),
        )
        for variables in action_variables:
            wr.add_event_reaction(
                "on_success",
                ActionUpdateCollectionWithArtifacts(
                    collection=(f"{vendor}@{CollectionCategory.ENVIRONMENTS}"),
                    variables=variables.to_dict(),
                    artifact_filters={"category": category},
                ),
            )

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """Compute dynamic data for this workflow."""
        return BaseDynamicTaskData(subject=self.data.vendor)

    def populate(self) -> None:
        """Create child work requests for all targets and architectures."""
        assert self.work_request is not None
        existing_mmdebstrap = self._find_children("mmdebstrap")
        existing_simplesystemimagebuild = self._find_children(
            "simplesystemimagebuild"
        )

        for target in self.data.targets:
            codenames = (
                target.codenames
                if isinstance(target.codenames, list)
                else [target.codenames]
            )
            variants = (
                target.variants
                if isinstance(target.variants, list)
                else [target.variants]
            )
            backends = (
                target.backends
                if isinstance(target.backends, list)
                else [target.backends]
            )

            for codename in codenames:
                # Build the group name for the child work requests.
                group = codename
                if variants:
                    group += (
                        f" [{','.join([str(variant) for variant in variants])}]"
                    )

                for architecture in target.architectures:
                    # Build data structures to help check whether a matching
                    # work request already exists.
                    action_variables = self._make_action_variables(
                        codenames=(
                            [codename]
                            + target.codename_aliases.get(codename, [])
                        ),
                        variants=variants,
                        backends=backends,
                    )
                    existing_child = ExistingChild(
                        group=group,
                        architecture=architecture,
                        action_variables=tuple(action_variables),
                    )

                    # Create new mmdebstrap request if needed.
                    if (
                        target.mmdebstrap_template is not None
                        and existing_child not in existing_mmdebstrap
                    ):
                        self._add_child(
                            group,
                            "mmdebstrap",
                            MmDebstrapData,
                            target.mmdebstrap_template,
                            category=ArtifactCategory.SYSTEM_TARBALL,
                            display_name_prefix="Build tarball",
                            vendor=self.data.vendor,
                            codename=codename,
                            action_variables=action_variables,
                            architecture=architecture,
                        )

                    # Create new simplesystemimagebuild request if needed.
                    if (
                        target.simplesystemimagebuild_template is not None
                        and existing_child
                        not in existing_simplesystemimagebuild
                    ):
                        self._add_child(
                            group,
                            "simplesystemimagebuild",
                            SystemImageBuildData,
                            target.simplesystemimagebuild_template,
                            category=ArtifactCategory.SYSTEM_IMAGE,
                            display_name_prefix="Build image",
                            vendor=self.data.vendor,
                            codename=codename,
                            action_variables=action_variables,
                            architecture=architecture,
                        )

    def get_label(self) -> str:
        """Return the task label."""
        if len(self.data.targets) == 1:
            return f"update 1 {self.data.vendor} environment"
        else:
            return (
                f"update {len(self.data.targets)}"
                f" {self.data.vendor} environments"
            )
