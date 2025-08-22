# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utility functions for tests that require collections."""

from typing import Any

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import Artifact, CollectionItem
from debusine.server.collections import (
    DebianSuiteLintianManager,
    DebianSuiteManager,
)
from debusine.test.django import TestCase


class CollectionTestMixin(TestCase):
    """Utility functions to create test collections."""

    def create_source_package(
        self, name: str, version: str, dsc_fields: dict[str, Any] | None = None
    ) -> Artifact:
        """Create a minimal `debian:source-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": name,
                "version": version,
                "type": "dpkg",
                "dsc_fields": dsc_fields or {},
            },
            paths=[f"{name}_{version}.dsc"],
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def create_source_package_item(
        self, manager: DebianSuiteManager, name: str, version: str
    ) -> CollectionItem:
        """Create a minimal source package collection item."""
        user = self.playground.get_default_user()
        return manager.add_artifact(
            self.create_source_package(name, version),
            user=user,
            variables={"component": "main", "section": "devel"},
        )

    def create_binary_package(
        self,
        srcpkg_name: str,
        srcpkg_version: str,
        name: str,
        version: str,
        architecture: str,
    ) -> Artifact:
        """Create a minimal `debian:binary-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "deb_fields": {
                    "Package": name,
                    "Version": version,
                    "Architecture": architecture,
                },
                "deb_control_files": [],
            },
            paths=[f"{name}_{version}_{architecture}.deb"],
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def create_binary_package_item(
        self,
        manager: DebianSuiteManager,
        srcpkg_name: str,
        srcpkg_version: str,
        name: str,
        version: str,
        architecture: str,
    ) -> CollectionItem:
        """Create a minimal source package collection item."""
        user = self.playground.get_default_user()
        return manager.add_artifact(
            self.create_binary_package(
                srcpkg_name, srcpkg_version, name, version, architecture
            ),
            user=user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

    def create_lintian_artifact(self, related_artifact: Artifact) -> Artifact:
        """Create a minimal `debian:lintian` artifact."""
        # BINARY_PACKAGE would also be possible, in which case we'd need to
        # extract the architecture from its data, but we don't need that
        # right now.
        assert related_artifact.category == ArtifactCategory.SOURCE_PACKAGE
        summary = {
            "tags_count_by_severity": {},
            "tags_found": [],
            "overridden_tags_found": [],
            "lintian_version": "1.0.0",
            "distribution": "bookworm",
            "package_filename": {
                fileinartifact.path.split("_")[0]: fileinartifact.path
                for fileinartifact in related_artifact.fileinartifact_set.all()
            },
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, related_artifact
        )
        return lintian_artifact

    def create_lintian_item(
        self, manager: DebianSuiteLintianManager, related_item: CollectionItem
    ) -> CollectionItem:
        """Create a minimal Lintian collection item."""
        assert related_item.artifact is not None
        user = self.playground.get_default_user()
        return manager.add_artifact(
            self.create_lintian_artifact(related_item.artifact),
            user=user,
            variables={"derived_from_ids": [related_item.id]},
        )
