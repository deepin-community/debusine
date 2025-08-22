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
from django.utils.html import format_html
from django.utils.safestring import mark_safe

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


@register.simple_tag
def artifact_link(artifact: int | Artifact) -> str:
    """Return a link to the artifact if it exists or "${artifact_id} Deleted."""

    def render(artifact_obj: Artifact) -> str:
        return format_html(
            '<a href="{}">{}</a>',
            artifact_obj.get_absolute_url(),
            artifact_obj.get_label(),
        )

    if isinstance(artifact, Artifact):
        return render(artifact)

    try:
        artifact_obj = Artifact.objects.get(id=artifact)
        return render(artifact_obj)
    except Artifact.DoesNotExist:
        return mark_safe(f"{artifact} (deleted)")


@register.simple_tag
def artifact_links(artifact_ids: list[int]) -> str:
    """Return links to the artifacts if exists or "${artifact_id} Deleted."""
    artifacts_by_id = {
        a.id: a for a in Artifact.objects.filter(id__in=artifact_ids)
    }

    # Some "artifact_ids" might not exist: use the Artifact or the id when
    # calling "artifact_link" to generate the ("deleted" artifacts)
    links = [
        artifact_link(artifacts_by_id.get(artifact_id, artifact_id))
        for artifact_id in artifact_ids
    ]
    return mark_safe(", ".join(links))
