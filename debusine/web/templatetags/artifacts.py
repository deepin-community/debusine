# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Template tag library to render artifacts."""

from django import template

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import Artifact
from debusine.db.models.artifacts import (
    ARTIFACT_CATEGORY_ICON_NAMES,
    ARTIFACT_CATEGORY_SHORT_NAMES,
)

register = template.Library()


@register.filter
def artifact_icon_name(artifact: Artifact | str) -> str:
    """Return the Bootstrap icon name for this artifact's category."""
    if isinstance(artifact, Artifact):
        category = artifact.category
    else:
        category = artifact
    return ARTIFACT_CATEGORY_ICON_NAMES.get(ArtifactCategory(category), "file")


@register.filter
def artifact_category_label(artifact: Artifact | str) -> str:
    """Return a short label to use to represent an artifact category."""
    if isinstance(artifact, Artifact):
        category = artifact.category
    else:
        category = artifact
    return ARTIFACT_CATEGORY_SHORT_NAMES.get(
        ArtifactCategory(category), "artifact"
    )
