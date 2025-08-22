# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the asset views."""

from typing import Any

from django.urls import reverse
from django.utils.dateparse import parse_datetime
from rest_framework import status

from debusine.artifacts.models import ArtifactCategory, TaskTypes
from debusine.assets import AssetCategory, KeyPurpose, SigningKeyData
from debusine.db.models import Artifact, Asset, Scope, WorkRequest, Workspace
from debusine.db.models.assets import AssetUsageRoles
from debusine.db.models.scopes import ScopeRoles
from debusine.db.playground import scenarios
from debusine.db.playground.playground import SourceUploadArtifacts
from debusine.server.views.assets import AssetPermissionCheckView, log
from debusine.signing.tasks.models import (
    DebsignData,
    DebsignDynamicData,
    SignData,
    SignDynamicData,
)
from debusine.tasks.models import LookupMultiple
from debusine.test.django import TestCase, TestResponseType


class AssetViewTests(TestCase):
    """Tests for AssetView."""

    scenario = scenarios.DefaultContextAPI()
    playground_memory_file_store = False

    def post_asset_create(
        self,
        *,
        token_key: str,
        data: dict[str, Any],
        scope: Scope,
    ) -> TestResponseType:
        """
        Call self.client.post() to api:asset.

        Pass the token.key and content_type="application/json".

        :param token_key: value of the Token header for the request
        :param data: data to post
        """
        headers: dict[str, str] = {}
        headers["X-Debusine-Scope"] = scope.name

        return self.client.post(
            reverse("api:asset"),
            data=data,
            HTTP_TOKEN=token_key,
            content_type="application/json",
            headers=headers,
        )

    def get_asset_list(
        self,
        *,
        token_key: str,
        query_params: dict[str, Any],
        scope: Scope,
    ) -> TestResponseType:
        """
        Call self.client.get() to api:asset.

        Pass the token.key and content_type="application/json".

        :param token_key: value of the Token header for the request
        :param query_params: query keys and values
        """
        headers: dict[str, str] = {}
        headers["Accept"] = "application/json"
        headers["X-Debusine-Scope"] = scope.name

        return self.client.get(
            reverse("api:asset"),
            HTTP_TOKEN=token_key,
            headers=headers,
            data=query_params,
        )

    def signing_key_asset_as_dict_request(
        self,
        fingerprint: str,
        public_key: str,
        workspace: Workspace,
        description: str | None = None,
        purpose: KeyPurpose = KeyPurpose.OPENPGP,
        work_request_id: int | None = None,
    ) -> dict[str, Any]:
        """Generate an asset creation request dict."""
        return {
            "workspace": workspace.name,
            "category": AssetCategory.SIGNING_KEY,
            "work_request": work_request_id,
            "data": SigningKeyData(
                purpose=purpose,
                fingerprint=fingerprint,
                public_key=public_key,
                description=description,
            ).dict(),
        }

    def test_create_asset(self) -> None:
        """Create a signing-key asset."""
        workspace = self.playground.get_default_workspace()
        user = self.playground.get_default_user()
        self.playground.create_group_role(
            workspace.scope, ScopeRoles.OWNER, users=[user]
        )
        response = self.post_asset_create(
            token_key=self.scenario.user_token.key,
            data=self.signing_key_asset_as_dict_request(
                fingerprint="ABC123",
                public_key="PUBLIC KEY",
                workspace=workspace,
            ),
            scope=workspace.scope,
        )
        body = self.assertAPIResponseOk(response, status.HTTP_201_CREATED)
        asset = Asset.objects.get(id=body["id"])
        self.assertEqual(asset.category, AssetCategory.SIGNING_KEY)
        data = asset.data_model
        assert isinstance(data, SigningKeyData)
        self.assertEqual(data.fingerprint, "ABC123")
        self.assertEqual(data.public_key, "PUBLIC KEY")
        self.assertEqual(asset.workspace, workspace)
        self.assertEqual(asset.created_by, user)
        self.assertIsNone(asset.created_by_work_request)

    def test_create_asset_idempotent(self) -> None:
        """Create a signing-key asset that already exists, return 200."""
        workspace = self.playground.get_default_workspace()
        user = self.playground.get_default_user()
        self.playground.create_group_role(
            workspace.scope, ScopeRoles.OWNER, users=[user]
        )
        asset = self.playground.create_signing_key_asset()
        data = asset.data_model
        assert isinstance(data, SigningKeyData)
        response = self.post_asset_create(
            token_key=self.scenario.user_token.key,
            data=self.signing_key_asset_as_dict_request(
                purpose=data.purpose,
                fingerprint=data.fingerprint,
                public_key="PUBLIC KEY",
                workspace=workspace,
            ),
            scope=workspace.scope,
        )
        body = self.assertAPIResponseOk(response)
        self.assertEqual(body["id"], asset.id)

    def assert_asset_equals(
        self, asset: Asset, asset_response: dict[str, Any]
    ) -> None:
        """Assert that asset == asset_response."""
        # Not true in general, but true for all the assets that the view can
        # currently return (since it uses Asset.objects.in_current_scope).
        assert asset.workspace is not None

        self.assertEqual(asset_response["id"], asset.id)
        self.assertEqual(asset_response["data"], asset.data)
        self.assertEqual(asset_response["category"], asset.category)
        self.assertEqual(asset_response["scope"], asset.workspace.scope.name)
        self.assertEqual(asset_response["workspace"], asset.workspace.name)
        self.assertEqual(
            parse_datetime(asset_response["created_at"]), asset.created_at
        )
        self.assertEqual(
            asset_response["work_request"], asset.created_by_work_request_id
        )

    def test_list_asset_by_id(self) -> None:
        """List an asset by ID."""
        workspace = self.playground.get_default_workspace()
        asset = self.playground.create_signing_key_asset()
        response = self.get_asset_list(
            token_key=self.scenario.user_token.key,
            query_params={"asset": asset.id},
            scope=workspace.scope,
        )
        body = self.assertAPIResponseOk(response)
        self.assert_asset_equals(asset, body[0])

    def test_list_asset_by_work_request(self) -> None:
        """List an asset by work request."""
        workspace = self.playground.get_default_workspace()
        work_request = self.playground.create_work_request()
        asset = self.playground.create_signing_key_asset(
            created_by_work_request=work_request
        )
        response = self.get_asset_list(
            token_key=self.scenario.user_token.key,
            query_params={"work_request": work_request.id},
            scope=workspace.scope,
        )
        body = self.assertAPIResponseOk(response)
        self.assert_asset_equals(asset, body[0])

    def test_list_assets_by_workspace(self) -> None:
        """List assets by workspace."""
        workspace = self.playground.get_default_workspace()
        asset1 = self.playground.create_signing_key_asset()
        asset2 = self.playground.create_signing_key_asset(
            purpose=KeyPurpose.UEFI
        )
        workspace2 = self.playground.create_workspace(name="workspace2")
        self.playground.create_signing_key_asset(workspace=workspace2)
        response = self.get_asset_list(
            token_key=self.scenario.user_token.key,
            query_params={"workspace": workspace.name},
            scope=workspace.scope,
        )
        body = self.assertAPIResponseOk(response)
        self.assertEqual(len(body), 2)
        self.assert_asset_equals(asset1, body[0])
        self.assert_asset_equals(asset2, body[1])

    def test_list_assets_unfiltered(self) -> None:
        """List assets without a filter."""
        workspace = self.playground.get_default_workspace()
        response = self.get_asset_list(
            token_key=self.scenario.user_token.key,
            query_params={},
            scope=workspace.scope,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(
            body,
            {
                "title": (
                    '"asset", "work_request", or "workspace" parameters are '
                    'mandatory'
                ),
            },
        )


class AssetPermissionCheckViewTests(TestCase):
    """Tests for AssetPermissionCheckView."""

    playground_memory_file_store = False

    def post_permission_check(
        self,
        *,
        asset_category: str,
        asset_slug: str,
        permission_name: str,
        token_key: str,
        data: dict[str, Any],
    ) -> TestResponseType:
        """
        Call self.client.post() to api:asset-permission-check.

        Pass the token.key and content_type="application/json".

        :param token_key: value of the Token header for the request
        :param data: data to post
        """
        return self.client.post(
            reverse(
                "api:asset-permission-check",
                kwargs={
                    "asset_category": asset_category,
                    "asset_slug": asset_slug,
                    "permission_name": permission_name,
                },
            ),
            data=data,
            HTTP_TOKEN=token_key,
            content_type="application/json",
        )

    def create_debsign_work_request(
        self,
        asset: Asset,
        upload_artifacts: SourceUploadArtifacts,
        **kwargs: Any,
    ) -> WorkRequest:
        """Create a debsign WorkRequest using asset and upload_artifacts."""
        asset_model = asset.data_model
        assert isinstance(asset_model, SigningKeyData)
        return self.playground.create_work_request(
            task_type=TaskTypes.SIGNING,
            task_name="debsign",
            task_data=DebsignData(
                unsigned=upload_artifacts.upload.id,
                key=asset_model.fingerprint,
            ),
            dynamic_task_data=DebsignDynamicData(
                unsigned_id=upload_artifacts.upload.id
            ).dict(),
            mark_running=True,
            **kwargs,
        )

    def test_permission_check_on_upload(self) -> None:
        workspace = self.playground.get_default_workspace()
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        upload_artifacts = self.playground.create_upload_artifacts(binary=False)
        work_request = self.create_debsign_work_request(asset, upload_artifacts)
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": upload_artifacts.upload.id,
                "work_request_id": work_request.id,
                "workspace": workspace.name,
            },
        )
        body = self.assertAPIResponseOk(response)
        self.assertFalse(body["has_permission"])
        self.assertEqual(body["username"], work_request.created_by.username)
        self.assertEqual(body["user_id"], work_request.created_by.id)
        self.assertEqual(
            body["resource"],
            {
                "architecture": "source",
                "source": "hello",
                "version": "1.0-1",
                "distribution": "unstable",
            },
        )

    def test_permission_check_on_upload_granted(self) -> None:
        workspace = self.playground.get_default_workspace()
        token = self.playground.create_worker_token()
        user = self.playground.get_default_user()
        asset = self.playground.create_signing_key_asset()
        asset_usage = self.playground.create_asset_usage(
            resource=asset, workspace=workspace
        )
        self.playground.create_group_role(
            asset_usage, AssetUsageRoles.SIGNER, users=[user]
        )
        upload_artifacts = self.playground.create_upload_artifacts(binary=False)
        work_request = self.create_debsign_work_request(
            asset, upload_artifacts, created_by=user
        )
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": upload_artifacts.upload.id,
                "work_request_id": work_request.id,
                "workspace": workspace.name,
            },
        )
        body = self.assertAPIResponseOk(response)
        self.assertTrue(body["has_permission"])
        self.assertEqual(body["username"], user.username)
        self.assertEqual(body["user_id"], work_request.created_by.id)

    def create_sign_work_request(
        self,
        asset: Asset,
        signing_input_artifacts: list[Artifact],
        **kwargs: Any,
    ) -> WorkRequest:
        """Create a sign WorkRequest using asset and signing_input."""
        asset_model = asset.data_model
        assert isinstance(asset_model, SigningKeyData)
        signing_input_ids = [
            signing_input.id for signing_input in signing_input_artifacts
        ]
        return self.playground.create_work_request(
            task_type=TaskTypes.SIGNING,
            task_name="sign",
            task_data=SignData(
                purpose=KeyPurpose.UEFI,
                unsigned=LookupMultiple.parse_obj(signing_input_ids),
                key=asset_model.fingerprint,
            ),
            dynamic_task_data=SignDynamicData(
                unsigned_ids=signing_input_ids,
            ).dict(),
            mark_running=True,
            **kwargs,
        )

    def test_permission_check_on_signing_input(self) -> None:
        workspace = self.playground.get_default_workspace()
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        signing_input = self.playground.create_signing_input_artifact()
        work_request = self.create_sign_work_request(asset, [signing_input])

        self.playground.create_artifact_relation(
            signing_input,
            target=self.playground.create_minimal_binary_package_artifact(),
        )

        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": signing_input.id,
                "work_request_id": work_request.id,
                "workspace": workspace.name,
            },
        )
        body = self.assertAPIResponseOk(response)
        self.assertFalse(body["has_permission"])
        self.assertEqual(body["username"], work_request.created_by.username)
        self.assertEqual(body["user_id"], work_request.created_by.id)
        self.assertEqual(
            body["resource"],
            {
                "architecture": "all",
                "package": "hello",
                "source": "hello",
                "version": "1.0-1",
            },
        )

    def test_permission_check_missing_asset(self) -> None:
        token = self.playground.create_worker_token()
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug="openpgp:ABC123",
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": 1,
                "work_request_id": 1,
                "workspace": "workspace",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        body = response.json()
        self.assertEqual(body["title"], "Asset matching query does not exist.")

    def test_permission_check_malformed_slug(self) -> None:
        token = self.playground.create_worker_token()
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug="foobar",
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": 1,
                "work_request_id": 1,
                "workspace": "workspace",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        body = response.json()
        self.assertEqual(
            body["title"],
            (
                "Malformed asset_slug: not enough values to unpack "
                "(expected 2, got 1)"
            ),
        )

    def test_permission_check_missing_artifact(self) -> None:
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        work_request = self.create_sign_work_request(asset, [])
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": 0,
                "work_request_id": work_request.id,
                "workspace": work_request.workspace.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(
            body["title"], "Artifact matching query does not exist."
        )

    def test_permission_check_missing_work_request(self) -> None:
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        artifact = self.playground.create_signing_input_artifact()
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": artifact.id,
                "work_request_id": 0,
                "workspace": artifact.workspace.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(
            body["title"], "WorkRequest matching query does not exist."
        )

    def test_permission_check_work_request_visible(self) -> None:
        # This is an impossible scenario but it's good to have the check for
        # completeness.
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        artifact = self.playground.create_signing_input_artifact()
        another_scope = self.playground.get_or_create_scope(name="another")
        private_workspace = self.playground.create_workspace(
            scope=another_scope, public=False
        )
        user = self.playground.get_default_user()
        work_request = self.create_sign_work_request(
            asset, [artifact], workspace=private_workspace, created_by=user
        )
        self.assertFalse(work_request.can_display(user))
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": artifact.id,
                "work_request_id": work_request.id,
                "workspace": artifact.workspace.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(
            body["title"],
            f"WorkRequest {work_request.id} is not visible to {user}",
        )

    def test_permission_check_artifact_visible(self) -> None:
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        another_scope = self.playground.get_or_create_scope(name="another")
        private_workspace = self.playground.create_workspace(
            scope=another_scope, public=False
        )
        artifact = self.playground.create_signing_input_artifact(
            workspace=private_workspace
        )
        user = self.playground.get_default_user()
        work_request = self.create_sign_work_request(
            asset, [artifact], created_by=user
        )
        self.assertFalse(artifact.can_display(user))
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": artifact.id,
                "work_request_id": work_request.id,
                "workspace": artifact.workspace.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(
            body["title"], f"Artifact {artifact.id} is not visible to {user}"
        )

    def test_permission_check_artifact_unrelated(self) -> None:
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        artifact = self.playground.create_signing_input_artifact()
        user = self.playground.get_default_user()
        work_request = self.create_sign_work_request(asset, [], created_by=user)
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": artifact.id,
                "work_request_id": work_request.id,
                "workspace": artifact.workspace.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(
            body["title"], f"{artifact} is not an input to {work_request}"
        )

    def test_permission_check_wrong_workspace(self) -> None:
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        artifact = self.playground.create_signing_input_artifact()
        work_request = self.create_sign_work_request(asset, [artifact])
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="sign_with",
            token_key=token.key,
            data={
                "artifact_id": artifact.id,
                "work_request_id": work_request.id,
                "workspace": "another-workspace",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(body["title"], "workspace does not match work request")

    def test_permission_check_unknown_permission(self) -> None:
        token = self.playground.create_worker_token()
        asset = self.playground.create_signing_key_asset()
        artifact = self.playground.create_signing_input_artifact()
        work_request = self.create_sign_work_request(asset, [artifact])
        response = self.post_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset.slug,
            permission_name="unknown",
            token_key=token.key,
            data={
                "artifact_id": artifact.id,
                "work_request_id": work_request.id,
                "workspace": artifact.workspace.name,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertEqual(body["title"], "Unknown permission 'unknown'")

    def test_describe_artifact_repository_index(self) -> None:
        artifact, _ = self.playground.create_artifact(
            paths=["Release"],
            category=ArtifactCategory.REPOSITORY_INDEX,
            create_files=True,
        )
        result = AssetPermissionCheckView.describe_artifact(artifact)
        self.assertEqual(result, {"path": "Release"})

    def test_describe_artifact_signing_input_standalone(self) -> None:
        artifact = self.playground.create_signing_input_artifact()
        with self.assertLogsContains(
            (
                f"INFO:debusine.server.views.assets:Unable to fully describe "
                f"artifact {artifact!r}, no related debian:binary-package."
            ),
            logger=log,
        ):
            result = AssetPermissionCheckView.describe_artifact(artifact)
            self.assertEqual(result, {"package": "hello"})

    def test_describe_artifact_signing_input_multiple_relations(self) -> None:
        artifact = self.playground.create_signing_input_artifact()
        for i in range(2):
            self.playground.create_artifact_relation(
                artifact,
                target=self.playground.create_minimal_binary_package_artifact(),
            )
        with self.assertLogsContains(
            (
                f"INFO:debusine.server.views.assets:Unable to fully describe "
                f"artifact {artifact!r}, multiple related "
                f"debian:binary-package."
            ),
            logger=log,
        ):
            result = AssetPermissionCheckView.describe_artifact(artifact)
            self.assertEqual(result, {"package": "hello"})

    def test_describe_artifact_signing_input_without_name(self) -> None:
        """Looks up the package from the debian:binary-package, if missing."""
        artifact = self.playground.create_signing_input_artifact(
            binary_package_name=None
        )
        self.playground.create_artifact_relation(
            artifact,
            target=self.playground.create_minimal_binary_package_artifact(),
        )
        result = AssetPermissionCheckView.describe_artifact(artifact)
        self.assertEqual(result["package"], "hello")

    def test_describe_artifact_upload_unknown(self) -> None:
        upload_artifacts = self.playground.create_upload_artifacts(binary=False)
        artifact = upload_artifacts.upload
        artifact.data["type"] = "unknown"
        with self.assertRaisesRegex(
            NotImplementedError,
            f"Unable to describe upload artifact {artifact}",
        ):
            AssetPermissionCheckView.describe_artifact(artifact)

    def test_describe_artifact_unknown(self) -> None:
        artifact = self.playground.create_minimal_binary_package_artifact()
        with self.assertRaisesRegex(
            NotImplementedError,
            f"Unable to describe artifact {artifact}",
        ):
            AssetPermissionCheckView.describe_artifact(artifact)
