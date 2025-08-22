# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: artifacts."""

import binascii
import logging
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, IO, Literal

from django.conf import settings
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import BaseParser, JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    File,
    FileInArtifact,
    FileUpload,
    User,
    WorkRequest,
)
from debusine.db.models.artifacts import (
    ArtifactQuerySet,
    ArtifactRelationQuerySet,
)
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    ArtifactRelationResponseSerializer,
    ArtifactSerializer,
    ArtifactSerializerResponse,
)
from debusine.server.views.base import (
    BaseAPIView,
    CanDisplayFilterBackend,
    DestroyAPIViewBase,
    GenericAPIViewBase,
    GetOrCreateAPIView,
    ListAPIViewBase,
)
from debusine.server.views.rest import (
    IsTokenUserAuthenticated,
    IsWorkerAuthenticated,
)
from debusine.utils import parse_content_range_header

logger = logging.getLogger(__name__)


class ArtifactView(GenericAPIViewBase[Artifact], BaseAPIView):
    """View used to get or create artifacts."""

    filter_backends = [CanDisplayFilterBackend]
    lookup_url_kwarg = "artifact_id"

    def get_queryset(self) -> ArtifactQuerySet[Any]:
        """Get the query set for this view."""
        return Artifact.objects.in_current_scope()

    def get_object(self) -> Artifact:
        """Override to return more API-friendly errors."""
        try:
            return super().get_object()
        except Http404 as exc:
            raise DebusineAPIException(
                title=str(exc), status_code=status.HTTP_404_NOT_FOUND
            )

    def get(self, request: Request, artifact_id: int) -> Response:  # noqa: U100
        """Return information of artifact_id."""
        artifact = self.get_object()
        self.set_current_workspace(artifact.workspace)
        self.enforce(artifact.can_display)

        artifact_response = ArtifactSerializerResponse.from_artifact(
            artifact, request
        )

        return Response(artifact_response.data, status=status.HTTP_200_OK)

    def _create_file_in_artifact(
        self, artifact: Artifact, path: str, file_deserialized: dict[str, Any]
    ) -> None:
        """Create a single file in an artifact."""
        file_size = file_deserialized["size"]
        hash_digest = binascii.unhexlify(
            file_deserialized["checksums"]["sha256"]
        )

        fileobj, created = File.objects.get_or_create(
            hash_digest=hash_digest,
            size=file_size,
        )

        file_in_artifact = FileInArtifact.objects.create(
            artifact=artifact,
            file=fileobj,
            path=path,
            complete=False,
            content_type=file_deserialized.get("content_type"),
        )

        if created or artifact.workspace.file_needs_upload(fileobj):
            if file_size == 0:
                # Create the file upload and add the file in the store
                temp_empty_file = tempfile.NamedTemporaryFile(
                    prefix="file-uploading-empty-",
                    dir=settings.DEBUSINE_UPLOAD_DIRECTORY,
                    delete=False,
                )
                temp_empty_file.close()
                file_upload = FileUpload.objects.create(
                    file_in_artifact=file_in_artifact,
                    path=Path(temp_empty_file.name).name,
                )
                UploadFileView.create_file_in_storage(file_upload)
                file_in_artifact.complete = True
                file_in_artifact.save()
        else:
            # The file already exists in the store and has a complete
            # FileInArtifact in the same workspace, indicating that it is
            # visible and we can safely copy a reference to it rather than
            # requiring it to be reuploaded.
            file_in_artifact.complete = True
            file_in_artifact.save()

    def post(self, request: Request) -> Response:
        """Create a new artifact."""
        data = request.data.copy()

        artifact_deserialized = ArtifactSerializer(data=data)
        artifact_deserialized.is_valid(raise_exception=True)

        workspace = artifact_deserialized.validated_data["workspace"]
        self.set_current_workspace(workspace)
        self.enforce(workspace.can_create_artifacts)

        work_request_id: int | None = artifact_deserialized.validated_data.get(
            "work_request"
        )
        work_request: WorkRequest | None = None
        created_by: User | None = None

        if work_request_id is not None:
            if not context.worker_token:
                raise DebusineAPIException(
                    title=(
                        "work_request field can only be used by a request with "
                        "a token from a Worker"
                    ),
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            try:
                work_request = WorkRequest.objects.get(id=work_request_id)
            except WorkRequest.DoesNotExist:
                raise DebusineAPIException(
                    title=f"WorkRequest {work_request_id} does not exist"
                )
        elif context.user is not None and context.user.is_authenticated:
            created_by = context.user
        else:
            raise DebusineAPIException(
                title=(
                    "If work_request field is empty the WorkRequest must be "
                    "created using a Token associated to a user"
                ),
                status_code=status.HTTP_403_FORBIDDEN,
            )

        artifact = Artifact.objects.create(
            category=artifact_deserialized["category"].value,
            workspace=workspace,
            data=artifact_deserialized["data"].value,
            created_by_work_request=work_request,
            expiration_delay=artifact_deserialized["expiration_delay"].value,
            created_by=created_by,
        )

        for path, file_deserialized in artifact_deserialized[
            "files"
        ].value.items():
            self._create_file_in_artifact(artifact, path, file_deserialized)

        artifact_response = ArtifactSerializerResponse.from_artifact(
            artifact, request
        )
        return Response(artifact_response.data, status=status.HTTP_201_CREATED)


class FileUploadParser(BaseParser):
    """View used to upload files into an artifact."""

    media_type = "*/*"

    @staticmethod
    def _raise_error_if_gap_in_file(
        range_start: int, temporary_file_path: Path
    ) -> None:
        uploaded_size = Path(temporary_file_path).stat().st_size
        if uploaded_size < range_start:
            raise ValueError(
                f"Server had received {uploaded_size} bytes of the file. "
                f"New upload starts at {range_start}. "
                f"Please continue from {uploaded_size}"
            )

    # TODO: Can we avoid overriding the return type of BaseParser.parse?  It
    # doesn't seem to cause a practical problem, but is a bit odd.
    def parse(  # type: ignore[override]
        self,
        stream: IO[Any],
        media_type: str | None = None,
        parser_context: Mapping[str, Any] | None = None,
    ) -> FileUpload:
        """Upload a file to an artifact."""
        media_type  # fake usage for vulture
        assert parser_context is not None
        file_in_artifact = parser_context["request"].file_in_artifact

        range_start = 0
        range_end = None

        content_range = parse_content_range_header(
            parser_context["request"].headers
        )

        if content_range is not None:
            # Checked by UploadFileView.put.
            assert content_range["start"] != "*"

            range_start = content_range["start"]
            range_end = content_range["end"]

        if range_start > (file_size := file_in_artifact.file.size):
            raise ValueError(
                f"Range start ({range_start}) is greater than "
                f"file size ({file_size})"
            )

        uploads_file: IO[bytes]
        try:
            file_upload = FileUpload.objects.get(
                file_in_artifact=file_in_artifact
            )
            temporary_file_path = file_upload.absolute_file_path()
            self._raise_error_if_gap_in_file(range_start, temporary_file_path)
            uploads_file = open(temporary_file_path, "r+b")
        except FileUpload.DoesNotExist:
            uploads_file = tempfile.NamedTemporaryFile(
                prefix='file-uploading-',
                dir=settings.DEBUSINE_UPLOAD_DIRECTORY,
                delete=False,
                mode="a+b",
            )
            file_upload = FileUpload.objects.create(
                file_in_artifact=file_in_artifact,
                path=Path(uploads_file.name).name,
            )

        uploads_file.seek(range_start)

        bytes_received = 0
        # DATA_UPLOAD_MAX_MEMORY_SIZE: arbitrary size that should make sense
        # that can be dealt with
        while chunk := stream.read(settings.DATA_UPLOAD_MAX_MEMORY_SIZE):
            bytes_received += len(chunk)

            if range_start + bytes_received > file_size:
                raise ValueError(
                    f"Cannot process range: attempted to write after end "
                    f"of the file. Range start: {range_start} "
                    f"Received: {bytes_received} File size: {file_size}"
                )

            uploads_file.write(chunk)

        if (
            range_end is not None
            and (expected_bytes := range_end - range_start + 1)
            != bytes_received
        ):
            raise ValueError(
                f"Expected {expected_bytes} bytes (based on range header: "
                f"end-start+1). Received: {bytes_received} bytes"
            )

        uploads_file.close()

        file_upload.last_activity_at = timezone.now()
        file_upload.save()

        return file_upload


class UploadFileView(BaseAPIView):
    """View to upload files."""

    parser_classes = [FileUploadParser]
    # TODO: This should be replaced by appropriate debusine permissions.
    permission_classes = [IsTokenUserAuthenticated | IsWorkerAuthenticated]

    @staticmethod
    def _range_header(start: int, end: int) -> dict[str, str]:
        return {"Range": f"bytes={start}-{end}"}

    @classmethod
    def _file_status_response(
        cls,
        file_in_artifact: FileInArtifact,
        range_file_size: int | Literal["*"],
    ) -> Response:
        """
        Return response with the status of a file (completed or missing range).

        :param file_in_artifact: FileInArtifact that is being uploaded
        :param range_file_size: if it's "*" returns the total file size,
          if it contains a number and does not match the expected
          the server returns HTTP 400 Bad request
        """
        file_upload = getattr(file_in_artifact, "fileupload", None)

        if file_upload is None:
            return Response(
                status=status.HTTP_206_PARTIAL_CONTENT,
                headers=cls._range_header(0, 0),
            )

        file_in_db_size = file_upload.file_in_artifact.file.size
        if range_file_size != "*" and range_file_size != file_in_db_size:
            # Client reported a size in Content-Range that is not
            # what was expected
            raise DebusineAPIException(
                title=(
                    "Invalid file size in Content-Range header. "
                    f"Expected {file_in_db_size} "
                    f"received {range_file_size}"
                )
            )

        current_size = FileUpload.current_size(
            artifact=file_in_artifact.artifact,
            path_in_artifact=file_in_artifact.path,
        )

        if current_size == file_in_db_size:
            # No missing parts: file upload is already completed
            return Response(status=status.HTTP_200_OK)
        else:
            # Return HTTP 206 with the Range: 0-{uploaded_size}
            # so the client can continue the upload
            return Response(
                status=status.HTTP_206_PARTIAL_CONTENT,
                headers=cls._range_header(0, current_size),
            )

    def _get_file_in_artifact(
        self, artifact_id: int, file_path: str
    ) -> FileInArtifact:
        """Get file to be uploaded, or raise DebusineAPIException."""
        try:
            artifact = (
                Artifact.objects.in_current_scope()
                .can_display(context.user)
                .get(id=artifact_id)
            )
        except Artifact.DoesNotExist:
            raise DebusineAPIException(
                title=f"Artifact {artifact_id} does not exist",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        assert isinstance(artifact, Artifact)

        try:
            return FileInArtifact.objects.get(artifact=artifact, path=file_path)
        except FileInArtifact.DoesNotExist:
            raise DebusineAPIException(
                title=f'No file_path "{file_path}" for artifact {artifact.id}',
                status_code=status.HTTP_404_NOT_FOUND,
            )

    def put(
        self, request: Request, artifact_id: int, file_path: str
    ) -> Response:
        """Receive file to be uploaded or request uploaded content-range."""
        file_in_artifact = self._get_file_in_artifact(artifact_id, file_path)

        try:
            content_range = parse_content_range_header(request.headers)
        except ValueError as exc:
            raise DebusineAPIException(title=str(exc))

        artifact = file_in_artifact.artifact

        if content_range is not None and content_range["start"] == "*":
            # Client would like to know the status of a file
            # (which parts have been uploaded)
            assert content_range["size"] is not None
            return self._file_status_response(
                file_in_artifact, content_range["size"]
            )

        if file_in_artifact.complete:
            # File was already uploaded
            return Response(status=status.HTTP_201_CREATED)

        setattr(request, "file_in_artifact", file_in_artifact)
        # request.data use the FileUploadParser
        try:
            file_upload = request.data
        except ValueError as value_error:
            raise DebusineAPIException(title=str(value_error))
        assert isinstance(file_upload, FileUpload)

        current_size = FileUpload.current_size(
            artifact=artifact, path_in_artifact=file_path
        )

        if current_size != file_upload.file_in_artifact.file.size:
            return Response(
                status=status.HTTP_200_OK,
                headers=self._range_header(0, current_size),
            )
        else:
            # File is uploaded: create the file in the storage
            # Will return HTTP 201 Created (or HTTP 409 Conflict if the
            # expected hash does not match, the client will need to re-upload)
            try:
                return self.create_file_in_storage(file_upload)
            finally:
                file_upload.delete()

    @staticmethod
    def create_file_in_storage(file_upload: FileUpload) -> Response:
        """
        Add file_upload to the artifact's default store.

        Return Response: 201 Created if real hash and length with the
        expected hash and length match, 409 Conflict if no match
        (and deletes the FileUpload): the client need to upload the file again.
        """
        file_path = file_upload.absolute_file_path()

        hash_digest = File.calculate_hash(file_path)

        file_in_artifact = file_upload.file_in_artifact

        expected_file_hash_digest = file_in_artifact.file.hash_digest
        if file_in_artifact.file.hash_digest.hex() != hash_digest.hex():
            raise DebusineAPIException(
                title=(
                    "Invalid file hash. Expected: "
                    f"{expected_file_hash_digest.hex()} "
                    f"Received: {hash_digest.hex()}"
                ),
                status_code=status.HTTP_409_CONFLICT,
                # Don't roll back; the caller wants to delete the FileUpload
                # in this case, and we haven't made any other database
                # changes yet.
                rollback_transaction=False,
            )

        scope = file_in_artifact.artifact.workspace.scope
        file_backend = scope.upload_file_backend(file_in_artifact.file)

        file_backend.add_file(file_path, fileobj=file_in_artifact.file)
        file_in_artifact.complete = True
        file_in_artifact.save()

        return Response(status=status.HTTP_201_CREATED)


class ArtifactRelationsView(
    GetOrCreateAPIView[ArtifactRelation],
    DestroyAPIViewBase[ArtifactRelation],
    ListAPIViewBase[ArtifactRelation],
    BaseAPIView,
):
    """Return, create and delete relations between artifacts."""

    serializer_class = ArtifactRelationResponseSerializer
    filter_backends = [CanDisplayFilterBackend]
    parser_classes = [JSONParser]
    pagination_class = None

    def get_queryset(self) -> ArtifactRelationQuerySet[Any]:
        """Get the query set for this view."""
        queryset = ArtifactRelation.objects.in_current_scope()
        filter_kwargs = {}

        if self.request.method == "GET":
            artifact_id = self.request.GET.get("artifact", None)
            target_artifact_id = self.request.GET.get("target_artifact", None)

            if artifact_id is not None:
                filter_kwargs = {"artifact_id": artifact_id}
                required_artifact_id = artifact_id
            elif target_artifact_id is not None:
                filter_kwargs = {"target_id": target_artifact_id}
                required_artifact_id = target_artifact_id
            else:
                raise DebusineAPIException(
                    title=(
                        '"artifact" or "target_artifact" parameter are '
                        'mandatory'
                    )
                )
            try:
                Artifact.objects.in_current_scope().can_display(
                    context.user
                ).get(id=required_artifact_id)
            except Artifact.DoesNotExist:
                raise DebusineAPIException(
                    title=f"Artifact {required_artifact_id} not found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

        return queryset.filter(**filter_kwargs)

    def perform_create(
        self, serializer: BaseSerializer[ArtifactRelation]
    ) -> None:
        """Create an artifact relation."""
        artifact = serializer.validated_data["artifact"]
        target = serializer.validated_data["target"]
        self.set_current_workspace(artifact.workspace)
        # TODO: This needs to be based on some kind of write permission
        # rather than on can_display.
        self.enforce(artifact.can_display)
        self.enforce(target.can_display)
        if artifact.fileinartifact_set.filter(complete=False).exists():
            raise DebusineAPIException(
                f"Cannot create relation: source artifact {artifact.id} is "
                f"incomplete"
            )
        if target.fileinartifact_set.filter(complete=False).exists():
            raise DebusineAPIException(
                f"Cannot create relation: target artifact {target.id} is "
                f"incomplete"
            )
        super().perform_create(serializer)
