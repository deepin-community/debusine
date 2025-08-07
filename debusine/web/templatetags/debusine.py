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
from datetime import datetime
from typing import Any

from django import template
from django.conf import settings
from django.db.models import Model
from django.template import Context, Node, NodeList, TemplateSyntaxError
from django.template.base import FilterExpression, Parser, Token
from django.utils.html import format_html
from django.utils.safestring import SafeString

import debusine.web.views.view_utils
from debusine.db.context import context
from debusine.server.scopes import urlconf_scope
from debusine.web.icons import Icons
from debusine.web.views.base import Widget
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


@register.simple_tag(takes_context=True)
def widget(context: Context, widget: Widget) -> str:
    """Render a UI widget."""
    try:
        return widget.render(context)
    except Exception:
        if settings.DEBUG:
            raise
        # When running in production, avoid leaking possibly sensitive error
        # information while providing enough information for a potential bug
        # report to locate the stack trace in the logs
        logger.warning("widget %r failed to render", widget, exc_info=True)
        return format_html(
            "<span data-role='debusine-widget-error'"
            " class='bg-danger text-white'>{ts} UTC: {widget}"
            " failed to render</span>",
            widget=widget.__class__.__name__,
            ts=datetime.utcnow().isoformat(),
        )


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

        with urlconf_scope(value.name), appcontext.local():
            # Render the contained template using a different current scope
            appcontext.reset()
            appcontext.set_scope(value)
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
def format_yaml(value: Any) -> str:
    """Format a data structure as YAML."""
    return debusine.web.views.view_utils.format_yaml(value)


@register.simple_tag
def icon(name: str) -> str:
    """Lookup an icon by name."""
    return "bi-" + getattr(Icons, name.upper(), "square-fill")


@register.filter(name="sorted")
def sorted_(value: Any) -> Any:
    """Sort a sequence of values."""
    return sorted(value)
