# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the Asset artifacts."""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from debusine.assets import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
    AssetCategory,
    KeyPurpose,
    SigningKeyData,
)
from debusine.db.context import context
from debusine.db.models import Asset, Workspace
from debusine.db.models.assets import (
    AssetRoles,
    AssetUsage,
    AssetUsageRole,
    AssetUsageRoles,
    UnknownPermissionError,
)
from debusine.db.models.scopes import ScopeRoles
from debusine.tasks.models import WorkerType
from debusine.test.django import TestCase


class AssetManagerTests(TestCase):
    """Tests for AssetManager and AssetQuerySet."""

    def test_in_current_scope(self) -> None:
        """Test in_current_scope()."""
        scope2 = self.playground.get_or_create_scope("scope2")
        default_workspace = self.playground.get_default_workspace()
        self.playground.create_signing_key_asset()

        with context.local():
            context.set_scope(default_workspace.scope)
            self.assertTrue(Asset.objects.in_current_scope().exists())

        with context.local():
            context.set_scope(scope2)
            self.assertFalse(Asset.objects.in_current_scope().exists())

    def test_can_display(self) -> None:
        """Test can_display() delegates to the workspace."""
        default_workspace = self.playground.get_default_workspace()
        private_scope = self.playground.get_or_create_scope("private")
        private_workspace = self.playground.create_workspace(
            public=False, scope=private_scope
        )
        # No access to private_workspace
        user = self.playground.create_user(username="unprivileged")
        asset = self.playground.create_signing_key_asset()
        self.playground.create_signing_key_asset(workspace=private_workspace)

        with context.local():
            context.set_scope(default_workspace.scope)
            context.set_user(user)
            self.assertEqual(Asset.objects.can_display(user).count(), 1)
            self.assertEqual(Asset.objects.can_display(user).first(), asset)

    def test_can_display_cloud_provider_accounts(self) -> None:
        default_workspace = self.playground.get_default_workspace()
        user = self.playground.get_default_user()
        asset = self.playground.create_asset(
            category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
            workspace=default_workspace,
            data=AWSProviderAccountData(
                name="test",
                configuration=AWSProviderAccountConfiguration(
                    region_name="test-region"
                ),
                credentials=AWSProviderAccountCredentials(
                    access_key_id="access-key", secret_access_key="secret-key"
                ),
            ).dict(),
            created_by=user,
        )
        self.playground.create_group_role(asset, AssetRoles.OWNER, users=[user])

        with context.local():
            context.set_scope(default_workspace.scope)
            context.set_user(user)
            self.assertQuerySetEqual(Asset.objects.can_display(user), [])

    def test_can_manage_permissions(self) -> None:
        """Test can_manage_permissions()."""
        user = self.playground.get_default_user()
        asset = self.playground.create_signing_key_asset()
        self.assertFalse(Asset.objects.can_manage_permissions(user).exists())

        self.playground.create_group_role(asset, AssetRoles.OWNER, users=[user])
        self.assertEqual(
            Asset.objects.can_manage_permissions(user).first(), asset
        )

    def test_get_by_slug(self) -> None:
        asset = self.playground.create_signing_key_asset()
        self.assertEqual(
            Asset.objects.get_by_slug(category=asset.category, slug=asset.slug),
            asset,
        )

    def test_get_by_slug_unknown_category(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"No slug defined for category 'unknown'"
        ):
            Asset.objects.get_by_slug(category="unknown", slug="abc123")


class AssetUsageManagerTests(TestCase):
    """Tests for AssetUsageManager."""

    def test_get_roles_model(self) -> None:
        self.assertEqual(AssetUsage.objects.get_roles_model(), AssetUsageRole)


class AssetTests(TestCase):
    """Tests for Asset."""

    def create_signing_key_asset(self) -> Asset:
        """Create an unsaved signing-key Asset."""
        return Asset(
            category=AssetCategory.SIGNING_KEY,
            workspace=self.playground.get_default_workspace(),
            data=SigningKeyData(
                purpose=KeyPurpose.OPENPGP,
                fingerprint="ABC123",
                public_key="PUBLIC KEY",
                description="A Description",
            ).dict(),
            created_by=self.playground.get_default_user(),
        )

    def create_cloud_provider_account_asset(
        self, workspace: Workspace | None
    ) -> Asset:
        """Create an unsaved cloud-provider-account Asset."""
        return Asset(
            category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
            workspace=workspace,
            data=AWSProviderAccountData(
                name="test",
                configuration=AWSProviderAccountConfiguration(
                    region_name="test-region"
                ),
                credentials=AWSProviderAccountCredentials(
                    access_key_id="access-key", secret_access_key="secret-key"
                ),
            ).dict(),
            created_by=self.playground.get_default_user(),
        )

    def test_signing_key_unique_constraint(self) -> None:
        """Test that signing-key assets have a unique constraint."""
        self.playground.create_signing_key_asset(fingerprint="ABC123")
        with self.assertRaises(IntegrityError):
            self.playground.create_signing_key_asset(fingerprint="ABC123")

    def test_clean(self) -> None:
        """Test that clean() validates the model."""
        asset = self.playground.create_signing_key_asset()
        asset.clean()
        asset.data["something"] = 42
        with self.assertRaisesRegex(
            ValidationError, r"invalid asset category or data"
        ):
            asset.clean()

    def test_slug(self) -> None:
        asset = self.create_signing_key_asset()
        self.assertEqual(asset.slug, "openpgp:ABC123")

    def test_slug_unknown_category(self) -> None:
        asset = self.create_signing_key_asset()
        asset.category = "unknown"
        with self.assertRaisesRegex(
            NotImplementedError, r"No slug defined for category 'unknown'"
        ):
            asset.slug

    def test_can_edit(self) -> None:
        """Test that can_edit() only permits direct owners to edit."""
        asset = self.playground.create_signing_key_asset()
        self.assertFalse(asset.can_edit(AnonymousUser()))
        user = self.playground.get_default_user()
        self.assertFalse(asset.can_edit(user))

        self.playground.create_group_role(asset, AssetRoles.OWNER, users=[user])
        self.assertTrue(asset.can_edit(user))

    def test_can_create(self) -> None:
        """Test that can_create() only permits scope owners to create."""
        asset = self.create_signing_key_asset()
        assert asset.workspace

        self.assertFalse(asset.can_create(AnonymousUser()))
        user = self.playground.get_default_user()
        self.assertFalse(asset.can_create(user))

        self.playground.create_group_role(
            asset.workspace.scope, ScopeRoles.OWNER, users=[user]
        )
        self.assertTrue(asset.can_create(user))

    def test_can_create_worker(self) -> None:
        """Test that can_create() can be used by signing workers."""
        asset = self.create_signing_key_asset()
        assert asset.workspace

        self.assertFalse(asset.can_create(AnonymousUser()))
        ext_worker_token = self.playground.create_worker_token()
        sig_worker_token = self.playground.create_worker_token(
            worker=self.playground.create_worker(worker_type=WorkerType.SIGNING)
        )
        with context.local():
            context.set_scope(asset.workspace.scope)
            context.set_worker_token(ext_worker_token)
            self.assertFalse(asset.can_create(AnonymousUser()))

        with context.local():
            context.set_scope(asset.workspace.scope)
            context.set_worker_token(sig_worker_token)
            self.assertTrue(asset.can_create(AnonymousUser()))

    def test_can_create_no_workspace(self) -> None:
        asset = self.create_cloud_provider_account_asset(workspace=None)
        user = self.playground.get_default_user()
        self.assertFalse(asset.can_create(user))

    def test_can_create_cloud_provider_account(self) -> None:
        asset = self.create_cloud_provider_account_asset(
            workspace=self.playground.get_default_workspace()
        )
        user = self.playground.get_default_user()
        assert asset.workspace
        self.playground.create_group_role(
            asset.workspace.scope, ScopeRoles.OWNER, users=[user]
        )
        self.assertFalse(asset.can_create(user))

    def test_has_permission_direct(self) -> None:
        asset = self.playground.create_signing_key_asset()
        user = self.playground.get_default_user()
        self.assertFalse(asset.has_permission("edit", user))
        self.playground.create_group_role(asset, AssetRoles.OWNER, users=[user])
        self.assertTrue(asset.has_permission("edit", user))

    def test_has_permission_anonymous(self) -> None:
        asset = self.playground.create_signing_key_asset()
        self.assertFalse(asset.has_permission("edit", None))
        self.assertFalse(asset.has_permission("edit", AnonymousUser()))

    def test_has_permission_in_workspace(self) -> None:
        asset = self.playground.create_signing_key_asset()
        user = self.playground.get_default_user()
        workspace = self.playground.get_default_workspace()
        usage = self.playground.create_asset_usage(asset, workspace)
        self.assertFalse(
            asset.has_permission("sign_with", user, workspace=workspace)
        )
        self.playground.create_group_role(
            usage, AssetUsageRoles.SIGNER, users=[user]
        )
        self.assertTrue(
            asset.has_permission("sign_with", user, workspace=workspace)
        )

    def test_has_permission_unknown(self) -> None:
        asset = self.playground.create_signing_key_asset()
        user = self.playground.get_default_user()
        workspace = self.playground.get_default_workspace()
        with self.assertRaises(UnknownPermissionError):
            asset.has_permission("unknown", user, workspace=workspace)

    def test_data_model(self) -> None:
        """Test data_model validations: No data."""
        asset = self.playground.create_signing_key_asset()

        self.assertIsInstance(asset.data_model, SigningKeyData)

    def test_data_model_validation_none(self) -> None:
        """Test data_model validations: No data."""
        asset = self.create_signing_key_asset()
        asset.data = None

        with self.assertRaisesRegex(
            ValidationError, r"data must be a dictionary"
        ):
            asset.data_model

    def test_data_model_validation_category(self) -> None:
        """Test data_model validations: Unknown category."""
        asset = self.create_signing_key_asset()
        asset.category = "unknown"

        with self.assertRaisesRegex(ValidationError, r"invalid asset category"):
            asset.data_model

    def test_save_create(self) -> None:
        """Test that save requires can_create permission for creation."""
        user = self.playground.get_default_user()
        asset = self.create_signing_key_asset()
        assert asset.workspace
        self.playground.create_group_role(
            asset.workspace.scope, ScopeRoles.OWNER, users=[user]
        )
        with context.local():
            context.set_scope(asset.workspace.scope)
            context.set_user(user)
            asset.workspace.set_current()
            self.assertTrue(asset.can_create(user))

            asset.save()

    def test_save_edit(self) -> None:
        """Test that save requires can_edit permission for modification."""
        user = self.playground.get_default_user()
        asset = self.playground.create_signing_key_asset()
        assert asset.workspace
        self.playground.create_group_role(asset, AssetRoles.OWNER, users=[user])
        with context.local():
            context.set_scope(asset.workspace.scope)
            context.set_user(user)
            asset.workspace.set_current()
            self.assertTrue(asset.can_edit(user))

            asset.save()

    def test_str(self) -> None:
        asset = self.create_signing_key_asset()
        self.assertEqual(
            str(asset),
            (
                "Id: None Category: debusine:signing-key "
                "Workspace: debusine/System"
            ),
        )
