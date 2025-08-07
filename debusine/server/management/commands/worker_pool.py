# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage worker pools."""

import argparse
import sys
from collections.abc import Callable
from typing import Any, IO, NoReturn, cast

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from debusine.assets.models import AssetCategory
from debusine.db.models import Asset, Worker, WorkerPool
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import WorkerPools
from debusine.server.worker_pools.models import WorkerPoolLimits


class Command(DebusineBaseCommand):
    """Command to manage worker pools."""

    help = "Manage worker pools."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the worker_pool command."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        create = subparsers.add_parser(
            "create", help="Ensure a worker pool exists"
        )
        create.add_argument("name", help="Name")
        create.add_argument(
            "--provider-account",
            type=self.get_provider_account,
            metavar="name",
            help="Name of cloud provider account",
        )
        create.add_argument(
            "--enabled",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Whether this worker pool is active (default: --enabled)",
        )
        create.add_argument(
            "--architectures",
            type=self.split_comma_separated_str,
            help=(
                "List of architectures supported by this pool (comma-separated)"
            ),
        )
        create.add_argument(
            "--tags",
            type=self.split_comma_separated_str,
            default=[],
            help="List of tags supported by this pool (comma-separated)",
        )
        create.add_argument(
            "--specifications",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the specification for the "
                "worker pool. YAML format. Defaults to stdin."
            ),
            default="-",
        )
        create.add_argument(
            "--instance-wide",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Whether this worker pool may be used by any scope "
                "(default: --instance-wide)"
            ),
        )
        create.add_argument(
            "--ephemeral",
            action=argparse.BooleanOptionalAction,
            default=False,
            help=(
                "Whether instances in this worker pool are terminated after "
                "every task (default: --no-ephemeral)"
            ),
        )
        create.add_argument(
            "--limits",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the limits for the "
                "worker pool. YAML format."
            ),
        )

        delete = subparsers.add_parser(
            "delete", help="Delete a worker pool and its workers"
        )
        delete.add_argument(
            "worker_pool", help="Name", type=self.get_worker_pool
        )
        delete.add_argument(
            "--force",
            action="store_true",
            help="Delete the worker pool even if it has active workers.",
        )

        enable = subparsers.add_parser("enable", help="Enable a worker pool")
        enable.add_argument(
            "worker_pool", help="Name", type=self.get_worker_pool
        )

        disable = subparsers.add_parser("disable", help="Disable a worker pool")
        disable.add_argument(
            "worker_pool", help="Name", type=self.get_worker_pool
        )

        list_ = subparsers.add_parser("list", help="List worker pools")
        list_.add_argument(
            "--yaml", action="store_true", help="Machine readable YAML output"
        )

        launch = subparsers.add_parser("launch", help="Launch workers")
        launch.add_argument(
            "worker_pool", help="Name", type=self.get_worker_pool
        )
        launch.add_argument(
            "count", type=int, help="Number of workers to launch."
        )

        terminate = subparsers.add_parser(
            "terminate", help="Terminate a pool worker"
        )
        terminate.add_argument(
            "worker", help="Worker Name", type=self.get_worker
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        for opt in ["specifications", "limits"]:
            if options.get(opt) and options[opt] != sys.stdin:
                options[opt].close()

    def get_provider_account(self, name: str) -> Asset:
        """Look up a cloud provider account asset by name."""
        try:
            return Asset.objects.get(
                category=AssetCategory.CLOUD_PROVIDER_ACCOUNT, data__name=name
            )
        except Asset.DoesNotExist:
            raise CommandError(
                f"Cloud provider account asset {name!r} not found", returncode=3
            )

    def get_worker_pool(self, name: str) -> WorkerPool:
        """Look up a worker pool by name."""
        try:
            return WorkerPool.objects.get(name=name)
        except WorkerPool.DoesNotExist:
            raise CommandError(f"Worker Pool {name!r} not found", returncode=3)

    def get_worker(self, name: str) -> Worker:
        """Look up a worker by name."""
        try:
            return Worker.objects.get(name=name)
        except Worker.DoesNotExist:
            raise CommandError(f"Worker {name!r} not found", returncode=3)

    def split_comma_separated_str(self, combined: str) -> list[str]:
        """Split a comma-separated command line parameter."""
        return [item.strip() for item in combined.split(",")]

    def handle_create(
        self,
        *,
        name: str,
        provider_account: Asset,
        enabled: bool,
        architectures: list[str],
        tags: list[str] | None = None,
        specifications: IO[Any],
        instance_wide: bool,
        ephemeral: bool,
        limits: IO[Any] | None = None,
        **options: Any,
    ) -> NoReturn:
        """
        Ensure a worker pool exists.

        This is idempotent, to make it easier to invoke from Ansible.
        """
        specifications_data = self.parse_yaml_data(specifications.read()) or {}
        limits_data: dict[str, Any] = WorkerPoolLimits().dict()
        if limits is not None:
            limits_data = self.parse_yaml_data(limits.read()) or {}
        with transaction.atomic():
            try:
                worker_pool = WorkerPool.objects.get(name=name)
            except WorkerPool.DoesNotExist:
                worker_pool = WorkerPool(
                    name=name, registered_at=timezone.now()
                )
            worker_pool.provider_account = provider_account
            worker_pool.enabled = enabled
            worker_pool.architectures = architectures
            worker_pool.tags = tags
            worker_pool.specifications = specifications_data
            worker_pool.instance_wide = instance_wide
            worker_pool.ephemeral = ephemeral
            worker_pool.limits = limits_data
            try:
                worker_pool.full_clean()
                worker_pool.save()
            except ValidationError as exc:
                raise CommandError(
                    "Error creating worker_pool: " + "\n".join(exc.messages),
                    returncode=3,
                )

        raise SystemExit(0)

    def handle_delete(
        self, *, worker_pool: WorkerPool, force: bool, **options: Any
    ) -> NoReturn:
        """Delete the worker pool."""
        with transaction.atomic():
            if worker_pool.workers_running.exists() and not force:
                raise CommandError(
                    (
                        f"Worker pool {worker_pool.name!r} has "
                        f"{worker_pool.workers_running.count()} running "
                        f"workers."
                    ),
                    returncode=1,
                )

            for worker in worker_pool.workers_running:
                worker_pool.terminate_worker(worker)

            Worker.objects.filter(worker_pool=worker_pool).delete()
            worker_pool.delete()
        raise SystemExit(0)

    def handle_enable(
        self, *, worker_pool: WorkerPool, **options: Any
    ) -> NoReturn:
        """Enable a worker pool."""
        worker_pool.enabled = True
        worker_pool.save()
        raise SystemExit(0)

    def handle_disable(
        self, *, worker_pool: WorkerPool, **options: Any
    ) -> NoReturn:
        """Disable a worker pool."""
        worker_pool.enabled = False
        worker_pool.save()
        raise SystemExit(0)

    def handle_list(
        self,
        yaml: bool,
        **options: Any,
    ) -> NoReturn:
        """List worker_pools."""
        worker_pools = WorkerPool.objects.all()
        WorkerPools(yaml).print(worker_pools, self.stdout)
        raise SystemExit(0)

    def handle_launch(
        self, *, worker_pool: WorkerPool, count: int, **options: Any
    ) -> NoReturn:
        """Launch workers in a pool."""
        worker_pool.launch_workers(count)
        raise SystemExit(0)

    def handle_terminate(self, *, worker: Worker, **options: Any) -> NoReturn:
        """Terminate a worker in a pool."""
        if not worker.worker_pool:
            raise CommandError(
                f"Worker {worker.name!r} is not a member of a pool.",
                returncode=3,
            )
        worker.worker_pool.terminate_worker(worker)
        raise SystemExit(0)

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Dispatch the requested action."""
        func = cast(
            Callable[..., NoReturn],
            getattr(self, f"handle_{options['action']}", None),
        )
        if func is None:
            raise CommandError(
                f"Action {options['action']!r} not found", returncode=3
            )

        func(*args, **options)
