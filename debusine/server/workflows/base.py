# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""
Base Workflow infrastructure.

See :ref:`explanation-workflows` for a high level explanation of concepts used
here.
"""
import logging
from abc import ABCMeta, abstractmethod
from typing import Any, TypeVar, TypedDict, cast

from django.contrib.auth.models import AnonymousUser

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.client.models import (
    LookupChildType,
    model_to_json_serializable_dict,
)
from debusine.db.models import (
    Collection,
    CollectionItem,
    TaskDatabase,
    User,
    WorkRequest,
    Workspace,
)
from debusine.db.models.work_requests import InternalTaskError
from debusine.server.collections.lookup import lookup_multiple, lookup_single
from debusine.server.tasks.base import BaseServerTask
from debusine.server.workflows.models import (
    BaseWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    ActionUpdateCollectionWithArtifacts,
    ActionUpdateCollectionWithData,
    BaseDynamicTaskData,
    BaseTaskData,
    LookupMultiple,
    LookupSingle,
    OutputData,
    OutputDataError,
    TaskTypes,
)
from debusine.tasks.server import TaskDatabaseInterface

logger = logging.getLogger(__name__)


WD = TypeVar("WD", bound=BaseWorkflowData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class WorkflowValidationError(Exception):
    """Raised if a workflow fails to validate its inputs."""


class WorkflowRunError(Exception):
    """Running a workflow orchestrator or callback failed."""

    def __init__(
        self, work_request: WorkRequest, message: str, code: str
    ) -> None:
        """Construct the exception."""
        self.work_request = work_request
        self.message = message
        self.code = code


class Workflow(BaseServerTask[WD, DTD], metaclass=ABCMeta):
    """
    Base class for workflow orchestrators.

    This is the base API for running :class:`WorkflowInstance` logic.
    """

    TASK_TYPE = TaskTypes.WORKFLOW

    work_request: WorkRequest
    workspace: Workspace

    def __init__(self, work_request: "WorkRequest"):
        """Instantiate a Workflow with its database instance."""
        super().__init__(
            task_data=work_request.used_task_data,
            dynamic_task_data=work_request.dynamic_task_data,
        )
        self.set_work_request(work_request)

    @classmethod
    def from_name(cls, name: str) -> type['Workflow[Any, Any]']:
        """Instantiate a workflow by name."""
        res = super().class_from_name(TaskTypes.WORKFLOW, name)
        return cast(type[Workflow[Any, Any]], res)

    @classmethod
    def validate_template_data(cls, data: dict[str, Any]) -> None:
        """Validate WorkflowTemplate data."""
        # By default nothing is enforced

    @classmethod
    def build_workflow_data(
        cls, template_data: dict[str, Any], user_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge template data and user-provided data."""
        # Data from the template has priority over user-provided.
        #
        # By default, merging of lists and dictionaries is not supported: only
        # toplevel keys are considered, and if a list or a dict exists both in
        # the user-provided data and in the template, the user-provided version
        # is ignored.
        #
        # Orchestrator subclasses may implement different merging strategies.
        data = user_data.copy()
        data.update(template_data)
        return data

    def validate_input(self) -> None:
        """
        Thorough validation of input data.

        This is run only once at workflow instantiation time, and can do
        slower things like database lookups to validate artifact or collection
        types.
        """
        # Do nothing by default

    def ensure_dynamic_data(self, task_database: TaskDatabaseInterface) -> None:
        """Ensure that this workflow's dynamic task data has been computed."""
        if self.work_request.dynamic_task_data is None:
            dynamic_task_data = self.compute_dynamic_data(task_database)
            self.work_request.dynamic_task_data = (
                model_to_json_serializable_dict(
                    dynamic_task_data, exclude_unset=True
                )
            )
            self.work_request.save()
            self.dynamic_data = self.dynamic_task_data_type(
                **self.work_request.dynamic_task_data
            )

    @abstractmethod
    def populate(self) -> None:
        """
        Create the initial WorkRequest structure.

        This is called once, when the workflow first becomes runnable.
        :py:meth:`validate_input` will already have been called.

        This method is required to be idempotent: calling it multiple times
        with the same argument MUST result in the same
        :py:class:`WorkRequest` structure as calling it once.
        """

    def callback(self, work_request: "WorkRequest") -> None:
        """
        Perform an orchestration step.

        Called with the workflow callback work request (note that this is
        not the same as ``self.work_request``) when the workflow node
        becomes ready to execute.

        Called with a :py:class:`WorkRequest` of type internal/workflow to
        perform an orchestration step triggered by a workflow callback.

        This method is required to be idempotent: calling it multiple times
        with the same argument MUST result in the same
        :py:class:`WorkRequest` structure as calling it once.
        """
        # Do nothing by default

    @staticmethod
    def provides_artifact(
        work_request: WorkRequest,
        category: ArtifactCategory,
        name: str,
        *,
        data: dict[str, Any] | None = None,
        artifact_filters: dict[str, Any] | None = None,  # noqa: U100
    ) -> None:
        """
        Indicate work_request will provide an artifact.

        :param work_request: work request that will provide the artifact
        :param category: category of the artifact that will be provided
        :param name: name of this item in the workflow’s internal collection
        :param data: add it to the data dictionary for the event reaction
        :param artifact_filters: for the
          :ref:`action-update-collection-with-artifacts` action, to allow
          workflows to add filtering
        :raise LookupError: if a key in "data" starts with ``promise_``

        Create an event reaction for ``on_creation`` adding a promise: this
        work request will create an artifact.

        Create an event reaction for ``on_success`` to update the collection
        with the relevant artifact.
        """
        if "/" in name:
            # This wouldn't work well, because "/" is used to separate
            # lookup string segments.
            raise ValueError('Collection item name may not contain "/".')

        if data is not None:
            for key, value in data.items():
                if key.startswith("promise_"):
                    raise ValueError(
                        f'Field name "{key}" starting with '
                        f'"promise_" is not allowed.'
                    )

        # work_request is part of a workflow
        assert work_request.parent is not None

        try:
            lookup_single(
                f"internal@collections/name:{name}",
                work_request.workspace,
                user=work_request.created_by or AnonymousUser(),
                workflow_root=work_request.get_workflow_root(),
                expect_type=LookupChildType.ARTIFACT_OR_PROMISE,
            )
        except KeyError:
            work_request.process_update_collection_with_data(
                [
                    ActionUpdateCollectionWithData(
                        collection="internal@collections",
                        name_template=name,
                        category=BareDataCategory.PROMISE,
                        data={
                            "promise_work_request_id": work_request.id,
                            "promise_workflow_id": work_request.parent.id,
                            "promise_category": category,
                            **(data or {}),
                        },
                    )
                ]
            )

        artifact_filters = artifact_filters or {}
        work_request.add_event_reaction(
            "on_success",
            ActionUpdateCollectionWithArtifacts(
                collection="internal@collections",
                name_template=name,
                variables=data,
                artifact_filters={**artifact_filters, "category": category},
            ),
        )

    @staticmethod
    def requires_artifact(
        work_request: WorkRequest, lookup: LookupSingle | LookupMultiple
    ) -> None:
        """
        Indicate that work_request requires input (lookup).

        :param work_request: for each lookup result call
          ``work_request.add_dependency(promise["promise_work_request_id"])``
        :param lookup: resolve the lookup and iterate over the results
          (for PROMISES only)
        """

        class LookupKwargs(TypedDict):
            """Dictionary containing keyword arguments for lookup functions."""

            workspace: Workspace
            user: User | AnonymousUser
            workflow_root: WorkRequest | None
            expect_type: LookupChildType

        lookup_kwargs: LookupKwargs = {
            "workspace": work_request.workspace,
            "user": work_request.created_by or AnonymousUser(),
            "workflow_root": work_request.get_workflow_root(),
            "expect_type": LookupChildType.ARTIFACT_OR_PROMISE,
        }

        if isinstance(lookup, LookupMultiple):
            results = lookup_multiple(lookup, **lookup_kwargs)
        else:
            results = [lookup_single(lookup, **lookup_kwargs)]

        for result in results:
            collection_item = result.collection_item

            if (
                collection_item is not None
                and collection_item.child_type == CollectionItem.Types.BARE
                and collection_item.category == BareDataCategory.PROMISE
            ):
                work_request_dependency = WorkRequest.objects.get(
                    id=collection_item.data["promise_work_request_id"]
                )
                work_request.add_dependency(work_request_dependency)

    def work_request_ensure_child(
        self,
        task_name: str,
        task_data: BaseTaskData,
        workflow_data: WorkRequestWorkflowData,
        task_type: TaskTypes = TaskTypes.WORKER,
    ) -> WorkRequest:
        """
        Create the child if one does not already exist.

        :return: new or existing :py:class:`WorkRequest`.
        """
        try:
            return self.work_request.children.get(
                task_name=task_name,
                task_data=model_to_json_serializable_dict(
                    task_data, exclude_unset=True
                ),
                workflow_data_json=workflow_data.dict(exclude_unset=True),
                task_type=task_type,
            )
        except WorkRequest.DoesNotExist:
            return self.work_request.create_child(
                task_name=task_name,
                task_data=task_data,
                workflow_data=workflow_data,
                task_type=task_type,
                # If we're creating a sub-workflow, then the parent must
                # already be running and therefore the child should be
                # unblocked.  In most cases dependencies will be handled at
                # the level of individual non-workflow work requests;
                # however, in some cases the sub-workflow itself needs a
                # dependency because it needs more information before being
                # able to create its own child work requests, so we leave
                # the sub-workflow pending to give the parent orchestrator a
                # chance to add those dependencies before marking the
                # sub-workflow running.
                status=(
                    WorkRequest.Statuses.PENDING
                    if task_type == TaskTypes.WORKFLOW
                    else WorkRequest.Statuses.BLOCKED
                ),
            )

    def lookup_singleton_collection(
        self,
        category: CollectionCategory,
        *,
        workspace: Workspace | None = None,
    ) -> Collection:
        """Look up a singleton collection related to this workflow."""
        return lookup_single(
            f"_@{category}",
            workspace=workspace or self.workspace,
            user=self.work_request.created_by,
            workflow_root=self.work_request.get_workflow_root(),
            expect_type=LookupChildType.COLLECTION,
        ).collection

    def _execute(self) -> bool:
        """Unused abstract method from BaseTask: populate() is used instead."""
        raise NotImplementedError()


def orchestrate_workflow(work_request: WorkRequest) -> None:
    """
    Orchestrate a workflow in whatever way is appropriate.

    For a workflow callback, run ``callback`` and mark the work request as
    completed.  For a workflow, run ``populate`` and unblock workflow
    children, but leave the workflow running until all its children have
    finished.  For any other work request, raise an error.
    """
    try:
        match (work_request.task_type, work_request.task_name):
            case (TaskTypes.INTERNAL, "workflow") | (TaskTypes.WORKFLOW, _):
                try:
                    orchestrator = work_request.get_task()
                except InternalTaskError as exc:
                    raise WorkflowRunError(
                        work_request, str(exc), "configure-failed"
                    ) from exc
                except TaskConfigError as exc:
                    raise WorkflowRunError(
                        work_request,
                        f"Failed to configure: {exc}",
                        "configure-failed",
                    ) from exc
                else:
                    assert isinstance(orchestrator, Workflow)

            case _:
                raise WorkflowRunError(
                    work_request,
                    "Does not have a workflow orchestrator",
                    "configure-failed",
                )

        match (work_request.task_type, work_request.task_name):
            case (TaskTypes.INTERNAL, "workflow"):
                parent = orchestrator.work_request
                try:
                    orchestrator.ensure_dynamic_data(TaskDatabase(parent))
                except Exception as exc:
                    # Workflow callbacks compute dynamic data for their
                    # parent workflow.  If this fails, it's probably least
                    # confusing to log information about both the workflow
                    # callback and the workflow.
                    raise WorkflowRunError(
                        work_request,
                        f"Failed to compute dynamic data for "
                        f"{parent.task_type}/{parent.task_name} ({parent.id}): "
                        f"{exc}",
                        "dynamic-data-failed",
                    ) from exc

                try:
                    orchestrator.callback(work_request)
                except Exception as exc:
                    raise WorkflowRunError(
                        work_request,
                        f"Orchestrator failed: {exc}",
                        "orchestrator-failed",
                    ) from exc

                work_request.mark_completed(WorkRequest.Results.SUCCESS)

            case (TaskTypes.WORKFLOW, _):
                try:
                    orchestrator.ensure_dynamic_data(
                        TaskDatabase(orchestrator.work_request)
                    )
                except Exception as exc:
                    raise WorkflowRunError(
                        work_request,
                        f"Failed to compute dynamic data: {exc}",
                        "dynamic-data-failed",
                    ) from exc

                match work_request.status:
                    case (
                        WorkRequest.Statuses.RUNNING
                        | WorkRequest.Statuses.COMPLETED
                        | WorkRequest.Statuses.ABORTED
                    ):
                        pass
                    case _:
                        raise WorkflowRunError(
                            work_request,
                            f"Work request is in status {work_request.status}, "
                            f"not running",
                            "wrong-status",
                        )

                try:
                    orchestrator.populate()
                except Exception as exc:
                    raise WorkflowRunError(
                        work_request,
                        f"Orchestrator failed: {exc}",
                        "orchestrator-failed",
                    ) from exc

                if work_request.children.exists():
                    orchestrator.work_request.unblock_workflow_children()
                else:
                    work_request.mark_completed(WorkRequest.Results.SUCCESS)

                # The workflow is left running until all its children have
                # finished.

            case _:  # pragma: no cover
                # Already checked above.
                raise AssertionError(
                    f"Unexpected work request: "
                    f"{work_request.task_type}/{work_request.task_name}"
                )

    except WorkflowRunError as exc:
        logger.warning(
            "Error running work request %s/%s (%s): %s",
            exc.work_request.task_type,
            exc.work_request.task_name,
            exc.work_request.id,
            exc.message,
        )
        work_request.mark_completed(
            WorkRequest.Results.ERROR,
            output_data=OutputData(
                errors=[OutputDataError(message=exc.message, code=exc.code)]
            ),
        )
        raise
