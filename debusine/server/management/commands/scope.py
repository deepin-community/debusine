# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-admin command to manage scopes.

Note: to make commands easier to be invoked from Ansible, we take care to make
them idempotent.
"""

from argparse import BooleanOptionalAction
from collections.abc import Callable
from typing import Any, NoReturn, cast

import rich
from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.db import transaction
from django.db.models import F
from rich.table import Table

from debusine.db.context import context
from debusine.db.models import (
    DEFAULT_FILE_STORE_NAME,
    File,
    FileStore,
    FileStoreInScope,
    Group,
    Scope,
)
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import get_scope


class Command(DebusineBaseCommand):
    """Command to manage scopes."""

    help = "Manage scopes"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        create = subparsers.add_parser("create", help="Ensure a scope exists")
        create.add_argument("name", help="Name for the new scope")
        create.add_argument(
            "--with-owners-group",
            type=str,
            nargs="?",
            metavar="name",
            const="Owners",
            help="Ensure the scope has the named owners group"
            " (name defaults to 'Owners')",
        )
        create.add_argument(
            "--label",
            type=str,
            help="Scope label. Defaults to capitalized name",
        )
        create.add_argument(
            "--icon",
            type=str,
            help="Optional scope icon, as a path resolved"
            " by the static tag in templates",
        )
        create.add_argument(
            "--file-store",
            dest="file_store_name",
            metavar="NAME",
            default=DEFAULT_FILE_STORE_NAME,
            help=(
                "Name of the initial file store to use for this scope"
                f" (default: {DEFAULT_FILE_STORE_NAME})"
            ),
        )

        rename = subparsers.add_parser("rename", help="Rename a scope")
        rename.add_argument("scope", help="Scope to rename")
        rename.add_argument("name", help="New name for the scope")

        add_file_store = subparsers.add_parser(
            "add_file_store", help="Add a file store to a scope"
        )
        add_file_store.add_argument("scope_name", help="Scope to manage")
        add_file_store.add_argument("file_store_name", help="File store to add")

        edit_file_store = subparsers.add_parser(
            "edit_file_store", help="Edit a file store in a scope"
        )
        edit_file_store.add_argument("scope_name", help="Scope to manage")
        edit_file_store.add_argument(
            "file_store_name", help="File store to edit"
        )

        remove_file_store = subparsers.add_parser(
            "remove_file_store", help="Remove a file store from a scope"
        )
        remove_file_store.add_argument("scope_name", help="Scope to manage")
        remove_file_store.add_argument(
            "file_store_name", help="File store to remove"
        )

        for subparser in (add_file_store, edit_file_store):
            subparser.add_argument(
                "--upload-priority",
                help=(
                    "Upload priority for the selected file store (use the "
                    "empty string to unset)"
                ),
            )
            subparser.add_argument(
                "--download-priority",
                help=(
                    "Download priority for the selected file store (use the "
                    "empty string to unset)"
                ),
            )
            subparser.add_argument(
                "--populate",
                action=BooleanOptionalAction,
                help=(
                    "Ensure that this store has a copy of all files in the "
                    "scope"
                ),
            )
            subparser.add_argument(
                "--drain",
                action=BooleanOptionalAction,
                help=(
                    "Schedule all files in this scope to be moved to some "
                    "other store in the same scope"
                ),
            )
            subparser.add_argument(
                "--drain-to",
                help=(
                    "Constrain --drain to use the store with this name in the "
                    "scope"
                ),
            )
            subparser.add_argument(
                "--read-only",
                action=BooleanOptionalAction,
                help="Do not add new files to this store",
            )
            subparser.add_argument(
                "--write-only",
                action=BooleanOptionalAction,
                help="Do not read files from this store",
            )
            subparser.add_argument(
                "--soft-max-size",
                help=(
                    "The number of bytes that the file store can hold for this "
                    "scope (use the empty string to unset)"
                ),
            )

        show = subparsers.add_parser("show", help="Show a scope")
        show.add_argument("scope", help="Scope to show")

    def get_file_store(self, name: str) -> FileStore:
        """Look up a file store by name."""
        try:
            return FileStore.objects.get(name=name)
        except FileStore.DoesNotExist:
            raise CommandError(f"File store {name!r} not found", returncode=3)

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

    @context.disable_permission_checks()
    def handle_create(
        self,
        *,
        name: str,
        with_owners_group: str | None = None,
        label: str | None,
        icon: str | None,
        file_store_name: str,
        **options: Any,
    ) -> NoReturn:
        """
        Create a scope, initialized with an Owners group.

        This is idempotent, and it makes sure the named scope exists, it has an
        "Owners" group, and that group has the ADMIN role on the scope.  It
        also makes sure that the named scope uses the given file store,
        defaulting to upload and download priorities of 100.
        """
        label = label or name.capitalize()
        icon = icon or ""
        with transaction.atomic():
            scope, _ = Scope.objects.get_or_create(
                name=name, defaults={"label": label, "icon": icon}
            )
            if with_owners_group:
                # Make sure the scope has an Owners group
                admin_group, _ = Group.objects.get_or_create(
                    scope=scope, name=with_owners_group
                )
                admin_group.assign_role(scope, "owner")

            if scope.label != label:
                scope.label = label
            if scope.icon != icon:
                scope.icon = icon

            file_store = self.get_file_store(file_store_name)
            scope.file_stores.add(
                file_store,
                through_defaults={
                    "upload_priority": 100,
                    "download_priority": 100,
                },
            )

            try:
                scope.full_clean()
            except ValidationError as exc:
                self.stderr.write("New scope would be invalid:")
                for field, errors in exc.message_dict.items():
                    for error in errors:
                        self.stderr.write(f"* {field}: {error}")
                raise SystemExit(3)
            scope.save()

        raise SystemExit(0)

    def handle_rename(
        self, *, scope: str, name: str, **options: Any
    ) -> NoReturn:
        """Rename a scope."""
        s = get_scope(scope)

        if name == s.name:
            raise SystemExit(0)

        s.name = name
        try:
            s.full_clean()
        except ValidationError as exc:
            self.stderr.write("Renamed scope would be invalid:")
            for field, errors in exc.message_dict.items():
                for error in errors:
                    self.stderr.write(f"* {field}: {error}")
            raise SystemExit(3)

        s.save()

        raise SystemExit(0)

    def handle_add_file_store(
        self,
        *,
        scope_name: str,
        file_store_name: str,
        upload_priority: str | None = None,
        download_priority: str | None = None,
        populate: bool | None = None,
        drain: bool | None = None,
        drain_to: str | None = None,
        read_only: bool | None = None,
        write_only: bool | None = None,
        soft_max_size: str | None = None,
        **options: Any,
    ) -> NoReturn:
        """Add a file store to a scope."""
        scope = get_scope(scope_name)
        file_store = self.get_file_store(file_store_name)
        through_defaults = {
            "upload_priority": upload_priority or None,
            "download_priority": download_priority or None,
            "populate": populate or False,
            "drain": drain or False,
            "drain_to": drain_to or None,
            "read_only": read_only or False,
            "write_only": write_only or False,
            "soft_max_size": soft_max_size or None,
        }
        scope.file_stores.add(file_store, through_defaults=through_defaults)

        raise SystemExit(0)

    def handle_edit_file_store(
        self,
        *,
        scope_name: str,
        file_store_name: str,
        upload_priority: str | None = None,
        download_priority: str | None = None,
        populate: bool | None = None,
        drain: bool | None = None,
        drain_to: str | None = None,
        read_only: bool | None = None,
        write_only: bool | None = None,
        soft_max_size: str | None = None,
        **options: Any,
    ) -> NoReturn:
        """Edit properties of a file store in a scope."""
        scope = get_scope(scope_name)
        try:
            file_store_extra = scope.filestoreinscope_set.get(
                file_store__name=file_store_name
            )
        except FileStoreInScope.DoesNotExist:
            raise CommandError(
                f"Scope {scope.name!r} has no file store named "
                f"{file_store_name!r}",
                returncode=3,
            )
        if upload_priority is not None:
            file_store_extra.upload_priority = upload_priority or None
        if download_priority is not None:
            file_store_extra.download_priority = download_priority or None
        if populate is not None:
            file_store_extra.populate = populate
        if drain is not None:
            file_store_extra.drain = drain
        if drain_to is not None:
            file_store_extra.drain_to = drain_to or None
        if read_only is not None:
            file_store_extra.read_only = read_only
        if write_only is not None:
            file_store_extra.write_only = write_only
        if soft_max_size is not None:
            file_store_extra.soft_max_size = soft_max_size or None
        file_store_extra.save()

        raise SystemExit(0)

    def handle_remove_file_store(
        self, scope_name: str, file_store_name: str, **options: Any
    ) -> NoReturn:
        """Remove a file store from a scope."""
        scope = get_scope(scope_name)
        try:
            file_store = scope.file_stores.get(name=file_store_name)
        except FileStore.DoesNotExist:
            raise CommandError(
                f"Scope {scope.name!r} has no file store named "
                f"{file_store_name!r}",
                returncode=3,
            )
        files_in_this_store = File.objects.filter(
            artifact__workspace__scope=scope, filestore=file_store
        )
        files_in_other_stores = File.objects.filter(
            artifact__workspace__scope=scope,
            filestore__in=scope.file_stores.exclude(pk=file_store.pk),
        )
        if files_in_this_store.exclude(pk__in=files_in_other_stores).exists():
            raise CommandError(
                f"Scope {scope.name!r} contains some files that are only in "
                f"file store {file_store_name!r}; drain it first",
                returncode=3,
            )
        scope.file_stores.remove(file_store)

        raise SystemExit(0)

    def handle_show(self, scope: str, **options: Any) -> NoReturn:
        """Show a scope."""
        s = get_scope(scope)

        # TODO: This layout is a bit odd: we're trying to fit everything
        # into a single table despite the true structure being nested.  It
        # does the job for now and is compatible with TabularOutputTests.
        # TODO: Add an option for YAML output.
        table = Table(box=rich.box.MINIMAL_DOUBLE_HEAD)
        table.add_column("Name")
        table.add_column("Label")
        table.add_column("File store name")
        table.add_column("Backend")
        table.add_column("Upload priority")
        table.add_column("Download priority")
        table.add_column("# Files")
        for i, file_store_extra in enumerate(
            s.filestoreinscope_set.order_by(
                F("upload_priority").desc(nulls_last=True),
                F("download_priority").desc(nulls_last=True),
            )
        ):
            file_store = file_store_extra.file_store
            files_in_this_store = File.objects.filter(
                artifact__workspace__scope=s, filestore=file_store
            )
            table.add_row(
                s.name if i == 0 else None,
                s.label if i == 0 else None,
                file_store.name,
                file_store.backend,
                (
                    None
                    if file_store_extra.upload_priority is None
                    else str(file_store_extra.upload_priority)
                ),
                (
                    None
                    if file_store_extra.download_priority is None
                    else str(file_store_extra.download_priority)
                ),
                str(files_in_this_store.count()),
            )
        rich.print(table)

        raise SystemExit(0)
