# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Mapping database models to REST Api support."""

from collections.abc import Callable
from copy import copy
from typing import (
    Any,
    NamedTuple,
    Never,
    NoReturn,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
)

from django.conf import settings
from django.db.models import QuerySet
from django.http import HttpRequest

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError

from debusine.artifacts.models import (
    ArtifactCategory,
    DebusineTaskConfiguration,
)
from debusine.assets import AssetCategory, asset_data_model
from debusine.client.models import (
    LookupChildType,
    WorkspaceInheritanceChainElement,
    model_to_json_serializable_dict,
)
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Asset,
    Collection,
    CollectionItem,
    File,
    TaskDatabase,
    WorkRequest,
    WorkflowTemplate,
    Workspace,
)
from debusine.server.collections.lookup import LookupResult
from debusine.server.exceptions import (
    DebusineAPIException,
    raise_workspace_not_found,
)
from debusine.server.workflows import Workflow
from debusine.server.workflows.models import WorkRequestManualUnblockAction
from debusine.tasks.models import EventReactions, OutputData, WorkerType

if TYPE_CHECKING:
    SerializerMixinBase: TypeAlias = serializers.Serializer[Any]
    SlugRelatedFieldBase = serializers.SlugRelatedField
    FieldBase = serializers.Field
else:
    SerializerMixinBase = object

    # SlugRelatedField doesn't support generic types at run-time yet.
    class _SlugRelatedFieldBase:
        def __class_getitem__(*args):
            return serializers.SlugRelatedField

    SlugRelatedFieldBase = _SlugRelatedFieldBase

    # Field doesn't support generic types at run-time yet.
    class _FieldBase:
        def __class_getitem__(*args):
            return serializers.Field

    FieldBase = _FieldBase

PydanticModel = TypeVar("PydanticModel", bound=pydantic.BaseModel)


class DebusineSerializerMixin(SerializerMixinBase):
    """Adjust a serializer with debusine-specific behaviour."""

    validation_error_title: str

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        """Raise a suitable API exception when deserialization fails."""
        valid = super().is_valid()
        if raise_exception and not valid:
            if (workspace_errors := self.errors.get("workspace", [])) and all(
                isinstance(error, ErrorDetail)
                and error.code == "does_not_exist"
                for error in workspace_errors
            ):
                raise_workspace_not_found(self.data["workspace"])
            else:
                raise DebusineAPIException(
                    title=self.validation_error_title,
                    validation_errors=self.errors,
                )
        return valid


class WorkspaceInCurrentScopeField(SlugRelatedFieldBase[Workspace]):
    """A field for workspaces in the current scope."""

    def __init__(self, **kwargs: Any) -> None:
        """Construct the field."""
        kwargs.setdefault("default", self._get_default_workspace)
        super().__init__(**kwargs)

    def get_queryset(self) -> QuerySet[Workspace]:
        """Limit to the current scope."""
        return Workspace.objects.all().in_current_scope()

    def _get_default_workspace(self) -> Workspace:
        """Get the default workspace."""
        try:
            return self.get_queryset().get(
                name=settings.DEBUSINE_DEFAULT_WORKSPACE
            )
        except Workspace.DoesNotExist:
            # The default workspace name might not exist in the current
            # scope.  If so, the client must provide a workspace.
            self.fail("required")


class WorkRequestSerializer(
    DebusineSerializerMixin, serializers.ModelSerializer[WorkRequest]
):
    """Serializer of a WorkRequest."""

    validation_error_title = "Cannot deserialize work request"

    url = serializers.URLField(source="get_absolute_url", read_only=True)
    artifacts = serializers.SerializerMethodField("artifacts_for_work_request")
    scope = serializers.CharField(source="workspace.scope.name", read_only=True)
    workspace = WorkspaceInCurrentScopeField(slug_field="name")
    workflow_data = serializers.JSONField(
        source="workflow_data_json", required=False
    )
    event_reactions = serializers.JSONField(
        source='event_reactions_json', required=False
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize object.

        :param kwargs['only_fields']: if not None: any field in .data that does
          not will make validate() raise a ValidationError.
        """
        self._only_fields = kwargs.pop('only_fields', None)
        super().__init__(*args, **kwargs)

    def artifacts_for_work_request(
        self, work_request: WorkRequest
    ) -> list[int]:
        """Return list of artifact ids created by this work request."""
        return list(
            work_request.artifact_set.all()
            .order_by("id")
            .values_list("id", flat=True)
        )

    def validate(self, data: Any) -> Any:
        """
        Add extra validation in the WorkRequestSerializer. Used by .is_valid().

        -if only_fields was passed in the __init__: raise ValidationError
          if the initial data had an unexpected field.
        """
        if self._only_fields is not None:
            fields_in_data = set(self.initial_data.keys())
            wanted_fields = set(self._only_fields)

            if unwanted_fields := (fields_in_data - wanted_fields):
                raise serializers.ValidationError(
                    f"Invalid fields: {', '.join(sorted(unwanted_fields))}"
                )

        return data

    def validate_event_reactions(self, value: Any) -> Any:
        """Validate event reactions."""
        try:
            EventReactions.parse_obj(value)
        except pydantic.ValidationError as e:
            raise serializers.ValidationError(f"Invalid event_reactions: {e}")
        return value

    class Meta:
        model = WorkRequest
        fields = [
            'id',
            'url',
            'task_name',
            'created_at',
            'created_by',
            'aborted_by',
            'started_at',
            'completed_at',
            'duration',
            'worker',
            'task_type',
            'task_data',
            'dynamic_task_data',
            'priority_base',
            'priority_adjustment',
            'workflow_data',
            'event_reactions',
            'status',
            'result',
            'artifacts',
            'scope',
            'workspace',
        ]


class WorkRequestSerializerWithConfiguredTaskData(WorkRequestSerializer):
    """Specialization of WorkRequestSerializer to feed task data to workers."""

    def update(self, instance: Any, validated_data: dict[str, Any]) -> NoReturn:
        """Disable update."""
        raise NotImplementedError('`update()` must be implemented.')

    def create(self, validated_data: dict[str, Any]) -> NoReturn:
        """Disable create."""
        raise NotImplementedError('`create()` must be implemented.')

    def to_representation(self, instance: WorkRequest) -> dict[str, Any]:
        """
        Use configured_task_data instead of task_data.

        This is intended to feed configured_task_data to workers without
        breaking the worker API.
        """
        res = super().to_representation(instance)
        if instance.configured_task_data:
            res["task_data"] = instance.configured_task_data
        return res


class WorkRequestUpdateSerializer(WorkRequestSerializer):
    """Serializer for updating work requests."""

    validation_error_title = "Cannot deserialize work request update"

    class Meta:
        model = WorkRequest
        fields = ['priority_adjustment']

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Ensure that only expected fields are passed."""
        kwargs["only_fields"] = self.Meta.fields
        super().__init__(*args, **kwargs)


class WorkRequestUnblockSerializer(
    DebusineSerializerMixin, serializers.Serializer[Never]
):
    """Serializer for the data used to edit a manual unblock."""

    validation_error_title = "Cannot deserialize work request unblock"

    notes = serializers.CharField(max_length=65536, required=False)
    action = serializers.ChoiceField(
        choices=list(WorkRequestManualUnblockAction), required=False
    )

    def validate(self, data: Any) -> Any:
        """Ensure at least one of notes and action is present."""
        if not data.get("notes") and data.get("action") is None:
            raise serializers.ValidationError(
                "At least one of notes and action must be set"
            )

        return data


class WorkRequestExternalDebsignSerializer(WorkRequestSerializer):
    """
    Serializer for an `ExternalDebsign` work request.

    This is similar to normal work request serialization, except that we
    make sure to return up-to-date dynamic task data so that it can be used
    to get the unsigned artifact ID.
    """

    def to_representation(self, instance: WorkRequest) -> dict[str, Any]:
        """Augment the representation with current dynamic task data."""
        data = super().to_representation(instance)

        try:
            task = instance.get_task()
            dynamic_task_data = task.compute_dynamic_data(
                TaskDatabase(instance)
            )
            data["dynamic_task_data"] = (
                None
                if dynamic_task_data is None
                else model_to_json_serializable_dict(
                    dynamic_task_data, exclude_unset=True
                )
            )
        except Exception as exc:
            raise ValidationError(detail=f"Cannot process task data: {exc}")

        return data


class WorkRequestExternalDebsignRequestSerializer(
    DebusineSerializerMixin, serializers.Serializer[Never]
):
    """Serializer for the data used to complete an `ExternalDebsign` task."""

    validation_error_title = "Cannot deserialize work request"

    signed_artifact = serializers.PrimaryKeyRelatedField(
        queryset=Artifact.objects
    )

    def validate_signed_artifact(self, value: Any) -> Any:
        """Check that the signed artifact has the correct category."""
        if value.category != ArtifactCategory.UPLOAD:
            raise ValidationError(
                detail=(
                    f"Expected signed artifact of category "
                    f"{ArtifactCategory.UPLOAD}; got {value.category}"
                )
            )
        if value.created_by_work_request is not None:
            raise ValidationError(
                detail=(
                    "Signed artifact must not have been created by a work "
                    "request"
                )
            )
        return value


class WorkerRegisterSerializer(serializers.Serializer[Never]):
    """Serializer for the data when a worker is registering."""

    token = serializers.CharField(max_length=64)
    fqdn = serializers.CharField(max_length=400)
    worker_type = serializers.ChoiceField(
        choices=list(WorkerType), default=WorkerType.EXTERNAL
    )


class WorkRequestCompletedSerializer(serializers.ModelSerializer[WorkRequest]):
    """Serializer for the data when a work request is completed."""

    result = serializers.ChoiceField(
        choices=[
            choice
            for choice in WorkRequest.Results
            if choice != WorkRequest.Results.NONE
        ]
    )
    output_data = serializers.JSONField(required=False)

    def validate_output_data(self, value: Any) -> Any:
        """Validate output_data."""
        try:
            OutputData.parse_obj(value)
        except pydantic.ValidationError as e:
            raise serializers.ValidationError(f"Invalid output_data: {e}")
        return value

    class Meta:
        model = WorkRequest
        fields = ["result", "output_data"]


class OnWorkRequestCompleted(serializers.Serializer[Never]):
    """Serializer used by the OnWorkRequestCompletedConsumer."""

    type = serializers.CharField()
    text = serializers.CharField()
    work_request_id = serializers.IntegerField()
    completed_at = serializers.DateTimeField()
    result = serializers.ChoiceField(choices=list(WorkRequest.Results))


class FileSerializer(serializers.Serializer[File]):
    """Serializer for the File class."""

    size = serializers.IntegerField(min_value=0)
    checksums = serializers.DictField(
        child=serializers.CharField(max_length=255)
    )
    type = serializers.ChoiceField(choices=("file",))
    content_type = serializers.CharField(allow_null=True, required=False)


class FileSerializerResponse(FileSerializer):
    """Serializer for the File responses. Include URL."""

    url = serializers.URLField()


class ArtifactSerializer(
    DebusineSerializerMixin, serializers.Serializer[Artifact]
):
    """Serializer to deserialize the client request to create an artifact."""

    validation_error_title = "Cannot deserialize artifact"

    category = serializers.CharField(max_length=255)
    workspace = WorkspaceInCurrentScopeField(slug_field="name")
    files = serializers.DictField(child=FileSerializer())
    # Deliberately incompatible with Serializer.data.
    data = serializers.DictField()  # type: ignore[assignment]
    work_request = serializers.IntegerField(allow_null=True, required=False)
    expiration_delay = serializers.DurationField(
        allow_null=True, required=False
    )


class ArtifactSerializerResponse(serializers.ModelSerializer[Artifact]):
    """Serializer for returning the information of an Artifact."""

    url = serializers.URLField(source="get_absolute_url", read_only=True)

    # Artifact's scope and workspace
    scope = serializers.CharField(source="workspace.scope.name", read_only=True)
    workspace = serializers.CharField(source="workspace.name", read_only=True)

    # Files with their hash and size
    files = serializers.SerializerMethodField("_files")

    # List of file paths pending to be uploaded
    files_to_upload = serializers.SerializerMethodField("_files_to_upload")

    # URL to download the artifact
    download_tar_gz_url = serializers.SerializerMethodField(
        "_download_tar_gz_url"
    )

    _base_download_url: str

    class Meta:
        model = Artifact
        fields = [
            "id",
            "url",
            "scope",
            "workspace",
            "category",
            "data",
            "created_at",
            "expire_at",
            "download_tar_gz_url",
            "files_to_upload",
            "files",
        ]

    def _files_to_upload(self, artifact: Artifact) -> list[str]:
        """Return file paths from artifact that have not been uploaded yet."""
        return list(
            artifact.fileinartifact_set.filter(complete=False)
            .order_by("path")
            .values_list("path", flat=True)
        )

    def _files(self, artifact: Artifact) -> dict[str, dict[str, Any]]:
        """
        Return files in the artifact with their hash and size.

        The files might not be available yet if they have not been uploaded.
        """
        files: dict[str, dict[str, Any]] = {}

        for file_in_artifact in artifact.fileinartifact_set.all().order_by(
            "path"
        ):
            file = file_in_artifact.file
            data = {
                "size": file.size,
                "type": "file",
                "checksums": {
                    file.current_hash_algorithm: file.hash_digest.hex()
                },
                "url": self._base_download_url + file_in_artifact.path,
                "content_type": file_in_artifact.content_type,
            }
            file_serialized = FileSerializerResponse(data=data)
            file_serialized.is_valid(raise_exception=True)

            files[file_in_artifact.path] = file_serialized.data

        return files

    def _download_tar_gz_url(self, obj: Artifact) -> str:  # noqa: U100
        """Based on self._base_download_url return URL to download .tar.gz."""
        return self._base_download_url + "?archive=tar.gz"

    @staticmethod
    def _build_absolute_download_url(
        artifact: Artifact, request: HttpRequest
    ) -> str:
        """Return URL to download the artifact (without the archive format)."""
        return request.build_absolute_uri(artifact.get_absolute_url_download())

    @classmethod
    def from_artifact(
        cls, artifact: Artifact, request: HttpRequest
    ) -> "ArtifactSerializerResponse":
        """Given an artifact and request return ArtifactSerializerResponse."""
        serialized = ArtifactSerializerResponse(artifact)
        serialized._base_download_url = cls._build_absolute_download_url(
            artifact, request
        )
        return serialized


class ArtifactRelationResponseSerializer(
    DebusineSerializerMixin, serializers.ModelSerializer[ArtifactRelation]
):
    """Serializer for relations between artifacts."""

    validation_error_title = "Cannot deserialize artifact relation"

    media_type = "application/json"

    class Meta:
        model = ArtifactRelation
        fields = ["id", "artifact", "target", "type"]
        # Don't let REST framework validate uniqueness constraints (which it
        # does automatically as of 3.15.0).  We want GetOrCreateAPIView to
        # return 200 if the relation already exists, which it will do if
        # REST framework hasn't returned 400 first due to a validation
        # failure.
        validators: list[Callable[[ArtifactRelation], None]] = []


class AssetSerializer(DebusineSerializerMixin, serializers.Serializer[Asset]):
    """Serializer to deserialize the client request to create an asset."""

    validation_error_title = "Cannot deserialize asset"

    id = serializers.IntegerField(required=False, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    category = serializers.CharField(max_length=255)
    scope = serializers.CharField(source="workspace.scope.name", read_only=True)
    workspace = WorkspaceInCurrentScopeField(slug_field="name")
    # Deliberately incompatible with Serializer.data.
    data = serializers.DictField()  # type: ignore[assignment]
    work_request = serializers.IntegerField(
        allow_null=True, required=False, source="created_by_work_request_id"
    )

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate category and data."""
        try:
            # Python 3.11 doesn't support X in StrEnum
            AssetCategory(data["category"])
        except ValueError:
            raise serializers.ValidationError(
                {"category": f"{data['category']} is not a known category."}
            )

        try:
            asset_data_model(data["category"], data["data"])
        except ValueError as e:
            raise serializers.ValidationError(
                {"data": f"invalid asset data: {e}"}
            ) from e
        return data

    def create(self, validated_data: dict[str, Any]) -> Asset:
        """Create an Asset model from deserialized data."""
        data = copy(validated_data)
        if not data.get("created_by_work_request_id"):
            data["created_by"] = context.require_user()
        return Asset.objects.create(**data)


class AssetPermissionCheckRequest(
    DebusineSerializerMixin, serializers.Serializer[Never]
):
    """Serializer for a permission check on an asset."""

    validation_error_title = "Cannot deserialize permission check request"

    artifact_id = serializers.IntegerField()
    work_request_id = serializers.IntegerField()
    workspace = serializers.CharField()


class AssetPermissionCheckResponse(
    DebusineSerializerMixin, serializers.Serializer[Never]
):
    """Serializer for a permission check result."""

    validation_error_title = "Cannot serialize permission check result"

    has_permission = serializers.BooleanField()
    username = serializers.CharField(max_length=150, required=False)
    user_id = serializers.IntegerField(required=False)
    resource = serializers.DictField(
        child=serializers.CharField(max_length=255), required=False
    )


class LookupExpectTypeChoiceField(serializers.ChoiceField):
    """
    A field for `Lookup*.expect_type`.

    This handles values of `LookupChildType`, but also accepts values of
    `CollectionItem.Types` for compatibility with old clients.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the field."""
        super().__init__(choices=list(LookupChildType))

    def to_internal_value(self, data: Any) -> str:
        """Accept values of `CollectionItemTypes` for compatibility."""
        if data in CollectionItem.Types:
            data = {
                CollectionItem.Types.BARE: LookupChildType.BARE,
                CollectionItem.Types.ARTIFACT: LookupChildType.ARTIFACT,
                CollectionItem.Types.COLLECTION: LookupChildType.COLLECTION,
            }[data]
        return super().to_internal_value(data)


class LookupSingleSerializer(serializers.Serializer[Never]):
    """Serializer for the data used to look up a single collection item."""

    lookup = serializers.JSONField()
    work_request = serializers.PrimaryKeyRelatedField(
        queryset=WorkRequest.objects
    )
    default_category = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )
    expect_type = LookupExpectTypeChoiceField()


class LookupMultipleSerializer(serializers.Serializer[Never]):
    """Serializer for the data used to look up multiple collection items."""

    lookup = serializers.JSONField()
    work_request = serializers.PrimaryKeyRelatedField(
        queryset=WorkRequest.objects
    )
    default_category = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )
    expect_type = LookupExpectTypeChoiceField()


class LookupResponseSerializer(serializers.Serializer[LookupResult]):
    """Serializer for the response to looking up collection items."""

    media_type = "application/json"

    result_type = serializers.ChoiceField(choices=list(CollectionItem.Types))
    collection_item: "serializers.PrimaryKeyRelatedField[CollectionItem]" = (
        serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    )
    artifact: "serializers.PrimaryKeyRelatedField[Artifact]" = (
        serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    )
    collection: "serializers.PrimaryKeyRelatedField[Collection]" = (
        serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    )


class WorkflowTemplateSerializer(
    DebusineSerializerMixin, serializers.ModelSerializer[WorkflowTemplate]
):
    """Serializer for workflow templates."""

    validation_error_title = "Cannot deserialize workflow template"

    media_type = "application/json"

    url = serializers.URLField(source="get_absolute_url", read_only=True)
    scope = serializers.CharField(source="workspace.scope.name", read_only=True)
    workspace = WorkspaceInCurrentScopeField(slug_field="name")

    class Meta:
        model = WorkflowTemplate
        fields = [
            "id",
            "url",
            "name",
            "scope",
            "workspace",
            "task_name",
            "task_data",
            "priority",
        ]

    def validate_task_name(self, value: str) -> str:
        """
        Ensure that task_name is valid.

        We don't currently validate task_data, since a template's task data
        is allowed to be incomplete.
        """
        try:
            Workflow.from_name(value)
        except ValueError as exc:
            raise serializers.ValidationError({"task_name": str(exc)})

        return value


class CreateWorkflowRequestSerializer(
    DebusineSerializerMixin, serializers.ModelSerializer[WorkRequest]
):
    """Serializer for requests to create workflows."""

    validation_error_title = "Cannot deserialize workflow creation request"

    media_type = "application/json"

    template_name = serializers.CharField(max_length=255)
    workspace = WorkspaceInCurrentScopeField(slug_field="name")

    class Meta:
        model = WorkRequest
        fields = [
            "template_name",
            "workspace",
            "task_data",
        ]


class PydanticSerializer(serializers.BaseSerializer[PydanticModel]):
    """Serialize pydantic models."""

    pydantic_model: type[PydanticModel]

    def to_representation(self, instance: PydanticModel) -> dict[str, Any]:
        """Delegate representation to pydantic."""
        return instance.dict()

    def to_internal_value(self, data: Any) -> PydanticModel:
        """Delegate validation to pydantic."""
        return self.pydantic_model(**data)


class DebusineTaskConfigurationSerializer(
    DebusineSerializerMixin, PydanticSerializer[DebusineTaskConfiguration]
):
    """Serialize a DebusineTaskConfiguration item."""

    pydantic_model = DebusineTaskConfiguration


class TaskConfigurationCollectionSerializer(
    DebusineSerializerMixin, serializers.ModelSerializer[Collection]
):
    """Serializer for a TaskConfiguration collection."""

    validation_error_title = "Cannot deserialize task configuration collection"
    media_type = "application/json"

    id = serializers.IntegerField()

    class Meta:
        model = Collection
        fields = ["id", "name", "data"]


class TaskConfigurationCollectionContents(NamedTuple):
    """Bundle together a TaskConfiguration collection and its items."""

    collection: Collection
    items: list[DebusineTaskConfiguration]


class TaskConfigurationCollectionContentsSerializer(
    DebusineSerializerMixin, serializers.Serializer[Never]
):
    """Serializer for the contents of a TaskConfiguration collection."""

    validation_error_title = "Cannot deserialize task configuration collection"
    media_type = "application/json"

    collection = TaskConfigurationCollectionSerializer()
    items = DebusineTaskConfigurationSerializer(many=True)


class TaskConfigurationCollectionUpdateResultsSerializer(
    DebusineSerializerMixin, serializers.Serializer[Never]
):
    """Serializer for the result information of a task configuration update."""

    validation_error_title = (
        "Cannot deserialize task configuration collection update results"
    )

    added = serializers.IntegerField()
    updated = serializers.IntegerField()
    removed = serializers.IntegerField()
    unchanged = serializers.IntegerField()


class WorkspaceChainItemField(
    FieldBase[Workspace, dict[str, Any], dict[str, Any], Workspace]
):
    """Serializers for a Workspace in a workspace chain."""

    def to_representation(self, value: Workspace) -> dict[str, Any]:
        """Trivially serialize workspaces with IDs."""
        res = WorkspaceInheritanceChainElement(
            id=value.id,
            scope=value.scope.name,
            workspace=value.name,
        )
        return model_to_json_serializable_dict(res)

    def _workspace_from_id(
        self, el: WorkspaceInheritanceChainElement
    ) -> Workspace:
        assert el.id is not None

        workspace_qs = Workspace.objects.can_display(
            context.require_user()
        ).select_related("scope")
        try:
            workspace = workspace_qs.get(pk=el.id)
        except Workspace.DoesNotExist:
            raise ValidationError(f"Workspace with id {el.id} does not exist")
        if el.workspace is not None and workspace.name != el.workspace:
            raise ValidationError(
                f"Workspace with id {el.id} is not named {el.workspace!r}"
            )
        if el.scope is not None and workspace.scope.name != el.scope:
            raise ValidationError(
                f"Workspace with id {el.id} is not in scope {el.scope!r}"
            )
        return workspace

    def _workspace_from_scope_workspace(
        self, el: WorkspaceInheritanceChainElement
    ) -> Workspace:
        assert el.workspace is not None

        workspace_qs = Workspace.objects.can_display(
            context.require_user()
        ).select_related("scope")

        if el.scope is None:
            el.scope = context.require_scope().name

        try:
            return workspace_qs.get(scope__name=el.scope, name=el.workspace)
        except Workspace.DoesNotExist:
            raise ValidationError(
                f"Workspace {el.workspace!r}"
                f" does not exist in scope {el.scope!r}"
            )

    def to_internal_value(self, data: Any) -> Workspace:
        """Parse a workspace from IDs or ``scope/name`` strings."""
        if not isinstance(data, dict):
            raise ValidationError("Workspace indicator should be a dict")

        try:
            parsed = WorkspaceInheritanceChainElement(**data)
        except ValueError as e:
            raise ValidationError(str(e))

        if parsed.id is not None:
            return self._workspace_from_id(parsed)
        else:
            return self._workspace_from_scope_workspace(parsed)


class WorkspaceChainSerializer(serializers.Serializer[Never]):
    """Serializer for a workspace chain."""

    chain = serializers.ListField(
        # Set an arbitrary high value to prevent a misguided API call impact
        # server performance
        child=WorkspaceChainItemField(),
        max_length=100,
    )
