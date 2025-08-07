# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Exceptions used by debusine.client."""

from typing import Any


class NotFoundError(Exception):
    """
    Raised if the server returns 404.

    The reason is probably that the queried item cannot be found. It could
    also be that the client requested a non-existing URL. Check message for
    more information.
    """


class UnexpectedResponseError(Exception):
    """
    Raised if the client received an unexpected response from the server.

    It could be a non-expected status code, the response is not JSON, etc.
    """


class ClientForbiddenError(Exception):
    """Raise if the server return 403 to a client."""


class ClientConnectionError(Exception):
    """Raised when the server cannot be reached: timeout, unreachable, etc."""


class TokenDisabledError(Exception):
    """Raised if the server disconnects the worker: the token is disabled."""


class DebusineError(Exception):
    """
    Raised when the debusine server returns HTTP 400 with a message.

    The message is in the JSON content in "detail".
    """

    def __init__(self, problem: dict[str, Any]) -> None:
        """Initialize the exception."""
        self.problem = problem

    def asdict(self) -> dict[str, Any]:
        """Return the DebusineError as dictionary."""
        return self.problem


class ContentValidationError(Exception):
    """Raised if downloaded data doesn't match the expected hash or size."""


class ContentTooLargeError(Exception):
    """Raised if content is bigger than expected."""
