# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for debusine.tests.django."""

import contextlib
import json
import re
from collections.abc import Callable, Generator
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar, Never, Self
from unittest import mock

import django.template
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import QuerySet
from django.http.response import HttpResponse, HttpResponseBase, JsonResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from debusine.db.context import context
from debusine.db.models import (
    ArtifactRelation,
    Scope,
    User,
    Workspace,
    permissions,
)
from debusine.db.models.permissions import Allow, PermissionUser
from debusine.db.models.workspaces import WorkspaceQuerySet
from debusine.server.views import ProblemResponse
from debusine.test.django import (
    AllowAll,
    ChannelsHelpersMixin,
    DenyAll,
    ListFilter,
    PermissionOverride,
    TestCase,
    override_permission,
)


class PermissionOverrideTests(TestCase):
    """Test permission overrides."""

    user: ClassVar[User]
    scope: ClassVar[Scope]
    scope1: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = Scope.objects.get(name=settings.DEBUSINE_DEFAULT_SCOPE)
        cls.scope1 = Scope.objects.create(name="scope1")
        cls.user = get_user_model().objects.create_user(
            username="testuser", password="testpassword"
        )

    def assert_all_allowed(
        self, obj: PermissionOverride[Scope], user: User | AnonymousUser
    ) -> None:
        """Check that all scopes test as allowed."""
        # Test allowed on permission check
        for scope in (self.scope, self.scope1):
            self.assertTrue(obj.check(scope, user))

        # Test allowed on permission filter
        self.assertQuerySetEqual(
            obj.filter(Scope.objects.all(), user),
            [self.scope, self.scope1],
            ordered=False,
        )

    def assert_allowed(
        self,
        obj: PermissionOverride[Scope],
        user: User | AnonymousUser,
        scopes: list[Scope],
    ) -> None:
        """Check that all the given scopes test as allowed."""
        # Test allowed on permission check
        for scope in scopes:
            self.assertTrue(obj.check(scope, user))
        for scope in {self.scope, self.scope1} - set(scopes):
            self.assertFalse(obj.check(scope, user))

        # Test allowed on permission filter
        self.assertQuerySetEqual(
            obj.filter(Scope.objects.all(), user),
            scopes,
            ordered=False,
        )

    def test_allow_all(self) -> None:
        """Test AllowAll.check."""
        obj = AllowAll[Scope]()
        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assert_all_allowed(obj, user)

    def test_deny_all_check(self) -> None:
        """Test DenyAll.check."""
        obj = DenyAll[Scope]()
        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assert_allowed(obj, user, [])

    def test_list_noop(self) -> None:
        """Test ListFilter predicate with no arguments: allows all."""
        obj = ListFilter[Scope]()
        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assert_all_allowed(obj, user)

    def test_list_include(self) -> None:
        """Test ListFilter with include."""
        obj = ListFilter[Scope](include=[self.scope])
        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assert_allowed(obj, user, [self.scope])

    def test_list_exclude(self) -> None:
        """Test ListFilter with exclude."""
        obj = ListFilter[Scope](exclude=[self.scope])
        for user in (AnonymousUser(), self.user):
            with self.subTest(user=user):
                self.assert_allowed(obj, user, [self.scope1])

    def test_override_permission(self) -> None:
        """Test override_permission as context manager."""
        with override_permission(Scope, "can_display", AllowAll):
            self.assertEqual(
                getattr(self.scope.can_display, "error_template"),
                "{user} cannot display scope {resource}",
            )
            self.assertTrue(self.scope.can_display(self.user))
            self.assertQuerySetEqual(
                Scope.objects.can_display(self.user),
                [self.scope, self.scope1],
                ordered=False,
            )
            self.assertQuerySetEqual(
                Scope.objects.all().can_display(self.user),
                [self.scope, self.scope1],
                ordered=False,
            )
        with override_permission(Scope, "can_display", DenyAll):
            self.assertFalse(self.scope.can_display(self.user))
            self.assertQuerySetEqual(Scope.objects.can_display(self.user), [])
            self.assertQuerySetEqual(
                Scope.objects.all().can_display(self.user), []
            )

    @override_permission(Scope, "can_display", DenyAll)
    def test_override_permission_decorator(self) -> None:
        """Test override_permission as decorator."""
        self.assertFalse(self.scope.can_display(self.user))


class TestCaseTests(TestCase):
    """Tests for methods in debusine.test.django.TestCase."""

    playground_memory_file_store = False

    @contextlib.contextmanager
    def assertExceptionHasFormattedAPIResponse(
        self,
    ) -> Generator[None, None, None]:
        with mock.patch(
            "debusine.test.django.BaseDjangoTestCase.format_api_response",
            return_value="…",
        ):
            try:
                yield
            except Exception as exc:
                self.assertIn("…", exc.__notes__)
                raise

    def test_create_artifact_relation_default_type(self) -> None:
        """create_artifact_relation() create artifact with type=RELATED_TO."""
        artifact, _ = self.playground.create_artifact()
        target, _ = self.playground.create_artifact()
        created_artifact_relation = self.playground.create_artifact_relation(
            artifact, target
        )
        created_artifact_relation.refresh_from_db

        self.assertEqual(created_artifact_relation.artifact, artifact)
        self.assertEqual(created_artifact_relation.target, target)
        self.assertEqual(
            created_artifact_relation.type,
            ArtifactRelation.Relations.RELATES_TO,
        )

    def test_create_artifact_relation_specific_type(self) -> None:
        """create_artifact_relation() create artifact with given type."""
        artifact, _ = self.playground.create_artifact()
        target, _ = self.playground.create_artifact()
        relation_type = ArtifactRelation.Relations.BUILT_USING

        created_artifact_relation = self.playground.create_artifact_relation(
            artifact, target, relation_type
        )
        created_artifact_relation.refresh_from_db()

        self.assertEqual(created_artifact_relation.artifact, artifact)
        self.assertEqual(created_artifact_relation.target, target)
        self.assertEqual(created_artifact_relation.type, relation_type)

    def test_create_artifact_default_expire_date(self) -> None:
        """create_artifact() set expire_at to None by default."""
        artifact, _ = self.playground.create_artifact()
        artifact.refresh_from_db()
        self.assertIsNone(artifact.expire_at)

    def test_create_artifact_expire_date(self) -> None:
        """create_artifact() set expire_at use correct expire_at.."""
        now = timezone.now()
        artifact, _ = self.playground.create_artifact(expiration_delay=1)
        artifact.created_at = now - timedelta(days=1)
        artifact.save()
        artifact.refresh_from_db()
        self.assertEqual(artifact.expire_at, now)

    def test_create_artifact_create_files(self) -> None:
        """create_artifact() returns an Artifact and create files."""
        paths = ["src/a", "b"]
        files_size = 12

        artifact, files_contents = self.playground.create_artifact(
            paths, files_size, create_files=True
        )

        self.assertEqual(artifact.fileinartifact_set.all().count(), len(paths))

        files_in_artifact = artifact.fileinartifact_set.all().order_by("path")

        for file_in_artifact in files_in_artifact:
            file = file_in_artifact.file
            file_backend = artifact.workspace.scope.download_file_backend(file)
            local_path = file_backend.get_local_path(file)
            assert local_path is not None
            self.assertEqual(
                local_path.read_bytes(), files_contents[file_in_artifact.path]
            )

    def test_create_artifact_do_not_create_files(self) -> None:
        """create_artifact() returns an Artifact and does not create files."""
        artifact, _ = self.playground.create_artifact(
            ["README"], create_files=False
        )

        self.assertEqual(artifact.fileinartifact_set.all().count(), 0)

    def test_create_artifact_raise_value_error(self) -> None:
        """create_artifact() raise ValueError: incompatible options."""
        with self.assertRaisesRegex(
            ValueError,
            "^skip_add_files_in_store must be False if create_files is False$",
        ):
            self.playground.create_artifact(
                create_files=False, skip_add_files_in_store=True
            )

    def test_format_api_response(self) -> None:
        for response, expected in (
            (
                HttpResponse("ok"),
                "200 OK (text/html; charset=utf-8): content='ok'",
            ),
            (
                HttpResponse(
                    "ok", status=status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
                "500 Internal Server Error (text/html; charset=utf-8):"
                " content='ok'",
            ),
            (
                JsonResponse({"success": True}),
                "200 OK (application/json): content={'success': True}",
            ),
            (
                ProblemResponse(title="pr title"),
                "400 Bad Request (application/problem+json): title='pr title'",
            ),
            (
                ProblemResponse(title="", detail="pr detail"),
                "400 Bad Request (application/problem+json):"
                " title='' detail='pr detail'",
            ),
            (
                ProblemResponse(
                    title="", validation_errors={"arbitrary": "data"}
                ),
                "400 Bad Request (application/problem+json): title=''"
                " validation_errors={'arbitrary': 'data'}",
            ),
            (
                HttpResponse("x" * 300),
                f"200 OK (text/html; charset=utf-8): content='{'x' * 200}…'",
            ),
            (
                JsonResponse({"long": "x" * 200}),
                f"200 OK (application/json): content={{'long': '{'x' * 190}…",
            ),
            (
                HttpResponseBase(),
                "200 OK (text/html; charset=utf-8): content not available",
            ),
        ):
            with self.subTest(response=response):
                self.assertEqual(self.format_api_response(response), expected)

    def test_format_api_response_problemresponse_with_exception(self) -> None:
        response = ProblemResponse(title="Error")
        setattr(
            response, "_original_exception", Exception("original exception")
        )
        self.assertEqual(
            self.format_api_response(response),
            "400 Bad Request (application/problem+json):"
            " title='Error' original exception follows:\n"
            "Exception: original exception\n",
        )

    def test_assertAPIResponseOk_ok(self) -> None:
        data = {"success": True}
        response = JsonResponse(data)
        self.assertEqual(self.assertAPIResponseOk(response), data)

    def test_assertAPIResponseOk_not_json(self) -> None:
        response = HttpResponse("ok")
        with (
            self.assertRaisesRegex(
                AssertionError,
                r"content type 'text/html; charset=utf-8'"
                " is not a JSON response",
            ),
            self.assertExceptionHasFormattedAPIResponse(),
        ):
            self.assertAPIResponseOk(response)

    def test_assertAPIResponseOk_invalid_json(self) -> None:
        response = HttpResponse("{", content_type="application/json")
        with (
            self.assertRaisesRegex(
                json.JSONDecodeError,
                r"Expecting property name enclosed in double quotes",
            ),
            self.assertExceptionHasFormattedAPIResponse(),
        ):
            self.assertAPIResponseOk(response)

    def test_assertAPIResponseOk_bad_status(self) -> None:
        response = HttpResponse(
            "fail", status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        with (
            self.assertRaisesRegex(AssertionError, r"500 != 200"),
            self.assertExceptionHasFormattedAPIResponse(),
        ):
            self.assertAPIResponseOk(response)

    def test_assertAPIResponseOk_no_content_type(self) -> None:
        response = HttpResponse("ok")
        del response.headers["Content-Type"]
        with (
            self.assertRaisesRegex(
                AssertionError, r"response has no content type"
            ),
            self.assertExceptionHasFormattedAPIResponse(),
        ):
            self.assertAPIResponseOk(response)

    def test_assertResponseProblem_valid(self) -> None:
        """assertResponseProblem() does not raise any exception."""
        title = "Invalid task name"
        response = ProblemResponse(title)

        self._process_json_response(response)

        self.assertResponseProblem(response, title)

    def test_assertResponseProblem_assertions(self) -> None:
        """Exercise all the checks done by assertResponseProblem()."""
        for response, title, detail_pattern, expected_regex in (
            (
                ProblemResponse("title", status_code=status.HTTP_200_OK),
                "title",
                "detail",
                "response status 200 != 400",
            ),
            (
                Response({}, status=status.HTTP_400_BAD_REQUEST),
                "title",
                None,
                r"application/problem\+json",
            ),
            (
                Response(
                    {},
                    status=status.HTTP_400_BAD_REQUEST,
                    content_type="something_invalid",
                ),
                "title",
                None,
                r"something_invalid",
            ),
            (
                ProblemResponse("actual-title"),
                "expected-title",
                None,
                'title "actual-title" != "expected-title"',
            ),
            (
                ProblemResponse("actual-title"),
                "actual-title",
                "expected-detail",
                '"detail" not found in ',
            ),
            (
                ProblemResponse("actual-title", "actual-detail"),
                "actual-title",
                "expected-detail",
                '"expected-detail" did not match "actual-detail"',
            ),
            (
                ProblemResponse("actual-title"),
                "actual-title",
                "expected-detail",
                '"detail" not found in response',
            ),
        ):
            with self.subTest(
                response=response, title=title, detail_pattern=detail_pattern
            ):
                with self.assertRaisesRegex(
                    self.failureException, expected_regex
                ):
                    response = response
                    self._process_json_response(response)

                    self.assertResponseProblem(
                        response, title, detail_pattern=detail_pattern
                    )

    @staticmethod
    def _process_json_response(response: HttpResponse) -> None:
        """
        Simulate part of what Django does when returning responses.

        It adds a new method (json()) returning json.loads(response.content).
        If response.content_type exist assign it to
        response.headers["Content-Type"].
        """
        response.json = lambda: json.loads(  # type: ignore[attr-defined]
            response.content
        )

        if content_type := getattr(response, "content_type", False):
            response.headers["Content-Type"] = content_type

    def test_ephemeral_savepoint(self) -> None:
        """Test ephemeral_savepoint."""
        with self.ephemeral_savepoint():
            scope = self.playground.get_or_create_scope(
                "test_ephemeral_savepoint"
            )
        self.assertFalse(Scope.objects.filter(pk=scope.pk).exists())

    def test_ephemeral_savepoint_with_exception(self) -> None:
        """Test ephemeral_savepoint with exceptions."""
        with (
            self.assertRaisesRegex(RuntimeError, r"expected error"),
            self.ephemeral_savepoint() as sid,
        ):
            scope = self.playground.get_or_create_scope(
                "test_ephemeral_savepoint"
            )
            raise RuntimeError("expected error")
        self.assertTrue(Scope.objects.filter(pk=scope.pk).exists())
        transaction.savepoint_rollback(sid)
        self.assertFalse(Scope.objects.filter(pk=scope.pk).exists())

    def test_test_check_predicate_unscoped_resources(self) -> None:
        """Test checking permissions on unscoped resources."""
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()

        results = iter([True])
        with (
            mock.patch(
                "debusine.db.models.Scope.can_display",
                side_effect=lambda _: next(results),
            ),
            mock.patch(
                "debusine.test.django.get_resource_scope",
                return_value=None,
            ),
        ):
            self.assertEqual(
                self._test_check_predicate(user, scope, "can_display", True),
                [],
            )

    def test_test_check_predicate_unscoped_resourcse(self) -> None:
        """Test checking permissions on non-workspaced resources."""
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()

        results = iter([True, True])
        with (
            mock.patch(
                "debusine.db.models.Scope.can_display",
                side_effect=lambda _: next(results),
            ),
        ):
            self.assertEqual(
                self._test_check_predicate(user, scope, "can_display", True),
                [],
            )

    def test_test_check_predicate_fail_scope_in_context(self) -> None:
        """Test checking permissions failing with scope in context."""
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()

        results = iter([True, False])
        with (
            mock.patch(
                "debusine.db.models.Scope.can_display",
                side_effect=lambda _: next(results),
            ),
        ):
            self.assertEqual(
                self._test_check_predicate(user, scope, "can_display", True),
                [
                    "can_display is False for playground on debusine"
                    " with scope set in context"
                ],
            )

    def test_test_check_predicate_fail_workspace_in_context(self) -> None:
        """Test checking permissions failing with scope in context."""
        workspace = self.playground.get_default_workspace()
        user = self.playground.get_default_user()

        results = iter([True, True, False])
        with (
            mock.patch(
                "debusine.db.models.Workspace.can_display",
                side_effect=lambda _: next(results),
            ),
        ):
            self.assertEqual(
                self._test_check_predicate(
                    user, workspace, "can_display", True
                ),
                [
                    "can_display is False for playground on debusine/System"
                    " with scope and workspace set in context"
                ],
            )

    def test_assertPermission_check_predicate(self) -> None:
        """Test _test_check_predicate."""
        context.set_scope(self.playground.get_default_scope())
        context.set_user(self.playground.get_default_user())
        public = self.playground.get_default_workspace()
        private = self.playground.create_workspace(name="private", public=False)
        self.assertPermission(
            "can_display",
            users=self.playground.get_default_user(),
            allowed=public,
            denied=private,
        )

    def test_assertPermission_collections(self) -> None:
        """Test passing collections to assertPermission."""
        context.set_scope(self.playground.get_default_scope())
        context.set_user(self.playground.get_default_user())
        public = self.playground.get_default_workspace()
        private = self.playground.create_workspace(name="private", public=False)
        self.assertPermission(
            "can_display",
            users=[self.playground.get_default_user()],
            allowed=(public,),
            denied={private},
        )

    def test_assertPermission_fails(self) -> None:
        """Test assertPermission failure reporting."""
        context.set_scope(self.playground.get_default_scope())
        context.set_user(self.playground.get_default_user())
        public = self.playground.get_default_workspace()
        private = self.playground.create_workspace(name="private", public=False)

        with self.assertRaises(self.failureException) as exc:
            self.assertPermission(
                "can_display",
                users=[self.playground.get_default_user()],
                allowed=(private,),
                denied={public},
            )
        self.assertEqual(
            exc.exception.args[0],
            "Predicate permission mismatch:\n"
            "* can_display is False for playground on debusine/private"
            " with empty context\n"
            "* can_display is False for playground on debusine/private"
            " with scope set in context\n"
            "* can_display is True for playground on debusine/System"
            " with empty context\n"
            "* can_display is True for playground on debusine/System"
            " with scope set in context\n"
            "* can_display is True for playground on debusine/System"
            " with scope and workspace set in context\n"
            "* can_display for playground selects debusine/System"
            " instead of debusine/private\n",
        )

        with self.assertRaises(self.failureException) as exc:
            self.assertPermission(
                "can_display",
                users=[self.playground.get_default_user()],
                allowed=(),
                denied=(private,),
            )
        self.assertEqual(
            exc.exception.args[0],
            "Predicate permission mismatch:\n"
            "* can_display for playground selects debusine/System"
            " instead of nothing\n",
        )

    def test_assertPermission_fails1(self) -> None:
        """Test assertPermission failure reporting."""
        workspace = self.playground.get_default_workspace()
        workspace.public = False
        workspace.save()

        with self.assertRaises(self.failureException) as exc:
            self.assertPermission(
                "can_display",
                users=[self.playground.get_default_user()],
                allowed=(workspace),
            )
        self.assertEqual(
            exc.exception.args[0],
            "Predicate permission mismatch:\n"
            "* can_display is False for playground on debusine/System"
            " with empty context\n"
            "* can_display is False for playground on debusine/System"
            " with scope set in context\n"
            "* can_display for playground selects nothing"
            " instead of debusine/System\n",
        )

    def test_build_permission_when_role_testlist(self) -> None:
        """Test the _build_permission_when_role_testlist method."""
        scope = self.playground.get_or_create_scope("scope")
        workspace = self.playground.create_workspace(
            name="workspace", scope=scope
        )
        # Alias the long function name
        f = self._build_permission_when_role_testlist
        self.assertEqual(
            f(scope.can_display, "owner", scope_roles="owner"),
            [
                (scope, Scope.Roles.OWNER),
                (scope, Scope.Roles.OWNER),
            ],
        )
        self.assertEqual(
            f(
                workspace.can_display,
                [Workspace.Roles.OWNER, "contributor"],
                scope_roles="owner",
                workspace_roles="owner",
            ),
            [
                (scope, Scope.Roles.OWNER),
                (workspace, Workspace.Roles.OWNER),
                (workspace, Workspace.Roles.OWNER),
                (workspace, Workspace.Roles.CONTRIBUTOR),
            ],
        )
        with self.assertRaises(AssertionError):
            f(scope.can_display, workspace_roles="owner")

    def test_assertPermissionWhenRole(self) -> None:
        """Test assertPermissionWhenRole."""
        scope = self.playground.get_or_create_scope("scope")
        workspace = self.playground.create_workspace(
            name="workspace", scope=scope
        )
        user = self.playground.get_default_user()
        work_request = self.playground.create_work_request(workspace=workspace)
        self.assertPermissionWhenRole(
            scope.can_create_workspace, user, roles="owner"
        )
        self.assertPermissionWhenRole(
            scope.can_create_workspace, user, scope_roles="owner"
        )
        self.assertPermissionWhenRole(
            work_request.can_unblock, user, workspace_roles="owner"
        )
        self.assertNoPermissionWhenRole(
            work_request.can_unblock, user, workspace_roles="contributor"
        )
        with self.assertRaisesRegex(
            self.failureException, r"is false even with contributor"
        ):
            self.assertPermissionWhenRole(
                work_request.can_unblock, user, workspace_roles="contributor"
            )
        with self.assertRaisesRegex(
            self.failureException, r"is true when given owner"
        ):
            self.assertNoPermissionWhenRole(
                scope.can_create_workspace, user, roles="owner"
            )

    def test_assertPermissionWhenRole_had_perm(self) -> None:
        """Test assertPermissionWhenRole when permission was already there."""
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()
        self.playground.create_group_role(
            scope, Scope.Roles.OWNER, users=[user]
        )
        with self.assertRaisesRegex(
            self.failureException, r"is true even without owner"
        ):
            self.assertPermissionWhenRole(scope.can_display, user, "owner")
        with self.assertRaisesRegex(
            self.failureException, r"is true even without owner"
        ):
            self.assertNoPermissionWhenRole(scope.can_display, user, "owner")

    def test_custom_template(self) -> None:
        """Test installing a custom template."""
        with self.custom_template("web/foo.html") as path:
            self.assertEqual(
                path,
                Path(settings.DEBUSINE_TEMPLATE_DIRECTORY) / "web" / "foo.html",
            )
            path.write_text("TEST")

        self.assertFalse(path.exists())

    def test_custom_template_not_written(self) -> None:
        """Test installing a custom template."""
        with self.custom_template("web/foo.html") as path:
            pass
        self.assertFalse(path.exists())

    def test_clear_template_cache(self) -> None:
        """Test clearing the template cache."""
        engine = django.template.engines["django"]

        with (
            self.custom_template("main.html") as main,
            self.custom_template("included.html") as included,
        ):
            main.write_text("A{% include 'included.html' %}")
            included.write_text("B")
            template = engine.get_template("main.html")
            self.assertEqual(template.render({}), "AB")

            # Changing the contents of an included template doesn't invalidate
            # the template cache
            included.write_text("C")
            template = engine.get_template("main.html")
            self.assertEqual(template.render({}), "AB")

            # Invalidating it manually works
            self.clear_template_cache()
            template = engine.get_template("main.html")
            self.assertEqual(template.render({}), "AC")

    def test_clear_template_cache_skip_unknown(self) -> None:
        """Test skipping unknown engines and loaders."""
        from django.template.backends.django import DjangoTemplates
        from django.template.loaders.base import Loader

        mock_django_loader = mock.Mock(spec=Loader)
        mock_other_loader = mock.Mock()
        mock_loaders = [mock_django_loader, mock_other_loader]

        mock_django_engine = mock.Mock(spec=DjangoTemplates)
        mock_django_engine.engine = mock.Mock()
        mock_django_engine.engine.template_loaders = mock_loaders
        mock_other_engine = mock.Mock()
        mock_engines = [mock_django_engine, mock_other_engine]

        with mock.patch(
            "django.template.engines.all", return_value=mock_engines
        ):
            self.clear_template_cache()

        mock_django_loader.reset.assert_called()
        mock_other_loader.reset.assert_not_called()


# TODO: coverage is confused by something here, possibly
# https://github.com/python/cpython/issues/106749
class TestChannelsHelpersMixinTests(
    ChannelsHelpersMixin, TestCase
):  # pragma: no cover
    """Tests for methods in ChannelsHelpersMixin."""

    # Default channel name to be used during the tests.
    channel_name = "generic-channel-for-testing"

    async def test_create_channel(self) -> None:
        """Create channel return a dictionary with layer and name keys."""
        channel = await self.create_channel("channel-test")

        self.assertEqual(channel.keys(), {"layer", "name"})

    async def test_assert_channel_received_raises_exception(self) -> None:
        """assert_channel_received raise exception: nothing was received."""
        channel = await self.create_channel(self.channel_name)
        with self.assertRaisesRegex(
            self.failureException,
            "^Expected '{'type': 'work_request'}' received nothing$",
        ):
            await self.assert_channel_received(
                channel, {"type": "work_request"}
            )

    async def test_assert_channel_received_raise_wrong_data(self) -> None:
        """assert_channel_received raise exception: unexpected data received."""
        channel = await self.create_channel(self.channel_name)
        message = {"type": "work_request.assigned"}
        await channel["layer"].group_send(
            self.channel_name, {"some other message": "values"}
        )

        with self.assertRaises(AssertionError):
            await self.assert_channel_received(channel, message)

    async def test_assert_channel_received_do_not_raise(self) -> None:
        """assert_channel_received does not raise an exception."""
        channel = await self.create_channel(self.channel_name)
        message = {"type": "work_request.assigned"}
        await channel["layer"].group_send(self.channel_name, message)

        await self.assert_channel_received(channel, message)

    async def test_assert_channel_nothing_received_do_not_raise(self) -> None:
        """assert_channel_nothing_received does not raise an exception."""
        channel = await self.create_channel(self.channel_name)
        await self.assert_channel_nothing_received(channel)

    async def test_assert_channel_nothing_receive_raise(self) -> None:
        """assert_channel_nothing_received raise exception: data is received."""
        channel = await self.create_channel(self.channel_name)
        message = {"type": "work_request.assigned"}

        await channel["layer"].group_send(self.channel_name, message)

        with self.assertRaisesRegex(
            self.failureException,
            "^Expected nothing. Received: '{'type': 'work_request.assigned'}'$",
        ):
            await self.assert_channel_nothing_received(channel)


class AssertRolePredicateTests(TestCase):
    """Tests for assertRolePredicate."""

    def assertFails(
        self, pattern: str, resource_cls: type[Any] = Workspace
    ) -> None:
        """Call the test and expect it to fail with matching error."""
        with self.assertRaisesRegex(self.failureException, re.escape(pattern)):
            self.assertRolePredicate(
                resource_cls(),
                "can_configure",
                Workspace.Roles.OWNER,
                workers=Allow.NEVER,
            )

    def test_no_perm_check(self) -> None:
        self.assertFails(
            "object has no @permission_check can_configure", resource_cls=object
        )

    def test_perm_check_undecorated(self) -> None:
        def f(_: PermissionUser) -> Never:
            raise NotImplementedError()

        with mock.patch("debusine.db.models.Workspace.can_configure", f):
            self.assertFails(
                "Workspace.can_configure is not marked as @permission_check"
            )

    def test_perm_check_wrong_result(self) -> None:
        def _pred(result: bool) -> Callable[[Workspace, PermissionUser], bool]:
            @permissions.permission_check("fail", workers=Allow.NEVER)
            def _predicate(self: Workspace, user: PermissionUser) -> bool:
                self.has_role(user, Workspace.Roles.OWNER)
                return result

            return _predicate

        with mock.patch(
            "debusine.db.models.Workspace.can_configure", _pred(True)
        ):
            self.assertFails("True is not false")
        with mock.patch(
            "debusine.db.models.Workspace.can_configure", _pred(False)
        ):
            self.assertFails("False is not true")

    def test_perm_check_wrong_role(self) -> None:
        def _pred(
            *roles: Workspace.Roles,
        ) -> Callable[[Workspace, PermissionUser], bool]:
            results = list(roles)

            @permissions.permission_check("fail", workers=Allow.NEVER)
            def _predicate(self: Workspace, user: PermissionUser) -> bool:
                return self.has_role(user, results.pop(0))

            return _predicate

        with mock.patch(
            "debusine.db.models.Workspace.can_configure",
            _pred(Workspace.Roles.CONTRIBUTOR),
        ):
            self.assertFails("expected call not found")

        with mock.patch(
            "debusine.db.models.Workspace.can_configure",
            _pred(Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR),
        ):
            self.assertFails("expected call not found")

    def test_no_manager_filter(self) -> None:
        with mock.patch("debusine.db.models.Workspace.objects", object()):
            self.assertFails(
                "Workspace.objects has no @permission_filter can_configure"
            )

    def test_manager_filter_undecorated(self) -> None:
        def f(_: PermissionUser) -> Never:
            raise NotImplementedError()

        with mock.patch.object(Workspace.objects, "can_configure", f):
            self.assertFails(
                "Workspace.objects.can_configure"
                " is not marked as @permission_filter"
            )

    def test_manager_filter_wrong_result(self) -> None:
        @permissions.permission_filter(workers=Allow.NEVER)
        def _predicate(
            self: WorkspaceQuerySet[Any], user: PermissionUser
        ) -> WorkspaceQuerySet[Any]:
            self.with_role(user, Workspace.Roles.OWNER)
            return None  # type: ignore[return-value]

        with mock.patch(
            "debusine.db.models.workspaces.WorkspaceQuerySet.can_configure",
            _predicate,
        ):
            self.assertFails("None is not <object")

    def test_manager_filter_wrong_role(self) -> None:
        @permissions.permission_filter(workers=Allow.NEVER)
        def _predicate(
            self: WorkspaceQuerySet[Any], user: PermissionUser
        ) -> WorkspaceQuerySet[Any]:
            return self.with_role(user, Workspace.Roles.CONTRIBUTOR)

        with mock.patch(
            "debusine.db.models.workspaces.WorkspaceQuerySet.can_configure",
            _predicate,
        ):
            self.assertFails("expected call not found")

    def test_no_queryset_filter(self) -> None:
        class TestQuerySet(QuerySet[Workspace]):
            def with_role(
                self,
                user: PermissionUser,  # noqa: U100
                role: Workspace.Roles,  # noqa: U100
            ) -> Self:
                raise NotImplementedError()

        with mock.patch(
            "debusine.db.models.workspaces.WorkspaceManager.all",
            return_value=TestQuerySet(Workspace),
        ):
            self.assertFails(
                "QuerySet[Workspace] has no @permission_filter can_configure"
            )

    def test_queryset_filter_undecorated(self) -> None:
        class TestQuerySet(QuerySet[Workspace]):
            def with_role(
                self, user: PermissionUser, role: Workspace.Roles  # noqa: U100
            ) -> Self:
                raise NotImplementedError()

            def can_configure(self, _: PermissionUser) -> Self:
                raise NotImplementedError()

        with mock.patch(
            "debusine.db.models.workspaces.WorkspaceManager.all",
            return_value=TestQuerySet(Workspace),
        ):
            self.assertFails(
                "QuerySet[Workspace].can_configure"
                " is not marked as @permission_filter"
            )

    def test_queryset_filter_wrong_result(self) -> None:
        class TestQuerySet(QuerySet[Workspace]):
            def with_role(
                self, user: PermissionUser, role: Workspace.Roles  # noqa: U100
            ) -> Self:
                raise NotImplementedError()

            @permissions.permission_filter(workers=Allow.NEVER)
            def can_configure(self, user: PermissionUser) -> Self:
                self.with_role(user, Workspace.Roles.OWNER)
                return None  # type: ignore[return-value]

        with mock.patch(
            "debusine.db.models.workspaces.WorkspaceManager.all",
            return_value=TestQuerySet(Workspace),
        ):
            self.assertFails("None is not <object")

    def test_queryset_filter_wrong_role(self) -> None:
        class TestQuerySet(QuerySet[Workspace]):
            def with_role(
                self, user: PermissionUser, role: Workspace.Roles  # noqa: U100
            ) -> Self:
                raise NotImplementedError()

            @permissions.permission_filter(workers=Allow.NEVER)
            def can_configure(self, user: PermissionUser) -> Self:
                return self.with_role(user, Workspace.Roles.CONTRIBUTOR)

        with mock.patch(
            "debusine.db.models.workspaces.WorkspaceManager.all",
            return_value=TestQuerySet(Workspace),
        ):
            self.assertFails("expected call not found")

    def test_predicate(self) -> None:
        self.assertRolePredicate(
            Workspace(),
            "can_configure",
            Workspace.Roles.OWNER,
            workers=Allow.NEVER,
        )
