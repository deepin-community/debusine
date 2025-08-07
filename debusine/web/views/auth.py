# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine auth views."""

from collections import defaultdict
from functools import cached_property
from typing import Any, cast

from django.contrib.auth import views as auth_views
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from debusine.db.context import context
from debusine.db.models import Group, Scope, Token, User
from debusine.db.models.auth import (
    GroupAuditLog,
    GroupMembership,
    GroupQuerySet,
    Identity,
    UserQuerySet,
)
from debusine.server.signon.providers import Provider
from debusine.server.signon.signon import Signon
from debusine.server.signon.views import SignonLogoutMixin
from debusine.web.forms import GroupAddUserForm, ModelFormBase, TokenForm
from debusine.web.views.base import (
    BaseUIView,
    CreateViewBase,
    DeleteViewBase,
    DetailViewBase,
    FormMixinBase,
    ListViewBase,
    SingleObjectMixinBase,
    UpdateViewBase,
)
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import (
    GroupAuditLogTable,
    GroupMembershipAdminTable,
    GroupMembershipTable,
)


class LoginView(BaseUIView, auth_views.LoginView):
    """Class for the login view."""

    template_name = "account/login.html"
    title = "Log in"

    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Store the ?next argument in the session."""
        request.session["sso_callback_next_url"] = self.get_success_url()
        return super().get(request, *args, **kwargs)

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Tell the base template we are a login view."""
        ctx = super().get_context_data(**kwargs)
        ctx["is_login_view"] = True
        return ctx


class LogoutView(
    auth_views.LogoutView,
    SignonLogoutMixin,
    BaseUIView,
):
    """Class for the logout view."""

    template_name = "account/logged_out.html"
    title = "Logged out"

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Tell the base template we are a logout view."""
        ctx = super().get_context_data(**kwargs)
        ctx["is_logout_view"] = True
        return ctx


class UserDetailView(BaseUIView, DetailViewBase[User]):
    """Show user details."""

    model = User
    template_name = "web/user-detail.html"
    # We need something that is not "user" not to confuse it with the
    # current user
    context_object_name = "person"

    def get_title(self) -> str:
        """Return the page title."""
        return self.object.get_full_name()

    def get_queryset(self) -> QuerySet[User]:
        """All workspaces for authenticated users or only public ones."""
        queryset = cast(UserQuerySet[Any], super().get_queryset())
        return queryset.can_display(context.user)

    def get_object(self, queryset: QuerySet[User] | None = None) -> User:
        """Return the collection object to show."""
        assert queryset is None
        queryset = self.get_queryset()
        return get_object_or_404(queryset, username=self.kwargs["username"])

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add user information to context data."""
        ctx = super().get_context_data(**kwargs)

        # Groups by scope
        by_group: dict[Scope, list[Group]] = defaultdict(list)
        for group in self.object.debusine_groups.all():
            by_group[group.scope].append(group)
        ctx["groups_by_scope"] = sorted(
            (
                (scope, sorted(groups, key=lambda g: g.name))
                for scope, groups in by_group.items()
            ),
            key=lambda s: s[0].name,
        )

        if self.object.can_manage(context.user):
            # SSO identities
            signon: Signon | None = getattr(self.request, "signon", None)
            if signon is not None:
                identities: list[tuple[Provider, Identity]] = []
                for identity in self.object.identities.all():
                    if (
                        provider := signon.get_provider_for_identity(identity)
                    ) is None:
                        continue
                    identities.append((provider, identity))
                ctx["identities"] = identities

        return ctx


class TokenView(BaseUIView):
    """Base view for token management."""

    @cached_property
    def user(self) -> User:
        """Return the user for which we manage tokens."""
        user = get_object_or_404(
            User.objects.can_display(context.user),
            username=self.kwargs["username"],
        )
        self.enforce(user.can_manage)
        return user

    def get_success_url(self) -> str:
        """Redirect to the view to see the created artifact."""
        return reverse(
            "user:token-list", kwargs={"username": self.user.username}
        )

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add the currently managed user to the template context."""
        ctx = super().get_context_data(**kwargs)
        ctx["person"] = self.user
        return ctx


class UserTokenListView(TokenView, ListViewBase[Token]):
    """List tokens for the user."""

    model = Token
    template_name = "web/user_token-list.html"
    context_object_name = "token_list"
    ordering = "name"
    title = "API tokens"

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add bits needed by the template."""
        ctx = super().get_context_data(**kwargs)
        ctx["only_one_scope"] = len(ctx["scopes"]) == 1
        return ctx

    def get_queryset(self) -> QuerySet[Token]:
        """All tokens for the authenticated user."""
        return Token.objects.filter(user=self.user).order_by('created_at')


class TokenFormView(TokenView, FormMixinBase[TokenForm]):
    """Mixin for views using TokenForm."""

    form_class = TokenForm
    action: str

    def get_title(self) -> str:
        """Get the default title for the view."""
        return f"{self.action} token"

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add action name to context data."""
        ctx = super().get_context_data(**kwargs)
        ctx["action"] = self.action
        return ctx

    def get_form_kwargs(self) -> dict[str, Any]:
        """Extend the default kwarg arguments: add "user"."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.user
        return kwargs


class UserTokenCreateView(TokenFormView, CreateViewBase[Token, TokenForm]):
    """Form view for creating tokens."""

    template_name = "web/user_token-form.html"
    action = "Create"
    context_object_name = "token"

    def get_form_kwargs(self) -> dict[str, Any]:
        """Extend the default kwarg arguments: add "user"."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.user
        return kwargs

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add the user object from URL kwargs."""
        ctx = super().get_context_data(**kwargs)
        ctx["person"] = self.user
        return ctx

    def get_title(self) -> str:
        """Switch title if we have a newly created token."""
        if self.object:
            return "Token created"
        else:
            return super().get_title()

    def get_template_names(self) -> list[str]:
        """Switch template if we have a newly created token."""
        if self.object:
            return ["web/user_token-created.html"]
        else:
            return [self.template_name]

    def form_valid(self, form: TokenForm) -> HttpResponse:
        """Validate form and return token created page."""
        # FIXME: a successful form submission should not render a page and
        # should redirect to another page instead, to avoid unintended
        # behaviour if the user triggers a reload. See:
        # https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Redirections#temporary_responses_to_unsafe_requests # noqa: E501
        #
        # However, here we lose the token key after we send a response, so to
        # avoid rendering a page to show it here, we need to store it somewhere
        # the target of the redirect can find it.
        #
        # That would introduce further issues in managing the security and the
        # lifetime of that storage, and the worst that can happen with a reload
        # here is the generation of another token, which seems like the lesser
        # risk for now.
        self.object = form.save()
        return self.render_to_response(self.get_context_data(**self.kwargs))


class UserTokenUpdateView(TokenFormView, UpdateViewBase[Token, TokenForm]):
    """Form view for creating tokens."""

    model = Token
    template_name = "web/user_token-form.html"
    action = "Edit"

    def get_queryset(self) -> QuerySet[Token]:
        """Only include tokens for the current user."""
        return super().get_queryset().filter(user=self.user)


class UserTokenDeleteView(
    TokenView, DeleteViewBase[Token, ModelFormBase[Token]]
):
    """View for deleting tokens."""

    object: Token  # https://github.com/typeddjango/django-stubs/issues/1227
    model = Token
    template_name = "web/user_token-confirm_delete.html"
    title = "Delete token"

    def get_queryset(self) -> QuerySet[Token]:
        """Only include tokens for the current user."""
        return super().get_queryset().filter(user=self.user)


class UserGroupListView(ListViewBase[GroupMembership], BaseUIView):
    """List groups for a user."""

    model = GroupMembership
    template_name = "web/user_group-list.html"
    context_object_name = "membership_list"
    ordering = "group__name"

    def get_title(self) -> str:
        """Return the page title."""
        return f"{self.user} group list in scope {context.scope}"

    @cached_property
    def user(self) -> User:
        """Return the user for which we list/manage groups."""
        user = get_object_or_404(
            User.objects.all(),
            username=self.kwargs["username"],
        )
        self.enforce(user.can_display)
        return user

    def get_queryset(self) -> QuerySet[GroupMembership]:
        """All groups for the authenticated user."""
        return GroupMembership.objects.filter(
            group__in=Group.objects.can_display(context.user),
            group__scope=context.require_scope(),
            user=self.user,
        ).select_related("group", "user")

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add bits needed by the template."""
        ctx = super().get_context_data(**kwargs)
        ctx["current_user"] = context.user == self.user
        ctx["person"] = self.user
        return ctx


class GroupDetailView(
    FormMixinBase[GroupAddUserForm],
    DetailViewBase[Group],
    BaseUIView,
):
    """List groups for a user."""

    model = Group
    form_class = GroupAddUserForm
    template_name = "web/group-detail.html"
    context_object_name = "group"

    def get_title(self) -> str:
        """Return the page title."""
        return f"Group {self.object}"

    def get_object(
        self, queryset: QuerySet[Group, Group] | None = None
    ) -> Group:
        """Get the Group to display."""
        assert queryset is None
        queryset = self.get_queryset()
        group = get_object_or_404(
            cast(GroupQuerySet[Any], queryset).can_display(
                context.require_user()
            ),
            scope=context.require_scope(),
            name=self.kwargs["group"],
        )
        return cast(Group, group)

    def get_form_kwargs(self) -> dict[str, Any]:
        """Extend the default kwarg arguments: add "user"."""
        kwargs = super().get_form_kwargs()
        kwargs["group"] = self.object
        return kwargs

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add bits needed by the template."""
        assert context.user and context.user.is_authenticated
        ctx = super().get_context_data(
            asc=self.request.GET.get("asc", "1"),
            order=self.request.GET.get("order"),
            **kwargs,
        )

        ctx["can_manage"] = can_manage = self.object.can_manage(
            context.require_user()
        )

        table_class = (
            GroupMembershipAdminTable if can_manage else GroupMembershipTable
        )
        ctx["members"] = table_class(
            request=self.request,
            object_list=GroupMembership.objects.filter(
                group=self.object
            ).select_related("user"),
        ).get_paginator(per_page=10)

        audit_log = self.object.audit_log.order_by(
            "-created_at"
        ).select_related("actor")[:6]
        ctx["audit_log"] = audit_log[:5]
        ctx["audit_log_more"] = len(audit_log) == 6
        try:
            ctx["role"] = GroupMembership.objects.get(
                group=self.object, user=context.user
            ).role
        except GroupMembership.DoesNotExist:
            pass

        return ctx

    # Adapted from BaseDeleteView, since ProcessFormView does not set
    # self.object
    def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any  # noqa: U100
    ) -> HttpResponse:
        """Handle POST requests."""
        self.object = self.get_object()
        self.enforce(self.object.can_manage)
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def form_valid(self, form: GroupAddUserForm) -> HttpResponse:
        """Validate form and return token created page."""
        username = form.cleaned_data["username"]
        user = User.objects.get(username=username)
        self.object.add_user(user)
        return super().form_valid(form)

    def get_success_url(self) -> str:
        """Redirect to the view to see the created artifact."""
        return self.object.get_absolute_url()


class MembershipView(SingleObjectMixinBase[GroupMembership], BaseUIView):
    """Base view for group management for a user."""

    def get_object(
        self, queryset: QuerySet[GroupMembership, GroupMembership] | None = None
    ) -> GroupMembership:
        """Get the GroupMembership to display."""
        assert queryset is None
        queryset = self.get_queryset()
        membership = get_object_or_404(
            queryset.filter(
                group__in=Group.objects.can_manage(context.require_user()),
                group__scope=context.require_scope(),
                group__name=self.kwargs["group"],
                user__username=self.kwargs["user"],
            ).select_related("group", "user"),
        )
        self.enforce(membership.group.can_manage)
        return membership

    def get_context_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Add bits needed by the template."""
        ctx = super().get_context_data(**kwargs)
        # FIXME: Django defines self.object differently in separate
        # incompatible view classes, and I could not find a way to play along
        # well with that
        ctx["person"] = self.object.user  # type: ignore[attr-defined]
        ctx["group"] = self.object.group  # type: ignore[attr-defined]
        return ctx


class MembershipUpdateView(
    MembershipView,
    UpdateViewBase[GroupMembership, ModelFormBase[GroupMembership]],
):
    """Edit a user's membership."""

    model = GroupMembership
    fields = ["role"]
    template_name = "web/membership-edit.html"
    title = "Update role"

    def form_valid(self, form: ModelFormBase[GroupMembership]) -> HttpResponse:
        """Change the member's role."""
        self.object = self.object.group.set_user_role(
            self.object.user, form.cleaned_data["role"]
        )
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self) -> str:
        """Redirect to the view to see the created artifact."""
        return self.object.group.get_absolute_url()


class MembershipDeleteView(
    MembershipView,
    DeleteViewBase[GroupMembership, ModelFormBase[GroupMembership]],
):
    """View for removing a user from a group."""

    # https://github.com/typeddjango/django-stubs/issues/1227
    object: GroupMembership
    model = GroupMembership
    template_name = "web/membership-confirm_delete.html"

    def get_title(self) -> str:
        """Return the page title."""
        return f"Remove user from {self.object.group}"

    def form_valid(
        self, form: ModelFormBase[GroupMembership]  # noqa: U100
    ) -> HttpResponse:
        """Remove the group member."""
        success_url = self.get_success_url()
        self.object.group.remove_user(self.object.user)
        return HttpResponseRedirect(success_url)

    def get_success_url(self) -> str:
        """Redirect to the view to see the created artifact."""
        return self.object.group.get_absolute_url()


class GroupAuditLogView(
    TableMixin[GroupAuditLog], ListViewBase[GroupAuditLog], BaseUIView
):
    """View for showing the full group audit log."""

    model = GroupAuditLog
    template_name = "web/group_audit_log-list.html"
    context_object_name = "audit_log"
    table_class = GroupAuditLogTable
    paginate_by = 10

    @cached_property
    def group(self) -> Group:
        """Return the group for the audit log."""
        return cast(
            Group,
            get_object_or_404(
                cast(
                    GroupQuerySet[Any],
                    Group.objects.can_display_audit_log(
                        context.require_user()
                    ).filter(scope=context.require_scope()),
                ),
                name=self.kwargs["group"],
            ),
        )

    def get_queryset(self) -> QuerySet[GroupAuditLog]:
        """All audit log entries for the current group."""
        return self.group.audit_log.select_related("actor")

    def get_title(self) -> str:
        """Return the page title."""
        return f"Audit log for {self.group}"
