# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for package archives."""

import re
from abc import ABC, abstractmethod
from typing import Any

from django.db.models import QuerySet
from django.db.models.fields.json import KT
from django.http import Http404, HttpRequest, HttpResponseBase
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_cache_control, patch_vary_headers
from rest_framework import status
from rest_framework.request import Request

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import Collection, CollectionItem, FileInArtifact
from debusine.server.collections.debian_suite import make_source_prefix
from debusine.server.exceptions import DebusineAPIException
from debusine.server.views.base import BaseAPIView
from debusine.web.views.files import FileDownloadMixin, FileUI


class ArchiveFileView(BaseAPIView, FileDownloadMixin, ABC):
    """A file in an archive."""

    model = FileInArtifact
    archive: Collection

    @property
    def cache_control(self) -> dict[str, Any]:
        """Return appropriate ``Cache-Control`` arguments for this response."""
        # Responses resulting from a snapshot-specific query are immutable.
        # For other responses, default to telling caches to consider them
        # stale after a relatively short delay; subclasses may override
        # this.
        return (
            {"max-age": 31536000}
            if self.kwargs.get("snapshot") is not None
            else {"max-age": 1800, "proxy-revalidate": True}
        )

    def get_snapshot(self) -> QuerySet[CollectionItem]:
        """Return a queryset of items active at the selected timestamp."""
        qs = CollectionItem.objects.filter(
            parent_collection__workspace=context.require_workspace()
        )
        if (snapshot := self.kwargs.get("snapshot")) is not None:
            return qs.active_at(snapshot)
        else:
            return qs.active()

    @abstractmethod
    def get_collection_items(self) -> QuerySet[CollectionItem]:
        """
        Return a queryset of collection items matching the requested file.

        Subclasses should use their URL arguments to narrow the filter until
        it is guaranteed to return at most one item pointing to an artifact
        containing exactly one file.
        """
        return self.get_snapshot().filter(
            child_type=CollectionItem.Types.ARTIFACT, artifact__isnull=False
        )

    def get_queryset(self) -> QuerySet[FileInArtifact]:
        """Filter to a single matching file."""
        # Maybe this should use the generic collection lookup mechanism, but
        # that doesn't currently support snapshots or other things we need
        # such as constraining pool files by component and source package
        # name.  In any case, it seems worth having an optimized lookup path
        # for these views.
        return FileInArtifact.objects.filter(
            artifact__in=self.get_collection_items().values("artifact")
        )

    def get(
        self, request: Request, *args: Any, **kwargs: Any  # noqa: U100
    ) -> HttpResponseBase:
        """Download a file."""
        self.set_current_workspace(self.kwargs["workspace"])
        workspace = context.require_workspace()
        try:
            self.archive = Collection.objects.get(
                workspace=workspace, category=CollectionCategory.ARCHIVE
            )
        except Collection.DoesNotExist:
            raise DebusineAPIException(
                title="Archive not found",
                detail=(
                    f"No {CollectionCategory.ARCHIVE} collection found in "
                    f"{workspace}"
                ),
                status_code=status.HTTP_404_NOT_FOUND,
            )
        self.enforce(self.archive.can_display)

        try:
            file_in_artifact = get_object_or_404(self.get_queryset())
        except Http404 as exc:
            raise DebusineAPIException(
                title=str(exc), status_code=status.HTTP_404_NOT_FOUND
            )
        ui_info = FileUI.from_file_in_artifact(file_in_artifact)
        return self.stream_file(file_in_artifact, ui_info)

    def dispatch(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Dispatch the request, setting appropriate response headers."""
        response = super().dispatch(request, *args, **kwargs)
        patch_cache_control(response, **self.cache_control)
        patch_vary_headers(response, ("Authorization",))
        return response


class SuiteFileView(ArchiveFileView, ABC):
    """A file looked up via a suite."""

    @abstractmethod
    def get_collection_items(self) -> QuerySet[CollectionItem]:
        """Filter to items in any suite in the selected archive."""
        suite_qs = self.get_snapshot().filter(
            child_type=CollectionItem.Types.COLLECTION,
            collection__isnull=False,
            parent_category=CollectionCategory.ARCHIVE,
            category=CollectionCategory.SUITE,
        )
        if (suite := self.kwargs.get("suite")) is not None:
            suite_qs = suite_qs.filter(name=suite)
        return (
            super()
            .get_collection_items()
            .filter(
                parent_collection__in=suite_qs.values("collection"),
                # Technically redundant with the filters in suite_qs, but
                # helps PostgreSQL to use the correct index.
                parent_category=CollectionCategory.SUITE,
            )
        )


class DistsByHashFileView(SuiteFileView):
    """An index file in a ``by-hash`` directory."""

    @property
    def cache_control(self) -> dict[str, Any]:
        """``by-hash`` responses are always immutable."""
        return {"max-age": 31536000}

    def get_snapshot(self) -> QuerySet[CollectionItem]:
        """
        Return a queryset of items that existed at the selected timestamp.

        This is slightly different for ``by-hash`` files than others: the
        items need to have existed at the given timestamp, but they don't
        need to have been active.
        """
        qs = CollectionItem.objects.filter(
            parent_collection__workspace=context.require_workspace()
        )
        if (snapshot := self.kwargs.get("snapshot")) is not None:
            qs = qs.filter(created_at__lte=snapshot)
        return qs

    def get_collection_items(self) -> QuerySet[CollectionItem]:
        """Filter to the requested index file."""
        return (
            super()
            .get_collection_items()
            .annotate(path=KT("data__path"))
            .filter(
                category=ArtifactCategory.REPOSITORY_INDEX,
                # The directory name must be equal to everything up to the
                # last slash.
                path__regex=f"^{re.escape(self.kwargs['directory'])}/[^/]+$",
            )
            # Release files and friends don't have by-hash entries.
            .exclude(path__in=("Release", "Release.gpg", "InRelease"))
        )

    def get_queryset(self) -> QuerySet[FileInArtifact]:
        """Filter to a file with the requested checksum."""
        # XXX 404 if bytes.fromhex fails
        return (
            super()
            .get_queryset()
            .filter(file__sha256=bytes.fromhex(self.kwargs["checksum"]))
        )


class DistsFileView(SuiteFileView):
    """An index file in a suite."""

    def get_collection_items(self) -> QuerySet[CollectionItem]:
        """Filter to the requested index file."""
        return (
            super()
            .get_collection_items()
            .annotate(path=KT("data__path"))
            .filter(
                category=ArtifactCategory.REPOSITORY_INDEX,
                path=self.kwargs["path"],
            )
        )


class PoolFileView(SuiteFileView):
    """
    A package in the archive's pool.

    While the suite isn't in the URL, we still need to look up pool files
    via any suite in the archive.  It may be in more than one suite, but
    constraints guarantee that they all refer to the same contents.
    """

    _re_binary_package = re.compile(
        r"^([^_]+)_([^_]+)_([^.]+)\.(?:deb|ddeb|udeb)$"
    )

    @property
    def cache_control(self) -> dict[str, Any]:
        """Pool files in ``may_reuse_versions=False`` archives are immutable."""
        if hasattr(self, "archive") and not self.archive.data.get(
            "may_reuse_versions", False
        ):
            return {"max-age": 31536000}
        else:
            return super().cache_control

    def get_collection_items(self) -> QuerySet[CollectionItem]:
        """Filter to the requested pool file."""
        if self.kwargs["sourceprefix"] != make_source_prefix(
            self.kwargs["source"]
        ):
            raise DebusineAPIException(
                title="No FileInArtifact matches the given query.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        qs = (
            super()
            .get_collection_items()
            .annotate(component=KT("data__component"))
            .filter(component=self.kwargs["component"])
        )
        # Although filtering on collection item properties here is somewhat
        # redundant with the path filter that get_queryset() will add, it
        # allows us to make use of db_ci_suite_source_idx and
        # db_ci_suite_binary_source_idx, and it means the database can
        # filter out irrelevant CollectionItem rows before needing to look
        # at FileInArtifact.  We don't bother constraining these searches by
        # version, since some files in source packages only contain the
        # upstream part of the version; this also saves us from worrying
        # about epochs.
        if m := self._re_binary_package.match(self.kwargs["filename"]):
            qs = qs.annotate(
                srcpkg_name=KT("data__srcpkg_name"),
                package=KT("data__package"),
                architecture=KT("data__architecture"),
            ).filter(
                category=ArtifactCategory.BINARY_PACKAGE,
                srcpkg_name=self.kwargs["source"],
                package=m.group(1),
                architecture=m.group(3),
            )
        else:
            qs = qs.annotate(package=KT("data__package")).filter(
                category=ArtifactCategory.SOURCE_PACKAGE,
                package=self.kwargs["source"],
            )
        return qs

    def get_queryset(self) -> QuerySet[FileInArtifact]:
        """Filter to a file with the requested name."""
        return (
            super()
            .get_queryset()
            .filter(path=self.kwargs["filename"])
            .distinct("file")
        )


class TopLevelFileView(ArchiveFileView):
    """A top-level file in the archive, not in any suite."""

    def get_collection_items(self) -> QuerySet[CollectionItem]:
        """Filter to the requested index file."""
        return (
            super()
            .get_collection_items()
            .annotate(path=KT("data__path"))
            .filter(
                parent_category=CollectionCategory.ARCHIVE,
                category=ArtifactCategory.REPOSITORY_INDEX,
                path=self.kwargs["path"],
            )
        )
