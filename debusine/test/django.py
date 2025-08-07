# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common test-helper code involving django usage."""

import abc
import asyncio
import contextlib
import copy
import inspect
import io
import itertools
import unittest
from collections.abc import Callable, Collection, Generator
from pathlib import Path
from typing import (
    Any,
    ClassVar,
    Generic,
    Protocol,
    TYPE_CHECKING,
    TypeAlias,
    TypeVar,
    cast,
    runtime_checkable,
)
from unittest import mock
from unittest.util import safe_repr

import django.test
import lxml
import requests
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.db.models import Model, QuerySet
from django.http.response import HttpResponseBase
from rest_framework import status

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Scope, Token, User, WorkRequest, Workspace
from debusine.db.models.permissions import (
    PermissionCheckPredicate,
    PermissionUser,
    Roles,
    get_resource_scope,
    get_resource_workspace,
    resolve_roles_list,
)
from debusine.db.playground.scenarios import Scenario
from debusine.test.playground import Playground

# Relative import needed to break an import loop
from .base import TestCase as BaseTestCase  # noqa: ABS101

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

    TestMixinBase = unittest.TestCase
else:
    TestMixinBase = object

M = TypeVar("M", bound=Model)
R = TypeVar("R", bound=Roles)

#: Type alias for response objects returned by the Django test client
TestResponseType: TypeAlias = "_MonkeyPatchedWSGIResponse"


class PermissionOverride(abc.ABC, Generic[M]):
    """Base class for configurable permission overrides for tests."""

    @abc.abstractmethod
    def check(self, obj: M, user: PermissionUser) -> bool:
        """Test the predicate on an object."""

    @abc.abstractmethod
    def filter(
        self, queryset: QuerySet[M, M], user: PermissionUser
    ) -> QuerySet[M, M]:
        """Filter a QuerySet with the predicate."""


class AllowAll(PermissionOverride[M]):
    """Permission override allowing all."""

    def check(self, obj: M, user: PermissionUser) -> bool:  # noqa: U100
        """Test the predicate on an object."""
        return True

    def filter(
        self, queryset: QuerySet[M, M], user: PermissionUser  # noqa: U100
    ) -> QuerySet[M, M]:
        """Filter a QuerySet with the predicate."""
        return queryset


class DenyAll(PermissionOverride[M]):
    """Permission override denying all."""

    def check(self, obj: M, user: PermissionUser) -> bool:  # noqa: U100
        """Test the predicate on an object."""
        return False

    def filter(
        self, queryset: QuerySet[M, M], user: PermissionUser  # noqa: U100
    ) -> QuerySet[M, M]:
        """Filter a QuerySet with the predicate."""
        return queryset.none()


class ListFilter(PermissionOverride[M]):
    """Permission override that includes or excludes specific items."""

    def __init__(
        self, include: list[M] | None = None, exclude: list[M] | None = None
    ) -> None:
        """Store filter arguments."""
        super().__init__()
        self._include = include
        self._exclude = exclude

    def check(self, obj: M, user: PermissionUser) -> bool:  # noqa: U100
        """Test the predicate on an object."""
        if self._include is not None:
            return obj in self._include
        if self._exclude is not None:
            return obj not in self._exclude
        return True

    def filter(
        self, queryset: QuerySet[M, M], user: PermissionUser  # noqa: U100
    ) -> QuerySet[M, M]:
        """Filter a QuerySet with the predicate."""
        res = queryset
        if self._include is not None:
            res = res.filter(pk__in=[x.pk for x in self._include])
        if self._exclude is not None:
            res = res.exclude(pk__in=[x.pk for x in self._exclude])
        return res


def set_context_for_resource(resource: Model, user: PermissionUser) -> None:
    """Set the current context to fit the resource."""
    scope = get_resource_scope(resource)
    assert scope is not None
    context.set_scope(scope)
    assert user is not None
    context.set_user(user)
    if workspace := get_resource_workspace(resource):
        workspace.set_current()


@contextlib.contextmanager
def override_permission(
    model_cls: type[M],
    pred: str,
    override_cls: type[PermissionOverride[M]],
    **kwargs: Any,
) -> Generator[None, None, None]:
    """
    Override a permission.

    Example usage::

        with override_permission(
            Workspace, "can_display", AllowAll
        ):
            ...

    Or also::

        @override_permission(
            Workspace, "can_display", AllowAll
        )
        def test_method(self) -> None:
            ...
    """
    override = override_cls(**kwargs)

    # Model.objects is a manager instance generated by from_queryset, and we
    # need a bit of creativity to mock its methods
    def on_manager(user: PermissionUser) -> QuerySet[M, M]:
        return override.filter(getattr(model_cls, "objects").all(), user)

    manager = getattr(model_cls, "objects")
    queryset_cls = manager.get_queryset().__class__
    error_template = getattr(getattr(model_cls, pred), "error_template")
    with (
        mock.patch.object(
            model_cls, pred, autospec=True, side_effect=override.check
        ) as check_patch,
        mock.patch.object(
            manager,
            pred,
            autospec=True,
            side_effect=on_manager,
        ),
        mock.patch.object(
            queryset_cls,
            pred,
            autospec=True,
            side_effect=override.filter,
        ),
    ):
        setattr(check_patch, "error_template", error_template)
        yield


@runtime_checkable
class JSONResponseProtocol(Protocol):
    """A Django test client response with a monkey-patched json() method."""

    def json(self) -> Any:
        """Return the body of the response, parsed as JSON."""


class BaseDjangoTestCase(django.test.SimpleTestCase, BaseTestCase):
    """
    Django-specific Debusine test methods.

    This augments debusine.test.TestCase with django-specific assert statements
    and factory functions.

    This is the common base class for TestCase and TransactionTestCase, for
    tests that do depend on Django code.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """
        Reset context before the TestCase initializes.

        Context is likely to be accessed by class-level test case setup (like
        ``setUpTestData``), and a context left over from a previous test will
        interfere with that
        """
        context.reset()
        super().setUpClass()

    def setUp(self) -> None:
        """Ensure test methods do not change the global scope."""
        super().setUp()
        self.enterContext(context.local())
        context.reset()

    @contextlib.contextmanager
    def ephemeral_savepoint(self) -> Generator[Any, None, None]:
        """
        Run the code inside a savepoint, rolled back at the end.

        Generates the transaction identifier.

        Rollback only happens if the code succeeded: a runaway exception is
        left to the care of the transaction inside which we are running.
        """
        sid = transaction.savepoint()
        yield sid
        transaction.savepoint_rollback(sid)

    def assertResponseHTML(
        self,
        response: TestResponseType,
        status_code: int = status.HTTP_200_OK,
        content_type: str = "text/html; charset=utf-8",
    ) -> lxml.objectify.ObjectifiedElement:
        """
        Check that the response is valid HTML.

        :param response: the Django response to check
        :param status_code: expected status code
        :param content_type: expected content type
        :returns: the parsed HTML contents
        """
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(response.headers["Content-Type"], content_type)
        return self.assertHTMLValid(response.content)

    def assertResponse400(
        self, response: "_MonkeyPatchedWSGIResponse", error: str
    ) -> None:
        """Assert that response is Http400 and contents is in response."""
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["error"], error)

    def assertResponseProblem(
        self,
        response: HttpResponseBase,
        title: str,
        detail_pattern: str | None = None,
        validation_errors_pattern: str | None = None,
        status_code: int = requests.codes.bad_request,
    ) -> None:
        """
        Assert that response is a valid application/problem+json.

        Assert that the content_type is application/problem+json and the
        title exist and matches title.

        :param response: response that it is asserting
        :param status_code: assert response.status_code == status_code
        :param title: exact match with response.data["title"]
        :param detail_pattern: if not None: assertRegex with
           response.data["detail"]. If None checks that response.data does not
           contain "detail".
        :param validation_errors_pattern: if not None: assertRegex with
           response.data["validation_errors"]. If None checks that
           response.data does not contain "validation_errors".
        """
        self.assertEqual(
            response.status_code,
            status_code,
            f"response status {response.status_code} != {status_code}",
        )

        content_type = response.headers["Content-Type"]
        self.assertEqual(
            content_type,
            "application/problem+json",
            f'content_type "{content_type}" != ' f'"application/problem+json"',
        )

        assert isinstance(response, JSONResponseProtocol)
        data = response.json()

        self.assertIn("title", data, '"title" not found in response')

        response_title = data["title"]
        self.assertEqual(
            response_title, title, f'title "{response_title}" != "{title}"'
        )

        if detail_pattern is not None:
            self.assertIn("detail", data, '"detail" not found in response')

            response_detail = str(data["detail"])
            self.assertRegex(
                response_detail,
                detail_pattern,
                f'Detail regexp "{detail_pattern}" did not '
                f'match "{response_detail}"',
            )
        else:
            self.assertNotIn("detail", data, '"detail" is in the response')

        if validation_errors_pattern is not None:
            self.assertIn(
                "validation_errors",
                data,
                '"validation_errors" not found in response',
            )
            self.assertIsInstance(data["validation_errors"], dict)

            response_validation_errors = str(data["validation_errors"])
            self.assertRegex(
                response_validation_errors,
                validation_errors_pattern,
                f'Validation errors regexp "{validation_errors_pattern}" did '
                f'not match "{response_validation_errors}"',
            )
        else:
            self.assertNotIn(
                "validation_errors",
                data,
                '"validation_errors" is in the response',
            )

    @context.local()
    def _test_check_predicate(
        self,
        user: PermissionUser,
        obj: M,
        name: str,
        expected: bool,
        token: Token | None = None,
    ) -> list[str]:
        """Test a single check predicate on an object."""
        errors: list[str] = []
        predicate = getattr(obj, name)

        # Test on an empty context
        context.reset()
        if token is not None and hasattr(token, "worker"):
            context.set_worker_token(token)

        if predicate(user) is not expected:
            errors.append(
                f"{name} is {not expected} for {user} on {obj}"
                " with empty context"
            )

        # Try with scope set in context, to check shortcut test code
        if (scope := get_resource_scope(obj)) is None:
            return errors

        try:
            context.set_scope(scope)
            if user is not None:
                context.set_user(user)
            if predicate(user) is not expected:
                errors.append(
                    f"{name} is {not expected} for {user} on {obj}"
                    " with scope set in context"
                )

            if (workspace := get_resource_workspace(obj)) is None:
                return errors

            # Try with also workspace set in context, to check shortcut test
            # code
            workspace.set_current()
            if predicate(user) is not expected:
                errors.append(
                    f"{name} is {not expected} for {user} on {obj}"
                    " with scope and workspace set in context"
                )
        except ContextConsistencyError:
            pass

        return errors

    @context.local()
    def assertPermission(  # noqa: C901
        self,
        name: str,
        users: PermissionUser | Collection[PermissionUser],
        allowed: M | Collection[M] = (),
        denied: M | Collection[M] = (),
        token: Token | None = None,
    ) -> None:
        """
        Test a permission predicate.

        For each user given, test that the model objects in allowed allow the
        named permission, and those in denied do not.

        Both check and filter predicates are tested. Note that, because the
        filter predicate is tested, allowed must list all objects in the
        database that test true for the permission.
        """
        context.reset()
        if token is not None and hasattr(token, "worker"):
            context.set_worker_token(token)

        if not isinstance(users, Collection):
            users = (users,)
        if not isinstance(allowed, Collection):
            allowed = (allowed,)
        if not isinstance(denied, Collection):
            denied = (denied,)

        errors: list[str] = []

        # We cannot use subTest because the reported stack trace would stop at
        # subTest, and we would lose information on where assertPermission is
        # called
        for user in users:
            for obj in allowed:
                errors += self._test_check_predicate(
                    user, obj, name, True, token=token
                )

            for obj in denied:
                if getattr(obj, name)(user):
                    errors.append(f"{name} is True for {user} on {obj}")

            # Pick the model class from the first element of allow or
            # denied
            model_cls = next(itertools.chain(allowed, denied)).__class__
            manager = getattr(model_cls, "objects")

            filtered = sorted(
                str(obj) for obj in getattr(manager, name)(user=user)
            )
            expected = sorted(str(obj) for obj in allowed)
            if filtered != expected:
                error = f"{name} for {user} selects "
                if filtered:
                    error += ", ".join(filtered)
                else:
                    error += "nothing"
                error += " instead of "
                if expected:
                    error += ", ".join(expected)
                else:
                    error += "nothing"
                errors.append(error)

        if errors:
            with io.StringIO() as buf:
                print("Predicate permission mismatch:", file=buf)
                for error in errors:
                    print(f"* {error}", file=buf)
                self.fail(buf.getvalue())

    @contextlib.contextmanager
    def assert_model_count_unchanged(
        self, model_cls: type[Model]
    ) -> Generator[None, None, None]:
        """Context manager to assert count of rows in a model is unchanged."""
        initial_count = getattr(model_cls, "objects").count()
        try:
            yield
        finally:
            self.assertEqual(
                getattr(model_cls, "objects").count(), initial_count
            )

    @contextlib.contextmanager
    def template_dir(self) -> Generator[Path, None, None]:
        """Prepend a temporary directory to the template search paths."""
        template_dir = self.create_temporary_directory()
        template_settings = copy.deepcopy(settings.TEMPLATES)
        cast(list[str], template_settings[0]["DIRS"]).insert(
            0, template_dir.as_posix()
        )
        with django.test.override_settings(TEMPLATES=template_settings):
            yield template_dir


class PlaygroundTestCase(BaseDjangoTestCase):
    """
    Playground-specific test case setup.

    Class annotations are scanned in test case setup for Scenario instances,
    which are automatically instantiated and set in the class.

    Scenario instances can be annotated with a dict, which will provide extra
    arguments passed to playground.scenario(). This can be used, for example,
    to call ``set_current``::

      scenario: Annotated[ClassVar[DefaultContext], {"set_current": True}]
    """

    #: set to False if you do not need a playground in your test case
    playground_needed: bool = True

    #: set to False if you need a playground with a LocalFileStore instead of
    #: an in-memory version
    playground_memory_file_store: bool = True

    @classmethod
    def create_playground(cls) -> Playground:
        """
        Create the playground object.

        This method can be overridden by subclasses to create a playground with
        a different configuration
        """
        return Playground(memory_file_store=cls.playground_memory_file_store)

    @classmethod
    def list_playground_scenarios(
        cls,
    ) -> Generator[tuple[str, Scenario], None, None]:
        """
        List the scenarios configured in the test case.

        Generate tuples like ``(name, scenario)``.
        """
        for name, value in inspect.getmembers(cls):
            if not isinstance(value, Scenario):
                continue
            yield name, value

    def _build_permission_when_role_testlist(
        self,
        predicate: Callable[[PermissionUser], bool],
        roles: str | Collection[str] = (),
        *,
        scope_roles: str | Collection[str] = (),
        workspace_roles: str | Collection[str] = (),
    ) -> list[tuple[Model, str]]:
        """Build a TODO list for testing the predicate with the given roles."""
        assert isinstance(predicate, PermissionCheckPredicate)
        resource = predicate.__self__
        roles = resolve_roles_list(resource, roles)
        scope_roles = resolve_roles_list(Scope, scope_roles)
        workspace_roles = resolve_roles_list(Workspace, workspace_roles)

        tests: list[tuple[Model, str]] = []
        if scope_roles:
            scope = get_resource_scope(resource)
            assert scope is not None
            tests.extend((scope, role) for role in scope_roles)
        if workspace_roles:
            workspace = get_resource_workspace(resource)
            assert workspace is not None
            tests.extend((workspace, role) for role in workspace_roles)
        tests.extend((resource, role) for role in roles)

        return tests

    def assertNoPermissionWhenRole(  # noqa: C901
        self,
        predicate: Callable[[PermissionUser], bool],
        user: User,
        roles: str | Collection[str] = (),
        *,
        scope_roles: str | Collection[str] = (),
        workspace_roles: str | Collection[str] = (),
    ) -> None:
        """
        Check that assigning roles does not make the predicate come true.

        Each role is tested in isolation: the test is repeated for each given
        role.
        """
        assert isinstance(predicate, PermissionCheckPredicate)
        errors: list[str] = []
        resource: Model = predicate.__self__
        pred_name = f"{predicate.__func__.__name__!r} on {str(resource)!r}"
        filter_predicate = getattr(
            getattr(resource.__class__, "objects"), predicate.__func__.__name__
        )
        for assignee, role in self._build_permission_when_role_testlist(
            predicate,
            roles,
            scope_roles=scope_roles,
            workspace_roles=workspace_roles,
        ):
            with context.local(), self.ephemeral_savepoint():
                context.reset()

                if predicate(user):
                    errors.append(f"{pred_name} is true even without {role}")
                if filter_predicate(user).filter(pk=resource.pk).exists():
                    errors.append(
                        f"{pred_name} passes filtering even without {role}"
                    )

                # Ignoring type until we find a way to type self.playground to
                # accept either a member or a classvar, depending on subclasses
                self.playground.create_group_role(  # type: ignore[attr-defined]
                    assignee,
                    role,
                    users=[user],
                    name="assertNoPermissionWhenRole",
                )

                if predicate(user):
                    errors.append(f"{pred_name} is true when given {role}")
                if filter_predicate(user).filter(pk=resource.pk).exists():
                    errors.append(
                        f"{pred_name} passes filtering when given {role}"
                    )

                # Once more with context set, to test shortcutting
                set_context_for_resource(assignee, user)

                if predicate(user):
                    errors.append(
                        f"{pred_name} is true when given {role} in context"
                    )
                if filter_predicate(user).filter(pk=resource.pk).exists():
                    errors.append(
                        f"{pred_name} passes filtering when given {role}"
                        " in context"
                    )

        self.assertEqual(errors, [])

    def assertPermissionWhenRole(  # noqa: C901
        self,
        predicate: Callable[[PermissionUser], bool],
        user: User,
        roles: str | Collection[str] = (),
        scope_roles: str | Collection[str] = (),
        workspace_roles: str | Collection[str] = (),
    ) -> None:
        """
        Check that assigning roles makes the predicate come true.

        Each role is tested in isolation: the test is repeated for each given
        role.
        """
        assert isinstance(predicate, PermissionCheckPredicate)
        errors: list[str] = []
        resource = predicate.__self__
        pred_name = f"{predicate.__func__.__name__!r} on {str(resource)!r}"
        filter_predicate = getattr(
            resource.__class__.objects, predicate.__func__.__name__
        )
        for assignee, role in self._build_permission_when_role_testlist(
            predicate,
            roles,
            scope_roles=scope_roles,
            workspace_roles=workspace_roles,
        ):
            with context.local(), self.ephemeral_savepoint():
                context.reset()

                if predicate(user):
                    errors.append(f"{pred_name} is true even without {role}")
                if filter_predicate(user).filter(pk=resource.pk).exists():
                    errors.append(
                        f"{pred_name} passes filtering even without {role}"
                    )

                # Ignoring type until we find a way to type self.playground to
                # accept either a member or a classvar, depending on subclasses
                self.playground.create_group_role(  # type: ignore[attr-defined]
                    assignee,
                    role,
                    users=[user],
                    name="assertPermissionWhenRole",
                )

                if not predicate(user):
                    errors.append(f"{pred_name} is false even with {role}")
                if not filter_predicate(user).filter(pk=resource.pk).exists():
                    errors.append(
                        f"{pred_name} does not pass filtering even with {role}"
                    )

                # Once more with context set, to test shortcutting
                set_context_for_resource(assignee, user)

                if not predicate(user):
                    errors.append(
                        f"{pred_name} is false even with {role} in context"
                    )
                if not filter_predicate(user).filter(pk=resource.pk).exists():
                    errors.append(
                        f"{pred_name} does not pass filtering even with {role}"
                        " in context"
                    )

        self.assertEqual(errors, [])


class TransactionTestCase(
    PlaygroundTestCase,
    django.test.TransactionTestCase,
):
    """Debusine-specific extensions to django's TransactionTestCase."""

    playground: Playground

    def setUp(self) -> None:
        """Create a playground for TransactionTestCase."""
        super().setUp()
        if self.playground_needed:
            # TransactionTestCase does not support setUpTestData, so we need a
            # playground instance per test method
            self.playground = self.enterContext(self.create_playground())
            # Instantiate playground scenarios
            for name, scenario in self.__class__.list_playground_scenarios():
                # Make a copy of the blank scenario in the class
                method_scenario = copy.deepcopy(scenario)
                # Instantiate a different one for each test method
                self.playground.build_scenario(
                    method_scenario, scenario_name=name, set_current=True
                )
                setattr(self, name, method_scenario)


class TestCase(
    PlaygroundTestCase,
    django.test.TestCase,
):
    """Debusine-specific extensions to django's TestCase."""

    playground: ClassVar[Playground]

    @classmethod
    def setUpTestData(cls) -> None:
        """Create a playground to setup test data."""
        super().setUpTestData()
        if cls.playground_needed:
            cls.playground = cls.enterClassContext(cls.create_playground())
            for name, scenario in cls.list_playground_scenarios():
                # set_current is delayed to the setUp method
                cls.playground.build_scenario(
                    scenario, scenario_name=name, set_current=False
                )

    def setUp(self) -> None:
        """Create a playground for TransactionTestCase."""
        super().setUp()
        if self.playground_needed:
            for name, scenario in self.playground.scenarios.items():
                # Replace the class version of the scenario with its deepcopied
                # version
                setattr(self, name, scenario)
                # BaseDjangoTestCase.setUp calls context.reset, so we need to
                # call set_current here instead of in setUpTestData
                if scenario.needs_set_current:
                    scenario.set_current()

    def assert_work_request_event_reactions(
        self,
        work_request: WorkRequest,
        *,
        on_creation: list[dict[str, Any]] | None = None,
        on_unblock: list[dict[str, Any]] | None = None,
        on_success: list[dict[str, Any]] | None = None,
        on_failure: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Assert that a work request has the given event reactions.

        The order of the items in each list of event reactions does not
        matter.  Leaving parameters unset or None is equivalent to passing
        the empty list, meaning that they are required to be empty.
        """
        self.assertCountEqual(
            work_request.event_reactions_json.get("on_creation", []),
            on_creation or [],
        )
        self.assertCountEqual(
            work_request.event_reactions_json.get("on_unblock", []),
            on_unblock or [],
        )
        self.assertCountEqual(
            work_request.event_reactions_json.get("on_success", []),
            on_success or [],
        )
        self.assertCountEqual(
            work_request.event_reactions_json.get("on_failure", []),
            on_failure or [],
        )


class ChannelsHelpersMixin(TestMixinBase):
    """
    Channel-related methods to help writing unit tests.

    Provides methods to setup a channel and assert messages or lack of messages.
    """

    def tearDown(self) -> None:
        """Flush the channel layer."""
        super().tearDown()
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.flush)()

    # TODO: coverage is confused by something here, possibly
    # https://github.com/python/cpython/issues/106749
    async def create_channel(
        self, group_name: str
    ) -> dict[str, Any]:  # pragma: no cover
        """
        Create a channel and add it to the group named ``group_name``.

        Return dict with layer and name.
        """
        channel_layer = get_channel_layer()
        channel_name = await channel_layer.new_channel()
        await channel_layer.group_add(group_name, channel_name)

        return {"layer": channel_layer, "name": channel_name}

    async def assert_channel_nothing_received(
        self, channel: dict[str, Any]
    ) -> None:
        """Assert that nothing is received in channel."""
        try:
            received = await asyncio.wait_for(
                channel["layer"].receive(channel["name"]), timeout=0.1
            )
        except asyncio.exceptions.TimeoutError:
            pass
        else:
            cast(unittest.TestCase, self).fail(
                "Expected nothing. Received: '%s'" % safe_repr(received)
            )

    async def assert_channel_received(
        self, channel: dict[str, Any], data: dict[str, Any]
    ) -> None:
        """Assert that data is received in channel_layer, channel_name."""
        try:
            received = await asyncio.wait_for(
                channel["layer"].receive(channel["name"]), timeout=0.1
            )
            cast(unittest.TestCase, self).assertEqual(received, data)
        except asyncio.exceptions.TimeoutError:
            cast(unittest.TestCase, self).fail(
                "Expected '%s' received nothing" % safe_repr(data)
            )
