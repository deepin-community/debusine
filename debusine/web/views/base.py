# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Base infrastructure for web views."""

import abc
import logging
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any, NoReturn, TYPE_CHECKING

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase
from django.template.context import Context
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)
from django.views.generic.base import ContextMixin, View
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import FormMixin
from django.views.generic.list import MultipleObjectMixin

from debusine.artifacts.models import CollectionCategory
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Scope, Workspace
from debusine.db.models.permissions import (
    PermissionUser,
    format_permission_check_error,
)

if TYPE_CHECKING:
    CreateViewBase = CreateView
    DeleteViewBase = DeleteView
    DetailViewBase = DetailView
    ListViewBase = ListView
    UpdateViewBase = UpdateView
    FormMixinBase = FormMixin
    SingleObjectMixinBase = SingleObjectMixin
    MultipleObjectMixinBase = MultipleObjectMixin
else:
    # Django's generic views don't support generic types at run-time yet.
    class _CreateViewBase:
        def __class_getitem__(*args):
            return CreateView

    class _DeleteViewBase:
        def __class_getitem__(*args):
            return DeleteView

    class _DetailViewBase:
        def __class_getitem__(*args):
            return DetailView

    class _ListViewBase:
        def __class_getitem__(*args):
            return ListView

    class _UpdateViewBase:
        def __class_getitem__(*args):
            return UpdateView

    class _FormMixinBase:
        def __class_getitem__(*args):
            return FormMixin

    class _SingleObjectMixinBase:
        def __class_getitem__(*args):
            return SingleObjectMixin

    class _MultipleObjectMixinBase:
        def __class_getitem__(*args):
            return MultipleObjectMixin

    CreateViewBase = _CreateViewBase
    DeleteViewBase = _DeleteViewBase
    DetailViewBase = _DetailViewBase
    ListViewBase = _ListViewBase
    SingleObjectMixinBase = _SingleObjectMixinBase
    MultipleObjectMixinBase = _MultipleObjectMixinBase
    UpdateViewBase = _UpdateViewBase
    FormMixinBase = _FormMixinBase


class Widget(abc.ABC):
    """Base class for template-renderable elements."""

    @abc.abstractmethod
    def render(self, context: Context) -> str:
        """Render the element."""


class BaseUIView(ContextMixin, View):
    """Base class for Debusine web views."""

    base_template = "web/_base.html"
    title = ""

    # If a member is defined for these methods
    http_init_view_method_names = [
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
    ]

    def dispatch(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Call self.init_view when appropriate."""
        assert request.method is not None
        if (
            method := request.method.lower()
        ) in self.http_init_view_method_names:
            if hasattr(self, method):
                self.init_view()
        return super().dispatch(request, *args, **kwargs)

    def init_view(self) -> None:
        """
        Initialize the view.

        Call this method to lookup common objects, or perform permission
        checks.
        """

    def get_title(self) -> str:
        """Get the title for the page."""
        return self.title

    def get_base_template(self) -> str:
        """Return the name of the base template to use with this view."""
        return self.base_template

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """
        Add base template information to the template context.

        Added elements:

        * base_template: name of the base template to load
        * title: string to use as default page title and header
        """
        ctx = super().get_context_data(**kwargs)
        ctx["base_template"] = self.get_base_template()
        ctx["title"] = self.get_title()
        ctx["scopes"] = Scope.objects.can_display(context.user).order_by("name")
        if context.workspace:
            ctx["other_workspaces"] = list(
                Workspace.objects.can_display(context.user)
                .exclude(pk=context.workspace.pk)
                .order_by("name")
            )
            ctx["workspace_collections"] = (
                context.workspace.collections.exclude(
                    category=CollectionCategory.WORKFLOW_INTERNAL
                ).can_display(context.user)
            )
            ctx["workflow_templates"] = list(
                context.workspace.workflowtemplate_set.can_display(
                    context.user
                ).order_by("name")
            )
        try:
            ctx["debusine_version"] = version("debusine")
        except PackageNotFoundError:
            pass
        return ctx

    def _raise_workspace_not_found(
        self, workspace: str | Workspace
    ) -> NoReturn:
        """
        Raise Http404 for a workspace not found.

        This is abstracted to provide consistent error messages also in case of
        permission denied, to prevent leaking the presence of workspaces in
        unaccessible scopes
        """
        if isinstance(workspace, Workspace):
            workspace = workspace.name
        raise Http404(
            f"Workspace {workspace} not found in scope {context.scope}, or "
            f"you are not authorized to see it"
        )

    def set_current_workspace(
        self,
        workspace: str | Workspace,
    ) -> None:
        """Set the current workspace in context."""
        if isinstance(workspace, str):
            try:
                workspace = Workspace.objects.get_for_context(name=workspace)
            except Workspace.DoesNotExist:
                self._raise_workspace_not_found(workspace)
        try:
            workspace.set_current()
        except ContextConsistencyError:
            # Turn exception in a 404 response.
            # 404 is used instead of 403 as an attempt to prevent leaking which
            # private workspace exists that the user cannot see
            logging.debug("permission denied on %s reported as 404", workspace)
            self._raise_workspace_not_found(workspace)

    def enforce(self, predicate: Callable[[PermissionUser], bool]) -> None:
        """Enforce a permission predicate."""
        if predicate(context.user):
            return

        raise PermissionDenied(
            format_permission_check_error(predicate, context.user),
        )


class WorkspaceView(BaseUIView, View):
    """Common structure for views under the /<workspace>/ URL namespace."""

    def init_view(self) -> None:
        """Set the current workspace."""
        super().init_view()
        self.set_current_workspace(self.kwargs["wname"])
