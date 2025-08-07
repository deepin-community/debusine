# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used for worker pool data."""

import abc
from enum import StrEnum
from typing import Any, Literal

from debusine.assets import CloudProvidersType

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore


class BaseWorkerPoolDataModel(pydantic.BaseModel, abc.ABC):
    """Base pydantic model for worker pool configuration."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid


_worker_pool_specifications_models: dict[
    CloudProvidersType, type["WorkerPoolSpecifications"]
] = {}


class WorkerPoolSpecifications(BaseWorkerPoolDataModel, abc.ABC):
    """Base class for WorkerPool.specifications data models."""

    provider_type: CloudProvidersType

    def __init_subclass__(
        cls,
        provider_type: CloudProvidersType,
        **kwargs: Any,
    ) -> None:
        """Register subclass in _worker_pool_specifications_models."""
        super().__init_subclass__(**kwargs)
        _worker_pool_specifications_models[provider_type] = cls


def worker_pool_specifications_model(
    data: dict[str, Any],
) -> WorkerPoolSpecifications:
    """Load the WorkerPoolSpecifications subclass model for data."""
    model = _worker_pool_specifications_models[data["provider_type"]]
    return model.parse_obj(data)


class WorkerPoolLimits(BaseWorkerPoolDataModel):
    """Specifications for limits on a WorkerPool."""

    max_active_instances: int | None = None
    target_max_seconds_per_month: int | None = None
    max_idle_seconds: int = 3600


class ScopeWorkerPoolLimits(BaseWorkerPoolDataModel):
    """Specifications for limits on a WorkerPool."""

    target_max_seconds_per_month: int | None = None
    target_latency_seconds: int | None = None


# Test Dummy:


class DummyWorkerPoolSpecification(
    WorkerPoolSpecifications, provider_type=CloudProvidersType.DUMMY
):
    """Specifications for a DummyWorkerPool."""

    provider_type: Literal[CloudProvidersType.DUMMY] = CloudProvidersType.DUMMY
    features: list[str] = []


# Amazon AWS EC2:


class DebianRelease(StrEnum):
    """Debian release that the image is based on."""

    BOOKWORM = "bookworm"
    TRIXIE = "trixie"
    FORKY = "forky"


class DebusineInstallSource(StrEnum):
    """Source for installing debusine-worker."""

    RELEASE = "release"
    BACKPORTS = "backports"
    DAILY_BUILDS = "daily-builds"
    PRE_INSTALLED = "pre-installed"


class AWSEC2InstanceMarketType(StrEnum):
    """Market to purche AWS EC2 instances in."""

    ON_DEMAND = "on-demand"
    SPOT = "spot"


class MinMaxRequirements(BaseWorkerPoolDataModel):
    """Minimum and Optional Maximum Requirements."""

    Min: int
    Max: int | None = None


class AWSInstanceRequirementFilterEnum(StrEnum):
    """Values for AWSEC2InstanceRequirements options that select features."""

    INCLUDED = "included"
    EXCLUDED = "excluded"
    REQUIRED = "required"


class AWSEC2InstanceRequirements(BaseWorkerPoolDataModel):
    """AWS EC2 Instance Requirements."""

    VCpuCount: MinMaxRequirements
    MemoryMiB: MinMaxRequirements
    MemoryGiBPerVCpu: MinMaxRequirements | None = None
    SpotMaxPricePercentageOverLowestPrice: int | None = None
    MaxSpotPriceAsPercentageOfOptimalOnDemandPrice: int | None = None
    BurstablePerformance: AWSInstanceRequirementFilterEnum = (
        AWSInstanceRequirementFilterEnum.EXCLUDED
    )


class AWSEC2NetworkInterface(BaseWorkerPoolDataModel):
    """AWS EC2 Network Interface."""

    AssociatePublicIpAddress: bool | None = None
    DeleteOnTermination: bool = True
    DeviceIndex: int = 0
    Ipv6AddressCount: int | None = None
    SubnetId: str | None = None
    Groups: list[str] | None = None

    @pydantic.validator("Groups")
    def validate_groups(
        cls, v: list[str] | None  # noqa: U100
    ) -> list[str] | None:
        """Ensure Groups is not an empty list."""
        if v is None or len(v) == 0:
            return None
        return v


class AWSEC2LaunchTemplate(BaseWorkerPoolDataModel):
    """AWS EC2 Launch Template."""

    EbsOptimized: bool = False
    ImageId: str
    KeyName: str | None = None
    InstanceType: str | None = None
    InstanceRequirements: AWSEC2InstanceRequirements | None = None
    NetworkInterfaces: list[AWSEC2NetworkInterface] | None = None
    # In GiB:
    root_device_size: int | None = None
    swap_size: int | None = None
    tags: dict[str, str] = {}

    @pydantic.validator("NetworkInterfaces")
    def validate_network_interfaces(
        cls, v: list[AWSEC2NetworkInterface] | None  # noqa: U100
    ) -> list[AWSEC2NetworkInterface] | None:
        """Ensure NetworkInterfaces have unique indices and not empty list."""
        if v is None or len(v) == 0:
            return None
        if len({interface.DeviceIndex for interface in v}) < len(v):
            raise ValueError(
                "Each NetworkInterface's DeviceIndex must be unique"
            )
        return v

    @pydantic.root_validator(pre=True)
    @classmethod
    def instance_type_or_requirements(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Only one of InstanceType or InstanceRequirements is specified."""
        type_defined = values.get("InstanceType") is not None
        req_defined = values.get("InstanceRequirements") is not None
        if type_defined and req_defined:
            raise ValueError(
                "If InstanceType is specified, InstanceRequirements cannot be"
            )
        if not type_defined and not req_defined:
            raise ValueError(
                "InstanceType or InstanceRequirements must be specified"
            )
        return values


class AWSEC2WorkerPoolSpecification(
    WorkerPoolSpecifications, provider_type=CloudProvidersType.AWS
):
    """Specifications for an AWS EC2 WorkerPool."""

    provider_type: Literal[CloudProvidersType.AWS] = CloudProvidersType.AWS
    launch_templates: list[AWSEC2LaunchTemplate]
    instance_market_type: AWSEC2InstanceMarketType = (
        AWSEC2InstanceMarketType.SPOT
    )
    # Spot:
    max_spot_price_per_hour: float | None = None
    debian_release: DebianRelease | None = None
    debusine_install_source: DebusineInstallSource = (
        DebusineInstallSource.RELEASE
    )


# Hetzner Cloud:


class HetznerCloudWorkerPoolSpecification(
    WorkerPoolSpecifications, provider_type=CloudProvidersType.HETZNER
):
    """Specifications for a Hetzner Cloud WorkerPool."""

    provider_type: Literal[CloudProvidersType.HETZNER] = (
        CloudProvidersType.HETZNER
    )
    server_type: str
    image_name: str
    ssh_keys: list[str] = []
    networks: list[str] = []
    location: str | None = None
    labels: dict[str, str] = {}
    enable_ipv4: bool = True
    enable_ipv6: bool = True
    debian_release: DebianRelease | None = None
    debusine_install_source: DebusineInstallSource = (
        DebusineInstallSource.RELEASE
    )
