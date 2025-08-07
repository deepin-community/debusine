# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the work request models."""

import io
import logging
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal
from unittest import mock

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebusinePromise,
    DebusineTaskConfiguration,
    RuntimeStatistics,
)
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    Scope,
    TaskDatabase,
    Token,
    WorkRequest,
    Worker,
    WorkerPoolTaskExecutionStatistics,
    WorkflowTemplate,
    Workspace,
    default_workspace,
)
from debusine.db.models.permissions import PartialCheckResult
from debusine.db.models.work_requests import (
    CannotRetry,
    DeleteUnused,
    InternalTaskError,
    MAX_AUTOMATIC_RETRIES,
    WorkRequestRetryReason,
    compute_workflow_runtime_status,
    workflow_flattened,
)
from debusine.db.playground import scenarios
from debusine.server.collections import CollectionManagerInterface
from debusine.server.collections.debusine_task_configuration import (
    DebusineTaskConfigurationManager,
)
from debusine.server.management.commands.delete_expired import (
    DeleteExpiredWorkRequests,
    DeleteOperation,
)
from debusine.server.tasks import ServerNoop
from debusine.server.tasks.tests.helpers import TestBaseWaitTask
from debusine.server.tasks.wait import Delay
from debusine.server.workflows import NoopWorkflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.tests.helpers import TestWorkflow
from debusine.signing.tasks import SigningNoop
from debusine.tasks import Noop, TaskConfigError
from debusine.tasks.models import (
    ActionRecordInTaskHistory,
    ActionRetryWithDelays,
    ActionSendNotification,
    ActionTypes,
    ActionUpdateCollectionWithArtifacts,
    ActionUpdateCollectionWithData,
    BaseDynamicTaskData,
    BaseTaskData,
    EventReaction,
    EventReactions,
    NoopData,
    OutputData,
    SbuildData,
    SbuildInput,
    TaskTypes,
)
from debusine.tasks.server import TaskDatabaseInterface
from debusine.test.django import (
    AllowAll,
    ChannelsHelpersMixin,
    DenyAll,
    TestCase,
    override_permission,
)
from debusine.test.test_utils import (
    create_system_tarball_data,
    preserve_task_registry,
)


class TestWorkflowData(BaseWorkflowData):
    """Workflow data to test instantiation."""

    ivar: int = 1
    dvar: dict[str, Any] = pydantic.Field(default_factory=dict)


class WorkRequestManagerTestsTestWorkflow(
    TestWorkflow[TestWorkflowData, BaseDynamicTaskData]
):
    """Workflow used to test instantiation of data."""

    # This needs a unique name to avoid conflicting with other tests, because
    # it gets registered as a Workflow subclass

    def validate_input(self) -> None:
        """Allow testing thorough validation of input data."""
        if "sentinel" in self.data.dvar:
            raise ValueError("Sentinel found")

    def populate(self) -> None:
        """Unused abstract method from Workflow."""
        raise NotImplementedError()


class WorkRequestManagerTests(TestCase):
    """Tests for WorkRequestManager."""

    scenario = scenarios.DefaultContext()

    def test_create_workflow(self) -> None:
        """Workflows are created correctly."""
        user = self.playground.get_default_user()

        workflow_name = "workrequestmanagerteststestworkflow"

        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name=workflow_name,
            task_data={},
            priority=10,
        )

        wr = self.playground.create_workflow(template, task_data={})
        self.assertEqual(wr.workspace, template.workspace)
        self.assertIsNone(wr.parent)
        self.assertEqual(wr.workflow_template, template)
        self.assertEqual(
            wr.workflow_data_json, {"workflow_template_name": "test"}
        )
        self.assertEqual(wr.created_by, user)
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr.task_type, TaskTypes.WORKFLOW)
        self.assertEqual(wr.task_name, workflow_name)
        self.assertEqual(wr.task_data, {})
        self.assertEqual(wr.priority_base, 10)
        self.assertIsNone(wr.output_data)
        self.assertQuerySetEqual(wr.dependencies.all(), [])
        assert wr.internal_collection is not None
        self.assertEqual(wr.internal_collection.name, f"workflow-{wr.id}")
        self.assertEqual(
            wr.internal_collection.category,
            CollectionCategory.WORKFLOW_INTERNAL,
        )
        self.assertEqual(wr.internal_collection.workspace, template.workspace)
        self.assertEqual(
            wr.internal_collection.retains_artifacts,
            Collection.RetainsArtifacts.WORKFLOW,
        )
        self.assertEqual(wr.internal_collection.workflow, wr)

    def test_create_workflow_with_parent(self) -> None:
        """A sub-workflow does not have its own internal collection."""
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name="workrequestmanagerteststestworkflow",
        )
        root = self.playground.create_workflow(template, task_data={})
        sub_template = WorkflowTemplate.objects.create(
            name="subtest", workspace=default_workspace(), task_name="noop"
        )
        sub = self.playground.create_workflow(
            sub_template, task_data={}, parent=root
        )

        self.assertEqual(sub.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(sub.parent, root)
        self.assertEqual(sub.workflow_template, sub_template)
        self.assertEqual(
            sub.workflow_data_json, {"workflow_template_name": "subtest"}
        )
        self.assertQuerySetEqual(sub.dependencies.all(), [])
        self.assertIsNone(sub.internal_collection)

    def test_create_workflow_with_parms(self) -> None:
        """Check that WorkflowTemplate parameters have precedence."""
        workflow_name = "workrequestmanagerteststestworkflow"

        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name=workflow_name,
            task_data={"ivar": 2, "dvar": {"a": 1}},
        )

        wr = self.playground.create_workflow(template, task_data={})
        self.assertEqual(wr.task_data, {"ivar": 2, "dvar": {"a": 1}})

        wr = self.playground.create_workflow(
            template, task_data={"ivar": 3, "dvar": {"b": 2}}
        )
        self.assertEqual(wr.task_data, {"ivar": 2, "dvar": {"a": 1}})

        template.task_data = {"ivar": 1}
        wr = self.playground.create_workflow(
            template, task_data={"ivar": 3, "dvar": {"b": 2}}
        )
        self.assertEqual(wr.task_data, {"ivar": 1, "dvar": {"b": 2}})

    def test_create_workflow_basic_validation(self) -> None:
        """Workflow parameters are validated against their model."""
        workflow_name = "workrequestmanagerteststestworkflow"

        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name=workflow_name,
            task_data={},
        )

        with self.assertRaisesRegex(
            TaskConfigError, "extra fields not permitted"
        ):
            self.playground.create_workflow(template, task_data={"noise": 1})

    def test_create_workflow_thorough_validation(self) -> None:
        """Check validation of parameters on workflow instantiation."""
        workflow_name = "workrequestmanagerteststestworkflow"

        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name=workflow_name,
            task_data={},
        )

        with self.assertRaisesRegex(ValueError, "Sentinel found"):
            self.playground.create_workflow(
                template, task_data={"dvar": {"sentinel": True}}
            )

    def test_create_synchronization_point(self) -> None:
        """Synchronization points are created correctly."""
        user = self.playground.get_default_user()

        parent = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=user,
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
            task_data={},
            priority_base=5,
            priority_adjustment=1,
        )

        wr = WorkRequest.objects.create_synchronization_point(
            parent=parent, step="test"
        )
        self.assertEqual(wr.workspace, parent.workspace)
        self.assertEqual(wr.parent, parent)
        self.assertIsNone(wr.workflow_template)
        self.assertIsNotNone(wr.created_at)
        self.assertIsNone(wr.started_at)
        self.assertIsNone(wr.completed_at)
        self.assertEqual(wr.created_by, user)
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)
        self.assertIsNone(wr.worker)
        self.assertEqual(wr.task_type, TaskTypes.INTERNAL)
        self.assertEqual(wr.task_name, "synchronization_point")
        self.assertEqual(wr.task_data, {})
        self.assertEqual(wr.priority_base, 6)
        self.assertIsNone(wr.output_data)
        self.assertQuerySetEqual(wr.dependencies.all(), [])
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(step="test", display_name="test"),
        )
        wr.full_clean()

        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(name="test")

        parent = WorkRequest.objects.create(
            workspace=workspace,
            created_by=user,
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
            task_data={},
        )
        wr = WorkRequest.objects.create_synchronization_point(
            parent=parent,
            step="test",
            display_name="Test",
            status=WorkRequest.Statuses.PENDING,
        )
        self.assertEqual(wr.workspace, workspace)
        self.assertEqual(wr.parent, parent)
        self.assertIsNotNone(wr.created_at)
        self.assertIsNone(wr.started_at)
        self.assertIsNone(wr.completed_at)
        self.assertEqual(wr.created_by, user)
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)
        self.assertIsNone(wr.worker)
        self.assertEqual(wr.task_type, TaskTypes.INTERNAL)
        self.assertEqual(wr.task_name, "synchronization_point")
        self.assertEqual(wr.task_data, {})
        self.assertIsNone(wr.output_data)
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(step="test", display_name="Test"),
        )
        wr.full_clean()

    def test_create_workflow_callback(self) -> None:
        """Workflow callbacks are created correctly."""
        user = self.playground.get_default_user()

        parent = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=user,
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
            task_data={},
        )

        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )
        self.assertEqual(wr.workspace, parent.workspace)
        self.assertEqual(wr.parent, parent)
        self.assertIsNone(wr.workflow_template)
        self.assertIsNotNone(wr.created_at)
        self.assertIsNone(wr.started_at)
        self.assertIsNone(wr.completed_at)
        self.assertEqual(wr.created_by, user)
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)
        self.assertIsNone(wr.worker)
        self.assertEqual(wr.task_type, TaskTypes.INTERNAL)
        self.assertEqual(wr.task_name, "workflow")
        self.assertEqual(wr.task_data, {})
        self.assertEqual(wr.priority_base, 0)
        self.assertIsNone(wr.output_data)
        self.assertQuerySetEqual(wr.dependencies.all(), [])
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(step="test", display_name="test"),
        )
        wr.full_clean()

        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(name="test")

        parent = WorkRequest.objects.create(
            workspace=workspace,
            created_by=user,
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
            task_data={},
            priority_base=20,
            priority_adjustment=10,
        )
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent,
            status=WorkRequest.Statuses.PENDING,
            step="test1",
            display_name="Test One",
        )
        self.assertEqual(wr.workspace, workspace)
        self.assertEqual(wr.parent, parent)
        self.assertIsNotNone(wr.created_at)
        self.assertIsNone(wr.started_at)
        self.assertIsNone(wr.completed_at)
        self.assertEqual(wr.created_by, user)
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)
        self.assertIsNone(wr.worker)
        self.assertEqual(wr.task_type, TaskTypes.INTERNAL)
        self.assertEqual(wr.task_name, "workflow")
        self.assertEqual(wr.task_data, {})
        self.assertEqual(wr.priority_base, 30)
        self.assertIsNone(wr.output_data)
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(step="test1", display_name="Test One"),
        )
        wr.full_clean()

    def test_pending(self) -> None:
        """WorkRequestManager.pending() returns pending WorkRequests."""
        work_request_1 = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING
        )

        work_request_2 = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING
        )

        self.playground.create_work_request(status=WorkRequest.Statuses.ABORTED)

        self.assertQuerySetEqual(
            WorkRequest.objects.pending(), [work_request_1, work_request_2]
        )

        work_request_1.created_at = timezone.now()
        work_request_1.save()

        self.assertQuerySetEqual(
            WorkRequest.objects.pending(), [work_request_2, work_request_1]
        )

        work_request_1.priority_adjustment = 1
        work_request_1.save()

        self.assertQuerySetEqual(
            WorkRequest.objects.pending(), [work_request_1, work_request_2]
        )

        work_request_2.priority_base = 2
        work_request_2.save()

        self.assertQuerySetEqual(
            WorkRequest.objects.pending(), [work_request_2, work_request_1]
        )

    def test_pending_filter_by_worker(self) -> None:
        """WorkRequestManager.pending() returns WorkRequest for the worker."""
        self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING, task_name="sbuild"
        )

        worker = Worker.objects.create_with_fqdn(
            "computer.lan", Token.objects.create()
        )

        work_request_2 = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING, worker=worker
        )

        self.assertQuerySetEqual(
            WorkRequest.objects.pending(worker=worker), [work_request_2]
        )

    def test_raise_value_error_exclude_assigned_and_worker(self) -> None:
        """WorkRequestManager.pending() raises ValueError."""
        worker = Worker.objects.create_with_fqdn(
            "computer.lan", Token.objects.create()
        )

        with self.assertRaisesRegex(
            ValueError, "Cannot exclude_assigned and filter by worker"
        ):
            WorkRequest.objects.pending(exclude_assigned=True, worker=worker)

    def test_pending_exclude_assigned(self) -> None:
        """
        Test WorkRequestManager.pending(exclude_assigned=True).

        It excludes work requests that are assigned to a worker.
        """
        # Pending, not assigned to a worker WorkRequest
        work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING, task_name="sbuild"
        )

        # Is returned as expected
        self.assertQuerySetEqual(
            WorkRequest.objects.pending(exclude_assigned=True), [work_request]
        )

        # Creates a worker
        worker = Worker.objects.create_with_fqdn(
            'test', token=Token.objects.create()
        )

        # Assigns the worker to the work_request
        work_request.worker = worker
        work_request.save()

        # pending(exclude_assigned=True) doesn't return it anymore
        self.assertQuerySetEqual(
            WorkRequest.objects.pending(exclude_assigned=True), []
        )

        # without the exclude_assigned it returns it
        self.assertQuerySetEqual(
            WorkRequest.objects.pending(exclude_assigned=False), [work_request]
        )

    def test_running(self) -> None:
        """WorkRequestManager.running() returns running WorkRequests."""
        work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.RUNNING
        )
        self.playground.create_work_request(status=WorkRequest.Statuses.ABORTED)

        self.assertQuerySetEqual(WorkRequest.objects.running(), [work_request])

    def test_completed(self) -> None:
        """WorkRequestManager.completed() returns completed WorkRequests."""
        work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.COMPLETED
        )
        self.playground.create_work_request(status=WorkRequest.Statuses.RUNNING)

        self.assertQuerySetEqual(
            WorkRequest.objects.completed(), [work_request]
        )

    def test_aborted(self) -> None:
        """WorkRequestManager.aborted() returns aborted WorkRequests."""
        work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.ABORTED
        )
        self.playground.create_work_request(status=WorkRequest.Statuses.RUNNING)

        self.assertQuerySetEqual(WorkRequest.objects.aborted(), [work_request])

    def test_aborted_or_failed(self) -> None:
        """WorkRequestManager.aborted() returns aborted WorkRequests."""
        wr_aborted = self.playground.create_work_request(
            status=WorkRequest.Statuses.ABORTED
        )
        wr_failed = self.playground.create_work_request(
            result=WorkRequest.Results.FAILURE,
        )
        self.playground.create_work_request(status=WorkRequest.Statuses.RUNNING)

        self.assertQuerySetEqual(
            WorkRequest.objects.aborted_or_failed(), [wr_aborted, wr_failed]
        )

    def test_expired(self) -> None:
        """Test expired() method."""
        wr = self.playground.create_work_request(task_name="noop")

        wr.created_at = timezone.now() - timedelta(days=2)
        wr.expiration_delay = timedelta(days=1)
        wr.save()
        self.assertQuerySetEqual(
            WorkRequest.objects.expired(timezone.now()), [wr]
        )

        wr.expiration_delay = timedelta(0)
        wr.save()
        self.assertQuerySetEqual(
            WorkRequest.objects.expired(timezone.now()), []
        )

        wr.created_at = timezone.now()
        wr.expiration_delay = timedelta(days=1)
        wr.save()
        self.assertQuerySetEqual(
            WorkRequest.objects.expired(timezone.now()), []
        )

        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(name="test")
            workspace.default_expiration_delay = timedelta(days=2)
            workspace.save()
        wr.workspace = workspace
        wr.created_at = timezone.now() - timedelta(days=3)
        wr.expiration_delay = None
        wr.save()
        self.assertQuerySetEqual(
            WorkRequest.objects.expired(timezone.now()), [wr]
        )

        workspace.default_expiration_delay = timedelta(days=5)
        workspace.save()
        self.assertQuerySetEqual(
            WorkRequest.objects.expired(timezone.now()), []
        )

        workspace.default_expiration_delay = timedelta(days=0)
        workspace.save()
        self.assertQuerySetEqual(
            WorkRequest.objects.expired(timezone.now()), []
        )

    def test_in_current_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter."""
        work_request = self.playground.create_work_request()
        with context.local():
            context.set_scope(self.scenario.scope)
            self.assertQuerySetEqual(
                WorkRequest.objects.in_current_scope(),
                [work_request],
            )

        scope1 = Scope.objects.create(name="Scope1")
        with context.local():
            context.set_scope(scope1)
            self.assertQuerySetEqual(
                WorkRequest.objects.in_current_scope(),
                [],
            )

    def test_in_current_scope_no_context_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter without scope set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "scope is not set"
        ):
            WorkRequest.objects.in_current_scope()

    def test_in_current_workspace(self) -> None:
        """Test the in_current_workspace() QuerySet filter."""
        work_request = self.playground.create_work_request()
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)

        with context.local():
            self.scenario.workspace.set_current()
            self.assertQuerySetEqual(
                WorkRequest.objects.in_current_workspace(),
                [work_request],
            )

        workspace1 = self.playground.create_workspace(name="other", public=True)
        with context.local():
            workspace1.set_current()
            self.assertQuerySetEqual(
                WorkRequest.objects.in_current_workspace(),
                [],
            )

    def test_in_current_workspace_no_context_workspace(self) -> None:
        """Test in_current_workspace() without workspace set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "workspace is not set"
        ):
            WorkRequest.objects.in_current_workspace()


class WorkRequestTests(TestCase):
    """Tests for the WorkRequest class."""

    scenario = scenarios.DefaultContext()
    template: ClassVar[WorkflowTemplate]
    workflow: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Add a sample user to the test fixture."""
        super().setUpTestData()
        cls.template = cls.playground.create_workflow_template(
            name="test",
            task_name="noop",
        )
        cls.workflow = cls.playground.create_workflow(
            cls.template, task_data={}
        )

    def assert_data_must_be_dictionary(
        self, wr: WorkRequest, messages: list[str] | None = None
    ) -> None:
        """Check that a WorkRequest's task_data is a dictionary."""
        if messages is None:
            messages = ["task data must be a dictionary"]
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"task_data": messages},
        )

    def test_clean_task_data_must_be_dict(self) -> None:
        """Ensure that task_data is a dict."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_name="noop",
            task_data={},
        )
        wr.full_clean()

        wr.task_data = None
        self.assert_data_must_be_dictionary(wr)

        wr.task_data = ""
        self.assert_data_must_be_dictionary(wr)

        wr.task_data = 3
        self.assert_data_must_be_dictionary(wr)

        wr.task_data = []
        self.assert_data_must_be_dictionary(wr)

        wr.task_data = object()
        self.assert_data_must_be_dictionary(
            wr,
            messages=[
                'Value must be valid JSON.',
                'task data must be a dictionary',
            ],
        )

    def test_clean_task_name_must_be_valid_worker(self) -> None:
        """Check validation of worker task names."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_name="noop",
            task_data={},
        )
        wr.full_clean()

        wr.task_name = "does-not-exist"
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()

        self.assertEqual(
            raised.exception.message_dict,
            {"task_name": ['does-not-exist: invalid Worker task name']},
        )

        wr.task_name = "does_not_exist"
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()

        self.assertEqual(
            raised.exception.message_dict,
            {
                "task_name": [
                    "does_not_exist: invalid Worker task name",
                ]
            },
        )

    def test_clean_task_name_must_be_valid_server(self) -> None:
        """Check validation of server task names."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_type=TaskTypes.SERVER,
            task_name="servernoop",
            task_data={},
        )
        wr.full_clean()

        wr.task_name = "does-not-exist"
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()

        self.assertEqual(
            raised.exception.message_dict,
            {"task_name": ['does-not-exist: invalid Server task name']},
        )

    def test_clean_task_name_must_be_valid_internal(self) -> None:
        """Check validation of internal task names."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_type=TaskTypes.INTERNAL,
            task_name="synchronization_point",
            task_data={},
        )
        wr.full_clean()

        wr.task_name = "workflow"
        wr.full_clean()

        wr.task_name = "does_not_exist"
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {
                "task_name": [
                    "does_not_exist: invalid task name for internal task"
                ]
            },
        )

    def test_clean_task_name_must_be_valid_workflow(self) -> None:
        """Check validation of workflow task names."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_type=TaskTypes.WORKFLOW,
            task_name="servernoop",
            task_data={},
        )
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"task_name": ["servernoop: invalid workflow name"]},
        )

        wr.task_name = "noop"
        wr.full_clean()

    def test_clean_task_name_must_be_valid_signing(self) -> None:
        """Check validation of signing task names."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_type=TaskTypes.SIGNING,
            task_name="generatekey",
            task_data={"purpose": "uefi", "description": "A UEFI key"},
        )
        wr.full_clean()

        wr.task_name = "does-not-exist"
        with self.assertRaises(ValidationError) as raised:
            wr.full_clean()

        self.assertEqual(
            raised.exception.message_dict,
            {"task_name": ['does-not-exist: invalid Signing task name']},
        )

    def test_clean_invalid_task_type(self) -> None:
        """An invalid work request task type is rejected."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.scenario.user,
            task_type="invalid",
            task_name="noop",
            task_data={},
        )
        with self.assertRaises(NotImplementedError):
            wr.full_clean()

    def test_clean_data_task_data_raise_validation_error(self) -> None:
        """Check validation of task data."""

        def make_task_data() -> dict[str, Any]:
            return {
                "build_components": ["any", "all"],
                "host_architecture": "amd64",
                "environment": (
                    "debian/match:codename=bookworm:architecture=amd64"
                ),
                "input": {
                    "source_artifact": 5,
                },
            }

        wr = WorkRequest.objects.create(
            created_by=self.scenario.user,
            workspace=default_workspace(),
            task_name="sbuild",
            task_data=make_task_data(),
        )
        wr.full_clean()

        wr.task_data = make_task_data()
        wr.task_data["invalid-key"] = None
        with self.assertRaisesRegex(
            ValidationError,
            "extra fields not permitted",
        ):
            wr.full_clean()

        wr.task_data = make_task_data()
        del wr.task_data["environment"]
        with self.assertRaisesRegex(
            ValidationError,
            r'environment\\n\s+field required \(type=value_error\.missing\)',
        ):
            wr.full_clean()

    def test_clean_workflow_data_raise_validation_error(self) -> None:
        """Check validation of workflow data."""
        wr = WorkRequest.objects.create(
            created_by=self.scenario.user,
            workspace=default_workspace(),
            task_type=TaskTypes.WORKFLOW,
            task_name="noop",
            task_data={},
        )
        wr.full_clean()

        wr.task_data["spurious"] = True
        with self.assertRaisesRegex(
            ValidationError,
            "extra fields not permitted",
        ):
            wr.full_clean()

    def test_wait_without_needs_input_raise_validation_error(self) -> None:
        """WAIT tasks must specify needs_input in their workflow_data."""
        wr = WorkRequest.objects.create(
            created_by=self.scenario.user,
            workspace=self.scenario.workspace,
            task_type=TaskTypes.WAIT,
            task_name="delay",
            task_data={"delay_until": timezone.now().isoformat()},
            workflow_data_json={"needs_input": False},
        )
        wr.full_clean()

        wr.workflow_data_json = {}
        with self.assertRaisesRegex(
            ValidationError, "WAIT tasks must specify needs_input"
        ):
            wr.full_clean()

    def test_workflow_data_accessors(self) -> None:
        """Test the custom pydantic accessors for workflow_data."""
        common_args = {
            "created_by": self.scenario.user,
            "workspace": default_workspace(),
            "task_type": TaskTypes.WORKER,
            "task_name": "noop",
            "task_data": {},
        }

        # Empty workflow_data
        wr = WorkRequest.objects.create(**common_args)
        self.assertEqual(wr.workflow_data_json, {})
        self.assertEqual(wr.workflow_data, WorkRequestWorkflowData())

        wr = WorkRequest.objects.create(**common_args)
        self.assertEqual(wr.workflow_data_json, {})
        self.assertEqual(wr.workflow_data, WorkRequestWorkflowData())

        # Filled workflow data
        wr = WorkRequest.objects.create(
            workflow_data_json={"display_name": "Test", "step": "test"},
            **common_args,
        )
        self.assertEqual(
            wr.workflow_data_json,
            {"display_name": "Test", "step": "test"},
        )
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(display_name="Test", step="test"),
        )

        wr.workflow_data = WorkRequestWorkflowData()
        self.assertEqual(wr.workflow_data_json, {})
        self.assertEqual(wr.workflow_data, WorkRequestWorkflowData())

        wr.workflow_data = WorkRequestWorkflowData(
            display_name="Test1", step="test1"
        )
        self.assertEqual(
            wr.workflow_data_json,
            {"display_name": "Test1", "step": "test1"},
        )
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(display_name="Test1", step="test1"),
        )

    def test_supersedes(self) -> None:
        """Test behaviour of the supersedes field."""
        wr = self.playground.create_work_request(task_name="noop")
        self.assertIsNone(wr.supersedes)
        self.assertFalse(hasattr(wr, "superseded"))

        wr1 = self.playground.create_work_request(task_name="noop")
        wr1.supersedes = wr
        wr1.save()

        wr.refresh_from_db()
        wr1.refresh_from_db()

        self.assertIsNone(wr.supersedes)
        self.assertEqual(wr1.supersedes, wr)
        self.assertEqual(wr.superseded, wr1)
        self.assertFalse(hasattr(wr1, "superseded"))

        self.assertQuerySetEqual(
            WorkRequest.objects.filter(superseded__isnull=False), [wr]
        )
        self.assertQuerySetEqual(
            WorkRequest.objects.filter(supersedes__isnull=False), [wr1]
        )

    def test_supersedes_expired(self) -> None:
        """Test expiring a superseded work request."""
        wr = self.playground.create_work_request(task_name="noop")
        wr.created_at = timezone.now() - timedelta(days=365)
        wr.expiration_delay = timedelta(days=7)
        wr.save()
        wr1 = self.playground.create_work_request(task_name="noop")
        wr1.supersedes = wr
        wr1.save()

        # Expire the superseded work request
        with (
            io.StringIO() as stdout,
            io.StringIO() as stderr,
            DeleteOperation(out=stdout, err=stderr, dry_run=False) as operation,
        ):
            delete_expired = DeleteExpiredWorkRequests(operation)
            delete_expired.run()

        self.assertQuerySetEqual(WorkRequest.objects.filter(pk=wr.pk), [])
        wr1.refresh_from_db()

        # Supersedes is set to None
        self.assertIsNone(wr1.supersedes)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_verify_retry_superseded(self) -> None:
        """Superseded work requests cannot be retried."""
        wr = self.workflow.create_child("noop")
        wr.status = WorkRequest.Statuses.ABORTED
        wr.save()
        self.assertTrue(wr.verify_retry())

        wr1 = self.workflow.create_child("noop")
        wr1.supersedes = wr
        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(
            CannotRetry, r"^Cannot retry old superseded tasks$"
        ):
            wr.retry()

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_verify_retry_aborted_failed(self) -> None:
        """Only aborted/failed work requests can be retried."""
        wr = self.workflow.create_child("noop")
        wr.status = WorkRequest.Statuses.COMPLETED
        wr.result = WorkRequest.Results.SUCCESS
        wr.save()

        msg = r"^Only aborted or failed tasks can be retried$"

        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

        wr.status = WorkRequest.Statuses.COMPLETED
        wr.result = WorkRequest.Results.ERROR
        wr.save()
        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

        wr.status = WorkRequest.Statuses.PENDING
        wr.result = WorkRequest.Results.NONE
        wr.save()
        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

        wr.status = WorkRequest.Statuses.RUNNING
        wr.result = WorkRequest.Results.NONE
        wr.save()
        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

        wr.status = WorkRequest.Statuses.BLOCKED
        wr.result = WorkRequest.Results.NONE
        wr.save()
        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_verify_retry_task_type(self) -> None:
        """Only some task types can be retried."""
        wr = self.workflow.create_child("noop")
        wr.status = WorkRequest.Statuses.COMPLETED
        wr.result = WorkRequest.Results.SUCCESS
        wr.save()

        msg = (
            r"^Only workflow, worker, internal, or signing tasks"
            " can be retried$"
        )

        wr.task_type = TaskTypes.SERVER
        wr.status = WorkRequest.Statuses.ABORTED
        wr.result = WorkRequest.Results.NONE
        wr.save()
        self.assertFalse(wr.verify_retry())
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_verify_retry_invalid_task_data(self) -> None:
        """Work request with invalid task data cannot be retried."""
        wr = self.workflow.create_child("noop")
        wr.status = WorkRequest.Statuses.ABORTED
        wr.result = WorkRequest.Results.NONE
        wr.task_name = "sbuild"
        wr.save()
        self.assertFalse(wr.verify_retry())
        msg = "^Task dependencies cannot be satisfied$"
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

    @preserve_task_registry()
    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_verify_retry_if_lookups_fail(self) -> None:
        """Work requests with unsatisfiable lookups can't retry."""
        wr = self.workflow.create_child("noop")
        wr.status = WorkRequest.Statuses.ABORTED
        wr.result = WorkRequest.Results.NONE
        wr.save()

        collection = self.playground.create_collection(
            name="debian",
            category=CollectionCategory.ENVIRONMENTS,
        )

        class LookupTask(Noop):
            """Test task that performs a lookup."""

            def compute_dynamic_data(
                self, task_database: TaskDatabaseInterface
            ) -> BaseDynamicTaskData:
                """Perform a lookup."""
                task_database.lookup_single_artifact(
                    "debian@debian:environments/match:codename=bookworm"
                )
                return BaseDynamicTaskData()

        wr = self.playground.create_work_request(
            task_name="lookuptask",
            task_data=NoopData(),
            status=WorkRequest.Statuses.ABORTED,
        )

        self.assertFalse(wr.verify_retry())
        msg = r"^Task dependencies cannot be satisfied$"
        with self.assertRaisesRegex(CannotRetry, msg):
            wr.retry()

        # Create the looked up artifact
        with context.disable_permission_checks():
            tarball, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL,
                data=create_system_tarball_data(
                    codename="bookworm", architecture="amd64"
                ),
            )
        collection.manager.add_artifact(tarball, user=self.scenario.user)

        self.assertTrue(wr.verify_retry())
        wr.retry()

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede(self) -> None:
        """Test behaviour of retry, superseding work requests."""
        # An aborted workflow, with a failed task and one depending on it
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()

        wr_failed = self.workflow.create_child("noop")
        wr_failed.status = WorkRequest.Statuses.COMPLETED
        wr_failed.result = WorkRequest.Results.FAILURE
        wr_failed.save()

        # Two reverse dependencies
        wr_dep1 = self.workflow.create_child(
            "noop", status=WorkRequest.Statuses.PENDING
        )
        wr_dep1.dependencies.add(wr_failed)
        wr_dep2 = self.workflow.create_child(
            "noop", status=WorkRequest.Statuses.COMPLETED
        )
        wr_dep2.dependencies.add(wr_failed)
        # One forward dependency
        wr_fdep1 = self.workflow.create_child(
            "noop", status=WorkRequest.Statuses.COMPLETED
        )
        wr_failed.dependencies.add(wr_fdep1)

        # wr_failed gets retried
        self.assertTrue(wr_failed.verify_retry())
        wr_new = wr_failed.retry(reason=WorkRequestRetryReason.WORKER_FAILED)

        self.assertEqual(wr_new.workspace, wr_failed.workspace)
        self.assertGreater(wr_new.created_at, wr_failed.created_at)
        self.assertIsNone(wr_new.started_at)
        self.assertIsNone(wr_new.completed_at)
        self.assertEqual(wr_new.created_by, wr_failed.created_by)
        self.assertEqual(wr_new.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr_new.result, WorkRequest.Results.NONE)
        self.assertIsNone(wr_new.worker)
        self.assertEqual(wr_new.task_type, wr_failed.task_type)
        self.assertEqual(wr_new.task_name, wr_failed.task_name)
        self.assertEqual(wr_new.task_data, wr_failed.task_data)
        self.assertIsNone(wr_new.dynamic_task_data)
        self.assertEqual(wr_new.priority_base, wr_failed.priority_base)
        self.assertEqual(
            wr_new.priority_adjustment, wr_failed.priority_adjustment
        )
        self.assertIsNone(wr_new.output_data_json)
        self.assertEqual(wr_new.unblock_strategy, wr_failed.unblock_strategy)
        self.assertEqual(wr_new.parent, self.workflow)
        self.assertEqual(wr_new.workflow_template, wr_failed.workflow_template)
        self.assertEqual(
            wr_new.workflow_data_json,
            {**wr_failed.workflow_data_json, "retry_count": 1},
        )
        self.assertEqual(
            wr_new.event_reactions_json, wr_failed.event_reactions_json
        )
        self.assertIsNone(wr_new.internal_collection)
        self.assertEqual(wr_new.expiration_delay, wr_failed.expiration_delay)
        self.assertEqual(wr_new.supersedes, wr_failed)
        self.assertEqual(wr_failed.superseded, wr_new)

        self.assertQuerySetEqual(wr_new.dependencies.all(), [wr_fdep1])

        self.assertQuerySetEqual(wr_dep1.dependencies.all(), [wr_new])
        wr_dep1.refresh_from_db()
        self.assertEqual(wr_dep1.status, WorkRequest.Statuses.BLOCKED)

        self.assertQuerySetEqual(wr_dep2.dependencies.all(), [wr_new])
        wr_dep2.refresh_from_db()
        self.assertEqual(wr_dep2.status, WorkRequest.Statuses.COMPLETED)

        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(self.workflow.result, WorkRequest.Results.NONE)
        self.assertIsNone(self.workflow.completed_at)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede_blocked_by_dep(self) -> None:
        """A retried work request is BLOCKED if it has incomplete deps."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()
        wr = self.workflow.create_child(
            "noop", status=WorkRequest.Statuses.ABORTED
        )
        wr_dep = self.workflow.create_child("noop")
        wr.dependencies.add(wr_dep)

        wr_new = wr.retry()

        self.assertEqual(wr_new.status, WorkRequest.Statuses.BLOCKED)
        self.assertEqual(wr_new.result, WorkRequest.Results.NONE)
        self.assertEqual(wr_new.unblock_strategy, wr.unblock_strategy)
        self.assertEqual(wr_new.supersedes, wr)
        self.assertEqual(wr.superseded, wr_new)
        self.assertQuerySetEqual(wr_new.dependencies.all(), [wr_dep])

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede_blocked_manual(self) -> None:
        """A retried work request is BLOCKED if it needs manual unblocking."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()
        wr = self.workflow.create_child(
            "noop", status=WorkRequest.Statuses.ABORTED
        )
        wr.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        wr.save()

        wr_new = wr.retry()

        self.assertEqual(wr_new.status, WorkRequest.Statuses.BLOCKED)
        self.assertEqual(wr_new.result, WorkRequest.Results.NONE)
        self.assertEqual(wr_new.unblock_strategy, wr.unblock_strategy)
        self.assertEqual(wr_new.supersedes, wr)
        self.assertEqual(wr.superseded, wr_new)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede_worker_failed_checks_retry_count(self) -> None:
        """Retrying a task (worker failed) checks its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()
        wr = self.workflow.create_child(
            "noop",
            status=WorkRequest.Statuses.ABORTED,
            workflow_data=WorkRequestWorkflowData(
                retry_count=MAX_AUTOMATIC_RETRIES
            ),
        )

        with self.assertRaisesRegex(
            CannotRetry, "Maximum number of automatic retries exceeded"
        ):
            wr.retry(reason=WorkRequestRetryReason.WORKER_FAILED)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede_worker_failed_increments_retry_count(self) -> None:
        """Retrying a task (worker failure) increments its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()
        wr = self.workflow.create_child(
            "noop",
            status=WorkRequest.Statuses.ABORTED,
            workflow_data=WorkRequestWorkflowData(retry_count=1),
        )

        wr_new = wr.retry(reason=WorkRequestRetryReason.WORKER_FAILED)

        self.assertEqual(wr_new.workflow_data_json, {"retry_count": 2})

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede_delay_increments_retry_count(self) -> None:
        """Retrying a task (delay) increments its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()
        wr = self.workflow.create_child(
            "noop",
            status=WorkRequest.Statuses.ABORTED,
            workflow_data=WorkRequestWorkflowData(
                retry_count=MAX_AUTOMATIC_RETRIES
            ),
        )

        wr_new = wr.retry(reason=WorkRequestRetryReason.DELAY)

        self.assertEqual(
            wr_new.workflow_data_json,
            {"retry_count": MAX_AUTOMATIC_RETRIES + 1},
        )

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_supersede_manual_resets_retry_count(self) -> None:
        """Retrying a task (manual) resets its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.result = WorkRequest.Results.FAILURE
        self.workflow.completed_at = timezone.now()
        self.workflow.save()
        wr = self.workflow.create_child(
            "noop",
            status=WorkRequest.Statuses.ABORTED,
            workflow_data=WorkRequestWorkflowData(
                retry_count=MAX_AUTOMATIC_RETRIES
            ),
        )

        wr_new = wr.retry()

        self.assertEqual(wr_new.workflow_data_json, {"retry_count": 0})

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_in_running_workflow(self) -> None:
        """Test retrying a task in a running workflow."""
        # An aborted workflow, with a failed task and one depending on it
        now = timezone.now()
        self.workflow.status = WorkRequest.Statuses.RUNNING
        # Bogus value to check that it does not get reset
        self.workflow.completed_at = now
        self.workflow.save()

        wr_failed = self.workflow.create_child("noop")
        wr_failed.status = WorkRequest.Statuses.COMPLETED
        wr_failed.result = WorkRequest.Results.FAILURE
        wr_failed.save()

        # wr_failed gets retried
        wr_failed.retry()

        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(self.workflow.completed_at, now)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_in_failed_workflow(self) -> None:
        """Test retrying a task in a failed workflow."""
        # An aborted workflow, with a failed task and one depending on it
        now = timezone.now()
        self.workflow.status = WorkRequest.Statuses.COMPLETED
        self.workflow.result = WorkRequest.Results.FAILURE
        # Bogus value to check that it does not get reset
        self.workflow.completed_at = now
        self.workflow.save()

        wr_failed = self.workflow.create_child("noop")
        wr_failed.status = WorkRequest.Statuses.COMPLETED
        wr_failed.result = WorkRequest.Results.FAILURE
        wr_failed.save()

        # wr_failed gets retried
        wr_failed.retry()

        # The workflow is set to running again
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.RUNNING)
        self.assertIsNone(self.workflow.completed_at)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_internal(self) -> None:
        """Internal work requests can be retried."""
        wr_aborted = WorkRequest.objects.create_synchronization_point(
            parent=self.workflow,
            step="test",
            status=WorkRequest.Statuses.ABORTED,
        )

        self.assertTrue(wr_aborted.verify_retry())
        with self.captureOnCommitCallbacks(execute=True):
            wr_aborted.retry()

        self.workflow.refresh_from_db()
        self.assertIsNotNone(self.workflow.workflow_runtime_status)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_empty_workflow(self) -> None:
        """Test behaviour of retrying for an empty workflow."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.save()

        wr = self.workflow.retry()

        # Retrying a workflow does not create a new work request
        self.assertEqual(wr.pk, self.workflow.pk)

        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(self.workflow.result, WorkRequest.Results.SUCCESS)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow(self) -> None:
        """Test behaviour of retrying for an empty workflow."""
        wr_completed = self.workflow.create_child("noop")
        wr_aborted = self.workflow.create_child("noop")

        # Simulate the previous workflow lifecycle
        self.assertTrue(self.workflow.mark_running())
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.RUNNING)
        self.workflow.unblock_workflow_children()
        wr_completed.refresh_from_db()
        self.assertTrue(
            wr_completed.mark_completed(WorkRequest.Results.SUCCESS)
        )
        wr_completed.completed_at = (
            orig_completed := timezone.now() - timedelta(days=7)
        )
        wr_completed.save()
        wr_aborted.refresh_from_db()
        self.assertTrue(wr_aborted.mark_aborted())

        # The workflow ended up as aborted
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.ABORTED)

        # Retry it
        wr = self.workflow.retry(reason=WorkRequestRetryReason.WORKER_FAILED)
        self.assertEqual(wr.pk, self.workflow.pk)

        self.workflow.refresh_from_db()
        wr_completed.refresh_from_db()
        wr_aborted.refresh_from_db()

        # The workflow is running
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(self.workflow.result, WorkRequest.Results.NONE)
        self.assertEqual(
            self.workflow.workflow_data_json,
            {"workflow_template_name": "test", "retry_count": 1},
        )
        self.assertEqual(wr_completed.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr_completed.result, WorkRequest.Results.SUCCESS)
        # Successful work requests did not get restarted
        self.assertEqual(wr_completed.completed_at, orig_completed)
        self.assertEqual(wr_aborted.status, WorkRequest.Statuses.ABORTED)
        self.assertEqual(wr_aborted.result, WorkRequest.Results.NONE)
        wr_aborted_new = wr_aborted.superseded
        self.assertEqual(wr_aborted_new.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr_aborted_new.result, WorkRequest.Results.NONE)
        self.assertEqual(wr_aborted_new.workflow_data.retry_count, 1)

        # Complete the one running task
        wr_aborted_new.mark_completed(WorkRequest.Results.SUCCESS)
        self.workflow.refresh_from_db()

        # Workflow completed successfully
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(self.workflow.result, WorkRequest.Results.SUCCESS)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow_calls(self) -> None:
        """Test behaviour of retrying for an empty workflow."""
        wr_aborted = self.workflow.create_child("noop")

        # Simulate the previous workflow lifecycle
        self.assertTrue(self.workflow.mark_running())
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.RUNNING)
        self.workflow.unblock_workflow_children()
        wr_aborted.refresh_from_db()
        self.assertTrue(wr_aborted.mark_aborted())

        # The workflow ended up as aborted
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.ABORTED)

        with (
            mock.patch(
                "debusine.server.workflows.base.orchestrate_workflow"
            ) as orchestrate_workflow,
            mock.patch(
                "debusine.db.models.work_requests.WorkRequest"
                ".maybe_finish_workflow"
            ) as maybe_finish_workflow,
        ):
            self.workflow.retry()

        orchestrate_workflow.assert_called_with(self.workflow)
        maybe_finish_workflow.assert_called()

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow_child_fails(self) -> None:
        """Test retrying a workflow with a child that cannot retry."""
        wr_aborted1 = self.workflow.create_child("noop")
        wr_unretriable = self.workflow.create_child("noop")
        wr_aborted2 = self.workflow.create_child("noop")

        self.assertTrue(self.workflow.mark_running())
        self.workflow.unblock_workflow_children()
        wr_aborted1.refresh_from_db()
        wr_unretriable.refresh_from_db()
        wr_aborted2.refresh_from_db()
        self.assertTrue(wr_aborted1.mark_aborted())
        self.assertTrue(wr_unretriable.mark_aborted())
        self.assertTrue(wr_aborted2.mark_aborted())

        # The workflow ended up as aborted
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.ABORTED)

        # Make wr_unretriable unretriable
        orig_check_retry = WorkRequest._verify_retry

        def test_check_retry(self: WorkRequest) -> None:
            if self.pk == wr_unretriable.pk:
                raise CannotRetry("expected failure")
            else:
                orig_check_retry(self)

        try:
            with transaction.atomic():
                with (
                    mock.patch(
                        "debusine.db.models.WorkRequest._verify_retry",
                        side_effect=test_check_retry,
                        autospec=True,
                    ),
                    self.assertRaises(CannotRetry) as exc,
                ):
                    self.workflow.retry()

                self.assertEqual(
                    exc.exception.args[0],
                    f"child work request {wr_unretriable.pk} cannot be retried",
                )
                self.assertIsNotNone(exc.exception.__context__)
                assert exc.exception.__context__ is not None
                self.assertEqual(
                    exc.exception.__context__.args[0], "expected failure"
                )

                self.workflow.refresh_from_db()
                wr_aborted1.refresh_from_db()
                wr_aborted1_new = wr_aborted1.superseded
                wr_unretriable.refresh_from_db()
                wr_aborted2.refresh_from_db()
                self.assertFalse(hasattr(wr_aborted2, "superseded"))
                self.assertEqual(
                    self.workflow.status, WorkRequest.Statuses.RUNNING
                )
                self.assertEqual(
                    wr_aborted1_new.status, WorkRequest.Statuses.PENDING
                )
                self.assertEqual(
                    wr_unretriable.status, WorkRequest.Statuses.ABORTED
                )
                self.assertEqual(
                    wr_aborted2.status, WorkRequest.Statuses.ABORTED
                )
                # Raise to roll back the transaction
                raise exc.exception
        except CannotRetry as exc1:
            self.assertIs(exc1, exc.exception)

        self.workflow.refresh_from_db()
        wr_aborted1.refresh_from_db()
        wr_unretriable.refresh_from_db()
        wr_aborted2.refresh_from_db()
        self.assertEqual(self.workflow.status, WorkRequest.Statuses.ABORTED)
        self.assertEqual(wr_aborted1.status, WorkRequest.Statuses.ABORTED)
        self.assertEqual(wr_unretriable.status, WorkRequest.Statuses.ABORTED)
        self.assertEqual(wr_aborted2.status, WorkRequest.Statuses.ABORTED)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow_worker_failed_checks_retry_count(self) -> None:
        """Automatically retrying a workflow checks its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.workflow_data = WorkRequestWorkflowData(
            retry_count=MAX_AUTOMATIC_RETRIES
        )
        self.workflow.save()

        with self.assertRaisesRegex(
            CannotRetry, "Maximum number of automatic retries exceeded"
        ):
            self.workflow.retry(reason=WorkRequestRetryReason.WORKER_FAILED)

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow_worker_failed_increments_retry_count(self) -> None:
        """Retrying a workflow (worker failed) increments its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.workflow_data = WorkRequestWorkflowData(retry_count=1)
        self.workflow.save()
        wr = self.workflow.retry(reason=WorkRequestRetryReason.WORKER_FAILED)
        self.assertEqual(wr.pk, self.workflow.pk)
        self.assertEqual(wr.workflow_data_json, {"retry_count": 2})

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow_delay_increments_retry_count(self) -> None:
        """Retrying a workflow (delay) increments its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.workflow_data = WorkRequestWorkflowData(
            retry_count=MAX_AUTOMATIC_RETRIES
        )
        self.workflow.save()
        wr = self.workflow.retry(reason=WorkRequestRetryReason.DELAY)
        self.assertEqual(wr.pk, self.workflow.pk)
        self.assertEqual(
            wr.workflow_data_json, {"retry_count": MAX_AUTOMATIC_RETRIES + 1}
        )

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_workflow_manual_resets_retry_count(self) -> None:
        """Retrying a workflow (manual) resets its retry count."""
        self.workflow.status = WorkRequest.Statuses.ABORTED
        self.workflow.workflow_data = WorkRequestWorkflowData(
            retry_count=MAX_AUTOMATIC_RETRIES
        )
        self.workflow.save()
        wr = self.workflow.retry()
        self.assertEqual(wr.pk, self.workflow.pk)
        self.assertEqual(wr.workflow_data_json, {"retry_count": 0})

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_retry_update_debusine_promise_work_request_id(self) -> None:
        """Test retrying updates promise of the expected work request id."""
        user = self.playground.get_default_user()

        template = WorkflowTemplate.objects.create(
            name="retry-update-debusine-promise-work-request-id",
            workspace=default_workspace(),
            task_name="noop",
        )
        workflow = self.playground.create_workflow(template, task_data={})

        collection = workflow.internal_collection
        assert collection is not None

        wr_to_retry = self.playground.create_work_request(
            parent=workflow,
            task_name="noop",
        )
        assert wr_to_retry.parent_id is not None
        wr_aborted = self.playground.create_work_request(
            parent=workflow,
            task_name="noop",
        )

        wr_to_retry.mark_aborted()
        wr_aborted.mark_aborted()

        debusine_promise_updated = collection.manager.add_bare_data(
            category=BareDataCategory.PROMISE,
            name="test-1",
            data=DebusinePromise(
                promise_work_request_id=wr_to_retry.id,
                promise_workflow_id=wr_to_retry.parent_id,
                promise_category="something",
            ),
            user=user,
        )
        debusine_promise_not_updated = collection.manager.add_bare_data(
            category=BareDataCategory.PROMISE,
            name="test-2",
            data=DebusinePromise(
                promise_work_request_id=wr_aborted.id,
                promise_workflow_id=wr_to_retry.parent_id,
                promise_category="something",
            ),
            user=user,
        )

        wr_supersedes = wr_to_retry.retry()

        debusine_promise_updated.refresh_from_db()
        debusine_promise_not_updated.refresh_from_db()

        # Updated to the new promise_work_request_id
        self.assertEqual(
            debusine_promise_updated.data["promise_work_request_id"],
            wr_supersedes.id,
        )

        # Still the old promise_work_request_id
        self.assertEqual(
            debusine_promise_not_updated.data["promise_work_request_id"],
            wr_aborted.id,
        )

    def test_is_workflow_task_type_workflow(self) -> None:
        """A WORKFLOW work request is a workflow."""
        self.assertTrue(self.workflow.is_workflow)

    def test_is_workflow_child(self) -> None:
        """A WORKER work request is not a workflow, even if it's part of one."""
        wr = self.playground.create_work_request(parent=self.workflow)

        self.assertFalse(wr.is_workflow)

    def test_is_part_of_workflow_standalone(self) -> None:
        """A standalone work request is not part of a workflow."""
        wr = self.playground.create_work_request()

        self.assertFalse(wr.is_part_of_workflow)

    def test_is_part_of_workflow_root(self) -> None:
        """A WORKFLOW work request with no parent is not part of a workflow."""
        self.assertFalse(self.workflow.is_part_of_workflow)

    def test_is_part_of_workflow_child(self) -> None:
        """A work request with a parent is part of a workflow."""
        wr = self.playground.create_work_request(parent=self.workflow)

        self.assertTrue(wr.is_part_of_workflow)

    def test_get_workflow_root_standalone(self) -> None:
        """A standalone work request has no workflow root."""
        wr = self.playground.create_work_request()

        self.assertIsNone(wr.get_workflow_root())

    def test_get_workflow_root_root(self) -> None:
        """A top-level work request in a workflow is its own root."""
        self.assertEqual(self.workflow.get_workflow_root(), self.workflow)

    def test_get_workflow_root_child(self) -> None:
        """A child work request in a workflow has a root."""
        wr = self.playground.create_work_request(parent=self.workflow)

        self.assertEqual(wr.get_workflow_root(), self.workflow)

    def test_get_workflow_root_grandchild(self) -> None:
        """A grandchild work request in a workflow has a root."""
        root = self.workflow
        child_template = WorkflowTemplate.objects.create(
            name="child",
            workspace=default_workspace(),
            task_name="noop",
            task_data={},
        )
        child = self.playground.create_workflow(
            child_template, task_data={}, parent=root
        )
        wr = self.playground.create_work_request(parent=child)

        self.assertEqual(wr.get_workflow_root(), root)

    def test_is_workflow_root(self) -> None:
        """Tests for is_workflow_root()."""
        # Standalone work request is not a workflow
        self.assertFalse(self.playground.create_work_request().is_workflow_root)

        root_workflow = self.playground.create_workflow()
        self.assertTrue(root_workflow.is_workflow_root)

        sub_workflow = self.playground.create_workflow()
        sub_workflow.parent = root_workflow
        sub_workflow.save()

        # A workflow in a workflow is not a root workflow
        self.assertFalse(sub_workflow.is_workflow_root)

    def test_workflow_display_name_explicit(self) -> None:
        """`workflow_display_name` returns an explicitly-set display name."""
        wr = self.playground.create_work_request(
            workflow_data=WorkRequestWorkflowData(
                display_name="Foo", step="foo"
            )
        )

        self.assertEqual(wr.workflow_display_name, "Foo")

    def test_workflow_display_name_task_name(self) -> None:
        """`workflow_display_name` defaults to the task name."""
        wr = self.playground.create_work_request(task_name="noop")

        self.assertEqual(wr.workflow_display_name, "noop")

    def test_requires_signature_running_external_debsign(self) -> None:
        """A running `Wait/ExternalDebsign` task requires a signature."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.WAIT,
            task_name="externaldebsign",
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )

        self.assertTrue(wr.requires_signature)

    def test_requires_signature_completed_external_debsign(self) -> None:
        """A completed `Wait/ExternalDebsign` task requires no signature."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.WAIT,
            task_name="externaldebsign",
            status=WorkRequest.Statuses.COMPLETED,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )

        self.assertFalse(wr.requires_signature)

    def test_requires_signature_other_task(self) -> None:
        """A non-`Wait/ExternalDebsign` task requires no signature."""
        wr = self.playground.create_work_request(task_name="noop")

        self.assertFalse(wr.requires_signature)

    def test_effective_expiration_delay(self) -> None:
        """Test WorkRequest.effective_expiration_delay() method."""
        wr = self.playground.create_work_request(task_name="noop")

        wr.expiration_delay = timedelta(days=1)
        self.assertEqual(wr.effective_expiration_delay(), timedelta(days=1))

        wr.expiration_delay = timedelta(days=0)
        self.assertEqual(wr.effective_expiration_delay(), timedelta(days=0))

        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(name="test")
        workspace.default_expiration_delay = timedelta(days=2)
        wr.workspace = workspace

        wr.expiration_delay = timedelta(days=1)
        self.assertEqual(wr.effective_expiration_delay(), timedelta(days=1))
        self.assertEqual(wr.expire_at, wr.created_at + timedelta(days=1))

        wr.expiration_delay = timedelta(days=0)
        self.assertEqual(wr.effective_expiration_delay(), timedelta(days=0))

        wr.expiration_delay = None
        self.assertEqual(wr.effective_expiration_delay(), timedelta(days=2))

    def test_get_task(self) -> None:
        """Test get_task with various task types."""
        workspace = self.playground.get_default_workspace()
        for task_type, task_name, expected_class, task_data, workflow_data in (
            (TaskTypes.WORKER, "noop", Noop, {}, {}),
            (TaskTypes.SERVER, "servernoop", ServerNoop, {}, {}),
            (TaskTypes.SIGNING, "noop", SigningNoop, {}, {}),
            (TaskTypes.WORKFLOW, "noop", NoopWorkflow, {}, {}),
            (
                TaskTypes.WAIT,
                "delay",
                Delay,
                {"delay_until": timezone.now().isoformat()},
                {"needs_input": False},
            ),
        ):
            with self.subTest(task_type=task_type):
                wr = WorkRequest(
                    workspace=workspace,
                    task_type=task_type,
                    task_name=task_name,
                    task_data=task_data,
                    workflow_data_json=workflow_data,
                )
                task = wr.get_task()
                self.assertIsInstance(task, expected_class)

    def test_get_task_internal_workflow_callback(self) -> None:
        """A workflow callback is instantiated as its workflow."""
        parent = self.playground.create_workflow(task_name="noop")
        wr = WorkRequest.objects.create_workflow_callback(
            parent=parent, step="test"
        )
        task = wr.get_task()
        self.assertIsInstance(task, NoopWorkflow)

    def test_get_task_internal_workflow_callback_outside_workflow(self) -> None:
        """A workflow callback outside a workflow cannot be instantiated."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.INTERNAL, task_name="workflow"
        )
        with self.assertRaisesRegex(
            InternalTaskError,
            "Workflow callback is not contained in a workflow",
        ):
            wr.get_task()

    def test_get_task_internal_not_workflow_callback(self) -> None:
        """A non-workflow-callback internal task cannot be instantiated."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.INTERNAL, task_name="synchronization_point"
        )
        with self.assertRaisesRegex(
            InternalTaskError,
            "Internal tasks other than workflow callbacks cannot be "
            "instantiated",
        ):
            wr.get_task()

    def test_get_task_worker_host_architecture(self) -> None:
        """get_task() sets worker_host_architecture if it has a worker."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        worker = self.playground.create_worker(
            extra_dynamic_metadata={"system:host_architecture": "i386"}
        )

        task = wr.get_task(worker=worker)

        self.assertEqual(task.worker_host_architecture, "i386")

    def test_get_task_no_worker_host_architecture(self) -> None:
        """get_task() skips worker_host_architecture if it has no worker."""
        wr = self.playground.create_work_request(
            task_type=TaskTypes.WORKER, task_name="noop"
        )

        task = wr.get_task()

        self.assertIsNone(task.worker_host_architecture)

    def test_get_label_from_data(self) -> None:
        """Test getting label from data."""
        wr = WorkRequest(
            task_type=TaskTypes.WORKER, task_name="noop", task_data={}
        )
        self.assertEqual(wr.get_label(), "noop")

    def test_get_label_from_bad_task_data(self) -> None:
        """Test getting label from inconsistent task data."""
        wr = WorkRequest(
            pk=42, task_type=TaskTypes.WORKER, task_name="sbuild", task_data={}
        )

        with override_settings(DEBUG=True):
            with self.assertNoLogs("debusine.db.models.work_requests"):
                self.assertEqual(wr.get_label(), "sbuild")

        with override_settings(DEBUG=False):
            with self.assertNoLogs("debusine.db.models.work_requests"):
                self.assertEqual(wr.get_label(), "sbuild")

    def test_get_label_for_internal_task(self) -> None:
        """Test getting label for an internal task."""
        wr = WorkRequest.objects.create_synchronization_point(
            parent=self.workflow,
            step="test",
            display_name="Test",
            status=WorkRequest.Statuses.BLOCKED,
        )
        self.assertEqual(wr.get_label(), "Test")

    def test_get_label_from_data_reuse_model(self) -> None:
        """Test getting label reusing the model instance."""
        wr = WorkRequest(
            task_type=TaskTypes.WORKER,
            task_name="blhc",
            task_data={"artifact": "TODO"},
        )
        task = Noop({})
        self.assertEqual(wr.get_label(task), "noop")

    def test_can_display_delegate_to_workspace(self) -> None:
        """Test the can_display predicate delegating to containing workspace."""
        with override_permission(Workspace, "can_display", AllowAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                allowed=self.workflow,
            )
        with override_permission(Workspace, "can_display", DenyAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                denied=self.workflow,
            )

    def test_can_retry_delegate_to_workspace(self) -> None:
        """Test the can_retry predicate delegating to containing workspace."""
        with override_permission(Workspace, "can_display", AllowAll):
            self.assertPermission(
                "can_retry",
                users=(AnonymousUser(), self.scenario.user),
                allowed=self.workflow,
            )
        with override_permission(Workspace, "can_display", DenyAll):
            self.assertPermission(
                "can_retry",
                users=(AnonymousUser(), self.scenario.user),
                denied=self.workflow,
            )

    def test_can_unblock(self) -> None:
        """Test the can_unblock predicate."""
        scope_owner = self.playground.create_user("scope_owner")
        self.playground.create_group_role(
            self.scenario.scope, Scope.Roles.OWNER, users=[scope_owner]
        )

        workspace_owner = self.playground.create_user("workspace_owner")
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.OWNER,
            users=[workspace_owner],
        )

        # Anonymous and not-owners not allowed
        self.assertPermission(
            "can_unblock",
            users=(AnonymousUser(), self.scenario.user),
            denied=self.workflow,
        )
        # Workers not allowed
        self.assertPermission(
            "can_unblock",
            users=None,
            token=self.playground.create_worker_token(),
            denied=self.workflow,
        )
        # Scope owner has access
        self.assertPermission(
            "can_unblock",
            users=scope_owner,
            allowed=[self.workflow],
        )
        # Workspace owner has access
        self.assertPermission(
            "can_unblock",
            users=workspace_owner,
            allowed=[self.workflow],
        )

    def test_flattened(self) -> None:
        """Test workflow_flattened."""
        child_1 = self.workflow.create_child(task_name="noop")
        child_2 = self.workflow.create_child(
            task_type=TaskTypes.WORKFLOW, task_name="noop"
        )
        child_3 = child_2.create_child(task_name="noop")

        self.assertQuerySetEqual(
            workflow_flattened(self.workflow), [child_1, child_3], ordered=False
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_aborted_propagates_last_activity_at(self) -> None:
        """Test mark_aborted() propagates last_activity_at to workflow."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.workflow.create_child("noop")

            wr.mark_aborted()

        self.workflow.refresh_from_db()

        self.assertEqual(
            self.workflow.workflow_last_activity_at, wr.completed_at
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_running_propagates_last_activity_at(self) -> None:
        """Test mark_running() propagates last_activity_at to workflow."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.playground.create_work_request(
                assign_new_worker=True, parent=self.workflow, task_name="noop"
            )

            self.assertTrue(wr.mark_running())

        self.workflow.refresh_from_db()

        self.assertEqual(self.workflow.workflow_last_activity_at, wr.started_at)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_completed_propagates_last_activity_at(self) -> None:
        """Test mark_completed() propagates last_activity_at to workflow."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.playground.create_work_request(
                mark_running=True,
                assign_new_worker=True,
                parent=self.workflow,
                task_name="noop",
            )

            self.assertTrue(wr.mark_completed(WorkRequest.Results.SUCCESS))

        self.workflow.refresh_from_db()

        self.assertEqual(
            self.workflow.workflow_last_activity_at, wr.completed_at
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_aborted_updates_runtime_status(self) -> None:
        """Test mark_aborted() updates workflow's runtime status."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.playground.create_work_request(
                mark_running=True,
                assign_new_worker=True,
                parent=self.workflow,
                task_name="noop",
            )

            self.assertTrue(wr.mark_aborted())

        self.workflow.refresh_from_db()

        self.assertEqual(
            self.workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.ABORTED,
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_completed_updates_runtime_status(self) -> None:
        """Test mark_completed() updates workflow's runtime status."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.playground.create_work_request(
                mark_running=True,
                assign_new_worker=True,
                parent=self.workflow,
                task_name="noop",
            )

            self.assertTrue(wr.mark_completed(WorkRequest.Results.SUCCESS))

        self.workflow.refresh_from_db()

        self.assertEqual(
            self.workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.COMPLETED,
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_pending_updates_runtime_status(self) -> None:
        """Test mark_pending() updates workflow's runtime status."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.playground.create_work_request(
                mark_running=True,
                assign_new_worker=True,
                parent=self.workflow,
                task_name="noop",
            )
            wr.status = WorkRequest.Statuses.BLOCKED
            wr.save()

            wr.mark_pending()

        self.workflow.refresh_from_db()

        self.assertEqual(
            self.workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.PENDING,
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_mark_running_updates_runtime_status(self) -> None:
        """Test mark_running() updates workflow's runtime status."""
        with self.captureOnCommitCallbacks(execute=True):
            wr = self.playground.create_work_request(
                assign_new_worker=True,
                parent=self.workflow,
                task_name="noop",
            )

            wr.mark_running()

        self.workflow.refresh_from_db()

        self.assertEqual(
            self.workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.RUNNING,
        )

    @preserve_task_registry()
    def test_compute_workflow_runtime_status_needs_input(self) -> None:
        """Test compute_workflow_runtime_status returns NEEDS_INPUT."""

        class WaitNoop(
            TestBaseWaitTask[BaseTaskData, BaseDynamicTaskData],
        ):
            TASK_VERSION = 1

            def _execute(self) -> bool:
                raise NotImplementedError()

        self.workflow.create_child(
            "waitnoop",
            task_type=TaskTypes.WAIT,
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )

        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.NEEDS_INPUT,
        )

    def test_compute_workflow_runtime_status_running(self) -> None:
        """Test compute_workflow_runtime_status returns RUNNING."""
        self.workflow.create_child(
            "noop",
            task_type=TaskTypes.WORKER,
            status=WorkRequest.Statuses.RUNNING,
        )

        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.RUNNING,
        )

    @preserve_task_registry()
    def test_compute_workflow_runtime_status_waiting(self) -> None:
        """Test compute_workflow_runtime_status returns WAITING."""

        class WaitNoop(
            TestBaseWaitTask[BaseTaskData, BaseDynamicTaskData],
        ):
            TASK_VERSION = 1

            def _execute(self) -> bool:
                raise NotImplementedError()

        self.workflow.create_child(
            "waitnoop",
            task_type=TaskTypes.WAIT,
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=False),
        )

        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.WAITING,
        )

    def test_compute_workflow_runtime_status_pending(self) -> None:
        """Test compute_workflow_runtime_status returns PENDING."""
        self.workflow.create_child("noop", status=WorkRequest.Statuses.PENDING)

        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.PENDING,
        )

    def test_compute_workflow_runtime_status_aborted(self) -> None:
        """Test compute_workflow_runtime_status returns ABORTED."""
        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.ABORTED,
        )

    def test_compute_workflow_runtime_status_completed(self) -> None:
        """Test compute_workflow_runtime_status returns COMPLETED."""
        self.workflow.create_child(
            "noop", status=WorkRequest.Statuses.COMPLETED
        )

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.COMPLETED,
        )

    def test_compute_workflow_runtime_status_blocked(self) -> None:
        """Test compute_workflow_runtime_status returns BLOCKED."""
        self.workflow.create_child("noop", status=WorkRequest.Statuses.BLOCKED)
        self.workflow.create_child("noop", status=WorkRequest.Statuses.ABORTED)

        self.assertEqual(
            compute_workflow_runtime_status(self.workflow),
            WorkRequest.RuntimeStatuses.BLOCKED,
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_schedule_workflow_update(self) -> None:
        """Test schedule_workflow_update()."""
        sub_workflow = self.playground.create_workflow(
            parent=self.workflow,
            task_name="noop",
        )

        self.assertIsNone(sub_workflow.workflow_runtime_status)
        self.assertIsNone(self.workflow.workflow_runtime_status)

        with self.captureOnCommitCallbacks(execute=True):
            sub_workflow.schedule_workflow_update()
            self.assertTrue(sub_workflow.workflows_need_update)

        sub_workflow.refresh_from_db()
        self.workflow.refresh_from_db()

        self.assertEqual(
            sub_workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.COMPLETED,
        )
        self.assertEqual(
            self.workflow.workflow_runtime_status,
            WorkRequest.RuntimeStatuses.COMPLETED,
        )
        self.assertFalse(sub_workflow.workflows_need_update)

    def test_schedule_workflow_update_batches(self) -> None:
        """schedule_workflow_update() runs the update once per savepoint."""
        sub_workflow = self.playground.create_workflow(
            parent=self.workflow,
            task_name="noop",
        )
        wr = sub_workflow.create_child("noop")

        with (
            mock.patch(
                "debusine.server.celery.update_workflows.delay"
            ) as mock_delay,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            wr.schedule_workflow_update()
            wr.schedule_workflow_update()
            sub_workflow.schedule_workflow_update()
            self.workflow.schedule_workflow_update()

        self.assertEqual(len(callbacks), 1)
        mock_delay.assert_called_once()

    def test_workflow_display_name_parameters_template_name(self) -> None:
        """Test workflow_display_name_parameters returns template name."""
        self.assertEqual(
            self.workflow.workflow_display_name_parameters,
            self.template.name,
        )

    def test_workflow_display_name_parameters_name_parameters(self) -> None:
        """Test workflow_display_name_parameters returns name and parameters."""
        self.workflow.dynamic_task_data = {"parameter_summary": "a,b"}
        self.assertEqual(
            self.workflow.workflow_display_name_parameters,
            f"{self.template.name}(a,b)",
        )

    def test_workflow_display_name_parameters_none(self) -> None:
        """Test workflow_display_name_parameters returns None."""
        self.workflow.workflow_data = WorkRequestWorkflowData()

        self.assertIsNone(self.workflow.workflow_display_name_parameters)

    def test_set_current(self) -> None:
        """Test WorkRequest.set_current."""
        scope = self.playground.get_or_create_scope(name="test")
        user = self.playground.create_user(username="testuser")
        workspace = self.playground.create_workspace(
            scope=scope, name="test", public=True
        )
        work_request = self.playground.create_work_request(
            workspace=workspace, created_by=user
        )
        work_request.set_current()
        self.assertEqual(context.scope, scope)
        self.assertEqual(context.user, user)
        self.assertEqual(context.workspace, workspace)


class WorkRequestWorkerTests(ChannelsHelpersMixin, TestCase):
    """Tests for the WorkRequest class scheduling."""

    worker: ClassVar[Worker]
    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()

        cls.worker = Worker.objects.create_with_fqdn(
            "computer.lan", Token.objects.create()
        )
        cls.work_request = cls.playground.create_work_request(
            task_name='noop',
            worker=cls.worker,
            status=WorkRequest.Statuses.PENDING,
        )

    def test_str(self) -> None:
        """Test WorkerRequest.__str__ return WorkRequest.id."""
        self.assertEqual(self.work_request.__str__(), str(self.work_request.id))

    def test_mark_running_from_aborted(self) -> None:
        """Test WorkRequest.mark_running() doesn't change (was aborted)."""
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        self.assertFalse(self.work_request.mark_running())

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)

    def test_mark_running(self) -> None:
        """Test WorkRequest.mark_running() change status to running."""
        self.work_request.status = WorkRequest.Statuses.PENDING
        self.work_request.save()
        self.assertIsNone(self.work_request.started_at)

        self.assertTrue(self.work_request.mark_running())

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.RUNNING)
        assert self.work_request.started_at is not None
        self.assertLess(self.work_request.started_at, timezone.now())

        # Marking as running again (a running WorkRequest) is a no-op
        started_at = self.work_request.started_at
        self.assertTrue(self.work_request.mark_running())

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.RUNNING)
        self.assertEqual(self.work_request.started_at, started_at)

    def test_mark_running_fails_worker_already_running(self) -> None:
        """WorkRequest.mark_running() return False: worker already running."""
        self.work_request.status = WorkRequest.Statuses.PENDING
        self.work_request.save()

        other_work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.RUNNING, worker=self.work_request.worker
        )

        with self.assertLogsContains(
            f"Cannot mark WorkRequest {self.work_request.pk} as running - the "
            f"assigned worker {self.work_request.worker} is running too many "
            f"other WorkRequests: [{other_work_request!r}]",
            logger="debusine.db.models",
            level=logging.DEBUG,
        ):
            self.assertFalse(self.work_request.mark_running())

    def test_mark_running_fails_no_assigned_worker(self) -> None:
        """WorkRequest.mark_running() return False: no worker assigned."""
        self.work_request.status = WorkRequest.Statuses.PENDING
        self.work_request.worker = None
        self.work_request.save()

        self.assertFalse(self.work_request.mark_running())

    def test_mark_completed_from_aborted(self) -> None:
        """Test WorkRequest.mark_completed() doesn't change (was aborted)."""
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        self.work_request.refresh_from_db()

        self.assertFalse(
            self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        )

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)

    def test_mark_completed(self) -> None:
        """Test WorkRequest.mark_completed() changes status to completed."""
        self.work_request.status = WorkRequest.Statuses.RUNNING
        self.work_request.save()

        self.assertIsNone(self.work_request.completed_at)
        self.assertEqual(self.work_request.result, WorkRequest.Results.NONE)

        self.assertTrue(
            self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        )

        self.work_request.refresh_from_db()
        self.assertEqual(
            self.work_request.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(self.work_request.result, WorkRequest.Results.SUCCESS)
        assert self.work_request.completed_at is not None
        self.assertLess(self.work_request.completed_at, timezone.now())

    def test_mark_completed_updates_workflow_last_activity_at(self) -> None:
        """
        Test mark_completed() updates its workflow's activity.

        Only when the work request is part of a workflow.
        """
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        sub_workflow = workflow.create_child(
            task_name="noop", task_type=TaskTypes.WORKFLOW
        )
        child = sub_workflow.create_child(task_name="noop")

        child.mark_completed(WorkRequest.Results.SUCCESS)
        workflow.refresh_from_db()
        sub_workflow.refresh_from_db()

        self.assertEqual(workflow.workflow_last_activity_at, child.completed_at)
        self.assertEqual(
            sub_workflow.workflow_last_activity_at, child.completed_at
        )

    def test_mark_completed_records_worker_pool_statistics(self) -> None:
        """mark_completed() records worker pool task execution statistics."""
        worker_pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=worker_pool)
        self.work_request.assign_worker(worker)
        self.work_request.mark_running()
        output_data = OutputData(
            runtime_statistics=RuntimeStatistics(duration=10)
        )

        self.work_request.mark_completed(
            WorkRequest.Results.SUCCESS, output_data
        )

        self.assertQuerySetEqual(
            WorkerPoolTaskExecutionStatistics.objects.filter(
                worker_pool=worker_pool
            ).values_list("worker_pool", "worker", "runtime"),
            [(worker_pool.id, worker.id, 10)],
        )

    def test_mark_running_updates_workflow_last_activity_at(self) -> None:
        """
        Test mark_running() updates its workflow's activity.

        Only when the work request is part of a workflow.
        """
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        sub_workflow = workflow.create_child(
            task_name="noop", task_type=TaskTypes.WORKFLOW
        )
        child = sub_workflow.create_child(task_name="noop")

        child.mark_running()
        workflow.refresh_from_db()
        sub_workflow.refresh_from_db()

        self.assertEqual(workflow.workflow_last_activity_at, child.started_at)
        self.assertEqual(
            sub_workflow.workflow_last_activity_at, child.started_at
        )

    def test_mark_aborted(self) -> None:
        """Test WorkRequest.mark_aborted() changes status to aborted."""
        self.assertIsNone(self.work_request.completed_at)

        self.assertTrue(self.work_request.mark_aborted())

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)
        assert self.work_request.completed_at is not None
        self.assertLess(self.work_request.completed_at, timezone.now())

    def test_maybe_finish_workflow_not_workflow(self) -> None:
        """`maybe_finish_workflow` does nothing on non-workflows."""
        self.assertFalse(self.work_request.maybe_finish_workflow())

    def test_maybe_finish_workflow_already_aborted(self) -> None:
        """`maybe_finish_workflow` does nothing if it was already aborted."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        workflow.create_child("noop", status=WorkRequest.Statuses.COMPLETED)
        self.assertTrue(workflow.mark_aborted())

        self.assertFalse(workflow.maybe_finish_workflow())

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, WorkRequest.Statuses.ABORTED)

    def test_maybe_finish_workflow_has_children_in_progress(self) -> None:
        """`maybe_finish_workflow` does nothing if a child is in progress."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        children = [workflow.create_child("noop") for _ in range(2)]
        workflow.mark_running()
        workflow.unblock_workflow_children()
        children[0].refresh_from_db()
        self.assertTrue(children[0].mark_completed(WorkRequest.Results.SUCCESS))

        self.assertFalse(workflow.maybe_finish_workflow())

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, WorkRequest.Statuses.RUNNING)

    def test_maybe_finish_workflow_all_succeeded(self) -> None:
        """All children succeeded: COMPLETED/SUCCESS."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        children = [workflow.create_child("noop") for _ in range(2)]
        workflow.mark_running()
        workflow.unblock_workflow_children()

        for child in children:
            child.refresh_from_db()
            self.assertTrue(child.mark_completed(WorkRequest.Results.SUCCESS))

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(workflow.result, WorkRequest.Results.SUCCESS)
        assert workflow.completed_at is not None
        self.assertLess(workflow.completed_at, timezone.now())

    def test_maybe_finish_workflow_allow_failure(self) -> None:
        """Some children failed but are allowed to fail: COMPLETED/SUCCESS."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        children = [
            workflow.create_child(
                "noop",
                workflow_data=WorkRequestWorkflowData(
                    allow_failure=True, display_name="test", step="test"
                ),
            )
            for _ in range(2)
        ]
        workflow.mark_running()
        workflow.unblock_workflow_children()

        children[0].refresh_from_db()
        children[0].mark_completed(WorkRequest.Results.FAILURE)
        children[1].refresh_from_db()
        children[1].mark_completed(WorkRequest.Results.SUCCESS)

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(workflow.result, WorkRequest.Results.SUCCESS)
        assert workflow.completed_at is not None
        self.assertLess(workflow.completed_at, timezone.now())

    def test_maybe_finish_workflow_some_failed(self) -> None:
        """Some children failed: COMPLETED/FAILURE."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        children = [workflow.create_child("noop") for _ in range(2)]
        workflow.mark_running()
        workflow.unblock_workflow_children()

        children[0].refresh_from_db()
        children[0].mark_completed(WorkRequest.Results.FAILURE)
        children[1].refresh_from_db()
        children[1].mark_completed(WorkRequest.Results.SUCCESS)

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(workflow.result, WorkRequest.Results.FAILURE)
        assert workflow.completed_at is not None
        self.assertLess(workflow.completed_at, timezone.now())

    def test_maybe_finish_workflow_some_aborted(self) -> None:
        """Some children were aborted: `maybe_finish_workflow` sets ABORTED."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        children = [workflow.create_child("noop") for _ in range(2)]
        workflow.mark_running()
        workflow.unblock_workflow_children()

        children[0].refresh_from_db()
        children[0].mark_aborted()
        children[1].refresh_from_db()
        children[1].mark_completed(WorkRequest.Results.SUCCESS)

        workflow.refresh_from_db()
        self.assertEqual(workflow.status, WorkRequest.Statuses.ABORTED)
        assert workflow.completed_at is not None
        self.assertLess(workflow.completed_at, timezone.now())

    def test_maybe_finish_workflow_superseded_aborted(self) -> None:
        """Some aborted children were superseded: ignores them."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        children = [workflow.create_child("noop") for _ in range(2)]
        workflow.started_at = timezone.now()
        workflow.status = WorkRequest.Statuses.RUNNING
        workflow.save()

        children[0].completed_at = timezone.now()
        children[0].status = WorkRequest.Statuses.ABORTED
        children[0].save()
        children[1].completed_at = timezone.now()
        children[1].status = WorkRequest.Statuses.COMPLETED
        children[1].result = WorkRequest.Results.SUCCESS
        children[1].save()

        self.assertTrue(workflow.maybe_finish_workflow())
        self.assertEqual(workflow.status, WorkRequest.Statuses.ABORTED)
        assert workflow.completed_at is not None
        self.assertLess(workflow.completed_at, timezone.now())
        prev_completed_at = workflow.completed_at

        new_wr = workflow.create_child("noop")
        new_wr.supersedes = children[0]
        new_wr.status = WorkRequest.Statuses.COMPLETED
        new_wr.result = WorkRequest.Results.SUCCESS
        new_wr.save()
        workflow.status = WorkRequest.Statuses.RUNNING
        workflow.save()

        self.assertTrue(workflow.maybe_finish_workflow())
        self.assertEqual(workflow.status, WorkRequest.Statuses.COMPLETED)
        assert workflow.completed_at is not None
        self.assertLess(workflow.completed_at, timezone.now())
        self.assertGreater(workflow.completed_at, prev_completed_at)

    async def test_assign_worker(self) -> None:
        """Assign Worker to WorkRequest."""

        def setup_work_request() -> Worker:
            worker = self.work_request.worker
            assert worker is not None

            self.work_request.worker = None
            self.work_request.save()

            self.work_request.refresh_from_db()

            # Initial status (no worker)
            self.assertIsNone(self.work_request.worker)
            self.assertEqual(
                self.work_request.status, WorkRequest.Statuses.PENDING
            )

            return worker

        worker = await sync_to_async(setup_work_request)()
        assert worker.token is not None

        channel = await self.create_channel(worker.token.hash)

        # Assign the worker to the WorkRequest
        await sync_to_async(self.work_request.assign_worker)(worker)

        await self.assert_channel_received(
            channel, {"type": "work_request.assigned"}
        )

        def assert_final_status() -> None:
            self.work_request.refresh_from_db()

            # Assert final status
            self.assertEqual(self.work_request.worker, worker)
            self.assertEqual(
                self.work_request.status, WorkRequest.Statuses.PENDING
            )

        await sync_to_async(assert_final_status)()

    def test_de_assign_worker_from_running(self) -> None:
        """Worker is de-assigned from a work request (was running)."""
        worker = Worker.objects.create_with_fqdn(
            "test", token=Token.objects.create()
        )
        self.work_request.assign_worker(worker)

        self.work_request.mark_running()

        self.assertEqual(self.work_request.worker, worker)
        self.assertIsInstance(self.work_request.started_at, datetime)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.RUNNING)

        self.assertTrue(self.work_request.de_assign_worker())

        self.work_request.refresh_from_db()

        self.assertIsNone(self.work_request.worker)
        self.assertIsNone(self.work_request.started_at)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)

    def test_de_assign_worker_from_pending(self) -> None:
        """Worker is de-assigned from a work request (was pending)."""
        worker = Worker.objects.create_with_fqdn(
            "test", token=Token.objects.create()
        )
        self.work_request.assign_worker(worker)

        self.assertEqual(self.work_request.worker, worker)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)

        self.assertTrue(self.work_request.de_assign_worker())

        self.work_request.refresh_from_db()

        self.assertIsNone(self.work_request.worker)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)

    def test_de_assign_worker_returns_false(self) -> None:
        """Worker cannot be de-assigned: was not in pending or running."""
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)

        log_msg = (
            f"WorkRequest {self.work_request.pk} cannot be de-assigned: "
            f"current status: {self.work_request.status}"
        )

        with self.assertLogsContains(
            log_msg, logger="debusine.db.models", level=logging.DEBUG
        ):
            self.assertFalse(self.work_request.de_assign_worker())

    def test_duration(self) -> None:
        """duration() returns the correct duration."""
        self.work_request.started_at = datetime(2022, 3, 7, 10, 51)
        self.work_request.completed_at = datetime(2022, 3, 9, 12, 53)
        duration = self.work_request.completed_at - self.work_request.started_at

        self.assertEqual(
            self.work_request.duration,
            duration.total_seconds(),
        )

    def test_duration_is_none_completed_at_is_none(self) -> None:
        """duration() returns None because completed_at is None."""
        self.work_request.started_at = datetime(2022, 3, 7, 10, 51)
        self.work_request.completed_at = None

        self.assertIsNone(self.work_request.duration)

    def test_duration_is_none_started_at_is_none(self) -> None:
        """duration() returns None because started_at is None."""
        self.work_request.started_at = None
        self.work_request.completed_at = datetime(2022, 3, 7, 10, 51)

        self.assertIsNone(self.work_request.duration)

    def test_priority_effective(self) -> None:
        """priority_effective() returns the effective priority."""
        self.work_request.priority_base = 10
        self.work_request.priority_adjustment = 1

        self.assertEqual(self.work_request.priority_effective, 11)

    def test_invalid_pending(self) -> None:
        """Reject invalid pending state change."""
        self.work_request.status = WorkRequest.Statuses.RUNNING
        self.assertFalse(self.work_request.mark_pending())
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.RUNNING)

    def test_add_dependency_within_workflow(self) -> None:
        """Dependencies within a workflow are allowed."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        workflow = self.playground.create_workflow(template, task_data={})
        children = [workflow.create_child("noop") for _ in range(2)]

        children[0].add_dependency(children[1])

        self.assertQuerySetEqual(children[0].dependencies.all(), [children[1]])

    def test_add_dependency_outside_workflow(self) -> None:
        """Dependencies from within a workflow to outside are forbidden."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        workflow = self.playground.create_workflow(template, task_data={})
        child = workflow.create_child("noop")
        outside = self.playground.create_work_request()

        with self.assertRaisesRegex(
            ValueError,
            "Work requests in a workflow may not depend on other work "
            "requests outside that workflow",
        ):
            child.add_dependency(outside)

    def test_add_dependency_auto_block(self) -> None:
        """`add_dependency` automatically blocks PENDING/DEPS work requests."""
        rdep = self.playground.create_work_request()
        rdep.add_dependency(self.work_request)
        self.assertEqual(rdep.status, WorkRequest.Statuses.BLOCKED)

    def test_add_dependency_no_auto_block_not_pending(self) -> None:
        """`add_dependency` doesn't automatically block non-PENDING requests."""
        rdep = self.playground.create_work_request()
        rdep.status = WorkRequest.Statuses.ABORTED
        rdep.save()
        rdep.add_dependency(self.work_request)
        self.assertEqual(rdep.status, WorkRequest.Statuses.ABORTED)

    def test_add_dependency_no_auto_block_not_deps(self) -> None:
        """`add_dependency` doesn't automatically block non-DEPS requests."""
        rdep = self.playground.create_work_request()
        rdep.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        rdep.save()
        rdep.add_dependency(self.work_request)
        self.assertEqual(rdep.status, WorkRequest.Statuses.PENDING)

    def test_add_dependency_no_auto_block_already_completed(self) -> None:
        """`add_dependency` doesn't auto-block on completed dependencies."""
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        rdep = self.playground.create_work_request()
        rdep.add_dependency(self.work_request)
        self.assertEqual(rdep.status, WorkRequest.Statuses.PENDING)

    def create_rdep(self) -> WorkRequest:
        """Create a reverse-dependency for the default work request."""
        rdep = self.playground.create_work_request(
            task_name='rdep', worker=self.work_request.worker
        )
        rdep.add_dependency(self.work_request)
        return rdep

    def test_dependencies_failure(self) -> None:
        """Failure result gets rdep aborted."""
        rdep = self.create_rdep()
        self.work_request.mark_completed(WorkRequest.Results.FAILURE)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.ABORTED)

    def test_dependencies_aborted(self) -> None:
        """Aborting a work request aborts its rdeps."""
        rdep = self.create_rdep()
        self.work_request.mark_aborted()
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.ABORTED)

    def test_dependencies_success_only_dep(self) -> None:
        """Success result, with nothing else to do, gets rdep pending."""
        rdep = self.create_rdep()
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.PENDING)

    def test_dependencies_failure_allow_failure(self) -> None:
        """Failure result, with allow_failure, gets rdep pending."""
        rdep = self.create_rdep()
        self.work_request.workflow_data = WorkRequestWorkflowData(
            allow_failure=True, display_name="test", step="test"
        )
        self.work_request.mark_completed(WorkRequest.Results.FAILURE)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.PENDING)

    def test_dependencies_aborted_allow_failure(self) -> None:
        """Aborting a work request, with allow_failure, keeps rdep blocked."""
        rdep = self.create_rdep()
        self.work_request.workflow_data = WorkRequestWorkflowData(
            allow_failure=True, display_name="test", step="test"
        )
        self.work_request.mark_aborted()
        rdep.refresh_from_db()
        # WorkRequest.can_be_automatically_unblocked says that work requests
        # with unblock_strategy DEPS can only be unblocked if all their
        # dependencies have completed.
        self.assertEqual(rdep.status, WorkRequest.Statuses.BLOCKED)

    def test_dependencies_success_manual(self) -> None:
        """Success result with manual unblock keeps rdep blocked."""
        rdep = self.create_rdep()
        rdep.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        rdep.save()
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.BLOCKED)

    def test_dependencies_success_more_deps(self) -> None:
        """Success result with something else to do keeps rdep blocked."""
        rdep = self.create_rdep()
        work_request2 = self.playground.create_work_request(
            task_name='request-02',
            worker=self.work_request.worker,
            status=WorkRequest.Statuses.RUNNING,
        )
        rdep.add_dependency(work_request2)
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.BLOCKED)

    def test_dependencies_in_pending_workflow(self) -> None:
        """Parent workflow that isn't running yet keeps rdep blocked."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        rdep = workflow.create_child("noop")
        self.work_request.parent = workflow
        self.work_request.save()
        rdep.add_dependency(self.work_request)
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.BLOCKED)

    def test_dependencies_in_running_workflow(self) -> None:
        """Parent workflow in running status gets rdep pending."""
        template = self.playground.create_workflow_template("test", "noop")
        workflow = self.playground.create_workflow(template, task_data={})
        rdep = workflow.create_child("noop")
        self.work_request.parent = workflow
        self.work_request.save()
        rdep.add_dependency(self.work_request)
        workflow.mark_running()
        self.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        rdep.refresh_from_db()
        self.assertEqual(rdep.status, WorkRequest.Statuses.PENDING)

    def test_add_event_reaction(self) -> None:
        """`add_event_reaction` is idempotent."""
        update_collection_with_artifacts = ActionUpdateCollectionWithArtifacts(
            collection="internal@collections",
            name_template="test",
            artifact_filters={"category": ArtifactCategory.TEST},
        )
        self.work_request.add_event_reaction(
            "on_success", update_collection_with_artifacts
        )
        self.assertEqual(
            self.work_request.event_reactions,
            EventReactions(on_success=[update_collection_with_artifacts]),
        )

        self.work_request.add_event_reaction(
            "on_success", update_collection_with_artifacts
        )
        self.assertEqual(
            self.work_request.event_reactions,
            EventReactions(on_success=[update_collection_with_artifacts]),
        )

        send_notification = ActionSendNotification(channel="test")
        self.work_request.add_event_reaction("on_success", send_notification)
        self.work_request.add_event_reaction(
            "on_failure", update_collection_with_artifacts
        )
        self.assertEqual(
            self.work_request.event_reactions,
            EventReactions(
                on_success=[
                    update_collection_with_artifacts,
                    send_notification,
                ],
                on_failure=[update_collection_with_artifacts],
            ),
        )

    def test_create_child_non_workflow(self) -> None:
        """Non-WORKFLOW work requests may not have children."""
        with self.assertRaisesRegex(
            ValueError, r"Only workflows may have child work requests\."
        ):
            self.work_request.create_child("noop")

    def test_create_child_validates_work_request(self) -> None:
        """Creating a child validates the resulting work request."""
        workflow_root = self.playground.create_workflow(task_name="noop")

        with self.assertRaises(ValidationError) as raised:
            workflow_root.create_child(task_name="does-not-exist")

        self.assertEqual(
            raised.exception.message_dict,
            {"task_name": ["does-not-exist: invalid Worker task name"]},
        )

    @preserve_task_registry()
    def test_get_triggered_actions(self) -> None:
        """`get_triggered_actions` combines actions from various sources."""

        class EventReactionsTask(Noop):
            """Task that provides more event reactions."""

            def get_event_reactions(
                self,
                event_name: Literal[
                    "on_creation", "on_unblock", "on_success", "on_failure"
                ],
            ) -> list[EventReaction]:
                """Return event reactions for this task."""
                event_reactions = list(super().get_event_reactions(event_name))
                if event_name == "on_failure":
                    event_reactions.append(ActionRetryWithDelays(delays=["1m"]))
                return event_reactions

        base_actions: dict[str, list[EventReaction]] = {
            action: [] for action in ActionTypes
        }
        base_actions[ActionTypes.RECORD_IN_TASK_HISTORY].append(
            ActionRecordInTaskHistory()
        )

        wr = self.playground.create_work_request(
            task_name="eventreactionstask", task_data=NoopData()
        )
        self.assertEqual(wr.get_triggered_actions("on_success"), base_actions)
        self.assertEqual(
            wr.get_triggered_actions("on_failure"),
            {
                **base_actions,
                ActionTypes.RETRY_WITH_DELAYS: [
                    ActionRetryWithDelays(delays=["1m"])
                ],
            },
        )

        wr.event_reactions = EventReactions(
            on_failure=[ActionSendNotification(channel="admin-team")]
        )
        self.assertEqual(wr.get_triggered_actions("on_success"), base_actions)
        self.assertEqual(
            wr.get_triggered_actions("on_failure"),
            {
                **base_actions,
                ActionTypes.RETRY_WITH_DELAYS: [
                    ActionRetryWithDelays(delays=["1m"])
                ],
                ActionTypes.SEND_NOTIFICATION: [
                    ActionSendNotification(channel="admin-team"),
                ],
            },
        )

    @context.disable_permission_checks()
    def test_update_collection_with_artifacts(self) -> None:
        """Update collection with artifacts on task completion."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        c = self.playground.create_collection(
            name=f"workflow-{wr.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )
        cm = CollectionManagerInterface.get_manager_for(c)
        a_replaced, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST,
            data={
                "deb_fields": {
                    "Package": "actiontest",
                    "Version": "1.0-1",
                },
                "type": "rebuild",
            },
        )
        cm.add_artifact(
            a_replaced, user=wr.created_by, name="actiontest_rebuild"
        )
        item = cm.lookup("name:actiontest_rebuild")
        assert item is not None
        self.assertEqual(item.artifact, a_replaced)

        a_ignored, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST,
            data={
                "deb_fields": {
                    "Package": "ignored",
                    "Version": "1.0-1",
                },
                "type": "rebuild",
            },
        )
        cm.add_artifact(a_ignored, user=wr.created_by, name="ignored_rebuild")
        item = cm.lookup("name:ignored_rebuild")
        assert item is not None
        self.assertEqual(item.artifact, a_ignored)

        a_replacement, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST,
            data={
                'deb_fields': {
                    "Package": "actiontest",
                    "Version": "1.0-1+b1",
                },
                "type": "rebuild",
            },
            work_request=wr,
        )

        a_added, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST,
            data={
                "deb_fields": {
                    "Package": "actiontest2",
                    "Version": "2.0-1+b1",
                },
                "type": "rebuild",
            },
            work_request=wr,
        )

        action = ActionUpdateCollectionWithArtifacts.parse_obj(
            {
                "artifact_filters": {
                    "category": ArtifactCategory.TEST,
                    "data__deb_fields__Package__startswith": "actiontest",
                },
                "collection": "internal@collections",
                "name_template": "{name}_{type}",
                "variables": {
                    "$name": "deb_fields.Package",
                    "$type": "type",
                },
            }
        )
        wr.event_reactions = EventReactions(
            on_success=[action],
            on_failure=[action],
        )
        wr.mark_completed(WorkRequest.Results.SUCCESS)
        self.assertIsNotNone(
            CollectionItem.objects.get(
                parent_collection=c, artifact=a_replaced
            ).removed_at
        )
        item = cm.lookup("name:ignored_rebuild")
        assert item is not None
        self.assertEqual(item.artifact, a_ignored)
        item = cm.lookup("name:actiontest_rebuild")
        assert item is not None
        self.assertEqual(item.artifact, a_replacement)
        self.assertEqual(item.data, {"name": "actiontest", "type": "rebuild"})
        item = cm.lookup("name:actiontest2_rebuild")
        assert item is not None
        self.assertEqual(item.artifact, a_added)
        self.assertEqual(item.data, {"name": "actiontest2", "type": "rebuild"})

    @context.disable_permission_checks()
    def test_update_collection_with_artifacts_no_name_template(self) -> None:
        """Without name_template, variables are passed through."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        c = Collection.objects.create(
            name="debian",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=default_workspace(),
        )
        tarball, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={"architecture": "amd64"},
            work_request=wr,
        )
        action = ActionUpdateCollectionWithArtifacts.parse_obj(
            {
                "artifact_filters": {
                    "category": ArtifactCategory.SYSTEM_TARBALL
                },
                "collection": "debian@debian:environments",
                "variables": {"codename": "bookworm", "variant": "buildd"},
            }
        )
        wr.event_reactions = EventReactions(on_success=[action])

        wr.mark_completed(WorkRequest.Results.SUCCESS)

        item = c.manager.lookup(
            "match:format=tarball:codename=bookworm:architecture=amd64:"
            "variant=buildd"
        )
        assert item is not None
        self.assertIsNotNone(item.artifact, tarball)

    @context.disable_permission_checks()
    def test_update_collection_with_artifacts_constraint_violation(
        self,
    ) -> None:
        """Violating collection constraints is an error."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        self.playground.create_collection(
            name=f"workflow-{wr.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )
        self.playground.create_artifact(
            category=ArtifactCategory.TEST, work_request=wr
        )
        # This action does not specify an item name.
        action = ActionUpdateCollectionWithArtifacts(
            artifact_filters={"category": ArtifactCategory.TEST},
            collection="internal@collections",
        )
        wr.event_reactions = EventReactions(on_success=[action])
        wr.status = WorkRequest.Statuses.RUNNING
        with self.assertLogsContains(
            "Cannot replace or add artifact",
            logger="debusine.db.models",
            level=logging.ERROR,
        ):
            wr.mark_completed(WorkRequest.Results.SUCCESS)

    def test_update_collection_with_artifacts_invalid_artifact_filters(
        self,
    ) -> None:
        """A failure to filter artifacts is an error."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        self.playground.create_collection(
            name=f"workflow-{wr.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )
        action = ActionUpdateCollectionWithArtifacts.parse_obj(
            {
                "artifact_filters": {"xxx": "yyy"},
                "collection": "internal@collections",
            }
        )
        wr.event_reactions = EventReactions(on_success=[action])
        with self.assertLogsContains(
            "Invalid update-collection-with-artifacts artifact_filters",
            logger="debusine.db.models",
            level=logging.ERROR,
        ):
            wr.mark_completed(WorkRequest.Results.SUCCESS)

    @context.disable_permission_checks()
    def test_update_collection_with_artifacts_invalid_variables(self) -> None:
        """A failure to expand variables is an error."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        self.playground.create_collection(
            name=f"workflow-{wr.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )
        self.playground.create_artifact(
            category=ArtifactCategory.TEST, work_request=wr
        )
        action = ActionUpdateCollectionWithArtifacts.parse_obj(
            {
                "artifact_filters": {"category": ArtifactCategory.TEST},
                "collection": "internal@collections",
                "name_template": "{name}",
                "variables": {"$name": "zzz"},
            }
        )
        wr.event_reactions = EventReactions(on_success=[action])
        with self.assertLogsContains(
            "Invalid update-collection-with-artifacts variables",
            logger="debusine.db.models",
            level=logging.ERROR,
        ):
            wr.mark_completed(WorkRequest.Results.SUCCESS)

    def test_update_collection_with_artifacts_different_workspace(self) -> None:
        """The updated collection must be in the same workspace."""
        wr = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        with context.disable_permission_checks():
            different_workspace = self.playground.create_workspace(name="test")
        Collection.objects.create(
            name="test_differentworkspace",
            category=CollectionCategory.WORKFLOW_INTERNAL,
            workspace=different_workspace,
        )
        action = ActionUpdateCollectionWithArtifacts.parse_obj(
            {
                "collection": (
                    "test_differentworkspace@debusine:workflow-internal"
                ),
                "artifact_filters": {"category": ArtifactCategory.TEST},
            }
        )
        wr.event_reactions = EventReactions(on_success=[action])
        with self.assertLogsContains(
            "'test_differentworkspace@debusine:workflow-internal' does not "
            "exist or is hidden",
            logger="debusine.db.models",
            level=logging.ERROR,
        ):
            wr.mark_completed(WorkRequest.Results.SUCCESS)

    def test_update_collection_with_data_on_creation(self) -> None:
        """
        Update collection with data on work request creation.

        This can also be done on completion, but
        `update-collection-with-artifacts` is usually more useful in that
        case.
        """
        workflow = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        c = self.playground.create_collection(
            name=f"workflow-{workflow.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )
        cm = CollectionManagerInterface.get_manager_for(c)

        action = ActionUpdateCollectionWithData.parse_obj(
            {
                "collection": "internal@collections",
                "category": BareDataCategory.TEST,
                "name_template": "{package}_{version}",
                "data": {"package": "hello", "version": "1.0-1"},
            }
        )
        workflow.create_child(
            "noop", event_reactions=EventReactions(on_creation=[action])
        )
        item = cm.lookup("name:hello_1.0-1")
        assert item is not None
        self.assertEqual(item.data, {"package": "hello", "version": "1.0-1"})

    def test_update_collection_with_data_on_unblock(self) -> None:
        """Update collection with data when a work request is unblocked."""
        workflow = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        c = self.playground.create_collection(
            name=f"workflow-{workflow.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )
        cm = CollectionManagerInterface.get_manager_for(c)

        action = ActionUpdateCollectionWithData.parse_obj(
            {
                "collection": "internal@collections",
                "category": BareDataCategory.TEST,
                "name_template": "{package}_{version}",
                "data": {"package": "hello", "version": "1.0-1"},
            }
        )
        wr = workflow.create_child(
            "noop", event_reactions=EventReactions(on_unblock=[action])
        )
        wr.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        wr.save()

        self.assertIsNone(cm.lookup("name:hello_1.0-1"))

        wr.mark_pending()

        item = cm.lookup("name:hello_1.0-1")
        assert item is not None
        self.assertEqual(item.data, {"package": "hello", "version": "1.0-1"})

    def test_update_collection_with_data_no_name_template(self) -> None:
        """Without name_template, name=None is passed."""
        workflow = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        c = self.playground.create_collection(
            name=f"workflow-{workflow.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
        )

        action = ActionUpdateCollectionWithData.parse_obj(
            {
                "collection": "internal@collections",
                "category": BareDataCategory.TEST,
                "data": {"package": "hello", "version": "1.0-1"},
            }
        )
        # We don't currently have a collection that accepts creating bare
        # data items without a name, but that's OK because the log message
        # is reasonably distinctive.
        with self.assertLogsContains(
            f"Cannot replace or add bare data of category "
            f"{BareDataCategory.TEST} to collection {c}",
            logger="debusine.db.models",
            level=logging.ERROR,
        ):
            workflow.create_child(
                "noop", event_reactions=EventReactions(on_creation=[action])
            )

    def test_update_collection_with_data_different_workspace(self) -> None:
        """The updated collection must be in the same workspace."""
        workflow = WorkRequest.objects.create(
            workspace=default_workspace(),
            created_by=self.playground.get_default_user(),
            task_type=TaskTypes.WORKFLOW,
            task_name="test",
        )
        with context.disable_permission_checks():
            different_workspace = self.playground.create_workspace(name="test")
        Collection.objects.create(
            name="test_differentworkspace",
            category=CollectionCategory.WORKFLOW_INTERNAL,
            workspace=different_workspace,
        )
        action = ActionUpdateCollectionWithData.parse_obj(
            {
                "collection": (
                    "test_differentworkspace@debusine:workflow-internal"
                ),
                "category": BareDataCategory.TEST,
            }
        )
        with self.assertLogsContains(
            "'test_differentworkspace@debusine:workflow-internal' does not "
            "exist or is hidden",
            logger="debusine.db.models",
            level=logging.ERROR,
        ):
            workflow.create_child(
                "noop", event_reactions=EventReactions(on_creation=[action])
            )

    def test_retry_with_delays(self) -> None:
        """Automatically retry a work request on failure, with a delay."""
        workflow = self.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        )
        action = ActionRetryWithDelays(
            delays=["5m", "30m", "1h", "2h", "2d", "1w"]
        )
        wr = workflow.create_child(
            "noop",
            status=WorkRequest.Statuses.PENDING,
            event_reactions=EventReactions(on_failure=[action]),
        )

        for retry_count, delay in (
            (1, timedelta(minutes=5)),
            (2, timedelta(minutes=30)),
            (3, timedelta(hours=1)),
            (4, timedelta(hours=2)),
            (5, timedelta(days=2)),
            (6, timedelta(weeks=1)),
        ):
            now = timezone.now()

            with mock.patch("django.utils.timezone.now", return_value=now):
                wr.mark_completed(WorkRequest.Results.FAILURE)

            self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
            self.assertEqual(wr.result, WorkRequest.Results.FAILURE)
            wr_new = wr.superseded
            assert wr_new.workflow_data is not None
            self.assertEqual(wr_new.workflow_data.retry_count, retry_count)
            self.assertEqual(wr_new.dependencies.count(), retry_count)
            wr_delay = wr_new.dependencies.latest("created_at")
            self.assertEqual(wr_delay.status, WorkRequest.Statuses.PENDING)
            self.assertEqual(
                wr_delay.task_data["delay_until"], (now + delay).isoformat()
            )

            # Pretend that the delay task has expired.
            wr_delay.mark_completed(WorkRequest.Results.SUCCESS)
            wr_new.refresh_from_db()

            wr = wr_new

        # Failing once more does not retry again, since the delays list has
        # been exhausted.
        wr.mark_completed(WorkRequest.Results.FAILURE)

        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.FAILURE)
        self.assertFalse(hasattr(wr, "superseded"))

    def test_retry_with_delays_outside_workflow(self) -> None:
        """The retry-with-delays action may only be used in a workflow."""
        wr = self.playground.create_work_request(
            event_reactions=EventReactions(
                on_failure=[ActionRetryWithDelays(delays=["1m"])]
            )
        )

        with self.assertRaisesRegex(
            CannotRetry,
            "retry-with-delays action may only be used in a workflow",
        ):
            wr.mark_completed(WorkRequest.Results.FAILURE)

    def test_record_in_task_history(self) -> None:
        """Record a task run in a task-history collection."""
        wr = self.playground.create_work_request(
            assign_new_worker=True, mark_running=True
        )
        assert wr.started_at is not None
        output_data = OutputData(
            runtime_statistics=RuntimeStatistics(duration=10)
        )
        wr.mark_completed(WorkRequest.Results.SUCCESS, output_data)

        task_history = wr.workspace.get_singleton_collection(
            user=wr.created_by, category=CollectionCategory.TASK_HISTORY
        )
        task_run = task_history.manager.lookup(
            f"last-success:{wr.task_type}:{wr.task_name}::"
        )
        assert task_run is not None
        self.assertEqual(
            task_run.name, f"{wr.task_type}:{wr.task_name}:::{wr.id}"
        )
        self.assertEqual(task_run.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            task_run.category, BareDataCategory.HISTORICAL_TASK_RUN
        )
        self.assertEqual(
            task_run.data,
            {
                "task_type": wr.task_type,
                "task_name": wr.task_name,
                "subject": None,
                "context": None,
                "timestamp": int(wr.started_at.timestamp()),
                "work_request_id": wr.id,
                "result": WorkRequest.Results.SUCCESS,
                "runtime_statistics": {"duration": 10},
            },
        )

    def test_record_in_task_history_no_collection(self) -> None:
        """We don't record a task run if there's no task-history collection."""
        wr = self.playground.create_work_request(
            assign_new_worker=True, mark_running=True
        )
        wr.workspace.get_singleton_collection(
            user=wr.created_by, category=CollectionCategory.TASK_HISTORY
        ).delete()
        output_data = OutputData(
            runtime_statistics=RuntimeStatistics(duration=10)
        )
        wr.mark_completed(WorkRequest.Results.SUCCESS, output_data)

        self.assertFalse(
            CollectionItem.active_objects.filter(
                child_type=CollectionItem.Types.BARE,
                category=BareDataCategory.HISTORICAL_TASK_RUN,
            ).exists()
        )

    def test_record_in_task_history_bad_task_data(self) -> None:
        """We record a task run even if the task data is bad."""
        wr = self.playground.create_work_request(
            assign_new_worker=True, mark_running=True
        )
        assert wr.started_at is not None
        wr.task_data = {"nonsense": 0}
        # Covering this case requires a manually-added event reaction; it
        # isn't added automatically if we can't construct the task.
        wr.event_reactions = EventReactions(
            on_failure=[ActionRecordInTaskHistory()]
        )
        wr.save()
        output_data = OutputData(
            runtime_statistics=RuntimeStatistics(duration=10)
        )
        wr.mark_completed(WorkRequest.Results.FAILURE, output_data)

        task_history = wr.workspace.get_singleton_collection(
            user=wr.created_by, category=CollectionCategory.TASK_HISTORY
        )
        task_run = task_history.manager.lookup(
            f"last-failure:{wr.task_type}:{wr.task_name}::"
        )
        assert task_run is not None
        self.assertEqual(
            task_run.name, f"{wr.task_type}:{wr.task_name}:::{wr.id}"
        )
        self.assertEqual(task_run.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            task_run.category, BareDataCategory.HISTORICAL_TASK_RUN
        )
        self.assertEqual(
            task_run.data,
            {
                "task_type": wr.task_type,
                "task_name": wr.task_name,
                "subject": None,
                "context": None,
                "timestamp": int(wr.started_at.timestamp()),
                "work_request_id": wr.id,
                "result": WorkRequest.Results.FAILURE,
                "runtime_statistics": {"duration": 10},
            },
        )

    def test_record_in_task_history_with_subject_and_context(self) -> None:
        """We record subject and context for the task run if available."""
        source_artifact = self.playground.create_source_artifact(name="hello")
        environment = self.playground.create_debian_environment()
        assert environment.artifact_id is not None
        wr = self.playground.create_work_request(
            assign_new_worker=True,
            mark_running=True,
            task_name="sbuild",
            task_data=SbuildData(
                input=SbuildInput(source_artifact=source_artifact.id),
                host_architecture="amd64",
                environment=environment.artifact_id,
            ),
        )
        assert wr.started_at is not None
        output_data = OutputData(
            runtime_statistics=RuntimeStatistics(duration=10)
        )
        wr.mark_completed(WorkRequest.Results.SUCCESS, output_data)

        task_history = wr.workspace.get_singleton_collection(
            user=wr.created_by, category=CollectionCategory.TASK_HISTORY
        )
        task_run = task_history.manager.lookup(
            f"last-success:{wr.task_type}:{wr.task_name}:"
            f"hello:any%3Aamd64%3Aamd64"
        )
        assert task_run is not None
        self.assertEqual(
            task_run.name,
            f"{wr.task_type}:{wr.task_name}:hello:any%3Aamd64%3Aamd64:{wr.id}",
        )
        self.assertEqual(task_run.child_type, CollectionItem.Types.BARE)
        self.assertEqual(
            task_run.category, BareDataCategory.HISTORICAL_TASK_RUN
        )
        self.assertEqual(
            task_run.data,
            {
                "task_type": wr.task_type,
                "task_name": wr.task_name,
                "subject": "hello",
                "context": "any:amd64:amd64",
                "timestamp": int(wr.started_at.timestamp()),
                "work_request_id": wr.id,
                "result": WorkRequest.Results.SUCCESS,
                "runtime_statistics": {"duration": 10},
            },
        )


class WorkflowTemplateManagerTests(TestCase):
    """Tests for WorkflowTemplateManager."""

    scenario = scenarios.DefaultContext()

    def test_in_current_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter."""
        template = self.playground.create_workflow_template(
            name="test", task_name="noop"
        )
        with context.local():
            context.set_scope(self.scenario.scope)
            self.assertQuerySetEqual(
                WorkflowTemplate.objects.in_current_scope(), [template]
            )

        scope1 = Scope.objects.create(name="Scope1")
        with context.local():
            context.set_scope(scope1)
            self.assertQuerySetEqual(
                WorkflowTemplate.objects.in_current_scope(), []
            )

    def test_in_current_scope_no_context_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter without scope set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "scope is not set"
        ):
            WorkflowTemplate.objects.in_current_scope()

    def test_in_current_workspace(self) -> None:
        """Test the in_current_workspace() QuerySet filter."""
        template = self.playground.create_workflow_template(
            name="test", task_name="noop"
        )
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)

        with context.local():
            self.scenario.workspace.set_current()
            self.assertQuerySetEqual(
                WorkflowTemplate.objects.in_current_workspace(), [template]
            )

        workspace1 = self.playground.create_workspace(name="other", public=True)
        with context.local():
            workspace1.set_current()
            self.assertQuerySetEqual(
                WorkflowTemplate.objects.in_current_workspace(), []
            )

    def test_in_current_workspace_no_context_workspace(self) -> None:
        """Test in_current_workspace() without workspace set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "workspace is not set"
        ):
            WorkflowTemplate.objects.in_current_workspace()


class WorkflowTemplateTests(TestCase):
    """Unit tests for the WorkflowTemplate class."""

    scenario = scenarios.DefaultContext()

    def assert_data_must_be_dictionary(
        self, wt: WorkflowTemplate, messages: list[str] | None = None
    ) -> None:
        """Check that a WorkflowTemplate's task_data is a dictionary."""
        if messages is None:
            messages = ["task data must be a dictionary"]
        with self.assertRaises(ValidationError) as raised:
            wt.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"task_data": messages},
        )

    def test_clean_task_data_must_be_dict(self) -> None:
        """Ensure that task_data is a dict."""
        wt = WorkflowTemplate.objects.create(
            name="test",
            workspace=self.scenario.workspace,
            task_name="noop",
            task_data={},
        )
        wt.full_clean()

        wt.task_data = None
        self.assert_data_must_be_dictionary(wt)

        wt.task_data = ""
        self.assert_data_must_be_dictionary(wt)

        wt.task_data = 3
        self.assert_data_must_be_dictionary(wt)

        wt.task_data = []
        self.assert_data_must_be_dictionary(wt)

        wt.task_data = object()
        self.assert_data_must_be_dictionary(
            wt,
            messages=[
                'Value must be valid JSON.',
                'task data must be a dictionary',
            ],
        )

    @preserve_task_registry()
    def test_validate_task_data(self) -> None:
        """Test orchestrator validation of task data."""

        class ValidatingWorkflow(
            TestWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Workflow used to test validation of template data."""

            @classmethod
            def validate_template_data(cls, data: dict[str, Any]) -> None:
                """data-controlled validation."""
                if msg := data.get("error"):
                    raise ValueError(msg)

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        wt = WorkflowTemplate.objects.create(
            name="test",
            workspace=self.scenario.workspace,
            task_name="validatingworkflow",
            task_data={},
        )
        wt.full_clean()

        wt.task_data = {"error": "example message"}
        with self.assertRaises(ValidationError) as raised:
            wt.full_clean()
        self.assertEqual(
            raised.exception.message_dict,
            {"task_data": ["example message"]},
        )

    def test_predicate_deny_from_context(self) -> None:
        """Test predicates propagating DENY from context_has_role."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        template = self.playground.create_workflow_template("test", "noop")
        with mock.patch(
            "debusine.db.models.workspaces.Workspace.context_has_role",
            return_value=PartialCheckResult.DENY,
        ):
            self.assertFalse(template.can_run(self.scenario.user))

    def test_can_display_delegate_to_workspace(self) -> None:
        """Test the can_display predicate delegating to containing workspace."""
        template = self.playground.create_workflow_template(
            name="test", task_name="noop"
        )
        with override_permission(Workspace, "can_display", AllowAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                allowed=template,
            )
        with override_permission(Workspace, "can_display", DenyAll):
            self.assertPermission(
                "can_display",
                users=(AnonymousUser(), self.scenario.user),
                denied=template,
            )

    def test_can_run_public_workspace(self) -> None:
        """Test can_run on public workspaces."""
        self.scenario.workspace.public = True
        self.scenario.workspace.save()
        template = self.playground.create_workflow_template("test", "noop")

        self.assertPermission(
            "can_run",
            users=(AnonymousUser(), self.scenario.user),
            denied=[template],
        )

    def test_can_run_current_owner(self) -> None:
        """Test can_run on owned workspaces."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        user1 = self.playground.create_user("test1")
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.OWNER,
            users=[self.scenario.user, user1],
        )
        template = self.playground.create_workflow_template("test", "noop")

        self.scenario.set_current()

        # Test shortcut code path
        with self.assertNumQueries(0):
            self.assertTrue(template.can_run(self.scenario.user))

        with self.assertNumQueries(1):
            self.assertTrue(template.can_run(user1))

    def test_can_run_current_contributor(self) -> None:
        """Test can_run on a workspace where the user is a contributor."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        template = self.playground.create_workflow_template("test", "noop")
        user1 = self.playground.create_user("test1")
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user, user1],
        )

        self.scenario.set_current()

        # Test shortcut code path
        with self.assertNumQueries(0):
            self.assertTrue(template.can_run(self.scenario.user))

        with self.assertNumQueries(1):
            self.assertTrue(template.can_run(user1))

    def test_can_run_by_roles(self) -> None:
        """Test can_run behaviour with roles."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        template = self.playground.create_workflow_template("test", "noop")

        self.assertPermissionWhenRole(
            template.can_run,
            self.scenario.user,
            (Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR),
            scope_roles=Scope.Roles.OWNER,
        )

    def test_can_run_with_token(self) -> None:
        """Test can_run with a worker token."""
        self.playground.create_group_role(
            self.scenario.scope, Scope.Roles.OWNER, users=[self.scenario.user]
        )
        template = self.playground.create_workflow_template("test", "noop")

        # Anonymous cannot
        self.assertPermission(
            "can_run",
            users=AnonymousUser(),
            denied=[template],
        )

        # User can
        self.assertPermission(
            "can_run",
            users=self.scenario.user,
            allowed=[template],
        )

        # A worker token cannot in any case
        self.assertPermission(
            "can_run",
            users=(None, AnonymousUser(), self.scenario.user),
            denied=[template],
            token=self.playground.create_worker_token(),
        )

    def test_completed_workflows(self) -> None:
        """Test completed_workflows()."""
        template = self.playground.create_workflow(
            status=WorkRequest.Statuses.COMPLETED
        ).workflow_template

        assert template

        context._workspace.set(template.workspace)

        self.assertEqual(template.completed_workflows(), 1)

    def test_running_workflows(self) -> None:
        """Test running_workflows()."""
        template = self.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        ).workflow_template

        assert template

        context._workspace.set(template.workspace)

        self.assertEqual(template.running_workflows(), 1)

    def test_needs_input_workflows(self) -> None:
        """Test needs_input_workflows()."""
        workflow = self.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        )

        workflow.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.NEEDS_INPUT
        )
        workflow.save()

        assert workflow.workspace

        context._workspace.set(workflow.workspace)

        assert workflow.workflow_template

        self.assertEqual(workflow.workflow_template.needs_input_workflows(), 1)

    def test_get_absolute_url(self) -> None:
        """Test for get_absolute_url."""
        template = self.playground.create_workflow_template("test", "noop")
        self.assertEqual(
            template.get_absolute_url(),
            reverse(
                "workspaces:workflow_templates:detail",
                kwargs={
                    "wname": self.scenario.workspace.name,
                    "name": template.name,
                },
            ),
        )


class DeleteUnusedTests(TestCase):
    """Test DeleteUnused."""

    def test_one(self) -> None:
        """One work request to delete."""
        wr1 = self.playground.create_work_request()
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, {wr1})
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [])

    def test_multiple(self) -> None:
        """Multiple work requests to delete."""
        wr1 = self.playground.create_work_request()
        wr2 = self.playground.create_work_request()
        wr3 = self.playground.create_work_request(parent=wr2)
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, {wr1, wr2, wr3})
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [])

    def test_referenced_by_collection_item(self) -> None:
        """
        Work requests are not unused if referenced by a CollectionItem.

        Keep work requests that:
        - CollectionItem.created_by_workflow -> FK WorkRequest
        - CollectionItem.removed_by_workflow -> FK WorkRequest
        - CollectionItem.id != WorkRequest.internal_collection.id
        """
        collection_1 = self.playground.create_collection(
            "internal-1", CollectionCategory.WORKFLOW_INTERNAL
        )
        collection_2 = self.playground.create_collection(
            "internal-2", CollectionCategory.WORKFLOW_INTERNAL
        )

        work_request_keep = self.playground.create_work_request(
            internal_collection=collection_2,
        )

        work_request_delete = self.playground.create_work_request(
            internal_collection=collection_1,
        )

        # Ensure all work requests are up for deletion (they are not
        # referenced by any CollectionItem yet)
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(
            to_delete.work_requests,
            {work_request_delete, work_request_keep},
        )

        # Make work_request_keep to be kept: referenced by a
        # CollectionItem (via created_by_workflow) and
        # WorkRequest.internal_collection != collection_1
        collection_1.manager.add_bare_data(
            name="Item-1",
            user=self.playground.get_default_user(),
            category=BareDataCategory.TEST,
            workflow=work_request_keep,
        )

        # work_request_delete is in a collection but deleted: referenced
        # by a CollectionItem, but
        # WorkRequest.internal-collection == collection_2
        collection_1.manager.add_bare_data(
            name="Item-2",
            user=self.playground.get_default_user(),
            category=BareDataCategory.TEST,
            workflow=work_request_delete,
        )

        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, {work_request_delete})

        # work_request_keep still kept: now is referenced by a
        # CollectionItem.removed_by_workflow
        collection_1.manager.remove_bare_data(
            name="Item-1",
            user=self.playground.get_default_user(),
            workflow=work_request_keep,
        )

        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, {work_request_delete})

        # Check again if no CollectionItem referenced the work requests:
        # they are deleted
        CollectionItem.objects.all().delete()
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(
            to_delete.work_requests,
            {work_request_keep, work_request_delete},
        )

        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [])

    def test_valid_parent(self) -> None:
        """Keep work requests with non-unused parent."""
        wr1 = self.playground.create_work_request()
        wr2 = self.playground.create_work_request(parent=wr1)
        wr3 = self.playground.create_work_request()
        to_delete = DeleteUnused(
            WorkRequest.objects.filter(pk__in=[wr2.pk, wr3.pk])
        )
        self.assertEqual(to_delete.work_requests, {wr3})
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(
            WorkRequest.objects.all(), [wr1, wr2], ordered=False
        )

    def test_valid_child(self) -> None:
        """Keep work requests with non-unused child."""
        wr1 = self.playground.create_work_request()
        wr2 = self.playground.create_work_request(parent=wr1)
        to_delete = DeleteUnused(WorkRequest.objects.filter(pk__in=[wr1.pk]))
        self.assertEqual(to_delete.work_requests, set())
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(
            WorkRequest.objects.all(), [wr1, wr2], ordered=False
        )

    def test_unused_child_with_build_log(self) -> None:
        """Keep unused work requests unused child which has a build log."""
        user = self.playground.get_default_user()
        wr1 = self.playground.create_work_request()
        wr2 = self.playground.create_work_request(parent=wr1)
        build_log = self.playground.create_build_log_artifact()
        build_logs = wr2.workspace.get_singleton_collection(
            user=user, category=CollectionCategory.PACKAGE_BUILD_LOGS
        )
        build_logs.manager.add_artifact(
            build_log,
            user=user,
            workflow=wr2,
            variables={
                "work_request_id": wr2.id,
                "vendor": "debian",
                "codename": "bookworm",
                "architecture": "amd64",
                "srcpkg_name": "hello-2",
                "srcpkg_version": "1.0-2",
            },
        )

        to_delete = DeleteUnused(
            WorkRequest.objects.filter(pk__in=[wr1.pk, wr2.pk])
        )
        self.assertEqual(to_delete.work_requests, set())
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(
            WorkRequest.objects.all(), [wr1, wr2], ordered=False
        )

    def test_with_collection(self) -> None:
        """Also delete internal collections."""
        c1 = self.playground.create_collection(
            "Collection-1", CollectionCategory.TEST
        )
        wr1 = self.playground.create_work_request(internal_collection=c1)
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, {wr1})
        self.assertEqual(to_delete.collections, {c1})
        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [])
        self.assertQuerySetEqual(Collection.objects.filter(pk=c1.pk), [])

    def test_delete_with_internal_collection(self) -> None:
        """Delete a work request referenced by an internal collection item."""
        collection = self.playground.create_collection(
            "internal", CollectionCategory.WORKFLOW_INTERNAL
        )

        work_request = self.playground.create_work_request(
            internal_collection=collection
        )

        collection.manager.add_bare_data(
            name="Item",
            user=self.playground.get_default_user(),
            category=BareDataCategory.TEST,
            workflow=work_request,
        )

        # Work request is now expired but referenced by an item in an internal
        # collection: it should be deleted
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, {work_request})

        # Check that it is actually deleted
        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [])
        self.assertQuerySetEqual(
            Collection.objects.filter(pk=collection.pk), []
        )

    def test_delete_workflow_with_build_logs(self) -> None:
        """Work requests referenced by build logs are kept."""
        user = self.playground.get_default_user()
        wr1 = self.playground.create_workflow()
        build_log = self.playground.create_build_log_artifact()
        build_logs = wr1.workspace.get_singleton_collection(
            user=user, category=CollectionCategory.PACKAGE_BUILD_LOGS
        )
        build_logs.manager.add_artifact(
            build_log,
            user=user,
            workflow=wr1,
            variables={
                "work_request_id": wr1.id,
                "vendor": "debian",
                "codename": "bookworm",
                "architecture": "amd64",
                "srcpkg_name": "hello-2",
                "srcpkg_version": "1.0-2",
            },
        )
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, set())
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [wr1])

    def test_delete_work_request_with_build_logs(self) -> None:
        """Work requests referenced by build logs are kept."""
        user = self.playground.get_default_user()
        wr1 = self.playground.create_work_request()
        build_log = self.playground.create_build_log_artifact()
        build_logs = wr1.workspace.get_singleton_collection(
            user=user, category=CollectionCategory.PACKAGE_BUILD_LOGS
        )
        build_logs.manager.add_artifact(
            build_log,
            user=user,
            workflow=wr1,
            variables={
                "work_request_id": wr1.id,
                "vendor": "debian",
                "codename": "bookworm",
                "architecture": "amd64",
                "srcpkg_name": "hello-2",
                "srcpkg_version": "1.0-2",
            },
        )
        to_delete = DeleteUnused(WorkRequest.objects.all())
        self.assertEqual(to_delete.work_requests, set())
        self.assertEqual(to_delete.collections, set())
        to_delete.perform_deletions()
        self.assertQuerySetEqual(WorkRequest.objects.all(), [wr1])


class ConfigureForWorkerTests(TestCase):
    """test :py:meth:`WorkRequest.configure_for_worker`."""

    scenario = scenarios.DefaultContext()

    config_collection: ClassVar[Collection]
    environment: ClassVar[Artifact]
    source: ClassVar[Artifact]
    worker: ClassVar[Worker]
    work_request: ClassVar[WorkRequest]
    task_database: ClassVar[TaskDatabase]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.config_collection = cls.playground.create_collection(
            "test", CollectionCategory.TASK_CONFIGURATION
        )

        environment_item = cls.playground.create_debian_environment()
        assert environment_item.artifact is not None
        cls.environment = environment_item.artifact
        cls.source = cls.playground.create_source_artifact()
        cls.work_request = cls.playground.create_sbuild_work_request(
            source=cls.source, environment=cls.environment, architecture="amd64"
        )
        cls.work_request.task_data["task_configuration"] = (
            "test@debusine:task-configuration"
        )
        cls.work_request.save()
        cls.worker = cls.playground.create_worker()

    @classmethod
    def _add_config(cls, entry: DebusineTaskConfiguration) -> CollectionItem:
        """Add a config entry to config_collection."""
        manager = DebusineTaskConfigurationManager(
            collection=cls.config_collection
        )
        return manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=cls.scenario.user,
            data=entry,
        )

    def test_no_task_config(self) -> None:
        """Test without configuration to apply."""
        task_data = {
            "backend": "unshare",
            "build_components": ["any"],
            "environment": self.environment.pk,
            "host_architecture": "amd64",
            "input": {"source_artifact": self.source.pk},
            "task_configuration": "test@debusine:task-configuration",
        }
        self.work_request.configure_for_worker(self.worker)
        self.assertEqual(self.work_request.task_data, task_data)
        self.assertEqual(self.work_request.configured_task_data, task_data)
        self.assertEqual(
            self.work_request.dynamic_task_data,
            {
                "binnmu_maintainer": (
                    f"Debusine <noreply@{settings.DEBUSINE_FQDN}>"
                ),
                "configuration_context": "bookworm",
                "environment_id": self.environment.pk,
                "input_extra_binary_artifacts_ids": [],
                "input_source_artifact_id": self.source.pk,
                "runtime_context": "any:amd64:amd64",
                "subject": "hello",
                "task_configuration_id": self.config_collection.pk,
            },
        )

    def test_with_task_config(self) -> None:
        """Test with overrides to apply."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="sbuild",
            subject="hello",
            default_values={"host_architecture": "arm64"},
            override_values={"build_profiles": ["nodoc"]},
        )
        self._add_config(entry)

        task_data = {
            "backend": "unshare",
            "build_components": ["any"],
            "environment": self.environment.pk,
            "host_architecture": "amd64",
            "input": {"source_artifact": self.source.pk},
            "task_configuration": "test@debusine:task-configuration",
        }
        self.work_request.configure_for_worker(self.worker)
        # Configured task data is not reflected in work_request.task_data
        self.assertEqual(self.work_request.task_data, task_data)
        # It is found in configured_task_data
        self.assertEqual(
            self.work_request.configured_task_data,
            task_data | {"build_profiles": ["nodoc"]},
        )
        # It also affects dynamic_task_data
        self.assertEqual(
            self.work_request.dynamic_task_data,
            {
                "binnmu_maintainer": (
                    f"Debusine <noreply@{settings.DEBUSINE_FQDN}>"
                ),
                "configuration_context": "bookworm",
                "environment_id": self.environment.pk,
                "input_extra_binary_artifacts_ids": [],
                "input_source_artifact_id": self.source.pk,
                # This is None because task-configuration has set
                # build_profiles to nodoc
                "runtime_context": None,
                "subject": "hello",
                "task_configuration_id": self.config_collection.pk,
            },
        )

    def test_apply_defaults(self) -> None:
        """Test with defaults to apply."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="sbuild",
            subject="hello",
            default_values={
                "binnmu": {"changelog": "", "suffix": "+b1"},
                "build_profiles": ["nodoc"],
            },
        )
        self._add_config(entry)

        # Explicitly set to None
        self.work_request.task_data["build_profiles"] = None
        self.work_request.configure_for_worker(self.worker)
        # Configured task data is not reflected in work_request.task_data
        self.assertEqual(
            self.work_request.task_data,
            {
                "backend": "unshare",
                "build_components": ["any"],
                "build_profiles": None,
                "environment": self.environment.pk,
                "host_architecture": "amd64",
                "input": {"source_artifact": self.source.pk},
                "task_configuration": "test@debusine:task-configuration",
            },
        )
        # It is found in configured_task_data
        self.assertEqual(
            self.work_request.configured_task_data,
            {
                "backend": "unshare",
                "binnmu": {"changelog": "", "suffix": "+b1"},
                "build_components": ["any"],
                "build_profiles": ["nodoc"],
                "environment": self.environment.pk,
                "host_architecture": "amd64",
                "input": {"source_artifact": self.source.pk},
                "task_configuration": "test@debusine:task-configuration",
            },
        )
        # It also affects dynamic_task_data
        self.assertEqual(
            self.work_request.dynamic_task_data,
            {
                "binnmu_maintainer": (
                    f"Debusine <noreply@{settings.DEBUSINE_FQDN}>"
                ),
                "configuration_context": "bookworm",
                "environment_id": self.environment.pk,
                "input_extra_binary_artifacts_ids": [],
                "input_source_artifact_id": self.source.pk,
                # This is None because task-configuration has set
                # build_profiles to nodoc
                "runtime_context": None,
                "subject": "hello",
                "task_configuration_id": self.config_collection.pk,
            },
        )

    def test_invalid_config(self) -> None:
        """Test with invalid configuration data."""
        entry = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="sbuild",
            subject="hello",
            default_values={"invalid": True},
        )
        self._add_config(entry)
        with self.assertRaisesRegex(
            TaskConfigError,
            r"(?s)Configured task data is invalid.+extra fields not permitted",
        ):
            self.work_request.configure_for_worker(self.worker)

    def test_used_task_data(self) -> None:
        """Ensure used_task_data picks the right data."""
        self.work_request.task_data = {}
        self.work_request.configured_task_data = None
        self.assertEqual(self.work_request.used_task_data, {})

        self.work_request.task_data = {"test": 1}
        self.work_request.configured_task_data = None
        self.assertEqual(self.work_request.used_task_data, {"test": 1})

        self.work_request.task_data = {"test": 1}
        self.work_request.configured_task_data = {}
        self.assertEqual(self.work_request.used_task_data, {})

        self.work_request.task_data = {"test": 1}
        self.work_request.configured_task_data = {"test": 2}
        self.assertEqual(self.work_request.used_task_data, {"test": 2})
