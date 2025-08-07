# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for db artifacts."""

from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Generic, Optional, TYPE_CHECKING, TypeVar

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Exists, F, OuterRef, Q, QuerySet, UniqueConstraint
from django.db.models.functions import Coalesce
from django.urls import reverse

from debusine.artifacts import LocalArtifact
from debusine.artifacts.models import ArtifactCategory, ArtifactData
from debusine.db.context import context
from debusine.db.models.files import File
from debusine.db.models.permissions import (
    AllowWorkers,
    PermissionUser,
    permission_check,
    permission_filter,
)
from debusine.db.models.workspaces import Workspace
from debusine.utils.typing_utils import copy_signature_from

if TYPE_CHECKING:
    from django_stubs_ext.db.models import TypedModelMeta

    from debusine.db.models.work_requests import WorkRequest
else:
    TypedModelMeta = object

A = TypeVar("A")

ARTIFACT_CATEGORY_ICON_NAMES = {
    ArtifactCategory.AUTOPKGTEST: "folder",
    ArtifactCategory.BINARY_PACKAGE: "folder",
    ArtifactCategory.BINARY_PACKAGES: "folder",
    ArtifactCategory.BLHC: "folder",
    ArtifactCategory.DEBDIFF: "folder",
    ArtifactCategory.LINTIAN: "folder",
    ArtifactCategory.PACKAGE_BUILD_LOG: "journal-text",
    ArtifactCategory.SIGNING_INPUT: "folder",
    ArtifactCategory.SIGNING_OUTPUT: "folder",
    ArtifactCategory.SOURCE_PACKAGE: "folder",
    ArtifactCategory.SYSTEM_IMAGE: "folder",
    ArtifactCategory.SYSTEM_TARBALL: "folder",
    ArtifactCategory.UPLOAD: "folder",
    ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: "folder",
}

ARTIFACT_CATEGORY_SHORT_NAMES = {
    ArtifactCategory.AUTOPKGTEST: "autopkgtest",
    ArtifactCategory.BINARY_PACKAGE: "binary package",
    ArtifactCategory.BINARY_PACKAGES: "binary packages",
    ArtifactCategory.BLHC: "blhc report",
    ArtifactCategory.DEBDIFF: "debdiff report",
    ArtifactCategory.LINTIAN: "lintian report",
    ArtifactCategory.PACKAGE_BUILD_LOG: "build log",
    ArtifactCategory.SIGNING_INPUT: "signing input",
    ArtifactCategory.SIGNING_OUTPUT: "signing output",
    ArtifactCategory.SOURCE_PACKAGE: "source package",
    ArtifactCategory.SYSTEM_IMAGE: "system image",
    ArtifactCategory.SYSTEM_TARBALL: "system tar",
    ArtifactCategory.UPLOAD: "package upload",
    ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: "debug log",
}


class ArtifactQuerySet(QuerySet["Artifact", A], Generic[A]):
    """Custom QuerySet for Artifact."""

    def in_current_scope(self) -> "ArtifactQuerySet[A]":
        """Filter to artifacts in the current scope."""
        return self.filter(workspace__scope=context.require_scope())

    def in_current_workspace(self) -> "ArtifactQuerySet[A]":
        """Filter to artifacts in the current workspace."""
        return self.filter(workspace=context.require_workspace())

    @permission_filter(workers=AllowWorkers.ALWAYS)
    def can_display(self, user: PermissionUser) -> "ArtifactQuerySet[A]":
        """Keep only Artifacts that can be displayed."""
        # Delegate to workspace can_display check
        return self.filter(workspace__in=Workspace.objects.can_display(user))

    # mypy_django_plugin generates an
    # Artifact@AnnotatedWith[TypedDict({"complete": Any})] model subclass
    # for this, but we don't have a way to refer to it here.
    def annotate_complete(self) -> "ArtifactQuerySet[Any]":
        """Annotate artifacts with whether all their files are complete."""
        return self.annotate(
            complete=~Exists(
                FileInArtifact.objects.filter(
                    artifact=OuterRef("pk"), complete=False
                )
            )
        )

    def not_expired(self, at: datetime) -> "ArtifactQuerySet[Any]":
        """
        Return queryset with artifacts that have not expired.

        :param at: datetime to check if the artifacts are not expired.
        :return: artifacts that expire_at is None (do not expire) or
          expire_at is after the given datetime.
        """
        return self.annotate(
            effective_expiration_delay=Coalesce(
                "expiration_delay",
                "workspace__default_expiration_delay",
            )
        ).filter(
            Q(effective_expiration_delay=timedelta(0))
            | Q(
                # https://github.com/typeddjango/django-stubs/issues/1548
                created_at__gt=(
                    at - F("effective_expiration_delay")  # type: ignore[operator] # noqa: E501
                )
            )
        )

    def expired(self, at: datetime) -> "ArtifactQuerySet[Any]":
        """
        Return queryset with artifacts that have expired.

        :param at: datetime to check if the artifacts are expired.
        :return: artifacts that expire_at is before the given datetime.
        """
        return (
            self.annotate(
                effective_expiration_delay=Coalesce(
                    "expiration_delay",
                    "workspace__default_expiration_delay",
                )
            )
            .exclude(effective_expiration_delay=timedelta(0))
            .filter(
                # https://github.com/typeddjango/django-stubs/issues/1548
                created_at__lte=(
                    at - F("effective_expiration_delay")  # type: ignore[operator] # noqa: E501
                )
            )
        )

    def part_of_collection_with_retains_artifacts(
        self,
    ) -> "ArtifactQuerySet[Any]":
        """
        Return Artifacts where the parent_collections has retains_artifacts set.

        :return: Artifacts that are part of a retains_artifacts collection.
        """
        # Import here to prevent circular imports
        from debusine.db.models.collections import Collection
        from debusine.db.models.work_requests import WorkRequest

        RetainsArtifacts = Collection.RetainsArtifacts
        return self.filter(
            Q(parent_collections__retains_artifacts=RetainsArtifacts.ALWAYS)
            | Q(
                parent_collections__retains_artifacts=RetainsArtifacts.WORKFLOW,
                parent_collections__workflow__status__in={
                    WorkRequest.Statuses.PENDING,
                    WorkRequest.Statuses.RUNNING,
                    WorkRequest.Statuses.BLOCKED,
                },
            )
        )


class ArtifactManager(models.Manager["Artifact"]):
    """Manager for the Artifact model."""

    def get_queryset(self) -> ArtifactQuerySet[Any]:
        """Use the custom QuerySet."""
        return ArtifactQuerySet(self.model, using=self._db)

    @classmethod
    def create_from_local_artifact(
        cls,
        local_artifact: LocalArtifact[Any],
        workspace: Workspace,
        *,
        created_by_work_request: Optional["WorkRequest"] = None,
    ) -> "Artifact":
        """Return a new Artifact based on a :class:`LocalArtifact`."""
        artifact = Artifact.objects.create(
            category=local_artifact.category,
            workspace=workspace,
            data=local_artifact.data.dict(),
            created_by_work_request=created_by_work_request,
        )

        for artifact_path, local_path in local_artifact.files.items():
            file = File.from_local_path(local_path)
            file_backend = workspace.scope.upload_file_backend(file)
            file_backend.add_file(local_path, fileobj=file)
            FileInArtifact.objects.create(
                artifact=artifact, path=artifact_path, file=file, complete=True
            )

        return artifact


class Artifact(models.Model):
    """Artifact model."""

    category = models.CharField(max_length=255)
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT)
    files = models.ManyToManyField(File, through="db.FileInArtifact")
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expiration_delay = models.DurationField(blank=True, null=True)
    created_by = models.ForeignKey(
        "User", blank=True, null=True, on_delete=models.PROTECT
    )
    created_by_work_request = models.ForeignKey(
        "WorkRequest", blank=True, null=True, on_delete=models.SET_NULL
    )
    original_artifact = models.ForeignKey(
        "Artifact", blank=True, null=True, on_delete=models.SET_NULL
    )

    objects = ArtifactManager.from_queryset(ArtifactQuerySet)()

    def clean(self) -> None:
        """
        Ensure that data is valid for this artifact category.

        :raise ValidationError: for invalid data.
        """
        if not isinstance(self.data, dict):
            raise ValidationError({"data": "data must be a dictionary"})

        try:
            artifact_cls = LocalArtifact.class_from_category(self.category)
        except ValueError as e:
            raise ValidationError(
                {"category": f"{self.category}: invalid artifact category"}
            ) from e

        try:
            artifact_cls.create_data(self.data)
        except ValueError as e:
            raise ValidationError(
                {"data": f"invalid artifact data: {e}"}
            ) from e

    @permission_check(
        "{user} cannot display artifact {resource}", workers=AllowWorkers.ALWAYS
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the artifact can be displayed."""
        return self.workspace.can_display(user)

    def create_data(self) -> ArtifactData:
        """Instantiate ArtifactData from data."""
        artifact_cls = LocalArtifact.class_from_category(self.category)
        artifact_data = artifact_cls.create_data(self.data)
        assert isinstance(artifact_data, ArtifactData)
        return artifact_data

    @copy_signature_from(models.Model.save)
    def save(self, **kwargs: Any) -> None:
        """Wrap save with permission checks."""
        from debusine.db.context import context

        if self._state.adding:
            # Create
            if not self.workspace.can_create_artifacts(context.user):
                raise PermissionDenied(
                    f"{context.user} cannot create artifacts"
                    f" in {self.workspace}"
                )
        else:
            # Update
            ...  # TODO: check for update permissions

        return super().save(**kwargs)

    def get_label(self, data: ArtifactData | None = None) -> str:
        """
        Return a label for this artifact.

        Optionally reuse an already instantiated data model.
        """
        if data is None:
            data = self.create_data()
        if label := data.get_label():
            return label
        return str(self.category)

    def effective_expiration_delay(self) -> timedelta:
        """Return expiration_delay, inherited if None."""
        expiration_delay = self.expiration_delay
        if self.expiration_delay is None:  # inherit
            expiration_delay = self.workspace.default_expiration_delay
        assert expiration_delay is not None
        return expiration_delay

    @property
    def expire_at(self) -> datetime | None:
        """Return computed expiration date."""
        delay = self.effective_expiration_delay()
        if delay == timedelta(0):
            return None
        return self.created_at + delay

    def expired(self, at: datetime) -> bool:
        """
        Return True if this artifact has expired at a given datetime.

        :param at: datetime to check if the artifact is expired.
        :return bool: True if the artifact's expire_at is on or earlier than
          the parameter at.
        """
        expire_at = self.expire_at
        if expire_at is None:
            return False
        return expire_at <= at

    def get_absolute_url(self) -> str:
        """Return the canonical URL to display the artifact."""
        return reverse(
            "workspaces:artifacts:detail",
            kwargs={"wname": self.workspace.name, "artifact_id": self.id},
        )

    def get_absolute_url_download(self) -> str:
        """Return the canonical URL to download the artifact."""
        return reverse(
            "workspaces:artifacts:download",
            kwargs={"wname": self.workspace.name, "artifact_id": self.id},
        )

    def __str__(self) -> str:
        """Return basic information of Artifact."""
        return (
            f"Id: {self.id} "
            f"Category: {self.category} "
            f"Workspace: {self.workspace.id}"
        )


class FileInArtifact(models.Model):
    """File in artifact."""

    artifact = models.ForeignKey(Artifact, on_delete=models.PROTECT)
    path = models.CharField(max_length=500)
    file = models.ForeignKey(File, on_delete=models.PROTECT)
    # We do a best-effort data migration here, but note that in cases where
    # a file store is shared between multiple workspaces, this field may be
    # left as False for files that were uploaded before this field was
    # added.  (debusine.debian.net only had a single workspace at the time
    # of this migration, so that's not a problem there.)
    complete = models.BooleanField(default=False)

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["artifact", "path"],
                name="%(app_label)s_%(class)s_unique_artifact_path",
            ),
        ]

    def __str__(self) -> str:
        """Return basic information of FileInArtifact."""
        return (
            f"Id: {self.id} Artifact: {self.artifact.id} "
            f"Path: {self.path} File: {self.file.id}"
        )

    def get_absolute_url(self) -> str:
        """Return an absolute URL to view this file."""
        return reverse(
            "workspaces:artifacts:file-detail",
            kwargs={
                "wname": self.artifact.workspace.name,
                "artifact_id": self.artifact.id,
                "path": self.path,
            },
        )

    def get_absolute_url_raw(self) -> str:
        """Return an absolute URL to view this file as raw."""
        return reverse(
            "workspaces:artifacts:file-raw",
            kwargs={
                "wname": self.artifact.workspace.name,
                "artifact_id": self.artifact.id,
                "path": self.path,
            },
        )

    def get_absolute_url_download(self) -> str:
        """Return an absolute URL to download this file."""
        return reverse(
            "workspaces:artifacts:file-download",
            kwargs={
                "wname": self.artifact.workspace.name,
                "artifact_id": self.artifact.id,
                "path": self.path,
            },
        )


class FileUpload(models.Model):
    """File that is being/has been uploaded."""

    file_in_artifact = models.OneToOneField(
        FileInArtifact, on_delete=models.PROTECT
    )
    path = models.CharField(
        max_length=500,
        help_text="Path in the uploads directory",
        unique=True,
    )
    last_activity_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def current_size(cls, artifact: Artifact, path_in_artifact: str) -> int:
        """
        Return current file size.

        The current file size might be smaller than the expected size of the
        file if the file has not finished being uploaded.

        Raise ValueError if path_in_artifact does not exist in Artifact or
        if there's no FileUpload object for the specific File.
        """
        try:
            file_in_artifact = FileInArtifact.objects.get(
                artifact=artifact, path=path_in_artifact
            )
        except FileInArtifact.DoesNotExist:
            raise ValueError(
                f'No FileInArtifact for Artifact {artifact.id} '
                f'and path "{path_in_artifact}"'
            )

        try:
            file_upload = FileUpload.objects.get(
                file_in_artifact=file_in_artifact
            )
        except FileUpload.DoesNotExist:
            raise ValueError(
                f"No FileUpload for FileInArtifact {file_in_artifact.id}"
            )

        try:
            size = file_upload.absolute_file_path().stat().st_size
        except FileNotFoundError:
            size = 0

        return size

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Schedule deletion of the file in the store."""
        file_path = self.absolute_file_path()

        result = super().delete(*args, **kwargs)

        # If this method is called from a transaction: transaction.on_commit()
        # will call file_path.unlink when the most outer transaction is
        # committed.
        #
        # In the case that the code is running without a transaction:
        # the file_path.unlink will happen now.
        #
        # It's important that file_path.unlink is called only if the
        # DB is updated with the deletion. Otherwise, the file could be
        # deleted from the store but still referenced from the DB.
        transaction.on_commit(partial(file_path.unlink, missing_ok=True))

        return result

    def absolute_file_path(self) -> Path:
        """
        Return the absolute file path of the file.

        The files are stored in settings.DEBUSINE_UPLOAD_DIRECTORY.
        """
        return Path(settings.DEBUSINE_UPLOAD_DIRECTORY) / self.path

    def __str__(self) -> str:
        """Return basic information."""
        return f"{self.id}"


class ArtifactRelationQuerySet(QuerySet["ArtifactRelation", A], Generic[A]):
    """Custom QuerySet for ArtifactRelation."""

    def in_current_scope(self) -> "ArtifactRelationQuerySet[A]":
        """Filter to artifact relations in the current scope."""
        scope = context.require_scope()
        return self.filter(
            artifact__workspace__scope=scope, target__workspace__scope=scope
        )

    @permission_filter(workers=AllowWorkers.ALWAYS)
    def can_display(
        self, user: PermissionUser
    ) -> "ArtifactRelationQuerySet[A]":
        """Keep only ArtifactRelations that can be displayed."""
        # Delegate to workspace can_display check
        workspaces = Workspace.objects.can_display(user)
        return self.filter(
            artifact__workspace__in=workspaces, target__workspace__in=workspaces
        )


class ArtifactRelationManager(models.Manager["ArtifactRelation"]):
    """Manager for the ArtifactRelation model."""

    def get_queryset(self) -> ArtifactRelationQuerySet[Any]:
        """Use the custom QuerySet."""
        return ArtifactRelationQuerySet(self.model, using=self._db)


class ArtifactRelation(models.Model):
    """Model relations between artifacts."""

    class Relations(models.TextChoices):
        EXTENDS = "extends", "Extends"
        RELATES_TO = "relates-to", "Relates to"
        BUILT_USING = "built-using", "Built using"

    objects = ArtifactRelationManager.from_queryset(ArtifactRelationQuerySet)()

    artifact = models.ForeignKey(
        Artifact, on_delete=models.PROTECT, related_name="relations"
    )
    target = models.ForeignKey(
        Artifact, on_delete=models.PROTECT, related_name="targeted_by"
    )
    type = models.CharField(max_length=11, choices=Relations.choices)

    def __str__(self) -> str:
        """Return str for the object."""
        return f"{self.artifact.id} {self.type} {self.target.id}"

    class Meta(TypedModelMeta):
        constraints = [
            UniqueConstraint(
                fields=["artifact", "target", "type"],
                name="%(app_label)s_%(class)s_unique_artifact_target_type",
            )
        ]

    @permission_check(
        "{user} cannot display artifact relation {resource}",
        workers=AllowWorkers.ALWAYS,
    )
    def can_display(self, user: PermissionUser) -> bool:
        """Check if the artifact can be displayed."""
        return self.artifact.workspace.can_display(
            user
        ) and self.target.workspace.can_display(user)
