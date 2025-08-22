# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the worker views."""

import logging
import secrets
from datetime import datetime
from datetime import timezone as tz
from typing import Any, ClassVar

from django.db.models import Max
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from debusine.artifacts.models import RuntimeStatistics
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import Token, WorkRequest, Worker
from debusine.server.views.rest import IsWorkerAuthenticated
from debusine.server.views.tests.test_work_requests import WorkRequestTestCase
from debusine.server.views.workers import (
    GetNextWorkRequestView,
    UpdateWorkRequestAsCompletedView,
    UpdateWorkerDynamicMetadataView,
)
from debusine.tasks.models import (
    OutputData,
    SbuildData,
    SbuildInput,
    WorkerType,
)
from debusine.test.django import TestCase


class RegisterViewTests(TestCase):
    """Tests for the RegisterView class."""

    def test_create_token_and_worker(self) -> None:
        """Token and Worker are created by the view."""
        self.assertQuerySetEqual(Token.objects.all(), [])
        self.assertQuerySetEqual(Worker.objects.all(), [])

        time_start = timezone.now()

        token_key = secrets.token_hex(32)

        data = {'token': token_key, 'fqdn': 'worker-bee.lan'}

        response = self.client.post(reverse('api:register'), data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        token = Token.objects.get_token_or_none(token_key=token_key)
        assert token is not None

        worker = token.worker

        # Assert created worker
        self.assertEqual(worker.name, 'worker-bee-lan')
        self.assertGreaterEqual(worker.registered_at, time_start)
        self.assertLessEqual(worker.registered_at, timezone.now())
        self.assertIsNone(worker.connected_at)
        self.assertEqual(worker.worker_type, WorkerType.EXTERNAL)

        # Assert created token
        self.assertGreaterEqual(token.created_at, time_start)
        self.assertLessEqual(token.created_at, timezone.now())
        self.assertIsNone(token.user)
        self.assertEqual(token.comment, '')
        self.assertFalse(token.enabled)

    def test_create_token_and_worker_duplicate_name(self) -> None:
        """Token is created and Worker disambiguated if needed."""
        token_1 = Token()
        token_1.save()

        Worker.objects.create_with_fqdn('worker-lan', token=token_1)

        token_key = secrets.token_hex(32)
        data = {'token': token_key, 'fqdn': 'worker.lan'}
        response = self.client.post(reverse('api:register'), data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertTrue(Worker.objects.filter(name='worker-lan-2').exists())

    def test_create_token_and_worker_signing(self) -> None:
        """Register a signing worker."""
        self.assertQuerySetEqual(Token.objects.all(), [])
        self.assertQuerySetEqual(Worker.objects.all(), [])

        time_start = timezone.now()

        token_key = secrets.token_hex(32)

        data = {
            'token': token_key,
            'fqdn': 'worker-bee.lan',
            'worker_type': WorkerType.SIGNING,
        }

        response = self.client.post(reverse('api:register'), data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        token = Token.objects.get_token_or_none(token_key=token_key)
        assert token is not None

        worker = token.worker

        # Assert created worker
        self.assertEqual(worker.name, 'worker-bee-lan')
        self.assertGreaterEqual(worker.registered_at, time_start)
        self.assertLessEqual(worker.registered_at, timezone.now())
        self.assertIsNone(worker.connected_at)
        self.assertEqual(worker.worker_type, WorkerType.SIGNING)

        # Assert created token
        self.assertGreaterEqual(token.created_at, time_start)
        self.assertLessEqual(token.created_at, timezone.now())
        self.assertIsNone(token.user)
        self.assertEqual(token.comment, '')

    def test_invalid_data(self) -> None:
        """Request is refused if we have bad data."""
        data = {
            'token': secrets.token_hex(128),  # Too long
            'fqdn': 'worker.lan',
        }

        response = self.client.post(reverse('api:register'), data)

        self.assertResponseProblem(
            response,
            "Cannot deserialize worker",
            validation_errors_pattern=(
                r"Ensure this field has no more than .* characters"
            ),
        )
        self.assertIsInstance(response.json()["validation_errors"], dict)

        self.assertFalse(Worker.objects.filter(name='worker-lan').exists())

    def test_activation_token(self) -> None:
        """Register using an activation token."""
        activation_token = self.playground.create_worker_activation_token()
        worker = activation_token.activating_worker
        assert worker is not None
        token_key = secrets.token_hex(32)
        data = {"token": token_key, "fqdn": "worker.lan"}

        response = self.client.post(
            reverse("api:register"),
            data,
            headers={"token": activation_token.key},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        worker.refresh_from_db()
        self.assertIsNone(worker.activation_token)
        token = Token.objects.get_token_or_none(token_key=token_key)
        assert token is not None
        self.assertEqual(token.worker, worker)
        self.assertTrue(token.enabled)
        self.assertFalse(
            Token.objects.get_token_or_none(token_key=activation_token.key)
        )

    def test_invalid_activation_token(self) -> None:
        """An invalid activation token creates a new worker instead."""
        token_key = secrets.token_hex(32)
        data = {"token": token_key, "fqdn": "worker.lan"}

        response = self.client.post(
            reverse("api:register"), data, headers={"token": token_key}
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        token = Token.objects.get_token_or_none(token_key=token_key)
        assert token is not None
        worker = token.worker
        self.assertEqual(worker.name, "worker-lan")
        self.assertFalse(token.enabled)


class GetNextWorkRequestViewTests(WorkRequestTestCase):
    """Tests for GetNextWorkRequestView."""

    token: ClassVar[Token]
    worker: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.token = cls.playground.create_bare_token()
        cls.worker = Worker.objects.create_with_fqdn(
            "worker-test", token=cls.token
        )

    def create_work_request(
        self, status: WorkRequest.Statuses, created_at: datetime
    ) -> WorkRequest:
        """Return a new work_request as specified by the parameters."""
        environment_item = self.playground.create_debian_environment()
        assert environment_item.artifact is not None
        source_artifact = self.playground.create_source_artifact()
        task_data = SbuildData(
            input=SbuildInput(source_artifact=source_artifact.id),
            host_architecture="test",
            environment=environment_item.artifact.id,
        )

        work_request = self.playground.create_work_request(
            worker=self.worker,
            task_name="sbuild",
            task_data=task_data,
            status=status,
        )

        work_request.created_at = created_at

        work_request.save()

        return work_request

    def test_check_permissions(self) -> None:
        """Only authenticated requests are processed by the view."""
        self.assertIn(
            IsWorkerAuthenticated,
            GetNextWorkRequestView.permission_classes,
        )

    def test_get_running_work_request(self) -> None:
        """A running work request is aborted and retried."""
        # Create WorkRequest pending
        work_request_pending = self.create_work_request(
            WorkRequest.Statuses.PENDING,
            datetime(2022, 1, 5, 10, 13, 20, 204242, tz.utc),
        )

        # Create WorkRequest running
        work_request_running = self.create_work_request(
            WorkRequest.Statuses.RUNNING,
            datetime(2022, 1, 5, 11, 14, 22, 242178, tz.utc),
        )

        # Request
        response = self.client.get(
            reverse('api:work-request-get-next'),
            headers={"token": self.token.key},
        )

        # The running work request was retried, though not yet assigned to a
        # worker; we got the pending one.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        work_request_running.refresh_from_db()
        self.assertEqual(
            work_request_running.status, WorkRequest.Statuses.ABORTED
        )
        self.assertTrue(hasattr(work_request_running, "superseded"))
        self.assertIsNone(work_request_running.superseded.worker)
        self.assertEqual(
            work_request_running.superseded.workflow_data.retry_count, 1
        )
        self.check_response_for_work_request(response, work_request_pending)

    def test_get_running_work_request_cannot_retry(self) -> None:
        """If a running work request cannot be retried, it is logged."""
        work_request_pending = self.create_work_request(
            WorkRequest.Statuses.PENDING,
            datetime(2022, 1, 5, 10, 13, 20, 204242, tz.utc),
        )
        work_request_running = self.create_work_request(
            WorkRequest.Statuses.RUNNING,
            datetime(2022, 1, 5, 11, 14, 22, 242178, tz.utc),
        )
        work_request_running.task_data = {"invalid": True}
        work_request_running.save()

        with self.assertLogsContains(
            "Cannot retry previously-running work request: "
            "Task dependencies cannot be satisfied",
            logger="debusine.server.views.workers",
            level=logging.DEBUG,
        ):
            response = self.client.get(
                reverse('api:work-request-get-next'),
                headers={"token": self.token.key},
            )

        # The running work request was aborted but could not be retried; we
        # got the pending one.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        work_request_running.refresh_from_db()
        self.assertEqual(
            work_request_running.status, WorkRequest.Statuses.ABORTED
        )
        self.assertFalse(hasattr(work_request_running, "superseded"))
        self.check_response_for_work_request(response, work_request_pending)

    def test_get_work_request_change_status(self) -> None:
        """Get a WorkRequest change the status to running."""
        work_request = self.create_work_request(
            WorkRequest.Statuses.PENDING,
            datetime(2022, 1, 5, 10, 13, 20, 204242, tz.utc),
        )

        # Request
        response = self.client.get(
            reverse('api:work-request-get-next'),
            headers={"token": self.token.key},
        )

        # Assert that we got the WorkRequest running
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(response, work_request)

        work_request.refresh_from_db()

        self.assertEqual(work_request.status, WorkRequest.Statuses.RUNNING)

    def test_get_work_request_configured_task_data(self) -> None:
        """Get a WorkRequest shows configured task data."""
        work_request = self.create_work_request(
            WorkRequest.Statuses.PENDING,
            datetime(2022, 1, 5, 10, 13, 20, 204242, tz.utc),
        )
        work_request.task_data = {"test": 1}
        work_request.configured_task_data = {"test": 2}
        work_request.save()

        # Request
        response = self.client.get(
            reverse('api:work-request-get-next'),
            headers={"token": self.token.key},
        )

        # Assert that we got the WorkRequest running
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.check_response_for_work_request(response, work_request)
        self.assertEqual(response.json()["task_data"], {"test": 2})

    def test_get_older_pending(self) -> None:
        """View return the older pending work request."""
        # Create WorkRequest pending
        work_request_pending_01 = self.create_work_request(
            WorkRequest.Statuses.PENDING,
            datetime(2022, 1, 5, 10, 13, 20, 204242, tz.utc),
        )

        work_request_pending_02 = self.create_work_request(
            WorkRequest.Statuses.PENDING,
            datetime(2022, 1, 5, 10, 13, 22, 204242, tz.utc),
        )
        # Request
        response = self.client.get(
            reverse('api:work-request-get-next'),
            headers={"token": self.token.key},
        )

        self.check_response_for_work_request(response, work_request_pending_01)

        # work_request_pending_01 status changed. Let's move it back to
        # pending. This is not an allowed transition but helps here to test
        # that clients will receive the older pending
        work_request_pending_01.status = WorkRequest.Statuses.PENDING

        # Change created_at to force a change in the order that debusine
        # sends the WorkRequests to the client
        work_request_pending_01.created_at = datetime(
            2022, 1, 5, 10, 13, 24, 204242, tz.utc
        )

        work_request_pending_01.save()

        # New request to check the new order
        response = self.client.get(
            reverse('api:work-request-get-next'),
            headers={"token": self.token.key},
        )

        self.check_response_for_work_request(response, work_request_pending_02)

    def test_get_no_work_request(self) -> None:
        """Assert debusine sends HTTP 204 when nothing assigned to Worker."""
        response = self.client.get(
            reverse('api:work-request-get-next'),
            headers={"token": self.token.key},
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_authentication_credentials_not_provided(self) -> None:
        """Assert Token is required to use the endpoint."""
        # This is to double-check that IsWorkerAuthenticated (inclusion
        # is verified in test_check_permissions) is returning what it should do.
        # All the views with IsWorkerAuthenticated behave the same way
        # IsWorkerAuthenticated is tested in IsWorkerAuthenticatedTests
        response = self.client.get(reverse('api:work-request-get-next'))
        self.assertEqual(
            response.json(),
            {
                "title": "Error",
                "detail": "Authentication credentials were not provided.",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class UpdateWorkRequestAsCompletedTests(TestCase):
    """Tests for UpdateWorkRequestAsCompleted class."""

    token: ClassVar[Token]
    worker: ClassVar[Worker]
    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.token = cls.playground.create_bare_token()
        cls.worker = Worker.objects.create_with_fqdn(
            "worker-test", token=cls.token
        )

        environment_item = cls.playground.create_debian_environment()
        assert environment_item.artifact is not None
        source_artifact = cls.playground.create_source_artifact()
        task_data = SbuildData(
            input=SbuildInput(source_artifact=source_artifact.id),
            host_architecture="test",
            environment=environment_item.artifact.id,
        )

        cls.work_request = cls.playground.create_work_request(
            worker=cls.worker,
            task_name='sbuild',
            task_data=task_data,
            status=WorkRequest.Statuses.PENDING,
        )

    def test_check_permissions(self) -> None:
        """Only authenticated requests are processed by the view."""
        self.assertIn(
            IsWorkerAuthenticated,
            UpdateWorkRequestAsCompletedView.permission_classes,
        )

    def put_api_work_request_completed(
        self, result: WorkRequest.Results, output_data: OutputData | None = None
    ) -> None:
        """Assert Worker can update WorkRequest result to completed-success."""
        self.work_request.status = WorkRequest.Statuses.RUNNING
        self.work_request.save()

        data: dict[str, Any] = {"result": result}
        if output_data is not None:
            data["output_data"] = model_to_json_serializable_dict(
                output_data, exclude_unset=True
            )
        response = self.client.put(
            reverse(
                'api:work-request-completed',
                kwargs={'work_request_id': self.work_request.id},
            ),
            data=data,
            headers={"token": self.token.key},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.work_request.refresh_from_db()
        self.assertEqual(
            self.work_request.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(self.work_request.result, result)
        self.assertEqual(self.work_request.output_data, output_data)

    def test_update_completed_result_is_success(self) -> None:
        """Assert Worker can update WorkRequest result to completed-success."""
        self.put_api_work_request_completed(WorkRequest.Results.SUCCESS)

    def test_update_completed_result_is_success_with_output_data(self) -> None:
        """Assert Worker can update WorkRequest result with output data."""
        self.put_api_work_request_completed(
            WorkRequest.Results.SUCCESS,
            OutputData(runtime_statistics=RuntimeStatistics(duration=60)),
        )

    def test_update_completed_result_is_failure(self) -> None:
        """Assert Worker can update WorkRequest result to completed-error."""
        self.put_api_work_request_completed(WorkRequest.Results.ERROR)

    def test_update_work_request_id_not_found(self) -> None:
        """Assert API returns HTTP 404 for a non-existing WorkRequest id."""
        max_id = WorkRequest.objects.aggregate(Max('id'))['id__max']

        response = self.client.put(
            reverse(
                'api:work-request-completed',
                kwargs={'work_request_id': max_id + 1},
            ),
            data={"result": WorkRequest.Results.SUCCESS},
            headers={"token": self.token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            "Work request not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_update_failure_invalid_result(self) -> None:
        """Assert Worker get HTTP 400 and error for invalid result field."""
        self.work_request.status = WorkRequest.Statuses.RUNNING
        self.work_request.save()

        response = self.client.put(
            reverse(
                'api:work-request-completed',
                kwargs={'work_request_id': self.work_request.id},
            ),
            data={"result": "something not recognised"},
            headers={"token": self.token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            "Cannot change work request as completed",
            validation_errors_pattern="something not recognised",
        )

    def test_unauthorized(self) -> None:
        """Assert worker cannot modify a task of another worker."""
        another_worker = Worker.objects.create_with_fqdn(
            "another-worker-test", token=self.playground.create_bare_token()
        )

        environment_item = self.playground.create_debian_environment()
        assert environment_item.artifact is not None
        source_artifact = self.playground.create_source_artifact()
        task_data = SbuildData(
            input=SbuildInput(source_artifact=source_artifact.id),
            host_architecture="test",
            environment=environment_item.artifact.id,
        )

        another_work_request = self.playground.create_work_request(
            worker=another_worker,
            task_name='sbuild',
            task_data=task_data,
            status=WorkRequest.Statuses.PENDING,
        )

        response = self.client.put(
            reverse(
                'api:work-request-completed',
                kwargs={'work_request_id': another_work_request.id},
            ),
            headers={"token": self.token.key},
        )

        self.assertResponseProblem(
            response,
            "Invalid worker to update the work request",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class UpdateWorkerDynamicMetadataTests(TestCase):
    """Tests for DynamicMetadata."""

    def setUp(self) -> None:
        """Set up common objects."""
        super().setUp()
        self.worker_01 = Worker.objects.create_with_fqdn(
            'worker-01-lan', token=self.playground.create_bare_token()
        )
        self.worker_02 = Worker.objects.create_with_fqdn(
            'worker-02-lan', token=self.playground.create_bare_token()
        )

    def test_check_permissions(self) -> None:
        """Only authenticated requests are processed by the view."""
        self.assertIn(
            IsWorkerAuthenticated,
            UpdateWorkerDynamicMetadataView.permission_classes,
        )

    def test_update_metadata_success(self) -> None:
        """Worker's dynamic_metadata is updated."""
        assert self.worker_01.token is not None
        self.assertEqual(self.worker_01.dynamic_metadata, {})
        self.assertEqual(self.worker_02.dynamic_metadata, {})

        metadata = {"cpu_cores": 4, "ram": 16}
        response = self.client.put(
            reverse('api:worker-dynamic-metadata'),
            data=metadata,
            headers={"token": self.worker_01.token.key},
            content_type="application/json",
        )

        self.worker_01.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(self.worker_01.dynamic_metadata, metadata)
        assert self.worker_01.dynamic_metadata_updated_at is not None
        self.assertLessEqual(
            self.worker_01.dynamic_metadata_updated_at, timezone.now()
        )

        self.assertEqual(self.worker_02.dynamic_metadata, {})
        self.assertIsNone(self.worker_02.dynamic_metadata_updated_at)
