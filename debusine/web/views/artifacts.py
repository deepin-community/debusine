# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine artifact views."""

import abc
import os.path
from enum import StrEnum
from functools import cached_property
from typing import Any, assert_never

from django.core.exceptions import PermissionDenied
from django.db.models import F, QuerySet
from django.db.models.functions import Lower
from django.http import Http404, HttpRequest, StreamingHttpResponse
from django.http.response import HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils.http import http_date
from rest_framework import status

from debusine.artifacts.models import ArtifactCategory
from debusine.db.context import context
from debusine.db.models import Artifact, ArtifactRelation, FileInArtifact
from debusine.server.tar import TarArtifact
from debusine.web.forms import ArtifactForm
from debusine.web.views import sidebar, ui_shortcuts
from debusine.web.views.base import (
    BaseUIView,
    CreateViewBase,
    DetailViewBase,
    SingleObjectMixinBase,
    WorkspaceView,
)
from debusine.web.views.base_rightbar import RightbarUIView
from debusine.web.views.files import (
    FileDownloadMixin,
    FileUI,
    FileWidget,
    PathMixin,
)
from debusine.web.views.http_errors import HttpError400, catch_http_errors
from debusine.web.views.sidebar import SidebarItem
from debusine.web.views.view_utils import format_yaml


class ArtifactView(WorkspaceView, RightbarUIView, abc.ABC):
    """Base view for UIs showing an artifact or part of it."""

    permission_denied_message = (
        "Non-public artifact: you might need to login "
        "or make a request with a valid Token header"
    )

    #: Artifact for this view
    artifact: Artifact

    @abc.abstractmethod
    def get_artifact(self) -> Artifact:
        """Return the current artifact."""

    def init_view(self) -> None:
        """Set the current artifact."""
        super().init_view()
        self.artifact = self.get_artifact()
        self.enforce(self.artifact.can_display)

    def get_sidebar_items(self) -> list[SidebarItem]:
        """Return a list of sidebar items."""
        items = super().get_sidebar_items()
        artifact = self.artifact
        items.append(sidebar.create_artifact_category(artifact))
        items.append(sidebar.create_workspace(artifact.workspace))
        items.append(
            sidebar.create_work_request(
                artifact.created_by_work_request, link=True
            )
        )
        items.append(sidebar.create_user(artifact.created_by, context=artifact))
        items.append(sidebar.create_created_at(artifact.created_at))
        items.append(sidebar.create_expire_at(artifact.expire_at))
        return items


class ArtifactDetailView(
    ArtifactView, DetailViewBase[Artifact], RightbarUIView
):
    """Display an artifact and its file(s)."""

    model = Artifact
    pk_url_kwarg = "artifact_id"
    template_name = "web/artifact-detail.html"
    context_object_name = "artifact"

    def get_artifact(self) -> Artifact:
        """Artifact for this view."""
        return self.get_object()

    def get_queryset(self) -> QuerySet[Artifact]:
        """Add the select_related we need to the queryset."""
        return super().get_queryset().select_related("workspace")

    def get_title(self) -> str:
        """Return the page title."""
        return f"Artifact {self.object.get_label()}"

    def get_main_ui_shortcuts(self) -> list[ui_shortcuts.UIShortcut]:
        """Return a list of UI shortcuts for this view."""
        shortcuts = super().get_main_ui_shortcuts()

        # TODO: I am not sure yet how to architecture category-dependenty
        # behaviour, and I'll postpone doing it until we have more cases that
        # should make it more obvious which way to refactor the code to avoid
        # a scattering of UI-related category matches
        if (work_request := self.object.created_by_work_request) and (
            self.object.category != ArtifactCategory.PACKAGE_BUILD_LOG
        ):
            try:
                build_log = Artifact.objects.filter(
                    created_by_work_request=work_request,
                    category=ArtifactCategory.PACKAGE_BUILD_LOG,
                ).latest("created_at")
                shortcuts.append(ui_shortcuts.create_artifact_view(build_log))
            except Artifact.DoesNotExist:
                pass
        shortcuts.append(ui_shortcuts.create_artifact_download(self.object))

        return shortcuts

    @cached_property
    def file_list(self) -> list[FileInArtifact]:
        """Return the files in the artifact."""
        file_list: list[FileInArtifact] = []
        queryset = (
            FileInArtifact.objects.filter(artifact=self.object)
            .select_related("file")
            .order_by(Lower("path"))
        )
        for file_in_artifact in queryset:
            file_list.append(file_in_artifact)
            setattr(
                file_in_artifact,
                "basename",
                os.path.basename(file_in_artifact.path),
            )
        return file_list

    def _current_view_is_specialized(self) -> bool:
        """
        Specialized (based on a plugin) view will be served.

        User did not force the default view, a plugin exists, and the work
        request passes validation.
        """
        return ArtifactPlugin.plugin_for(self.artifact.category) is not None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Return context for this view."""
        ctx = super().get_context_data(**kwargs)

        plugin_view = ArtifactPlugin.plugin_for(self.artifact.category)

        for file_in_artifact in self.file_list:
            self.add_object_ui_shortcuts(
                file_in_artifact,
                ui_shortcuts.create_file_view(file_in_artifact),
                ui_shortcuts.create_file_view_raw(file_in_artifact),
                ui_shortcuts.create_file_download(file_in_artifact),
            )

        ctx["artifact_label"] = self.object.get_label()
        ctx["file_list"] = self.file_list
        ctx["artifact_data"] = format_yaml(self.object.data)

        ctx["forward"] = (
            Artifact.objects.filter(targeted_by__artifact=self.object)
            .annotate(relation_type=F("targeted_by__type"))
            .annotate_complete()
        )
        ctx["reverse_extends"] = (
            Artifact.objects.filter(relations__target=self.object)
            .annotate(relation_type=F("relations__type"))
            .filter(relation_type=ArtifactRelation.Relations.EXTENDS)
            .annotate_complete()
        )
        ctx["relation_count"] = (
            ctx["forward"].count() + ctx["reverse_extends"].count()
        )

        if len(self.file_list) == 1:
            # If there is only one file: display it inline. Otherwise the
            # template will list the files
            ctx["file"] = FileWidget.create(self.file_list[0], file_tag="files")

        ctx["file_count"] = len(self.file_list)

        if plugin_view is not None:
            # Add specific specialized_plugin information
            ctx.update(
                **plugin_view(self.artifact).get_context_data(),
                **{"specialized_view": True},
            )

        return ctx

    def get_template_names(self) -> list[str]:
        """Return the plugin's template_name or the default one."""
        if self._current_view_is_specialized():
            plugin_class = ArtifactPlugin.plugin_for(self.artifact.category)
            assert plugin_class is not None
            return [plugin_class.template_name]

        return [self.template_name]


class FileView(
    PathMixin,
    FileDownloadMixin,
    ArtifactView,
    SingleObjectMixinBase[FileInArtifact],
):
    """Base for all FileInArtifact views."""

    model = FileInArtifact
    object: FileInArtifact

    def get_queryset(self) -> QuerySet[FileInArtifact]:
        """Get the queryset."""
        return (
            super()
            .get_queryset()
            .filter(artifact_id=self.kwargs["artifact_id"])
            .select_related("artifact__workspace")
        )

    def get_object(
        self, queryset: QuerySet[FileInArtifact] | None = None
    ) -> FileInArtifact:
        """Return the FileInArtifact object to show."""
        # Prevent repeated queries: get_artifact calls get_object before
        # dispatch, and Django's BaseDetailView calls get_object in get()
        if not hasattr(self, "object"):
            assert queryset is None
            queryset = self.get_queryset()
            self.object = get_object_or_404(queryset, path=self.kwargs["path"])
        return self.object

    def get_artifact(self) -> Artifact:
        """Artifact for this view."""
        return self.get_object().artifact

    def get_title(self) -> str:
        """Return the page title."""
        title = self.object.path
        if not self.object.complete:
            title += " (incomplete)"
        return title

    def get_main_ui_shortcuts(self) -> list[ui_shortcuts.UIShortcut]:
        """Return a list of UI shortcuts for this view."""
        shortcuts = super().get_main_ui_shortcuts()
        shortcuts.append(ui_shortcuts.create_file_view_raw(self.object))
        shortcuts.append(ui_shortcuts.create_file_download(self.object))
        shortcuts.append(
            ui_shortcuts.create_artifact_view(self.object.artifact)
        )
        return shortcuts

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add the file to display."""
        ctx = super().get_context_data(**kwargs)
        ctx["file"] = FileWidget.create(self.object)
        return ctx


class FileDetailView(FileView, DetailViewBase[FileInArtifact]):
    """Display a file from an artifact."""

    template_name = "web/fileinartifact-detail.html"
    object: FileInArtifact


class FileDetailViewRaw(FileView):
    """Download a file from an artifact, displayed inline."""

    def get(
        self, request: HttpRequest, **kwargs: Any  # noqa: U100
    ) -> HttpResponseBase:
        """Download file with disposition inline."""
        self.object = self.get_object()
        ui_info = FileUI.from_file_in_artifact(self.object)
        return self.stream_file(self.object, ui_info, download=False)


class DownloadFormat(StrEnum):
    """Available download formats."""

    AUTO = "auto"
    TAR_GZ = "tar.gz"


class DownloadPathView(PathMixin, FileDownloadMixin, BaseUIView):
    """View to download an artifact (in .tar.gz or list its files)."""

    permission_denied_message = (
        "Non-public artifact: you might need to login "
        "or make a request with a valid Token header"
    )

    @catch_http_errors
    def dispatch(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Enforce permissions and setup common members."""
        artifact_id = self.kwargs["artifact_id"]
        try:
            self.artifact = Artifact.objects.get(pk=artifact_id)
        except Artifact.DoesNotExist:
            raise Http404(f"Artifact {artifact_id} does not exist")
        self.set_current_workspace(self.artifact.workspace)
        self.enforce(self.artifact.can_display)
        return super().dispatch(request, *args, **kwargs)

    @cached_property
    def download_format(self) -> DownloadFormat:
        """Return the download format."""
        if (archive := self.request.GET.get("archive", None)) is None:
            return DownloadFormat.AUTO
        try:
            return DownloadFormat(archive)
        except ValueError:
            values = ", ".join(e.value for e in DownloadFormat)
            raise HttpError400(
                f"Invalid archive parameter: {archive!r}. Supported: {values}"
            )

    def get(self, request: HttpRequest, **kwargs: Any) -> HttpResponseBase:
        """Download files from the artifact in .tar.gz."""
        if not self.path:
            # Download the whole artifact
            return self._get_artifact()

        try:
            # Try to return a file
            file_in_artifact = self.artifact.fileinartifact_set.get(
                path=self.path.rstrip("/"), complete=True
            )
            return self._get_file(file_in_artifact)
        except FileInArtifact.DoesNotExist:
            # No file exist
            pass

        # Try to return a .tar.gz / list of files for the directory
        directory_exists = self.artifact.fileinartifact_set.filter(
            path__startswith=self.path, complete=True
        ).exists()
        if directory_exists:
            return self._get_directory()

        # Neither a file nor directory existed, HTTP 404
        ctx = {
            "error": f'Artifact {self.artifact.id} does not have any file '
            f'or directory for "{self.kwargs["path"]}"'
        }
        return TemplateResponse(
            request, "404.html", ctx, status=status.HTTP_404_NOT_FOUND
        )

    def _get_artifact(self) -> HttpResponseBase:
        match self.download_format:
            case DownloadFormat.AUTO:
                files_in_artifact = self.artifact.fileinartifact_set.filter(
                    complete=True
                )
                if files_in_artifact.count() == 1:
                    file_in_artifact = files_in_artifact.first()
                    assert file_in_artifact is not None
                    return self._get_file(file_in_artifact)
                else:
                    return self._get_directory_tar_gz()
            case DownloadFormat.TAR_GZ:
                return self._get_directory_tar_gz()
            case _ as unreachable:
                assert_never(unreachable)

    def _get_directory(self) -> HttpResponseBase:
        match self.download_format:
            case DownloadFormat.TAR_GZ:
                return self._get_directory_tar_gz()
            case _:
                raise HttpError400(
                    "archive argument needed when downloading directories"
                )

    def _get_directory_tar_gz(self) -> HttpResponseBase:
        # Currently due to https://code.djangoproject.com/ticket/33735
        # the .tar.gz file is kept in memory by Django (asgi) and the
        # first byte to be sent to the client happens when the .tar.gz has
        # been all generated. When the Django ticket is fixed the .tar.gz
        # will be served as soon as a file is added and the memory usage will
        # be reduced to TarArtifact._chunk_size_mb

        response = StreamingHttpResponse(
            TarArtifact(
                self.artifact,
                None if self.path == "" else self.path,
            ),
            status=status.HTTP_200_OK,
        )
        response["Content-Type"] = "application/octet-stream"

        directory_name = ""
        if self.path != "":
            directory_name = self.path.removesuffix("/")
            directory_name = directory_name.replace("/", "_")
            directory_name = f"-{directory_name}"

        filename = f"artifact-{self.artifact.id}{directory_name}.tar.gz"
        disposition = f'attachment; filename="{filename}"'
        response["Content-Disposition"] = disposition
        response["Last-Modified"] = http_date(
            self.artifact.created_at.timestamp()
        )

        return response

    def _get_file(self, file_in_artifact: FileInArtifact) -> HttpResponseBase:
        """Return a response that streams the_given file."""
        ui_info = FileUI.from_file_in_artifact(file_in_artifact)
        return self.stream_file(file_in_artifact, ui_info, download=True)


class CreateArtifactView(
    CreateViewBase[Artifact, ArtifactForm], WorkspaceView, BaseUIView
):
    """View to create an artifact (uploading files)."""

    template_name = "web/artifact-create.html"
    form_class = ArtifactForm
    title = "Create artifact"

    def init_view(self) -> None:
        """Check can_create_artifacts permission."""
        super().init_view()
        if not context.require_workspace().can_create_artifacts(context.user):
            raise PermissionDenied(
                f"User cannot create artifacts on {context.workspace}"
            )

    def get_form_kwargs(self) -> dict[str, Any]:
        """Extend the default kwarg arguments: add "user"."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["workspace"] = context.require_workspace()
        return kwargs

    def get_success_url(self) -> str:
        """Redirect to the view to see the created artifact."""
        assert self.object is not None
        return self.object.get_absolute_url()

    def form_valid(self, form: ArtifactForm) -> HttpResponse:
        """Save the associated model, then clean up."""
        try:
            return super().form_valid(form)
        finally:
            form.cleanup()

    def form_invalid(self, form: ArtifactForm) -> HttpResponse:
        """Render the invalid form, then clean up."""
        try:
            return super().form_invalid(form)
        finally:
            form.cleanup()


class ArtifactPlugin(abc.ABC):
    """
    Artifacts with specific outputs must subclass it.

    When subclassing, the subclass:
    - Is automatically used by the /artifact/ID/ endpoint
    - Must define "artifact_category" and "template_name"
    - Must implement "get_context_data()"
    """

    model = Artifact
    artifact_category: ArtifactCategory
    template_name: str
    name: str

    _artifact_plugins: dict[str, type["ArtifactPlugin"]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: U100
        """Register the plugin."""
        cls._artifact_plugins[cls.artifact_category] = cls

    def __init__(self, artifact: Artifact) -> None:
        """Store the artifact to display."""
        self.artifact = artifact

    @classmethod
    def plugin_for(
        cls, artifact_category: str
    ) -> type["ArtifactPlugin"] | None:
        """Return ArtifactPlugin for artifact_category or None."""
        return cls._artifact_plugins.get(artifact_category)

    @abc.abstractmethod
    def get_context_data(self) -> dict[str, Any]:
        """Return context data for rendering the artifact view."""
