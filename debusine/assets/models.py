# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used for asset data."""

import abc
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from debusine.utils import DjangoChoicesEnum

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore


class AssetCategory(DjangoChoicesEnum):
    """Possible asset categories."""

    CLOUD_PROVIDER_ACCOUNT = "debusine:cloud-provider-account"
    SIGNING_KEY = "debusine:signing-key"


class BaseAssetDataModel(pydantic.BaseModel, abc.ABC):
    """Base pydantic model for asset data and their components."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid


class ModelCategory(BaseAssetDataModel):
    """All models for a category, organized by discriminator value."""

    discriminator: str | None = None
    models: dict[str | None, type[BaseAssetDataModel]] = {}


_data_models: dict[AssetCategory, ModelCategory] = {}


class BaseRegisteredAssetDataModel(BaseAssetDataModel, abc.ABC):
    """An asset data model registered for use by `asset_data_model`."""

    def __init_subclass__(
        cls,
        category: AssetCategory,
        discriminator: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Register subclass in _data_models."""
        super().__init_subclass__(**kwargs)
        if category not in _data_models:
            _data_models[category] = ModelCategory(discriminator=discriminator)
        modelcategory = _data_models[category]
        assert modelcategory.discriminator == discriminator
        discriminator_value: str | None = None
        if discriminator:
            discriminator_value = cls.__fields__[discriminator].default
        modelcategory.models[discriminator_value] = cls


def asset_data_model(
    category: AssetCategory | str,
    data: dict[str, Any],
) -> BaseAssetDataModel:
    """Load the DataModel for an asset, with specified category and data."""
    category = AssetCategory(category)
    if category not in _data_models:
        raise ValueError(f"No data model for {category} exists.")
    modelcategory = _data_models[category]
    if modelcategory.discriminator is None:
        model = modelcategory.models[None]
    else:
        if modelcategory.discriminator not in data:
            raise ValueError(
                f"Discriminator key {modelcategory.discriminator} "
                f"required for {category} missing in data."
            )
        discriminator_value = data[modelcategory.discriminator]
        if discriminator_value not in modelcategory.models:
            raise ValueError(
                f"No data model for {category} with "
                f"{modelcategory.discriminator}={discriminator_value}."
            )
        model = modelcategory.models[discriminator_value]
    return model.parse_obj(data)


def asset_categories() -> list[AssetCategory]:
    """List all known asset categories."""
    return list(_data_models.keys())


class KeyPurpose(DjangoChoicesEnum):
    """Choices for SigningKey.purpose."""

    UEFI = "uefi"
    OPENPGP = "openpgp"


class SigningKeyData(
    BaseRegisteredAssetDataModel, category=AssetCategory.SIGNING_KEY
):
    """Data for a debusine:signing-key asset."""

    purpose: KeyPurpose
    fingerprint: str
    public_key: str
    description: str | None


class CloudProvidersType(StrEnum):
    """Choices for CloudProviderAccountData.provider_type."""

    AWS = "aws"
    HETZNER = "hetzner"
    DUMMY = "dummy"


class AWSProviderAccountConfiguration(BaseAssetDataModel):
    """Non-secret configuration for an AWS account."""

    region_name: str | None = None
    ec2_endpoint_url: str | None = None
    s3_endpoint_url: str | None = None


class AWSProviderAccountCredentials(BaseAssetDataModel):
    """Secret credentials for an AWS account."""

    access_key_id: str
    secret_access_key: str


class AWSProviderAccountData(
    BaseRegisteredAssetDataModel,
    category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
    discriminator="provider_type",
):
    """Data for a debusine:cloud-provider-account asset for AWS."""

    provider_type: Literal[CloudProvidersType.AWS] = CloudProvidersType.AWS
    name: str
    configuration: AWSProviderAccountConfiguration
    credentials: AWSProviderAccountCredentials


class HetznerProviderAccountConfiguration(BaseAssetDataModel):
    """Non-secret configuration for a Hetzner Cloud account."""

    region_name: str


class HetznerProviderAccountCredentials(BaseAssetDataModel):
    """Secret credentials for a Hetzner Cloud account."""

    api_token: str


class HetznerProviderAccountData(
    BaseRegisteredAssetDataModel,
    category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
    discriminator="provider_type",
):
    """Data for a debusine:cloud-provider-account asset for Hetzner Cloud."""

    provider_type: Literal[CloudProvidersType.HETZNER] = (
        CloudProvidersType.HETZNER
    )
    name: str
    configuration: HetznerProviderAccountConfiguration
    credentials: HetznerProviderAccountCredentials


class DummyProviderAccountData(
    BaseRegisteredAssetDataModel,
    category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
    discriminator="provider_type",
):
    """Data for a debusine:cloud-provider-account asset for test cases."""

    provider_type: Literal[CloudProvidersType.DUMMY] = CloudProvidersType.DUMMY
    name: str
    secret: str = "secret"


CloudProviderAccountData = Annotated[
    Union[
        AWSProviderAccountData,
        HetznerProviderAccountData,
        DummyProviderAccountData,
    ],
    pydantic.Field(discriminator="provider_type"),
]
