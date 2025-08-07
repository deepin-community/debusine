# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test the Worker Pool models."""

from django.test import SimpleTestCase

from debusine.server.worker_pools import (
    AWSEC2InstanceRequirements,
    AWSEC2LaunchTemplate,
    AWSEC2NetworkInterface,
    DummyWorkerPoolSpecification,
    MinMaxRequirements,
    worker_pool_specifications_model,
)


class TestWorkerPoolSpecifications(SimpleTestCase):
    """Tests for WorkerPoolSpecifications."""

    def test_worker_pool_specifications_model(self) -> None:
        model = DummyWorkerPoolSpecification(features=["something"])
        result = worker_pool_specifications_model(model.dict())
        self.assertEqual(model, result)


class TestAWSEC2LaunchTemplate(SimpleTestCase):
    """Tests for AWSEC2LaunchTemplate."""

    def test_no_instance_type_spec(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            r"InstanceType or InstanceRequirements must be specified",
        ):
            AWSEC2LaunchTemplate(
                ImageId="my-image",
                InstanceType=None,
                InstanceRequirements=None,
            )

    def get_instance_requirements(self) -> AWSEC2InstanceRequirements:
        """Generate some test instance requirements."""
        return AWSEC2InstanceRequirements(
            VCpuCount=MinMaxRequirements(Min=2),
            MemoryMiB=MinMaxRequirements(Min=1024),
        )

    def test_both_instance_type_specs(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            r"If InstanceType is specified, InstanceRequirements cannot be",
        ):
            AWSEC2LaunchTemplate(
                ImageId="my-image",
                InstanceType="t3a.medium",
                InstanceRequirements=self.get_instance_requirements(),
            )

    def test_instance_requirements(self) -> None:
        AWSEC2LaunchTemplate(
            ImageId="my-image",
            InstanceRequirements=self.get_instance_requirements(),
        )

    def test_instance_type(self) -> None:
        AWSEC2LaunchTemplate(
            ImageId="my-image",
            InstanceType="t3a.medium",
        )

    def test_network_interfaces_empty_list(self) -> None:
        launch_template = AWSEC2LaunchTemplate(
            ImageId="my-image",
            InstanceType="t3a.medium",
            NetworkInterfaces=[],
        )
        self.assertIsNone(launch_template.NetworkInterfaces)

    def test_network_interfaces_one(self) -> None:
        AWSEC2LaunchTemplate(
            ImageId="my-image",
            InstanceType="t3a.medium",
            NetworkInterfaces=[
                AWSEC2NetworkInterface(
                    AssociatePublicIpAddress=True,
                    DeleteOnTermination=True,
                    DeviceIndex=0,
                    Ipv6AddressCount=1,
                    SubnetId="subnet-42",
                ),
            ],
        )

    def test_network_interfaces_two(self) -> None:
        AWSEC2LaunchTemplate(
            ImageId="my-image",
            InstanceType="t3a.medium",
            NetworkInterfaces=[
                AWSEC2NetworkInterface(
                    AssociatePublicIpAddress=True,
                    DeleteOnTermination=True,
                    DeviceIndex=0,
                    Ipv6AddressCount=1,
                    SubnetId="subnet-42",
                ),
                AWSEC2NetworkInterface(
                    DeviceIndex=1,
                    SubnetId="subnet-43",
                ),
            ],
        )

    def test_network_interfaces_device_indices(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"Each NetworkInterface's DeviceIndex must be unique"
        ):
            AWSEC2LaunchTemplate(
                ImageId="my-image",
                InstanceType="t3a.medium",
                NetworkInterfaces=[
                    AWSEC2NetworkInterface(
                        DeviceIndex=0,
                    ),
                    AWSEC2NetworkInterface(
                        DeviceIndex=0,
                    ),
                ],
            )

    def test_network_interfaces_groups(self) -> None:
        self.assertIsNone(AWSEC2NetworkInterface(Groups=None).Groups)
        self.assertIsNone(AWSEC2NetworkInterface(Groups=[]).Groups)
        self.assertEqual(
            AWSEC2NetworkInterface(Groups=["sg-01"]).Groups, ["sg-01"]
        )
