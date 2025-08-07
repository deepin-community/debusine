# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for scope handling in views."""

from collections.abc import Callable
from typing import Any, ClassVar, Generic, Protocol, TypeVar, cast
from unittest import mock

import django.http
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import Scope, Token, User
from debusine.server.middlewares.scopes import (
    AuthorizationMiddleware,
    ScopeMiddleware,
)
from debusine.test.django import TestCase

#: Singleton response used to check if a middleware called get_response()
MOCK_RESPONSE = django.http.HttpResponse()


class MiddlewareProtocol(Protocol):
    """A protocol defining our expectations of middlewares."""

    def __init__(
        self,
        get_response: Callable[
            [django.http.HttpRequest], django.http.HttpResponse
        ],
    ) -> None:
        """Instantiate the middleware."""

    def __call__(
        self, request: django.http.HttpRequest
    ) -> django.http.HttpResponse:
        """Call the middleware."""


_M = TypeVar("_M", bound=MiddlewareProtocol)


class MiddlewareTestMixin(TestCase, Generic[_M]):
    """Common functions to test middlewares."""

    middleware_class: type[_M]

    scope: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a database layout for views."""
        super().setUpTestData()
        cls.scope = cls.playground.get_or_create_scope("scope")

    def get_middleware(self) -> _M:
        """Instantiate a test middleware."""
        return self.middleware_class(
            cast(
                Callable[[django.http.HttpRequest], django.http.HttpResponse],
                lambda x: MOCK_RESPONSE,  # noqa: U100
            )
        )

    def request_for_path(
        self, path_info: str, **kwargs: Any
    ) -> django.http.HttpRequest:
        """Configure a request for path_info with the middleware."""
        request = RequestFactory().get(path_info, **kwargs)
        self.get_middleware()(request)
        return request


class ScopeMiddlewareTests(MiddlewareTestMixin[ScopeMiddleware]):
    """Test ScopeMiddleware."""

    middleware_class = ScopeMiddleware

    def test_get_scope(self) -> None:
        """Test get_scope."""
        default = self.playground.get_default_scope()
        mw = self.get_middleware()
        self.assertEqual(mw.get_scope(settings.DEBUSINE_DEFAULT_SCOPE), default)
        self.assertEqual(mw.get_scope("scope"), self.scope)
        with self.assertRaises(django.http.Http404):
            mw.get_scope("nonexistent")

    def test_request_setup_debusine(self) -> None:
        """Test request setup with the fallback scope."""
        request = self.request_for_path(f"/{settings.DEBUSINE_DEFAULT_SCOPE}/")
        self.assertEqual(getattr(request, "urlconf"), "debusine.project.urls")
        self.assertEqual(context.scope, self.playground.get_default_scope())

    def test_request_setup_api(self) -> None:
        """Test request setup for API calls."""
        request = self.request_for_path("/api")
        self.assertEqual(getattr(request, "urlconf"), "debusine.project.urls")
        self.assertEqual(context.scope, self.playground.get_default_scope())

    def test_request_setup_api_scope_in_header(self) -> None:
        """Test request setup for API calls with scope in header."""
        # TODO: use headers={"X-Debusine-Scope": "scope"} from Django 4.2+
        request = self.request_for_path("/api", HTTP_X_DEBUSINE_SCOPE="scope")
        self.assertEqual(
            getattr(request, "urlconf"), "debusine.server._urlconfs.scope"
        )
        self.assertEqual(context.scope, self.scope)

    def test_request_setup_scope(self) -> None:
        """Test request setup with a valid scope."""
        request = self.request_for_path("/scope/")
        self.assertEqual(
            getattr(request, "urlconf"), "debusine.server._urlconfs.scope"
        )
        self.assertEqual(context.scope, self.scope)

    def test_request_setup_wrong_scope(self) -> None:
        """Test request setup with an invalid scope."""
        with self.assertRaises(django.http.Http404):
            self.request_for_path("/wrongscope/")


class AuthorizationMiddlewareTests(
    MiddlewareTestMixin[AuthorizationMiddleware]
):
    """Test AuthorizationMiddleware."""

    middleware_class = AuthorizationMiddleware

    def setUp(self) -> None:
        """Set the scope in context."""
        super().setUp()
        context.set_scope(self.scope)

    def make_request(
        self, user: User | None = None, token: Token | str | None = None
    ) -> django.http.HttpRequest:
        """Create a request."""
        headers = {}
        match token:
            case Token():
                headers["token"] = token.key
            case str():
                headers["token"] = token
        request = RequestFactory().get("/", headers=headers)
        request.user = user or AnonymousUser()
        return request

    def test_allowed(self) -> None:
        """Test the allowed case."""
        # There currently is no permission predicate implemented for scope
        # visibility, only a comment placeholder in Context.set_user.
        mw = self.get_middleware()
        request = self.make_request(user=self.playground.get_default_user())
        self.assertIs(mw(request), MOCK_RESPONSE)
        self.assertEqual(context.user, self.playground.get_default_user())

    def test_forbidden(self) -> None:
        """Test the forbidden case."""
        # There currently is no permission predicate implemented for scope
        # visibility, only a comment placeholder in Context.set_user.
        # Mock Context.set_user instead of a permission, to test handling of
        # failure to set it
        mw = self.get_middleware()
        request = self.make_request(user=self.playground.get_default_user())
        with mock.patch(
            "debusine.db.context.Context.set_user",
            side_effect=ContextConsistencyError("expected fail"),
        ):
            response = mw(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content, b"expected fail")
        self.assertIsNone(context.user)

    def test_evaluates_lazy_object(self) -> None:
        """Lazy objects are evaluated before storing them in the context."""
        mw = self.get_middleware()
        request = RequestFactory().get("/")
        request.user = SimpleLazyObject(
            self.playground.get_default_user
        )  # type: ignore[assignment]
        self.assertIs(mw(request), MOCK_RESPONSE)
        default_user = self.playground.get_default_user()
        with mock.patch.object(User.objects, "get", side_effect=RuntimeError):
            self.assertEqual(context.user, default_user)

    def test_bare_token(self) -> None:
        """Check handling of bare tokens."""
        token = self.playground.create_bare_token()
        mw = self.get_middleware()
        request = self.make_request(token=token)
        response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)
        self.assertEqual(getattr(request, "_debusine_token"), token)
        self.assertIsNone(context.worker_token)

    def test_user_token(self) -> None:
        """Check handling of user tokens."""
        token = self.playground.create_user_token()
        mw = self.get_middleware()
        request = self.make_request(token=token)
        response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)
        self.assertEqual(getattr(request, "_debusine_token"), token)
        self.assertIsNone(context.worker_token)

    def test_user_token_logged_in(self) -> None:
        """Check handling of user tokens while logged in."""
        token = self.playground.create_user_token()
        mw = self.get_middleware()
        request = self.make_request(token=token, user=token.user)
        response = mw(request)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.content,
            b"cannot use both Django and user token authentication",
        )
        self.assertIsNone(context.user)

    def test_user_token_forbidden(self) -> None:
        """Test the forbidden case for user token."""
        token = self.playground.create_user_token()
        mw = self.get_middleware()
        request = self.make_request(token=token)

        # There currently is no permission predicate implemented for scope
        # visibility, only a comment placeholder in Context.set_user.
        # Mock Context.set_user instead of a permission, to test handling of
        # failure to set it
        with mock.patch(
            "debusine.db.context.Context.set_user",
            side_effect=ContextConsistencyError("expected fail"),
        ):
            response = mw(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content, b"expected fail")
        self.assertIsNone(context.user)

    def test_worker_token(self) -> None:
        """Check handling of worker tokens."""
        token = self.playground.create_worker_token()
        mw = self.get_middleware()
        request = self.make_request(token=token)
        response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)
        self.assertEqual(getattr(request, "_debusine_token"), token)
        self.assertEqual(context.worker_token, token)

    def test_worker_token_disabled(self) -> None:
        """Check handling of disabled tokens."""
        token = self.playground.create_worker_token()
        token.disable()
        mw = self.get_middleware()
        request = self.make_request(token=token)
        response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)
        self.assertIsNone(getattr(request, "_debusine_token"))
        self.assertIsNone(context.worker_token)

    def test_token_invalid(self) -> None:
        """Check handling of invalid tokens."""
        mw = self.get_middleware()
        request = self.make_request(token="invalid")
        response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)
        self.assertIsNone(getattr(request, "_debusine_token"))
        self.assertIsNone(context.worker_token)

    def test_user_token_and_django_user(self) -> None:
        """Check handling of user tokens and django users."""
        token = self.playground.create_worker_token()
        token.user = self.playground.get_default_user()
        token.save()
        mw = self.get_middleware()
        request = self.make_request(
            token=token, user=self.playground.get_default_user()
        )
        response = mw(request)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.content,
            b"a token cannot be both a user and a worker token",
        )
        self.assertIsNone(context.user)

    def test_count_queries(self) -> None:
        """Check the number of queries done to authenticate."""
        token = self.playground.create_worker_token()
        mw = self.get_middleware()
        request = self.make_request(token=token)
        with self.assertNumQueries(2):
            response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)

    def test_update_last_seen_at(self) -> None:
        """Request with a token updates last_seen_at."""
        before = timezone.now()
        token = self.playground.create_bare_token()
        self.assertIsNone(token.last_seen_at)
        mw = self.get_middleware()
        request = self.make_request(token=token)
        response = mw(request)
        self.assertIs(response, MOCK_RESPONSE)

        # token.last_seen_at was updated
        token.refresh_from_db()
        assert token.last_seen_at is not None
        self.assertGreaterEqual(token.last_seen_at, before)
        self.assertLessEqual(token.last_seen_at, timezone.now())
