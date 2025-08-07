# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Migrate autopkgtest tasks from extra_apt_sources to extra_repositories."""

import logging
import re

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

from debusine.tasks.models import ExtraRepository, TaskTypes

log = logging.getLogger(__name__)


def migrate_to_extra_repositories(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate autopkgtest tasks to extra_repositories."""
    WorkRequest = apps.get_model('db', 'WorkRequest')

    source_re = re.compile(
        r"""^
        (?P<type>deb(?:-src)?)\s+
        (?:\[(?P<options>.+)\]\s+)?
        (?P<mirror>\S+)\s+
        (?P<suite>\S+)
        (?:\s+(?P<components>\S.*?)\s*)?
        $""",
        re.VERBOSE,
    )
    for work_request in WorkRequest.objects.filter(
        task_type=TaskTypes.WORKER,
        task_name="autopkgtest",
        task_data__has_key="extra_apt_sources",
    ):
        extra_apt_sources = work_request.task_data.pop("extra_apt_sources")
        extra_repositories: list[dict[str, str]] = []
        for source in extra_apt_sources:
            m = source_re.match(source)
            if not m:
                log.warning(
                    "Unable to parse apt source %r in work request %i, ignored",
                    source,
                    work_request.id,
                )
                continue
            if m.group("type") == "deb-src":
                log.warning(
                    "Ignoring deb-src apt source %r in work request %i",
                    source,
                    work_request.id,
                )
                continue
            if m.group("options"):
                log.warning(
                    "Ignoring options in apt source %r in work request %i",
                    source,
                    work_request.id,
                )
            repo = {
                "url": m.group("mirror"),
                "suite": m.group("suite"),
            }
            if components := m.group("components"):
                repo["components"] = components.split()
            extra_repositories.append(repo)
        if extra_repositories:
            work_request.task_data["extra_repositories"] = extra_repositories
        work_request.save()


def migrate_to_extra_apt_sources(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Migrate autopkgtest tasks back to extra_apt_sources."""
    WorkRequest = apps.get_model('db', 'WorkRequest')

    for work_request in WorkRequest.objects.filter(
        task_type=TaskTypes.WORKER,
        task_name="autopkgtest",
        task_data__has_key="extra_repositories",
    ):
        extra_repositories = work_request.task_data.pop("extra_repositories")
        extra_apt_sources: list[str] = []
        for repo_dict in extra_repositories:
            repo = ExtraRepository.parse_obj(repo_dict)
            if repo.signing_key:
                log.warning(
                    "Unable to represent apt signing_key %r in "
                    "extra_apt_sources in work request %i",
                    repo,
                    work_request.id,
                )
            components = ' '.join(repo.components) if repo.components else ''
            extra_apt_sources.append(
                f"deb {repo.url} {repo.suite} {components}".strip()
            )
        work_request.task_data["extra_apt_sources"] = extra_apt_sources
        work_request.save()


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0111_workrequest_workflow_runtime_status"),
    ]

    operations = [
        migrations.RunPython(
            migrate_to_extra_repositories, migrate_to_extra_apt_sources
        )
    ]
