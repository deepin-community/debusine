# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
View extension to model sidebar elements.

Sidebar elements are informational elements or actions that can be used to
populate the UI sidebar.
"""

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

from django.db.models import Model
from django.template.context import BaseContext
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.timesince import timesince

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import (
    Artifact,
    Collection,
    User,
    WorkRequest,
    Worker,
    Workspace,
)
from debusine.db.models.artifacts import ARTIFACT_CATEGORY_SHORT_NAMES
from debusine.web.icons import Icons
from debusine.web.views.base import Widget


@dataclass(kw_only=True, frozen=True)
class SidebarItem(Widget):
    """Renderable element for the right sidebar."""

    #: icon to use
    icon: str = "app"
    #: label describing what the field is about
    label: str
    #: user-presented value for the field
    value: str | None = None
    #: detailed information about the value
    detail: str | None = None

    @cached_property
    def content(self) -> str:
        """Select the main content to display."""
        if self.value:
            return self.value
        if self.detail:
            return self.detail
        return "-"

    @cached_property
    def main_tooltip(self) -> str:
        """Select what to use as tooltip for the whole entry."""
        if self.detail and self.value and self.detail != self.value:
            return self.detail
        return self.label

    @cached_property
    def icon_tooltip(self) -> str | None:
        """Select if a tooltip is shown on the icon, and what it is."""
        if self.main_tooltip == self.label:
            return None
        return self.label

    def render(self, context: BaseContext) -> str:
        """Render this item."""
        raise NotImplementedError(
            f"{self.__class__.__name__}.render not implemented"
        )


@dataclass(kw_only=True, frozen=True)
class SidebarElement(SidebarItem):
    """Informative element for the right sidebar."""

    def render(self, context: BaseContext) -> str:  # noqa: U100
        """Render this item."""
        if self.icon_tooltip:
            icon_title = format_html(' title="{}"', self.icon_tooltip)
        else:
            icon_title = SafeString()
        return format_html(
            '<span class="list-group-item" title="{main_tooltip}">'
            '<span class="bi bi-{icon}"{icon_title}></span> '
            '{content}</span>',
            main_tooltip=self.main_tooltip,
            icon_title=icon_title,
            icon=self.icon,
            content=self.content,
        )


@dataclass(kw_only=True, frozen=True)
class SidebarAction(SidebarItem):
    """Action element for the right sidebar."""

    # Action URL
    url: str

    def render(self, context: BaseContext) -> str:  # noqa: U100
        """Render this item."""
        if self.icon_tooltip:
            icon_title = format_html(' title="{}"', self.icon_tooltip)
        else:
            icon_title = SafeString()
        return format_html(
            '<a class="list-group-item list-group-item-action'
            ' d-flex justify-content-between"'
            ' title="{main_tooltip}" href="{url}">'
            '<span><span class="bi bi-{icon}"{icon_title}></span> '
            '{content}</span>'
            '<span>…</span>'
            '</a>',
            main_tooltip=self.main_tooltip,
            icon_title=icon_title,
            url=self.url,
            icon=self.icon,
            content=self.content,
        )


def create_workspace(workspace: Workspace) -> SidebarItem:
    """Create workspace information."""
    return SidebarAction(
        url=workspace.get_absolute_url(),
        icon="person-workspace",
        label="Workspace",
        value=workspace.name,
    )


def create_work_request(
    work_request: WorkRequest | None, *, link: bool = False
) -> SidebarItem:
    """Create work request information."""
    kwargs = {
        "icon": Icons.WORK_REQUEST,
        "label": "Work request",
    }

    if work_request is None:
        return SidebarElement(**kwargs)

    kwargs["detail"] = f"{work_request.task_type} {work_request.task_name} task"
    kwargs["value"] = work_request.get_label()

    if not link:
        return SidebarElement(**kwargs)

    return SidebarAction(
        url=work_request.get_absolute_url(),
        **kwargs,
    )


def create_user(
    user: User | None, *, context: Model | None = None
) -> SidebarItem:
    """Create user information."""
    match context:
        case WorkRequest():
            title = "User who created the work request"
        case Artifact():
            title = "User who created the artifact"
        case _:
            title = "User"

    return SidebarElement(
        icon=Icons.USER,
        label=title,
        value=str(user) if user else None,
    )


def create_created_at(at: datetime) -> SidebarItem:
    """Create created_at information."""
    return SidebarElement(
        icon=Icons.CREATED_AT,
        label="Created",
        value=timesince(at) + " ago",
        detail=date_format(at, "DATETIME_FORMAT"),
    )


def create_expire_at(at: datetime | None) -> SidebarItem:
    """Create expire_at information."""
    kwargs = {
        "icon": Icons.EXPIRE_AT,
        "label": "Expiration date",
    }
    if at:
        return SidebarElement(
            detail=date_format(at, "DATETIME_FORMAT"),
            value=timesince(at),
            **kwargs,
        )
    else:
        return SidebarElement(value="never", **kwargs)


def create_work_request_status(work_request: WorkRequest) -> SidebarItem:
    """Create information about a work request status and result."""
    kwargs = {
        "icon": Icons.WORK_REQUEST_STATUS,
        "label": "Status and result",
    }

    match work_request.status:
        case (
            WorkRequest.Statuses.PENDING
            | WorkRequest.Statuses.RUNNING
            | WorkRequest.Statuses.BLOCKED
        ):
            status_bg = "secondary"
        case WorkRequest.Statuses.COMPLETED:
            status_bg = "primary"
        case WorkRequest.Statuses.ABORTED:
            status_bg = "dark"
        case _ as unreachable:  # noqa: F841
            status_bg = "info"

    status = format_html(
        '<span class="badge text-bg-{bg}">{status}</span>',
        bg=status_bg,
        status=work_request.status.capitalize(),
    )

    result_bg: str | None
    match work_request.result:
        case WorkRequest.Results.NONE:
            result_bg = None
        case WorkRequest.Results.SUCCESS:
            result_bg = "success"
        case WorkRequest.Results.FAILURE:
            result_bg = "warning"
        case WorkRequest.Results.ERROR:
            result_bg = "danger"
        case _ as unreachable:  # noqa: F841
            result_bg = "info"

    if result_bg is None:
        kwargs["value"] = status
    else:
        kwargs["value"] = status + format_html(
            ' <span class="badge text-bg-{bg}">{result}</span>',
            bg=result_bg,
            result=work_request.result.capitalize(),
        )

    return SidebarElement(**kwargs)


def create_work_request_superseded(work_request: WorkRequest) -> SidebarItem:
    """Create a link to the work request that supersedes this one."""
    kwargs = {
        "icon": Icons.WORK_REQUEST_SUPERSEDED,
        "label": "Superseded by",
    }
    if (superseded := getattr(work_request, "superseded", None)) is None:
        return SidebarElement(**kwargs)
    return SidebarAction(
        url=superseded.get_absolute_url(),
        value=str(superseded),
        **kwargs,
    )


def create_work_request_supersedes(work_request: WorkRequest) -> SidebarItem:
    """Create a link to the work request superseded by this one."""
    kwargs = {
        "icon": Icons.WORK_REQUEST_SUPERSEDES,
        "label": "Supersedes",
    }
    if (supersedes := getattr(work_request, "supersedes", None)) is None:
        return SidebarElement(**kwargs)

    return SidebarAction(
        url=supersedes.get_absolute_url(),
        value=str(supersedes),
        **kwargs,
    )


def create_workflow(workflow: WorkRequest | None) -> SidebarItem:
    """Create a link to the associated workflow."""
    if workflow is None:
        return SidebarElement(
            icon=Icons.WORKFLOW,
            label="Workflow",
        )

    return SidebarAction(
        icon="diagram-3",
        label="Workflow",
        url=workflow.get_absolute_url(),
        value=str(workflow),
    )


def create_worker(worker: Worker | None) -> SidebarItem:
    """Create a link to a worker."""
    kwargs = {
        "icon": Icons.WORKER,
        "label": "Worker",
    }
    if worker is None:
        return SidebarElement(**kwargs)
    return SidebarAction(
        value=worker.name, url=worker.get_absolute_url(), **kwargs
    )


def create_work_request_started_at(work_request: WorkRequest) -> SidebarItem:
    """Show the time the work request spent in a queue before being started."""
    kwargs = {
        "icon": Icons.STARTED_AT,
        "label": "Time from creation to start",
    }
    if not work_request.started_at:
        return SidebarElement(**kwargs)

    return SidebarElement(
        detail=date_format(work_request.started_at, "DATETIME_FORMAT"),
        value=timesince(work_request.created_at, work_request.started_at),
        **kwargs,
    )


def create_work_request_duration(work_request: WorkRequest) -> SidebarItem:
    """Show the duration of the work request run."""
    kwargs = {
        "icon": Icons.DURATION,
        "label": "Duration",
    }

    if (
        work_request.started_at
        and work_request.status == WorkRequest.Statuses.RUNNING
    ):
        return SidebarElement(
            value=timesince(work_request.started_at),
            **kwargs,
        )

    if not work_request.started_at or not work_request.completed_at:
        return SidebarElement(**kwargs)

    return SidebarElement(
        value=timesince(work_request.started_at, work_request.completed_at),
        detail=date_format(work_request.completed_at, "DATETIME_FORMAT"),
        **kwargs,
    )


def create_artifact_category(artifact: Artifact) -> SidebarItem:
    """Show an artifact's category."""
    return SidebarElement(
        icon=Icons.CATEGORY,
        label="Artifact category",
        detail=artifact.category,
        value=ARTIFACT_CATEGORY_SHORT_NAMES.get(
            ArtifactCategory(artifact.category), "artifact"
        ),
    )


def create_collection(collection: Collection) -> SidebarItem:
    """Link to a collection."""
    return SidebarAction(
        url=collection.get_absolute_url(),
        icon=Icons.COLLECTION,
        label="Collection",
        detail=f"{collection.name}@{collection.category}",
        value=collection.name,
    )
