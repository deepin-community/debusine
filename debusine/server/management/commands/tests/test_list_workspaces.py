# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command list_workspaces."""

from datetime import timedelta
from typing import ClassVar

import yaml

from debusine.db.context import context
from debusine.db.models import Workspace
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.test.django import TestCase


class ListWorkspacesCommandTests(TabularOutputTests, TestCase):
    """Test for list_workspaces management command."""

    workspaces: ClassVar[list[Workspace]]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        ws1 = cls.playground.get_default_workspace()

        scope2 = cls.playground.get_or_create_scope("scope2")
        ws2 = cls.playground.create_workspace(
            scope=scope2, name="TestWorkspace2Public"
        )
        ws2.public = True
        ws2.save()

        ws3 = cls.playground.create_workspace(
            scope=scope2, name="TestWorkspace3WithOtherWorkspaces"
        )

        ws4 = cls.playground.create_workspace(
            scope=scope2, name="TestWorkspace4WithExpiration"
        )
        ws4.default_expiration_delay = timedelta(days=400)
        ws4.save()

        cls.workspaces = [ws1, ws2, ws3, ws4]

    def test_list_workspaces(self) -> None:
        """List workspace command prints workspace information."""
        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command('list_workspaces')

        def _format_expiration(ws: Workspace) -> str:
            if days := ws.default_expiration_delay.days:
                return str(days)
            return "Never"

        self.assertEqual(output.col(0), [str(ws) for ws in self.workspaces])
        self.assertEqual(
            output.col(1), [str(ws.public) for ws in self.workspaces]
        )
        self.assertEqual(
            output.col(2),
            [_format_expiration(ws) for ws in self.workspaces],
        )

    def test_list_workspaces_yaml(self) -> None:
        """List workspace command prints yaml workspace information."""
        stdout, stderr, _ = call_command('list_workspaces', "--yaml")
        data = yaml.safe_load(stdout)
        self.assertEqual(
            data,
            [
                {
                    'expiration': 'Never',
                    'name': 'debusine/System',
                    'public': True,
                },
                {
                    'expiration': 'Never',
                    'name': 'scope2/TestWorkspace2Public',
                    'public': True,
                },
                {
                    'expiration': 'Never',
                    'name': 'scope2/TestWorkspace3WithOtherWorkspaces',
                    'public': False,
                },
                {
                    'expiration': 400,
                    'name': 'scope2/TestWorkspace4WithExpiration',
                    'public': False,
                },
            ],
        )
