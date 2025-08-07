# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the enroll views."""

import datetime
import secrets
from datetime import timezone as tz

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import Client
from django.urls import reverse
from rest_framework import status

from debusine.client.models import (
    EnrollConfirmPayload,
    EnrollOutcome,
    EnrollPayload,
)
from debusine.db.models import Token
from debusine.db.models.auth import ClientEnroll
from debusine.db.playground import scenarios
from debusine.test.django import TestCase
from debusine.web.views.tests.utils import ViewTestMixin


class ConfirmViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`ConfirmView`."""

    scenario = scenarios.DefaultScopeUser()
    payload: EnrollPayload
    url: str
    xpath_form = "//form[@id='confirm-form']"
    xpath_bad_scope = "//div[@id='bad-scope']"
    xpath_bad_payload = "//div[@id='bad-payload']"

    def setUp(self) -> None:
        """Set up common data."""
        super().setUp()
        # This is done in setUp to use a different nonce per test, making sure
        # that each test uses a different channel
        self.payload = EnrollPayload(
            nonce=secrets.token_urlsafe(8),
            challenge="correct horse battery staple",
            hostname="hostname",
            scope=self.scenario.scope.name,
        )
        self.url = reverse(
            "enroll:confirm", kwargs={"nonce": self.payload.nonce}
        )

    def test_GET_anonymous(self) -> None:
        """Anonymous GET redirects to login."""
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("login") + "?next=" + self.url)

    def test_POST_anonymous(self) -> None:
        """Anonymous GET redirects to login."""
        response = self.client.post(self.url, data=self.payload.dict())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_GET_missing_nonce(self) -> None:
        """Invalid nonces on GET return 404."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_POST_missing_nonce(self) -> None:
        """Invalid nonces on POST return 404."""
        self.client.force_login(self.scenario.user)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_GET_bad_payload(self) -> None:
        """Invalid payload in database on GET."""
        ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload={"mischief": True}
        )
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.url)
        tree = self.assertResponseHTML(response)
        msg = self.assertHasElement(tree, self.xpath_bad_payload)
        self.assertTextContentEqual(
            msg,
            "The data filed for this enrollment seem to be corrupted:"
            " please retry the procedure with debusine setup.",
        )
        self.assertFalse(tree.xpath(self.xpath_form))

    def test_POST_bad_payload(self) -> None:
        """Invalid payload in database on POST."""
        ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload={"mischief": True}
        )
        self.client.force_login(self.scenario.user)
        response = self.client.post(self.url)
        self.assertEqual(
            response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        self.assertEqual(response.content, b"Invalid payload in database")

    def test_GET_bad_scope(self) -> None:
        """Invalid payload in database on GET."""
        self.payload.scope = "does-not-exist"
        ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload=self.payload.dict()
        )
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.url)
        tree = self.assertResponseHTML(response)
        msg = self.assertHasElement(tree, self.xpath_bad_scope)
        self.assertTextContentEqual(
            msg,
            "The scope does-not-exist is not present"
            " in this Debusine instance.",
        )
        self.assertHasElement(tree, self.xpath_form)

    def test_POST_csrf(self) -> None:
        """POST enforces CSRF."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.scenario.user)
        response = client.post(self.url)
        self.assertContains(
            response,
            "CSRF verification failed.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def test_POST_bad_outcome(self) -> None:
        """Invalid outcome in POST data."""
        ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload=self.payload.dict()
        )
        self.client.force_login(self.scenario.user)
        response = self.client.post(self.url, data={"outcome": "mischief"})
        self.assertEqual(
            response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        self.assertEqual(response.content, b"Invalid outcome in POST data")

    def test_valid_GET(self) -> None:
        """Test a valid GET."""
        ce = ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload=self.payload.dict()
        )
        ce.created_at = datetime.datetime(2025, 1, 1, tzinfo=tz.utc)
        ce.save()

        self.client.force_login(self.scenario.user)
        response = self.client.get(self.url)
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath(self.xpath_bad_payload))
        self.assertFalse(tree.xpath(self.xpath_bad_scope))
        p_created_at = self.assertHasElement(tree, "//p[@id='created-at']")
        self.assertRegex(
            self.get_node_text_normalized(p_created_at),
            r"The request was made at 2025-01-01 00:00 \(.+ ago\)"
            f" from hostname for scope {self.scenario.scope.name}.",
        )
        p_challenge = self.assertHasElement(tree, "//p[@id='challenge']")
        self.assertTextContentEqual(
            p_challenge,
            "Please check that the challenge words in debusine setup's"
            f" output are: {self.payload.challenge}.",
        )
        self.assertHasElement(tree, self.xpath_form)

    def test_valid_POST_confirm(self) -> None:
        """Test a valid POST with the confirm button."""
        ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload=self.payload.dict()
        )
        self.client.force_login(self.scenario.user)
        response = self.client.post(self.url, data={"outcome": "confirm"})

        # POST redirects to token list
        self.assertRedirects(
            response,
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            ),
        )

        # A confirmation message is sent via channels
        channel_layer = get_channel_layer()
        channel_name = f"enroll.{self.payload.nonce}"
        msg = async_to_sync(channel_layer.receive)(channel_name)
        confirm_payload = EnrollConfirmPayload.parse_obj(msg)

        self.assertEqual(confirm_payload.outcome, EnrollOutcome.CONFIRM)
        self.assertIsNotNone(confirm_payload.token)

        # Token is in the database
        assert confirm_payload.token is not None
        token = Token.objects.get(
            hash=Token._generate_hash(confirm_payload.token)
        )
        self.assertEqual(
            token.comment, "obtained via 'debusine setup' on hostname"
        )

    def test_valid_POST_cancel(self) -> None:
        """Test a valid POST with the cancel button."""
        initial_token_count = Token.objects.count()
        ClientEnroll.objects.create(
            nonce=self.payload.nonce, payload=self.payload.dict()
        )
        self.client.force_login(self.scenario.user)
        response = self.client.post(self.url, data={"outcome": "cancel"})

        # POST redirects to token list
        self.assertRedirects(
            response,
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            ),
        )

        # A confirmation message is sent via channels
        channel_layer = get_channel_layer()
        channel_name = f"enroll.{self.payload.nonce}"
        msg = async_to_sync(channel_layer.receive)(channel_name)
        confirm_payload = EnrollConfirmPayload.parse_obj(msg)

        self.assertEqual(confirm_payload.outcome, EnrollOutcome.CANCEL)
        self.assertIsNone(confirm_payload.token)

        # No token was created
        self.assertEqual(Token.objects.count(), initial_token_count)
