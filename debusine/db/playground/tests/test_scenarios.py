# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the database playground scenarios."""

from typing import Any, Generic, TypeVar

from django.contrib.auth.hashers import check_password

from debusine.db.context import context
from debusine.db.models import Scope, Workspace
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.utils import extract_generic_type_arguments

S = TypeVar("S", bound=scenarios.Scenario)


class ScenarioTestBase(TestCase, Generic[S]):
    """Base class for scenario tests."""

    scenario_cls: type[S]
    built_scenario: S | None
    scenario_kwargs: dict[str, Any]

    def setUp(self) -> None:
        """Set up backend for lazily building a scenario."""
        super().setUp()
        self.built_scenario = None
        self.scenario_kwargs = {}

    def build_scenario(self, **kwargs: Any) -> None:
        """Build the scenario for the current test."""
        self.built_scenario = self.scenario_cls(**kwargs)
        self.playground.build_scenario(
            self.built_scenario,
            set_current=kwargs.get("set_current", False),
        )

    @property
    def scenario(self) -> S:
        """Return the scenario to be tested, building it if needed."""
        if self.built_scenario is None:
            self.build_scenario()
        assert self.built_scenario is not None
        return self.built_scenario

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Set scenario_cls from the type argument."""
        super().__init_subclass__(**kwargs)
        [cls.scenario_cls] = extract_generic_type_arguments(
            cls, ScenarioTestBase
        )


DSU = TypeVar("DSU", bound=scenarios.DefaultScopeUser)


class DefaultScopeUserTestBase(ScenarioTestBase[DSU]):
    """Test DefaultScopeUser-derived scenario."""

    def test_members(self) -> None:
        """Ensure we get the default members."""
        self.assertEqual(self.scenario.scope.name, "debusine")
        self.assertEqual(self.scenario.user.username, "playground")

    def test_set_context(self) -> None:
        """Test setting in context."""
        self.build_scenario(set_current=True)
        self.assertEqual(context.scope, self.scenario.scope)
        self.assertEqual(context.user, self.scenario.user)

    def test_scope_owners(self) -> None:
        """Test scope_owners."""
        group = self.scenario.scope_owners
        self.assertQuerySetEqual(group.users.all(), [])
        self.assertEqual(
            group.scope_roles.get(resource=self.scenario.scope).role,
            Scope.Roles.OWNER,
        )


class DefaultScopeUserTest(
    DefaultScopeUserTestBase[scenarios.DefaultScopeUser]
):
    """Test DefaultScopeUser scenario."""

    def test_set_context(self) -> None:
        """Test setting in context."""
        super().test_set_context()
        self.assertIsNone(context.workspace)


class DefaultScopeUserAPITest(
    DefaultScopeUserTestBase[scenarios.DefaultScopeUserAPI]
):
    """Test DefaultScopeUserAPI scenario."""

    def test_members(self) -> None:
        """Ensure we get the default members."""
        super().test_members()
        self.assertEqual(self.scenario.user_token.user, self.scenario.user)


DC = TypeVar("DC", bound=scenarios.DefaultContext)


class DefaultContextTestBase(DefaultScopeUserTestBase[DC]):
    """Test DefaultContext-derived scenario."""

    def test_members(self) -> None:
        """Ensure we get the default members."""
        super().test_members()
        self.assertEqual(self.scenario.workspace.name, "System")
        self.assertTrue(self.scenario.workspace.public)

    def test_set_context(self) -> None:
        """Test setting in context."""
        super().test_set_context()
        self.assertEqual(context.workspace, self.scenario.workspace)

    def test_workspace_owners(self) -> None:
        """Test scope_owners."""
        group = self.scenario.workspace_owners
        self.assertQuerySetEqual(group.users.all(), [])
        self.assertEqual(
            group.workspace_roles.get(resource=self.scenario.workspace).role,
            Workspace.Roles.OWNER,
        )


class DefaultContextTest(DefaultContextTestBase[scenarios.DefaultContext]):
    """Test DefaultContext scenario."""

    pass


class DefaultContextAPITest(
    DefaultContextTestBase[scenarios.DefaultContextAPI]
):
    """Test DefaultContextAPI scenario."""

    def test_members(self) -> None:
        """Ensure we get the default members."""
        super().test_members()
        self.assertEqual(self.scenario.user_token.user, self.scenario.user)


class UIPlaygroundTest(DefaultContextTestBase[scenarios.UIPlayground]):
    """Test UIPlayground scenario."""

    def test_members(self) -> None:
        """Ensure we get the default members."""
        super().test_members()
        self.assertTrue(
            check_password("playground", self.scenario.user.password)
        )
        # TODO: the scenario is kind of improvised and could use a dust-off to
        # make it state of the art with how Debusine actually work. When that
        # is done, the resulting expectations can be encoded here


del DefaultScopeUserTestBase
del DefaultContextTestBase
