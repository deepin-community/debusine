# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: workers."""

import logging

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from debusine.db.models import Token, WorkRequest, Worker
from debusine.db.models.work_requests import CannotRetry, WorkRequestRetryReason
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    WorkRequestCompletedSerializer,
    WorkRequestSerializerWithConfiguredTaskData,
    WorkerRegisterSerializer,
)
from debusine.server.views.base import BaseAPIView
from debusine.server.views.rest import IsWorkerAuthenticated
from debusine.tasks.models import OutputData

logger = logging.getLogger(__name__)


class RegisterView(BaseAPIView):
    """View used by workers to register to debusine."""

    def post(self, request: Request) -> Response:
        """Worker registers (sends token and fqdn)."""
        worker_register = WorkerRegisterSerializer(data=request.data)

        if not worker_register.is_valid():
            raise DebusineAPIException(
                title="Cannot deserialize worker",
                validation_errors=worker_register.errors,
            )

        token_key = worker_register.validated_data['token']
        fqdn = worker_register.validated_data['fqdn']
        worker_type = worker_register.validated_data['worker_type']

        token_hash = Token._generate_hash(token_key)
        token, _ = Token.objects.get_or_create(hash=token_hash)

        if hasattr(request.auth, "activating_worker"):
            # Activate an existing worker by registering and automatically
            # enabling a token for it.
            activating_worker = request.auth.activating_worker
            assert activating_worker is not None
            activating_worker.activation_token = None
            activating_worker.token = token
            activating_worker.save()
            token.enable()
            request.auth.delete()
        else:
            Worker.objects.create_with_fqdn(
                fqdn, token, worker_type=worker_type
            )

        logger.info('Client registered. Token key: %s', token_key)

        return Response(status=status.HTTP_201_CREATED)


class GetNextWorkRequestView(BaseAPIView):
    """View used by workers to request a task."""

    permission_classes = [IsWorkerAuthenticated]

    def get(self, request: Request) -> Response:
        """Return the task to build."""
        token_key = request.headers['token']
        worker = Worker.objects.get_worker_by_token_key_or_none(token_key)
        # This can't be None at this point, since IsWorkerAuthenticated
        # passed.
        assert worker is not None

        # This view is normally only called when a worker is idle, but if
        # the worker was restarted then the server may still think it has a
        # running work request.  In that case, retry the work request since
        # it may be in an unclear intermediate state.
        for running in WorkRequest.objects.running(worker=worker):
            running.mark_aborted()
            try:
                running.retry(reason=WorkRequestRetryReason.WORKER_FAILED)
            except CannotRetry as e:
                logger.debug(  # noqa: G200
                    "Cannot retry previously-running work request: %s", e
                )

        work_request = WorkRequest.objects.pending(worker=worker).first()

        status_code: int
        if work_request:
            content = WorkRequestSerializerWithConfiguredTaskData(
                work_request
            ).data
            work_request.mark_running()
            status_code = status.HTTP_200_OK
        else:
            # There is no work request available for the worker
            content = None
            status_code = status.HTTP_204_NO_CONTENT

        return Response(content, status=status_code)


class UpdateWorkRequestAsCompletedView(BaseAPIView):
    """View used by the workers to mark a task as completed."""

    permission_classes = [IsWorkerAuthenticated]

    def put(self, request: Request, work_request_id: int) -> Response:
        """Mark a work request as completed."""
        token_key = request.headers['token']
        worker = Worker.objects.get_worker_by_token_key_or_none(token_key)

        try:
            work_request = WorkRequest.objects.get(pk=work_request_id)
        except WorkRequest.DoesNotExist:
            raise DebusineAPIException(
                title="Work request not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if work_request.worker == worker:
            work_request_completed_serializer = WorkRequestCompletedSerializer(
                data=request.data
            )

            if work_request_completed_serializer.is_valid():
                data = work_request_completed_serializer.validated_data
                if "output_data" in data:
                    output_data = OutputData.parse_obj(data["output_data"])
                else:
                    output_data = None
                work_request.mark_completed(data["result"], output_data)
                content = None
                status_code = status.HTTP_200_OK
            else:
                raise DebusineAPIException(
                    title="Cannot change work request as completed",
                    validation_errors=work_request_completed_serializer.errors,
                )
        else:
            raise DebusineAPIException(
                title="Invalid worker to update the work request",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(content, status=status_code)


class UpdateWorkerDynamicMetadataView(BaseAPIView):
    """View used by the workers to post dynamic metadata."""

    permission_classes = [IsWorkerAuthenticated]

    def put(self, request: Request) -> Response:
        """Update Worker dynamic metadata."""
        token_key = request.headers['token']
        worker = Worker.objects.get_worker_by_token_key_or_none(token_key)
        # Checked by IsWorkerAuthenticated.
        assert worker is not None

        worker.set_dynamic_metadata(request.data)

        return Response(status=status.HTTP_204_NO_CONTENT)
