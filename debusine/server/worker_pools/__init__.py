# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Cloud Worker Pools."""
from debusine.server.worker_pools.amazon_ec2 import AWSEC2WorkerPool
from debusine.server.worker_pools.base import (
    WorkerPoolInterface,
    provider_interface,
)
from debusine.server.worker_pools.dummy import DummyWorkerPool
from debusine.server.worker_pools.hetzner import HetznerCloudWorkerPool
from debusine.server.worker_pools.models import (
    AWSEC2InstanceMarketType,
    AWSEC2InstanceRequirements,
    AWSEC2LaunchTemplate,
    AWSEC2NetworkInterface,
    AWSEC2WorkerPoolSpecification,
    AWSInstanceRequirementFilterEnum,
    DummyWorkerPoolSpecification,
    HetznerCloudWorkerPoolSpecification,
    MinMaxRequirements,
    ScopeWorkerPoolLimits,
    WorkerPoolLimits,
    WorkerPoolSpecifications,
    worker_pool_specifications_model,
)

__all__ = [
    "AWSEC2InstanceMarketType",
    "AWSEC2InstanceRequirements",
    "AWSEC2LaunchTemplate",
    "AWSEC2NetworkInterface",
    "AWSEC2WorkerPool",
    "AWSEC2WorkerPoolSpecification",
    "AWSInstanceRequirementFilterEnum",
    "DummyWorkerPool",
    "DummyWorkerPoolSpecification",
    "HetznerCloudWorkerPool",
    "HetznerCloudWorkerPoolSpecification",
    "MinMaxRequirements",
    "ScopeWorkerPoolLimits",
    "WorkerPoolInterface",
    "WorkerPoolLimits",
    "WorkerPoolSpecifications",
    "provider_interface",
    "worker_pool_specifications_model",
]
