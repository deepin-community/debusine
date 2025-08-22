# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the workspace views."""

from typing import Any, assert_never

from django.urls import reverse
from rest_framework import status

from debusine.db.models import Workspace
from debusine.db.models.workspaces import WorkspaceChain
from debusine.db.playground import scenarios
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)


class WorkspaceInheritanceViewTests(TestCase):
    """Tests for :py:class:`WorkspaceInheritanceView`."""

    scenario = scenarios.DefaultContextAPI()

    def get(
        self,
        workspace: Workspace | str | None = None,
        user_token: bool = True,
    ) -> TestResponseType:
        """GET request for a workspace."""
        match workspace:
            case None:
                workspace_name = self.scenario.workspace.name
            case str():
                workspace_name = workspace
            case _:
                workspace_name = workspace.name

        match user_token:
            case True:
                headers = {"token": self.scenario.user_token.key}
            case False:
                headers = {}
                pass
            case _ as unreachable:
                assert_never(unreachable)

        return self.client.get(
            reverse(
                "api:workspace-inheritance",
                kwargs={"workspace": workspace_name},
            ),
            content_type="application/json",
            headers=headers,
        )

    def post(
        self,
        workspace: Workspace | str | None = None,
        user_token: bool = True,
        data: dict[str, Any] | None = None,
    ) -> TestResponseType:
        """POST request for a named collection."""
        match workspace:
            case None:
                workspace_name = self.scenario.workspace.name
            case str():
                workspace_name = workspace
            case _:
                workspace_name = workspace.name

        match user_token:
            case True:
                headers = {"token": self.scenario.user_token.key}
            case False:
                headers = {}
                pass
            case _ as unreachable:
                assert_never(unreachable)

        if data is None:
            data = {}

        return self.client.post(
            reverse(
                "api:workspace-inheritance",
                kwargs={"workspace": workspace_name},
            ),
            data=data,
            content_type="application/json",
            headers=headers,
        )

    def test_unauthenticated(self) -> None:
        """Authentication is required."""
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(user_token=False)
                self.assertResponseProblem(
                    response,
                    "Error",
                    detail_pattern=(
                        "Authentication credentials were not provided."
                    ),
                    status_code=status.HTTP_403_FORBIDDEN,
                )

    def test_workspace_not_found(self) -> None:
        """Workspace must exist."""
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(workspace="does-not-exist")
                self.assertResponseProblem(
                    response,
                    "Workspace not found",
                    detail_pattern=(
                        "Workspace does-not-exist not found in scope debusine"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )

    def test_workspace_not_accessible(self) -> None:
        """User must be able to display the workspace."""
        workspace = self.playground.create_workspace(name="private")
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(workspace=workspace)
                self.assertResponseProblem(
                    response,
                    "Workspace not found",
                    detail_pattern=(
                        "Workspace private not found in scope debusine"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )

    def test_post_needs_can_configure(self) -> None:
        """The user must be able to display the collection."""
        with override_permission(Workspace, "can_configure", DenyAll):
            response = self.post()
            self.assertResponseProblem(
                response,
                "playground cannot configure workspace debusine/System",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    def test_get_empty_chain(self) -> None:
        response = self.get()
        data = self.assertAPIResponseOk(response)
        self.assertEqual(data, {"chain": []})

    def test_get_chain(self) -> None:
        first = self.playground.create_workspace(name="first")
        second = self.playground.create_workspace(name="second")
        self.scenario.workspace.set_inheritance([first, second])
        response = self.get()
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            {
                "chain": [
                    {"id": first.pk, "scope": "debusine", "workspace": "first"},
                    {
                        "id": second.pk,
                        "scope": "debusine",
                        "workspace": "second",
                    },
                ]
            },
        )

        # Try again after swapping order, to see if order is preserved
        chain = WorkspaceChain.objects.get(
            parent=first, child=self.scenario.workspace
        )
        chain.order = 10
        chain.save()
        response = self.get()
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            {
                "chain": [
                    {
                        "id": second.pk,
                        "scope": "debusine",
                        "workspace": "second",
                    },
                    {"id": first.pk, "scope": "debusine", "workspace": "first"},
                ]
            },
        )

    @override_permission(Workspace, "can_configure", AllowAll)
    def test_set_chain(self) -> None:
        first = self.playground.create_workspace(name="first", public=True)
        second = self.playground.create_workspace(name="second", public=True)
        response = self.post(
            data={
                "chain": [
                    {"id": first.pk},
                    {"scope": "debusine", "workspace": second.name},
                ]
            }
        )
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            {
                "chain": [
                    {"id": first.pk, "scope": "debusine", "workspace": "first"},
                    {
                        "id": second.pk,
                        "scope": "debusine",
                        "workspace": "second",
                    },
                ]
            },
        )

    @override_permission(Workspace, "can_display", AllowAll)
    @override_permission(Workspace, "can_configure", AllowAll)
    def test_set_chain_private_workspace_in_other_scope(self) -> None:
        scope = self.playground.get_or_create_scope("otherscope")
        second = self.playground.create_workspace(name="other", scope=scope)
        self.playground.create_group_role(
            second, Workspace.Roles.OWNER, [self.scenario.user]
        )

        response = self.post(data={"chain": [{"id": second.pk}]})
        self.assertResponseProblem(
            response,
            "Workspace cannot be inherited",
            detail_pattern=(
                "Private workspaces from other scopes cannot be inherited."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    @override_permission(Workspace, "can_configure", AllowAll)
    def test_set_invalid_chain(self) -> None:
        first = self.playground.create_workspace(name="first", public=True)
        response = self.post(
            data={
                "chain": [
                    {"id": first.pk},
                    {"id": first.pk},
                ]
            }
        )
        self.assertResponseProblem(
            response,
            "Invalid inheritance chain",
            detail_pattern="duplicate workspace 'first' in inheritance chain",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
