# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test the Debusine extensions to Signon."""

import contextlib
from collections.abc import Generator
from typing import Any, ClassVar
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, override_settings

from debusine.db.models import Group, Identity, Scope
from debusine.server.signon import providers
from debusine.server.signon.debusine import DebusineSignon
from debusine.test.django import TestCase


@contextlib.contextmanager
def provider_options(**kwargs: Any) -> Generator[None, None, None]:
    """Set SIGNON_PROVIDERS with the given options."""
    with override_settings(
        SIGNON_PROVIDERS=[
            providers.Provider(
                name="test",
                label="sso.debian.org",
                options=kwargs,
            ),
        ]
    ):
        yield


class TestDebusineSignon(TestCase):
    """Test DebusineSignon."""

    scope: ClassVar[Scope]
    group: ClassVar[Group]
    identity: ClassVar[Identity]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope = cls.playground.get_default_scope()
        cls.group = cls.playground.create_group(name="group")
        cls.identity = Identity.objects.create(
            issuer="test",
            subject="test@debian.org",
            claims={"name": "Test User", "email": "test@debian.org"},
        )

    def setUp(self) -> None:
        """Provide a mock unauthenticated request for tests."""
        super().setUp()
        self.factory = RequestFactory()
        self.request = self.factory.get("/")

    def test_create_user_plain(self) -> None:
        """Create a user from an identity, without add_to_group."""
        with provider_options():
            signon = DebusineSignon(self.request)
            user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])

    def test_create_user_add_to_group(self) -> None:
        """Create a user from an identity, without add_to_group."""
        with provider_options(add_to_group="debusine/group"):
            signon = DebusineSignon(self.request)
            user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [self.group])

    def test_create_user_add_to_wrong_group(self) -> None:
        """Create a user from an identity, with add_to_group."""
        with provider_options(add_to_group="debusine/does-not-exist"):
            signon = DebusineSignon(self.request)
            with self.assertRaisesRegex(
                ImproperlyConfigured,
                r"Signon provider 'test' adds to missing group"
                r" 'debusine/does-not-exist'",
            ):
                signon.create_user_from_identity(self.identity)

    def test_create_user_no_user(self) -> None:
        """Test when Signon.create_user_from_identity fails."""
        with mock.patch(
            "debusine.server.signon.signon.Signon.create_user_from_identity",
            return_value=None,
        ):
            signon = DebusineSignon(self.request)
            user = signon.create_user_from_identity(self.identity)
        self.assertIsNone(user)

    def test_create_user_no_provider(self) -> None:
        """Test when the identity has no provider."""
        with (
            provider_options(add_to_group="debusine/group"),
            mock.patch(
                "debusine.server.signon.signon"
                ".Signon.get_provider_for_identity",
                return_value=None,
            ),
        ):
            signon = DebusineSignon(self.request)
            user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])
