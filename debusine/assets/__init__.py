# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used for asset data."""

from debusine.assets.models import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
    AssetCategory,
    BaseAssetDataModel,
    CloudProviderAccountData,
    CloudProvidersType,
    DummyProviderAccountData,
    HetznerProviderAccountConfiguration,
    HetznerProviderAccountCredentials,
    HetznerProviderAccountData,
    KeyPurpose,
    SigningKeyData,
    asset_data_model,
)

__all__ = [
    "AWSProviderAccountConfiguration",
    "AWSProviderAccountCredentials",
    "AWSProviderAccountData",
    "AssetCategory",
    "BaseAssetDataModel",
    "CloudProviderAccountData",
    "CloudProvidersType",
    "DummyProviderAccountData",
    "HetznerProviderAccountConfiguration",
    "HetznerProviderAccountCredentials",
    "HetznerProviderAccountData",
    "KeyPurpose",
    "SigningKeyData",
    "asset_data_model",
]
