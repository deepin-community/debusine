# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-client enrolling views."""

from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseServerError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from debusine.client.models import (
    EnrollConfirmPayload,
    EnrollOutcome,
    EnrollPayload,
)
from debusine.db.models import Scope, Token
from debusine.db.models.auth import ClientEnroll
from debusine.web.views.base import BaseUIView, DetailViewBase


class ConfirmView(BaseUIView, DetailViewBase[ClientEnroll]):
    """Confirm a waiting client."""

    template_name = "web/enroll-confirm.html"
    title = "Confirm 'debusine setup'"
    model = ClientEnroll

    def get_object(
        self, queryset: QuerySet[ClientEnroll] | None = None
    ) -> ClientEnroll:
        """Return the ClientEnroll object to show."""
        assert queryset is None
        queryset = self.get_queryset()
        return get_object_or_404(queryset, nonce=self.kwargs["nonce"])

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add the parsed payload."""
        ctx = super().get_context_data(**kwargs)
        payload: EnrollPayload | None
        try:
            payload = EnrollPayload.parse_obj(self.object.payload)
        except ValueError:
            payload = None
        ctx["payload"] = payload
        if payload:
            ctx["scope_valid"] = Scope.objects.filter(
                name=payload.scope
            ).exists()
        return ctx

    def get(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Ask for a confirmation."""
        if not request.user.is_authenticated:
            return redirect(
                reverse("login") + "?next=" + request.get_full_path()
            )
        return super().get(request, *args, **kwargs)

    def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Provide a confirmation."""
        if not request.user.is_authenticated:
            raise PermissionDenied()
        self.object = self.get_object()
        try:
            payload = EnrollPayload.parse_obj(self.object.payload)
        except ValueError:
            return HttpResponseServerError("Invalid payload in database")
        channel_layer = get_channel_layer()
        channel_name = f"enroll.{payload.nonce}"

        try:
            outcome = EnrollOutcome(request.POST["outcome"])
        except ValueError:
            return HttpResponseServerError("Invalid outcome in POST data")

        if outcome == EnrollOutcome.CONFIRM:
            # Generate the new token
            token = Token.objects.create(
                enabled=True,
                user=request.user,
                comment=(
                    f"obtained via 'debusine setup' on {payload.hostname}"
                ),
            ).key
        else:
            token = None

        confirmation = EnrollConfirmPayload(outcome=outcome, token=token)
        async_to_sync(channel_layer.send)(channel_name, confirmation.dict())

        return redirect(
            reverse(
                "user:token-list", kwargs={"username": request.user.username}
            )
        )
