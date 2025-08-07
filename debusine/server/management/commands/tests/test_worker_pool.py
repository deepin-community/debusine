# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command worker_pool."""

from io import StringIO
from unittest import mock

import yaml
from django.core.management.base import CommandError
from django.test import override_settings

from debusine.assets import CloudProvidersType, DummyProviderAccountData
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.models import Worker, WorkerPool
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.server.management.commands.worker_pool import Command
from debusine.server.management.management_utils import sort_keys
from debusine.server.worker_pools import (
    DummyWorkerPool,
    DummyWorkerPoolSpecification,
    WorkerPoolLimits,
)
from debusine.test.django import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class WorkerPoolCommandTests(TabularOutputTests, TestCase):
    """Tests for the `worker_pool` command."""

    def test_create_defaults_from_stdin(self) -> None:
        name = "test"
        provider_account = self.playground.create_cloud_provider_account_asset()
        provider_account_data = provider_account.data_model
        assert isinstance(provider_account_data, DummyProviderAccountData)
        specifications = DummyWorkerPoolSpecification(features=["foo"])

        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "create",
            name,
            "--provider-account",
            provider_account_data.name,
            "--architectures",
            "amd64,i386",
            "--specifications",
            "-",
            stdin=StringIO(yaml.safe_dump(specifications.dict())),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        pool = WorkerPool.objects.get(name=name)
        self.assertTrue(pool.enabled)
        self.assertEqual(pool.architectures, ["amd64", "i386"])
        self.assertEqual(pool.tags, [])
        self.assertEqual(pool.provider_account, provider_account)
        self.assertEqual(pool.specifications, specifications.dict())
        self.assertTrue(pool.instance_wide)
        self.assertFalse(pool.ephemeral)
        self.assertEqual(pool.limits, WorkerPoolLimits().dict())

    def test_create_all_args_from_files(self) -> None:
        name = "test"
        provider_account = self.playground.create_cloud_provider_account_asset()
        provider_account_data = provider_account.data_model
        assert isinstance(provider_account_data, DummyProviderAccountData)
        specifications = DummyWorkerPoolSpecification(features=["something"])
        specifications_file = self.create_temporary_file(
            contents=specifications.json().encode()
        )

        limits = WorkerPoolLimits(max_idle_seconds=60)
        limits_file = self.create_temporary_file(
            contents=limits.json().encode()
        )

        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "create",
            name,
            "--provider-account",
            provider_account_data.name,
            "--no-enabled",
            "--architectures",
            "i386",
            "--tags",
            "tag-1",
            "--specifications",
            str(specifications_file),
            "--no-instance-wide",
            "--ephemeral",
            "--limits",
            str(limits_file),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        pool = WorkerPool.objects.get(name=name)
        self.assertFalse(pool.enabled)
        self.assertEqual(pool.architectures, ["i386"])
        self.assertEqual(pool.tags, ["tag-1"])
        self.assertEqual(pool.provider_account, provider_account)
        self.assertEqual(pool.specifications, specifications.dict())
        self.assertFalse(pool.instance_wide)
        self.assertTrue(pool.ephemeral)
        self.assertEqual(pool.limits, limits.dict())

    def test_create_idempotent(self) -> None:
        pool = self.playground.create_worker_pool()
        specifications_file = self.create_temporary_file(
            contents=yaml.safe_dump(pool.specifications).encode()
        )
        limits = WorkerPoolLimits(max_idle_seconds=60)
        limits_file = self.create_temporary_file(
            contents=limits.json().encode()
        )
        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "create",
            pool.name,
            "--provider-account",
            pool.provider_account.data["name"],
            "--no-enabled",
            "--architectures",
            "i386",
            "--tags",
            "tag-1",
            "--specifications",
            str(specifications_file),
            "--no-instance-wide",
            "--ephemeral",
            "--limits",
            str(limits_file),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        pool.refresh_from_db()

        self.assertFalse(pool.enabled)
        self.assertEqual(pool.architectures, ["i386"])
        self.assertEqual(pool.tags, ["tag-1"])
        self.assertFalse(pool.instance_wide)
        self.assertTrue(pool.ephemeral)
        self.assertEqual(pool.limits, limits.dict())

    def test_create_invalid_specifications(self) -> None:
        name = "test"
        provider_account = self.playground.create_cloud_provider_account_asset()
        provider_account_data = provider_account.data_model
        assert isinstance(provider_account_data, DummyProviderAccountData)
        specifications_file = self.create_temporary_file(
            contents=yaml.dump(
                {
                    "provider_type": "dummy",
                    "something": "some value",
                },
                encoding="utf-8",
            )
        )
        with self.assertRaisesRegex(
            CommandError, r"(?s)Error creating worker_pool:.*validation error"
        ):
            call_command(
                "worker_pool",
                "create",
                name,
                "--provider-account",
                provider_account_data.name,
                "--specifications",
                str(specifications_file),
            )

    def test_create_invalid_limits(self) -> None:
        name = "test"
        provider_account = self.playground.create_cloud_provider_account_asset()
        provider_account_data = provider_account.data_model
        assert isinstance(provider_account_data, DummyProviderAccountData)
        specifications = DummyWorkerPoolSpecification(features=["something"])
        specifications_file = self.create_temporary_file(
            contents=specifications.json().encode()
        )
        limits_file = self.create_temporary_file(
            contents=yaml.dump(
                {
                    "something": "some value",
                },
                encoding="utf-8",
            )
        )
        with self.assertRaisesRegex(
            CommandError, r"(?s)Error creating worker_pool:.*validation error"
        ):
            call_command(
                "worker_pool",
                "create",
                name,
                "--provider-account",
                provider_account_data.name,
                "--specifications",
                str(specifications_file),
                "--limits",
                str(limits_file),
            )

    def test_create_unknown_provider_account(self) -> None:
        with self.assertRaisesRegex(
            CommandError, r"Cloud provider account asset 'unknown' not found"
        ):
            call_command(
                "worker_pool",
                "create",
                "test",
                "--provider-account",
                "unknown",
            )

    def test_delete(self) -> None:
        pool = self.playground.create_worker_pool()
        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "delete",
            pool.name,
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertFalse(WorkerPool.objects.filter(name="test").exists())

    def test_delete_not_found(self) -> None:
        with self.assertRaisesRegex(
            CommandError, r"Worker Pool 'unknown' not found"
        ):
            call_command(
                "worker_pool",
                "delete",
                "unknown",
            )

    def test_delete_running_workers(self) -> None:
        pool = self.playground.create_worker_pool()
        self.playground.create_worker(worker_pool=pool)
        with self.assertRaisesRegex(
            CommandError, r"Worker pool 'test' has 1 running workers\."
        ):
            call_command(
                "worker_pool",
                "delete",
                "test",
            )

    def test_delete_running_workers_force(self) -> None:
        pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=pool)
        with mock.patch.object(
            DummyWorkerPool, "terminate_worker"
        ) as mock_terminate_worker:
            stdout, stderr, exit_code = call_command(
                "worker_pool",
                "delete",
                "test",
                "--force",
            )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        mock_terminate_worker.assert_called_once_with(worker)
        self.assertFalse(WorkerPool.objects.filter(name="test").exists())
        self.assertFalse(Worker.objects.filter(id=worker.id).exists())

    def test_enable(self) -> None:
        pool = self.playground.create_worker_pool(enabled=False)
        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "enable",
            pool.name,
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        pool.refresh_from_db()
        self.assertTrue(pool.enabled)

    def test_disable(self) -> None:
        pool = self.playground.create_worker_pool()
        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "disable",
            pool.name,
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        pool.refresh_from_db()
        self.assertFalse(pool.enabled)

    def test_list(self) -> None:
        dummy_provider = self.playground.create_cloud_provider_account_asset(
            name="dummy"
        )
        aws_provider = self.playground.create_cloud_provider_account_asset(
            name="aws", cloud_provider=CloudProvidersType.AWS
        )
        pools = [
            self.playground.create_worker_pool(
                name="dummy", provider_account=dummy_provider
            ),
            self.playground.create_worker_pool(
                name="aws", provider_account=aws_provider
            ),
        ]
        expected_data = [
            {
                "name": pool.name,
                "provider_account": pool.provider_account.data["name"],
                "enabled": pool.enabled,
                "specifications": sort_keys(
                    model_to_json_serializable_dict(
                        pool.specifications_model, exclude_unset=True
                    )
                ),
                "limits": sort_keys(
                    model_to_json_serializable_dict(
                        pool.limits_model, exclude_unset=True
                    )
                ),
            }
            for pool in pools
        ]

        with self.assertPrintsTable() as output:
            stdout, stderr, exit_code = call_command("worker_pool", "list")
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.col(0), [expected["name"] for expected in expected_data]
        )
        self.assertEqual(
            output.col(1),
            [expected["provider_account"] for expected in expected_data],
        )
        self.assertEqual(
            output.col(2),
            [str(expected["enabled"]) for expected in expected_data],
        )
        self.assertEqual(
            output.col(3),
            [str(expected["specifications"]) for expected in expected_data],
        )
        self.assertEqual(
            output.col(4),
            [str(expected["limits"]) for expected in expected_data],
        )

        stdout, stderr, exit_code = call_command(
            "worker_pool", "list", "--yaml"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        data = yaml.safe_load(stdout)
        self.assertEqual(data, expected_data)

    def test_launch(self) -> None:
        pool = self.playground.create_worker_pool()
        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "launch",
            pool.name,
            "2",
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        self.assertEqual(
            pool.workers_running.filter(
                worker_pool_data__launched=True
            ).count(),
            2,
        )

    def test_terminate(self) -> None:
        pool = self.playground.create_worker_pool()
        worker = self.playground.create_worker(worker_pool=pool)
        worker.worker_pool_data = {"launched": True}
        worker.save()
        self.assertIsNotNone(worker.instance_created_at)

        stdout, stderr, exit_code = call_command(
            "worker_pool",
            "terminate",
            worker.name,
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        worker.refresh_from_db()
        self.assertIsNone(worker.instance_created_at)
        self.assertIsNone(worker.worker_pool_data)

    def test_terminate_non_pool_worker(self) -> None:
        worker = self.playground.create_worker()
        with self.assertRaisesRegex(
            CommandError, r"Worker 'computer-lan' is not a member of a pool\."
        ):
            call_command(
                "worker_pool",
                "terminate",
                worker.name,
            )

    def test_terminate_unknown_worker(self) -> None:
        with self.assertRaisesRegex(
            CommandError, r"Worker 'unknown' not found"
        ):
            call_command(
                "worker_pool",
                "terminate",
                "unknown",
            )

    def test_unexpected_action(self) -> None:
        command = Command()

        with self.assertRaisesRegex(
            CommandError, r"Action 'does_not_exist' not found"
        ) as exc:
            command.handle(action="does_not_exist")

        self.assertEqual(getattr(exc.exception, "returncode"), 3)
