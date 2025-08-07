# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for management/utils.py."""

import datetime
from typing import assert_never

from django.conf import settings
from django.core.management import CommandError

from debusine.db.models import Workspace
from debusine.server.management import management_utils
from debusine.test.django import TestCase


class ManagementUtilsTests(TestCase):
    """Tests for methods in management/management_utils.py."""

    def test_column_to_data(self) -> None:
        """Test Column.to_data."""
        col = management_utils.Column("foo", "Foo")
        for val in (None, 1, "str", datetime.datetime.today()):
            with self.subTest(val=val):
                self.assertIs(col.to_data(val), val)

    def test_column_to_rich(self) -> None:
        """Test Column.to_rich."""
        col = management_utils.Column("foo", "Foo")
        dt = datetime.datetime.today()
        for val, expected in (
            (None, "-"),
            (1, "1"),
            ("str", "str"),
            (dt, dt.isoformat()),
        ):
            with self.subTest(val=val):
                self.assertEqual(col.to_rich(val), expected)

    def test_attrcolumn_to_data(self) -> None:
        """Test Column.to_data."""
        self.value = 42
        col = management_utils.AttrColumn("value", "Value", "value")
        self.assertIs(col.to_data(self), 42)

    def test_get_scope(self) -> None:
        """Test get_scope."""
        self.assertEqual(
            management_utils.get_scope(settings.DEBUSINE_DEFAULT_SCOPE),
            self.playground.get_default_scope(),
        )
        with self.assertRaisesRegex(
            CommandError, r"Scope 'test' not found"
        ) as exc:
            management_utils.get_scope("test")
        self.assertEqual(getattr(exc.exception, "returncode"), 3)

    def test_get_workspace(self) -> None:
        """Test get_workspace."""
        default_workspace = self.playground.get_default_workspace()
        scope = self.playground.get_or_create_scope("scope")
        workspace = self.playground.create_workspace(
            name="workspace", scope=scope
        )
        for name, require_scope, expected in (
            (settings.DEBUSINE_DEFAULT_WORKSPACE, False, default_workspace),
            (
                "workspace",
                False,
                f"Workspace 'workspace' not found in scope "
                f"'{settings.DEBUSINE_DEFAULT_SCOPE}'",
            ),
            (
                "workspace",
                True,
                "scope_workspace 'workspace' should be in the form "
                "'scopename/workspacename'",
            ),
            (
                f"{settings.DEBUSINE_DEFAULT_SCOPE}/"
                f"{settings.DEBUSINE_DEFAULT_WORKSPACE}",
                False,
                default_workspace,
            ),
            ("scope/workspace", False, workspace),
            (
                "does-not-exist/workspace",
                False,
                "Scope 'does-not-exist' not found",
            ),
            (
                f"{settings.DEBUSINE_DEFAULT_SCOPE}/workspace",
                False,
                f"Workspace 'workspace' not found in scope "
                f"'{settings.DEBUSINE_DEFAULT_SCOPE}'",
            ),
        ):
            with self.subTest(name=name):
                match expected:
                    case Workspace():
                        self.assertEqual(
                            management_utils.get_workspace(
                                name, require_scope=require_scope
                            ),
                            expected,
                        )
                    case str():
                        with self.assertRaisesRegex(
                            CommandError, expected
                        ) as exc:
                            management_utils.get_workspace(
                                name, require_scope=require_scope
                            )
                        self.assertEqual(exc.exception.returncode, 3)
                    case _ as unreachable:
                        assert_never(unreachable)
