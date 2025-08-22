# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Tests for the base view code."""

from typing import Any, Union

from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.urls import reverse
from rest_framework import status
from rest_framework.request import Request
from rest_framework.views import APIView

from debusine.db.context import context
from debusine.db.models import Scope, Token, Workspace
from debusine.server.exceptions import DebusineAPIException
from debusine.server.views.base import BaseAPIView
from debusine.server.views.rest import (
    IsGet,
    IsTokenAuthenticated,
    IsTokenUserAuthenticated,
    IsWorkerAuthenticated,
)
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)

TestResponseType  # Fake use for vulture


class IsTokenAuthenticatedTests(TestCase):
    """Tests for IsTokenAuthenticated."""

    def setUp(self) -> None:
        """Set up common objects."""
        super().setUp()
        self.is_token_authenticated = IsTokenAuthenticated()
        self.request = Request(HttpRequest())

    def test_request_with_valid_token_worker_yes_permission(self) -> None:
        """IsTokenAuthenticated.has_permission() return True: valid token."""
        self.request.auth = self.playground.create_worker_token()
        self.assertTrue(
            self.is_token_authenticated.has_permission(self.request, APIView())
        )

    def test_request_with_non_existing_token_no_permission(self) -> None:
        """IsTokenAuthenticated.has_permission() return False: invalid token."""
        self.request.auth = None
        self.assertFalse(
            self.is_token_authenticated.has_permission(self.request, APIView())
        )

    def test_request_with_disabled_token_no_permission(self) -> None:
        """
        IsTokenAuthenticated.has_permission() return False.

        The token is disabled.
        """
        self.request.auth = Token.objects.create()
        self.assertFalse(
            self.is_token_authenticated.has_permission(self.request, APIView())
        )


class IsTokenUserAuthenticatedTests(TestCase):
    """Tests for IsTokenUserAuthenticated class."""

    def setUp(self) -> None:
        """Set up test."""
        super().setUp()
        self.is_token_user_authenticated = IsTokenUserAuthenticated()
        self.request = Request(HttpRequest())

    def test_request_disabled_token(self) -> None:
        """Request with a Token header that is disabled: permission denied."""
        self.request.auth = Token.objects.create()
        self.assertFalse(self.request.auth.enabled)
        self.assertFalse(
            self.is_token_user_authenticated.has_permission(
                self.request, APIView()
            )
        )

    def test_request_non_existing_token(self) -> None:
        """Request without a token."""
        self.request.auth = None
        self.assertFalse(
            self.is_token_user_authenticated.has_permission(
                self.request, APIView()
            )
        )

    def test_request_enabled_token_without_user(self) -> None:
        """Request with a Token that does not have a user: permission denied."""
        self.request.auth = self.playground.create_bare_token()
        self.assertFalse(
            self.is_token_user_authenticated.has_permission(
                self.request, APIView()
            )
        )

    def test_request_enabled_token_with_user(self) -> None:
        """Request with a Token that does have a user: permission granted.."""
        self.request.auth = self.playground.create_user_token()
        self.assertTrue(
            self.is_token_user_authenticated.has_permission(
                self.request, APIView()
            )
        )


class IsWorkerAuthenticatedTests(TestCase):
    """Tests for IsWorkerAuthenticated."""

    def setUp(self) -> None:
        """Set up common objects."""
        super().setUp()
        self.is_worker_authenticated = IsWorkerAuthenticated()
        self.request = Request(HttpRequest())

    def test_request_with_valid_token_worker_yes_permission(self) -> None:
        """IsWorkerAuthenticated.has_permission() return True: valid token."""
        self.request.auth = self.playground.create_worker_token()
        self.assertTrue(
            self.is_worker_authenticated.has_permission(self.request, APIView())
        )

    def test_request_with_non_existing_token_no_permission(self) -> None:
        """
        IsWorkerAuthenticated.has_permission() return False.

        request.auth is None.
        """
        self.request.auth = None
        self.assertFalse(
            self.is_worker_authenticated.has_permission(self.request, APIView())
        )

    def test_request_with_token_no_associated_worker_no_permission(
        self,
    ) -> None:
        """IsWorkerAuthenticated.has_permission() return False: no worker."""
        self.request.auth = self.playground.create_bare_token()
        self.assertFalse(
            self.is_worker_authenticated.has_permission(self.request, APIView())
        )


class IsGetTests(TestCase):
    """Tests for IsGet."""

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.is_get = IsGet()
        self.http_request = Request(HttpRequest())

    def test_request_is_get(self) -> None:
        """Request method is GET. Return True."""
        self.http_request.method = "GET"

        self.assertTrue(
            self.is_get.has_permission(self.http_request, APIView())
        )

    def test_request_is_post(self) -> None:
        """Request method is POST. Return False."""
        self.http_request.method = "POST"

        self.assertFalse(
            self.is_get.has_permission(self.http_request, APIView())
        )


class TestBaseAPIView(TestCase):
    """Test the BaseAPIView view."""

    def test_enforce(self) -> None:
        """Test the enforce method."""
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()
        context.set_scope(scope)
        context.set_user(user)
        view = BaseAPIView()
        with override_permission(Scope, "can_display", AllowAll):
            view.enforce(scope.can_display)
        with (
            override_permission(Scope, "can_display", DenyAll),
            self.assertRaises(DebusineAPIException) as exc,
        ):
            view.enforce(scope.can_display)
        e = exc.exception
        self.assertEqual(
            e.debusine_title, "playground cannot display scope debusine"
        )
        self.assertIsNone(e.debusine_detail)
        self.assertIsNone(e.debusine_validation_errors)
        self.assertEqual(e.debusine_status_code, status.HTTP_403_FORBIDDEN)

    def test_set_current_workspace(self) -> None:
        """Test the set_current_workspace method."""
        workspace: Workspace | str
        error: str | None
        workspace = self.playground.get_default_workspace()
        private_workspace = self.playground.create_workspace(name="private")
        scope = self.playground.get_default_scope()
        user = self.playground.get_default_user()
        context.set_scope(scope)
        context.set_user(user)
        view = BaseAPIView()
        for workspace, error in (
            (workspace, None),
            (workspace.name, None),
            (
                private_workspace,
                "Workspace private not found in scope debusine",
            ),
            (
                private_workspace.name,
                "Workspace private not found in scope debusine",
            ),
            (
                "does-not-exist",
                "Workspace does-not-exist not found in scope debusine",
            ),
        ):
            with self.subTest(workspace=workspace), context.local():
                if not error:
                    view.set_current_workspace(workspace)
                else:
                    with self.assertRaises(DebusineAPIException) as exc:
                        view.set_current_workspace(workspace)
                    self.assertEqual(exc.exception.debusine_detail, error)


class TestWhoami(TestCase):
    """Test authentication using the whoami view."""

    def whoami(
        self, token: Token | str | None = None, client: Client | None = None
    ) -> "Union[dict[str, Any], TestResponseType]":
        """Call the whoami API view."""
        if client is None:
            client = self.client

        headers = {}
        match token:
            case None:
                pass
            case str():
                headers["token"] = token
            case _:
                headers["token"] = token.key

        response = client.get(reverse("api:whoami"), headers=headers)
        if hasattr(response, "data"):
            data = getattr(response, "data")
            assert isinstance(data, dict)
            return data
        else:
            return response

    def test_bare_token(self) -> None:
        """Try a bare token."""
        # Non-worker, non-user token
        self.assertEqual(
            self.whoami(self.playground.create_bare_token()),
            {'auth': True, 'user': "<anonymous>", 'worker_token': False},
        )

    def test_worker_token(self) -> None:
        """Try a worker token."""
        self.assertEqual(
            self.whoami(self.playground.create_worker_token()),
            {'auth': True, 'user': "<anonymous>", 'worker_token': True},
        )

    def test_user_token(self) -> None:
        """Try a user token."""
        self.assertEqual(
            self.whoami(self.playground.create_user_token()),
            {'auth': True, 'user': "playground", 'worker_token': False},
        )

    def test_both_token(self) -> None:
        """Try a user and worker token."""
        # Worker and user token
        token = self.playground.create_worker_token()
        token.user = self.playground.get_default_user()
        token.save()
        response = self.whoami(token)
        assert isinstance(response, HttpResponse)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.content,
            b"a token cannot be both a user and a worker token",
        )

    def test_no_token(self) -> None:
        """Try without a token."""
        self.assertEqual(
            self.whoami(),
            {'auth': False, 'user': "<anonymous>", 'worker_token': False},
        )

    def test_session_auth(self) -> None:
        """Try django auth."""
        user = self.playground.get_default_user()
        client = Client()
        client.force_login(user)
        self.assertEqual(
            self.whoami(client=client),
            {'auth': False, 'user': user.username, 'worker_token': False},
        )

    def test_token_key_does_not_exist(self) -> None:
        """Try a Token key that does not exist."""
        self.assertEqual(
            self.whoami("does-not-exist"),
            {'auth': False, 'user': "<anonymous>", 'worker_token': False},
        )

    def test_token_disabled(self) -> None:
        """Try a disabled Token."""
        # Worker and user token
        token = self.playground.create_user_token(enabled=False)
        self.assertEqual(
            self.whoami(token),
            {'auth': False, 'user': "<anonymous>", 'worker_token': False},
        )
