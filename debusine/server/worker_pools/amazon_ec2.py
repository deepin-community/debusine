# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""API for provisioning and managing AWS EC2 cloud workers."""

from functools import lru_cache
from typing import Any, Literal, TYPE_CHECKING, cast

import boto3
from botocore.exceptions import ClientError
from django.utils import timezone

from debusine.assets import AWSProviderAccountData, CloudProvidersType
from debusine.db.models import Token
from debusine.server.worker_pools.base import WorkerPoolInterface
from debusine.server.worker_pools.cloud_init import (
    CloudInitConfig,
    FilesystemSetup,
    worker_bootstrap_cloud_init,
)
from debusine.server.worker_pools.exceptions import InstanceLaunchException
from debusine.server.worker_pools.models import (
    AWSEC2InstanceMarketType,
    AWSEC2LaunchTemplate,
    AWSEC2WorkerPoolSpecification,
)

if TYPE_CHECKING:
    from mypy_boto3_ec2.client import EC2Client
    from mypy_boto3_ec2.type_defs import (
        FleetLaunchTemplateConfigRequestTypeDef,
        InstanceRequirementsRequestTypeDef,
        LaunchTemplateBlockDeviceMappingRequestTypeDef,
        LaunchTemplateInstanceNetworkInterfaceSpecificationRequestTypeDef,
        LaunchTemplateTagSpecificationRequestTypeDef,
        RequestLaunchTemplateDataTypeDef,
        SpotOptionsRequestTypeDef,
    )

    from debusine.db.models import Worker

    # fake usage for vulture
    EC2Client
else:
    # They're all TypedDicts
    FleetLaunchTemplateConfigRequestTypeDef = dict
    InstanceRequirementsRequestTypeDef = dict
    LaunchTemplateBlockDeviceMappingRequestTypeDef = dict
    LaunchTemplateTagSpecificationRequestTypeDef = dict
    RequestLaunchTemplateDataTypeDef = dict
    SpotOptionsRequestTypeDef = dict
    LaunchTemplateInstanceNetworkInterfaceSpecificationRequestTypeDef = dict


class AWSEC2WorkerPool(
    WorkerPoolInterface, cloud_provider=CloudProvidersType.AWS
):
    """Amazon EC2 Worker Pool."""

    specifications: AWSEC2WorkerPoolSpecification
    client: "EC2Client"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize an AWSEC2WorkerPool."""
        super().__init__(*args, **kwargs)
        provider_account_data = self.worker_pool.provider_account.data_model
        assert isinstance(provider_account_data, AWSProviderAccountData)
        self.client = self._make_client(provider_account_data)

    def launch_worker(self, worker: "Worker") -> None:
        """Launch a cloud worker using worker."""
        assert worker.activation_token

        self._terminate_existing_instances(worker.worker_pool_data)

        specs = self.specifications
        launch_template_ids = []
        try:
            # We support multiple templates, to allow finding the cheapest
            # instance even if different instances require different
            # configuration
            for id_, launch_template in enumerate(specs.launch_templates):
                name = f"debusine_worker_{worker.name}_{id_}"
                try:
                    # If there was a left-over template from before, delete it
                    self.client.delete_launch_template(LaunchTemplateName=name)
                except ClientError:
                    pass
                launch_template_ids.append(
                    self._create_launch_template(
                        name,
                        self._launch_template_data(
                            launch_template, worker.activation_token
                        ),
                    )
                )
            instance_id = self._create_fleet(
                launch_template_ids,
                instance_market_type=specs.instance_market_type,
                worker_name=worker.name,
                max_spot_price_per_hour=specs.max_spot_price_per_hour,
            )
        finally:
            for template_id in launch_template_ids:
                self.client.delete_launch_template(LaunchTemplateId=template_id)

        worker.instance_created_at = timezone.now()
        worker.worker_pool_data = {"instance-id": instance_id}
        worker.save()

    def terminate_worker(self, worker: "Worker") -> None:
        """Terminate the cloud worker specified."""
        self._terminate_existing_instances(worker.worker_pool_data)
        worker.worker_pool_data = {}
        worker.instance_created_at = None
        worker.save()

    def _make_client(
        self, provider_account_data: AWSProviderAccountData
    ) -> "EC2Client":
        """Make an EC2 client for this provider account."""
        return boto3.client(
            "ec2",
            region_name=provider_account_data.configuration.region_name,
            endpoint_url=provider_account_data.configuration.ec2_endpoint_url,
            aws_access_key_id=provider_account_data.credentials.access_key_id,
            aws_secret_access_key=(
                provider_account_data.credentials.secret_access_key
            ),
        )

    def _terminate_existing_instances(
        self, worker_pool_data: dict[str, Any] | None
    ) -> None:
        """
        If worker_pool_data references an instance-id, terminate it.

        We are often working with spot instances that vanish unexpectedly, just
        ignore failures for unknown instances.
        """
        if worker_pool_data is None:
            return
        if existing_instance := worker_pool_data.get("instance-id", None):
            try:
                self.client.terminate_instances(InstanceIds=[existing_instance])
            except ClientError as e:
                if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                    return
                raise

    def _cloud_init_config(
        self, launch_template: AWSEC2LaunchTemplate, activation_token: Token
    ) -> CloudInitConfig:
        """Generate cloud-init configuration for a new instance."""
        config = worker_bootstrap_cloud_init(
            activation_token,
            self.specifications.debusine_install_source,
            self.specifications.debian_release,
        )
        if launch_template.swap_size:
            config.device_aliases["swap"] = "/dev/xvdb"
            config.fs_setup.append(
                FilesystemSetup(device="swap", filesystem="swap")
            )
            config.mounts.append(
                [
                    "swap",
                    "none",
                    "swap",
                    "sw,nofail,x-systemd.requires=cloud-init.service",
                ]
            )
        return config

    @lru_cache
    def _lookup_image_root_device_name(self, image_id: str) -> str:
        """Look up the DeviceName of the root filesystem in the image."""
        response = self.client.describe_images(ImageIds=[image_id])
        images = response["Images"]
        if len(images) != 1:
            raise ValueError(
                f"Found {len(images)} images instead of the expected 1"
            )
        image = images[0]

        if image["RootDeviceType"] != "ebs":
            raise ValueError(f"Image {image_id} is not EBS-backed")
        return image["RootDeviceName"]

    def _launch_template_data(
        self, launch_template: AWSEC2LaunchTemplate, activation_token: Token
    ) -> RequestLaunchTemplateDataTypeDef:
        """
        Generate a low-level LaunchTemplateData from launch_template.

        AWSEC2LaunchTemplate includes some low-level properties directly, but
        also provides higher level abstractions.
        """
        block_device_mappings: list[
            LaunchTemplateBlockDeviceMappingRequestTypeDef
        ] = []
        if launch_template.root_device_size:
            block_device_mappings.append(
                {
                    "DeviceName": self._lookup_image_root_device_name(
                        launch_template.ImageId
                    ),
                    "Ebs": {
                        "VolumeSize": launch_template.root_device_size,
                        "DeleteOnTermination": True,
                        "VolumeType": "gp3",
                    },
                }
            )
        if launch_template.swap_size:
            block_device_mappings.append(
                {
                    "DeviceName": "/dev/xvdb",
                    "Ebs": {
                        "VolumeSize": launch_template.swap_size,
                        "DeleteOnTermination": True,
                        "VolumeType": "gp3",
                    },
                },
            )
        cloud_init_config = self._cloud_init_config(
            launch_template, activation_token
        )

        tag_specifications: list[
            LaunchTemplateTagSpecificationRequestTypeDef
        ] = []
        if launch_template.tags:
            tag_specifications.append(
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": key, "Value": value}
                        for key, value in launch_template.tags.items()
                    ],
                }
            )

        data: RequestLaunchTemplateDataTypeDef = {
            "EbsOptimized": launch_template.EbsOptimized,
            "BlockDeviceMappings": block_device_mappings,
            "ImageId": launch_template.ImageId,
            "UserData": cloud_init_config.as_base64_str(),
            "TagSpecifications": tag_specifications,
        }

        if launch_template.InstanceRequirements:
            data["InstanceRequirements"] = cast(
                InstanceRequirementsRequestTypeDef,
                launch_template.InstanceRequirements.dict(exclude_unset=True),
            )
        else:
            # mypy_boto3 types this as a very long set of Literal options
            data["InstanceType"] = launch_template.InstanceType  # type: ignore

        if launch_template.KeyName:
            data["KeyName"] = launch_template.KeyName
        if launch_template.NetworkInterfaces:
            data["NetworkInterfaces"] = [
                cast(
                    LaunchTemplateInstanceNetworkInterfaceSpecificationRequestTypeDef,  # noqa: E501
                    interface.dict(exclude_none=True),
                )
                for interface in launch_template.NetworkInterfaces
            ]

        return data

    def _create_launch_template(
        self,
        name: str,
        launch_template_data: RequestLaunchTemplateDataTypeDef,
    ) -> str:
        """
        Create an EC2 Launch template, and return its ID.

        We have to create server-side named templates, to be able to pass
        UserData: https://github.com/aws/aws-sdk/issues/528
        """
        response = self.client.create_launch_template(
            LaunchTemplateName=name,
            LaunchTemplateData=launch_template_data,
        )
        return response["LaunchTemplate"]["LaunchTemplateId"]

    def _create_fleet(
        self,
        launch_template_ids: list[str],
        instance_market_type: AWSEC2InstanceMarketType,
        worker_name: str,
        max_spot_price_per_hour: float | None,
    ) -> str:
        """Create an EC2 instance using create_fleet, and return its ID."""
        if not launch_template_ids:
            raise ValueError("No launch_template_ids were provided")
        launch_template_configs: list[
            FleetLaunchTemplateConfigRequestTypeDef
        ] = []
        for launch_template_id in launch_template_ids:
            launch_template_configs.append(
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": "$Latest",
                    },
                }
            )
        spot_options: SpotOptionsRequestTypeDef = {}
        if (
            instance_market_type == AWSEC2InstanceMarketType.SPOT
            and max_spot_price_per_hour
        ):
            spot_options["MaxTotalPrice"] = str(max_spot_price_per_hour)

        response = self.client.create_fleet(
            SpotOptions=spot_options,
            LaunchTemplateConfigs=launch_template_configs,
            TargetCapacitySpecification={
                "TotalTargetCapacity": 1,
                "DefaultTargetCapacityType": cast(
                    Literal["on-demand", "spot"], str(instance_market_type)
                ),
            },
            Type="instant",
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": worker_name}],
                },
            ],
        )
        # No exception is raised by boto3 on an error, it's a 200
        for error in response.get("Errors", []):
            raise InstanceLaunchException(
                f"Failure to launch fleet: {error['ErrorCode']}: "
                f"{error['ErrorMessage']}"
            )
        instance_ids = []
        for instance in response["Instances"]:
            instance_ids += instance["InstanceIds"]
        if len(instance_ids) == 0:
            raise InstanceLaunchException("No instances launched")
        assert len(instance_ids) == 1
        return instance_ids[0]
