# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application: workspaces."""

import logging

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response

from debusine.client.models import (
    WorkspaceInheritanceChain,
    WorkspaceInheritanceChainElement,
    model_to_json_serializable_dict,
)
from debusine.db.context import context
from debusine.db.models.workspaces import Workspace, WorkspaceChain
from debusine.server.exceptions import DebusineAPIException
from debusine.server.serializers import WorkspaceChainSerializer
from debusine.server.views.base import BaseAPIView
from debusine.server.views.rest import IsTokenUserAuthenticated

logger = logging.getLogger(__name__)


class WorkspaceInheritanceView(BaseAPIView):
    """View used to fetch and update a workspace inheritance chain."""

    permission_classes = [IsTokenUserAuthenticated]
    parser_classes = [JSONParser]

    def _get_chain(self, workspace: Workspace) -> WorkspaceInheritanceChain:
        res = WorkspaceInheritanceChain(chain=[])
        for pk, scope_name, workspace_name in (
            WorkspaceChain.objects.filter(child=workspace)
            .order_by("order")
            .values_list("parent__id", "parent__scope__name", "parent__name")
        ):
            res.chain.append(
                WorkspaceInheritanceChainElement(
                    id=pk, scope=scope_name, workspace=workspace_name
                )
            )
        return res

    def get(self, request: Request, workspace: str) -> Response:  # noqa: U100
        """Retrieve the contents of the collection."""
        self.set_current_workspace(workspace)
        chain = self._get_chain(context.require_workspace())
        return Response(
            model_to_json_serializable_dict(chain),
            status=status.HTTP_200_OK,
        )

    def post(self, request: Request, workspace: str) -> Response:  # noqa: U100
        """Update the contents of the collection."""
        self.set_current_workspace(workspace)
        current = context.require_workspace()
        self.enforce(current.can_configure)

        # Decode request.data
        serializer = WorkspaceChainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_chain = serializer.validated_data["chain"]

        # Check permissions of items
        existing_ids = frozenset(c.id for c in self._get_chain(current).chain)
        for ws in user_chain:
            if (
                ws.pk not in existing_ids
                and not ws.public
                and ws.scope != current.scope
            ):
                raise DebusineAPIException(
                    title="Workspace cannot be inherited",
                    detail=(
                        "Private workspaces from other scopes"
                        " cannot be inherited."
                    ),
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        # Set new chain
        try:
            current.set_inheritance(user_chain)
        except ValueError as e:
            raise DebusineAPIException(
                title="Invalid inheritance chain",
                detail=str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Return the new chain
        chain = self._get_chain(current)
        return Response(
            model_to_json_serializable_dict(chain),
            status=status.HTTP_200_OK,
        )
