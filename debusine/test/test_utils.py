# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utility functions for tests."""

import contextlib
import copy
import sys
import traceback
from collections.abc import Callable, Generator, Iterator
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any
from urllib.parse import quote, urljoin

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import DebianSystemTarball
from debusine.client.models import (
    ArtifactResponse,
    FileResponse,
    FilesResponseType,
    PaginatedResponse,
    RemoteArtifact,
    StrictBaseModel,
    WorkRequestResponse,
    WorkflowTemplateResponse,
)
from debusine.tasks import BaseTask

_data_generator_counter = count(0)


def data_generator(size: int) -> Iterator[bytes]:
    """
    Return a data generator of size bytes.

    Each time generates different data.
    """
    while True:
        chunk = bytearray()

        while len(chunk) < size:
            s = str(next(_data_generator_counter)).encode("ascii")
            remaining_space = size - len(chunk)
            chunk.extend(s[:remaining_space])
        yield bytes(chunk)


def yesterday() -> datetime:
    """Return datetime of yesterday."""
    return datetime.now(timezone.utc) - timedelta(days=1)


def tomorrow() -> datetime:
    """Return datetime of tomorrow."""
    return datetime.now(timezone.utc) + timedelta(days=1)


def date_time_to_isoformat_rest_framework(value: datetime) -> str:
    """
    Django REST serialize datetime in a specific way.

    It is reimplemented (simplified) here for Debusine client tests (which
    do not use Django REST).

    See rest_framework/fields.py, DateTimeField.to_representation.
    """
    value_str = value.isoformat()
    if value_str.endswith("+00:00"):
        value_str = value_str[:-6] + "Z"

    return value_str


def create_file_response(**kwargs: Any) -> FileResponse:
    """Return a FileResponse. Use defaults for certain fields."""
    defaults: dict[str, Any] = {
        "size": 0,
        "checksums": {"sha256": "not-used"},
        "type": "file",
        "url": "https://example.com/some-path",
    }
    defaults.update(kwargs)

    return FileResponse(**defaults)


def create_artifact_response(
    base_url: str = "https://example.com/",
    scope: str = "debusine",
    **kwargs: Any,
) -> ArtifactResponse:
    """Return an ArtifactResponse. Use defaults for certain fields."""
    kwargs = kwargs.copy()
    defaults: dict[str, Any] = {
        "category": "Testing",
        "data": {},
        "files_to_upload": [],
        "created_at": datetime.now(timezone.utc),
        "workspace": "Testing",
        "download_tar_gz_url": "https://example.com/some-path",
        "files": FilesResponseType(kwargs.pop("files", {})),
    }
    defaults.update(kwargs)
    defaults.setdefault(
        "url",
        urljoin(
            base_url,
            f"{quote(scope)}/{quote(defaults['workspace'])}/"
            f"artifact/{defaults['id']}/",
        ),
    )

    return ArtifactResponse(**defaults)


def create_remote_artifact(
    base_url: str = "https://example.com/",
    scope: str = "debusine",
    **kwargs: Any,
) -> RemoteArtifact:
    """Return a RemoteArtifact. Use defaults for certain fields."""
    defaults: dict[str, Any] = kwargs.copy()
    defaults.setdefault(
        "url",
        urljoin(
            base_url,
            f"{quote(scope)}/{quote(defaults['workspace'])}/"
            f"artifact/{defaults['id']}/",
        ),
    )

    return RemoteArtifact(**defaults)


def create_work_request_response(
    base_url: str = "https://example.com/",
    scope: str = "debusine",
    **kwargs: Any,
) -> WorkRequestResponse:
    """Return a WorkRequestResponse. Use defaults for certain fields."""
    defaults: dict[str, Any] = {
        "id": 11,
        "created_at": datetime.now(timezone.utc),
        "status": "pending",
        "result": "",
        "task_type": "Worker",
        "task_name": "sbuild",
        "task_data": {},
        "priority_base": 0,
        "priority_adjustment": 0,
        "artifacts": [],
        "workspace": "Testing",
    }
    defaults.update(kwargs)
    defaults.setdefault(
        "url",
        urljoin(
            base_url,
            f"{quote(scope)}/{quote(defaults['workspace'])}/"
            f"work-request/{defaults['id']}/",
        ),
    )

    return WorkRequestResponse(**defaults)


def create_workflow_template_response(
    base_url: str = "https://example.com/",
    scope: str = "debusine",
    **kwargs: Any,
) -> WorkflowTemplateResponse:
    """Return a WorkflowTemplateResponse. Use defaults for certain fields."""
    defaults: dict[str, Any] = {
        "id": 11,
        "name": "noop",
        "task_name": "noop",
        "task_data": {},
        "priority": 0,
        "workspace": "Testing",
    }
    defaults.update(kwargs)
    defaults.setdefault(
        "url",
        urljoin(
            base_url,
            f"{quote(scope)}/{quote(defaults['workspace'])}/"
            f"workflow-template/{quote(defaults['name'])}/",
        ),
    )

    return WorkflowTemplateResponse(**defaults)


def create_listing_response(
    *args: StrictBaseModel, next_url: str | None = None
) -> PaginatedResponse:
    """Return a single page of PaginatedResponse, containing args."""
    return PaginatedResponse(
        count=None,
        next=(
            pydantic.parse_obj_as(pydantic.AnyUrl, next_url)
            if next_url is not None
            else None
        ),
        previous=None,
        results=[arg.dict() for arg in args],
    )


def create_system_tarball_data(
    codename: str = "sid",
    variant: str | None = None,
    architecture: str = "amd64",
) -> DebianSystemTarball:
    """Return data suitable for a test debian:system-tarball artifact."""
    return DebianSystemTarball(
        filename="system.tar.xz",
        vendor="debian",
        codename=codename,
        components=["main"],
        mirror=pydantic.parse_obj_as(
            pydantic.AnyUrl, "https://deb.debian.org/debian"
        ),
        variant=variant,
        pkglist={},
        architecture=architecture,
        with_dev=False,
        with_init=False,
    )


@contextlib.contextmanager
def preserve_task_registry() -> Generator[None, None, None]:
    """Make task registry changes ephemeral."""
    orig = BaseTask._sub_tasks
    BaseTask._sub_tasks = copy.deepcopy(BaseTask._sub_tasks)
    try:
        yield
    finally:
        BaseTask._sub_tasks = orig


@contextlib.contextmanager
def print_sql_and_stack() -> Generator[None, None, None]:
    """Print each SQL query executed, with python stack trace."""
    from django.db import connection

    class Printer:
        """Print numbered SQL queries and stack traces."""

        def __init__(self) -> None:
            """Set the initial counter."""
            self.index = 0

        def __call__(
            self,
            execute: Callable[[str, Any, bool, dict[str, Any]], Any],
            sql: str,
            params: Any,
            many: bool,
            context: dict[str, Any],
        ) -> Any:
            """Interface with the django query executor to print traces."""
            self.index += 1
            print(f"#{self.index}:", "-" * 10, "8<", "-" * 10, file=sys.stderr)
            traceback.print_stack(file=sys.stderr)
            print("  SQL Query:", sql, file=sys.stderr)
            return execute(sql, params, many, context)

    with connection.execute_wrapper(Printer()):
        yield
