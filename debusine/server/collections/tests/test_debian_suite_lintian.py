# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebianSuiteLintianManager."""

from datetime import datetime
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Collection,
    CollectionItem,
    User,
    Workspace,
    default_workspace,
)
from debusine.server.collections import (
    DebianSuiteLintianManager,
    ItemAdditionError,
)
from debusine.test.django import TestCase


class DebianSuiteLintianManagerTests(TestCase):
    """Tests for DebianSuiteLintianManager."""

    user: ClassVar[User]
    workspace: ClassVar[Workspace]
    collection: ClassVar[Collection]
    manager: ClassVar[DebianSuiteLintianManager]

    basic_summary = {
        "tags_count_by_severity": {},
        "tags_found": [],
        "overridden_tags_found": [],
        "lintian_version": "1.0.0",
        "distribution": "bookworm",
    }

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up tests."""
        super().setUpTestData()

        cls.user = get_user_model().objects.create_user(
            username="John", email="john@example.org"
        )

        cls.workspace = default_workspace()

        cls.collection = Collection.objects.create(
            name="Debian",
            category=CollectionCategory.SUITE_LINTIAN,
            workspace=cls.workspace,
        )

        cls.manager = DebianSuiteLintianManager(collection=cls.collection)

    def create_source_package(self, name: str, version: str) -> Artifact:
        """Create a minimal `debian:source-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": name,
                "version": version,
                "type": "dpkg",
                "dsc_fields": {},
            },
        )
        return artifact

    def create_binary_package(
        self, srcpkg_name: str, srcpkg_version: str
    ) -> Artifact:
        """Create a minimal `debian:binary-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "deb_fields": {},
                "deb_control_files": [],
            },
        )
        return artifact

    def create_binary_packages(
        self,
        srcpkg_name: str,
        srcpkg_version: str,
        version: str,
        architecture: str,
    ) -> Artifact:
        """Create a minimal `debian:binary-packages` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGES,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "version": version,
                "architecture": architecture,
                "packages": [],
            },
        )
        return artifact

    def create_binary_upload(
        self, architecture: str, filenames: list[str]
    ) -> Artifact:
        """Create a minimal `debian:upload` artifact with binaries."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD,
            data={
                "type": "dpkg",
                "changes_fields": {
                    "Architecture": architecture,
                    "Files": [{"name": filename} for filename in filenames],
                },
            },
        )
        return artifact

    def test_init_wrong_collection_category_raise_value_error(self) -> None:
        """Init raises `ValueError`: wrong collection category."""
        category = "debian:something-else"
        collection = Collection.objects.create(
            name="Name is not used",
            category=category,
            workspace=self.workspace,
        )

        msg = f'^DebianSuiteLintianManager cannot manage "{category}" category$'

        with self.assertRaisesRegex(ValueError, msg):
            DebianSuiteLintianManager(collection)

    def test_get_related_source_package_direct(self) -> None:
        """Analysis relates directly to a source package."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )

        self.assertEqual(
            self.manager.get_related_source_package(lintian_artifact),
            source_package_artifact,
        )

    def test_get_related_source_package_via_binary_package(self) -> None:
        """Analysis relates to a source package via `debian:binary-package`."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        binary_package_artifact = self.create_binary_package("hello", "1.0")
        self.playground.create_artifact_relation(
            binary_package_artifact,
            source_package_artifact,
            ArtifactRelation.Relations.BUILT_USING,
        )
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0_amd64.deb"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "amd64", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, binary_package_artifact
        )

        self.assertEqual(
            self.manager.get_related_source_package(lintian_artifact),
            source_package_artifact,
        )

    def test_get_related_source_package_via_binary_packages(self) -> None:
        """Analysis relates to a source package via `debian:binary-packages`."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        binary_packages_artifact = self.create_binary_packages(
            "hello", "1.0", "1.0", "amd64"
        )
        self.playground.create_artifact_relation(
            binary_packages_artifact,
            source_package_artifact,
            ArtifactRelation.Relations.BUILT_USING,
        )
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0_amd64.deb"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "amd64", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, binary_packages_artifact
        )

        self.assertEqual(
            self.manager.get_related_source_package(lintian_artifact),
            source_package_artifact,
        )

    def test_get_related_source_package_via_upload(self) -> None:
        """Analysis relates to a source package via `debian:upload`."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        binary_package_artifact = self.create_binary_package("hello", "1.0")
        self.playground.create_artifact_relation(
            binary_package_artifact,
            source_package_artifact,
            ArtifactRelation.Relations.BUILT_USING,
        )
        upload_artifact = self.create_binary_upload(
            "amd64", ["hello_1.0_amd64.deb"]
        )
        self.playground.create_artifact_relation(
            upload_artifact,
            binary_package_artifact,
            ArtifactRelation.Relations.EXTENDS,
        )
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0_amd64.deb"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "amd64", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, upload_artifact
        )

        self.assertEqual(
            self.manager.get_related_source_package(lintian_artifact),
            source_package_artifact,
        )

    def test_get_lintian_architecture_source(self) -> None:
        """Get the architecture name for a source analysis."""
        self.assertEqual(
            self.manager.get_lintian_architecture({"hello": "hello_1.0.dsc"}),
            "source",
        )

    def test_get_lintian_architecture_binary_all(self) -> None:
        """Get the architecture name for a binary-all analysis."""
        self.assertEqual(
            self.manager.get_lintian_architecture(
                {
                    "hello": "hello_1.0_all.deb",
                    "hello-doc": "hello-doc_1.0_all.deb",
                }
            ),
            "all",
        )

    def test_get_lintian_architecture_binary_any(self) -> None:
        """Get the architecture name for a binary-any analysis."""
        self.assertEqual(
            self.manager.get_lintian_architecture(
                {
                    "libhello1": "libhello1_1.0_amd64.deb",
                    "libhello-dev": "libhello-dev_1.0_amd64.deb",
                }
            ),
            "amd64",
        )

    def test_get_lintian_architecture_binary_any_udeb(self) -> None:
        """Get the architecture name for a binary-any analysis with a udeb."""
        self.assertEqual(
            self.manager.get_lintian_architecture(
                {"hello-udeb": "hello-udeb_1.0_amd64.udeb"}
            ),
            "amd64",
        )

    def test_get_lintian_architecture_no_architectures(self) -> None:
        """`get_lintian_architecture` raises error: no architectures."""
        with self.assertRaisesRegex(
            ItemAdditionError, "Could not infer any architectures for analysis"
        ):
            self.manager.get_lintian_architecture({"hello": "nonsense"})

    def test_get_lintian_architecture_multiple_architectures(self) -> None:
        """`get_lintian_architecture` raises error: multiple architectures."""
        with self.assertRaisesRegex(
            ItemAdditionError,
            r"Analysis refers to multiple architectures: \['amd64', 's390x'\]",
        ):
            self.manager.get_lintian_architecture(
                {
                    "libhello1": "libhello1_1.0_amd64.deb",
                    "libhello-dev": "libhello-dev_1.0_s390x.deb",
                }
            )

    def test_do_add_artifact(self) -> None:
        """`do_add_artifact` adds the artifact."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )

        collection_item = self.manager.add_artifact(
            lintian_artifact, user=self.user
        )

        collection_item.refresh_from_db()

        self.assertEqual(collection_item.name, "hello_1.0_source")
        self.assertEqual(
            collection_item.data,
            {"package": "hello", "version": "1.0", "architecture": "source"},
        )

    def test_do_add_artifact_raise_item_addition_error(self) -> None:
        """`do_add_artifact` raises error: duplicated CollectionItem data."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact_1, source_package_artifact
        )
        lintian_artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact_2, source_package_artifact
        )

        self.manager.add_artifact(lintian_artifact_1, user=self.user)

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_artifact(lintian_artifact_2, user=self.user)

    def test_do_add_artifact_no_related_source_package(self) -> None:
        """`do_add_artifact` raises error: no related source package."""
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "has no related source package"
        ):
            self.manager.add_artifact(lintian_artifact, user=self.user)

    def test_do_add_artifact_multiple_related_source_packages(self) -> None:
        """`do_add_artifact` raises error: multiple related source packages."""
        source_package_artifact_1 = self.create_source_package("hello", "1.0")
        source_package_artifact_2 = self.create_source_package("hello", "1.1")
        binary_package_artifact = self.create_binary_package("hello", "1.1")
        self.playground.create_artifact_relation(
            binary_package_artifact,
            source_package_artifact_2,
            ArtifactRelation.Relations.BUILT_USING,
        )
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact_1
        )
        self.playground.create_artifact_relation(
            lintian_artifact, binary_package_artifact
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "has multiple related source packages"
        ):
            self.manager.add_artifact(lintian_artifact, user=self.user)

    def test_do_add_artifact_derived_from(self) -> None:
        """`do_add_artifact` stores derived-from information if given."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        suite_collection = Collection.objects.create(
            name="test",
            category=CollectionCategory.SUITE,
            workspace=self.workspace,
        )
        source_package_item = suite_collection.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )

        collection_item = self.manager.add_artifact(
            lintian_artifact,
            user=self.user,
            variables={"derived_from_ids": [source_package_item.id]},
        )

        self.assertEqual(
            collection_item.data["derived_from"], [source_package_item.id]
        )

    def test_do_add_artifact_replace(self) -> None:
        """`do_add_artifact` can replace an existing artifact."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )
        collection_item = self.manager.add_artifact(
            lintian_artifact, user=self.user
        )
        lintian_artifact2, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact2, source_package_artifact
        )

        collection_item2 = self.manager.add_artifact(
            lintian_artifact2, user=self.user, replace=True
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, lintian_artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "hello_1.0_source")
        self.assertEqual(collection_item2.artifact, lintian_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    def test_do_add_artifact_replace_nonexistent(self) -> None:
        """Replacing a nonexistent artifact is allowed."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )

        collection_item = self.manager.add_artifact(
            lintian_artifact, user=self.user, replace=True
        )

        self.assertEqual(collection_item.name, "hello_1.0_source")
        self.assertEqual(collection_item.artifact, lintian_artifact)

    def test_do_add_collection_raise_item_addition_error(self) -> None:
        """
        `do_add_collection` raises `ItemAdditionError`.

        No Collections can be added to the debian:suite-lintian collection.
        """
        msg = (
            f'^Cannot add collections into '
            f'"{self.manager.COLLECTION_CATEGORY}"$'
        )
        collection = Collection.objects.create(
            name="Some-collection",
            category="Some category",
            workspace=self.workspace,
        )

        with self.assertRaisesRegex(ItemAdditionError, msg):
            self.manager.do_add_collection(collection, user=self.user)

    def test_do_remove_item_artifact(self) -> None:
        """`do_remove_item` removes an artifact item."""
        source_package_artifact = self.create_source_package("hello", "1.0")
        summary = {
            **self.basic_summary,
            "package_filename": {"hello": "hello_1.0.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )

        collection_item = self.manager.add_artifact(
            lintian_artifact, user=self.user
        )

        # Test removing the artifact from the collection
        self.manager.remove_item(collection_item, user=self.user)

        # The artifact is not removed yet (retention period applies)
        self.assertEqual(collection_item.artifact, lintian_artifact)

        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsInstance(collection_item.removed_at, datetime)

    def test_lookup_unexpected_format_raise_lookup_error(self) -> None:
        """`lookup` raises `LookupError`: invalid format."""
        msg = '^Unexpected lookup format: "binary:hello"$'

        with self.assertRaisesRegex(LookupError, msg):
            self.manager.lookup("binary:hello")

    def test_lookup_return_none(self) -> None:
        """`lookup` returns None if there are no matches."""
        self.assertIsNone(self.manager.lookup("latest:hello_source"))

    def test_lookup_return_matching_collection_item(self) -> None:
        """`lookup` returns a matching collection item."""
        items: list[CollectionItem] = []
        for name, version, architecture in (
            ("hello", "1.0", "source"),
            ("hello", "1.0", "amd64"),
            ("hello", "1.1", "source"),
            ("base-files", "1.0", "source"),
        ):
            source_package_artifact = self.create_source_package(name, version)
            if architecture != "source":
                binary_package_artifact = self.create_binary_package(
                    name, version
                )
                self.playground.create_artifact_relation(
                    binary_package_artifact,
                    source_package_artifact,
                    ArtifactRelation.Relations.BUILT_USING,
                )
                lintian_target = binary_package_artifact
            else:
                lintian_target = source_package_artifact
            summary = {
                **self.basic_summary,
                "package_filename": {
                    name: (
                        f"{name}_{version}.dsc"
                        if architecture == "source"
                        else f"{name}_{version}_{architecture}.deb"
                    )
                },
            }
            lintian_artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.LINTIAN,
                data={"architecture": architecture, "summary": summary},
            )
            self.playground.create_artifact_relation(
                lintian_artifact, lintian_target
            )
            items.append(
                self.manager.add_artifact(lintian_artifact, user=self.user)
            )

        source_package_artifact = self.create_source_package("hello", "1.2")
        summary = {
            **self.basic_summary,
            "package_filename": {name: f"{name}_{version}.dsc"},
        }
        lintian_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.LINTIAN,
            data={"architecture": "source", "summary": summary},
        )
        self.playground.create_artifact_relation(
            lintian_artifact, source_package_artifact
        )
        items.append(
            self.manager.add_artifact(lintian_artifact, user=self.user)
        )
        items[-1].removed_at = timezone.now()
        items[-1].save()

        # CollectionItem of type BARE should not exist in this collection
        # (the manager does not allow adding it).  Add one to ensure that it
        # is filtered out.
        CollectionItem.objects.create(
            child_type=CollectionItem.Types.BARE,
            created_by_user=self.user,
            parent_collection=self.collection,
            category=ArtifactCategory.LINTIAN,
            name="something",
            data={"package": "hello", "version": "1.0"},
        )

        # items[0] (hello_1.0_source) and items[2] (hello_1.1_source) both
        # match, but items[2] was created later.  items[4] (hello_1.2_source)
        # has been removed.
        self.assertEqual(self.manager.lookup("latest:hello_source"), items[2])

        self.assertEqual(self.manager.lookup("latest:hello_amd64"), items[1])

        self.assertEqual(
            self.manager.lookup("version:hello_1.0_source"), items[0]
        )
        self.assertEqual(
            self.manager.lookup("version:hello_1.0_amd64"), items[1]
        )
        self.assertEqual(
            self.manager.lookup("version:hello_1.1_source"), items[2]
        )
        self.assertIsNone(self.manager.lookup("version:hello_1.1_amd64"))
        self.assertIsNone(self.manager.lookup("version:hello_1.2_source"))
        self.assertEqual(
            self.manager.lookup("version:base-files_1.0_source"), items[3]
        )
        self.assertIsNone(self.manager.lookup("version:base-files_1.0_amd64"))

        self.assertEqual(self.manager.lookup("name:hello_1.0_source"), items[0])
        self.assertEqual(self.manager.lookup("name:hello_1.0_amd64"), items[1])
        self.assertEqual(self.manager.lookup("name:hello_1.1_source"), items[2])
        self.assertEqual(
            self.manager.lookup("name:base-files_1.0_source"), items[3]
        )
        self.assertIsNone(self.manager.lookup("name:hello_1.2_source"))
