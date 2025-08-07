# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for debusine templatetags."""

from typing import ClassVar

from django.contrib.auth.models import AnonymousUser
from django.template import TemplateSyntaxError, engines
from django.test import RequestFactory

from debusine.db.context import context
from debusine.db.models import Scope, User
from debusine.db.models.permissions import PermissionUser
from debusine.web.templatetags.debusine import has_perm, sorted_

# templatetags loading code will attempt to import tests, debusine.test
# dependencies (such as lxml) may not be available at runtime
try:
    from debusine.test.django import TestCase
except ImportError:
    from unittest import TestCase  # type: ignore[assignment]


class WithscopeTests(TestCase):
    """Tests for withscope tag."""

    scope1: ClassVar[Scope]
    scope2: ClassVar[Scope]
    user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.scope1 = cls.playground.get_or_create_scope("scope1")
        cls.scope2 = cls.playground.get_or_create_scope("scope2")
        cls.user = cls.playground.get_default_user()

    def setUp(self) -> None:
        """Reset context at the beginning of tests."""
        super().setUp()
        context.reset()

    def render(self, template_code: str) -> str:
        """Render a template from a string."""
        request = RequestFactory().get("/")
        template = engines["django"].from_string(template_code)
        return template.render({}, request=request)

    def test_context_accessors_defaults(self) -> None:
        """Test defaults for application context accessors."""
        self.assertEqual(self.render("{{scope.name}}"), "")
        self.assertEqual(self.render("{{workspace.name}}"), "")

    def test_context_accessors_populated(self) -> None:
        """Test application context accessors for populated contexts."""
        with context.disable_permission_checks():
            workspace1 = self.playground.create_workspace(
                scope=self.scope1, name="workspace1", public=True
            )

        context.set_scope(self.scope1)
        context.set_user(self.user)
        workspace1.set_current()

        self.assertEqual(self.render("{{scope.name}}"), "scope1")
        self.assertEqual(self.render("{{workspace.name}}"), "workspace1")

    def test_withscope(self) -> None:
        """Test withscope template tag."""
        context.set_scope(self.scope1)
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope 'scope2' %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            "scope1scope2scope1",
        )

    def test_withscope_misspelled(self) -> None:
        """Test withscope with a misspelled variable."""
        context.set_scope(self.scope1)
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope misspelled_var %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            "scope1scope1scope1",
        )

    def test_withscope_wrongtype(self) -> None:
        """Test withscope with a scope of an inappropriate type."""
        context.set_scope(self.scope1)
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope 3 %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            "scope1scope1scope1",
        )

    def test_withscope_wrongscope(self) -> None:
        """Test withscope with a nonexistent scope."""
        context.set_scope(self.scope1)
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope 'wrongscope' %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            "scope1scope1scope1",
        )

    def test_withscope_noarg(self) -> None:
        """Test withscope without args."""
        with self.assertRaisesRegex(
            TemplateSyntaxError, "withscope requires exactly one argument"
        ):
            self.render(
                "{% load debusine %}"
                "{% withscope %}{{scope}}{% endwithscope %}"
            ),

    def test_withscope_toomanyargs(self) -> None:
        """Test withscope with too many args."""
        with self.assertRaisesRegex(
            TemplateSyntaxError, "withscope requires exactly one argument"
        ):
            self.render(
                "{% load debusine %}"
                "{% withscope a b %}{{scope}}{% endwithscope %}"
            ),


class MockResource:
    """Mock a permission predicate for has_perm tests."""

    user: PermissionUser

    def __init__(self, retval: bool) -> None:
        """Store the return value."""
        self.retval = retval

    def predicate(self, user: PermissionUser) -> bool:
        """Store the argument for checking later."""
        self.user = user
        return self.retval


class HaspermTests(TestCase):
    """Test the has_perm template filter."""

    def test_context_user_unset(self) -> None:
        """Test using the default user."""
        resource = MockResource(True)
        self.assertTrue(
            has_perm(resource, "predicate"),  # type: ignore[arg-type]
        )
        self.assertIsNone(resource.user)

    def test_context_user_anonymous(self) -> None:
        """Test using the default user."""
        context.set_scope(self.playground.get_default_scope())
        context.set_user(AnonymousUser())
        resource = MockResource(True)
        self.assertTrue(
            has_perm(resource, "predicate"),  # type: ignore[arg-type]
        )
        assert resource.user is not None
        self.assertFalse(resource.user.is_authenticated)

    def test_context_user(self) -> None:
        """Test using the default user."""
        user = self.playground.get_default_user()
        context.set_scope(self.playground.get_default_scope())
        context.set_user(user)
        resource = MockResource(False)
        self.assertFalse(
            has_perm(resource, "predicate"),  # type: ignore[arg-type]
        )
        assert resource.user is not None
        self.assertEqual(resource.user, user)


class SortedTests(TestCase):
    """Test the sorted template filter."""

    def test_sorted(self) -> None:
        """Test sorting a seguence."""
        self.assertEqual(sorted_([1, 5, 2, 4, 3]), [1, 2, 3, 4, 5])
