# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for server-side task models."""

from unittest import TestCase

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.server.tasks.models import APTMirrorData


class APTMirrorDataTests(TestCase):
    """Tests for APTMirror task data."""

    def test_flat_repository(self) -> None:
        """A flat repository must not have components."""
        APTMirrorData(
            collection="example",
            url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://deb.example.org/"
            ),
            suite="./",
            architectures=["amd64"],
        )
        with self.assertRaisesRegex(
            ValueError,
            r'Flat repositories \(where suite ends with "/"\) must not have '
            r'components',
        ):
            APTMirrorData(
                collection="example",
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl, "https://deb.example.org/"
                ),
                suite="./",
                components=["main"],
                architectures=["amd64"],
            )

    def test_non_flat_repository(self) -> None:
        """A non-flat repository must have components."""
        APTMirrorData(
            collection="debian/bookworm",
            url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://deb.debian.org/debian"
            ),
            suite="bookworm",
            components=["main"],
            architectures=["amd64"],
        )
        with self.assertRaisesRegex(
            ValueError,
            r'Non-flat repositories \(where suite does not end with "/"\) must '
            r'have components',
        ):
            APTMirrorData(
                collection="debian/bookworm",
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl, "https://deb.debian.org/debian"
                ),
                suite="bookworm",
                architectures=["amd64"],
            )
