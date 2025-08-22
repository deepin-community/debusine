# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the work request views."""

import re
from base64 import b64encode
from datetime import datetime
from logging import getLogger
from typing import Any, ClassVar
from unittest import mock

from django.contrib.auth import get_user_model
from django.db.models import Max
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from rest_framework import status

from debusine.artifacts.models import (
    ArtifactCategory,
    ArtifactData,
    DebianSourcePackage,
    DebianUpload,
    TaskTypes,
)
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Token,
    WorkRequest,
    Worker,
    Workspace,
    default_workspace,
)
from debusine.db.playground import scenarios
from debusine.server.scopes import urlconf_scope
from debusine.server.serializers import WorkRequestSerializer
from debusine.server.tasks.wait.models import ExternalDebsignData
from debusine.server.views.work_requests import WorkRequestPagination
from debusine.server.workflows.models import (
    WorkRequestManualUnblockAction,
    WorkRequestWorkflowData,
)
from debusine.test.django import (
    AllowAll,
    DenyAll,
    JSONResponseProtocol,
    TestCase,
    TestResponseType,
    override_permission,
)
from debusine.test.test_utils import date_time_to_isoformat_rest_framework


class WorkRequestTestCase(TestCase):
    """Helper methods for tests with WorkRequest as response."""

    def check_response_for_work_request(
        self,
        response: TestResponseType,
        work_request: WorkRequest,
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Ensure the json data corresponds to the supplied work request."""

        def _timestamp(ts: datetime | None) -> str | None:
            if ts is None:
                return None
            return date_time_to_isoformat_rest_framework(ts)

        artifact_ids = list(
            work_request.artifact_set.all()
            .order_by("id")
            .values_list("id", flat=True)
        )

        with urlconf_scope(work_request.workspace.scope.name):
            work_request_url = work_request.get_absolute_url()

        self.assertEqual(
            response.json(),
            {
                'id': work_request.pk,
                'url': work_request_url,
                'task_name': work_request.task_name,
                'created_at': _timestamp(work_request.created_at),
                'started_at': _timestamp(work_request.started_at),
                'completed_at': _timestamp(work_request.completed_at),
                'duration': work_request.duration,
                'worker': work_request.worker_id,
                'task_type': work_request.task_type,
                'task_data': (
                    work_request.configured_task_data
                    if work_request.configured_task_data
                    else work_request.task_data
                ),
                'dynamic_task_data': (
                    dynamic_task_data
                    if work_request.dynamic_task_data is None
                    else work_request.dynamic_task_data
                ),
                'priority_base': work_request.priority_base,
                'priority_adjustment': work_request.priority_adjustment,
                'workflow_data': work_request.workflow_data_json,
                'event_reactions': work_request.event_reactions_json,
                'status': work_request.status,
                'result': work_request.result,
                'artifacts': artifact_ids,
                'scope': work_request.workspace.scope.name,
                'workspace': work_request.workspace.name,
                'created_by': work_request.created_by.id,
                'aborted_by': (
                    None
                    if work_request.aborted_by is None
                    else work_request.aborted_by.id
                ),
            },
        )

    def check_response_for_work_request_list(
        self,
        response: TestResponseType,
        work_requests: list[WorkRequest],
        start: int = 0,
        page_size: int | None = None,
    ) -> None:
        """Ensure the json data corresponds to this list of work requests."""

        def _timestamp(ts: datetime | None) -> str | None:
            if ts is None:
                return None
            return date_time_to_isoformat_rest_framework(ts)

        def _encode_cursor(
            *, reverse: bool = False, position: str | None = None
        ) -> str:
            tokens: dict[str, int | str] = {}
            if reverse:
                tokens['r'] = 1
            if position is not None:  # pragma: no cover
                tokens['p'] = position
            querystring = urlencode(tokens, doseq=True)
            return b64encode(querystring.encode('ascii')).decode('ascii')

        next_url = None
        previous_url = None
        if page_size is not None:
            if start + page_size < len(work_requests):
                next_cursor = _encode_cursor(
                    position=str(
                        work_requests[start + page_size - 1].created_at
                    )
                )
                next_url = (
                    'http://testserver'
                    + reverse('api:work-requests')
                    + '?'
                    + urlencode({'cursor': next_cursor})
                )
            if start > 0:
                previous_cursor = _encode_cursor(
                    position=str(work_requests[start].created_at),
                    reverse=True,
                )
                previous_url = (
                    'http://testserver'
                    + reverse('api:work-requests')
                    + '?'
                    + urlencode({'cursor': previous_cursor})
                )
            work_requests = work_requests[start : start + page_size]

        expected_results: list[dict[str, Any]] = []
        for work_request in work_requests:
            with urlconf_scope(work_request.workspace.scope.name):
                work_request_url = work_request.get_absolute_url()
            expected_results.append(
                {
                    'id': work_request.pk,
                    'url': work_request_url,
                    'task_name': work_request.task_name,
                    'created_at': _timestamp(work_request.created_at),
                    'started_at': _timestamp(work_request.started_at),
                    'completed_at': _timestamp(work_request.completed_at),
                    'duration': work_request.duration,
                    'worker': work_request.worker_id,
                    'task_type': work_request.task_type,
                    'task_data': work_request.task_data,
                    'dynamic_task_data': None,
                    'priority_base': work_request.priority_base,
                    'priority_adjustment': work_request.priority_adjustment,
                    'workflow_data': work_request.workflow_data_json,
                    'event_reactions': work_request.event_reactions_json,
                    'status': work_request.status,
                    'result': work_request.result,
                    'artifacts': list(
                        work_request.artifact_set.all()
                        .order_by("id")
                        .values_list("id", flat=True)
                    ),
                    'scope': work_request.workspace.scope.name,
                    'workspace': work_request.workspace.name,
                    'created_by': work_request.created_by.id,
                    'aborted_by': (
                        None
                        if work_request.aborted_by is None
                        else work_request.aborted_by.id
                    ),
                }
            )

        self.assertEqual(
            response.json(),
            {
                'next': next_url,
                'previous': previous_url,
                'results': expected_results,
            },
        )

    def assert_response_work_request_id_exists(
        self, response: TestResponseType, workspace: Workspace
    ) -> None:
        """Assert that there is a WorkRequest with id == response["id"]."""
        work_request_id = response.json()["id"]
        self.assertTrue(
            WorkRequest.objects.filter(
                id=work_request_id, workspace=workspace
            ).exists()
        )


class WorkRequestViewTests(WorkRequestTestCase):
    """Tests for WorkRequestView class."""

    scenario = scenarios.DefaultContextAPI()
    artifact: ClassVar[Artifact]

    def setUp(self) -> None:
        """Set up common data."""
        super().setUp()
        self.worker_01 = Worker.objects.create_with_fqdn(
            "worker-01", token=self.playground.create_bare_token()
        )
        self.playground.create_worker_token(self.worker_01)

        # This token would be used by a debusine client using the API
        self.token = self.playground.create_user_token()

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.artifact = cls.playground.create_source_artifact()

    def create_test_work_request(
        self, workspace: Workspace | None = None
    ) -> WorkRequest:
        """Create a work request for testing."""
        work_request = self.playground.create_work_request(
            worker=self.worker_01,
            task_name='sbuild',
            task_data={'architecture': 'amd64'},
            status=WorkRequest.Statuses.PENDING,
            workspace=workspace,
        )
        return work_request

    def create_superuser_token(self) -> Token:
        """Create a token for a superuser."""
        user = get_user_model().objects.create_superuser(
            username="testsuperuser",
            email="superuser@mail.none",
            password="testsuperpass",
        )
        return self.playground.create_user_token(user=user)

    def post_work_requests(
        self,
        work_request_serialized: dict[str, Any],
        token: Token | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """Post work_request to the endpoint api:work-requests."""
        if token is None:
            token = self.token
        headers = {"Token": token.key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope

        response = self.client.post(
            reverse('api:work-requests'),
            data=work_request_serialized,
            headers=headers,
            content_type="application/json",
        )

        return response

    def test_check_permissions_get_token_must_be_enabled(self) -> None:
        """An enabled token is needed to request a WorkRequest."""
        self.token.disable()
        self.token.save()
        work_request = self.create_test_work_request()

        response = self.client.get(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={"token": self.token.key},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_check_permissions_post_token_must_have_user_associated(
        self,
    ) -> None:
        """The token must have a user associated for creating a WorkRequest."""
        token = Token.objects.create()
        token.enable()
        token.save()

        work_request_serialized = {
            "task_name": "sbuild",
            "task_data": {"foo": "bar"},
        }

        response = self.client.post(
            reverse('api:work-requests'),
            data=work_request_serialized,
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_work_request_success(self) -> None:
        """Return HTTP 200 and the correct information for the WorkRequest."""
        assert self.worker_01.token is not None
        work_request = self.create_test_work_request()

        response = self.client.get(
            reverse(
                'api:work-request-detail',
                kwargs={"work_request_id": work_request.id},
            ),
            headers={"token": self.worker_01.token.key},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(response, work_request)

    def test_get_work_request_worker_activation_token(self) -> None:
        """A worker activation token cannot get a work request."""
        token = self.playground.create_worker_activation_token()
        work_request = self.create_test_work_request()

        response = self.client.get(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={"token": token.key},
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="You do not have permission to perform this action.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_get_work_request_not_found(self) -> None:
        """Return HTTP 404 if the WorkRequest id does not exist."""
        assert self.worker_01.token is not None
        self.create_test_work_request()

        max_id = WorkRequest.objects.aggregate(Max('id'))['id__max']
        response = self.client.get(
            reverse(
                'api:work-request-detail',
                kwargs={'work_request_id': max_id + 1},
            ),
            headers={"token": self.worker_01.token.key},
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_work_request_honours_scope(self) -> None:
        """Getting a work request looks it up in the current scope."""
        assert self.worker_01.token is not None
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        work_request = self.create_test_work_request(workspace=workspace)

        response = self.client.get(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": self.worker_01.token.key,
                "X-Debusine-Scope": work_request.workspace.scope.name,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(response, work_request)

        response = self.client.get(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": self.worker_01.token.key,
                "X-Debusine-Scope": scope2.name,
            },
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_work_request_private_workspace_unauthorized(self) -> None:
        """Work requests in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        work_request = self.create_test_work_request(
            workspace=private_workspace
        )

        response = self.client.get(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": self.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_work_request_private_workspace_authorized(self) -> None:
        """Work requests in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        work_request = self.create_test_work_request(
            workspace=private_workspace
        )

        response = self.client.get(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": self.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(response, work_request)

    def verify_post_success(
        self,
        *,
        workspace: Workspace | None = None,
        input_artifact_id: int | None = None,
    ) -> None:
        """Client POST a WorkRequest and verifies it gets created."""
        work_request_serialized = {
            'task_name': 'sbuild',
            'task_data': {
                'input': {
                    'source_artifact': input_artifact_id or self.artifact.id
                },
                'host_architecture': 'amd64',
                'environment': 'debian/match:codename=bookworm',
            },
        }

        if workspace is not None:
            work_request_serialized["workspace"] = workspace.name

        with (
            self.captureOnCommitCallbacks(execute=True),
            mock.patch(
                "debusine.server.scheduler.schedule_task.delay"
            ) as mock_schedule_task_delay,
        ):
            response = self.post_work_requests(
                work_request_serialized,
                scope=workspace.scope.name if workspace is not None else None,
            )
        work_request = WorkRequest.objects.latest('created_at')

        # Response contains the serialized WorkRequest
        with urlconf_scope(work_request.workspace.scope.name):
            self.assertEqual(
                response.json(), WorkRequestSerializer(work_request).data
            )

        # Response contains fields that were posted
        self.assertDictContainsAll(response.json(), work_request_serialized)

        # A scheduler run was requested via Celery
        mock_schedule_task_delay.assert_called_once()

        expected_workspace = workspace or default_workspace()
        self.assertEqual(response.json()["workspace"], expected_workspace.name)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_response_work_request_id_exists(
            response, expected_workspace
        )

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=False)
    def test_post_success_workspace_testing(self) -> None:
        """Client POST a WorkRequest with a specific workspace."""
        workspace = self.playground.create_workspace()
        workspace.name = "Testing"
        workspace.save()
        self.playground.create_group_role(
            workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.playground.create_debian_environment(
            codename="bookworm", workspace=workspace
        )
        artifact = self.playground.create_source_artifact(workspace=workspace)

        self.verify_post_success(
            workspace=workspace, input_artifact_id=artifact.id
        )

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=False)
    def test_post_success_workspace_default(self) -> None:
        """Client POST a WorkRequest without specifying a workspace."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        self.playground.create_debian_environment(codename="bookworm")
        self.verify_post_success()

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=False)
    def test_post_success_workspace_default_superuser(self) -> None:
        """Client POST a WorkRequest without specifying a workspace."""
        self.token = self.create_superuser_token()
        assert self.token.user is not None
        self.playground.add_user(
            self.scenario.workspace_owners, self.token.user
        )
        self.playground.create_debian_environment(codename="bookworm")
        self.verify_post_success()

    @override_settings(DISABLE_AUTOMATIC_SCHEDULING=False)
    def test_post_work_request_honours_scope(self) -> None:
        """POST checks that the workspace is in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        scope3 = self.playground.get_or_create_scope("scope3")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="common-name", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="common-name", public=True
        )

        for workspace in (workspace1, workspace2):
            self.playground.create_group_role(
                workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
            )
            self.playground.create_debian_environment(
                codename="bookworm", workspace=workspace
            )
            self.verify_post_success(workspace=workspace)

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(
                {"workspace": workspace1.name, "task_name": "noop"},
                scope=scope3.name,
            )
        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern="Workspace common-name not found in scope scope3",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_work_request_no_default_workspace(self) -> None:
        """POST with no workspace in a scope without a default workspace."""
        assert self.worker_01.token is not None
        scope = self.playground.get_or_create_scope("empty-scope")

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(
                {"task_name": "noop"}, scope=scope.name
            )

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request",
            validation_errors_pattern=(
                r"'workspace': \['This field is required\.'\]"
            ),
        )

    def test_post_work_request_unauthorized(self) -> None:
        """POST without the right permission returns 403."""
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests({"task_name": "noop"})

        self.assertResponseProblem(
            response,
            f"{self.scenario.user} cannot create work requests in "
            f"{self.scenario.workspace}",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_post_task_name_invalid(self) -> None:
        """Client POST a WorkRequest its task_name is invalid."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        work_request_serialized = {
            'task_name': 'bad-name',
            'task_data': {'foo': 'bar'},
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response,
            "Cannot create work request: task name is not registered",
            '^Task name: "bad-name". Registered task names: .*sbuild.*$',
        )

    def test_post_non_worker_task(self) -> None:
        """Client POST a WorkRequest without enough rights for that type."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        work_request_serialized = {
            'task_name': 'servernoop',
            'task_data': {'foo': 'bar'},
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response,
            "Cannot create work request: task name is not registered",
            '^Task name: "servernoop"',
        )

    def test_post_non_worker_task_superuser(self) -> None:
        """Superusers can POST a server task."""
        token = self.create_superuser_token()
        assert token.user is not None
        self.playground.add_user(self.scenario.workspace_owners, token.user)

        work_request_serialized = {
            'task_name': 'servernoop',
            'task_data': {},
        }
        response = self.post_work_requests(work_request_serialized, token=token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_response_work_request_id_exists(
            response, default_workspace()
        )

    def test_post_work_request_too_many_fields(self) -> None:
        """Post request with too many fields: returns HTTP 400 and error."""
        work_request_serialized = {
            'task_name': 'sbuild',
            'task_data': {'foo': 'bar'},
            'result': 'SUCCESS',
        }
        response = self.post_work_requests(work_request_serialized)

        with self.assert_model_count_unchanged(WorkRequest):
            self.assertResponseProblem(
                response,
                "Cannot deserialize work request",
                validation_errors_pattern="Invalid fields: result",
            )

        assert isinstance(response, JSONResponseProtocol)
        self.assertIsInstance(response.json()["validation_errors"], dict)

    def test_post_work_request_invalid_channel(self) -> None:
        """Post request with an invalid channel: returns HTTP 400 and error."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        channel = "non-existing"
        work_request_serialized = {
            "task_name": "sbuild",
            "event_reactions": {
                "on_failure": [
                    {"action": "send-notification", "channel": channel}
                ]
            },
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response, f"Non-existing channels: ['{channel}']"
        )

    def test_post_work_request_invalid_reaction_events(self) -> None:
        """Post request with an invalid channel: returns HTTP 400 and error."""
        work_request_serialized = {
            "task_name": "sbuild",
            "event_reactions": {"on_invalid": []},
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request",
            validation_errors_pattern="Invalid event_reactions",
        )

    def test_post_work_request_invalid_data(self) -> None:
        """Post request with an invalid data: returns HTTP 400 and error."""
        work_request_serialized = {
            "task_name": "sbuild",
            "event_reactions": {"on_failure": [{"channel": "my-changes"}]},
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request",
            validation_errors_pattern="Invalid event_reactions",
        )

    def test_post_work_request_compute_dynamic_data_raise_exception(
        self,
    ) -> None:
        """
        Post request with data which compute_dynamic_data_raise exception.

        Returns HTTP 400 and error.
        """
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        work_request_serialized = {
            "task_name": "sbuild",
            "task_data": {
                "build_components": ["any", "all"],
                "host_architecture": "amd64",
                "input": {"source_artifact": 536},
                "environment": "NON-EXISTING/match:codename=trixie",
            },
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response,
            "Cannot create work request: error computing dynamic data",
            detail_pattern="^Task data: .*Error: .*",
        )

    def test_post_work_request_compute_dynamic_data_assertion_failed(
        self,
    ) -> None:
        """Trigger an AssertionError in compute_dynamic_data_raise."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        work_request_serialized = {
            "task_name": "sbuild",
            "task_data": {
                "build_components": ["any", "all"],
                "host_architecture": "amd64",
                "input": {"source_artifact": 536},
                'environment': 'debian/match:codename=bookworm',
            },
        }
        with (
            self.assert_model_count_unchanged(WorkRequest),
            mock.patch(
                "debusine.tasks.sbuild.Sbuild.compute_dynamic_data",
                side_effect=AssertionError,
            ),
            self.assertLogs(
                logger=getLogger("debusine.server.views.work_requests"),
            ) as logs,
        ):
            response = self.post_work_requests(work_request_serialized)

        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(
            record.msg,
            (
                "Assertion failed computing dynamic data for task %s with "
                "data %s"
            ),
        )
        self.assertIsNotNone(record.exc_info)

        self.assertResponseProblem(
            response,
            "Cannot create work request: error computing dynamic data",
            detail_pattern="^Task data: .*Error: .*",
        )

    def test_post_work_request_disallowed_action(self) -> None:
        """Action other than send-notification: returns HTTP 400 and error."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        work_request_serialized = {
            "task_name": "sbuild",
            "event_reactions": {
                "on_success": [
                    {
                        "action": "update-collection-with-artifacts",
                        "collection": 1,
                        "artifact_filters": {},
                    }
                ]
            },
        }
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_work_requests(work_request_serialized)

        self.assertResponseProblem(
            response,
            "Invalid event_reactions",
            detail_pattern=(
                "Action type 'update-collection-with-artifacts' is not "
                "allowed here"
            ),
        )

    def test_list_work_request_empty(self) -> None:
        """Return an empty list if there are no work requests."""
        assert self.worker_01.token is not None
        response = self.client.get(
            reverse('api:work-requests'),
            headers={"token": self.worker_01.token.key},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(response, [])

    def test_list_work_request_one_page(self) -> None:
        """Return a single page if there are only a few work requests."""
        assert self.worker_01.token is not None
        work_requests = list(
            reversed([self.create_test_work_request() for _ in range(3)])
        )

        with mock.patch.object(WorkRequestPagination, 'page_size', 5):
            response = self.client.get(
                reverse('api:work-requests'),
                headers={"token": self.worker_01.token.key},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(response, work_requests)

    def test_list_work_request_honours_scope(self) -> None:
        """Listing work requests filters to the current scope."""
        assert self.worker_01.token is not None
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="workspace1", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="workspace2", public=True
        )
        work_requests: dict[str, list[WorkRequest]] = {}
        for workspace in (workspace1, workspace2):
            work_requests[str(workspace)] = list(
                reversed(
                    [
                        self.create_test_work_request(workspace=workspace)
                        for _ in range(3)
                    ]
                )
            )

        for workspace in (workspace1, workspace2):
            with mock.patch.object(WorkRequestPagination, "page_size", 5):
                response = self.client.get(
                    reverse("api:work-requests"),
                    headers={
                        "Token": self.worker_01.token.key,
                        "X-Debusine-Scope": workspace.scope.name,
                    },
                )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.check_response_for_work_request_list(
                response, work_requests[str(workspace)]
            )

    def test_list_work_request_private_workspace(self) -> None:
        """Listing work requests only shows those that can be displayed."""
        assert self.worker_01.token is not None
        private_workspace = self.playground.create_workspace(name="Private")
        work_requests = list(
            reversed(
                [
                    self.create_test_work_request(workspace=private_workspace)
                    for _ in range(3)
                ]
            )
        )

        # An unprivileged user sees nothing.
        response = self.client.get(
            reverse("api:work-requests"),
            headers={
                "Token": self.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(response, [])

        # A worker sees all the work requests.
        response = self.client.get(
            reverse("api:work-requests"),
            headers={
                "Token": self.worker_01.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(response, work_requests)

        # A user with permission to see the private workspace sees its work
        # requests.
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        response = self.client.get(
            reverse("api:work-requests"),
            headers={
                "Token": self.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(response, work_requests)

    def test_list_work_request_multiple_pages(self) -> None:
        """Return multiple pages if there are many work requests."""
        assert self.worker_01.token is not None
        work_requests = list(
            reversed([self.create_test_work_request() for _ in range(10)])
        )

        with mock.patch.object(WorkRequestPagination, 'page_size', 5):
            response = self.client.get(
                reverse('api:work-requests'),
                headers={"token": self.worker_01.token.key},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(
            response, work_requests, start=0, page_size=5
        )

        with mock.patch.object(WorkRequestPagination, 'page_size', 5):
            response = self.client.get(
                response.json()['next'],
                headers={"token": self.worker_01.token.key},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request_list(
            response, work_requests, start=5, page_size=5
        )

    def test_patch_work_request_success(self) -> None:
        """Patch a work request and return its new representation."""
        work_request = self.create_test_work_request()
        token = self.playground.create_user_token()
        assert token.user is not None
        self.playground.add_user_permission(
            token.user, WorkRequest, "manage_workrequest_priorities"
        )

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={"priority_adjustment": 100},
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        work_request.refresh_from_db()
        self.assertEqual(work_request.priority_adjustment, 100)
        self.check_response_for_work_request(response, work_request)

    def test_patch_work_request_empty(self) -> None:
        """An empty patch to a work request does nothing."""
        work_request = self.create_test_work_request()
        token = self.playground.create_user_token()

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={},
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        work_request.refresh_from_db()
        self.assertEqual(work_request.priority_adjustment, 0)
        self.check_response_for_work_request(response, work_request)

    def test_patch_work_request_honours_scope(self) -> None:
        """Patching a work request looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        work_request = self.create_test_work_request(workspace=workspace)
        token = self.playground.create_user_token()

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": token.key,
                "X-Debusine-Scope": work_request.workspace.scope.name,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": token.key,
                "X-Debusine-Scope": scope2.name,
            },
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_patch_work_request_anonymous(self) -> None:
        """Anonymous users may not patch work requests."""
        work_request = self.create_test_work_request()

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={"priority_adjustment": -100},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Authentication credentials were not provided.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_patch_work_request_private_workspace_unauthorized(self) -> None:
        """Work requests in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        work_request = self.create_test_work_request(
            workspace=private_workspace
        )

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": self.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_patch_work_request_private_workspace_authorized(self) -> None:
        """Work requests in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        work_request = self.create_test_work_request(
            workspace=private_workspace
        )

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            headers={
                "Token": self.token.key,
                "X-Debusine-Scope": private_workspace.scope.name,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(response, work_request)

    def test_patch_work_request_own_request_negative_adjustment(self) -> None:
        """Users can set negative adjustments on their own work requests."""
        work_request = self.create_test_work_request()
        token = self.playground.create_user_token(user=work_request.created_by)

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={"priority_adjustment": -100},
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        work_request.refresh_from_db()
        self.assertEqual(work_request.priority_adjustment, -100)
        self.check_response_for_work_request(response, work_request)

    def test_patch_work_request_own_request_positive_adjustment(self) -> None:
        """Users cannot set positive adjustments on their own work requests."""
        work_request = self.create_test_work_request()
        token = self.playground.create_user_token(user=work_request.created_by)

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={"priority_adjustment": 100},
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            "You are not permitted to set priority adjustments",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_patch_work_request_without_permission(self) -> None:
        """Setting positive adjustments requires a special permission."""
        work_request = self.create_test_work_request()
        token = self.playground.create_user_token()

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={"priority_adjustment": 100},
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            "You are not permitted to set priority adjustments",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_patch_work_request_disallowed_property(self) -> None:
        """Only certain properties of work requests may be patched."""
        work_request = self.create_test_work_request()
        token = self.playground.create_user_token()
        assert token.user is not None
        self.playground.add_user_permission(
            token.user, WorkRequest, "manage_workrequest_priorities"
        )

        response = self.client.patch(
            reverse(
                "api:work-request-detail",
                kwargs={"work_request_id": work_request.id},
            ),
            data={"priority_base": 100},
            headers={"token": token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request update",
            validation_errors_pattern="Invalid fields: priority_base",
        )


class WorkRequestRetryViewTests(TestCase):
    """Tests for WorkRequestRetryView class."""

    scenario = scenarios.DefaultContextAPI()
    work_request: ClassVar[WorkRequest]
    token: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.work_request = cls.playground.create_work_request(
            task_name="noop", workspace=cls.scenario.workspace
        )

    def post(
        self,
        work_request_id: int | None = None,
        token: Token | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """Post work_request to the endpoint api:work-requests."""
        headers = {"Token": (token or self.scenario.user_token).key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.post(
            reverse(
                'api:work-requests-retry',
                kwargs={
                    "work_request_id": (
                        self.work_request.id
                        if work_request_id is None
                        else work_request_id
                    )
                },
            ),
            data={},
            headers=headers,
            content_type="application/json",
        )

    def test_check_permissions_post_token_must_be_enabled(self) -> None:
        """An enabled token is needed to retry a WorkRequest."""
        self.scenario.user_token.disable()
        self.scenario.user_token.save()
        response = self.post(token=self.scenario.user_token)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_check_permissions_post_token_must_have_user_associated(
        self,
    ) -> None:
        """The token must have a user for retrying a WorkRequest."""
        self.scenario.user_token.user = None
        self.scenario.user_token.save()
        response = self.post(token=self.scenario.user_token)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_work_request_not_found(self) -> None:
        """Return HTTP 404 if the WorkRequest id does not exist."""
        max_id = WorkRequest.objects.aggregate(Max('id'))['id__max']
        response = self.post(work_request_id=max_id + 1)
        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_cannot_retry(self) -> None:
        """Target work request cannot be retried."""
        with override_permission(WorkRequest, "can_retry", DenyAll):
            self.assertFalse(self.work_request.verify_retry())
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertResponseProblem(
            response,
            "Cannot retry work request",
            detail_pattern="Only aborted or failed tasks can be retried",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    @override_permission(WorkRequest, "can_retry", AllowAll)
    def test_post_success(self) -> None:
        """Client POST retries the WorkRequest and the new gets created."""
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()
        self.assertFalse(hasattr(self.work_request, "superseded"))
        self.assertTrue(self.work_request.verify_retry())
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.work_request.refresh_from_db()
        self.assertTrue(hasattr(self.work_request, "superseded"))

        # Response contains the new WorkRequest
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(
            response.json(),
            WorkRequestSerializer(self.work_request.superseded).data,
        )

    def test_post_honours_scope(self) -> None:
        """Retrying a work request looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )

        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        self.playground.create_group_role(
            workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )

        work_request = self.playground.create_work_request(
            workspace=workspace,
            task_name="noop",
            status=WorkRequest.Statuses.ABORTED,
        )

        response = self.post(work_request_id=work_request.id, scope=scope2.name)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.post(
            work_request_id=work_request.id,
            scope=work_request.workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(hasattr(work_request, "superseded"))

    def test_post_private_workspace_unauthorized(self) -> None:
        """Retrying in a private workspace 404s without `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.work_request.workspace = private_workspace
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        response = self.post()

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_private_workspace_authorized(self) -> None:
        """Retrying in a private workspace succeeds with `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.work_request.workspace = private_workspace
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertTrue(hasattr(self.work_request, "superseded"))


class WorkRequestAbortViewTests(WorkRequestTestCase):
    """Tests for WorkRequestAbortView class."""

    scenario = scenarios.DefaultContextAPI()
    work_request: ClassVar[WorkRequest]
    token: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.work_request = cls.playground.create_work_request(
            task_name="noop", workspace=cls.scenario.workspace
        )

    def post(
        self,
        work_request_id: int | None = None,
        token: Token | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """Post work_request to the endpoint api:work-request-abort."""
        headers = {"Token": (token or self.scenario.user_token).key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.post(
            reverse(
                "api:work-request-abort",
                kwargs={
                    "work_request_id": (
                        self.work_request.id
                        if work_request_id is None
                        else work_request_id
                    )
                },
            ),
            data={},
            headers=headers,
            content_type="application/json",
        )

    def test_check_permissions_post_token_must_be_enabled(self) -> None:
        """An enabled token is needed to abort a WorkRequest."""
        self.scenario.user_token.disable()
        self.scenario.user_token.save()
        response = self.post(token=self.scenario.user_token)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_check_permissions_post_token_must_have_user_associated(
        self,
    ) -> None:
        """The token must have a user for aborting a WorkRequest."""
        self.scenario.user_token.user = None
        self.scenario.user_token.save()
        response = self.post(token=self.scenario.user_token)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_work_request_not_found(self) -> None:
        """Return HTTP 404 if the WorkRequest id does not exist."""
        max_id = WorkRequest.objects.aggregate(Max("id"))["id__max"]
        response = self.post(work_request_id=max_id + 1)
        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_cannot_abort(self) -> None:
        """Target work request cannot be aborted."""
        self.work_request.mark_aborted()
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertResponseProblem(
            response,
            "Cannot abort work request",
            detail_pattern=(
                "Only pending, blocked, or running tasks can be aborted"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    @override_permission(WorkRequest, "can_abort", AllowAll)
    def test_post_success(self) -> None:
        """Client POST aborts the WorkRequest."""
        self.assertTrue(self.work_request.verify_abort())
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.aborted_by, self.scenario.user)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)
        self.check_response_for_work_request(response, self.work_request)

    def test_post_honours_scope(self) -> None:
        """Aborting a work request looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        work_request = self.playground.create_work_request(
            workspace=workspace, task_name="noop"
        )

        response = self.post(work_request_id=work_request.id, scope=scope2.name)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.post(
            work_request_id=work_request.id,
            scope=work_request.workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        work_request.refresh_from_db()
        self.assertEqual(work_request.aborted_by, self.scenario.user)
        self.assertEqual(work_request.status, WorkRequest.Statuses.ABORTED)

    def test_post_private_workspace_unauthorized(self) -> None:
        """Aborting in a private workspace 404s without `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.post()

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_private_workspace_authorized(self) -> None:
        """Aborting in a private workspace succeeds with `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.aborted_by, self.scenario.user)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)


class WorkRequestUnblockViewTests(WorkRequestTestCase):
    """Tests for :py:class:`WorkRequestUnblockView`."""

    scenario = scenarios.DefaultContextAPI()
    workflow: ClassVar[WorkRequest]
    work_request: ClassVar[WorkRequest]
    token: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.workflow = cls.playground.create_workflow(
            status=WorkRequest.Statuses.RUNNING
        )

        cls.work_request = WorkRequest.objects.create_synchronization_point(
            parent=cls.workflow,
            step="test",
            status=WorkRequest.Statuses.BLOCKED,
        )
        cls.work_request.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        cls.work_request.save()
        # This token would be used by a debusine client using the API
        cls.token = cls.scenario.user_token

    def post(
        self,
        work_request_id: int | None = None,
        token: Token | None = None,
        scope: str | None = None,
        notes: str | None = None,
        action: WorkRequestManualUnblockAction | None = None,
    ) -> TestResponseType:
        """Post work_request to the endpoint api:work-request-unblock."""
        headers = {"Token": (token or self.token).key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        data: dict[str, str] = {}
        if notes is not None:
            data["notes"] = notes
        if action is not None:
            data["action"] = action
        return self.client.post(
            reverse(
                'api:work-request-unblock',
                kwargs={
                    "work_request_id": (
                        self.work_request.id
                        if work_request_id is None
                        else work_request_id
                    )
                },
            ),
            data=data,
            headers=headers,
            content_type="application/json",
        )

    def test_post_token_must_be_enabled(self) -> None:
        """An enabled token is needed to unblock a WorkRequest."""
        self.token.disable()
        self.token.save()

        response = self.post(token=self.token)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_token_must_have_user_associated(self) -> None:
        """The token must have a user for unblocking a WorkRequest."""
        self.token.user = None
        self.token.save()

        response = self.post(token=self.token)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_without_permission(self) -> None:
        """Unblocking work requests requires a special permission."""
        response = self.post(work_request_id=self.work_request.id, notes="x")

        self.assertResponseProblem(
            response,
            f"{self.token.user} cannot unblock {self.work_request.id}",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_post_neither_notes_nor_action(self) -> None:
        """At least one of notes and action must be set."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        response = self.post(work_request_id=self.work_request.id)

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request unblock",
            validation_errors_pattern=(
                "At least one of notes and action must be set"
            ),
        )

    def test_post_work_request_not_found(self) -> None:
        """Return HTTP 404 if the WorkRequest id does not exist."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )

        max_id = WorkRequest.objects.aggregate(Max("id"))["id__max"]
        response = self.post(work_request_id=max_id + 1)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="No WorkRequest matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_not_manual(self) -> None:
        """The work request must have the manual unblock strategy."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )

        self.work_request.unblock_strategy = WorkRequest.UnblockStrategy.DEPS
        self.work_request.save()

        response = self.post(work_request_id=self.work_request.id, notes="x")

        self.assertResponseProblem(
            response,
            f"Work request {self.work_request.id} cannot be manually unblocked",
        )

    def test_post_not_part_of_workflow(self) -> None:
        """The work request must be part of a workflow."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        work_request = self.playground.create_work_request(
            task_name="noop",
            status=WorkRequest.Statuses.BLOCKED,
            unblock_strategy=WorkRequest.UnblockStrategy.MANUAL,
        )

        response = self.post(work_request_id=work_request.id, notes="x")

        self.assertResponseProblem(
            response,
            f"Work request {work_request.id} is not part of a workflow",
        )

    def test_post_not_blocked(self) -> None:
        """The work request must currently be blocked."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )
        self.work_request.status = WorkRequest.Statuses.PENDING
        self.work_request.save()

        response = self.post(work_request_id=self.work_request.id, notes="x")

        self.assertResponseProblem(
            response, f"Work request {self.work_request.id} cannot be unblocked"
        )

    def test_post_accept(self) -> None:
        """Accept a blocked work request."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )

        response = self.post(
            work_request_id=self.work_request.id,
            action=WorkRequestManualUnblockAction.ACCEPT,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)
        assert self.work_request.workflow_data is not None
        manual_unblock = self.work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 1)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, timezone.now())
        self.assertIsNone(manual_unblock.log[0].notes)
        self.assertEqual(
            manual_unblock.log[0].action, WorkRequestManualUnblockAction.ACCEPT
        )
        assert isinstance(response, HttpResponse)
        self.check_response_for_work_request(response, self.work_request)

    def test_post_reject(self) -> None:
        """Reject a blocked work request."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )

        response = self.post(
            work_request_id=self.work_request.id,
            notes="Go away",
            action=WorkRequestManualUnblockAction.REJECT,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)
        assert self.work_request.workflow_data is not None
        manual_unblock = self.work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 1)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, timezone.now())
        self.assertEqual(manual_unblock.log[0].notes, "Go away")
        self.assertEqual(
            manual_unblock.log[0].action, WorkRequestManualUnblockAction.REJECT
        )
        assert isinstance(response, HttpResponse)
        self.check_response_for_work_request(response, self.work_request)

    def test_post_record_notes_only(self) -> None:
        """Record notes on a blocked work request."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )

        response = self.post(
            work_request_id=self.work_request.id, notes="Not sure"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.BLOCKED)
        assert self.work_request.workflow_data is not None
        manual_unblock = self.work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 1)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, timezone.now())
        self.assertEqual(manual_unblock.log[0].notes, "Not sure")
        self.assertIsNone(manual_unblock.log[0].action)
        assert isinstance(response, HttpResponse)
        self.check_response_for_work_request(response, self.work_request)

    def test_post_reject_retry_accept(self) -> None:
        """Reject a blocked work request, then retry it, then accept it."""
        self.playground.add_user(
            self.scenario.workspace_owners, self.scenario.user
        )

        response = self.post(
            work_request_id=self.work_request.id,
            notes="Go away",
            action=WorkRequestManualUnblockAction.REJECT,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reject_timestamp = timezone.now()

        self.work_request.refresh_from_db()
        retried_work_request = self.work_request.retry()

        response = self.post(
            work_request_id=retried_work_request.id,
            notes="OK then",
            action=WorkRequestManualUnblockAction.ACCEPT,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        retried_work_request.refresh_from_db()
        self.assertEqual(
            retried_work_request.status, WorkRequest.Statuses.PENDING
        )
        assert retried_work_request.workflow_data is not None
        manual_unblock = retried_work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 2)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, reject_timestamp)
        self.assertEqual(manual_unblock.log[0].notes, "Go away")
        self.assertEqual(
            manual_unblock.log[0].action, WorkRequestManualUnblockAction.REJECT
        )
        self.assertEqual(manual_unblock.log[1].user_id, self.scenario.user.id)
        self.assertGreater(manual_unblock.log[1].timestamp, reject_timestamp)
        self.assertLess(manual_unblock.log[1].timestamp, timezone.now())
        self.assertEqual(manual_unblock.log[1].notes, "OK then")
        self.assertEqual(
            manual_unblock.log[1].action, WorkRequestManualUnblockAction.ACCEPT
        )
        assert isinstance(response, HttpResponse)
        self.check_response_for_work_request(response, retried_work_request)

    def test_post_honours_scope(self) -> None:
        """Unblocking a work request looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.work_request.workspace = workspace
        self.work_request.save()
        self.playground.create_group_role(
            workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        response = self.post(
            work_request_id=self.work_request.id,
            scope=scope2.name,
            action=WorkRequestManualUnblockAction.ACCEPT,
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.post(
            work_request_id=self.work_request.id,
            scope=workspace.scope.name,
            action=WorkRequestManualUnblockAction.ACCEPT,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)

    def test_post_private_workspace_unauthorized(self) -> None:
        """Unblocking in a private workspace 404s without `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.post(action=WorkRequestManualUnblockAction.ACCEPT)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_private_workspace_authorized(self) -> None:
        """Unblocking in a private workspace succeeds with `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.post(action=WorkRequestManualUnblockAction.ACCEPT)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)


class WorkRequestExternalDebsignViewTests(WorkRequestTestCase):
    """Tests for :py:class:`WorkRequestExternalDebsignView`."""

    scenario = scenarios.DefaultContextAPI()
    unsigned_files: ClassVar[dict[str, bytes]]
    unsigned: ClassVar[Artifact]
    work_request: ClassVar[WorkRequest]
    token: ClassVar[Token]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.unsigned_files = {
            "foo_1.0.orig.tar.gz": b"orig.tar.gz",
            "foo_1.0.dsc": b"unsigned dsc",
            "foo_1.0_source.changes": b"unsigned changes",
        }
        cls.unsigned, _ = cls.playground.create_artifact(
            paths=cls.unsigned_files,
            category=ArtifactCategory.UPLOAD,
            data=DebianUpload(
                type="dpkg",
                changes_fields={
                    "Architecture": "source",
                    "Files": [{"name": name} for name in cls.unsigned_files],
                },
            ),
            create_files=True,
            skip_add_files_in_store=True,
        )
        cls.work_request = cls.playground.create_work_request(
            mark_running=True,
            task_type=TaskTypes.WAIT,
            task_name="externaldebsign",
            task_data=ExternalDebsignData(
                unsigned=f"{cls.unsigned.id}@artifacts"
            ),
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )
        cls.token = cls.scenario.user_token

    def get(
        self,
        *,
        work_request_id: int | None = None,
        token: Token | None = None,
        scope: str | None = None,
    ) -> TestResponseType:
        """
        Get the work request from api:work-request-external-debsign.

        :param work_request_id: ID of the work request to get, defaulting to
          the ID of `self.work_request`.
        :param token: authentication token to send, defaulting to
          `self.token`.
        :param scope: scope name to send.
        """
        headers = {"Token": (token or self.token).key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.get(
            reverse(
                "api:work-request-external-debsign",
                kwargs={
                    "work_request_id": (
                        self.work_request.id
                        if work_request_id is None
                        else work_request_id
                    )
                },
            ),
            headers=headers,
        )

    def post(
        self,
        *,
        work_request_id: int | None = None,
        token: Token | None = None,
        scope: str | None = None,
        signed: Artifact,
    ) -> TestResponseType:
        """
        Post a signed artifact to api:work-request-external-debsign.

        :param work_request_id: ID of the work request to post to,
          defaulting to the ID of `self.work_request`.
        :param token: authentication token to send, defaulting to
          `self.token`.
        :param scope: scope name to send.
        :param signed: the signed artifact to post.
        """
        headers = {"Token": (token or self.token).key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.post(
            reverse(
                "api:work-request-external-debsign",
                kwargs={
                    "work_request_id": (
                        self.work_request.id
                        if work_request_id is None
                        else work_request_id
                    )
                },
            ),
            data={"signed_artifact": signed.id},
            headers=headers,
            content_type="application/json",
        )

    @classmethod
    def create_signed_artifact(
        cls,
        paths: list[str] | dict[str, bytes] | None = None,
        category: ArtifactCategory = ArtifactCategory.UPLOAD,
        data: ArtifactData | None = None,
    ) -> Artifact:
        """
        Create a signed upload artifact.

        :param paths: list of paths to create (with random data), or
          dictionary mapping paths to create to their data; defaults to a
          structurally-valid signed upload
        :param category: the upload artifact category, defaulting to
          `debian:upload`
        """
        if paths is None:
            paths = {
                **cls.unsigned_files,
                "foo_1.0.dsc": b"signed dsc",
                "foo_1.0_source.changes": b"signed changes",
            }
        if data is None:
            data = DebianUpload(
                type="dpkg",
                changes_fields={
                    "Architecture": "source",
                    "Files": [{"name": name} for name in paths],
                },
            )
        signed, _ = cls.playground.create_artifact(
            paths=paths,
            category=category,
            data=data,
            create_files=True,
            skip_add_files_in_store=True,
        )
        return signed

    def test_get_token_must_be_enabled(self) -> None:
        """A `GET` request needs an enabled token."""
        self.token.disable()
        self.token.save()

        response = self.get()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_token_must_have_user_associated(self) -> None:
        """A `GET` request needs a token with a user."""
        self.token.user = None
        self.token.save()

        response = self.get()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_work_request_not_found(self) -> None:
        """A `GET` request returns 404 if the work request does not exist."""
        max_id = WorkRequest.objects.aggregate(Max("id"))["id__max"]

        response = self.get(work_request_id=max_id + 1)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="No WorkRequest matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_wrong_work_request(self) -> None:
        """A `GET` request requires the right kind of work request."""
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.WORKER, task_name="noop"
        )

        response = self.get(work_request_id=work_request.id)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=(
                "Expected work request to be Wait/ExternalDebsign; got "
                "Worker/noop"
            ),
        )

    def test_get_work_request_not_running(self) -> None:
        """A `GET` request requires that the work request be running."""
        self.work_request.mark_aborted()

        response = self.get()

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Work request is not running. Status: aborted",
        )

    def test_get_bad_task_data(self) -> None:
        """A `GET` request fails on bad task data."""
        self.work_request.task_data = {"unsigned": ""}
        self.work_request.save()

        response = self.get()

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Cannot process task data: Empty lookup",
        )

    def test_get_success(self) -> None:
        """A successful `GET` request returns the serialized work request."""
        response = self.get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )

    def test_get_different_user(self) -> None:
        """A `GET` request does not have to be sent by the WR creator."""
        user = get_user_model().objects.create_user(
            username="another", email="another@example.org"
        )
        token = self.playground.create_user_token(user=user)

        response = self.get(token=token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )

    def test_get_honours_scope(self) -> None:
        """A `GET` request looks up the work request in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.work_request.workspace = workspace
        self.work_request.save()

        response = self.get(scope=scope2.name)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.get(scope=workspace.scope.name)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )

    def test_get_private_workspace_unauthorized(self) -> None:
        """Work requests in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.get(scope=private_workspace.scope.name)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_private_workspace_authorized(self) -> None:
        """Work requests in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.get(scope=private_workspace.scope.name)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )

    def test_post_token_must_be_enabled(self) -> None:
        """A `POST` request needs an enabled token."""
        self.token.disable()
        self.token.save()

        response = self.post(signed=self.create_signed_artifact())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_token_must_have_user_associated(self) -> None:
        """A `POST` request needs a token with a user."""
        self.token.user = None
        self.token.save()

        response = self.post(signed=self.create_signed_artifact())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_work_request_not_found(self) -> None:
        """A `POST` request returns 404 if the work request does not exist."""
        max_id = WorkRequest.objects.aggregate(Max("id"))["id__max"]

        response = self.post(
            work_request_id=max_id + 1, signed=self.create_signed_artifact()
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="No WorkRequest matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_wrong_work_request(self) -> None:
        """A `POST` request requires the right kind of work request."""
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        artifact = self.create_signed_artifact()

        response = self.post(
            work_request_id=work_request.id,
            signed=artifact,
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=(
                "Expected work request to be Wait/ExternalDebsign; got "
                "Worker/noop"
            ),
        )

    def test_post_work_request_not_running(self) -> None:
        """A `POST` request requires that the work request be running."""
        self.work_request.mark_aborted()

        response = self.post(signed=self.create_signed_artifact())

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Work request is not running. Status: aborted",
        )

    def test_post_bad_task_data(self) -> None:
        """A `POST` request fails on bad task data."""
        self.work_request.task_data = {"unsigned": ""}
        self.work_request.save()

        response = self.post(signed=self.create_signed_artifact())

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Cannot process task data: Empty lookup",
        )

    def test_post_wrong_user(self) -> None:
        """A `POST` request may only be sent by the user who created the WR."""
        user = get_user_model().objects.create_user(
            username="another", email="another@example.org"
        )
        token = self.playground.create_user_token(user=user)
        artifact = self.create_signed_artifact()

        response = self.post(token=token, signed=artifact)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=(
                "Only the user who created the work request can provide a "
                "signature"
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_post_wrong_category(self) -> None:
        """A `POST` request checks the category of the signed artifact."""
        signed = self.create_signed_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data=DebianSourcePackage(
                name="hello", version="1.0", type="dpkg", dsc_fields={}
            ),
        )

        response = self.post(signed=signed)

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request",
            validation_errors_pattern=(
                "Expected signed artifact of category debian:upload; got "
                "debian:source-package"
            ),
        )

    def test_post_signed_artifact_already_created_by_work_request(self) -> None:
        """The signed artifact must not have been created by a work request."""
        signed = self.create_signed_artifact()
        signed.created_by_work_request = self.playground.create_work_request()
        signed.save()

        response = self.post(signed=signed)

        self.assertResponseProblem(
            response,
            "Cannot deserialize work request",
            validation_errors_pattern=(
                "Signed artifact must not have been created by a work request"
            ),
        )

    def test_post_signed_artifact_new_files(self) -> None:
        """The signed artifact may not add new files."""
        signed = self.create_signed_artifact(
            paths={
                **self.unsigned_files,
                "foo_1.0-1.debian.tar.xz": b"debian.tar.xz",
            }
        )

        response = self.post(signed=signed)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=re.escape(
                "Signed upload adds extra files when compared to unsigned "
                "upload. New paths: ['foo_1.0-1.debian.tar.xz']"
            ),
        )

    def test_post_signed_artifact_unchanged_changes_file(self) -> None:
        """The signed artifact must change the `.changes` file."""
        signed = self.create_signed_artifact(paths={**self.unsigned_files})

        response = self.post(signed=signed)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Signed upload does not change the .changes file",
        )

    def test_post_signed_artifact_changed_wrong_files(self) -> None:
        """The signed artifact may not change unsignable files."""
        signed = self.create_signed_artifact(
            paths={
                **self.unsigned_files,
                "foo_1.0.orig.tar.gz": b"new orig.tar.gz",
                "foo_1.0_source.changes": b"signed changes",
            }
        )

        response = self.post(signed=signed)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=re.escape(
                "Signed upload changes more than .changes/.dsc/.buildinfo. "
                "Changed paths: "
                "['foo_1.0.orig.tar.gz', 'foo_1.0_source.changes']"
            ),
        )

    def test_post_signed_artifact_success(self) -> None:
        """A successful `POST` request completes and returns 200."""
        signed = self.create_signed_artifact()

        response = self.post(signed=signed)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertQuerySetEqual(
            ArtifactRelation.objects.filter(artifact=signed).values(
                "artifact", "target", "type"
            ),
            [
                {
                    "artifact": signed.id,
                    "target": self.unsigned.id,
                    "type": ArtifactRelation.Relations.RELATES_TO,
                }
            ],
        )
        self.work_request.refresh_from_db()
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )
        self.assertEqual(
            self.work_request.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(self.work_request.result, WorkRequest.Results.SUCCESS)
        signed.refresh_from_db()
        self.assertEqual(signed.created_by_work_request, self.work_request)

    def test_post_honours_scope(self) -> None:
        """A `POST` request looks up the work request in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        self.work_request.workspace = workspace
        self.work_request.save()
        signed = self.create_signed_artifact()

        response = self.post(scope=scope2.name, signed=signed)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.post(scope=workspace.scope.name, signed=signed)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )
        self.assertEqual(
            self.work_request.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(self.work_request.result, WorkRequest.Results.SUCCESS)

    def test_post_private_workspace_unauthorized(self) -> None:
        """`POST` in a private workspace 404s without `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.work_request.workspace = private_workspace
        self.work_request.save()
        signed = self.create_signed_artifact()

        response = self.post(signed=signed)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern=r"No WorkRequest matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_private_workspace_authorized(self) -> None:
        """`POST` in a private workspace succeeds with `can_display`."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.work_request.workspace = private_workspace
        self.work_request.save()
        signed = self.create_signed_artifact()

        response = self.post(signed=signed)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.work_request.refresh_from_db()
        self.check_response_for_work_request(
            response,
            self.work_request,
            dynamic_task_data={"unsigned_id": self.unsigned.id},
        )
        self.assertEqual(
            self.work_request.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(self.work_request.result, WorkRequest.Results.SUCCESS)
