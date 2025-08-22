# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: assets."""

from copy import copy
from logging import getLogger
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from debusine.artifacts.models import ArtifactCategory
from debusine.assets.models import AssetCategory
from debusine.db.context import context
from debusine.db.models import Artifact, ArtifactRelation, Asset, WorkRequest
from debusine.db.models.assets import AssetQuerySet, UnknownPermissionError
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import (
    AssetPermissionCheckRequest,
    AssetPermissionCheckResponse,
    AssetSerializer,
)
from debusine.server.views.base import (
    BaseAPIView,
    CanDisplayFilterBackend,
    GetOrCreateAPIView,
    ListAPIViewBase,
)

log = getLogger(__name__)


class AssetView(GetOrCreateAPIView[Asset], ListAPIViewBase[Asset], BaseAPIView):
    """View used to get or create artifacts."""

    filter_backends = [CanDisplayFilterBackend]
    parser_classes = [JSONParser]
    serializer_class = AssetSerializer
    pagination_class = None

    def get_queryset(self) -> AssetQuerySet[Any]:
        """Get the query set for this view."""
        filter_kwargs: dict[str, Any] = {}
        if self.request.method == "GET":
            if asset_id := self.request.GET.get("asset", None):
                filter_kwargs["id"] = asset_id
            if work_request_id := self.request.GET.get("work_request", None):
                filter_kwargs["created_by_work_request_id"] = work_request_id
            if workspace := self.request.GET.get("workspace", None):
                filter_kwargs["workspace__name"] = workspace
            if not filter_kwargs:
                raise DebusineAPIException(
                    title=(
                        '"asset", "work_request", or "workspace" parameters '
                        'are mandatory'
                    )
                )
        return Asset.objects.in_current_scope().filter(**filter_kwargs)

    def get_existing_object(self, serializer: BaseSerializer[Asset]) -> Asset:
        """
        If there is an existing object matching Serializer, return it.

        If not, raise ObjectDoesNotExist.
        """
        data = copy(serializer.validated_data)
        match data["category"]:
            case AssetCategory.SIGNING_KEY:
                obj = self.get_queryset().get(
                    category=data["category"],
                    data__purpose=data["data"].get("purpose", None),
                    data__fingerprint=data["data"].get("fingerprint", None),
                )
            case _:  # pragma: no cover
                raise NotImplementedError(
                    f"Unable to locate assets of category '{data['category']}'"
                )
        assert isinstance(obj, Asset)
        return obj


class AssetPermissionCheckView(BaseAPIView):
    """Check if a work request is allowed to use an asset."""

    def get_object(self, asset_category: str, asset_slug: str) -> Asset:
        """Return the requested asset."""
        try:
            return Asset.objects.get_by_slug(
                category=asset_category,
                slug=asset_slug,
            )
        except Asset.DoesNotExist as exc:
            raise DebusineAPIException(
                title=str(exc), status_code=status.HTTP_404_NOT_FOUND
            )
        except ValueError as exc:
            raise DebusineAPIException(
                title=f"Malformed asset_slug: {exc}",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    @classmethod
    def describe_artifact(cls, artifact: Artifact) -> dict[str, str]:
        """Return a dictionary description of artifact."""
        resource: dict[str, str] = {}
        match artifact.category:
            case ArtifactCategory.REPOSITORY_INDEX:
                # A debian:repository-index artifact must have exactly one
                # file.
                resource["path"] = artifact.fileinartifact_set.get().path
            case ArtifactCategory.SIGNING_INPUT:
                resource["package"] = artifact.data["binary_package_name"]
                try:
                    extracted_from = artifact.relations.get(
                        type=ArtifactRelation.Relations.RELATES_TO,
                        target__category=ArtifactCategory.BINARY_PACKAGE,
                    ).target
                except ArtifactRelation.DoesNotExist:
                    log.info(
                        (
                            "Unable to fully describe artifact %r, no related "
                            "debian:binary-package."
                        ),
                        artifact,
                    )
                except ArtifactRelation.MultipleObjectsReturned:
                    log.info(
                        (
                            "Unable to fully describe artifact %r, multiple "
                            "related debian:binary-package."
                        ),
                        artifact,
                    )
                else:
                    resource["source"] = extracted_from.data["srcpkg_name"]
                    deb_fields = extracted_from.data["deb_fields"]
                    resource["architecture"] = deb_fields["Architecture"]
                    if not resource["package"]:
                        resource["package"] = deb_fields["Package"]
                    resource["version"] = deb_fields["Version"]
            case ArtifactCategory.UPLOAD:
                match artifact.data["type"]:
                    case "dpkg":
                        changes_fields = artifact.data["changes_fields"]
                        # coverage 6.5.0 gets confused here
                        for key in (
                            "Architecture",
                            "Source",
                            "Version",
                            "Distribution",
                        ):  # pragma: no cover
                            resource[key.lower()] = changes_fields[key]
                    case _:
                        raise NotImplementedError(
                            f"Unable to describe upload artifact {artifact}"
                        )
            case _:
                raise NotImplementedError(
                    f"Unable to describe artifact {artifact}"
                )
        return resource

    def post(
        self,
        request: Request,
        asset_category: str,
        asset_slug: str,
        permission_name: str,
    ) -> Response:
        """Handle a POST request to check permissions."""
        check_request = AssetPermissionCheckRequest(data=request.data)
        check_request.is_valid(raise_exception=True)

        asset = self.get_object(
            asset_category=asset_category, asset_slug=asset_slug
        )

        try:
            artifact = Artifact.objects.get(
                id=check_request.validated_data["artifact_id"]
            )
            work_request = WorkRequest.objects.get(
                id=check_request.validated_data["work_request_id"]
            )
        except ObjectDoesNotExist as exc:
            raise DebusineAPIException(title=str(exc))

        user = work_request.created_by
        # Note: Worker API requests can currently access all workspaces
        # Pretent that we aren't in a worker API request.
        # The context hack can be removed once #523 is resolved.
        with context.local():
            context.reset()

            if not work_request.can_display(user):
                raise DebusineAPIException(
                    title=(
                        f"WorkRequest {work_request.id} is not visible to "
                        f"{user}"
                    )
                )
            if not artifact.can_display(user):
                raise DebusineAPIException(
                    title=f"Artifact {artifact.id} is not visible to {user}"
                )

        task = work_request.get_task()
        if artifact.id not in task.get_input_artifacts_ids():
            raise DebusineAPIException(
                title=f"{artifact} is not an input to {work_request}"
            )

        workspace = work_request.workspace
        if workspace.name != check_request.validated_data["workspace"]:
            raise DebusineAPIException(
                title="workspace does not match work request"
            )

        try:
            has_permission = asset.has_permission(
                permission=permission_name,
                user=user,
                workspace=workspace,
            )
        except UnknownPermissionError:
            raise DebusineAPIException(
                title=f"Unknown permission '{permission_name}'"
            )

        response = AssetPermissionCheckResponse(
            data={
                "has_permission": has_permission,
                "username": user.username,
                "user_id": user.id,
                "resource": self.describe_artifact(artifact),
            }
        )
        response.is_valid(raise_exception=True)
        return Response(response.data, status=status.HTTP_200_OK)
