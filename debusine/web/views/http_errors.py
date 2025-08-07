# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Exception based HTTP error handling."""

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.template.response import TemplateResponse
from rest_framework import status


class HttpError(Exception):
    """HTTP error to be rendered as a response."""

    def __init__(self, code: int, error: str) -> None:
        """Initialize with http code and error message."""
        super().__init__(error)
        self.code = code


class HttpError400(HttpError):
    """Raise a Bad Request HTTP error."""

    def __init__(self, error: str) -> None:
        """Force code to be 400."""
        super().__init__(code=status.HTTP_400_BAD_REQUEST, error=error)


def catch_http_errors(
    view_func: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    """Wrap a view function to render raised HttpError exceptions."""
    # We mostly use this of HTTP 400, for which there is BadRequest. However,
    # django.views.defaults.bad_request does not pass information from the
    # exception to the template context, to prevent disclosing sensitive
    # information.
    #
    # We can use this decorator to have an alternative exception-based error
    # reporting for views, using a custom HttpError exception that is intended
    # to be shown to the user

    @wraps(view_func)
    def wrapped(
        request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        try:
            return view_func(request, *args, **kwargs)
        except HttpError as e:
            context = {"error": e.args[0]}
            return TemplateResponse(
                request, f"{e.code}.html", context, status=e.code
            )

    return wrapped
