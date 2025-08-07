# Copyright 2020-2023 Enrico Zini <enrico@debian.org>
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test of the backend for signon authentication using external providers."""


import re
from typing import Any

import django.contrib.sessions.backends.base
from django.contrib import auth
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase, override_settings

from debusine.db.models import Identity, User
from debusine.server.signon import providers
from debusine.server.signon.auth import SignonAuthBackend
from debusine.server.signon.middleware import RequestSignonProtocol
from debusine.server.signon.signon import Signon


class MockSession(django.contrib.sessions.backends.base.SessionBase):
    """In-memory session with no persistence, used for tests."""

    # TODO: is there a more standard way to provide a mock session?
    # Would SessionStore be enough?

    # def exists(self, session_key: str) -> bool:
    #     """Mock SessionBase API endpoint."""
    #     return True

    def create(self) -> None:
        """Mock SessionBase API endpoint."""

    # def save(self, must_create: bool = False) -> None:
    #     """Mock SessionBase API endpoint."""
    #     pass

    def delete(self, *args: Any) -> None:
        """Mock SessionBase API endpoint."""

    # def load(self) -> dict[str, Any]:
    #     """Mock SessionBase API endpoint."""
    #     pass

    # @classmethod
    # def clear_expired(cls) -> None:
    #     """Mock SessionBase API endpoint."""
    #     pass


@override_settings(
    SIGNON_PROVIDERS=[
        providers.Provider(name="debsso", label="sso.debian.org"),
        providers.GitlabProvider(
            name="salsa",
            label="Salsa",
            icon="signon/gitlabian.svg",
            client_id="123client_id",
            client_secret="123client_secret",
            url="https://salsa.debian.org",
            scope=("openid", "profile", "email"),
        ),
    ]
)
class TestAuthentication(TestCase):
    """Test Signon."""

    def setUp(self) -> None:
        """Provide a mock unauthenticated request for tests."""
        super().setUp()
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.session = MockSession()
        self.request.user = AnonymousUser()
        setattr(self.request, "signon", Signon(self.request))

    def _make_identity(
        self,
        user: User | None = None,
        issuer: str = "salsa",
        claims: dict[str, Any] | None = None,
    ) -> Identity:
        """Create a test identity."""
        subject = f"{issuer}@debian.org"
        if claims is None:
            claims = {}
        return Identity.objects.create(
            user=user,
            issuer=issuer,
            subject=subject,
            claims=claims,
        )

    def test_no_active_identities(self) -> None:
        """No active external identities leave request unauthenticated."""
        assert isinstance(self.request, RequestSignonProtocol)
        self.assertEqual(self.request.signon.identities, {})
        self.assertFalse(self.request.user.is_authenticated)

        status = list(self.request.signon.status())
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0][0].name, "debsso")
        self.assertIsNone(status[0][1])
        self.assertEqual(status[1][0].name, "salsa")
        self.assertIsNone(status[1][1])

    def test_one_active_unbound_identity_autocreates(self) -> None:
        """One active but unbound identity tries to autocreate a user."""
        assert isinstance(self.request, RequestSignonProtocol)
        identity = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": True,
            }
        )
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(identity)

        self.assertEqual(
            self.request.signon.identities,
            {
                "salsa": identity,
            },
        )
        self.assertTrue(self.request.user.is_authenticated)

        identity.refresh_from_db()
        self.assertIsNotNone(identity.user)
        user = identity.user

        self.assertQuerySetEqual(user.identities.all(), [identity])
        self.assertEqual(user.username, "test@example.org")
        self.assertEqual(user.email, "test@example.org")
        self.assertEqual(user.first_name, "Test")
        self.assertEqual(user.last_name, "User")

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                f"{user}: auto created from identity {identity}",
                "INFO:debusine.server.signon:"
                f"{user}: bound to identity {identity}",
            ],
        )

        status = list(self.request.signon.status())
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0][0].name, "debsso")
        self.assertIsNone(status[0][1])
        self.assertEqual(status[1][0].name, "salsa")
        self.assertEqual(status[1][1], identity)

    def test_one_active_bound_identity_logs_in(self) -> None:
        """One active bound identity is enough to authenticate."""
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        identity = self._make_identity(user=user)
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(identity)

        self.assertEqual(
            self.request.signon.identities,
            {
                "salsa": identity,
            },
        )
        self.assertEqual(self.request.user, user)
        self.assertIsInstance(
            auth.load_backend(
                self.request.session.get(auth.BACKEND_SESSION_KEY)
            ),
            SignonAuthBackend,
        )

    def test_add_aligned_bound_identity(self) -> None:
        """Adding a bound matching identity raises PermissionDenied."""
        # Multiple active bound identities pointing to the same user
        # authenticate successfully
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident1 = self._make_identity(user=user, issuer="debsso")
        ident2 = self._make_identity(user=user, issuer="salsa")

        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        self.assertEqual(self.request.signon.identities, {"debsso": ident1})
        self.assertEqual(self.request.user, user)

        with self.assertRaises(PermissionDenied):
            self.request.signon.activate_identity(ident2)

        self.assertEqual(
            self.request.signon.identities,
            {
                "debsso": ident1,
            },
        )
        self.assertEqual(self.request.user, user)

    def test_add_unbound_identity(self) -> None:
        """Adding an unbound identity raises PermissionDenied."""
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        ident1 = self._make_identity(user=user, issuer="debsso")
        ident2 = self._make_identity(issuer="salsa")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        self.assertEqual(self.request.signon.identities, {"debsso": ident1})
        self.assertEqual(self.request.user, user)

        with self.assertRaises(PermissionDenied):
            self.request.signon.activate_identity(ident2)

        self.assertEqual(self.request.signon.identities, {"debsso": ident1})
        self.assertTrue(self.request.user.is_authenticated)
        self.assertEqual(self.request.user, user)

        ident1.refresh_from_db()
        self.assertEqual(ident1.user, user)
        ident2.refresh_from_db()
        self.assertIsNone(ident2.user)

    def test_add_conflicting_bound_identity(self) -> None:
        """Adding a bound conflicting identity raises PermissionDenied."""
        assert isinstance(self.request, RequestSignonProtocol)
        user1 = get_user_model().objects.create_user(
            "test1", email="test1@example.org"
        )
        user2 = get_user_model().objects.create_user(
            "test2", email="test2@example.org"
        )
        ident1 = self._make_identity(user=user1, issuer="debsso")
        ident2 = self._make_identity(user=user2, issuer="salsa")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        self.assertEqual(self.request.signon.identities, {"debsso": ident1})
        self.assertEqual(self.request.user, user1)

        with self.assertRaises(PermissionDenied):
            self.request.signon.activate_identity(ident2)

        self.assertEqual(self.request.signon.identities, {"debsso": ident1})
        self.assertTrue(self.request.user.is_authenticated)
        self.assertEqual(self.request.user, user1)

        ident1.refresh_from_db()
        self.assertEqual(ident1.user, user1)
        ident2.refresh_from_db()
        self.assertEqual(ident2.user, user2)

    def test_one_active_missing_identity(self) -> None:
        """One identity present in session but not the DB is removed."""
        assert isinstance(self.request, RequestSignonProtocol)
        self.request.session["signon_identity_salsa"] = 1
        self.request.signon._compute_identities()

        self.assertEqual(self.request.signon.identities, {})
        self.assertFalse(self.request.user.is_authenticated)

        self.assertIsNone(self.request.session.get("signon_identity_salsa"))

        status = list(self.request.signon.status())
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0][0].name, "debsso")
        self.assertIsNone(status[0][1])
        self.assertEqual(status[1][0].name, "salsa")
        self.assertIsNone(status[1][1])

    def test_bind_intention_succeeds(self) -> None:
        """Bind intention binds to current user."""
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        self.request.user = user

        ident = self._make_identity()
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident, "bind")

        self.assertEqual(self.request.signon.identities, {"salsa": ident})
        self.assertEqual(self.request.user, user)

        ident.refresh_from_db()
        self.assertEqual(ident.user, user)

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                f"{user}: auto associated to {ident}"
            ],
        )

    def test_bind_intention_succeeds_other_backend(self) -> None:
        """Bind intention binds to current user from a different backend."""
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")

        # Simulate a valid existing login from a different auth backend
        self.request.user = user
        self.request.session[auth.BACKEND_SESSION_KEY] = (
            "django.contrib.auth.backends.ModelBackend"
        )

        ident = self._make_identity()
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident, "bind")

        self.assertEqual(self.request.signon.identities, {"salsa": ident})
        self.assertEqual(self.request.user, user)

        ident.refresh_from_db()
        self.assertEqual(ident.user, user)

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                f"{user}: auto associated to {ident}",
            ],
        )

        ident.refresh_from_db()
        self.assertEqual(ident.user, user)

    def test_add_unbound_identities_binds(self) -> None:
        """One active bound identity and one unbound, when binding, binds."""
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        ident1 = self._make_identity(user=user, issuer="debsso")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        ident2 = self._make_identity(issuer="salsa")
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident2, "bind")

        self.assertEqual(
            self.request.signon.identities,
            {
                "debsso": ident1,
                "salsa": ident2,
            },
        )
        self.assertEqual(self.request.user, user)

        ident1.refresh_from_db()
        self.assertEqual(ident1.user, user)
        ident2.refresh_from_db()
        self.assertEqual(ident2.user, user)

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                f"{user}: auto associated to {ident2}",
            ],
        )

    def test_conflicting_bound_identities_bind(self) -> None:
        """Binding a second previously bound identity, rebinds."""
        assert isinstance(self.request, RequestSignonProtocol)
        user1 = get_user_model().objects.create_user(
            "test1", email="test1@example.org"
        )
        ident1 = self._make_identity(user=user1, issuer="debsso")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        user2 = get_user_model().objects.create_user(
            "test2", email="test2@example.org"
        )
        ident2 = self._make_identity(user=user2, issuer="salsa")
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident2, "bind")

        self.assertEqual(
            self.request.signon.identities,
            {
                "debsso": ident1,
                "salsa": ident2,
            },
        )
        self.assertEqual(self.request.user, user1)

        ident1.refresh_from_db()
        self.assertEqual(ident1.user, user1)
        ident2.refresh_from_db()
        self.assertEqual(ident2.user, user1)

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                f"{user1}: auto associated to {ident2}",
            ],
        )

    def test_logout_identities_logs_our_user_out(self) -> None:
        """logout_identities() deactivates identities."""
        # Log in using an external identity
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        ident1 = self._make_identity(user=user)
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)
        self.assertEqual(self.request.user, user)

        # Log out identities
        self.request.signon.logout_identities()
        self.assertEqual(self.request.signon.identities, {})

        # Since the user was logged in by external identities, it's now logged
        # out
        self.assertFalse(self.request.user.is_authenticated)

    def test_logout_identities_keep_user_from_other_backends(self) -> None:
        """logout_identities() deactivates identities."""
        # Log in using ModelBackend
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        self.request.user = user
        self.request.session[auth.BACKEND_SESSION_KEY] = (
            "django.contrib.auth.backends.ModelBackend"
        )

        # Also activate an identity mapped to user
        ident1 = self._make_identity(user=user)
        with self.assertLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1, "bind")
        self.assertEqual(self.request.user, user)

        # Log out identities
        self.request.signon.logout_identities()
        self.assertEqual(self.request.signon.identities, {})

        # Since the user was logged in by another auth backend, it's still
        # logged in
        self.assertTrue(self.request.user.is_authenticated)
        self.assertEqual(self.request.user, user)

    def test_map_user_email(self) -> None:
        """Map identity to user based on email."""
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": True,
            }
        )

        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident)

        ident.refresh_from_db()
        self.assertTrue(self.request.user.is_authenticated)
        self.assertEqual(ident.user, user)

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                f"{user}: user matched to identity {ident}",
                "INFO:debusine.server.signon:"
                f"{user}: bound to identity {ident}",
            ],
        )

    def test_map_user_unverified_email(self) -> None:
        """Do not map an identity with unverified email."""
        assert isinstance(self.request, RequestSignonProtocol)
        get_user_model().objects.create_user("test", email="test@example.org")
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
            }
        )

        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident)

        ident.refresh_from_db()
        self.assertFalse(self.request.user.is_authenticated)
        self.assertIsNone(ident.user)

        self.assertEqual(
            log.output,
            [
                "WARNING:debusine.server.signon:"
                f"identity {ident} does not have a verified email"
            ],
        )

    def test_map_user_invalid_email(self) -> None:
        """Test mapping an identity with an invalid email."""
        assert isinstance(self.request, RequestSignonProtocol)
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "Invalid Email In Claim" * 20,
                "email_verified": True,
            }
        )

        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident)

        ident.refresh_from_db()
        self.assertFalse(self.request.user.is_authenticated)
        self.assertIsNone(ident.user)

        self.assertRegex(
            log.output[0],
            r"(?s)WARNING:debusine\.server\.signon:"
            f"{re.escape(str(ident))}: cannot create a local user"
            r".+Enter a valid username"
            r".+Enter a valid email",
        )
        self.assertEqual(len(log.output), 1)

    def test_auto_create_user_fails(self) -> None:
        """Do not autocreate users with unverified emails."""
        assert isinstance(self.request, RequestSignonProtocol)
        ident = self._make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
            }
        )

        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident)

        ident.refresh_from_db()
        self.assertFalse(self.request.user.is_authenticated)
        self.assertIsNone(ident.user)

        self.assertEqual(
            log.output,
            [
                "WARNING:debusine.server.signon:"
                f"identity {ident} does not have a verified email"
            ],
        )

    def test_map_identity_invalid_provider(self) -> None:
        """An invalid provider in identity fails autocreation."""
        assert isinstance(self.request, RequestSignonProtocol)
        ident = self._make_identity(
            issuer="invalid",
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
                "groups_direct": ["debian"],
            },
        )

        with self.assertLogs("debusine.server.signon") as log:
            self.assertEqual(
                self.request.signon._map_identity_to_user(ident),
                None,
            )

        self.assertEqual(
            log.output,
            [
                "WARNING:debusine.server.signon:"
                f"identity {ident} has unknown issuer invalid"
            ],
        )
