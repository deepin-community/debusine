# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Views for the server application's OpenMetrics statistics."""

from collections.abc import Mapping
from enum import StrEnum
from functools import cache
from typing import Any

from django.utils.encoding import smart_str
from prometheus_client import exposition as prometheus
from prometheus_client.openmetrics import exposition as openmetrics
from prometheus_client.registry import CollectorRegistry
from rest_framework import renderers, status
from rest_framework.request import Request
from rest_framework.response import Response

from debusine.server.open_metrics import DebusineCollector
from debusine.server.views.base import BaseAPIView


def extract_media_type(content_type: str) -> str:
    """Remove the charset from a fully specified Content-Type."""
    return content_type.split(";", 1)[0].strip()


class RendererFormats(StrEnum):
    """Supported output formats."""

    PROMETHEUS = "prometheus"
    OPENMETRICS = "openmetrics"


class BaseTextRenderer(renderers.BaseRenderer):
    """Base class for returning text responses."""

    def render(
        self,
        data: str,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> str:
        """Render data to bytes."""
        accepted_media_type, renderer_context  # fake usage for vulture
        assert self.charset
        return smart_str(data, encoding=self.charset)


class PrometheusRenderer(BaseTextRenderer):
    """Renderer for Prometheus native metrics."""

    media_type = extract_media_type(prometheus.CONTENT_TYPE_LATEST)
    format = RendererFormats.PROMETHEUS


class OpenMetricsRenderer(BaseTextRenderer):
    """Renderer for OpenMetrics format metrics."""

    media_type = extract_media_type(openmetrics.CONTENT_TYPE_LATEST)
    format = RendererFormats.OPENMETRICS


@cache
def registry() -> CollectorRegistry:
    """Return a registry with our collector."""
    registry = CollectorRegistry(auto_describe=True)
    registry.register(DebusineCollector())
    return registry


class OpenMetricsView(BaseAPIView):
    """View used to get the OpenMetrics statistics."""

    renderer_classes = [PrometheusRenderer, OpenMetricsRenderer]

    def get(self, request: Request) -> Response:
        """Return OpenMetrics statistics."""
        match request.accepted_renderer.format:
            case RendererFormats.PROMETHEUS:
                data = prometheus.generate_latest(registry())
                content_type = prometheus.CONTENT_TYPE_LATEST
            case RendererFormats.OPENMETRICS:
                data = openmetrics.generate_latest(registry())  # type: ignore
                content_type = openmetrics.CONTENT_TYPE_LATEST
            case _:  # pragma: no cover
                raise AssertionError("An unexpected renderer was requested")
        return Response(
            data, status=status.HTTP_200_OK, content_type=content_type
        )
