# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine dput-ng hooks."""

from typing import Any
from urllib.parse import quote, urljoin

from dput.changes import Changes
from dput.core import logger
from dput.exceptions import HookException
from dput.interface import AbstractInterface

from debusine.client.dput_ng.dput_ng_utils import make_debusine_client
from debusine.client.models import CreateWorkflowRequest


class NoWorkflowError(HookException):  # type: ignore[misc]
    """No debusine workflow is configured for this distribution."""


def choose_workflow(changes: Changes, profile: dict[str, Any]) -> str:
    """
    Select the debusine workflow to create after the upload.

    :raises NoWorkflowError: if we do not know what workflow to create.
    """
    if isinstance((workflow_name := profile.get("debusine_workflow")), str):
        return workflow_name
    distribution = changes["Distribution"]
    try:
        workflow_template_name = profile["debusine_workflows_by_distribution"][
            distribution
        ]
        assert isinstance(workflow_template_name, str)
        return workflow_template_name
    except KeyError:
        raise NoWorkflowError(
            f"No debusine workflow is configured for {distribution!r}"
        )


def check_workflow(
    changes: Changes,
    profile: dict[str, Any],
    interface: AbstractInterface,  # noqa: U100
) -> None:
    """Check that we know what debusine workflow to create."""
    choose_workflow(changes, profile)


def create_workflow(
    changes: Changes,
    profile: dict[str, Any],
    interface: AbstractInterface,  # noqa: U100
) -> None:
    """Create a workflow based on an upload to debusine."""
    scope = profile["debusine_scope"]
    workspace = profile["debusine_workspace"]
    try:
        workflow_template_name = choose_workflow(changes, profile)
    except NoWorkflowError:
        # Give up; this should already have been checked by a pre-upload hook.
        return
    task_data = profile.get("debusine_workflow_data", {})
    for key in task_data:
        if task_data[key] == "@UPLOAD@":
            task_data[key] = profile["debusine_artifact_ids"]["changes"]
    debusine = make_debusine_client(profile)
    workflow = CreateWorkflowRequest(
        template_name=workflow_template_name,
        workspace=workspace,
        task_data=task_data,
    )
    try:
        workflow_created = debusine.workflow_create(workflow)
    except Exception as e:
        raise HookException(str(e)) from e
    logger.info(
        "Created workflow: %s",
        # TODO: Ideally we wouldn't have to hardcode the URL structure here.
        urljoin(
            debusine.base_api_url,
            f"/{quote(scope)}/{quote(workspace)}/work-request"
            f"/{workflow_created.id}/",
        ),
    )
