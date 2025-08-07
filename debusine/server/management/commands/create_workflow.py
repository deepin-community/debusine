# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-admin command to create workflows.

This is similar to the API endpoint, but it doesn't require a token so it's
easier to use in simple automation.
"""

import argparse
import sys
from typing import Any, NoReturn

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, CommandParser
from django.db import transaction

from debusine.db.context import context
from debusine.db.models import (
    SYSTEM_USER_NAME,
    User,
    WorkRequest,
    WorkflowTemplate,
)
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import get_workspace


class Command(DebusineBaseCommand):
    """Command to create a workflow."""

    help = "Create a new workflow"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the create_workflow command."""
        parser.add_argument("template_name", help="Template name")
        parser.add_argument(
            "--created-by",
            help="Name of the user creating the workflow",
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

    def cleanup_arguments(self, *args: Any, **options: Any) -> None:
        """Clean up objects created by parsing arguments."""
        if options["data"] != sys.stdin:
            options["data"].close()

    @context.local()
    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Create the workflow."""
        template_name = options["template_name"]
        created_by_name = options["created_by"]
        data = self.parse_yaml_data(options["data"].read()) or {}

        workspace = get_workspace(options["workspace"])

        try:
            created_by = get_user_model().objects.get(username=created_by_name)
        except User.DoesNotExist:
            raise CommandError(
                f'User "{created_by_name}" not found', returncode=3
            )

        try:
            template = WorkflowTemplate.objects.get(
                name=template_name, workspace=workspace
            )
        except WorkflowTemplate.DoesNotExist:
            raise CommandError(
                f'Workflow template "{template_name}" not found', returncode=3
            )

        context.set_scope(workspace.scope)
        context.set_user(created_by)
        workspace.set_current()

        with context.disable_permission_checks(), transaction.atomic():
            try:
                workflow = WorkRequest.objects.create_workflow(
                    template=template, data=data
                )
                workflow.full_clean()
            except Exception as e:
                raise CommandError(
                    f"Failed to create workflow: {e}", returncode=3
                )

        raise SystemExit(0)
