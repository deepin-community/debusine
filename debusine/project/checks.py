# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine checks using Django checks framework."""

import getpass
import os
import pathlib
from collections.abc import Iterable, Sequence
from functools import partial
from typing import Any

from django.apps.config import AppConfig
from django.conf import settings
from django.core.checks import Error, register

from debusine.django.checks import all_migrations_applied, is_migration_command


def _check_directories_owners(
    setting_names: Iterable[str], *, missing_ok: bool
) -> list[Error]:
    current_user = getpass.getuser()
    acceptable_owners = {'root', current_user}

    errors = []

    for setting_name in setting_names:
        directory = pathlib.Path(getattr(settings, setting_name))

        if not directory.exists() and missing_ok:
            continue
        elif not directory.exists():
            errors.append(Error(f"{directory} must exist"))
        elif directory.owner() not in acceptable_owners:
            errors.append(
                Error(
                    f"{directory} owner ({directory.owner()}) must match "
                    f"current user ({current_user})"
                )
            )

    return errors


@register()
def directories_owners_check(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any  # noqa: U100
) -> list[Error]:
    """Check optional directories, if they exist, have the correct owner."""
    if is_migration_command():
        return []

    return _check_directories_owners(
        [
            "DEBUSINE_DATA_PATH",
            "STATIC_ROOT",
            "MEDIA_ROOT",
            "DEBUSINE_CACHE_DIRECTORY",
            "DEBUSINE_TEMPLATE_DIRECTORY",
            "DEBUSINE_UPLOAD_DIRECTORY",
            "DEBUSINE_STORE_DIRECTORY",
        ],
        missing_ok=True,
    )


@register()
def secret_key_not_default_in_debug_0(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any  # noqa: U100
) -> list[Error]:
    """Check SECRET_KEY is not the default if Debug=False (unless testing)."""
    if getattr(settings, "TEST_MODE", False):
        # In Django the tests run with settings.DEBUG = False by design.
        # When testing Debusine it is allowed to use the default SECRET_KEY
        return []

    if not settings.DEBUG and settings.SECRET_KEY.startswith("default:"):
        return [
            Error(
                'Default SECRET_KEY cannot be used in DEBUG=False. Make sure '
                'to use "SECRET_KEY = read_secret_key(path)" from selected.py '
                'settings file',
                hint="Generate a secret key using the command: "
                "$ python3 -c 'from django.core.management.utils import "
                "get_random_secret_key; print(get_random_secret_key())'",
            )
        ]

    return []


@register()
def disable_automatic_scheduling_false_in_production(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any  # noqa: U100
) -> list[Error]:
    """Check DISABLE_AUTOMATIC_SCHEDULING is enabled only in TEST_MODE."""
    if getattr(settings, "DISABLE_AUTOMATIC_SCHEDULING", False) and not getattr(
        settings, "TEST_MODE", False
    ):
        return [
            Error(
                "DISABLE_AUTOMATIC_SCHEDULING can only be enabled in TEST_MODE",
                hint="Remove DISABLE_AUTOMATIC_SCHEDULING=True "
                "from debusine-server settings",
            )
        ]

    return []


register(partial(all_migrations_applied, "debusine-admin"))


@register()
def local_file_store_directories_writable(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any  # noqa: U100
) -> list[Error]:
    """
    Check that each local file store base_directory exist.

    Avoid checking it if a migration command is being run.
    """
    if is_migration_command():
        return []

    from debusine.db.models import FileStore
    from debusine.server.file_backend.local import LocalFileBackend

    errors = []

    for file_store in FileStore.objects.filter(
        backend=FileStore.BackendChoices.LOCAL
    ).order_by("name"):
        file_store_backend = file_store.get_backend_object()
        assert isinstance(file_store_backend, LocalFileBackend)
        base_directory = file_store_backend.base_directory()
        if not os.access(base_directory, os.W_OK):
            errors.append(
                Error(
                    f'FileStore "{file_store.name}" has a non-existent '
                    f'or non-writable base_directory: '
                    f'"{str(file_store_backend.base_directory())}"',
                    hint="Create the directory, change the permissions "
                    "or change the FileStore configuration",
                )
            )

    return errors
