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

import responses
from django.test import RequestFactory, override_settings
from rest_framework import status

from debusine.db.models import Group, Identity, Scope
from debusine.server.signon import providers
from debusine.server.signon.sites import DebianSignon
from debusine.server.signon.tests.test_signon import SignonTestCase


@contextlib.contextmanager
def nm_status(status: str | None = None) -> Generator[None, None, None]:
    """
    Mock fetch_nm_claims.

    :param status: status to return from nm, or None for empty claims
    """
    return_value = {}
    if status is not None:
        return_value["nm_status"] = status
    with mock.patch(
        "debusine.server.signon.sites.DebianSignon.fetch_nm_claims",
        return_value=return_value,
    ) as fetch_nm_claims:
        yield
    fetch_nm_claims.assert_called()


@contextlib.contextmanager
def nm_not_called() -> Generator[None, None, None]:
    """Ensure fetch_nm_claims is not called."""
    with mock.patch(
        "debusine.server.signon.sites.DebianSignon.fetch_nm_claims",
        side_effect=AssertionError("fetch_nm_claims unexpectedly called"),
    ):
        yield


class TestDebianSignon(SignonTestCase):
    """Test DebianSignon."""

    scope: ClassVar[Scope]
    debian_group: ClassVar[Group]
    dm_group: ClassVar[Group]
    elts_group: ClassVar[Group]
    identity: ClassVar[Identity]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        debian_scope = cls.playground.get_or_create_scope("debian")
        freexian_scope = cls.playground.get_or_create_scope("freexian")
        cls.scope = cls.playground.get_default_scope()
        cls.debian_group = cls.playground.create_group(
            name="Debian", scope=debian_scope
        )
        cls.dm_group = cls.playground.create_group(
            name="DM", scope=debian_scope
        )
        cls.elts_group = cls.playground.create_group(
            name="elts-contributors", scope=freexian_scope
        )
        cls.identity = Identity.objects.create(
            issuer="test",
            subject="123",
            claims={
                "sub": 123,
                "name": "Test User",
                "email": "test@debian.org",
                "email_verified": True,
            },
        )

    def setUp(self) -> None:
        """Provide a mock unauthenticated request for tests."""
        super().setUp()
        self.factory = RequestFactory()
        self.request = self.factory.get("/")

    def setup_provider(self, name: str, add_to_group: Any = None) -> None:
        self.provider = self.make_gitlab_provider(
            name=name, add_to_group=add_to_group
        )
        self.enterContext(override_settings(SIGNON_PROVIDERS=[self.provider]))
        self.identity.issuer = name
        self.identity.save()

    @nm_not_called()
    def test_validate_claims_non_gitlab(self) -> None:
        provider = providers.Provider(name="unknown", label="Unknown")
        self.enterContext(override_settings(SIGNON_PROVIDERS=[provider]))
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(provider, self.identity),
            [
                "unsupported validation of logins from 'unknown' provider:"
                " Provider is not supported"
            ],
        )

    @nm_not_called()
    def test_validate_claims_email_not_verified(self) -> None:
        self.identity.claims["email_verified"] = False
        self.identity.save()
        self.setup_provider("unknown")
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(self.provider, self.identity),
            [f"identity {self.identity} does not have a verified email"],
        )

    @nm_status()
    def test_validate_claims_salsa_not_dd(self) -> None:
        self.setup_provider("salsa")
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(self.provider, self.identity),
            ["identity salsa:123 is not DD or DM"],
        )
        self.identity.refresh_from_db()
        self.assertNotIn("nm_status", self.identity.claims)

    @nm_status("dd")
    def test_validate_claims_salsa_dd(self) -> None:
        self.identity.claims["groups_direct"] = ["debian"]
        self.identity.save()
        self.setup_provider("salsa")
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(self.provider, self.identity), []
        )
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.claims["nm_status"], "dd")

    @nm_status("dm")
    def test_validate_claims_salsa_dm(self) -> None:
        self.setup_provider("salsa")
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(self.provider, self.identity), []
        )
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.claims["nm_status"], "dm")

    @nm_status("dm_ga")
    def test_validate_claims_salsa_dm_ga(self) -> None:
        """Log in a DM with guest account."""
        self.setup_provider("salsa")
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(self.provider, self.identity), []
        )
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.claims["nm_status"], "dm_ga")

    @nm_status("dd_r")
    def test_validate_claims_salsa_dd_r(self) -> None:
        """Removed DDs are not allowed."""
        self.setup_provider("salsa")
        signon = DebianSignon(self.request)
        self.assertEqual(
            signon.validate_claims(self.provider, self.identity),
            ["identity salsa:123 is not DD or DM"],
        )
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.claims["nm_status"], "dd_r")

    @nm_not_called()
    def test_create_user_add_to_group_legacy_string(self) -> None:
        self.setup_provider("test", add_to_group="debian/Debian")
        signon = DebianSignon(self.request)
        salsa_groups: None | list[str]
        for salsa_groups in (
            None,
            [],
            ["debian"],
            ["freexian/teams/lts-contributors"],
            ["debian", "freexian/teams/lts-contributors"],
        ):
            with self.subTest(salsa_groups=salsa_groups):
                if salsa_groups is None:
                    self.identity.claims.pop("groups_direct", None)
                else:
                    self.identity.claims["groups_direct"] = salsa_groups
                self.identity.save()
                user = signon.create_user_from_identity(self.identity)
                assert user is not None
                try:
                    self.assertQuerySetEqual(
                        user.debusine_groups.all(),
                        [],
                    )
                finally:
                    self.identity.user = None
                    self.identity.save()
                    user.delete()

    @nm_not_called()
    def test_create_user_add_to_group(self) -> None:
        self.setup_provider(
            "test",
            add_to_group={
                "debian": "debian/Debian",
                "freexian/teams/lts-contributors": "freexian/elts-contributors",
                "nm:dd": "debian/Debian",
                "nm:dm": "debian/DM",
                "nm:dm_ga": "debian/DM",
            },
        )
        signon = DebianSignon(self.request)
        salsa_groups: None | list[str]
        for salsa_groups, nm_status, debusine_groups in (
            (None, None, []),
            ([], None, []),
            (["debian"], None, [self.debian_group]),
            (["freexian/teams/lts-contributors"], None, [self.elts_group]),
            (
                ["debian", "freexian/teams/lts-contributors"],
                None,
                [self.debian_group, self.elts_group],
            ),
            ([], "dd", [self.debian_group]),
            ([], "dm", [self.dm_group]),
            ([], "dm_ga", [self.dm_group]),
            ([], "dd_r", []),
            (["debian"], "dd", [self.debian_group]),
        ):
            with self.subTest(salsa_groups=salsa_groups, nm_status=nm_status):
                if salsa_groups is None:
                    self.identity.claims.pop("groups_direct", None)
                else:
                    self.identity.claims["groups_direct"] = salsa_groups
                if nm_status is not None:
                    self.identity.claims["nm_status"] = nm_status
                self.identity.save()
                user = signon.create_user_from_identity(self.identity)
                assert user is not None
                signon.add_user_to_groups(
                    user, self.identity, self.provider.options["add_to_group"]
                )
                try:
                    self.assertQuerySetEqual(
                        user.debusine_groups.all(),
                        debusine_groups,
                        ordered=False,
                    )
                finally:
                    self.identity.user = None
                    self.identity.save()
                    user.delete()

    @nm_not_called()
    def test_create_user_unknown_provider(self) -> None:
        provider = providers.Provider(name="unknown", label="Unknown")
        self.enterContext(override_settings(SIGNON_PROVIDERS=[provider]))
        signon = DebianSignon(self.request)
        user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])

    @nm_not_called()
    def test_create_user_salsa_not_dd(self) -> None:
        self.setup_provider("salsa")
        signon = DebianSignon(self.request)
        user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])

    @nm_not_called()
    def test_create_user_salsa_dd(self) -> None:
        self.identity.claims["groups_direct"] = ["debian"]
        self.identity.save()
        self.setup_provider("salsa", add_to_group={"debian": "debian/Debian"})
        signon = DebianSignon(self.request)
        user = signon.create_user_from_identity(self.identity)
        assert user is not None
        signon.add_user_to_groups(
            user, self.identity, self.provider.options["add_to_group"]
        )
        assert user is not None
        self.assertQuerySetEqual(
            user.debusine_groups.all(), [self.debian_group]
        )

    @nm_not_called()
    def test_create_user_gitlab_not_contributor(self) -> None:
        self.setup_provider("gitlab")
        signon = DebianSignon(self.request)
        user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])

    @nm_not_called()
    def test_create_user_no_user(self) -> None:
        """Test when Signon.create_user_from_identity fails."""
        with mock.patch(
            "debusine.server.signon.signon.Signon.create_user_from_identity",
            return_value=None,
        ):
            signon = DebianSignon(self.request)
            user = signon.create_user_from_identity(self.identity)
        self.assertIsNone(user)

    @nm_not_called()
    def test_create_user_no_provider(self) -> None:
        """Test when the identity has no provider."""
        with (
            mock.patch(
                "debusine.server.signon.signon"
                ".Signon.get_provider_for_identity",
                return_value=None,
            ),
        ):
            signon = DebianSignon(self.request)
            user = signon.create_user_from_identity(self.identity)
        assert user is not None
        self.assertQuerySetEqual(user.debusine_groups.all(), [])

    @responses.activate
    def test_fetch_nm_claims_user_not_found(self) -> None:
        responses.add(
            responses.GET,
            "https://nm.debian.org/api/salsa_status/123",
            json={},
            status=status.HTTP_404_NOT_FOUND,
        )
        signon = DebianSignon(self.request)
        with self.assertLogs("debusine.server.signon") as log:
            self.assertEqual(signon.fetch_nm_claims(123), {})

        self.assertEqual(
            log.output,
            [
                "INFO:debusine.server.signon:"
                "https://nm.debian.org/api/salsa_status/123:"
                " user is not known in nm.debian.org"
            ],
        )

    @responses.activate
    def test_fetch_nm_claims_missing_status(self) -> None:
        responses.add(
            responses.GET,
            "https://nm.debian.org/api/salsa_status/123",
            json={},
        )
        signon = DebianSignon(self.request)
        with self.assertLogs("debusine.server.signon") as log:
            self.assertEqual(signon.fetch_nm_claims(123), {})

        self.assertEqual(
            log.output,
            [
                "WARNING:debusine.server.signon:"
                "https://nm.debian.org/api/salsa_status/123:"
                " user record has no 'status' information"
            ],
        )

    @responses.activate
    def test_fetch_nm_claims_error(self) -> None:
        responses.add(
            responses.GET,
            "https://nm.debian.org/api/salsa_status/123",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        signon = DebianSignon(self.request)
        with self.assertLogs("debusine.server.signon") as log:
            self.assertEqual(signon.fetch_nm_claims(123), {})

        self.assertEqual(
            log.output,
            [
                "WARNING:debusine.server.signon:"
                "https://nm.debian.org/api/salsa_status/123:"
                " cannot fetch user information: 500 (Internal Server Error)"
            ],
        )

    @responses.activate
    def test_fetch_nm_claims(self) -> None:
        responses.add(
            responses.GET,
            "https://nm.debian.org/api/salsa_status/123",
            json={"status": "dm"},
        )
        signon = DebianSignon(self.request)
        with self.assertNoLogs("debusine.server.signon"):
            self.assertEqual(signon.fetch_nm_claims(123), {"nm_status": "dm"})
