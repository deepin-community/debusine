# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test Asset data models."""

from unittest import TestCase, mock

from debusine.assets import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
    AssetCategory,
    KeyPurpose,
    SigningKeyData,
    asset_categories,
    asset_data_model,
)


class TestBaseAssetDataModel(TestCase):
    """Tests for BaseAssetDataModel."""

    def test_asset_data_model(self) -> None:
        """Test lookup of a known category."""
        data = SigningKeyData(
            purpose=KeyPurpose.UEFI,
            fingerprint="ABC123",
            public_key="PUBLIC",
            description="A Test Signing Key",
        )
        model = asset_data_model(AssetCategory.SIGNING_KEY, data.dict())
        self.assertEqual(model, data)

    def test_asset_data_model_unknown(self) -> None:
        """Test lookup of an unknown category."""
        with (
            # Replace AssetCategory() with a noop lookup
            mock.patch("debusine.assets.models.AssetCategory", new=str),
            self.assertRaisesRegex(
                ValueError, r"No data model for unknown exists\."
            ),
        ):
            asset_data_model("unknown", {})

    def test_asset_data_model_discriminator(self) -> None:
        data = AWSProviderAccountData(
            name="test",
            configuration=AWSProviderAccountConfiguration(
                region_name="test-region"
            ),
            credentials=AWSProviderAccountCredentials(
                access_key_id="access-key", secret_access_key="secret-key"
            ),
        )
        model = asset_data_model(
            AssetCategory.CLOUD_PROVIDER_ACCOUNT, data.dict()
        )
        self.assertEqual(model, data)

    def test_asset_data_model_discriminator_missing(self) -> None:
        with (
            self.assertRaisesRegex(
                ValueError,
                (
                    r"Discriminator key provider_type required for "
                    r"debusine:cloud-provider-account missing in data\."
                ),
            ),
        ):
            asset_data_model(AssetCategory.CLOUD_PROVIDER_ACCOUNT, {})

    def test_asset_data_model_discriminator_unknown(self) -> None:
        with (
            self.assertRaisesRegex(
                ValueError,
                (
                    r"No data model for debusine:cloud-provider-account with "
                    r"provider_type=unknown\."
                ),
            ),
        ):
            asset_data_model(
                AssetCategory.CLOUD_PROVIDER_ACCOUNT,
                {"provider_type": "unknown"},
            )

    def test_asset_categories(self) -> None:
        categories = asset_categories()
        self.assertIn(AssetCategory.CLOUD_PROVIDER_ACCOUNT, categories)
        self.assertIn(AssetCategory.SIGNING_KEY, categories)
