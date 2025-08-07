# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for SystemImageBuild class."""
from typing import Any

from debusine.tasks.models import BaseDynamicTaskData
from debusine.tasks.systemimagebuild import SystemImageBuild
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase


class SystemImageBuildImpl(SystemImageBuild):
    """Implementation of SystemImageBuild ontology."""

    def _cmdline(self) -> list[str]:
        return []  # pragma: no cover


class SystemImageBuildTests(
    ExternalTaskHelperMixin[SystemImageBuildImpl], TestCase
):
    """Tests SystemImageBuild class."""

    SAMPLE_TASK_DATA: dict[str, Any] = {
        "bootstrap_options": {
            "architecture": "amd64",
            "extra_packages": ["hello", "python3"],
            "use_signed_by": True,
        },
        "bootstrap_repositories": [
            {
                "mirror": "https://deb.debian.org/debian",
                "suite": "stable",
                "components": ["main", "contrib"],
                "check_signature_with": "system",
            },
            {
                "types": ["deb-src"],
                "mirror": "https://example.com",
                "suite": "bullseye",
                "components": ["main"],
                "check_signature_with": "system",
                "keyring": {"url": "https://example.com/keyring.gpg"},
            },
        ],
        "disk_image": {
            "format": "raw",
            "partitions": [
                {
                    "size": 2,
                    "filesystem": "ext4",
                },
            ],
        },
    }

    def setUp(self) -> None:
        """Initialize test."""
        self.configure_task()

    def test_compute_dynamic_data(self) -> None:
        """Test compute_dynamic_data."""
        task_database = FakeTaskDatabase()

        self.assertEqual(
            self.task.compute_dynamic_data(task_database),
            BaseDynamicTaskData(
                subject="stable",
                runtime_context="amd64:None:hello,python3",
                configuration_context="amd64",
            ),
        )

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "bootstrap a system image")
