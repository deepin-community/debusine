# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for artifacts templatetags."""

from unittest import TestCase

from debusine.db.models import Artifact
from debusine.db.models.artifacts import (
    ARTIFACT_CATEGORY_ICON_NAMES,
    ARTIFACT_CATEGORY_SHORT_NAMES,
)
from debusine.web.templatetags.artifacts import (
    artifact_category_label,
    artifact_icon_name,
)


class ArtifactTagsTests(TestCase):
    """Tests for artifacts tags."""

    def test_category_label_str(self) -> None:
        """Test artifact_category_label tag on strings."""
        for k, v in ARTIFACT_CATEGORY_SHORT_NAMES.items():
            self.assertEqual(artifact_category_label(k), v)
        self.assertEqual(artifact_category_label("debusine:test"), "artifact")

    def test_category_label_artifact(self) -> None:
        """Test artifact_category_label tag on artifacts."""
        for k, v in ARTIFACT_CATEGORY_SHORT_NAMES.items():
            artifact = Artifact(category=k)
            self.assertEqual(artifact_category_label(artifact), v)
        artifact = Artifact(category="debusine:test")
        self.assertEqual(artifact_category_label(artifact), "artifact")

    def test_icon_name_str(self) -> None:
        """Test artifact_icon_name tag on strings."""
        for k, v in ARTIFACT_CATEGORY_ICON_NAMES.items():
            self.assertEqual(artifact_icon_name(k), v)
        self.assertEqual(artifact_icon_name("debusine:test"), "file")

    def test_icon_name_artifact(self) -> None:
        """Test artifact_icon_name tag on artifacts."""
        for k, v in ARTIFACT_CATEGORY_ICON_NAMES.items():
            artifact = Artifact(category=k)
            self.assertEqual(artifact_icon_name(artifact), v)
        artifact = Artifact(category="debusine:test")
        self.assertEqual(artifact_icon_name(artifact), "file")
