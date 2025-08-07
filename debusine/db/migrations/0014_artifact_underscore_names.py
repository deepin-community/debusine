# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Rename artifacts to use underscores instead of hyphens in key names."""

from django.db import migrations

from debusine.artifacts.models import ArtifactCategory
from debusine.db.migrations._utils import make_artifact_field_renamer


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("db", "0013_expiration_delay"),
    ]

    operations = [
        make_artifact_field_renamer(
            ArtifactCategory.UPLOAD, [("changes-fields", "changes_fields")]
        ),
        make_artifact_field_renamer(
            ArtifactCategory.SOURCE_PACKAGE, [("dsc-fields", "dsc_fields")]
        ),
        make_artifact_field_renamer(
            ArtifactCategory.BINARY_PACKAGE,
            [
                ("srcpkg-name", "srcpkg_name"),
                ("srcpkg-version", "srcpkg_version"),
                ("deb-fields", "deb_fields"),
                ("deb-control-files", "deb_control_files"),
            ],
        ),
        make_artifact_field_renamer(
            ArtifactCategory.BINARY_PACKAGES,
            [
                ("srcpkg-name", "srcpkg_name"),
                ("srcpkg-version", "srcpkg_version"),
            ],
        ),
    ]
