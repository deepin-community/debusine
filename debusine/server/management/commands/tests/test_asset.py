# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command: asset."""
import io

import yaml
from django.conf import settings
from django.core.management import CommandError

from debusine.assets.models import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
    AssetCategory,
    CloudProvidersType,
    KeyPurpose,
)
from debusine.db.models import Asset
from debusine.db.models.assets import AssetRoles, AssetUsageRoles
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class AssetCommandsTests(TabularOutputTests, TestCase):
    """Tests for asset management commands."""

    def test_create_from_file(self) -> None:
        data = {
            "provider_type": CloudProvidersType.AWS,
            "name": "test",
            "configuration": {"region_name": "test-region"},
            "credentials": {
                "access_key_id": "access-key",
                "secret_access_key": "secret-key",
            },
        }
        data_file = self.create_temporary_file(
            contents=yaml.safe_dump(data).encode()
        )
        stdout, stderr, exit_code = call_command(
            "asset",
            "create",
            AssetCategory.CLOUD_PROVIDER_ACCOUNT,
            "--data",
            str(data_file),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        asset = Asset.objects.get(
            category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, data__name="test"
        )
        self.assertEqual(asset.data, data)
        self.assertIsNone(asset.workspace)

    def test_create_from_stdin(self) -> None:
        data = {
            "provider_type": CloudProvidersType.AWS,
            "name": "test",
            "configuration": {"region_name": "test-region"},
            "credentials": {
                "access_key_id": "access-key",
                "secret_access_key": "secret-key",
            },
        }
        stdout, stderr, exit_code = call_command(
            "asset",
            "create",
            AssetCategory.CLOUD_PROVIDER_ACCOUNT,
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        asset = Asset.objects.get(
            category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, data__name="test"
        )
        self.assertEqual(asset.data, data)
        self.assertIsNone(asset.workspace)

    def test_create_updates_existing_cloud_provider_account(self) -> None:
        self.playground.create_cloud_provider_account_asset()
        other_asset = self.playground.create_cloud_provider_account_asset(
            data=AWSProviderAccountData(
                name="other",
                configuration=AWSProviderAccountConfiguration(
                    region_name="test-region"
                ),
                credentials=AWSProviderAccountCredentials(
                    access_key_id="other-access-key",
                    secret_access_key="other-secret-key",
                ),
            )
        )
        data = {
            "provider_type": CloudProvidersType.AWS,
            "name": "test",
            "configuration": {"region_name": "new-region"},
            "credentials": {
                "access_key_id": "new-access-key",
                "secret_access_key": "new-secret-key",
            },
        }

        stdout, stderr, exit_code = call_command(
            "asset",
            "create",
            AssetCategory.CLOUD_PROVIDER_ACCOUNT,
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        asset = Asset.objects.get(
            category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, data__name="test"
        )
        self.assertEqual(asset.data, data)
        self.assertIsNone(asset.workspace)
        self.assertEqual(
            Asset.objects.get(
                category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
                data__name="other",
            ).data,
            other_asset.data,
        )

    def test_create_updates_existing_signing_key(self) -> None:
        asset = self.playground.create_signing_key_asset(
            purpose=KeyPurpose.OPENPGP, fingerprint="0123"
        )
        other_asset = self.playground.create_signing_key_asset(
            purpose=KeyPurpose.OPENPGP, fingerprint="4567"
        )
        data = {
            "purpose": KeyPurpose.OPENPGP,
            "fingerprint": "0123",
            "public_key": "a public key",
            "description": "a description",
        }

        stdout, stderr, exit_code = call_command(
            "asset",
            "create",
            "--workspace",
            f"{settings.DEBUSINE_DEFAULT_SCOPE}/"
            f"{settings.DEBUSINE_DEFAULT_WORKSPACE}",
            AssetCategory.SIGNING_KEY,
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        asset = Asset.objects.get(
            category=AssetCategory.SIGNING_KEY, data__fingerprint="0123"
        )
        self.assertEqual(asset.data, data)
        self.assertEqual(
            asset.workspace, self.playground.get_default_workspace()
        )
        self.assertEqual(
            Asset.objects.get(
                category=AssetCategory.SIGNING_KEY, data__fingerprint="4567"
            ).data,
            other_asset.data,
        )

    def test_create_validation_error(self) -> None:
        with self.assertRaisesRegex(
            CommandError,
            r"^Error creating asset: debusine:cloud-provider-account: "
            r"invalid asset category or data: .* validation errors for "
            r"AWSProviderAccountData",
        ) as exc:
            stdout, stderr, exit_code = call_command(
                "asset",
                "create",
                AssetCategory.CLOUD_PROVIDER_ACCOUNT,
                stdin=io.StringIO(
                    yaml.safe_dump(
                        {
                            "provider_type": CloudProvidersType.AWS,
                            "name": "test",
                        }
                    )
                ),
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(
            Asset.objects.filter(
                category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, data__name="test"
            ).exists()
        )

    def test_create_integrity_error(self) -> None:
        with self.assertRaisesRegex(
            CommandError,
            r'^Error creating asset: new row for relation "db_asset" violates '
            r'check constraint "db_asset_workspace_not_null"',
        ) as exc:
            call_command(
                "asset",
                "create",
                AssetCategory.SIGNING_KEY,
                stdin=io.StringIO(
                    yaml.safe_dump(
                        {
                            "purpose": KeyPurpose.OPENPGP,
                            "fingerprint": "0123",
                            "public_key": "a public key",
                            "description": "a description",
                        }
                    )
                ),
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(
            Asset.objects.filter(
                category=AssetCategory.SIGNING_KEY, data__fingerprint="0123"
            ).exists()
        )

    def test_delete_asset(self) -> None:
        asset = self.playground.create_signing_key_asset()

        stdout, stderr, exit_code = call_command(
            "asset",
            "delete",
            str(asset.id),
            "--yes",
        )
        self.assertEqual("", stdout)
        self.assertEqual(exit_code, 0)

        with self.assertRaises(Asset.DoesNotExist):
            Asset.objects.get(id=asset.id)

    def test_delete_asset_confirmation(self) -> None:
        asset = self.playground.create_signing_key_asset()

        stdout, stderr, exit_code = call_command(
            "asset",
            "delete",
            str(asset.id),
            stdin=io.StringIO("N\n"),
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stdout, f"Would you like to delete asset {asset}? [yN] "
        )
        self.assertEqual(stderr, "Not deleted.\n")
        self.assertTrue(Asset.objects.filter(id=asset.id).exists())

        call_command(
            "asset",
            "delete",
            str(asset.id),
            stdin=io.StringIO("\n"),
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stdout, f"Would you like to delete asset {asset}? [yN] "
        )
        self.assertEqual(stderr, "Not deleted.\n")
        self.assertTrue(Asset.objects.filter(id=asset.id).exists())

    def test_delete_does_not_exist(self) -> None:
        """Attempt to delete a non-existing asset."""
        with self.assertRaisesRegex(CommandError, r"Asset ID '-1' not found"):
            call_command(
                "asset",
                "delete",
                "-1",
                "--yes",
            )

    def test_list(self) -> None:
        default_scope = self.playground.get_default_scope()
        second_workspace = self.playground.create_workspace(name="second")
        another_scope = self.playground.get_or_create_scope(name="another")
        another_workspace = self.playground.create_workspace(
            name="default", scope=another_scope
        )
        assets = [
            self.playground.create_signing_key_asset(fingerprint="abc"),
            self.playground.create_signing_key_asset(
                fingerprint="def", workspace=second_workspace
            ),
            self.playground.create_signing_key_asset(
                fingerprint="ghi", workspace=another_workspace
            ),
        ]
        expected_data = [
            {
                "id": asset.id,
                "category": asset.category,
                "workspace": str(asset.workspace),
                "data": {
                    "fingerprint": asset.data["fingerprint"],
                    "purpose": asset.data["purpose"].value,
                },
            }
            for asset in assets
        ]

        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command("asset", "list")
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.col(0), [str(expected["id"]) for expected in expected_data]
        )
        self.assertEqual(
            output.col(1), [expected["category"] for expected in expected_data]
        )
        self.assertEqual(
            output.col(2), [expected["workspace"] for expected in expected_data]
        )
        self.assertEqual(
            output.col(3), [str(expected["data"]) for expected in expected_data]
        )

        stdout, stderr, exit_code = call_command("asset", "list", "--yaml")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        data = yaml.safe_load(stdout)
        self.assertEqual(data, expected_data)

        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "asset", "list", f"--workspace={second_workspace}"
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(output.col(0), [str(assets[1].id)])

        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "asset",
                "list",
                f"--workspace={second_workspace}",
                f"--scope={default_scope}",
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(output.col(0), [str(assets[1].id)])

        with self.assertRaisesRegex(
            CommandError,
            r"<Workspace: debusine/second> is not in <Scope: another>",
        ):
            stdout, stderr, exit_code = call_command(
                "asset",
                "list",
                f"--workspace={second_workspace}",
                f"--scope={another_scope}",
            )

        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command(
                "asset", "list", f"--scope={default_scope}"
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(output.col(0), [str(assets[0].id), str(assets[1].id)])

    def test_list_roles(self) -> None:
        another_workspace = self.playground.create_workspace(name="another")
        user1 = self.playground.create_user(username="user1")
        user2 = self.playground.create_user(username="user2")
        asset = self.playground.create_signing_key_asset()
        self.playground.create_group_role(
            asset,
            AssetRoles.OWNER,
            users=[user1],
            name="asset-owners",
        )
        asset_usage = self.playground.create_asset_usage(
            asset, workspace=another_workspace
        )
        self.playground.create_group_role(
            asset_usage,
            AssetUsageRoles.SIGNER,
            users=[user2],
            name="asset-signers",
        )
        expected_data = [
            [
                {
                    "group": "asset-owners",
                    "role": "owner",
                },
            ],
            [
                {
                    "workspace": "another",
                    "group": "asset-signers",
                    "role": "signer",
                },
            ],
        ]

        with self.assertPrintsTables() as output:
            stdout, stderr, exit_code = call_command(
                "asset", "list_roles", str(asset.id)
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0].col(0), ["asset-owners"])
        self.assertEqual(output[0].col(1), ["owner"])
        self.assertEqual(output[1].col(0), ["another"])
        self.assertEqual(output[1].col(1), ["asset-signers"])
        self.assertEqual(output[1].col(2), ["signer"])

        stdout, stderr, exit_code = call_command(
            "asset", "list_roles", str(asset.id), "--yaml"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        data = list(yaml.safe_load_all(stdout))
        self.assertEqual(data, expected_data)

    def test_grant_role_direct(self) -> None:
        group1 = self.playground.create_group(name="group1")
        asset = self.playground.create_signing_key_asset()
        stdout, stderr, exit_code = call_command(
            "asset", "grant_role", str(asset.id), "owner", group1.name
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

        self.assertTrue(
            asset.roles.filter(group=group1, role=AssetRoles.OWNER).exists()
        )
        self.assertFalse(asset.usage.all().exists())

    def test_grant_role_usage(self) -> None:
        workspace1 = self.playground.create_workspace(name="workspace1")
        group1 = self.playground.create_group(name="group1")
        asset = self.playground.create_signing_key_asset()
        stdout, stderr, exit_code = call_command(
            "asset",
            "grant_role",
            str(asset.id),
            "--workspace",
            str(workspace1),
            "signer",
            group1.name,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

        usage = asset.usage.get(workspace=workspace1)
        self.assertTrue(
            usage.roles.filter(
                group=group1, role=AssetUsageRoles.SIGNER
            ).exists()
        )
        self.assertFalse(asset.roles.filter(group=group1).exists())

    def test_grant_role_unknown_role(self) -> None:
        group1 = self.playground.create_group(name="group1")
        asset = self.playground.create_signing_key_asset()
        with self.assertRaisesRegex(
            CommandError, r"'unknown' is not a valid AssetRoles"
        ):
            call_command(
                "asset", "grant_role", str(asset.id), "unknown", group1.name
            )
        self.assertFalse(asset.roles.all().exists())

    def test_grant_role_unknown_group(self) -> None:
        asset = self.playground.create_signing_key_asset()
        with self.assertRaisesRegex(
            CommandError, r"Group 'unknown' not found in scope 'debusine'"
        ):
            call_command(
                "asset", "grant_role", str(asset.id), "owner", "unknown"
            )
        self.assertFalse(asset.roles.all().exists())

    def test_grant_role_unknown_scope(self) -> None:
        asset = self.playground.create_signing_key_asset()
        group1 = self.playground.create_group(name="group1")
        with self.assertRaisesRegex(CommandError, r"Scope 'unknown' not found"):
            call_command(
                "asset",
                "grant_role",
                str(asset.id),
                "--workspace",
                "unknown/foo",
                "signer",
                group1.name,
            )
        self.assertFalse(asset.usage.all().exists())

    def test_grant_role_unknown_workspace(self) -> None:
        asset = self.playground.create_signing_key_asset()
        group1 = self.playground.create_group(name="group1")
        with self.assertRaisesRegex(
            CommandError, r"Workspace 'unknown' not found in scope 'debusine'"
        ):
            call_command(
                "asset",
                "grant_role",
                str(asset.id),
                "--workspace",
                "debusine/unknown",
                "signer",
                group1.name,
            )
        self.assertFalse(asset.usage.all().exists())

    def test_grant_role_invalid_scope(self) -> None:
        asset = self.playground.create_signing_key_asset()
        group1 = self.playground.create_group(name="group1")
        with self.assertRaisesRegex(
            CommandError,
            (
                r"scope_workspace 'unknown' should be in the form "
                r"'scopename/workspacename'"
            ),
        ):
            call_command(
                "asset",
                "grant_role",
                str(asset.id),
                "--workspace",
                "unknown",
                "signer",
                group1.name,
            )
        self.assertFalse(asset.usage.all().exists())

    def test_grant_role_without_workspace(self) -> None:
        asset = self.playground.create_cloud_provider_account_asset()
        group1 = self.playground.create_group(name="group1")
        with self.assertRaisesRegex(
            CommandError,
            "Cannot grant roles on an asset not assigned to a workspace",
        ):
            call_command(
                "asset",
                "grant_role",
                str(asset.id),
                "owner",
                group1.name,
            )
        self.assertFalse(asset.roles.all().exists())

    def test_grant_role_usage_without_workspace(self) -> None:
        workspace1 = self.playground.create_workspace(name="workspace1")
        asset = self.playground.create_cloud_provider_account_asset()
        group1 = self.playground.create_group(name="group1")
        call_command(
            "asset",
            "grant_role",
            str(asset.id),
            "--workspace",
            str(workspace1),
            "signer",
            group1.name,
        )
        usage = asset.usage.get(workspace=workspace1)
        self.assertTrue(
            usage.roles.filter(
                group=group1, role=AssetUsageRoles.SIGNER
            ).exists()
        )

    def test_revoke_role_direct(self) -> None:
        asset = self.playground.create_signing_key_asset()
        user1 = self.playground.create_user(username="user1")
        group1 = self.playground.create_group_role(
            asset, AssetRoles.OWNER, users=[user1]
        )
        stdout, stderr, exit_code = call_command(
            "asset", "revoke_role", str(asset.id), "owner", group1.name
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

        self.assertFalse(asset.roles.all().exists())

    def test_revoke_role_usage(self) -> None:
        asset = self.playground.create_signing_key_asset()
        workspace1 = self.playground.create_workspace(name="workspace1")
        asset_usage = self.playground.create_asset_usage(
            asset, workspace=workspace1
        )
        user1 = self.playground.create_user(username="user1")
        group1 = self.playground.create_group_role(
            asset_usage, AssetUsageRoles.SIGNER, users=[user1]
        )
        stdout, stderr, exit_code = call_command(
            "asset",
            "revoke_role",
            str(asset.id),
            "--workspace",
            str(workspace1),
            "signer",
            group1.name,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

        self.assertFalse(asset_usage.roles.all().exists())

    def test_revoke_role_without_workspace(self) -> None:
        asset = self.playground.create_cloud_provider_account_asset()
        group1 = self.playground.create_group(name="group1")
        with self.assertRaisesRegex(
            CommandError,
            "Cannot revoke roles on an asset not assigned to a workspace",
        ):
            call_command(
                "asset",
                "revoke_role",
                str(asset.id),
                "owner",
                group1.name,
            )
        self.assertFalse(asset.roles.all().exists())

    def test_revoke_role_usage_without_workspace(self) -> None:
        workspace1 = self.playground.create_workspace(name="workspace1")
        asset = self.playground.create_cloud_provider_account_asset()
        asset_usage = self.playground.create_asset_usage(
            asset, workspace=workspace1
        )
        user1 = self.playground.create_user(username="user1")
        group1 = self.playground.create_group_role(
            asset_usage, AssetUsageRoles.SIGNER, users=[user1]
        )
        call_command(
            "asset",
            "revoke_role",
            str(asset.id),
            "--workspace",
            str(workspace1),
            "signer",
            group1.name,
        )
        self.assertFalse(
            asset_usage.roles.filter(
                group=group1, role=AssetUsageRoles.SIGNER
            ).exists()
        )
