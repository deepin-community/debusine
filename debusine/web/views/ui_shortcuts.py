# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
View extension to add context-dependent UI shortcut widgets.

A UI shortcut is a widget that can be rendered associated to a UI element to
provide a shortcut to commonly used views or actions related to it.
"""

from dataclasses import dataclass

from django.template.backends.utils import csrf_input
from django.template.context import BaseContext
from django.utils.html import format_html

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import (
    Artifact,
    CollectionItem,
    FileInArtifact,
    WorkRequest,
)
from debusine.db.models.artifacts import (
    ARTIFACT_CATEGORY_ICON_NAMES,
    ARTIFACT_CATEGORY_SHORT_NAMES,
)
from debusine.web.icons import Icons
from debusine.web.views.base import Widget


@dataclass(kw_only=True)
class UIShortcut(Widget):
    """Renderable UI shortcut."""

    #: User-readable label
    label: str
    #: Icon (name in the Bootstrap icon set, without the leading "bi-")
    icon: str
    #: Target URL for the action
    url: str

    def render(self, context: BaseContext) -> str:  # noqa: U100
        """Render the shortcut as an <a> button."""
        return format_html(
            "<a class='btn btn-outline-secondary'"
            " href='{url}' title='{label}'>"
            "<span class='bi bi-{icon}'></span>"
            "</a>",
            label=self.label,
            icon=self.icon,
            url=self.url,
        )


class UIShortcutPOST(UIShortcut):
    """UI shortcut that triggers a POST."""

    def render(self, context: BaseContext) -> str:
        """Render the shortcut as a form."""
        return format_html(
            "<form method='post' action='{url}'>{csrf}"
            "<button type='submit' class='btn btn-primary bi bi-{icon}'"
            " title='{label}'></button>"
            "</form>",
            csrf=csrf_input(context["request"]),
            label=self.label,
            icon=self.icon,
            url=self.url,
        )


def create_work_request_view(work_request: WorkRequest) -> UIShortcut:
    """Create a shortcut to view a work request."""
    return UIShortcut(
        label="View work request",
        icon=Icons.WORK_REQUEST,
        url=work_request.get_absolute_url(),
    )


def create_work_request_retry(work_request: WorkRequest) -> "UIShortcut":
    """Create a shortcut to retry a work request."""
    return UIShortcutPOST(
        label="Retry work request",
        icon=Icons.WORK_REQUEST_RETRY,
        url=work_request.get_absolute_url_retry(),
    )


def create_artifact_view(artifact: Artifact) -> UIShortcut:
    """Create a shortcut to view an artifact."""
    category = ArtifactCategory(artifact.category)
    short_name = ARTIFACT_CATEGORY_SHORT_NAMES.get(category, "artifact")
    return UIShortcut(
        label=f"View {short_name} artifact",
        icon=ARTIFACT_CATEGORY_ICON_NAMES.get(category, "folder"),
        url=artifact.get_absolute_url(),
    )


def create_file_view(file_in_artifact: FileInArtifact) -> UIShortcut:
    """Create a shortcut to view a file."""
    return UIShortcut(
        label=f"View {file_in_artifact.path}",
        icon=Icons.FILE_VIEW,
        url=file_in_artifact.get_absolute_url(),
    )


def create_file_view_raw(file_in_artifact: FileInArtifact) -> UIShortcut:
    """Create a shortcut to stream a file inline."""
    return UIShortcut(
        label=f"View {file_in_artifact.path} raw",
        icon=Icons.FILE_VIEW_RAW,
        url=file_in_artifact.get_absolute_url_raw(),
    )


def create_file_download(file_in_artifact: FileInArtifact) -> UIShortcut:
    """Create a shortcut to download a file."""
    return UIShortcut(
        label=f"Download {file_in_artifact.path}",
        icon=Icons.FILE_DOWNLOAD,
        url=file_in_artifact.get_absolute_url_download(),
    )


def create_artifact_download(artifact: Artifact) -> UIShortcut:
    """Create a shortcut to download an artifact."""
    return UIShortcut(
        label="Download artifact",
        icon=Icons.ARTIFACT_DOWNLOAD,
        url=artifact.get_absolute_url_download(),
    )


def create_collection_item(item: CollectionItem) -> UIShortcut:
    """Create a shortcut to view collection item details."""
    return UIShortcut(
        label="Collection item details",
        icon=Icons.COLLECTION_ITEM_DETAILS,
        url=item.get_absolute_url(),
    )
