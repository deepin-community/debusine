# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the workflow views."""

from typing import Any

from django.conf import settings
from django.http.response import HttpResponseBase
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from debusine.artifacts.models import ArtifactCategory
from debusine.db.context import context
from debusine.db.models import (
    WorkRequest,
    WorkflowTemplate,
    Workspace,
    default_workspace,
)
from debusine.db.playground import scenarios
from debusine.server.serializers import WorkRequestSerializer
from debusine.tasks.models import TaskTypes
from debusine.test.django import (
    JSONResponseProtocol,
    TestCase,
    TestResponseType,
)


class WorkflowTemplateViewTests(TestCase):
    """Tests for WorkflowTemplate."""

    scenario = scenarios.DefaultContextAPI()

    def setUp(self) -> None:
        """Set up common objects."""
        super().setUp()
        self.client = APIClient()
        self.token = self.scenario.user_token

    def get_workflow_template(
        self, workflow_template_id: int, scope: str | None = None
    ) -> HttpResponseBase:
        """Get a workflow template from api:workflow-template-detail."""
        headers = {"Token": self.token.key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.get(
            reverse(
                "api:workflow-template-detail",
                kwargs={"pk": workflow_template_id},
            ),
            headers=headers,
        )

    def post_workflow_template(
        self, data: dict[str, Any], scope: str | None = None
    ) -> HttpResponseBase:
        """Post a workflow template to api:workflow-templates."""
        headers = {"Token": self.token.key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.post(
            reverse("api:workflow-templates"),
            data=data,
            headers=headers,
            format="json",
        )

    def patch_workflow_template(
        self,
        workflow_template_id: int,
        data: dict[str, Any],
        scope: str | None = None,
    ) -> HttpResponseBase:
        """Patch a workflow template via api:workflow-template-detail."""
        headers = {"Token": self.token.key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.patch(
            reverse(
                "api:workflow-template-detail",
                kwargs={"pk": workflow_template_id},
            ),
            data=data,
            headers=headers,
            format="json",
        )

    def delete_workflow_template(
        self, workflow_template_id: int, scope: str | None = None
    ) -> HttpResponseBase:
        """Delete a workflow template via api:workflow-template-detail."""
        headers = {"Token": self.token.key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope
        return self.client.delete(
            reverse(
                "api:workflow-template-detail",
                kwargs={"pk": workflow_template_id},
            ),
            headers=headers,
        )

    def test_authentication_credentials_not_provided(self) -> None:
        """A Token is required to use the endpoints."""
        response = self.client.get(reverse("api:workflow-templates"))
        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Authentication credentials were not provided.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

        response = self.client.get(
            reverse("api:workflow-template-detail", kwargs={"pk": 0})
        )
        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Authentication credentials were not provided.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_get_return_404_workflow_template_not_found(self) -> None:
        """Get a nonexistent workflow template: return 404."""
        response = self.get_workflow_template(0)

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="No WorkflowTemplate matches the given query.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_success(self) -> None:
        """Get a workflow template."""
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name="sbuild",
            task_data={"architectures": ["amd64", "arm64"]},
        )

        response = self.get_workflow_template(template.id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(
            response.json(),
            {
                "id": template.id,
                "name": template.name,
                "workspace": template.workspace.name,
                "task_name": template.task_name,
                "task_data": template.task_data,
                "priority": template.priority,
            },
        )

    def test_get_honours_scope(self) -> None:
        """Getting a workflow template looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64", "arm64"]},
        )

        response = self.get_workflow_template(
            template.id, scope=template.workspace.scope.name
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(response.json()["workspace"], template.workspace.name)

        response = self.get_workflow_template(template.id, scope=scope2.name)

        self.assertResponseProblem(
            response,
            title="Error",
            detail_pattern=r"No WorkflowTemplate matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_private_workspace_unauthorized(self) -> None:
        """Workflow templates in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=private_workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64", "arm64"]},
        )

        response = self.get_workflow_template(
            template.id, scope=private_workspace.scope.name
        )

        self.assertResponseProblem(
            response,
            title="Error",
            detail_pattern=r"No WorkflowTemplate matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_private_workspace_authorized(self) -> None:
        """Workflow templates in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=private_workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64", "arm64"]},
        )

        response = self.get_workflow_template(
            template.id, scope=private_workspace.scope.name
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert isinstance(response, JSONResponseProtocol)
        self.assertEqual(response.json()["workspace"], template.workspace.name)

    def test_post_without_model_permissions(self) -> None:
        """Only privileged users may create workflow templates."""
        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
                "task_name": "noop",
            }
        )

        self.assertResponseProblem(
            response,
            f"{self.scenario.user} cannot configure workspace "
            f"{default_workspace()}",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_post_success(self) -> None:
        """Create a new workflow template."""
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        task_data = {
            "target_distribution": "debian:bookworm",
            "architectures": ["amd64", "arm64"],
        }

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
                "task_name": "sbuild",
                "task_data": task_data,
                "priority": 0,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        template = WorkflowTemplate.objects.get(
            name="test", workspace=default_workspace()
        )
        self.assertEqual(template.task_name, "sbuild")
        self.assertEqual(template.task_data, task_data)

    def test_post_different_workspace(self) -> None:
        """Create a new workflow template in a non-default workspace."""
        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(
                name="test-workspace", public=True
            )
        self.playground.create_group_role(
            workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        task_data = {"target_distribution": "debian:bookworm"}

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": "test-workspace",
                "task_name": "sbuild",
                "task_data": task_data,
                "priority": 0,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        template = WorkflowTemplate.objects.get(
            name="test", workspace=workspace
        )
        self.assertEqual(template.task_name, "sbuild")
        self.assertEqual(template.task_data, task_data)

    def test_post_honours_scope(self) -> None:
        """Creating a workflow template looks up workspace in current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        scope3 = self.playground.get_or_create_scope("scope3")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="common-name", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="common-name", public=True
        )

        for workspace in (workspace1, workspace2):
            self.playground.create_group_role(
                workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
            )

            response = self.post_workflow_template(
                {
                    "name": "test",
                    "workspace": "common-name",
                    "task_name": "sbuild",
                    "priority": 0,
                },
                scope=workspace.scope.name,
            )

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            assert isinstance(response, Response)
            template = WorkflowTemplate.objects.get(id=response.data["id"])
            self.assertEqual(template.workspace, workspace)

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": "common-name",
                "task_name": "sbuild",
                "priority": 0,
            },
            scope=scope3.name,
        )
        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern="Workspace common-name not found in scope scope3",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_no_default_workspace(self) -> None:
        """POST with no workspace in a scope without a default workspace."""
        scope = self.playground.get_or_create_scope("empty-scope")

        response = self.post_workflow_template(
            {"name": "test", "task_name": "sbuild", "priority": 0},
            scope=scope.name,
        )

        self.assertResponseProblem(
            response,
            "Cannot deserialize workflow template",
            validation_errors_pattern=(
                r"'workspace': \['This field is required\.'\]"
            ),
        )

    def test_post_private_workspace_unauthorized(self) -> None:
        """POST to private workspaces 404s to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": private_workspace.name,
                "task_name": "sbuild",
                "priority": 0,
            },
            scope=private_workspace.scope.name,
        )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern=(
                f"Workspace {private_workspace.name} not found in scope "
                f"{private_workspace.scope.name}"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_post_private_workspace_authorized(self) -> None:
        """POST to private workspaces succeeds for the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": private_workspace.name,
                "task_name": "sbuild",
                "priority": 0,
            },
            scope=private_workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assert isinstance(response, Response)
        template = WorkflowTemplate.objects.get(id=response.data["id"])
        self.assertEqual(template.workspace, private_workspace)

    def test_post_positive_priority_without_permissions(self) -> None:
        """Creating with positive priorities requires a special permission."""
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
                "task_name": "noop",
                "priority": 1,
            }
        )

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="You are not permitted to set positive priorities",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_post_positive_priority_with_permissions(self) -> None:
        """Privileged users may create with positive priorities."""
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        self.playground.add_user_permission(
            self.scenario.user, WorkRequest, "manage_workrequest_priorities"
        )

        response = self.post_workflow_template(
            {
                "name": "test",
                "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
                "task_name": "noop",
                "priority": 1,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        template = WorkflowTemplate.objects.get(
            name="test", workspace=default_workspace()
        )
        self.assertEqual(template.priority, 1)

    def test_patch_without_model_permissions(self) -> None:
        """Only privileged users may update workflow templates."""
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name="sbuild",
            task_data={"architectures": ["amd64"]},
        )

        response = self.patch_workflow_template(
            template.id, {"task_data": {"architectures": ["amd64", "arm64"]}}
        )

        self.assertResponseProblem(
            response,
            f"{self.scenario.user} cannot configure workspace "
            f"{default_workspace()}",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_patch_success(self) -> None:
        """Update a workflow template."""
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name="sbuild",
            task_data={"architectures": ["amd64"]},
        )
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        task_data = {"architectures": ["amd64", "arm64"]}

        response = self.patch_workflow_template(
            template.id, {"task_data": task_data}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        template.refresh_from_db()
        self.assertEqual(template.task_data, task_data)

    def test_patch_positive_priority_without_permissions(self) -> None:
        """Updating with positive priorities requires a special permission."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )

        response = self.patch_workflow_template(template.id, {"priority": 1})

        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="You are not permitted to set positive priorities",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_patch_positive_priority_with_permissions(self) -> None:
        """Privileged users may update with positive priorities."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        self.playground.add_user_permission(
            self.scenario.user, WorkRequest, "manage_workrequest_priorities"
        )

        response = self.patch_workflow_template(template.id, {"priority": 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        template.refresh_from_db()
        self.assertEqual(template.priority, 1)

    def test_patch_honours_scope(self) -> None:
        """Patching a workflow template looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64"]},
        )
        self.playground.create_group_role(
            workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        task_data = {"architectures": ["amd64", "arm64"]}

        response = self.patch_workflow_template(
            template.id,
            {"task_data": task_data},
            scope=template.workspace.scope.name,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        template.refresh_from_db()
        self.assertEqual(template.task_data, task_data)

        response = self.patch_workflow_template(
            template.id, {"task_data": task_data}, scope=scope2.name
        )

        self.assertResponseProblem(
            response,
            title="Error",
            detail_pattern=r"No WorkflowTemplate matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_patch_private_workspace_unauthorized(self) -> None:
        """Workflow templates in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=private_workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64"]},
        )
        task_data = {"architectures": ["amd64", "arm64"]}

        response = self.patch_workflow_template(
            template.id,
            {"task_data": task_data},
            scope=private_workspace.scope.name,
        )

        self.assertResponseProblem(
            response,
            title="Error",
            detail_pattern=r"No WorkflowTemplate matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_patch_private_workspace_authorized(self) -> None:
        """Workflow templates in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=private_workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64"]},
        )
        task_data = {"architectures": ["amd64", "arm64"]}

        response = self.patch_workflow_template(
            template.id,
            {"task_data": task_data},
            scope=private_workspace.scope.name,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        template.refresh_from_db()
        self.assertEqual(template.task_data, task_data)

    def test_delete_without_model_permissions(self) -> None:
        """Only privileged users may delete workflow templates."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )

        response = self.delete_workflow_template(template.id)

        self.assertResponseProblem(
            response,
            f"{self.scenario.user} cannot configure workspace "
            f"{default_workspace()}",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_delete_success(self) -> None:
        """Delete a workflow template."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        self.playground.create_group_role(
            default_workspace(),
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )

        response = self.delete_workflow_template(template.id)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        with self.assertRaises(WorkflowTemplate.DoesNotExist):
            template.refresh_from_db()

    def test_delete_honours_scope(self) -> None:
        """Deleting a workflow template looks it up in the current scope."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        workspace = self.playground.create_workspace(
            scope=scope1, name="workspace", public=True
        )
        template = WorkflowTemplate.objects.create(
            name="test", workspace=workspace, task_name="noop"
        )
        self.playground.create_group_role(
            workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        response = self.get_workflow_template(template.id, scope=scope2.name)

        self.assertResponseProblem(
            response,
            title="Error",
            detail_pattern=r"No WorkflowTemplate matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

        response = self.delete_workflow_template(
            template.id, scope=template.workspace.scope.name
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        with self.assertRaises(WorkflowTemplate.DoesNotExist):
            template.refresh_from_db()

    def test_delete_private_workspace_unauthorized(self) -> None:
        """Workflow templates in private workspaces 404 to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        template = WorkflowTemplate.objects.create(
            name="test", workspace=private_workspace, task_name="noop"
        )

        response = self.delete_workflow_template(
            template.id, scope=private_workspace.scope.name
        )

        self.assertResponseProblem(
            response,
            title="Error",
            detail_pattern=r"No WorkflowTemplate matches the given query\.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_delete_private_workspace_authorized(self) -> None:
        """Workflow templates in private workspaces 200 to the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        template = WorkflowTemplate.objects.create(
            name="test", workspace=private_workspace, task_name="noop"
        )
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        response = self.delete_workflow_template(
            template.id, scope=private_workspace.scope.name
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        with self.assertRaises(WorkflowTemplate.DoesNotExist):
            template.refresh_from_db()


class WorkflowViewTests(TestCase):
    """Tests for WorkflowView."""

    scenario = scenarios.DefaultContextAPI()

    def setUp(self) -> None:
        """Set up common objects."""
        super().setUp()
        self.client = APIClient()
        self.token = self.scenario.user_token

    def post_workflow(
        self, data: dict[str, Any], scope: str | None = None
    ) -> TestResponseType:
        """Post a workflow creation request to api:workflows."""
        headers = {"Token": self.token.key}
        if scope is not None:
            headers["X-Debusine-Scope"] = scope

        return self.client.post(
            reverse("api:workflows"), data=data, headers=headers, format="json"
        )

    def test_authentication_credentials_not_provided(self) -> None:
        """A Token is required to use the endpoint."""
        response = self.client.post(reverse("api:workflows"))
        self.assertResponseProblem(
            response,
            "Error",
            detail_pattern="Authentication credentials were not provided.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_create_workflow_success(self) -> None:
        """Create a workflow."""
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            [self.scenario.user],
        )
        artifact = self.playground.create_source_artifact()
        for architecture in ("amd64", "arm64"):
            self.playground.create_debian_environment(
                codename="bookworm", architecture=architecture
            )

        WorkflowTemplate.objects.create(
            name="test",
            workspace=default_workspace(),
            task_name="sbuild",
            task_data={"architectures": ["amd64", "arm64"]},
        )

        response = self.post_workflow(
            {
                "template_name": "test",
                "task_data": {
                    "input": {"source_artifact": artifact.id},
                    "target_distribution": "debian:bookworm",
                },
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workflow = WorkRequest.objects.latest("created_at")
        self.assertEqual(response.json(), WorkRequestSerializer(workflow).data)
        self.assertDictContainsAll(
            response.json(),
            {
                "workspace": settings.DEBUSINE_DEFAULT_WORKSPACE,
                "created_by": self.token.user_id,
                "task_type": TaskTypes.WORKFLOW,
                "task_name": "sbuild",
                "task_data": {
                    "input": {"source_artifact": artifact.id},
                    "target_distribution": "debian:bookworm",
                    "architectures": ["amd64", "arm64"],
                },
                "priority_base": 0,
            },
        )

    def test_create_workflow_different_workspace(self) -> None:
        """Create a workflow in a non-default workspace."""
        workspace = self.playground.create_workspace(
            name="test-workspace", public=True
        )
        self.playground.create_group_role(
            workspace,
            Workspace.Roles.CONTRIBUTOR,
            [self.scenario.user],
        )
        artifact = self.playground.create_source_artifact(workspace=workspace)
        for architecture in ("amd64", "arm64"):
            self.playground.create_debian_environment(
                workspace=workspace,
                codename="bookworm",
                architecture=architecture,
            )
        WorkflowTemplate.objects.create(
            name="test",
            workspace=workspace,
            task_name="sbuild",
            task_data={"architectures": ["amd64", "arm64"]},
        )

        response = self.post_workflow(
            {
                "template_name": "test",
                "workspace": workspace.name,
                "task_data": {
                    "input": {"source_artifact": artifact.id},
                    "target_distribution": "debian:bookworm",
                },
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workflow = WorkRequest.objects.latest("created_at")
        self.assertEqual(response.json(), WorkRequestSerializer(workflow).data)
        self.assertEqual(workflow.workspace, workspace)

    def test_create_workflow_nonexistent_template(self) -> None:
        """The view returns HTTP 404 if the workflow template does not exist."""
        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_workflow(
                {"template_name": "test", "task_data": {}}
            )

        self.assertResponseProblem(
            response,
            "Workflow template not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_create_workflow_invalid_task_data(self) -> None:
        """Creating a workflow validates task data."""
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            [self.scenario.user],
        )
        self.playground.create_workflow_template(name="noop", task_name="noop")

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_workflow(
                {"template_name": "noop", "task_data": {"nonexistent": True}}
            )

        self.assertResponseProblem(
            response,
            "Cannot create workflow",
            detail_pattern=(
                r"extra fields not permitted \(type=value_error\.extra\)"
            ),
        )

    def test_create_workflow_thorough_input_validation(self) -> None:
        """Creating a workflow does thorough validation of input data."""
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            [self.scenario.user],
        )
        self.playground.create_workflow_template(
            name="sbuild", task_name="sbuild"
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_workflow(
                {
                    "template_name": "sbuild",
                    "task_data": {
                        "input": {"source_artifact": artifact.id},
                        "target_distribution": "debian:bookworm",
                        "architectures": ["amd64"],
                    },
                }
            )

        self.assertResponseProblem(
            response,
            "Cannot create workflow",
            detail_pattern=(
                r"^input.source_artifact: unexpected artifact category: "
                r"'debusine:test'. Valid categories: "
                r"\['debian:source-package', 'debian:upload'\]$"
            ),
        )

    def test_create_workflow_honours_scope(self) -> None:
        """Creating a workflow looks up workspace in current scope."""
        artifact = self.playground.create_source_artifact()
        for architecture in ("amd64", "i386"):
            self.playground.create_debian_environment(
                codename="bookworm", architecture=architecture
            )

        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        scope3 = self.playground.get_or_create_scope("scope3")
        workspace1 = self.playground.create_workspace(
            scope=scope1, name="common-name", public=True
        )
        workspace2 = self.playground.create_workspace(
            scope=scope2, name="common-name", public=True
        )
        for workspace, architectures in (
            (workspace1, ["amd64"]),
            (workspace2, ["i386"]),
        ):
            workspace.set_inheritance([self.scenario.workspace])
            self.playground.create_workflow_template(
                name="test",
                task_name="sbuild",
                workspace=workspace,
                task_data={"architectures": architectures},
            )
        task_data = {
            "input": {"source_artifact": artifact.id},
            "target_distribution": "debian:bookworm",
        }

        for workspace, architectures in (
            (workspace1, ["amd64"]),
            (workspace2, ["i386"]),
        ):
            self.playground.create_group_role(
                workspace,
                Workspace.Roles.CONTRIBUTOR,
                [self.scenario.user],
            )

            response = self.post_workflow(
                {
                    "template_name": "test",
                    "workspace": "common-name",
                    "task_data": task_data,
                },
                scope=workspace.scope.name,
            )

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            workflow = WorkRequest.objects.latest("created_at")
            self.assertEqual(
                response.json(), WorkRequestSerializer(workflow).data
            )
            self.assertEqual(workflow.workspace, workspace)
            self.assertEqual(workflow.task_data["architectures"], architectures)

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_workflow(
                {
                    "template_name": "test",
                    "workspace": "common-name",
                    "task_data": task_data,
                },
                scope=scope3.name,
            )
        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern="Workspace common-name not found in scope scope3",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_create_workflow_no_default_workspace(self) -> None:
        """POST with no workspace in a scope without a default workspace."""
        scope = self.playground.get_or_create_scope("empty-scope")

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_workflow(
                {"template_name": "test"}, scope=scope.name
            )

        self.assertResponseProblem(
            response,
            "Cannot deserialize workflow creation request",
            validation_errors_pattern=(
                r"'workspace': \['This field is required\.'\]"
            ),
        )

    def test_create_workflow_private_workspace_unauthorized(self) -> None:
        """POST to private workspaces 404s to the unauthorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_workflow_template(
            name="test", task_name="noop", workspace=private_workspace
        )

        with self.assert_model_count_unchanged(WorkRequest):
            response = self.post_workflow(
                {"template_name": "test", "workspace": private_workspace.name},
                scope=private_workspace.scope.name,
            )

        self.assertResponseProblem(
            response,
            "Workspace not found",
            detail_pattern=(
                f"Workspace {private_workspace.name} not found in scope "
                f"{private_workspace.scope.name}"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_create_workflow_private_workspace_authorized(self) -> None:
        """POST to private workspaces succeeds for the authorized."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.playground.create_workflow_template(
            name="test", task_name="noop", workspace=private_workspace
        )

        response = self.post_workflow(
            {"template_name": "test", "workspace": private_workspace.name},
            scope=private_workspace.scope.name,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workflow = WorkRequest.objects.latest("created_at")
        self.assertEqual(response.json(), WorkRequestSerializer(workflow).data)
        self.assertEqual(workflow.workspace, private_workspace)
