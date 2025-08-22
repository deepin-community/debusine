# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Template tag library to render Debusine UI elements."""

import logging
from datetime import datetime, timezone
from typing import Any, TypeVar

from django import template
from django.conf import settings
from django.db.models import Model
from django.template import Context, Node, NodeList, TemplateSyntaxError
from django.template.base import FilterExpression, Parser, Token
from django.template.loader import render_to_string
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import SafeString

import debusine.web.views.view_utils
from debusine.db.context import context
from debusine.db.models.permissions import PermissionUser
from debusine.server.scopes import urlconf_scope
from debusine.web.helps import HELPS
from debusine.web.icons import Icons
from debusine.web.views.base import Widget
from debusine.web.views.ui.base import UI
from debusine.web.views.ui_shortcuts import UIShortcut

logger = logging.getLogger("debusine.web")

register = template.Library()


@register.simple_tag
def ui_shortcuts(obj: Model) -> list[UIShortcut]:
    """Return the stored UI shortcuts for the given object."""
    stored = getattr(obj, "_ui_shortcuts", None)
    if stored is None:
        return []
    # Always set by debusine.web.views.base_rightbar.RightbarUIView, which
    # has a type annotation ensuring that this is always a list of
    # UIShortcut instances.
    assert isinstance(stored, list)
    return stored


class WidgetNode(Node):
    """Template node implementing the {% widget %} tag."""

    def __init__(self, value: FilterExpression) -> None:
        """Store node arguments."""
        self.value = value

    def _render_error(
        self,
        user_message: str,
        devel_message: str,
        exception: Exception | None = None,
    ) -> SafeString:
        """
        Render a description for a rendering error.

        Depending on settings, it can either raise an exception or render a
        description suitable for template output.
        """
        if settings.DEBUG or getattr(settings, "TEST_MODE", False):
            if exception is not None:
                exception.add_note(devel_message)
                raise exception
            else:
                raise ValueError(devel_message)

        # When running in production, avoid leaking possibly sensitive error
        # information while providing enough information for a potential bug
        # report to locate the stack trace in the logs
        logger.warning(
            "Widget rendering error: %s", devel_message, exc_info=exception
        )
        return format_html(
            "<span data-role='debusine-widget-error'"
            " class='bg-danger text-white'>{ts} UTC: {message}</span>",
            ts=datetime.now(timezone.utc).isoformat(),
            message=user_message,
        )

    def render(self, context: Context) -> str | SafeString:
        """Render the template node."""
        # Resolve the argument token to a value
        try:
            # ignore_failures seems counterintuitive to me.
            # If False, then django will *handle* failures, generating a value
            # using `string_if_invalid` configured in the template `Engine`.
            # If True, then django will *not* handle failures, and use None for
            # an undefined variable, which is something that we can look for.
            value: str | Widget | None = self.value.resolve(
                context, ignore_failures=True
            )
        except Exception as e:
            return self._render_error(
                user_message="widget argument malformed",
                devel_message=f"Invalid widget argument: {self.value!r}",
                exception=e,
            )

        # Validate the value as a widget
        match value:
            case None:
                return self._render_error(
                    user_message="invalid or undefined widget value",
                    devel_message=f"widget {self.value!r} resolved to None",
                )
            case str():
                if context.autoescape:
                    return conditional_escape(value)
                else:
                    return value
            case Widget():
                try:
                    return value.render(context)
                except Exception as e:
                    return self._render_error(
                        user_message=(
                            f"{value.__class__.__name__} failed to render"
                        ),
                        devel_message=(
                            f"Widget {self.value!r} ({value!r})"
                            " failed to render"
                        ),
                        exception=e,
                    )
            case _:
                return self._render_error(
                    user_message="invalid widget type",
                    devel_message=(
                        f"widget {self.value!r} {value!r} has invalid type"
                    ),
                )


@register.tag
def widget(parser: Parser, token: Token) -> WidgetNode:
    """Parser for the {% widget %} tag."""
    bits = token.split_contents()
    if len(bits) == 2:
        tag, value = bits
    else:
        raise TemplateSyntaxError("{% widget %} requires exactly one argument")
    return WidgetNode(parser.compile_filter(value))


class WithScopeNode(Node):
    """Template node implementing the {% withscope %} tag."""

    def __init__(self, value: FilterExpression, nodelist: NodeList) -> None:
        """Store node arguments."""
        self.value = value
        self.nodelist = nodelist

    def render(self, context: Context) -> SafeString:
        """Render the template node."""
        from debusine.db.context import context as appcontext
        from debusine.db.models import Scope

        # Resolve the argument token to a value
        value: str | Scope = self.value.resolve(context)

        # Resolve a scope name to a Scope object
        match value:
            case str():
                try:
                    value = Scope.objects.get(name=value)
                except Scope.DoesNotExist:
                    return self.nodelist.render(context)
            case Scope():
                pass
            case _:
                return self.nodelist.render(context)

        user = appcontext.user
        assert user is not None
        with urlconf_scope(value.name), appcontext.local():
            # Render the contained template using a different current scope
            appcontext.reset()
            appcontext.set_scope(value)
            # Note: this triggers an extra database query, since set_user needs
            # to go and load what roles the user has on the scope.
            # TODO: if this is a problem in big querysets, the same can be done
            #       with Scope as with Workspace.objects.with_role_annotations
            appcontext.set_user(user)
            # Workspace is not preserved, since the scope changed
            # worker_token is not preserved since it makes no sense for
            #              templates
            # TODO: permission_checks_disabled is not currently preserved as
            #       there is no use case to justify it
            return self.nodelist.render(context)


@register.tag
def withscope(parser: Parser, token: Token) -> WithScopeNode:
    """Parser for the {% withscope %} tag."""
    bits = token.split_contents()
    if len(bits) == 2:
        tag, value = bits
    else:
        raise TemplateSyntaxError("withscope requires exactly one argument")

    nodelist = parser.parse(("endwithscope",))
    parser.delete_first_token()

    return WithScopeNode(parser.compile_filter(value), nodelist)


@register.filter
def has_perm(resource: Model, name: str) -> bool:
    """Check a permission predicate."""
    predicate = getattr(resource, name)
    result = predicate(context.user)
    assert isinstance(result, bool)
    return result


@register.filter
def roles(resource: Model, user: PermissionUser = None) -> list[str]:
    """Return the list of roles the user has on the resource."""
    if (get_roles := getattr(resource, "get_roles", None)) is None:
        return []
    return sorted(get_roles(user or context.user))


@register.filter
def format_yaml(value: Any, flags: str = "") -> str:
    """Format a data structure as YAML."""
    sort_keys = "unsorted" not in flags.split(",")
    return debusine.web.views.view_utils.format_yaml(value, sort_keys=sort_keys)


@register.simple_tag
def icon(name: str) -> str:
    """Lookup an icon by name."""
    return "bi-" + getattr(Icons, name.upper(), "square-fill")


@register.filter(name="sorted")
def sorted_(value: Any) -> Any:
    """Sort a sequence of values."""
    return sorted(value)


@register.filter
def is_list_like(value: Any) -> bool:
    """Check if a value is list-like."""
    return isinstance(value, (list, tuple))


@register.filter
def is_dict_like(value: Any) -> bool:
    """Check if a value is dict-like."""
    return isinstance(value, dict)


_KT = TypeVar("_KT")
_VT = TypeVar("_VT")


# https://code.djangoproject.com/ticket/12486
@register.filter
def lookup(d: dict[_KT, _VT], key: _KT) -> _VT:
    """Get a value from a dictionary."""
    return d[key]


@register.simple_tag(name="help")
def _help(name: str) -> str:
    """Return HTML with a "?" icon and a popover with the html."""
    return render_to_string("web/_help.html", HELPS[name]._asdict())


class UINode(Node):
    """Template node implementing the {% ui %} tag."""

    def __init__(self, objvar: FilterExpression, asvar: str) -> None:
        """Store node arguments."""
        self.objvar = objvar
        self.asvar = asvar

    def _lookup_error(
        self,
        devel_message: str,
        exception: Exception | None = None,
    ) -> None:
        """
        Render a description for a rendering error.

        Depending on settings, it can either raise an exception or render a
        description suitable for template output.
        """
        if settings.DEBUG or getattr(settings, "TEST_MODE", False):
            if exception is not None:
                exception.add_note(devel_message)
                raise exception
            else:
                raise ValueError(devel_message)

        # When running in production, avoid leaking possibly sensitive error
        # information while providing enough information for a potential bug
        # report to locate the stack trace in the logs
        logger.warning(
            "UI helper lookup failed: %s", devel_message, exc_info=exception
        )

    def render(self, context: Context) -> SafeString:
        """Lookup the helper and store it in the context."""
        try:
            # ignore_failures seems counterintuitive to me.
            # If False, then django will *handle* failures, generating a value
            # using `string_if_invalid` configured in the template `Engine`.
            # If True, then django will *not* handle failures, and use None for
            # an undefined variable, which is something that we can look for.
            instance: Model | None = self.objvar.resolve(
                context, ignore_failures=True
            )
        except Exception as e:
            self._lookup_error(
                f"Invalid ui argument: {self.objvar!r}",
                exception=e,
            )
            context[self.asvar] = None
            return SafeString()

        match instance:
            case None:
                self._lookup_error(
                    f"ui model instance {self.objvar!r} resolved to None"
                )
                context[self.asvar] = None
            case Model():
                try:
                    context[self.asvar] = UI.for_instance(
                        context["request"], instance
                    )
                except Exception as e:
                    self._lookup_error(
                        f"ui helper lookup for {self.objvar!r} failed",
                        exception=e,
                    )
                    context[self.asvar] = None

            case _:
                self._lookup_error(
                    f"ui model instance {self.objvar!r} {instance!r}"
                    " has invalid type"
                )
                context[self.asvar] = None
        return SafeString()


@register.tag
def ui(parser: Parser, token: Token) -> UINode:
    """
    Lookup the UI helper for a model object.

    Usage::

        {% ui file as fileui %}

        ...use fileui at will...
    """
    bits = token.split_contents()
    asvar = None
    if len(bits) == 4 and bits[-2] == "as":
        objvar = bits[1]
        asvar = bits[3]
    else:
        raise TemplateSyntaxError(
            "'ui' statement syntax is {% ui [object] as [variable] %}"
        )
    return UINode(parser.compile_filter(objvar), asvar)
