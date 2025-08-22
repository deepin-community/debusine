# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Used by Django REST framework exception handling."""

import logging
import traceback
from collections.abc import Iterable
from typing import Any, NoReturn, TYPE_CHECKING

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler, set_rollback

from debusine.db.context import context
from debusine.db.models import Workspace

if TYPE_CHECKING:
    from debusine.server.views import ProblemResponse

logger = logging.getLogger(__name__)


class DebusineAPIException(APIException):
    """APIException producing the JSON structure expected by Debusine API."""

    def __init__(
        self,
        title: str,
        detail: str | None = None,
        validation_errors: Iterable[Any] | None = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        rollback_transaction: bool = True,
    ) -> None:
        """Initialize with ProblemResponse's arguments."""
        super().__init__(code=str(status_code))
        self.debusine_title = title
        self.debusine_detail = detail
        self.debusine_validation_errors = validation_errors
        self.debusine_status_code = status_code
        self.rollback_transaction = rollback_transaction


def raise_workspace_not_found(workspace: str | Workspace) -> NoReturn:
    """
    Raise Http404 for a workspace not found.

    This is abstracted to provide consistent error messages also in case of
    permission denied, to prevent leaking the presence of workspaces in
    inaccessible scopes.
    """
    if isinstance(workspace, Workspace):
        workspace = workspace.name
    raise DebusineAPIException(
        title="Workspace not found",
        detail=f"Workspace {workspace} not found in scope {context.scope}",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def debusine_exception_handler(
    exc: Exception, context: dict[str, Any]
) -> "ProblemResponse":
    """Return ProblemResponse based on exc, context."""
    from debusine.server.views import ProblemResponse

    match exc:
        case DebusineAPIException():
            if exc.rollback_transaction:
                set_rollback()
            return ProblemResponse(
                title=exc.debusine_title,
                detail=exc.debusine_detail,
                validation_errors=exc.debusine_validation_errors,
                status_code=exc.debusine_status_code,
            )
        case _:
            rest_response = exception_handler(exc, context)

            status_code = getattr(
                rest_response, "status_code", status.HTTP_400_BAD_REQUEST
            )
            detail = getattr(exc, "detail", None)

            formatted_traceback = traceback.format_exc().replace("\n", "\\n")

            logger.error(
                "Server exception. status_code: %s detail: %s traceback: %s",
                status_code,
                detail,
                formatted_traceback,
            )

            result = ProblemResponse(
                title="Error", detail=detail, status_code=status_code
            )
            # Annotate the response with the original exception, so that
            # BaseDjangoTestCase.format_api_response can find it and print it
            setattr(result, "_original_exception", exc)
            return result
