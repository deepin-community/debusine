# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage file stores."""

import argparse
import sys
from collections.abc import Callable
from typing import Any, IO, NoReturn, cast

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.db import IntegrityError, transaction

from debusine.assets.models import AssetCategory
from debusine.db.models import Asset, FileStore
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to manage file stores."""

    help = "Manage file stores."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the file_store command."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        create = subparsers.add_parser(
            "create", help="Ensure a file store exists"
        )
        create.add_argument("name", help="Name")
        create.add_argument(
            "backend",
            help="Backend type",
            choices=dict(FileStore.BackendChoices.choices).keys(),
        )
        create.add_argument(
            "--configuration",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the configuration for the "
                "file store. YAML format. Defaults to stdin."
            ),
            default="-",
        )
        create.add_argument(
            "--instance-wide",
            action=argparse.BooleanOptionalAction,
            help=(
                "Whether this file store may be used by any scope "
                "(default: --instance-wide)"
            ),
        )
        create.add_argument(
            "--soft-max-size",
            type=int,
            help=(
                "Soft limit in bytes for the total capacity of the store; may "
                "be exceeded temporarily during uploads, and cleaned up later "
                "by storage maintenance"
            ),
        )
        create.add_argument(
            "--max-size",
            type=int,
            help=(
                "Hard limit in bytes for the total capacity of the store; may "
                "not be exceeded even temporarily during uploads"
            ),
        )
        create.add_argument(
            "--provider-account",
            type=self.get_provider_account,
            metavar="name",
            help="Name of cloud provider account",
        )

        delete = subparsers.add_parser("delete", help="Delete a file store")
        delete.add_argument("name", help="Name")
        delete.add_argument(
            "--force",
            action="store_true",
            help=(
                "Delete the file store even if it still contains files or is "
                "still in any scope"
            ),
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if "configuration" in options and options["configuration"] != sys.stdin:
            options["configuration"].close()

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

    def handle_create(
        self,
        *,
        name: str,
        backend: str,
        configuration: IO[Any],
        instance_wide: bool | None = None,
        soft_max_size: int | None = None,
        max_size: int | None = None,
        provider_account: Asset | None = None,
        **options: Any,
    ) -> NoReturn:
        """
        Ensure a file store exists.

        This is idempotent, to make it easier to invoke from Ansible.
        """
        configuration_data = self.parse_yaml_data(configuration.read()) or {}

        with transaction.atomic():
            file_store, _ = FileStore.objects.get_or_create(
                name=name,
                defaults={
                    "backend": backend,
                    "configuration": configuration_data,
                    "instance_wide": (
                        True if instance_wide is None else instance_wide
                    ),
                    "soft_max_size": soft_max_size,
                    "max_size": max_size,
                    "provider_account": provider_account,
                },
            )
            if file_store.backend != backend:
                raise CommandError(
                    "Cannot change backend of existing file store", returncode=3
                )
            if file_store.configuration != configuration_data:
                # In some cases this might be possible, but in general it is
                # unsafe: for example, changing the base directory of a
                # local file store would orphan any existing files there.
                raise CommandError(
                    "Cannot change configuration of existing file store",
                    returncode=3,
                )
            if (
                instance_wide is not None
                and file_store.instance_wide != instance_wide
            ):
                file_store.instance_wide = instance_wide
            if (
                soft_max_size is not None
                and file_store.soft_max_size != soft_max_size
            ):
                file_store.soft_max_size = soft_max_size
            if max_size is not None and file_store.max_size != max_size:
                file_store.max_size = max_size
            if (
                provider_account is not None
                and provider_account != file_store.provider_account
            ):
                file_store.provider_account = provider_account

            try:
                file_store.full_clean()
                file_store.save()
            except ValidationError as exc:
                raise CommandError(
                    "Error creating file store: " + "\n".join(exc.messages),
                    returncode=3,
                )
            except IntegrityError as exc:
                raise CommandError(
                    f"Error creating file store: {exc}", returncode=3
                )

        raise SystemExit(0)

    def handle_delete(
        self, *, name: str, force: bool, **options: Any
    ) -> NoReturn:
        """Delete the file store."""
        with transaction.atomic():
            try:
                file_store = FileStore.objects.get(name=name)
            except FileStore.DoesNotExist:
                raise CommandError(
                    f"File store {name!r} does not exist", returncode=3
                )

            if (num_files := file_store.files.count()) and not force:
                raise CommandError(
                    f"File store {name!r} still contains {num_files} files; "
                    f"drain it first",
                    returncode=3,
                )

            if (num_scopes := file_store.scopes.count()) and not force:
                raise CommandError(
                    (
                        f"File store {name!r} is still used by {num_scopes} "
                        f"scopes; use 'debusine-admin scope remove_file_store' "
                        f"first"
                    ),
                    returncode=3,
                )

            file_store.files.clear()
            file_store.scopes.clear()
            file_store.delete()

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
