# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Template tag override_query_parameters."""

from typing import Any

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def override_query_parameters(
    context: template.RequestContext, **kwargs: Any
) -> str:
    """
    Return query with overridden query parameters.

    If a query parameter is:
    page=1&order=id&asc=1

    Using this template tag with:
    override_query_parameters(page=2)

    result:
    page=2&order=id&asc=1
    """
    query = context.request.GET.copy()

    for k, v in kwargs.items():
        query[k] = v

    return query.urlencode()
