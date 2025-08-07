# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for AWS EC2 cloud workers."""

from base64 import b64decode
from typing import Any
from unittest.mock import patch

from botocore import stub
from botocore.exceptions import ClientError

from debusine.assets import AWSProviderAccountData, CloudProvidersType
from debusine.db.context import context
from debusine.db.models import Asset, WorkerPool
from debusine.server.worker_pools.amazon_ec2 import AWSEC2WorkerPool
from debusine.server.worker_pools.cloud_init import FilesystemSetup
from debusine.server.worker_pools.exceptions import InstanceLaunchException
from debusine.server.worker_pools.models import (
    AWSEC2InstanceMarketType,
    AWSEC2InstanceRequirements,
    AWSEC2LaunchTemplate,
    AWSEC2NetworkInterface,
    MinMaxRequirements,
)
from debusine.test.django import TestCase


class TestAWSEC2WorkerPool(TestCase):
    """Test for AWSEC2WorkerPool."""

    provider_account: Asset
    worker_pool_model: WorkerPool

    def setUp(self) -> None:
        super().setUp()
        self.provider_account = (
            self.playground.create_cloud_provider_account_asset(
                cloud_provider=CloudProvidersType.AWS
            )
        )
        self.worker_pool_model = self.playground.create_worker_pool(
            provider_account=self.provider_account
        )

    def get_worker_pool(self) -> AWSEC2WorkerPool:
        """Return a AWSEC2WorkerPool instance."""
        worker_pool = self.worker_pool_model.provider_interface
        assert isinstance(worker_pool, AWSEC2WorkerPool)
        return worker_pool

    def test_client_attributes(self) -> None:
        worker_pool = self.get_worker_pool()
        client = worker_pool.client
        self.assertEqual(client.__class__.__name__, "EC2")
        self.assertIsNotNone(client)
        provider_account_data = self.provider_account.data_model
        assert isinstance(provider_account_data, AWSProviderAccountData)
        self.assertEqual(
            client.meta.region_name,
            provider_account_data.configuration.region_name,
        )
        # Private attribute not exposed by type annotations.
        signer = getattr(client, "_request_signer")
        self.assertEqual(
            signer._credentials.access_key,
            provider_account_data.credentials.access_key_id,
        )
        self.assertEqual(
            signer._credentials.secret_key,
            provider_account_data.credentials.secret_access_key,
        )

    def test_client_endpoint_url(self) -> None:
        provider_account_data = self.provider_account.data_model
        assert isinstance(provider_account_data, AWSProviderAccountData)
        provider_account_data.configuration.ec2_endpoint_url = (
            "https://example.com/"
        )
        with context.disable_permission_checks():
            self.provider_account.data = provider_account_data.dict(
                exclude_unset=True
            )
            self.provider_account.save()

        worker_pool = self.get_worker_pool()
        self.assertEqual(
            worker_pool.client.meta.endpoint_url, "https://example.com/"
        )

    def launch_template(self, **kwargs: Any) -> AWSEC2LaunchTemplate:
        """Generate a test AWSEC2LaunchTemplate."""
        launch_template = AWSEC2LaunchTemplate(
            ImageId="ec2-image",
            InstanceType="t3a.medium",
        )
        return launch_template.copy(update=kwargs)

    def test_terminate_existing_instances(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_response(
                method="terminate_instances",
                expected_params={
                    "InstanceIds": ["instance-1"],
                },
                service_response={},
            )
            worker_pool._terminate_existing_instances(
                {"instance-id": "instance-1"}
            )

    def test_terminate_existing_instances_noop(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client):
            worker_pool._terminate_existing_instances(None)
            worker_pool._terminate_existing_instances({})

    def test_terminate_existing_instances_missing(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_client_error(
                method="terminate_instances",
                expected_params={
                    "InstanceIds": ["instance-1"],
                },
                service_error_code="InvalidInstanceID.NotFound",
                service_message="The instance ID 'instance-1' does not exist",
            )
            worker_pool._terminate_existing_instances(
                {"instance-id": "instance-1"}
            )

    def test_terminate_existing_instances_unexpected_failure(self) -> None:
        worker_pool = self.get_worker_pool()
        with (
            stub.Stubber(worker_pool.client) as stubber,
            self.assertRaisesRegex(
                ClientError, r"Some entirely unexpected failure"
            ),
        ):
            stubber.add_client_error(
                method="terminate_instances",
                expected_params={
                    "InstanceIds": ["instance-1"],
                },
                service_error_code="InternalFailure",
                service_message="Some entirely unexpected failure",
            )
            worker_pool._terminate_existing_instances(
                {"instance-id": "instance-1"}
            )

    def test_cloud_init_config_simple(self) -> None:
        token = self.playground.create_bare_token()
        config = self.get_worker_pool()._cloud_init_config(
            self.launch_template(), token
        )
        self.assertEqual(config.device_aliases, {})
        self.assertEqual(config.fs_setup, [])
        self.assertEqual(config.mounts, [])
        self.assertGreater(len(config.runcmd), 0)

    def test_cloud_init_config_swap(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(swap_size=4)
        config = self.get_worker_pool()._cloud_init_config(
            launch_template, token
        )
        self.assertEqual(config.device_aliases, {"swap": "/dev/xvdb"})
        self.assertEqual(
            config.fs_setup, [FilesystemSetup(device="swap", filesystem="swap")]
        )
        self.assertEqual(
            config.mounts,
            [
                [
                    "swap",
                    "none",
                    "swap",
                    "sw,nofail,x-systemd.requires=cloud-init.service",
                ],
            ],
        )
        self.assertGreater(len(config.runcmd), 0)

    def test_lookup_image_root_device_name(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_response(
                method="describe_images",
                expected_params={"ImageIds": ["test-image"]},
                service_response={
                    "Images": [
                        {
                            "RootDeviceName": "/dev/xvda",
                            "RootDeviceType": "ebs",
                        }
                    ]
                },
            )
            root_device = worker_pool._lookup_image_root_device_name(
                "test-image"
            )
        self.assertEqual(root_device, "/dev/xvda")

    def test_lookup_image_root_device_name_no_image(self) -> None:
        worker_pool = self.get_worker_pool()
        with (
            stub.Stubber(worker_pool.client) as stubber,
            self.assertRaisesRegex(
                ValueError, r"Found 0 images instead of the expected 1"
            ),
        ):
            stubber.add_response(
                method="describe_images",
                expected_params={"ImageIds": ["test-image"]},
                service_response={"Images": []},
            )
            worker_pool._lookup_image_root_device_name("test-image")

    def test_lookup_image_root_device_name_instance_store(self) -> None:
        worker_pool = self.get_worker_pool()
        with (
            stub.Stubber(worker_pool.client) as stubber,
            self.assertRaisesRegex(
                ValueError, r"Image test-image is not EBS-backed"
            ),
        ):
            stubber.add_response(
                method="describe_images",
                expected_params={"ImageIds": ["test-image"]},
                service_response={
                    "Images": [{"RootDeviceType": "instance-store"}]
                },
            )
            worker_pool._lookup_image_root_device_name("test-image")

    def test_launch_template_data_simple(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template()
        launch_template_data = self.get_worker_pool()._launch_template_data(
            launch_template, token
        )
        # Literal copies
        self.assertEqual(
            launch_template_data["EbsOptimized"], launch_template.EbsOptimized
        )
        self.assertEqual(
            launch_template_data["ImageId"], launch_template.ImageId
        )
        self.assertEqual(
            launch_template_data["InstanceType"], launch_template.InstanceType
        )
        self.assertNotIn("InstanceRequirements", launch_template_data)
        self.assertIsNone(launch_template.KeyName)
        self.assertNotIn("KeyName", launch_template_data)
        # More complex interpretation
        self.assertEqual(launch_template_data["BlockDeviceMappings"], [])
        self.assertEqual(launch_template_data["TagSpecifications"], [])
        self.assertTrue(
            b64decode(launch_template_data["UserData"])
            .decode("utf-8")
            .startswith("#cloud-config\n")
        )

    def test_launch_template_data_instance_requirements(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(
            InstanceType=None,
            InstanceRequirements=AWSEC2InstanceRequirements(
                VCpuCount=MinMaxRequirements(Min=1),
                MemoryMiB=MinMaxRequirements(Min=1024),
            ),
        )
        launch_template_data = self.get_worker_pool()._launch_template_data(
            launch_template, token
        )
        self.assertNotIn("InstanceType", launch_template_data)
        assert launch_template.InstanceRequirements
        self.assertEqual(
            launch_template_data["InstanceRequirements"],
            launch_template.InstanceRequirements.dict(exclude_unset=True),
        )

    def test_launch_template_data_ssh_key(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(KeyName="my-key")
        launch_template_data = self.get_worker_pool()._launch_template_data(
            launch_template, token
        )
        self.assertEqual(
            launch_template_data["KeyName"], launch_template.KeyName
        )

    def test_launch_template_data_network_interfaces(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(
            NetworkInterfaces=[
                AWSEC2NetworkInterface(
                    AssociatePublicIpAddress=True,
                    Ipv6AddressCount=1,
                )
            ],
        )
        launch_template_data = self.get_worker_pool()._launch_template_data(
            launch_template, token
        )
        self.assertEqual(
            launch_template_data["NetworkInterfaces"],
            [
                {
                    "AssociatePublicIpAddress": True,
                    "DeleteOnTermination": True,
                    "DeviceIndex": 0,
                    "Ipv6AddressCount": 1,
                },
            ],
        )

    def test_launch_template_data_resize_rootfs(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(root_device_size=8)
        worker_pool = self.get_worker_pool()
        with patch.object(
            worker_pool,
            "_lookup_image_root_device_name",
            return_value="/dev/xvda",
        ):
            launch_template_data = worker_pool._launch_template_data(
                launch_template, token
            )
        self.assertEqual(
            launch_template_data["BlockDeviceMappings"],
            [
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": 8,
                        "DeleteOnTermination": True,
                        "VolumeType": "gp3",
                    },
                }
            ],
        )

    def test_launch_template_data_with_swap(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(swap_size=8)
        launch_template_data = self.get_worker_pool()._launch_template_data(
            launch_template, token
        )
        self.assertEqual(
            launch_template_data["BlockDeviceMappings"],
            [
                {
                    "DeviceName": "/dev/xvdb",
                    "Ebs": {
                        "VolumeSize": 8,
                        "DeleteOnTermination": True,
                        "VolumeType": "gp3",
                    },
                }
            ],
        )

    def test_launch_template_data_with_tags(self) -> None:
        token = self.playground.create_bare_token()
        launch_template = self.launch_template(tags={"foo": "bar"})
        launch_template_data = self.get_worker_pool()._launch_template_data(
            launch_template, token
        )
        self.assertEqual(
            launch_template_data["TagSpecifications"],
            [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {
                            "Key": "foo",
                            "Value": "bar",
                        },
                    ],
                },
            ],
        )

    def test_create_launch_template(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_response(
                method="create_launch_template",
                expected_params={
                    "LaunchTemplateName": "test-image",
                    "LaunchTemplateData": {},
                },
                service_response={
                    "LaunchTemplate": {
                        "LaunchTemplateId": "lt-id",
                        "LaunchTemplateName": "test-image",
                    },
                },
            )
            template_id = worker_pool._create_launch_template("test-image", {})
        self.assertEqual(template_id, "lt-id")

    def get_expected_create_fleet_params(
        self,
        template: str = "my-template",
        instance_market_type: str = "spot",
        worker_name: str = "cloud0",
    ) -> dict[str, Any]:
        """Get the expected parameters to create_fleet"""
        return {
            "SpotOptions": {},
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": template,
                        "Version": "$Latest",
                    },
                }
            ],
            "TargetCapacitySpecification": {
                "TotalTargetCapacity": 1,
                "DefaultTargetCapacityType": instance_market_type,
            },
            "Type": "instant",
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": worker_name}],
                },
            ],
        }

    def test_create_fleet_spot(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_response(
                method="create_fleet",
                expected_params=self.get_expected_create_fleet_params(),
                service_response={
                    "Instances": [
                        {
                            "InstanceIds": ["i-id"],
                        },
                    ],
                    "Errors": [],
                },
            )
            instance_id = worker_pool._create_fleet(
                ["my-template"],
                AWSEC2InstanceMarketType.SPOT,
                worker_name="cloud0",
                max_spot_price_per_hour=None,
            )
        self.assertEqual(instance_id, "i-id")

    def test_create_fleet_spot_max_price(self) -> None:
        worker_pool = self.get_worker_pool()
        expected_params = self.get_expected_create_fleet_params()
        expected_params["SpotOptions"]["MaxTotalPrice"] = "0.1"
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_response(
                method="create_fleet",
                expected_params=expected_params,
                service_response={
                    "Instances": [
                        {
                            "InstanceIds": ["i-id"],
                        },
                    ],
                    "Errors": [],
                },
            )
            instance_id = worker_pool._create_fleet(
                ["my-template"],
                AWSEC2InstanceMarketType.SPOT,
                worker_name="cloud0",
                max_spot_price_per_hour=0.1,
            )
        self.assertEqual(instance_id, "i-id")

    def test_create_fleet_on_demand(self) -> None:
        worker_pool = self.get_worker_pool()
        with stub.Stubber(worker_pool.client) as stubber:
            stubber.add_response(
                method="create_fleet",
                expected_params=self.get_expected_create_fleet_params(
                    instance_market_type="on-demand"
                ),
                service_response={
                    "Instances": [
                        {
                            "InstanceIds": ["i-id"],
                        },
                    ],
                    "Errors": [],
                },
            )
            instance_id = worker_pool._create_fleet(
                ["my-template"],
                AWSEC2InstanceMarketType.ON_DEMAND,
                worker_name="cloud0",
                max_spot_price_per_hour=None,
            )
        self.assertEqual(instance_id, "i-id")

    def test_create_fleet_no_templates(self) -> None:
        worker_pool = self.get_worker_pool()
        with self.assertRaisesRegex(
            ValueError, r"No launch_template_ids were provided"
        ):
            worker_pool._create_fleet(
                [],
                AWSEC2InstanceMarketType.SPOT,
                worker_name="cloud0",
                max_spot_price_per_hour=None,
            )

    def test_create_fleet_error(self) -> None:
        worker_pool = self.get_worker_pool()
        with (
            stub.Stubber(worker_pool.client) as stubber,
            self.assertRaisesRegex(
                InstanceLaunchException,
                (
                    r"Failure to launch fleet: InvalidParameterCombination: "
                    r"Foo cannot be combined with bar\."
                ),
            ),
        ):
            stubber.add_response(
                method="create_fleet",
                expected_params=self.get_expected_create_fleet_params(),
                service_response={
                    "Instances": [],
                    "Errors": [
                        {
                            "ErrorCode": "InvalidParameterCombination",
                            "ErrorMessage": "Foo cannot be combined with bar.",
                        },
                        {
                            "ErrorCode": "InvalidParameterCombination",
                            "ErrorMessage": "Foo cannot be combined with bar.",
                        },
                    ],
                },
            )
            worker_pool._create_fleet(
                ["my-template"],
                AWSEC2InstanceMarketType.SPOT,
                worker_name="cloud0",
                max_spot_price_per_hour=None,
            )

    def test_create_fleet_no_instances(self) -> None:
        worker_pool = self.get_worker_pool()
        with (
            stub.Stubber(worker_pool.client) as stubber,
            self.assertRaisesRegex(
                InstanceLaunchException, r"No instances launched"
            ),
        ):
            stubber.add_response(
                method="create_fleet",
                expected_params=self.get_expected_create_fleet_params(),
                service_response={
                    # This should never happen... But we wouldn't want to hit
                    # an IndexError
                    "Instances": [],
                    "Errors": [],
                },
            )
            worker_pool._create_fleet(
                ["my-template"],
                AWSEC2InstanceMarketType.SPOT,
                worker_name="cloud0",
                max_spot_price_per_hour=None,
            )

    def test_launch_worker(self) -> None:
        worker_pool = self.get_worker_pool()
        specs = worker_pool.specifications
        launch_template = self.launch_template()
        specs.launch_templates = [launch_template]
        worker = self.playground.create_worker(
            worker_pool=worker_pool.worker_pool
        )
        worker.worker_pool_data = {"random-data": "foo"}
        worker.instance_created_at = None
        worker.save()

        with (
            stub.Stubber(worker_pool.client) as stubber,
            patch.object(
                worker_pool, "_launch_template_data", return_value=object()
            ) as launch_template_data,
            patch.object(
                worker_pool,
                "_create_launch_template",
                return_value="template-1",
            ) as create_launch_template,
            patch.object(
                worker_pool, "_create_fleet", return_value="instance-1"
            ) as create_fleet,
            patch.object(
                worker_pool, "_terminate_existing_instances"
            ) as terminate_existing_instances,
        ):
            stubber.add_client_error(
                method="delete_launch_template",
                expected_params={
                    "LaunchTemplateName": f"debusine_worker_{worker.name}_0",
                },
                service_error_code="NotFoundException",
                service_message=(
                    f"The specified launch template, with template name "
                    f"debusine_worker_{worker.name}_0, does not exist."
                ),
            )
            stubber.add_response(
                method="delete_launch_template",
                expected_params={
                    "LaunchTemplateId": "template-1",
                },
                service_response={},
            )
            worker_pool.launch_worker(worker)

        launch_template_data.assert_called_once_with(
            launch_template, worker.activation_token
        )
        create_launch_template.assert_called_once_with(
            f"debusine_worker_{worker.name}_0",
            launch_template_data.return_value,
        )
        create_fleet.assert_called_once_with(
            ["template-1"],
            instance_market_type=specs.instance_market_type,
            worker_name=worker.name,
            max_spot_price_per_hour=specs.max_spot_price_per_hour,
        )
        terminate_existing_instances.assert_called_once_with(
            {"random-data": "foo"}
        )
        self.assertIsNotNone(worker.instance_created_at)
        self.assertEqual(worker.worker_pool_data, {"instance-id": "instance-1"})
        assert worker.activation_token
        self.assertTrue(worker.activation_token.enabled)

    def test_terminate_worker(self) -> None:
        worker_pool = self.get_worker_pool()
        worker = self.playground.create_worker(
            worker_pool=worker_pool.worker_pool
        )
        worker.worker_pool_data = {"instance-id": "instance-1"}
        worker.save()

        with patch.object(
            worker_pool, "_terminate_existing_instances"
        ) as terminate_existing_instances:
            worker_pool.terminate_worker(worker)

        terminate_existing_instances.assert_called_once_with(
            {"instance-id": "instance-1"}
        )
        self.assertIsNone(worker.instance_created_at)
        self.assertEqual(worker.worker_pool_data, {})
