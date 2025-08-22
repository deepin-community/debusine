# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for debusine templatetags."""

import re
from typing import Any, ClassVar, cast
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.db.models import Model
from django.template import TemplateSyntaxError, engines
from django.template.context import Context
from django.test import RequestFactory, override_settings
from django.test.utils import isolate_apps
from django.utils.safestring import SafeString

from debusine.db.context import context
from debusine.db.models import Scope
from debusine.db.models.permissions import PermissionUser
from debusine.db.playground import scenarios
from debusine.web.helps import HELPS
from debusine.web.icons import Icons
from debusine.web.templatetags.debusine import (
    UINode,
    WidgetNode,
    _help,
    format_yaml,
    has_perm,
    icon,
    roles,
    sorted_,
)
from debusine.web.views.base import Widget
from debusine.web.views.ui.base import UI

# templatetags loading code will attempt to import tests, debusine.test
# dependencies (such as lxml) may not be available at runtime
try:
    from debusine.test.django import TestCase
except ImportError:
    from unittest import TestCase  # type: ignore[assignment]


class WidgetTests(TestCase):
    """Tests for the widget tag."""

    def render(
        self,
        template_code: str = "{% load debusine %}{% widget value %}",
        **kwargs: Any,
    ) -> str:
        """Render a template from a string."""
        request = RequestFactory().get("/")
        template = engines["django"].from_string(template_code)
        return template.render(kwargs, request=request)

    def assertRenderError(
        self,
        user_message: str,
        devel_message: str,
        template_code: str = "{% load debusine %}{% widget value %}",
        **kwargs: Any,
    ) -> None:
        """Ensure rendering the template produces the given render error."""
        with mock.patch(
            "debusine.web.templatetags.debusine.WidgetNode._render_error",
            return_value="",
        ) as render_error:
            self.render(template_code, **kwargs)
        render_error.assert_called_once()
        self.assertEqual(
            render_error.call_args.kwargs["user_message"], user_message
        )
        self.assertRegex(
            render_error.call_args.kwargs["devel_message"], devel_message
        )

    @override_settings(TEST_MODE=False)
    def test_render_error_debug_or_tests(self) -> None:
        for setting in ("DEBUG", "TEST_MODE"):
            with (
                self.subTest(setting=setting),
                override_settings(**{setting: True}),
            ):
                node = WidgetNode(None)  # type: ignore[arg-type]
                with self.assertRaisesRegex(ValueError, r"devel message"):
                    node._render_error("user message", "devel message")

    @override_settings(TEST_MODE=False)
    def test_render_error_exception_debug_or_tests(self) -> None:
        class _Test(Widget):
            def render(self, _: Context) -> str:
                raise RuntimeError("expected error")

        for setting in ("DEBUG", "TEST_MODE"):
            with (
                self.subTest(setting=setting),
                override_settings(**{setting: True}),
            ):
                value = mock.Mock()
                value.resolve = mock.Mock(return_value=_Test())
                node = WidgetNode(value)
                with self.assertRaises(RuntimeError) as exc:
                    node.render(Context())
                self.assertEqual(str(exc.exception), "expected error")
                self.assertRegex(
                    exc.exception.__notes__[0],
                    r"Widget <Mock.+> \(.+_Test object at .+\)"
                    r" failed to render",
                )

    @override_settings(TEST_MODE=False)
    def test_render_error_production(self) -> None:
        node = WidgetNode(None)  # type: ignore[arg-type]
        with self.assertLogs("debusine.web", level="WARNING") as log:
            rendered = node._render_error("user message", "devel message")

        tree = self.assertHTMLValid(rendered, check_widget_errors=False)
        span = self.assertHasElement(tree, "body/span")
        self.assertEqual(span.get("data-role"), "debusine-widget-error")
        _, actual_message = self.get_node_text_normalized(span).split(": ")
        self.assertEqual(actual_message, "user message")

        self.assertEqual(
            log.output,
            ["WARNING:debusine.web:Widget rendering error: devel message"],
        )

    def test_value_errors(self) -> None:
        """Test rendering with problematic values."""
        for value, user_message, devel_message in (
            (
                "value",
                "invalid or undefined widget value",
                re.escape("widget <FilterExpression 'value'> resolved to None"),
            ),
            (
                "42",
                "invalid widget type",
                re.escape("widget <FilterExpression '42'> 42 has invalid type"),
            ),
            (
                "3.14",
                "invalid widget type",
                re.escape(
                    "widget <FilterExpression '3.14'> 3.14 has invalid type"
                ),
            ),
            (
                "value|has_perm:'foo'",
                "widget argument malformed",
                re.escape(
                    "Invalid widget argument: "
                    """<FilterExpression "value|has_perm:\'foo\'">"""
                ),
            ),
        ):
            with self.subTest(value=value):
                self.assertRenderError(
                    user_message,
                    devel_message,
                    template_code=(
                        "{% load debusine %}{% widget " + value + " %}"
                    ),
                )

    def test_string(self) -> None:
        """A string is a valid widget, returned as is."""
        for value, rendered in (
            ("", ""),
            ("foo", "foo"),
            ("<foo>", "&lt;foo&gt;"),
            (SafeString("<safefoo>"), "<safefoo>"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.render(value=value), rendered)

    def test_string_noautoescape(self) -> None:
        """A string is a valid widget, returned as is."""
        template_code = (
            "{% load debusine %}"
            "{% autoescape off %}{% widget value %}{% endautoescape %}"
        )
        for value, rendered in (
            ("", ""),
            ("foo", "foo"),
            ("<foo>", "<foo>"),
            (SafeString("<safefoo>"), "<safefoo>"),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.render(template_code, value=value), rendered
                )

    def test_widget(self) -> None:
        """Render an actual widget."""

        class _Test(Widget):
            def render(self, _: Context) -> str:
                return "rendered"

        self.assertEqual(self.render(value=_Test()), "rendered")

    def test_widget_raises_exception(self) -> None:
        """Render a widget that raises an exception."""

        class _Test(Widget):
            def render(self, _: Context) -> str:
                raise RuntimeError("expected error")

        self.assertRenderError(
            user_message="_Test failed to render",
            devel_message=(
                r"Widget <FilterExpression 'value'> \(.+_Test object at .+\)"
                " failed to render"
            ),
            value=_Test(),
        )

    def test_widget_arg_validation(self) -> None:
        """Check that widget validates the number of arguments."""
        for arg in ("", "foo bar", "foo bar baz"):
            with (
                self.subTest(arg=arg),
                self.assertRaisesRegex(
                    TemplateSyntaxError,
                    re.escape("{% widget %} requires exactly one argument"),
                ),
            ):
                self.render("{% load debusine %}{% widget " + arg + " %}")


class WithscopeTests(TestCase):
    """Tests for withscope tag."""

    scenario = scenarios.DefaultContext()
    scope2: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.scope2 = cls.playground.get_or_create_scope("scope2")

    def setUp(self) -> None:
        """Reset context at the beginning of tests."""
        super().setUp()
        context.reset()

    def render(self, template_code: str) -> str:
        """Render a template from a string."""
        request = RequestFactory().get("/")
        template = engines["django"].from_string(template_code)
        return template.render({"context": context}, request=request)

    def test_context_accessors_defaults(self) -> None:
        """Test defaults for application context accessors."""
        self.assertEqual(self.render("{{scope.name}}"), "")
        self.assertEqual(self.render("{{workspace.name}}"), "")

    def test_context_accessors_populated(self) -> None:
        """Test application context accessors for populated contexts."""
        self.scenario.set_current()
        self.assertEqual(
            self.render("{{scope.name}}"), self.scenario.scope.name
        )
        self.assertEqual(
            self.render("{{context.user.username}}"),
            self.scenario.user.username,
        )
        self.assertEqual(
            self.render("{{workspace.name}}"), self.scenario.workspace.name
        )

    def test_withscope(self) -> None:
        """Test withscope template tag."""
        self.scenario.set_current()
        name = self.scenario.scope.name
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope 'scope2' %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            f"{name}scope2{name}",
        )

    def test_withscope_preserve_user(self) -> None:
        self.scenario.set_current()
        self.assertEqual(
            self.render(
                "{% load debusine %}"
                "{% withscope 'scope2' %}"
                "{{scope}}{{context.user}}"
                "{% endwithscope %}"
            ),
            f"scope2{self.scenario.user.username}",
        )

    def test_withscope_misspelled(self) -> None:
        """Test withscope with a misspelled variable."""
        self.scenario.set_current()
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope misspelled_var %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            self.scenario.scope.name * 3,
        )

    def test_withscope_wrongtype(self) -> None:
        """Test withscope with a scope of an inappropriate type."""
        self.scenario.set_current()
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope 3 %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            self.scenario.scope.name * 3,
        )

    def test_withscope_wrongscope(self) -> None:
        """Test withscope with a nonexistent scope."""
        self.scenario.set_current()
        self.assertEqual(
            self.render(
                "{% load debusine %}{{scope}}"
                "{% withscope 'wrongscope' %}{{scope}}{% endwithscope %}"
                "{{scope}}"
            ),
            self.scenario.scope.name * 3,
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

    def get_roles(self, user: PermissionUser) -> list[str]:
        """Get the roles of the user."""
        # Mock as roles the user name and an arbitrary name, since get_roles
        # can return multiple values
        return [str(user), "a_test_role"]


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


class RolesTests(TestCase):
    """Test the roles filter."""

    scenario = scenarios.DefaultScopeUser()

    def test_current(self) -> None:
        """Test with the default user."""
        self.scenario.set_current()

        resource = cast(Model, MockResource(False))
        self.assertEqual(roles(resource), ["a_test_role", "playground"])

    def test_explicit(self) -> None:
        """Test passing a user explicitly."""
        self.scenario.set_current()
        user = self.playground.create_user("other")

        resource = cast(Model, MockResource(False))
        self.assertEqual(roles(resource, user), ["a_test_role", "other"])

    def test_response_without_role(self) -> None:
        self.scenario.set_current()
        resource = cast(Model, object())
        self.assertEqual(roles(resource), [])


class SortedTests(TestCase):
    """Test the sorted template filter."""

    def test_sorted(self) -> None:
        """Test sorting a sequence."""
        self.assertEqual(sorted_([1, 5, 2, 4, 3]), [1, 2, 3, 4, 5])


class FormatYamlTests(TestCase):
    """Test the format_yaml filter."""

    def test_format(self) -> None:
        tree = self.assertHTMLValid(format_yaml(42))
        div = self.assertHasElement(tree, "body/div")
        self.assertTextContentEqual(div, "42\n...")
        self.assertEqual(div.get("class"), "file_highlighted")

    def test_format_sorted(self) -> None:
        tree = self.assertHTMLValid(format_yaml({"b": 2, "a": 1}))
        div = self.assertHasElement(tree, "body/div")
        self.assertTextContentEqual(div, "a: 1 b: 2")

    def test_format_unsorted(self) -> None:
        tree = self.assertHTMLValid(format_yaml({"b": 2, "a": 1}, "unsorted"))
        div = self.assertHasElement(tree, "body/div")
        self.assertTextContentEqual(div, "b: 2 a: 1")


class IconTests(TestCase):
    """Test the icon filter."""

    def test_icon(self) -> None:
        for name, expected in (
            ("USER", Icons.USER),
            ("CaTeGoRy", Icons.CATEGORY),
            ("created_at", Icons.CREATED_AT),
            ("does-not-exist", "square-fill"),
        ):
            with self.subTest(name=name):
                self.assertEqual(icon(name), "bi-" + expected)


class HelpTests(TestCase):
    """Test the "help" template tag."""

    def test_help(self) -> None:
        help_data = HELPS["dependencies"]

        html = _help("dependencies")

        tree = self.assertHTMLValid(html)

        self.assertEqual(tree.body.i.attrib["title"], help_data.title)
        self.assertIn(help_data.link, tree.body.i.attrib["data-bs-content"])
        self.assertIn(help_data.summary, tree.body.i.attrib["data-bs-content"])


class UITests(TestCase):
    """Tests for the ui tag."""

    syntax_error = re.escape(
        "'ui' statement syntax is {% ui [object] as [variable] %}"
    )

    @isolate_apps("debusine.db")
    def make_mock_model(self) -> type[Model]:
        """Create a mock model class."""

        class MockModel(Model):
            pass

        return MockModel

    def make_helper(self, model_class: type[Model]) -> type[UI[Model]]:
        """Create a mock helper for a model class."""

        class Helper(UI[model_class]):  # type: ignore[valid-type]
            def label(self) -> str:
                return str(self.instance.pk)  # type: ignore[attr-defined]

        return Helper

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(UI.preserve_registry())
        factory = RequestFactory()
        self.request = factory.get("/")

    def render(
        self,
        template_code: str,
        **kwargs: Any,
    ) -> str:
        """Render a template from a string."""
        template = engines["django"].from_string(
            "{% load debusine %}" + template_code
        )
        return template.render(kwargs, request=self.request)

    def assertRenderError(
        self,
        devel_message: str,
        template_code: str = "{% load debusine %}{% widget value %}",
        **kwargs: Any,
    ) -> None:
        """Ensure rendering the template produces the given render error."""
        with mock.patch(
            "debusine.web.templatetags.debusine.UINode._lookup_error",
        ) as lookup_error:
            self.render(template_code, **kwargs)
        lookup_error.assert_called_once()
        self.assertRegex(lookup_error.call_args.args[0], devel_message)

    def test_lookup(self) -> None:
        """Lookup a UI helper."""
        Model = self.make_mock_model()
        self.make_helper(Model)
        obj = Model(pk=42)
        self.assertEqual(
            self.render("{% ui obj as helper %}{{helper.label}}", obj=obj), "42"
        )

    @override_settings(TEST_MODE=False)
    def test_lookup_error_debug_or_tests(self) -> None:
        for setting in ("DEBUG", "TEST_MODE"):
            with (
                self.subTest(setting=setting),
                override_settings(**{setting: True}),
            ):
                node = UINode(None, None)  # type: ignore[arg-type]
                with self.assertRaisesRegex(ValueError, r"devel message"):
                    node._lookup_error("devel message")

    @override_settings(TEST_MODE=False)
    def test_lookup_error_exception_debug_or_tests(self) -> None:
        for setting in ("DEBUG", "TEST_MODE"):
            with (
                self.subTest(setting=setting),
                override_settings(**{setting: True}),
            ):
                node = UINode(None, None)  # type: ignore[arg-type]
                with self.assertRaisesRegex(
                    ValueError, r"test exception"
                ) as exc:
                    node._lookup_error(
                        "devel message",
                        exception=ValueError("test exception"),
                    )
                self.assertRegex(exc.exception.__notes__[0], r"devel message")

    @override_settings(TEST_MODE=False)
    def test_lookup_error_production(self) -> None:
        node = UINode(None, None)  # type: ignore[arg-type]
        with self.assertLogs("debusine.web", level="WARNING") as log:
            node._lookup_error("devel message")
        self.assertEqual(
            log.output,
            ["WARNING:debusine.web:UI helper lookup failed: devel message"],
        )

    def test_value_errors(self) -> None:
        """Test lookup with problematic values."""
        for value, devel_message in (
            (
                "value",
                re.escape(
                    "ui model instance <FilterExpression 'value'>"
                    " resolved to None"
                ),
            ),
            (
                "42",
                re.escape(
                    "ui model instance <FilterExpression '42'> 42"
                    " has invalid type"
                ),
            ),
            (
                "3.14",
                re.escape(
                    "ui model instance <FilterExpression '3.14'> 3.14"
                    " has invalid type"
                ),
            ),
            (
                "value|has_perm:'foo'",
                re.escape(
                    "Invalid ui argument: "
                    """<FilterExpression "value|has_perm:\'foo\'">"""
                ),
            ),
        ):
            with self.subTest(value=value):
                self.assertRenderError(
                    devel_message,
                    template_code=("{% ui " + value + " as foo %}"),
                )

    def test_model_without_helper(self) -> None:
        """Test lookup of an instance whose model has no helper."""
        Model = self.make_mock_model()
        with mock.patch(
            "debusine.web.templatetags.debusine.UI.for_instance",
            side_effect=KeyError("helper not found"),
        ):
            self.assertRenderError(
                r"ui helper lookup for <FilterExpression 'obj'> failed",
                template_code="{% ui obj as foo %}",
                obj=Model(),
            )

    def test_ui_no_args(self) -> None:
        Model = self.make_mock_model()
        self.make_helper(Model)
        obj = Model(pk=42)
        with self.assertRaisesRegex(TemplateSyntaxError, self.syntax_error):
            self.assertEqual(self.render("{% ui %}", obj=obj), "FAIL")

    def test_ui_no_as(self) -> None:
        Model = self.make_mock_model()
        self.make_helper(Model)
        obj = Model(pk=42)
        with self.assertRaisesRegex(TemplateSyntaxError, self.syntax_error):
            self.assertEqual(self.render("{% ui helper %}", obj=obj), "FAIL")

    def test_ui_no_target(self) -> None:
        Model = self.make_mock_model()
        self.make_helper(Model)
        obj = Model(pk=42)
        with self.assertRaisesRegex(TemplateSyntaxError, self.syntax_error):
            self.assertEqual(self.render("{% ui as %}", obj=obj), "FAIL")

    def test_ui_multiple_targets(self) -> None:
        Model = self.make_mock_model()
        self.make_helper(Model)
        obj = Model(pk=42)
        with self.assertRaisesRegex(TemplateSyntaxError, self.syntax_error):
            self.assertEqual(self.render("{% ui as a b %}", obj=obj), "FAIL")
