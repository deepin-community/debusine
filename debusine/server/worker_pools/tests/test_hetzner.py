# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for Hetzner Cloud workers."""

import json
from unittest.mock import patch

import responses
from hcloud import APIException
from hcloud.networks.domain import Network
from responses.matchers import query_string_matcher

from debusine.assets import CloudProvidersType, HetznerProviderAccountData
from debusine.db.models import Asset, WorkerPool
from debusine.server.worker_pools.exceptions import InstanceLaunchException
from debusine.server.worker_pools.hetzner import HetznerCloudWorkerPool
from debusine.test.django import TestCase


class TestHetznerCloudWorkerPool(TestCase):
    """
    Test for HetznerCloudWorkerPool.

    We use responses instead of mocks, as Server() objects don't implement
    equality checking magic methods, yet:
    https://github.com/hetznercloud/hcloud-python/pull/481
    """

    provider_account: Asset
    worker_pool_model: WorkerPool

    def setUp(self) -> None:
        super().setUp()
        self.provider_account = (
            self.playground.create_cloud_provider_account_asset(
                cloud_provider=CloudProvidersType.HETZNER
            )
        )
        self.worker_pool_model = self.playground.create_worker_pool(
            provider_account=self.provider_account
        )

    def get_worker_pool(self) -> HetznerCloudWorkerPool:
        """Return a HetznerCloudWorkerPool instance."""
        worker_pool = self.worker_pool_model.provider_interface
        assert isinstance(worker_pool, HetznerCloudWorkerPool)
        return worker_pool

    def test_client_attributes(self) -> None:
        worker_pool = self.get_worker_pool()
        client = worker_pool.client
        self.assertIsNotNone(client)
        provider_account_data = self.provider_account.data_model
        assert isinstance(provider_account_data, HetznerProviderAccountData)
        self.assertEqual(
            client.token,
            provider_account_data.credentials.api_token,
        )

    @responses.activate
    def test_terminate_existing_instances(self) -> None:
        worker_pool = self.get_worker_pool()
        responses.add(
            responses.DELETE,
            "https://api.hetzner.cloud/v1/servers/42",
            json={"action": {"id": 1}},
        )
        worker_pool._terminate_existing_instances({"instance-id": 42})

    def test_terminate_existing_instances_noop(self) -> None:
        worker_pool = self.get_worker_pool()
        worker_pool._terminate_existing_instances(None)
        worker_pool._terminate_existing_instances({})

    def test_terminate_existing_instances_missing(self) -> None:
        worker_pool = self.get_worker_pool()
        with patch.object(
            worker_pool.client.servers,
            "delete",
            side_effect=APIException(
                code="not_found",
                message="server with ID '42' not found",
                details=None,
            ),
        ):
            worker_pool._terminate_existing_instances({"instance-id": 42})

    def test_terminate_existing_instances_unexpected_failure(self) -> None:
        worker_pool = self.get_worker_pool()
        with (
            patch.object(
                worker_pool.client.servers,
                "delete",
                side_effect=APIException(
                    code="something_unexpected",
                    message="Server said no",
                    details=None,
                ),
            ),
            self.assertRaisesRegex(APIException, r"Server said no"),
        ):
            worker_pool._terminate_existing_instances({"instance-id": 42})

    def test_cloud_init_config(self) -> None:
        token = self.playground.create_bare_token()
        config = self.get_worker_pool()._cloud_init_config(token)
        self.assertIsNotNone(config)

    @responses.activate
    def test_get_network_by_name(self) -> None:
        worker_pool = self.get_worker_pool()
        responses.add(
            responses.GET,
            "https://api.hetzner.cloud/v1/networks",
            match=[query_string_matcher("name=my-network")],
            json={
                "networks": [{"id": 7, "name": "my-network"}],
            },
        )
        network = worker_pool._get_network_by_name(name="my-network")
        self.assertEqual(network.id, 7)

    @responses.activate
    def test_get_network_by_name_not_found(self) -> None:
        worker_pool = self.get_worker_pool()
        responses.add(
            responses.GET,
            "https://api.hetzner.cloud/v1/networks",
            match=[query_string_matcher("name=my-network")],
            json={
                "networks": [],
            },
        )
        with self.assertRaisesRegex(
            InstanceLaunchException, r"Unable to locate network 'my-network'"
        ):
            worker_pool._get_network_by_name(name="my-network")

    @responses.activate
    def test_create_instance_complete(self) -> None:
        token = self.playground.create_bare_token()
        worker_pool = self.get_worker_pool()
        worker_pool.specifications.image_name = "debian-12"
        worker_pool.specifications.ssh_keys = ["testkey"]
        worker_pool.specifications.location = "nbg1"
        worker_pool.specifications.networks = ["my-net"]
        worker_pool.specifications.labels = {"role": "debusine-worker"}
        worker_pool.specifications.enable_ipv4 = True
        worker_pool.specifications.enable_ipv6 = True
        responses.add(
            responses.POST,
            "https://api.hetzner.cloud/v1/servers",
            json={
                "action": {"id": 1},
                "server": {"id": 42},
                "next_actions": [],
                "root_password": "secret-password",
            },
        )
        with patch.object(
            worker_pool, "_get_network_by_name", return_value=Network(id=7)
        ):
            server_id = worker_pool._create_instance(
                worker_name="hetzner-001", activation_token=token
            )
        self.assertEqual(server_id, 42)

        # With responses >= 0.21, we can assert against the response we
        # configured above, rather than the global calls list
        assert responses.calls[0].request.body
        body = json.loads(responses.calls[0].request.body)
        self.assertEqual(body["name"], "hetzner-001")
        self.assertEqual(body["image"], worker_pool.specifications.image_name)
        self.assertEqual(body["ssh_keys"], worker_pool.specifications.ssh_keys)
        self.assertEqual(body["location"], worker_pool.specifications.location)
        self.assertEqual(body["networks"], [7])
        self.assertTrue(body["user_data"].startswith("#cloud-config"))
        self.assertEqual(body["labels"], worker_pool.specifications.labels)
        self.assertEqual(
            body["public_net"]["enable_ipv4"],
            worker_pool.specifications.enable_ipv4,
        )
        self.assertEqual(
            body["public_net"]["enable_ipv6"],
            worker_pool.specifications.enable_ipv6,
        )

    @responses.activate
    def test_create_instance_simple(self) -> None:
        token = self.playground.create_bare_token()
        worker_pool = self.get_worker_pool()
        worker_pool.specifications.image_name = "debian-12"
        worker_pool.specifications.ssh_keys = []
        worker_pool.specifications.location = None
        worker_pool.specifications.networks = []
        worker_pool.specifications.labels = {}
        worker_pool.specifications.enable_ipv4 = False
        worker_pool.specifications.enable_ipv6 = False
        responses.add(
            responses.POST,
            "https://api.hetzner.cloud/v1/servers",
            json={
                "action": {"id": 1},
                "server": {"id": 42},
                "next_actions": [],
                "root_password": "secret-password",
            },
        )
        server_id = worker_pool._create_instance(
            worker_name="hetzner-001", activation_token=token
        )
        self.assertEqual(server_id, 42)

        # With responses >= 0.21, we can assert against the response we
        # configured above, rather than the global calls list
        assert responses.calls[0].request.body
        body = json.loads(responses.calls[0].request.body)
        self.assertEqual(body["name"], "hetzner-001")
        self.assertEqual(body["image"], worker_pool.specifications.image_name)
        self.assertEqual(body["ssh_keys"], worker_pool.specifications.ssh_keys)
        self.assertNotIn("location", body)
        self.assertEqual(body["networks"], [])
        self.assertTrue(body["user_data"].startswith("#cloud-config"))
        self.assertEqual(body["labels"], worker_pool.specifications.labels)
        self.assertEqual(
            body["public_net"]["enable_ipv4"],
            worker_pool.specifications.enable_ipv4,
        )
        self.assertEqual(
            body["public_net"]["enable_ipv6"],
            worker_pool.specifications.enable_ipv6,
        )

    def test_launch_worker(self) -> None:
        worker_pool = self.get_worker_pool()
        worker = self.playground.create_worker(
            worker_pool=worker_pool.worker_pool
        )
        worker.worker_pool_data = {"random-data": "foo"}
        worker.instance_created_at = None
        worker.save()

        with (
            patch.object(
                worker_pool, "_create_instance", return_value=42
            ) as create_instance,
            patch.object(
                worker_pool, "_terminate_existing_instances"
            ) as terminate_existing_instances,
        ):
            worker_pool.launch_worker(worker)

        create_instance.assert_called_once_with(
            worker_name=worker.name,
            activation_token=worker.activation_token,
        )
        terminate_existing_instances.assert_called_once_with(
            {"random-data": "foo"}
        )
        self.assertIsNotNone(worker.instance_created_at)
        self.assertEqual(worker.worker_pool_data, {"instance-id": 42})
        assert worker.activation_token
        self.assertTrue(worker.activation_token.enabled)

    def test_terminate_worker(self) -> None:
        worker_pool = self.get_worker_pool()
        worker = self.playground.create_worker(
            worker_pool=worker_pool.worker_pool
        )
        worker.worker_pool_data = {"instance-id": 42}
        worker.save()

        with patch.object(
            worker_pool, "_terminate_existing_instances"
        ) as terminate_existing_instances:
            worker_pool.terminate_worker(worker)

        terminate_existing_instances.assert_called_once_with(
            {"instance-id": 42}
        )
        self.assertIsNone(worker.instance_created_at)
        self.assertEqual(worker.worker_pool_data, {})
