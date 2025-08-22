# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the enroll views."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Generator
from typing import Any, ClassVar, Never, Union
from unittest import mock

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction
from django.http import StreamingHttpResponse
from django.test import AsyncClient, Client
from django.urls import reverse
from rest_framework import status

from debusine.client.models import (
    EnrollConfirmPayload,
    EnrollOutcome,
    EnrollPayload,
)
from debusine.db.models.auth import ClientEnroll
from debusine.test.django import TestCase, TestResponseType


class EnrollViewTests(TestCase):
    """Tests for :py:class:`EnrollView`."""

    payload: ClassVar[EnrollPayload]
    url: ClassVar[str]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data."""
        super().setUpTestData()
        cls.payload = EnrollPayload(
            nonce="12345678",
            challenge="correct horse battery staple",
            hostname="hostname",
            scope="scope",
        )
        cls.url = reverse("api:enroll")

    @contextlib.contextmanager
    def assertValidationSuccessful(self) -> Generator[None, None, None]:
        """Ensure EnrollView passes validation."""

        class Reached(BaseException):
            """Thrown when the target code is reached."""

        with (
            mock.patch(
                "debusine.server.views.enroll.EnrollView.long_poll",
                side_effect=Reached,
            ),
            self.assertRaises(Reached),
        ):
            yield

    async def assertResponsePayload(
        self, response: StreamingHttpResponse
    ) -> EnrollConfirmPayload:
        """Consume streaming content and return the parsed payload."""
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Django's typing currently do not seem to contemplate async streaming
        # content
        assert isinstance(response.streaming_content, AsyncIterator)
        chunks = [x async for x in response.streaming_content]
        return EnrollConfirmPayload.parse_obj(json.loads(b"".join(chunks)))

    def _post(
        self,
        payload: EnrollPayload | dict[str, Any] | str | None,
        content_type: str = "application/json",
    ) -> "Union[TestResponseType, StreamingHttpResponse]":
        match payload:
            case EnrollPayload():
                return self.client.post(
                    self.url, data=payload.dict(), content_type=content_type
                )
            case dict():
                return self.client.post(
                    self.url, data=payload, content_type=content_type
                )
            case "":
                # Django's test client does 'if data' instead of 'if data is
                # None', so we need to special-case sending an empty payload
                return self.client.post(
                    self.url,
                    data=b"",
                    content_type=content_type,
                    headers={
                        "Content-Length": "0",
                        "Content-Type": content_type,
                    },
                )
            case _:
                return self.client.post(
                    self.url, data=payload, content_type=content_type
                )

    async def _apost(self, payload: EnrollPayload) -> StreamingHttpResponse:
        """
        POST the payload, and wait until the view waits for confirmation.

        :returns: the response task, paused at ``get_channel_layer``.
        """
        client = AsyncClient()
        res = await client.post(
            self.url,
            data=payload.dict(),
            content_type="application/json",
        )
        assert isinstance(res, StreamingHttpResponse)
        return res

    def test_invalid_methods(self) -> None:
        """Only POST is accepted."""
        for method in "get", "put", "patch", "delete", "head":
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url)
                self.assertEqual(
                    response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
                )

    def test_unauthenticated(self) -> None:
        """Authentication is not needed."""
        with self.assertValidationSuccessful():
            self._post(self.payload)

    def test_csrf_exempt(self) -> None:
        """CSRF token is not needed."""
        client = Client(enforce_csrf_checks=True)
        with self.assertValidationSuccessful():
            client.post(
                self.url,
                data=self.payload.dict(),
                content_type="application/json",
            )

    def test_request_content_type(self) -> None:
        """Only application/json requests are accepted."""
        for content_type in (
            "text/json",
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        ):
            with self.subTest(content_type=content_type):
                response = self._post(self.payload, content_type=content_type)
                self.assertResponseProblem(
                    response,
                    "only JSON data is accepted",
                )

    def test_request_body_length_cap(self) -> None:
        """Request body length is capped."""
        response = self._post("*" * 4097)
        self.assertResponseProblem(response, "JSON payload too big")

    def test_request_body_valid_json(self) -> None:
        """Request body must be valid JSON."""
        for body in "{", "", "[", "foo":
            with self.subTest(body=body):
                response = self._post(body)
                self.assertResponseProblem(
                    response, "payload is not valid JSON"
                )

    def test_request_body_valid_payload(self) -> None:
        """Request body must be a valid EnrollPayload."""
        for body in (
            {},
            "[]",
            "null",
            "42",
            {"nonce": "foobarbaz"},
            {"nonce": "foo", "challenge": "too short"},
        ):
            with self.subTest(body=body):
                response = self._post(body)
                self.assertResponseProblem(response, "payload data is invalid")

    async def test_payload_in_database(self) -> None:
        """Payload is stored in the database."""
        response = await self._apost(self.payload)

        # Payload is in database while the post is waiting for confirmation
        ce = await ClientEnroll.objects.aget(nonce=self.payload.nonce)
        self.assertEqual(ce.nonce, self.payload.nonce)
        self.assertEqual(ce.payload, self.payload.dict())

        # Send confirmation
        channel_layer = get_channel_layer()
        confirmation = EnrollConfirmPayload(
            outcome=EnrollOutcome.CONFIRM, token="a" * 32
        )
        await channel_layer.send(
            f"enroll.{self.payload.nonce}", confirmation.dict()
        )

        # The payload is in the database while the response is streaming (that
        # is, waiting/long polling)
        await ClientEnroll.objects.aget(nonce=self.payload.nonce)

        # Consume the streaming output
        payload = await self.assertResponsePayload(response)

        # The payload has been deleted from the database
        with self.assertRaises(ClientEnroll.DoesNotExist):
            await ClientEnroll.objects.aget(nonce=self.payload.nonce)

        self.assertEqual(payload.token, "a" * 32)

    async def test_duplicate_request(self) -> None:
        """Payload is stored in the database."""
        # The second request is isolated in a separate executor, to avoid a
        # query error that invalidates the transaction of the first one

        def duplicate_request() -> None:
            with transaction.atomic():
                self.assertResponseProblem(
                    self._post(self.payload),
                    "duplicate or invalid request received",
                )

        response = await self._apost(self.payload)

        # The second response fails
        await sync_to_async(duplicate_request)()

        # Send confirmation to complete the first response
        channel_layer = get_channel_layer()
        confirmation = EnrollConfirmPayload(
            outcome=EnrollOutcome.CONFIRM, token="a" * 32
        )
        await channel_layer.send(
            f"enroll.{self.payload.nonce}", confirmation.dict()
        )
        payload = await self.assertResponsePayload(response)
        self.assertEqual(payload.token, "a" * 32)

    async def test_client_disconnect(self) -> None:
        """Client disconnects during streaming."""
        # Replace the channel receive result with a future that we can cancel,
        # to simulate a client disconnect

        class _MockChannelLayer:
            def __init__(self) -> None:
                self.future: asyncio.Future[None] = asyncio.Future()

            async def receive(self, channel_name: str) -> Never:  # noqa: U100
                await self.future
                raise AssertionError(
                    "the future should have raised CancelledError"
                )

        mock_channel_layer = _MockChannelLayer()

        with mock.patch(
            "debusine.server.views.enroll.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            response = await self._apost(self.payload)

            # Payload is in the database
            await ClientEnroll.objects.aget(nonce=self.payload.nonce)

            # Simulate a browser disconnect
            mock_channel_layer.future.cancel()

            # Consume streaming content as a replacement for daphne doing its
            # thing
            # TODO: is there a better way to simulate a client disconnect?
            with self.assertRaises(asyncio.CancelledError):
                assert isinstance(response.streaming_content, AsyncIterator)
                async for chunk in response.streaming_content:
                    pass  # pragma: no cover

            # The payload has been deleted from the database
            with self.assertRaises(ClientEnroll.DoesNotExist):
                await ClientEnroll.objects.aget(nonce=self.payload.nonce)

    async def test_keepalive(self) -> None:
        """Test data sent to keep the connection alive during the long poll."""
        # Set interval timeout to "immediate"
        with mock.patch(
            "debusine.server.views.enroll.EnrollView.PING_INTERVAL", 0
        ):
            response = await self._apost(self.payload)

            # Before confirmation arrives, the line is kept alive with newlines
            assert isinstance(response.streaming_content, AsyncIterator)
            chunk = await anext(response.streaming_content)
            self.assertEqual(chunk, b"\n")

            # Send confirmation
            channel_layer = get_channel_layer()
            confirmation = EnrollConfirmPayload(
                outcome=EnrollOutcome.CONFIRM, token="a" * 32
            )

            await channel_layer.send(
                f"enroll.{self.payload.nonce}", confirmation.dict()
            )

            payload = await self.assertResponsePayload(response)
            self.assertEqual(payload.token, "a" * 32)
