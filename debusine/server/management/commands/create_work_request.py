# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-admin command to create work requests.

This has fewer restrictions on task types or event reactions than the API
endpoint, and doesn't require a token so it's easier to use in simple
automation.
"""

import argparse
import sys
from typing import Any, NoReturn

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, CommandParser
from django.db import transaction

from debusine.artifacts.models import TaskTypes
from debusine.db.models import SYSTEM_USER_NAME, User, WorkRequest
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import get_workspace


class Command(DebusineBaseCommand):
    """Command to create a work request."""

    help = "Create a new work request"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_work_request command."""
        parser.add_argument(
            "task_type",
            choices=[
                TaskTypes.WORKER.lower(),
                TaskTypes.SERVER.lower(),
                TaskTypes.SIGNING.lower(),
            ],
            help="Task type",
        )
        parser.add_argument("task_name", help="Task name")
        parser.add_argument(
            "--created-by",
            help="Name of the user creating the work request",
            default=SYSTEM_USER_NAME,
        )
        parser.add_argument(
            "--workspace",
            metavar="scope/name",
            help="Workspace",
            default=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )
        parser.add_argument(
            "--data",
            type=argparse.FileType("r"),
            help=(
                "File path (or - for stdin) to read the data for the work "
                "request. YAML format. Defaults to stdin."
            ),
            default="-",
        )
        parser.add_argument(
            "--event-reactions",
            type=argparse.FileType("r"),
            help="File path in YAML format requesting notifications.",
        )

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if options["data"] != sys.stdin:
            options["data"].close()
        if options["event_reactions"] not in {None, sys.stdin}:
            options["event_reactions"].close()

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create the work request."""
        task_type = TaskTypes[options["task_type"].upper()]
        task_name = options["task_name"]
        created_by_name = options["created_by"]
        data = self.parse_yaml_data(options["data"].read()) or {}
        if options["event_reactions"] is not None:
            event_reactions = (
                self.parse_yaml_data(options["event_reactions"].read()) or {}
            )
        else:
            event_reactions = {}

        try:
            created_by = get_user_model().objects.get(username=created_by_name)
        except User.DoesNotExist:
            raise CommandError(
                f'User "{created_by_name}" not found', returncode=3
            )

        workspace = get_workspace(options["workspace"])

        with transaction.atomic():
            try:
                work_request = WorkRequest.objects.create(
                    workspace=workspace,
                    created_by=created_by,
                    task_type=task_type,
                    task_name=task_name,
                    task_data=data,
                    event_reactions_json=event_reactions,
                )
                work_request.full_clean()
            except Exception as e:
                raise CommandError(
                    f"Failed to create work request: {e}", returncode=3
                )
            else:
                print("---")
                print(yaml.safe_dump({"work_request_id": work_request.id}))

        raise SystemExit(0)
