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
from collections.abc import Collection
from typing import Any
from unittest import mock

import django.contrib.sessions.backends.base
from django.contrib import auth
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import RequestFactory, override_settings

from debusine.db.models import Identity, User
from debusine.server.signon import providers
from debusine.server.signon.auth import SignonAuthBackend
from debusine.server.signon.middleware import RequestSignonProtocol
from debusine.server.signon.signon import MapIdentityFailed, Signon
from debusine.test.django import TestCase


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


class SignonTestCase(TestCase):
    """Base class for testing Signon and its subclasses."""

    signon_class: type[Signon] = Signon

    def setUp(self) -> None:
        """Provide a mock unauthenticated request for tests."""
        super().setUp()
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.session = MockSession()
        self.request.user = AnonymousUser()
        setattr(self.request, "_messages", FallbackStorage(self.request))
        self.providers: list[providers.Provider] = []
        self.enterContext(override_settings(SIGNON_PROVIDERS=self.providers))
        setattr(self.request, "signon", self.signon_class(self.request))

    def add_generic_provider(self, name: str = "generic") -> providers.Provider:
        """Add a generic provider to the provider configuration."""
        provider = providers.Provider(name=name, label=name.capitalize())
        self.providers.append(provider)
        return provider

    def make_gitlab_provider(
        self,
        name: str = "gitlab",
        restrict: Collection[str] | None = None,
        add_to_group: Any = None,
    ) -> providers.GitlabProvider:
        """Create a GitLab provider."""
        kwargs: dict[str, Any] = {}
        if restrict is not None:
            kwargs["restrict"] = restrict
        provider = providers.GitlabProvider(
            name=name,
            label=name.capitalize(),
            icon="signon/gitlabian.svg",
            client_id="123client_id",
            client_secret="123client_secret",
            url="https://salsa.debian.org",
            scope=("openid", "profile", "email"),
            **kwargs,
        )
        if add_to_group is not None:
            provider.options["add_to_group"] = add_to_group
        return provider

    def add_gitlab_provider(
        self,
        name: str = "gitlab",
        restrict: Collection[str] | None = None,
        add_to_group: Any = None,
    ) -> providers.GitlabProvider:
        """Add a GitLab provider to the provider configuration."""
        provider = self.make_gitlab_provider(
            name=name, restrict=restrict, add_to_group=add_to_group
        )
        self.providers.append(provider)
        return provider

    def add_salsa_provider(
        self, restrict: Collection[str] | None = None, add_to_group: Any = None
    ) -> providers.GitlabProvider:
        """Add a Salsa provider to the provider configuration."""
        provider = self.make_gitlab_provider(
            name="salsa", restrict=restrict, add_to_group=add_to_group
        )
        self.providers.append(provider)
        return provider

    def make_identity(
        self,
        user: User | None = None,
        issuer: str = "salsa",
        subject: str = "123",
        claims: dict[str, Any] | None = None,
    ) -> Identity:
        """Create a test identity."""
        if claims is None:
            claims = {}
        return Identity.objects.create(
            user=user,
            issuer=issuer,
            subject=subject,
            claims=claims,
        )


class SignonTests(SignonTestCase):
    """Test Signon."""

    def assertRestrictValidates(
        self, identity: Identity, restrict: Collection[str]
    ) -> None:
        """Identity passes this restrict rule."""
        assert isinstance(self.request, RequestSignonProtocol)
        provider = self.make_gitlab_provider(name=identity.issuer)
        provider.restrict = restrict
        self.assertEqual(
            self.request.signon.validate_claims(provider, identity), []
        )

    def assertRestrictDoesNotValidate(
        self,
        identity: Identity,
        restrict: Collection[str],
        *regexps: str,
    ) -> None:
        """Identity does not pass this restrict rule."""
        assert isinstance(self.request, RequestSignonProtocol)
        provider = self.make_gitlab_provider(name=identity.issuer)
        provider.restrict = restrict
        errors = self.request.signon.validate_claims(provider, identity)
        self.assertNotEqual(errors, [])
        for regexp in regexps:
            for idx, error in enumerate(errors):
                if re.search(regexp, error):  # pragma: no cover
                    errors.pop(idx)
                    break
            else:
                self.fail(
                    f"error list {errors!r} did not match {regexp!r}"
                )  # pragma: no cover
        if errors:
            self.fail(f"unexpected errors: {errors!r}")  # pragma: no cover

    def test_no_active_identities(self) -> None:
        """No active external identities leave request unauthenticated."""
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        self.assertEqual(self.request.signon.identities, {})
        self.assertFalse(self.request.user.is_authenticated)

        status = list(self.request.signon.status())
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0][0].name, "generic")
        self.assertIsNone(status[0][1])
        self.assertEqual(status[1][0].name, "salsa")
        self.assertIsNone(status[1][1])

    def test_one_active_unbound_identity_autocreates(self) -> None:
        """One active but unbound identity tries to autocreate a user."""
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        identity = self.make_identity(
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
        user = identity.user
        assert user is not None

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
        self.assertEqual(status[0][0].name, "generic")
        self.assertIsNone(status[0][1])
        self.assertEqual(status[1][0].name, "salsa")
        self.assertEqual(status[1][1], identity)

    def test_one_active_bound_identity_logs_in(self) -> None:
        """One active bound identity is enough to authenticate."""
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        identity = self.make_identity(user=user)
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(identity)

        self.assertEqual(
            self.request.signon.identities,
            {
                "salsa": identity,
            },
        )
        self.assertEqual(self.request.user, user)
        backend_path = self.request.session.get(auth.BACKEND_SESSION_KEY)
        assert backend_path is not None
        self.assertIsInstance(
            auth.load_backend(backend_path), SignonAuthBackend
        )

    def test_add_aligned_bound_identity(self) -> None:
        """Adding a bound matching identity raises PermissionDenied."""
        self.add_generic_provider()
        self.add_salsa_provider()
        # Multiple active bound identities pointing to the same user
        # authenticate successfully
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident1 = self.make_identity(user=user, issuer="generic")
        ident2 = self.make_identity(user=user, issuer="salsa")

        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        self.assertEqual(self.request.signon.identities, {"generic": ident1})
        self.assertEqual(self.request.user, user)

        with self.assertRaises(PermissionDenied):
            self.request.signon.activate_identity(ident2)

        self.assertEqual(
            self.request.signon.identities,
            {
                "generic": ident1,
            },
        )
        self.assertEqual(self.request.user, user)

    def test_add_unbound_identity(self) -> None:
        """Adding an unbound identity raises PermissionDenied."""
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        ident1 = self.make_identity(user=user, issuer="generic")
        ident2 = self.make_identity(issuer="salsa")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        self.assertEqual(self.request.signon.identities, {"generic": ident1})
        self.assertEqual(self.request.user, user)

        with self.assertRaises(PermissionDenied):
            self.request.signon.activate_identity(ident2)

        self.assertEqual(self.request.signon.identities, {"generic": ident1})
        self.assertTrue(self.request.user.is_authenticated)
        self.assertEqual(self.request.user, user)

        ident1.refresh_from_db()
        self.assertEqual(ident1.user, user)
        ident2.refresh_from_db()
        self.assertIsNone(ident2.user)

    def test_add_conflicting_bound_identity(self) -> None:
        """Adding a bound conflicting identity raises PermissionDenied."""
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user1 = get_user_model().objects.create_user(
            "test1", email="test1@example.org"
        )
        user2 = get_user_model().objects.create_user(
            "test2", email="test2@example.org"
        )
        ident1 = self.make_identity(user=user1, issuer="generic")
        ident2 = self.make_identity(user=user2, issuer="salsa")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        self.assertEqual(self.request.signon.identities, {"generic": ident1})
        self.assertEqual(self.request.user, user1)

        with self.assertRaises(PermissionDenied):
            self.request.signon.activate_identity(ident2)

        self.assertEqual(self.request.signon.identities, {"generic": ident1})
        self.assertTrue(self.request.user.is_authenticated)
        self.assertEqual(self.request.user, user1)

        ident1.refresh_from_db()
        self.assertEqual(ident1.user, user1)
        ident2.refresh_from_db()
        self.assertEqual(ident2.user, user2)

    def test_one_active_missing_identity(self) -> None:
        """One identity present in session but not the DB is removed."""
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        self.request.session["signon_identity_salsa"] = 1
        self.request.signon._compute_identities()

        self.assertEqual(self.request.signon.identities, {})
        self.assertFalse(self.request.user.is_authenticated)

        self.assertIsNone(self.request.session.get("signon_identity_salsa"))

        status = list(self.request.signon.status())
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0][0].name, "generic")
        self.assertIsNone(status[0][1])
        self.assertEqual(status[1][0].name, "salsa")
        self.assertIsNone(status[1][1])

    def test_bind_intention_succeeds(self) -> None:
        """Bind intention binds to current user."""
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        self.request.user = user

        ident = self.make_identity()
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
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")

        # Simulate a valid existing login from a different auth backend
        self.request.user = user
        self.request.session[auth.BACKEND_SESSION_KEY] = (
            "django.contrib.auth.backends.ModelBackend"
        )

        ident = self.make_identity()
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
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user("test")
        ident1 = self.make_identity(user=user, issuer="generic")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        ident2 = self.make_identity(issuer="salsa")
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident2, "bind")

        self.assertEqual(
            self.request.signon.identities,
            {
                "generic": ident1,
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
        self.add_generic_provider()
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user1 = get_user_model().objects.create_user(
            "test1", email="test1@example.org"
        )
        ident1 = self.make_identity(user=user1, issuer="generic")
        with self.assertNoLogs("debusine.server.signon"):
            self.request.signon.activate_identity(ident1)

        user2 = get_user_model().objects.create_user(
            "test2", email="test2@example.org"
        )
        ident2 = self.make_identity(user=user2, issuer="salsa")
        with self.assertLogs("debusine.server.signon") as log:
            self.request.signon.activate_identity(ident2, "bind")

        self.assertEqual(
            self.request.signon.identities,
            {
                "generic": ident1,
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
        ident1 = self.make_identity(user=user)
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
        ident1 = self.make_identity(user=user)
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
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        user = get_user_model().objects.create_user(
            "test", email="test@example.org"
        )
        ident = self.make_identity(
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
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        get_user_model().objects.create_user("test", email="test@example.org")
        ident = self.make_identity(
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
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        ident = self.make_identity(
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
        self.add_salsa_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        ident = self.make_identity(
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
        ident = self.make_identity(
            issuer="invalid",
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
                "groups_direct": ["debian"],
            },
        )

        with (
            self.assertLogs("debusine.server.signon") as log,
            self.assertRaisesRegex(
                MapIdentityFailed, r"Invalid provider 'invalid' in identity"
            ),
        ):
            self.request.signon.identity_create_user(ident)

        self.assertEqual(
            log.output,
            [
                "WARNING:debusine.server.signon:"
                f"identity {ident} has unknown issuer invalid"
            ],
        )

    def test_validate_not_oidc(self) -> None:
        self.add_generic_provider()
        assert isinstance(self.request, RequestSignonProtocol)
        identity = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
            },
            issuer="generic",
        )
        provider = self.request.signon.get_provider_for_identity(identity)
        assert provider is not None
        self.assertEqual(
            self.request.signon.validate_claims(provider, identity), []
        )

    def test_validate_not_gitlab(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        url = "https://salsa.debian.org"
        provider = providers.OIDCProvider(
            name="oidc",
            label="OIDC",
            icon="signon/gitlabian.svg",
            client_id="123client_id",
            client_secret="123client_secret",
            url_issuer=url,
            url_authorize=f"{url}/oauth/authorize",
            url_token=f"{url}/oauth/token",
            url_userinfo=f"{url}/oauth/userinfo",
            url_jwks=f"{url}/oauth/discovery/keys",
            scope=("openid", "profile", "email"),
        )
        self.enterContext(override_settings(SIGNON_PROVIDERS=[provider]))
        identity = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
            },
            issuer="oidc",
        )
        self.assertEqual(
            self.request.signon.validate_claims(provider, identity), []
        )

    def test_validate_email_verified(self) -> None:
        """email-verified validates as intended."""
        ident = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
            }
        )
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["email-verified"], "does not have a verified email"
        )

        ident.claims["email_verified"] = True
        self.assertRestrictValidates(ident, [])
        self.assertRestrictValidates(ident, ["email-verified"])

    def test_validate_group(self) -> None:
        """group:* validates as intended."""
        ident = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "groups_direct": ["debian"],
            }
        )
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["group:admin"], "not in group 'admin'"
        )
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictDoesNotValidate(
            ident, ["group:debian", "group:admin"], "not in group 'admin'"
        )

        ident.claims["groups_direct"] = []
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["group:admin"], "not in group 'admin'"
        )
        self.assertRestrictDoesNotValidate(
            ident,
            ["group:debian", "group:admin"],
            "not in group 'debian'",
            "not in group 'admin'",
        )

        ident.claims["groups_direct"] = ["debian", "admin"]
        self.assertRestrictValidates(ident, [])
        self.assertRestrictValidates(ident, ["group:admin"])
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictValidates(ident, ["group:debian", "group:admin"])

    def test_validate_combine(self) -> None:
        """group:* and email-verified can be used together."""
        ident = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@example.org",
                "email_verified": False,
                "groups_direct": ["debian"],
            }
        )
        self.assertRestrictValidates(ident, [])
        self.assertRestrictDoesNotValidate(
            ident, ["email-verified"], "does not have a verified email"
        )
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictDoesNotValidate(
            ident,
            ["email-verified", "group:debian"],
            "does not have a verified email",
        )

        ident.claims["email_verified"] = True
        self.assertRestrictValidates(ident, [])
        self.assertRestrictValidates(ident, ["email-verified"])
        self.assertRestrictValidates(ident, ["group:debian"])
        self.assertRestrictValidates(ident, ["email-verified", "group:debian"])
        self.assertRestrictDoesNotValidate(
            ident, ["email-verified", "group:admin"], "not in group 'admin'"
        )

    def test_validate_invalid_expression(self) -> None:
        """An invalid restrict value raises ImproperlyConfigured."""
        assert isinstance(self.request, RequestSignonProtocol)
        provider = self.make_gitlab_provider(restrict=["invalid"])
        identity = self.make_identity()
        with self.assertRaises(ImproperlyConfigured):
            self.request.signon.validate_claims(provider, identity)

    def test_validate_claims_gitlab_not_contributor(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        provider = self.add_gitlab_provider(
            restrict=["group:freexian/teams/lts-contributors"]
        )
        identity = self.make_identity(issuer="gitlab")
        self.assertEqual(
            self.request.signon.validate_claims(provider, identity),
            [
                "identity gitlab:123 is not in group"
                " 'freexian/teams/lts-contributors'"
            ],
        )

    def test_validate_claims_gitlab_contributor(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        provider = self.add_gitlab_provider(
            restrict=["group:freexian/teams/lts-contributors"]
        )
        identity = self.make_identity(issuer="gitlab")
        identity.claims["groups_direct"] = ["freexian/teams/lts-contributors"]
        identity.save()
        self.assertEqual(
            self.request.signon.validate_claims(provider, identity),
            [],
        )

    def test_identity_create_user_unknown_provider(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        identity = self.make_identity(issuer="unknown")
        with self.assertRaisesRegex(
            MapIdentityFailed, r"Invalid provider 'unknown' in identity"
        ):
            self.request.signon.identity_create_user(identity)

    def test_create_user_unsupported_provider(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        identity = self.make_identity(
            issuer="nonexisting",
            claims={
                "name": "Test User",
                "email": "test@debian.org",
                "email_verified": True,
            },
        )
        user = self.request.signon.create_user_from_identity(identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])

    def test_setup_new_user_missing_group(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        self.add_salsa_provider(add_to_group={"debian": "debusine/missing"})
        identity = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@debian.org",
                "email_verified": True,
                "groups_direct": ["debian"],
            }
        )
        user = self.request.signon.create_user_from_identity(identity)
        assert user is not None
        with self.assertRaisesRegex(
            ImproperlyConfigured, "Group 'debusine/missing' not found"
        ):
            self.request.signon.setup_new_user(user, identity)

    def test_setup_new_user_missing_provider(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        identity = self.make_identity(
            claims={
                "name": "Test User",
                "email": "test@debian.org",
                "email_verified": True,
                "groups_direct": ["debian"],
            }
        )
        user = self.request.signon.create_user_from_identity(identity)
        assert user is not None
        with mock.patch(
            "debusine.server.signon.signon.Signon.add_user_to_groups"
        ) as add_user_to_groups:
            self.request.signon.setup_new_user(user, identity)
        add_user_to_groups.assert_not_called()

    def test_setup_new_user_add_to_group(self) -> None:
        assert isinstance(self.request, RequestSignonProtocol)
        debian_scope = self.playground.get_or_create_scope("debian")
        freexian_scope = self.playground.get_or_create_scope("freexian")
        debian_group = self.playground.create_group(
            name="Debian", scope=debian_scope
        )
        elts_group = self.playground.create_group(
            name="elts-contributors", scope=freexian_scope
        )
        groups = [debian_group, elts_group]

        user = get_user_model().objects.create_user("test")
        identity = self.make_identity(
            issuer="salsa",
            claims={
                "name": "Test User",
                "email": "test@debian.org",
                "email_verified": True,
                "groups_direct": ["debian"],
            },
        )
        self.add_gitlab_provider()
        add_to_group = {
            "debian": "debian/Debian",
            "freexian/teams/lts-contributors": "freexian/elts-contributors",
            "nm:dd": "debian/Debian",
            "nm:dm": "debian/DM",
            "nm:dm_ga": "debian/DM",
        }
        signon = self.request.signon
        salsa_groups: None | list[str]
        for salsa_groups, debusine_groups in (
            (None, []),
            ([], []),
            (["debian"], [debian_group]),
            (["freexian/teams/lts-contributors"], [elts_group]),
            (
                ["debian", "freexian/teams/lts-contributors"],
                [debian_group, elts_group],
            ),
        ):
            with self.subTest(salsa_groups=salsa_groups):
                if salsa_groups is None:
                    identity.claims.pop("groups_direct", None)
                else:
                    identity.claims["groups_direct"] = salsa_groups
                identity.save()
                for group in groups:
                    group.users.remove(user)
                signon.add_user_to_groups(user, identity, add_to_group)
                try:
                    self.assertQuerySetEqual(
                        user.debusine_groups.all(),
                        debusine_groups,
                        ordered=False,
                    )
                finally:
                    identity.user = None
                    identity.save()
