# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test authentication for package archives."""

import base64
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.test import RequestFactory, override_settings
from django.utils import timezone
from rest_framework import HTTP_HEADER_ENCODING
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from debusine.db.models import Token, User
from debusine.test.django import TestCase
from debusine.web.archives.auth import ArchiveAuthentication


@override_settings(LANGUAGE_CODE="en-us")
class ArchiveAuthenticationTests(TestCase):
    """Test ArchiveAuthentication."""

    def authenticate(
        self,
        *,
        host: str | None = None,
        secure: bool = True,
        username: str | None = None,
        password: Token | str | None = None,
        authorization: str | None = None,
    ) -> tuple[User, None] | None:
        """Run the authentication method."""
        kwargs: dict[str, Any] = {
            "secure": secure,
            "HTTP_HOST": host or settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN,
        }
        if username is not None and password is not None:
            assert authorization is None
            if isinstance(password, Token):
                password = password.key
            credentials = f"{username}:{password}"
            base64_credentials = base64.b64encode(
                credentials.encode(HTTP_HEADER_ENCODING)
            ).decode(HTTP_HEADER_ENCODING)
            kwargs["HTTP_AUTHORIZATION"] = f"Basic {base64_credentials}"
        elif authorization is not None:
            kwargs["HTTP_AUTHORIZATION"] = authorization
        request = Request(RequestFactory().get("/", **kwargs))
        return ArchiveAuthentication().authenticate(request)

    def test_not_debian_archive_fqdn(self) -> None:
        token = self.playground.create_user_token()
        self.assertIsNone(
            self.authenticate(
                host=settings.DEBUSINE_FQDN,
                username=self.playground.default_username,
                password=token,
            )
        )

    @override_settings(
        ALLOWED_HOSTS=["*"],
        DEBUSINE_DEBIAN_ARCHIVE_FQDN=["deb.example.com", "deb.example.org"],
    )
    def test_not_any_of_debian_archive_fqdn(self) -> None:
        token = self.playground.create_user_token()
        self.assertIsNone(
            self.authenticate(
                host="deb.example.net",
                username=self.playground.default_username,
                password=token,
            )
        )

    def test_no_authorization_header(self) -> None:
        self.assertIsNone(self.authenticate())

    def test_authorization_not_basic(self) -> None:
        self.assertIsNone(self.authenticate(authorization="Token foo"))

    def test_no_basic_credentials(self) -> None:
        with self.assertRaisesRegex(
            AuthenticationFailed, "No credentials provided"
        ):
            self.authenticate(authorization="Basic")

    def test_basic_credentials_containing_spaces(self) -> None:
        with self.assertRaisesRegex(
            AuthenticationFailed, "Credentials string should not contain spaces"
        ):
            self.authenticate(authorization="Basic foo bar")

    def test_bad_base64_encoding(self) -> None:
        with self.assertRaisesRegex(
            AuthenticationFailed, "Credentials not correctly base64 encoded"
        ):
            self.authenticate(authorization="Basic foo")

    def test_not_https(self) -> None:
        with self.assertRaisesRegex(
            AuthenticationFailed,
            r"Authenticated access is only allowed using HTTPS\.",
        ):
            self.authenticate(
                username=self.playground.default_username,
                password="test",
                secure=False,
            )

    def test_nonexistent_token(self) -> None:
        with self.assertRaisesRegex(AuthenticationFailed, r"Invalid token\."):
            self.authenticate(
                username=self.playground.default_username, password="test"
            )

    def test_bare_token(self) -> None:
        token = self.playground.create_bare_token()
        with self.assertRaisesRegex(AuthenticationFailed, r"Invalid token\."):
            self.authenticate(
                username=self.playground.default_username, password=token
            )

    def test_worker_token(self) -> None:
        token = self.playground.create_worker_token()
        with self.assertRaisesRegex(AuthenticationFailed, r"Invalid token\."):
            self.authenticate(
                username=self.playground.default_username, password=token
            )

    def test_wrong_user(self) -> None:
        user = self.playground.create_user("test")
        token = self.playground.create_user_token(user=user)
        with self.assertRaisesRegex(AuthenticationFailed, r"Invalid token\."):
            self.authenticate(
                username=self.playground.default_username, password=token
            )

    def test_expired_user_token(self) -> None:
        token = self.playground.create_user_token(
            expire_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaisesRegex(AuthenticationFailed, r"Invalid token\."):
            self.authenticate(
                username=self.playground.default_username, password=token
            )

    def test_valid_user_token(self) -> None:
        token = self.playground.create_user_token()
        self.assertEqual(
            self.authenticate(
                username=self.playground.default_username, password=token
            ),
            (token.user, None),
        )

    @override_settings(
        ALLOWED_HOSTS=["*"],
        DEBUSINE_DEBIAN_ARCHIVE_FQDN=["deb.example.com", "deb.example.org"],
    )
    def test_any_of_debian_archive_fqdn(self) -> None:
        token = self.playground.create_user_token()
        self.assertEqual(
            self.authenticate(
                host="deb.example.org",
                username=self.playground.default_username,
                password=token,
            ),
            (token.user, None),
        )
