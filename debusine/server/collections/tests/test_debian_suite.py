# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebianSuiteManager."""

from datetime import datetime, timedelta
from pathlib import PurePath
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    FileInArtifact,
    User,
    Workspace,
    default_workspace,
)
from debusine.server.collections import DebianSuiteManager, ItemAdditionError
from debusine.server.collections.debian_suite import (
    make_pool_filename,
    make_source_prefix,
)
from debusine.test.django import TestCase


class MakePoolFilenameTests(TestCase):
    """Tests for make_pool_filename."""

    def test_normal(self) -> None:
        """`make_pool_filename` works for normal source package names."""
        self.assertEqual(make_source_prefix("hello"), "h")
        self.assertEqual(
            "pool/main/h/hello/hello_1.0.dsc",
            make_pool_filename("hello", "main", "hello_1.0.dsc"),
        )

    def test_library(self) -> None:
        """`make_pool_filename` works for library source package names."""
        self.assertEqual(make_source_prefix("libhello"), "libh")
        self.assertEqual(
            "pool/non-free/libh/libhello/libhello_1.0.dsc",
            make_pool_filename("libhello", "non-free", "libhello_1.0.dsc"),
        )


class DebianSuiteManagerTests(TestCase):
    """Tests for DebianSuiteManager."""

    user: ClassVar[User]
    workspace: ClassVar[Workspace]
    collection: ClassVar[Collection]
    manager: ClassVar[DebianSuiteManager]

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
            category=CollectionCategory.SUITE,
            workspace=cls.workspace,
        )

        assert isinstance(cls.collection.manager, DebianSuiteManager)
        cls.manager = cls.collection.manager

    def create_source_package(
        self, name: str, version: str, paths: list[str]
    ) -> Artifact:
        """Create a minimal `debian:source-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": name,
                "version": version,
                "type": "dpkg",
                "dsc_fields": {},
            },
            paths=paths,
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def create_binary_package(
        self,
        srcpkg_name: str,
        srcpkg_version: str,
        name: str,
        version: str,
        architecture: str,
        paths: list[str],
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
            paths=paths,
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def create_repository_index(self, path: str) -> Artifact:
        """Create a minimal ``debian:repository-index`` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.REPOSITORY_INDEX,
            paths=[path],
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def test_init_wrong_collection_category_raise_value_error(self) -> None:
        """Init raise ValueError: wrong collection category."""
        category = "debian:something-else"
        collection = Collection.objects.create(
            name="Name is not used",
            category=category,
            workspace=self.workspace,
        )

        msg = f'^DebianSuiteManager cannot manage "{category}" category$'

        with self.assertRaisesRegex(ValueError, msg):
            DebianSuiteManager(collection)

    def test_add_artifact_source_package(self) -> None:
        """`add_artifact` can add a source package to the suite."""
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file

        collection_item = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.name, "hello_1.0")
        self.assertEqual(
            collection_item.data,
            {
                "package": "hello",
                "version": "1.0",
                "component": "main",
                "section": "devel",
            },
        )
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": collection_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.dsc",
                    "value": f"sha256:{dsc_file.hash_digest.hex()}",
                },
                {
                    "collection_item_id": collection_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.tar.xz",
                    "value": f"sha256:{tar_file.hash_digest.hex()}",
                },
            ],
        )

    def test_add_artifact_source_package_adds_archive_constraints(self) -> None:
        """`add_artifact` adds archive constraints for source packages."""
        archive = self.workspace.get_singleton_collection(
            user=self.user, category=CollectionCategory.ARCHIVE
        )
        archive.manager.add_collection(self.collection, user=self.user)
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file

        collection_item = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.name, "hello_1.0")
        self.assertEqual(
            collection_item.data,
            {
                "package": "hello",
                "version": "1.0",
                "component": "main",
                "section": "devel",
            },
        )
        for collection in (self.collection, archive):
            self.assert_collection_has_constraints(
                self.collection,
                [
                    {
                        "collection_item_id": collection_item.id,
                        "constraint_name": "pool-file",
                        "key": "pool/main/h/hello/hello_1.0.dsc",
                        "value": f"sha256:{dsc_file.hash_digest.hex()}",
                    },
                    {
                        "collection_item_id": collection_item.id,
                        "constraint_name": "pool-file",
                        "key": "pool/main/h/hello/hello_1.0.tar.xz",
                        "value": f"sha256:{tar_file.hash_digest.hex()}",
                    },
                ],
            )

    def test_add_artifact_source_package_replace(self) -> None:
        """`add_artifact` can replace an existing source package."""
        self.collection.data["may_reuse_versions"] = True
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        collection_item = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )
        source_package_artifact2 = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        collection_item2 = self.manager.add_artifact(
            source_package_artifact2,
            user=self.user,
            variables={"component": "main", "section": "devel"},
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, source_package_artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "hello_1.0")
        self.assertEqual(collection_item2.artifact, source_package_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    def test_add_artifact_source_package_replace_nonexistent(self) -> None:
        """Replacing a nonexistent source package is allowed."""
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )

        collection_item = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
            replace=True,
        )

        self.assertEqual(collection_item.name, "hello_1.0")
        self.assertEqual(collection_item.artifact, source_package_artifact)

    def test_add_artifact_source_package_already_replaced(self) -> None:
        """`add_artifact` can add an already-replaced source package."""
        self.collection.data["may_reuse_versions"] = True
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file
        collection_item_new = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )
        source_package_artifact_old = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.gz"]
        )
        yesterday = timezone.now() - timedelta(days=1)
        collection_item_old = self.manager.add_artifact(
            source_package_artifact_old,
            user=self.user,
            variables={"component": "main", "section": "devel"},
            created_at=yesterday,
            replaced_by=collection_item_new,
        )

        self.assertEqual(collection_item_new.artifact, source_package_artifact)
        self.assertIsNone(collection_item_new.removed_at)
        self.assertEqual(collection_item_old.name, "hello_1.0")
        self.assertEqual(
            collection_item_old.artifact, source_package_artifact_old
        )
        self.assertEqual(collection_item_old.created_at, yesterday)
        self.assertEqual(
            collection_item_old.removed_at, collection_item_new.created_at
        )
        self.assertEqual(
            collection_item_old.removed_by_user,
            collection_item_new.created_by_user,
        )
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": collection_item_new.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.dsc",
                    "value": f"sha256:{dsc_file.hash_digest.hex()}",
                },
                {
                    "collection_item_id": collection_item_new.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.tar.xz",
                    "value": f"sha256:{tar_file.hash_digest.hex()}",
                },
            ],
        )

    def test_add_artifact_binary_package(self) -> None:
        """`add_artifact` can add a binary package to the suite."""
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        deb_file = binary_package_artifact.fileinartifact_set.get(
            path="libhello1_1.0_amd64.deb"
        ).file
        dbgsym_file = binary_package_artifact.fileinartifact_set.get(
            path="libhello1-dbgsym_1.0_amd64.deb",
        ).file

        collection_item = self.manager.add_artifact(
            binary_package_artifact,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.name, "libhello1_1:1.0_amd64")
        self.assertEqual(
            collection_item.data,
            {
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0",
                "package": "libhello1",
                "version": "1:1.0",
                "architecture": "amd64",
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": collection_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/libhello1_1.0_amd64.deb",
                    "value": f"sha256:{deb_file.hash_digest.hex()}",
                },
                {
                    "collection_item_id": collection_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/libhello1-dbgsym_1.0_amd64.deb",
                    "value": f"sha256:{dbgsym_file.hash_digest.hex()}",
                },
            ],
        )

    def test_add_artifact_binary_package_adds_archive_constraints(self) -> None:
        """`add_artifact` adds archive constraints for binary packages."""
        archive = self.workspace.get_singleton_collection(
            user=self.user, category=CollectionCategory.ARCHIVE
        )
        archive.manager.add_collection(self.collection, user=self.user)
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        deb_file = binary_package_artifact.fileinartifact_set.get(
            path="libhello1_1.0_amd64.deb"
        ).file
        dbgsym_file = binary_package_artifact.fileinartifact_set.get(
            path="libhello1-dbgsym_1.0_amd64.deb",
        ).file

        collection_item = self.manager.add_artifact(
            binary_package_artifact,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.name, "libhello1_1:1.0_amd64")
        self.assertEqual(
            collection_item.data,
            {
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0",
                "package": "libhello1",
                "version": "1:1.0",
                "architecture": "amd64",
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )
        for collection in (self.collection, archive):
            self.assert_collection_has_constraints(
                collection,
                [
                    {
                        "collection_item_id": collection_item.id,
                        "constraint_name": "pool-file",
                        "key": "pool/main/h/hello/libhello1_1.0_amd64.deb",
                        "value": f"sha256:{deb_file.hash_digest.hex()}",
                    },
                    {
                        "collection_item_id": collection_item.id,
                        "constraint_name": "pool-file",
                        "key": (
                            "pool/main/h/hello/libhello1-dbgsym_1.0_amd64.deb"
                        ),
                        "value": f"sha256:{dbgsym_file.hash_digest.hex()}",
                    },
                ],
            )

    def test_add_artifact_binary_package_replace(self) -> None:
        """`add_artifact` can replace an existing binary package."""
        self.collection.data["may_reuse_versions"] = True
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        collection_item = self.manager.add_artifact(
            binary_package_artifact,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )
        binary_package_artifact2 = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        collection_item2 = self.manager.add_artifact(
            binary_package_artifact2,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, binary_package_artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "libhello1_1:1.0_amd64")
        self.assertEqual(collection_item2.artifact, binary_package_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    def test_add_artifact_binary_package_replace_nonexistent(self) -> None:
        """Replacing a nonexistent binary package is allowed."""
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )

        collection_item = self.manager.add_artifact(
            binary_package_artifact,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
            replace=True,
        )

        self.assertEqual(collection_item.name, "libhello1_1:1.0_amd64")
        self.assertEqual(collection_item.artifact, binary_package_artifact)

    def test_add_artifact_binary_package_already_replaced(self) -> None:
        """`add_artifact` can add an already-replaced binary package."""
        self.collection.data["may_reuse_versions"] = True
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        deb_file = binary_package_artifact.fileinartifact_set.get(
            path="libhello1_1.0_amd64.deb"
        ).file
        dbgsym_file = binary_package_artifact.fileinartifact_set.get(
            path="libhello1-dbgsym_1.0_amd64.deb",
        ).file
        collection_item_new = self.manager.add_artifact(
            binary_package_artifact,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )
        binary_package_artifact_old = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello-dev_1.0_amd64.deb"],
        )
        yesterday = timezone.now() - timedelta(days=1)
        collection_item_old = self.manager.add_artifact(
            binary_package_artifact_old,
            user=self.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
            created_at=yesterday,
            replaced_by=collection_item_new,
        )

        self.assertEqual(collection_item_new.artifact, binary_package_artifact)
        self.assertIsNone(collection_item_new.removed_at)
        self.assertEqual(collection_item_old.name, "libhello1_1:1.0_amd64")
        self.assertEqual(
            collection_item_old.artifact, binary_package_artifact_old
        )
        self.assertEqual(collection_item_old.created_at, yesterday)
        self.assertEqual(
            collection_item_old.removed_at, collection_item_new.created_at
        )
        self.assertEqual(
            collection_item_old.removed_by_user,
            collection_item_new.created_by_user,
        )
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": collection_item_new.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/libhello1_1.0_amd64.deb",
                    "value": f"sha256:{deb_file.hash_digest.hex()}",
                },
                {
                    "collection_item_id": collection_item_new.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/libhello1-dbgsym_1.0_amd64.deb",
                    "value": f"sha256:{dbgsym_file.hash_digest.hex()}",
                },
            ],
        )

    def test_add_artifact_repository_index(self) -> None:
        """``add_artifact`` can add a repository index to the suite."""
        packages_artifact = self.create_repository_index("Packages.xz")

        collection_item = self.manager.add_artifact(
            packages_artifact,
            user=self.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
        )

        self.assertEqual(
            collection_item.name, "index:main/binary-amd64/Packages.xz"
        )
        self.assertEqual(
            collection_item.data, {"path": "main/binary-amd64/Packages.xz"}
        )
        self.assert_collection_has_constraints(self.collection, [])

    def test_add_artifact_repository_index_replace(self) -> None:
        """``add_artifact`` can replace an existing repository index."""
        packages_artifact = self.create_repository_index("Packages.xz")
        collection_item = self.manager.add_artifact(
            packages_artifact,
            user=self.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
        )
        packages_artifact2 = self.create_repository_index("Packages.xz")
        collection_item2 = self.manager.add_artifact(
            packages_artifact2,
            user=self.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, packages_artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(
            collection_item2.name, "index:main/binary-amd64/Packages.xz"
        )
        self.assertEqual(collection_item2.artifact, packages_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    def test_add_artifact_repository_index_already_replaced(self) -> None:
        """``add_artifact`` can add an already-replaced repository index."""
        packages_artifact = self.create_repository_index("Packages.xz")
        collection_item_new = self.manager.add_artifact(
            packages_artifact,
            user=self.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
        )
        packages_artifact2 = self.create_repository_index("Packages.xz")
        yesterday = timezone.now() - timedelta(days=1)
        collection_item_old = self.manager.add_artifact(
            packages_artifact2,
            user=self.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
            created_at=yesterday,
            replaced_by=collection_item_new,
        )

        self.assertEqual(collection_item_new.artifact, packages_artifact)
        self.assertIsNone(collection_item_new.removed_at)
        self.assertEqual(
            collection_item_old.name, "index:main/binary-amd64/Packages.xz"
        )
        self.assertEqual(collection_item_old.artifact, packages_artifact2)
        self.assertEqual(collection_item_old.created_at, yesterday)
        self.assertEqual(
            collection_item_old.removed_at, collection_item_new.created_at
        )
        self.assertEqual(
            collection_item_old.removed_by_user,
            collection_item_new.created_by_user,
        )

    def test_do_add_artifact_source_package_requires_component(self) -> None:
        """`do_add_artifact` requires `component` for source packages."""
        source_package_artifact = self.create_source_package("hello", "1.0", [])

        with self.assertRaisesRegex(
            ItemAdditionError,
            '^Adding debian:source-package to debian:suite requires '
            '"component"$',
        ):
            self.manager.do_add_artifact(
                source_package_artifact, user=self.user
            )

    def test_do_add_artifact_source_package_requires_section(self) -> None:
        """`do_add_artifact` requires `section` for source packages."""
        source_package_artifact = self.create_source_package("hello", "1.0", [])

        with self.assertRaisesRegex(
            ItemAdditionError,
            '^Adding debian:source-package to debian:suite requires "section"$',
        ):
            self.manager.do_add_artifact(
                source_package_artifact,
                user=self.user,
                variables={"component": "main"},
            )

    def test_do_add_artifact_binary_package_requires_component(self) -> None:
        """`do_add_artifact` requires `component` for binary packages."""
        binary_package_artifact = self.create_binary_package(
            "hello", "1.0", "hello", "1.0", "amd64", []
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            '^Adding debian:binary-package to debian:suite requires '
            '"component"$',
        ):
            self.manager.do_add_artifact(
                binary_package_artifact, user=self.user
            )

    def test_do_add_artifact_binary_package_requires_section(self) -> None:
        """`do_add_artifact` requires `section` for binary packages."""
        binary_package_artifact = self.create_binary_package(
            "hello", "1.0", "hello", "1.0", "amd64", []
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            '^Adding debian:binary-package to debian:suite requires "section"$',
        ):
            self.manager.do_add_artifact(
                binary_package_artifact,
                user=self.user,
                variables={"component": "main"},
            )

    def test_do_add_artifact_binary_package_requires_priority(self) -> None:
        """`do_add_artifact` requires `priority` for binary packages."""
        binary_package_artifact = self.create_binary_package(
            "hello", "1.0", "hello", "1.0", "amd64", []
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            '^Adding debian:binary-package to debian:suite requires '
            '"priority"$',
        ):
            self.manager.do_add_artifact(
                binary_package_artifact,
                user=self.user,
                variables={"component": "main", "section": "devel"},
            )

    def test_do_add_artifact_overlapping_files_same_contents(self) -> None:
        """`do_add_artifact` allows same paths with same contents."""
        source_package_artifacts: list[Artifact] = []
        path_digests: list[tuple[str, str]] = []
        for version in ("1.0-1", "1.0-2"):
            artifact = self.create_source_package("hello", version, [])
            for path in (
                f"hello_{version}.dsc",
                f"hello_{version}.debian.tar.xz",
                "hello_1.0.orig.tar.xz",
            ):
                contents = f"data for {path}".encode()
                file = self.playground.create_file(contents)
                FileInArtifact.objects.create(
                    artifact=artifact, path=path, file=file
                )
                path_digests.append((path, file.hash_digest.hex()))
            source_package_artifacts.append(artifact)

        collection_items = [
            self.manager.add_artifact(
                artifact,
                user=self.user,
                variables={"component": "main", "section": "devel"},
            )
            for artifact in source_package_artifacts
        ]

        for collection_item in collection_items:
            collection_item.refresh_from_db()
        self.assertEqual(
            [item.name for item in collection_items],
            ["hello_1.0-1", "hello_1.0-2"],
        )
        self.assertEqual(
            [item.data for item in collection_items],
            [
                {
                    "package": "hello",
                    "version": version,
                    "component": "main",
                    "section": "devel",
                }
                for version in ("1.0-1", "1.0-2")
            ],
        )
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": collection_items[item_index].id,
                    "constraint_name": "pool-file",
                    "key": f"pool/main/h/hello/{path}",
                    "value": f"sha256:{digest}",
                }
                for item_index, (path, digest) in zip(
                    (0, 0, 0, 1, 1, 1), path_digests
                )
            ],
        )

    def test_do_add_artifact_overlapping_files_different_contents(self) -> None:
        """`do_add_artifact` forbids same paths with different contents."""
        source_package_artifacts: list[Artifact] = []
        for version in ("1.0-1", "1.0-2"):
            artifact = self.create_source_package("hello", version, [])
            for path in (
                f"hello_{version}.dsc",
                f"hello_{version}.debian.tar.xz",
                "hello_1.0.orig.tar.xz",
            ):
                contents = f"data for {path} at {version}".encode()
                file = self.playground.create_file(contents)
                FileInArtifact.objects.create(
                    artifact=artifact, path=path, file=file
                )
            source_package_artifacts.append(artifact)

        self.manager.add_artifact(
            source_package_artifacts[0],
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )
        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitemmatchconstraint_match_value"
        ):
            self.manager.add_artifact(
                source_package_artifacts[1],
                user=self.user,
                variables={"component": "main", "section": "devel"},
            )

    def test_do_remove_item_artifact(self) -> None:
        """`do_remove_item` removes an artifact item."""
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file

        collection_item = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )
        self.manager.remove_item(collection_item, user=self.user)

        collection_item.refresh_from_db()

        # The artifact is not removed yet (retention period applies)
        self.assertEqual(collection_item.artifact, source_package_artifact)

        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsInstance(collection_item.removed_at, datetime)

        # Since `may_reuse_versions` defaults to False, the constraints on
        # matching pool file contents are retained.
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": collection_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.dsc",
                    "value": f"sha256:{dsc_file.hash_digest.hex()}",
                },
                {
                    "collection_item_id": collection_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.tar.xz",
                    "value": f"sha256:{tar_file.hash_digest.hex()}",
                },
            ],
        )

    def test_do_remove_item_artifact_reuse_versions(self) -> None:
        """If `may_reuse_versions=True`, versions may be reused."""
        self.collection.data["may_reuse_versions"] = True
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )

        collection_item = self.manager.add_artifact(
            source_package_artifact,
            user=self.user,
            variables={"component": "main", "section": "devel"},
        )
        self.manager.remove_item(collection_item, user=self.user)

        collection_item.refresh_from_db()

        # The artifact is not removed yet (retention period applies)
        self.assertEqual(collection_item.artifact, source_package_artifact)

        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsInstance(collection_item.removed_at, datetime)

        # Constraints on matching pool file contents are gone.
        self.assert_collection_has_constraints(self.collection, [])

    def test_lookup_unexpected_format_raise_lookup_error(self) -> None:
        """`lookup` raises `LookupError`: invalid format."""
        with self.assertRaisesRegex(
            LookupError, '^Unexpected lookup format: "binary:hello"$'
        ):
            self.manager.lookup("binary:hello")

    def test_lookup_return_none(self) -> None:
        """`lookup` returns None if there are no matches."""
        self.assertIsNone(self.manager.lookup("source:hello"))
        self.assertIsNone(self.manager.lookup("binary:hello_amd64"))
        self.assertIsNone(self.manager.lookup("index:missing"))
        self.assertIsNone(self.manager.lookup("name:nonexistent"))

    def test_lookup_source(self) -> None:
        """`lookup` returns a matching source package item."""
        items: list[CollectionItem] = []
        for name, version in (
            ("hello", "1.0"),
            ("hello", "1.1"),
            ("base-files", "1.0"),
        ):
            source_package_artifact = self.create_source_package(
                name, version, []
            )
            items.append(
                self.manager.add_artifact(
                    source_package_artifact,
                    user=self.user,
                    variables={"component": "main", "section": "devel"},
                )
            )

        source_package_artifact = self.create_source_package("hello", "1.2", [])
        items.append(
            self.manager.add_artifact(
                source_package_artifact,
                user=self.user,
                variables={"component": "main", "section": "devel"},
            )
        )
        self.manager.remove_item(items[-1], user=self.user)

        # CollectionItem of type BARE should not exist in this collection
        # (the manager does not allow adding it).  Add one to ensure that it
        # is filtered out.
        CollectionItem.objects.create(
            child_type=CollectionItem.Types.BARE,
            created_by_user=self.user,
            parent_collection=self.collection,
            category=ArtifactCategory.SOURCE_PACKAGE,
            name="something",
            data={"package": "hello", "version": "1.0"},
        )

        # items[0] (hello_1.0) and items[1] (hello_1.1) both match, but
        # items[1] has a higher version.  items[3] (hello_1.2) has been
        # removed.
        self.assertEqual(self.manager.lookup("source:hello"), items[1])

        self.assertEqual(
            self.manager.lookup("source-version:hello_1.0"), items[0]
        )
        self.assertEqual(
            self.manager.lookup("source-version:hello_1.1"), items[1]
        )
        self.assertIsNone(self.manager.lookup("source-version:hello_1.2"))

        self.assertEqual(self.manager.lookup("name:hello_1.0"), items[0])
        self.assertEqual(self.manager.lookup("name:hello_1.1"), items[1])
        self.assertEqual(self.manager.lookup("name:base-files_1.0"), items[2])
        self.assertIsNone(self.manager.lookup("name:hello_1.2"))

    def test_lookup_binary(self) -> None:
        """`lookup` returns a matching binary package item."""
        items: list[CollectionItem] = []
        for srcpkg_name, srcpkg_version, name, version, architecture in (
            ("hello", "1.0", "libhello1", "1.0", "amd64"),
            ("hello", "1.0", "libhello1", "1.0", "s390x"),
            ("hello", "1.0", "libhello-doc", "1.0", "all"),
            ("hello", "1.1", "libhello1", "1.1", "amd64"),
            ("base-files", "1.0", "base-files", "1:1.0", "amd64"),
        ):
            binary_package_artifact = self.create_binary_package(
                srcpkg_name, srcpkg_version, name, version, architecture, []
            )
            items.append(
                self.manager.add_artifact(
                    binary_package_artifact,
                    user=self.user,
                    variables={
                        "component": "main",
                        "section": "devel",
                        "priority": "optional",
                    },
                )
            )

        binary_package_artifact = self.create_binary_package(
            "hello", "1.2", "libhello1", "1.2", "amd64", []
        )
        items.append(
            self.manager.add_artifact(
                binary_package_artifact,
                user=self.user,
                variables={
                    "component": "main",
                    "section": "devel",
                    "priority": "optional",
                },
            )
        )
        self.manager.remove_item(items[-1], user=self.user)

        # CollectionItem of type BARE should not exist in this collection
        # (the manager does not allow adding it).  Add one to ensure that it
        # is filtered out.
        CollectionItem.objects.create(
            child_type=CollectionItem.Types.BARE,
            created_by_user=self.user,
            parent_collection=self.collection,
            category=ArtifactCategory.BINARY_PACKAGE,
            name="something",
            data={
                "package": "libhello1",
                "version": "1.0",
                "architecture": "amd64",
            },
        )

        # items[0] (libhello1_1.0_amd64) and items[3] (libhello_1.1_amd64)
        # both match, but items[3] has a higher version.  items[5]
        # (libhello1_1.2_amd64) has been removed.
        self.assertEqual(
            self.manager.lookup("binary:libhello1_amd64"), items[3]
        )

        self.assertEqual(
            self.manager.lookup("binary-version:libhello1_1.0_amd64"), items[0]
        )
        self.assertEqual(
            self.manager.lookup("binary-version:libhello1_1.0_s390x"), items[1]
        )
        self.assertEqual(
            self.manager.lookup("binary-version:libhello-doc_1.0_all"), items[2]
        )
        self.assertEqual(
            self.manager.lookup("binary-version:libhello-doc_1.0_amd64"),
            items[2],
        )
        self.assertEqual(
            self.manager.lookup("binary-version:libhello-doc_1.0_s390x"),
            items[2],
        )
        self.assertEqual(
            self.manager.lookup("binary-version:libhello1_1.1_amd64"), items[3]
        )
        self.assertIsNone(
            self.manager.lookup("binary-version:libhello1_1.1_s390x")
        )
        self.assertIsNone(
            self.manager.lookup("binary-version:libhello1_1.2_amd64")
        )

        self.assertEqual(
            self.manager.lookup("name:libhello1_1.0_amd64"), items[0]
        )
        self.assertEqual(
            self.manager.lookup("name:libhello1_1.0_s390x"), items[1]
        )
        self.assertEqual(
            self.manager.lookup("name:libhello-doc_1.0_all"), items[2]
        )
        self.assertEqual(
            self.manager.lookup("name:libhello1_1.1_amd64"), items[3]
        )
        self.assertEqual(
            self.manager.lookup("name:base-files_1:1.0_amd64"), items[4]
        )
        self.assertIsNone(self.manager.lookup("name:libhello1_1.2_amd64"))

    def test_lookup_repository_index(self) -> None:
        """``lookup`` returns a matching repository index."""
        items: list[CollectionItem] = []
        for path in (
            "main/source/Sources.xz",
            "main/binary-amd64/Packages.xz",
            "Release",
        ):
            index_artifact = self.create_repository_index(PurePath(path).name)
            items.append(
                self.manager.add_artifact(
                    index_artifact, user=self.user, variables={"path": path}
                )
            )

        index_artifact = self.create_repository_index("Packages.xz")
        items.append(
            self.manager.add_artifact(
                index_artifact,
                user=self.user,
                variables={"path": "main/binary-amd64/Packages.xz"},
                replace=True,
            )
        )

        # CollectionItem of type BARE should not exist in this collection
        # (the manager does not allow adding it).  Add one to ensure that it
        # is filtered out.
        CollectionItem.objects.create(
            child_type=CollectionItem.Types.BARE,
            created_by_user=self.user,
            parent_collection=self.collection,
            category=ArtifactCategory.REPOSITORY_INDEX,
            name="something",
            data={"path": "Release"},
        )

        self.assertEqual(
            self.manager.lookup("index:main/source/Sources.xz"), items[0]
        )
        # items[1] and items[3] would both match, but only items[3] is
        # active.
        self.assertEqual(
            self.manager.lookup("index:main/binary-amd64/Packages.xz"), items[3]
        )
        self.assertEqual(self.manager.lookup("index:Release"), items[2])
        self.assertIsNone(self.manager.lookup("index:missing"))
