# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebianSuiteManager."""

from datetime import datetime
from operator import itemgetter
from typing import Any, ClassVar

from django.contrib.auth import get_user_model

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    CollectionItemMatchConstraint,
    FileInArtifact,
    User,
    Workspace,
    default_workspace,
)
from debusine.server.collections import DebianSuiteManager, ItemAdditionError
from debusine.server.collections.debian_suite import make_pool_filename
from debusine.test.django import TestCase


class MakePoolFilenameTests(TestCase):
    """Tests for make_pool_filename."""

    def test_normal(self) -> None:
        """`make_pool_filename` works for normal source package names."""
        self.assertEqual(
            "pool/main/h/hello/hello_1.0.dsc",
            make_pool_filename("hello", "main", "hello_1.0.dsc"),
        )

    def test_library(self) -> None:
        """`make_pool_filename` works for library source package names."""
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

        cls.manager = DebianSuiteManager(collection=cls.collection)

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

    def assert_collection_has_constraints(
        self,
        collection: Collection,
        expected_constraints: list[dict[str, Any]],
    ) -> None:
        """Assert that a collection item has these constraints."""
        constraints = [
            dict(constraint)
            for constraint in CollectionItemMatchConstraint.objects.filter(
                collection=collection
            ).values()
        ]
        for constraint in constraints:
            del constraint["id"]
        self.assertEqual(
            sorted(constraints, key=itemgetter("collection_item_id", "key")),
            sorted(
                [
                    {"collection_id": collection.id, **constraint}
                    for constraint in expected_constraints
                ],
                key=itemgetter("collection_item_id", "key"),
            ),
        )

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

    @context.disable_permission_checks()
    def test_add_source_package(self) -> None:
        """`add_source_package` adds a source package to the suite."""
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file

        collection_item = self.manager.add_source_package(
            source_package_artifact,
            user=self.user,
            component="main",
            section="devel",
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

    @context.disable_permission_checks()
    def test_add_source_package_wrong_category(self) -> None:
        """`add_source_package` requires `debian:source-package` artifacts."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            r'^add_source_package requires a debian:source-package artifact, '
            r'not "debian:binary-package"$',
        ):
            self.manager.add_source_package(
                artifact, user=self.user, component="main", section="devel"
            )

    @context.disable_permission_checks()
    def test_add_source_package_replace(self) -> None:
        """`add_source_package` can replace an existing artifact."""
        self.collection.data["may_reuse_versions"] = True
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        collection_item = self.manager.add_source_package(
            source_package_artifact,
            user=self.user,
            component="main",
            section="devel",
        )
        source_package_artifact2 = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        collection_item2 = self.manager.add_source_package(
            source_package_artifact2,
            user=self.user,
            component="main",
            section="devel",
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, source_package_artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "hello_1.0")
        self.assertEqual(collection_item2.artifact, source_package_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    @context.disable_permission_checks()
    def test_add_source_package_replace_nonexistent(self) -> None:
        """Replacing a nonexistent source package is allowed."""
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )

        collection_item = self.manager.add_source_package(
            source_package_artifact,
            user=self.user,
            component="main",
            section="devel",
            replace=True,
        )

        self.assertEqual(collection_item.name, "hello_1.0")
        self.assertEqual(collection_item.artifact, source_package_artifact)

    @context.disable_permission_checks()
    def test_add_binary_package(self) -> None:
        """`add_binary_package` adds a binary package to the suite."""
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

        collection_item = self.manager.add_binary_package(
            binary_package_artifact,
            user=self.user,
            component="main",
            section="devel",
            priority="optional",
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

    @context.disable_permission_checks()
    def test_add_binary_package_wrong_category(self) -> None:
        """`add_binary_package` requires `debian:binary-package` artifacts."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            r'^add_binary_package requires a debian:binary-package artifact, '
            r'not "debian:source-package"$',
        ):
            self.manager.add_binary_package(
                artifact,
                user=self.user,
                component="main",
                section="devel",
                priority="optional",
            )

    @context.disable_permission_checks()
    def test_add_binary_package_replace(self) -> None:
        """`add_binary_package` can replace an existing artifact."""
        self.collection.data["may_reuse_versions"] = True
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        collection_item = self.manager.add_binary_package(
            binary_package_artifact,
            user=self.user,
            component="main",
            section="devel",
            priority="optional",
        )
        binary_package_artifact2 = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )
        collection_item2 = self.manager.add_binary_package(
            binary_package_artifact2,
            user=self.user,
            component="main",
            section="devel",
            priority="optional",
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, binary_package_artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "libhello1_1:1.0_amd64")
        self.assertEqual(collection_item2.artifact, binary_package_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    @context.disable_permission_checks()
    def test_add_binary_package_replace_nonexistent(self) -> None:
        """Replacing a nonexistent binary package is allowed."""
        binary_package_artifact = self.create_binary_package(
            "hello",
            "1.0",
            "libhello1",
            "1:1.0",
            "amd64",
            ["libhello1_1.0_amd64.deb", "libhello1-dbgsym_1.0_amd64.deb"],
        )

        collection_item = self.manager.add_binary_package(
            binary_package_artifact,
            user=self.user,
            component="main",
            section="devel",
            priority="optional",
            replace=True,
        )

        self.assertEqual(collection_item.name, "libhello1_1:1.0_amd64")
        self.assertEqual(collection_item.artifact, binary_package_artifact)

    @context.disable_permission_checks()
    def test_do_add_artifact_source_package_requires_component(self) -> None:
        """`do_add_artifact` requires `component` for source packages."""
        source_package_artifact = self.create_source_package("hello", "1.0", [])

        with self.assertRaisesRegex(
            ItemAdditionError, "^Adding to debian:suite requires a component$"
        ):
            self.manager.do_add_artifact(
                source_package_artifact, user=self.user
            )

    @context.disable_permission_checks()
    def test_do_add_artifact_source_package_requires_section(self) -> None:
        """`do_add_artifact` requires `section` for source packages."""
        source_package_artifact = self.create_source_package("hello", "1.0", [])

        with self.assertRaisesRegex(
            ItemAdditionError, "^Adding to debian:suite requires a section$"
        ):
            self.manager.do_add_artifact(
                source_package_artifact,
                user=self.user,
                variables={"component": "main"},
            )

    @context.disable_permission_checks()
    def test_do_add_artifact_binary_package_requires_component(self) -> None:
        """`do_add_artifact` requires `component` for binary packages."""
        binary_package_artifact = self.create_binary_package(
            "hello", "1.0", "hello", "1.0", "amd64", []
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "^Adding to debian:suite requires a component$"
        ):
            self.manager.do_add_artifact(
                binary_package_artifact, user=self.user
            )

    @context.disable_permission_checks()
    def test_do_add_artifact_binary_package_requires_section(self) -> None:
        """`do_add_artifact` requires `section` for binary packages."""
        binary_package_artifact = self.create_binary_package(
            "hello", "1.0", "hello", "1.0", "amd64", []
        )

        with self.assertRaisesRegex(
            ItemAdditionError, "^Adding to debian:suite requires a section$"
        ):
            self.manager.do_add_artifact(
                binary_package_artifact,
                user=self.user,
                variables={"component": "main"},
            )

    @context.disable_permission_checks()
    def test_do_add_artifact_binary_package_requires_priority(self) -> None:
        """`do_add_artifact` requires `priority` for binary packages."""
        binary_package_artifact = self.create_binary_package(
            "hello", "1.0", "hello", "1.0", "amd64", []
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            "^Adding a binary package to debian:suite requires a priority$",
        ):
            self.manager.do_add_artifact(
                binary_package_artifact,
                user=self.user,
                variables={"component": "main", "section": "devel"},
            )

    @context.disable_permission_checks()
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
            self.manager.add_source_package(
                artifact, user=self.user, component="main", section="devel"
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

    @context.disable_permission_checks()
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

        self.manager.add_source_package(
            source_package_artifacts[0],
            user=self.user,
            component="main",
            section="devel",
        )
        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitemmatchconstraint_match_value"
        ):
            self.manager.add_source_package(
                source_package_artifacts[1],
                user=self.user,
                component="main",
                section="devel",
            )

    @context.disable_permission_checks()
    def test_do_remove_artifact(self) -> None:
        """`do_remove_artifact` removes the artifact."""
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file

        collection_item = self.manager.add_source_package(
            source_package_artifact,
            user=self.user,
            component="main",
            section="devel",
        )
        self.manager.remove_artifact(source_package_artifact, user=self.user)

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

    @context.disable_permission_checks()
    def test_do_remove_artifact_reuse_versions(self) -> None:
        """If `may_reuse_versions=False`, versions may be reused."""
        self.collection.data["may_reuse_versions"] = True
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )

        collection_item = self.manager.add_source_package(
            source_package_artifact,
            user=self.user,
            component="main",
            section="devel",
        )
        self.manager.remove_artifact(source_package_artifact, user=self.user)

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
        self.assertIsNone(self.manager.lookup("name:nonexistent"))

    @context.disable_permission_checks()
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
                self.manager.add_source_package(
                    source_package_artifact,
                    user=self.user,
                    component="main",
                    section="devel",
                )
            )

        source_package_artifact = self.create_source_package("hello", "1.2", [])
        items.append(
            self.manager.add_source_package(
                source_package_artifact,
                user=self.user,
                component="main",
                section="devel",
            )
        )
        self.manager.remove_artifact(source_package_artifact, user=self.user)

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

    @context.disable_permission_checks()
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
                self.manager.add_binary_package(
                    binary_package_artifact,
                    user=self.user,
                    component="main",
                    section="devel",
                    priority="optional",
                )
            )

        binary_package_artifact = self.create_binary_package(
            "hello", "1.2", "libhello1", "1.2", "amd64", []
        )
        items.append(
            self.manager.add_binary_package(
                binary_package_artifact,
                user=self.user,
                component="main",
                section="devel",
                priority="optional",
            )
        )
        self.manager.remove_artifact(binary_package_artifact, user=self.user)

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
