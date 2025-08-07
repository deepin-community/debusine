# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""API for provisioning and managing Hetzner Cloud workers."""

from functools import lru_cache
from typing import Any, TYPE_CHECKING

import hcloud
from django.utils import timezone

# Before hcloud 1.27.0, we must import from the .domain modules
from hcloud.images.domain import Image
from hcloud.locations.domain import Location
from hcloud.networks.domain import Network
from hcloud.server_types.domain import ServerType
from hcloud.servers.domain import Server, ServerCreatePublicNetwork
from hcloud.ssh_keys.domain import SSHKey

from debusine.assets import CloudProvidersType, HetznerProviderAccountData
from debusine.db.models import Token
from debusine.server.worker_pools.base import WorkerPoolInterface
from debusine.server.worker_pools.cloud_init import (
    CloudInitConfig,
    worker_bootstrap_cloud_init,
)
from debusine.server.worker_pools.exceptions import InstanceLaunchException
from debusine.server.worker_pools.models import (
    HetznerCloudWorkerPoolSpecification,
)

if TYPE_CHECKING:
    from debusine.db.models import Worker


class HetznerCloudWorkerPool(
    WorkerPoolInterface, cloud_provider=CloudProvidersType.HETZNER
):
    """Hetzner Cloud Worker Pool."""

    specifications: HetznerCloudWorkerPoolSpecification
    client: hcloud.Client

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize an HetznerCloudWorkerPool."""
        super().__init__(*args, **kwargs)
        provider_account_data = self.worker_pool.provider_account.data_model
        assert isinstance(provider_account_data, HetznerProviderAccountData)
        self.client = self._make_client(provider_account_data)

    def launch_worker(self, worker: "Worker") -> None:
        """Launch a cloud worker using worker."""
        assert worker.activation_token

        self._terminate_existing_instances(worker.worker_pool_data)

        instance_id = self._create_instance(
            worker_name=worker.name, activation_token=worker.activation_token
        )

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
        self, provider_account_data: HetznerProviderAccountData
    ) -> hcloud.Client:
        """Make a Hetzner Cloud client for this provider account."""
        return hcloud.Client(token=provider_account_data.credentials.api_token)

    def _terminate_existing_instances(
        self, worker_pool_data: dict[str, Any] | None
    ) -> None:
        """If worker_pool_data references an instance-id, terminate it."""
        if worker_pool_data is None:
            return
        if existing_instance := worker_pool_data.get("instance-id", None):
            try:
                self.client.servers.delete(Server(id=existing_instance))
            except hcloud.APIException as e:
                if e.code != "not_found":
                    raise

    def _cloud_init_config(self, activation_token: Token) -> CloudInitConfig:
        """Generate cloud-init configuration for a new instance."""
        return worker_bootstrap_cloud_init(
            activation_token,
            self.specifications.debusine_install_source,
            self.specifications.debian_release,
        )

    @lru_cache
    def _get_network_by_name(self, name: str) -> Network:
        """Lookup a Hetzner network by name."""
        network = self.client.networks.get_by_name(name)
        if network is None:
            raise InstanceLaunchException(f"Unable to locate network {name!r}")
        return network

    def _create_instance(
        self, worker_name: str, activation_token: Token
    ) -> int:
        """Create a Hetzner Cloud server, and return its ID."""
        response = self.client.servers.create(
            name=worker_name,
            server_type=ServerType(name=self.specifications.server_type),
            image=Image(name=self.specifications.image_name),
            ssh_keys=[
                SSHKey(name=name) for name in self.specifications.ssh_keys
            ],
            location=(
                Location(name=self.specifications.location)
                if self.specifications.location
                else None
            ),
            networks=[
                self._get_network_by_name(name)
                for name in self.specifications.networks
            ],
            user_data=self._cloud_init_config(activation_token).as_str(),
            labels=self.specifications.labels,
            public_net=ServerCreatePublicNetwork(
                enable_ipv4=self.specifications.enable_ipv4,
                enable_ipv6=self.specifications.enable_ipv6,
            ),
        )

        assert response.server.id
        return response.server.id
