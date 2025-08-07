# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used by debusine client."""

import json
import re
from collections.abc import Iterator, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Literal, NewType, TypeVar

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.assets import AssetCategory, BaseAssetDataModel, asset_data_model
from debusine.utils import calculate_hash


class StrictBaseModel(pydantic.BaseModel):
    """Stricter pydantic configuration."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True


class PaginatedResponse(StrictBaseModel):
    """Paginated response from the API."""

    count: int | None
    next: pydantic.AnyUrl | None
    previous: pydantic.AnyUrl | None
    results: list[dict[str, Any]]


class WorkRequestRequest(StrictBaseModel):
    """Client send a WorkRequest to the server."""

    task_name: str
    workspace: str | None = None
    task_data: dict[str, Any]
    event_reactions: dict[str, Any]


class WorkRequestResponse(StrictBaseModel):
    """Server return a WorkRequest to the client."""

    id: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration: int | None = None
    status: str
    result: str
    worker: int | None = None
    task_type: str
    task_name: str
    task_data: dict[str, Any]
    dynamic_task_data: dict[str, Any] | None
    priority_base: int
    priority_adjustment: int
    artifacts: list[int]
    workspace: str

    def __str__(self) -> str:
        """Return representation of the object."""
        return f'WorkRequest: {self.id}'


class WorkRequestExternalDebsignRequest(StrictBaseModel):
    """Client sends data from an external `debsign` run to the server."""

    signed_artifact: int


class OnWorkRequestCompleted(StrictBaseModel):
    """
    Server return an OnWorkRequestCompleted to the client.

    Returned via websocket consumer endpoint.
    """

    work_request_id: int
    completed_at: datetime
    result: str


class WorkflowTemplateRequest(StrictBaseModel):
    """Client sends a WorkflowTemplate to the server."""

    name: str
    task_name: str
    workspace: str | None = None
    task_data: dict[str, Any]
    priority: int


class WorkflowTemplateResponse(StrictBaseModel):
    """Server returns a WorkflowTemplate to the server."""

    id: int
    task_name: str
    workspace: str
    task_data: dict[str, Any]
    priority: int


class CreateWorkflowRequest(StrictBaseModel):
    """Client sends a workflow creation request to the server."""

    template_name: str
    workspace: str | None = None
    task_data: dict[str, Any]


# With pydantic >= 2.1.0, use Annotated[str, pydantic.Field(max_length=255)]
# instead.  Unfortunately, while pydantic v1 seems to accept this, it
# silently ignores the max_length annotation.
class StrMaxLength255(pydantic.ConstrainedStr):
    """A string with a maximum length of 255 characters."""

    max_length = 255


class FileRequest(StrictBaseModel):
    """Declare a FileRequest: client sends it to the server."""

    size: int = pydantic.Field(ge=0)
    checksums: dict[str, StrMaxLength255]
    type: Literal["file"]

    @staticmethod
    def create_from(path: Path) -> "FileRequest":
        """Return a FileRequest for the file path."""
        return FileRequest(
            size=path.stat().st_size,
            checksums={
                "sha256": pydantic.parse_obj_as(
                    StrMaxLength255, calculate_hash(path, "sha256").hex()
                )
            },
            type="file",
        )


class FileResponse(StrictBaseModel):
    """Declare a FileResponse: server sends it to the client."""

    size: int = pydantic.Field(ge=0)
    checksums: dict[str, StrMaxLength255]
    type: Literal["file"]
    url: pydantic.AnyUrl


FilesRequestType = NewType("FilesRequestType", dict[str, FileRequest])
FilesResponseType = NewType("FilesResponseType", dict[str, FileResponse])


class ArtifactCreateRequest(StrictBaseModel):
    """Declare an ArtifactCreateRequest: client sends it to the server."""

    category: str
    workspace: str | None = None
    files: FilesRequestType = FilesRequestType({})
    data: dict[str, Any] = {}
    work_request: int | None = None
    expire_at: datetime | None = None


class ArtifactResponse(StrictBaseModel):
    """Declare an ArtifactResponse: server sends it to the client."""

    id: int
    workspace: str
    category: str
    created_at: datetime
    data: dict[str, Any]
    download_tar_gz_url: pydantic.AnyUrl
    files_to_upload: list[str]
    expire_at: datetime | None = None
    files: FilesResponseType = FilesResponseType({})


class RemoteArtifact(StrictBaseModel):
    """Declare RemoteArtifact."""

    id: int
    workspace: str


class AssetCreateRequest(StrictBaseModel):
    """Request for the Asset creation API."""

    category: AssetCategory
    data: BaseAssetDataModel
    work_request: int | None = None
    workspace: str


class AssetResponse(StrictBaseModel):
    """Response from an Asset creation / listing API."""

    id: int
    category: AssetCategory
    data: BaseAssetDataModel | dict[str, Any]
    work_request: int | None = None
    workspace: str

    @pydantic.root_validator
    def parse_data(cls, values: dict[str, Any]) -> dict[str, Any]:  # noqa: U100
        """Parse data using the correct data model."""
        if isinstance(values.get("data"), dict):
            category = values.get("category")
            if category:  # pragma: no cover
                values["data"] = asset_data_model(category, values["data"])
        return values


class AssetsResponse(StrictBaseModel):
    """A response from the server with multiple AssetResponse objects."""

    # This model parses a list, not an object/dict.
    __root__: Sequence[AssetResponse]

    def __iter__(self) -> Iterator[AssetResponse]:  # type: ignore[override]
        """Iterate over individual asset responses."""
        return iter(self.__root__)


class AssetPermissionCheckResponse(StrictBaseModel):
    """A response from the asset permission check endpoint."""

    has_permission: bool
    username: str | None
    user_id: int | None
    resource: dict[str, Any] | None


class RelationType(StrEnum):
    """Possible values for `RelationCreateRequest.type`."""

    EXTENDS = "extends"
    RELATES_TO = "relates-to"
    BUILT_USING = "built-using"


class RelationCreateRequest(StrictBaseModel):
    """Declare a RelationCreateRequest: client sends it to the server."""

    artifact: int
    target: int
    type: RelationType


class RelationResponse(RelationCreateRequest):
    """Declare a RelationResponse."""

    id: int


class RelationsResponse(StrictBaseModel):
    """A response from the server with multiple RelationResponse objects."""

    # This model parses a list, not an object/dict.
    __root__: Sequence[RelationResponse]

    def __iter__(self) -> Iterator[RelationResponse]:  # type: ignore[override]
        """Iterate over individual relation responses."""
        return iter(self.__root__)


class LookupChildType(StrEnum):
    """Possible values for `LookupDict.child_type` and `expect_type`."""

    BARE = "bare"
    ARTIFACT = "artifact"
    ARTIFACT_OR_PROMISE = "artifact-or-promise"
    COLLECTION = "collection"
    ANY = "any"


class LookupResultType(StrEnum):
    """A collection item type returned by a lookup."""

    BARE = "b"
    ARTIFACT = "a"
    COLLECTION = "c"


class LookupSingleRequest(StrictBaseModel):
    """A request from the client to look up a single collection item."""

    lookup: int | str
    work_request: int
    expect_type: LookupChildType
    default_category: str | None = None


class LookupMultipleRequest(StrictBaseModel):
    """A request from the client to look up multiple collection items."""

    lookup: list[int | str | dict[str, Any]]
    work_request: int
    expect_type: LookupChildType
    default_category: str | None = None


class LookupSingleResponse(StrictBaseModel):
    """A response from the server with a single lookup result."""

    result_type: LookupResultType
    collection_item: int | None = None
    artifact: int | None = None
    collection: int | None = None


class LookupSingleResponseArtifact(LookupSingleResponse):
    """
    A response from the server with a single lookup result for an artifact.

    Used to assist type annotations.
    """

    result_type: Literal[LookupResultType.ARTIFACT]
    artifact: int


class LookupSingleResponseCollection(LookupSingleResponse):
    """
    A response from the server with a single lookup result for a collection.

    Used to assist type annotations.
    """

    result_type: Literal[LookupResultType.COLLECTION]
    collection: int


LSR = TypeVar("LSR", bound=LookupSingleResponse, covariant=True)


class LookupMultipleResponse(StrictBaseModel, Generic[LSR]):
    """A response from the server with multiple lookup results."""

    # This model parses a list, not an object/dict.
    __root__: Sequence[LSR]

    def __iter__(self) -> Iterator[LSR]:  # type: ignore[override]
        """Iterate over individual results."""
        return iter(self.__root__)


re_nonce = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
re_challenge = re.compile(r"^\w{4,10}(?: \w{4,10}){2,7}$", re.ASCII)


class EnrollPayload(pydantic.BaseModel):
    """Client-provided enrollment payload."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    #: Nonce identifying the client enrollment
    nonce: str
    #: Human-readable challenge to verify the client in the web UI
    challenge: str
    #: Scope. Informational, the confirmation page will warn if invalid
    scope: str
    #: Hostname. Informational, shown in the confirmation page and in the
    #: generated token comment
    hostname: str

    @pydantic.validator("nonce", pre=True)
    @classmethod
    def _validate_nonce(cls, nonce: str) -> str:
        """Validate that the nonce is well formed."""
        if not re_nonce.match(nonce):
            raise ValueError("Nonce is malformed")
        return nonce

    @pydantic.validator("challenge", pre=True)
    @classmethod
    def _validate_challenge(cls, challenge: str) -> str:
        """Validate that the challenge is well formed."""
        if not re_challenge.match(challenge):
            raise ValueError("Challenge is malformed")
        return challenge


re_token = re.compile(r"^[A-Za-z0-9]{8,64}$")


class EnrollOutcome(StrEnum):
    """User action in response to an enroll confirmation request."""

    CONFIRM = "confirm"
    CANCEL = "cancel"


class EnrollConfirmPayload(pydantic.BaseModel):
    """Enrollment response from the server."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    outcome: EnrollOutcome
    token: str | None = None

    @pydantic.validator("token", pre=True)
    @classmethod
    def _validate_token(cls, token: str | None) -> str | None:
        """Validate that the token is well formed."""
        if token is not None and not re_token.match(token):
            raise ValueError("Token is malformed")
        return token


def model_to_json_serializable_dict(
    model: pydantic.BaseModel, exclude_unset: bool = False
) -> dict[Any, Any]:
    """
    Similar to model.dict() but the returned dictionary is JSON serializable.

    For example, a datetime() is not JSON serializable. Using this method will
    return a dictionary with a string instead of a datetime object.

    Replace with model_dump() in Pydantic 2.
    """
    serializable = json.loads(model.json(exclude_unset=exclude_unset))
    assert isinstance(serializable, dict)
    return serializable


def model_to_json_serializable_list(
    model: pydantic.BaseModel, exclude_unset: bool = False
) -> list[dict[Any, Any]]:
    """Similar to model_to_json_serializable_dict, but for a list response."""
    serializable = json.loads(model.json(exclude_unset=exclude_unset))
    assert isinstance(serializable, list)
    return serializable
