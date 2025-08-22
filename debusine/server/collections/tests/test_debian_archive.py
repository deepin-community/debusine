# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebianArchiveManager."""

from datetime import datetime
from pathlib import PurePath
from typing import ClassVar

from django.test import override_settings

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import Artifact, Collection, CollectionItem
from debusine.db.playground import scenarios
from debusine.server.collections import DebianArchiveManager, ItemAdditionError
from debusine.test.django import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class DebianArchiveManagerTests(TestCase):
    """Tests for DebianArchiveManager."""

    scenario = scenarios.DefaultContext()
    collection: ClassVar[Collection]
    manager: ClassVar[DebianArchiveManager]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.collection = cls.scenario.workspace.get_singleton_collection(
            user=cls.scenario.user, category=CollectionCategory.ARCHIVE
        )
        assert isinstance(cls.collection.manager, DebianArchiveManager)
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
            workspace=self.scenario.workspace,
            paths=[path],
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def create_suite(self, name: str) -> Collection:
        """Create a minimal ``debian:suite`` collection."""
        return self.playground.create_collection(
            name, CollectionCategory.SUITE, workspace=self.scenario.workspace
        )

    def test_add_artifact_repository_index(self) -> None:
        """``add_artifact`` can add a repository index to the suite."""
        doc_artifact = self.create_repository_index("social-contract.txt")

        collection_item = self.manager.add_artifact(
            doc_artifact,
            user=self.scenario.user,
            variables={"path": "doc/social-contract.txt"},
        )

        self.assertEqual(collection_item.name, "index:doc/social-contract.txt")
        self.assertEqual(
            collection_item.data, {"path": "doc/social-contract.txt"}
        )

    def test_add_artifact_repository_index_path_under_dists(self) -> None:
        """``add_artifact`` refuses paths not under ``dists/``."""
        artifact = self.create_repository_index("foo")

        with self.assertRaisesRegex(
            ItemAdditionError,
            "Archive-level repository indexes may not have paths under dists/",
        ):
            self.manager.add_artifact(
                artifact,
                user=self.scenario.user,
                variables={"path": "dists/foo"},
            )

    def test_add_artifact_repository_index_violates_constraint(self) -> None:
        """``add_artifact`` reports repository index constraint violations."""
        artifacts = [self.create_repository_index("README") for _ in range(2)]
        self.manager.add_artifact(
            artifacts[0], user=self.scenario.user, variables={"path": "README"}
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            r'^duplicate key value violates unique constraint '
            r'"db_collectionitem_unique_active_name"',
        ):
            self.manager.add_artifact(
                artifacts[1],
                user=self.scenario.user,
                variables={"path": "README"},
            )

    def test_add_artifact_repository_index_replace(self) -> None:
        """``add_artifact`` can replace an existing repository index."""
        readme_artifact = self.create_repository_index("README")
        collection_item = self.manager.add_artifact(
            readme_artifact,
            user=self.scenario.user,
            variables={"path": "README"},
        )
        readme_artifact2 = self.create_repository_index("README")
        collection_item2 = self.manager.add_artifact(
            readme_artifact2,
            user=self.scenario.user,
            variables={"path": "README"},
            replace=True,
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, readme_artifact)
        self.assertEqual(collection_item.removed_by_user, self.scenario.user)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "index:README")
        self.assertEqual(collection_item2.artifact, readme_artifact2)
        self.assertIsNone(collection_item2.removed_at)

    def test_do_add_artifact_requires_path(self) -> None:
        """`do_add_artifact` requires a `path` variable."""
        readme_artifact = self.create_repository_index("README")

        with self.assertRaisesRegex(
            ItemAdditionError,
            '^Adding debian:repository-index to debian:archive requires '
            '"path"$',
        ):
            self.manager.do_add_artifact(
                readme_artifact, user=self.scenario.user
            )

    def test_do_add_collection(self) -> None:
        """``do_add_collection`` can add a suite to the archive."""
        suite = self.create_suite("bookworm")

        collection_item = self.manager.add_collection(
            suite, user=self.scenario.user
        )

        self.assertEqual(collection_item.name, "bookworm")
        self.assertEqual(collection_item.data, {})

    def test_do_add_collection_copies_suite_constraints(self) -> None:
        """``do_add_collection`` copies pool-file constraints from the suite."""
        suite = self.create_suite("bookworm")
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file
        suite_source_item = suite.manager.add_artifact(
            source_package_artifact,
            user=self.scenario.user,
            variables={"component": "main", "section": "devel"},
        )
        expected_constraints = [
            {
                "collection_item_id": suite_source_item.id,
                "constraint_name": "pool-file",
                "key": "pool/main/h/hello/hello_1.0.dsc",
                "value": f"sha256:{dsc_file.hash_digest.hex()}",
            },
            {
                "collection_item_id": suite_source_item.id,
                "constraint_name": "pool-file",
                "key": "pool/main/h/hello/hello_1.0.tar.xz",
                "value": f"sha256:{tar_file.hash_digest.hex()}",
            },
        ]
        self.assert_collection_has_constraints(suite, expected_constraints)
        self.assert_collection_has_constraints(self.collection, [])

        self.manager.add_collection(suite, user=self.scenario.user)

        for collection in (suite, self.collection):
            self.assert_collection_has_constraints(
                collection, expected_constraints
            )

    def test_do_add_collection_replaced_by_ignores_suite_constraints(
        self,
    ) -> None:
        new_suite = self.create_suite("bookworm")
        new_item = self.manager.add_collection(
            new_suite, user=self.scenario.user
        )

        old_suite = self.create_suite("bookworm-old")
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file
        suite_source_item = old_suite.manager.add_artifact(
            source_package_artifact,
            user=self.scenario.user,
            variables={"component": "main", "section": "devel"},
        )
        expected_constraints = [
            {
                "collection_item_id": suite_source_item.id,
                "constraint_name": "pool-file",
                "key": "pool/main/h/hello/hello_1.0.dsc",
                "value": f"sha256:{dsc_file.hash_digest.hex()}",
            },
            {
                "collection_item_id": suite_source_item.id,
                "constraint_name": "pool-file",
                "key": "pool/main/h/hello/hello_1.0.tar.xz",
                "value": f"sha256:{tar_file.hash_digest.hex()}",
            },
        ]
        self.assert_collection_has_constraints(old_suite, expected_constraints)
        self.assert_collection_has_constraints(self.collection, [])

        self.manager.add_collection(
            old_suite, user=self.scenario.user, replaced_by=new_item
        )

        self.assert_collection_has_constraints(self.collection, [])

    def test_do_add_collection_different_workspace(self) -> None:
        other_workspace = self.playground.create_workspace(name="other")
        suite = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE, workspace=other_workspace
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            "Suite must be in the same workspace as the archive",
        ):
            self.manager.add_collection(suite, user=self.scenario.user)

    def test_do_add_collection_violates_constraint(self) -> None:
        """``add_collection`` reports constraint violations."""
        suites = [self.create_suite(name) for name in ("bookworm", "trixie")]
        # do_add_collection generates the item name from the collection
        # name, so this won't happen normally, but force it so that we can
        # test the constraint violation.
        CollectionItem.objects.create_from_collection(
            suites[0],
            parent_collection=self.collection,
            name="trixie",
            data={},
            created_by_user=self.scenario.user,
        )

        with self.assertRaisesRegex(
            ItemAdditionError,
            r'^duplicate key value violates unique constraint '
            r'"db_collectionitem_unique_active_name"',
        ):
            self.manager.add_collection(suites[1], user=self.scenario.user)

    def test_do_remove_item_artifact(self) -> None:
        """``do_remove_item`` removes an artifact."""
        readme_artifact = self.create_repository_index("README")
        collection_item = self.manager.add_artifact(
            readme_artifact,
            user=self.scenario.user,
            variables={"path": "README"},
        )

        self.manager.remove_item(collection_item, user=self.scenario.user)

        self.assertEqual(collection_item.artifact, readme_artifact)
        self.assertEqual(collection_item.removed_by_user, self.scenario.user)
        self.assertIsInstance(collection_item.removed_at, datetime)

    def test_do_remove_item_collection(self) -> None:
        """``do_remove_item`` can remove a suite from the archive."""
        suite = self.create_suite("bookworm")
        suite_item = self.manager.add_collection(suite, user=self.scenario.user)

        self.manager.remove_item(suite_item, user=self.scenario.user)

        self.assertEqual(suite_item.collection, suite)
        self.assertEqual(suite_item.removed_by_user, self.scenario.user)
        self.assertIsInstance(suite_item.removed_at, datetime)

    def test_do_remove_item_collection_retains_suite_constraints(self) -> None:
        """If `may_reuse_versions=False`, versions may not be reused."""
        suite = self.create_suite("bookworm")
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        dsc_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.dsc"
        ).file
        tar_file = source_package_artifact.fileinartifact_set.get(
            path="hello_1.0.tar.xz"
        ).file
        suite_source_item = suite.manager.add_artifact(
            source_package_artifact,
            user=self.scenario.user,
            variables={"component": "main", "section": "devel"},
        )
        suite_item = self.manager.add_collection(suite, user=self.scenario.user)

        self.manager.remove_item(suite_item, user=self.scenario.user)

        # Since `may_reuse_versions` defaults to False, the constraints on
        # matching pool file contents are retained.
        self.assert_collection_has_constraints(
            self.collection,
            [
                {
                    "collection_item_id": suite_source_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.dsc",
                    "value": f"sha256:{dsc_file.hash_digest.hex()}",
                },
                {
                    "collection_item_id": suite_source_item.id,
                    "constraint_name": "pool-file",
                    "key": "pool/main/h/hello/hello_1.0.tar.xz",
                    "value": f"sha256:{tar_file.hash_digest.hex()}",
                },
            ],
        )

    def test_do_remove_item_collection_reuse_versions(self) -> None:
        """If `may_reuse_versions=True`, versions may be reused."""
        self.collection.data["may_reuse_versions"] = True
        suite = self.create_suite("bookworm")
        source_package_artifact = self.create_source_package(
            "hello", "1.0", ["hello_1.0.dsc", "hello_1.0.tar.xz"]
        )
        suite.manager.add_artifact(
            source_package_artifact,
            user=self.scenario.user,
            variables={"component": "main", "section": "devel"},
        )
        suite_item = self.manager.add_collection(suite, user=self.scenario.user)

        self.manager.remove_item(suite_item, user=self.scenario.user)

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
        """`lookup` returns a matching source package item from a suite."""
        suites = [self.create_suite(name) for name in ("bookworm", "trixie")]
        for suite in suites:
            self.manager.add_collection(suite, user=self.scenario.user)

        items: list[CollectionItem] = []
        for suite, name, version in (
            (suites[0], "hello", "1.0"),
            (suites[1], "hello", "1.1"),
            (suites[0], "base-files", "1.0"),
        ):
            source_package_artifact = self.create_source_package(
                name, version, []
            )
            items.append(
                suite.manager.add_artifact(
                    source_package_artifact,
                    user=self.scenario.user,
                    variables={"component": "main", "section": "devel"},
                )
            )

        source_package_artifact = self.create_source_package("hello", "1.2", [])
        items.append(
            suites[1].manager.add_artifact(
                source_package_artifact,
                user=self.scenario.user,
                variables={"component": "main", "section": "devel"},
            )
        )
        suites[1].manager.remove_item(items[-1], user=self.scenario.user)

        # items[0] (bookworm/hello_1.0) and items[1] (trixie/hello_1.1) both
        # match, but items[1] has a higher version.  items[3]
        # (trixie/hello_1.2) has been removed.
        self.assertEqual(self.manager.lookup("source:hello"), items[1])

        self.assertEqual(
            self.manager.lookup("source-version:hello_1.0"), items[0]
        )
        self.assertEqual(
            self.manager.lookup("source-version:hello_1.1"), items[1]
        )
        self.assertIsNone(self.manager.lookup("source-version:hello_1.2"))

    def test_lookup_binary(self) -> None:
        """`lookup` returns a matching binary package item."""
        suites = [self.create_suite(name) for name in ("bookworm", "trixie")]
        for suite in suites:
            self.manager.add_collection(suite, user=self.scenario.user)

        items: list[CollectionItem] = []
        for suite, srcpkg_name, srcpkg_version, name, version, architecture in (
            (suites[0], "hello", "1.0", "libhello1", "1.0", "amd64"),
            (suites[0], "hello", "1.0", "libhello1", "1.0", "s390x"),
            (suites[0], "hello", "1.0", "libhello-doc", "1.0", "all"),
            (suites[1], "hello", "1.1", "libhello1", "1.1", "amd64"),
            (suites[0], "base-files", "1.0", "base-files", "1:1.0", "amd64"),
        ):
            binary_package_artifact = self.create_binary_package(
                srcpkg_name, srcpkg_version, name, version, architecture, []
            )
            items.append(
                suite.manager.add_artifact(
                    binary_package_artifact,
                    user=self.scenario.user,
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
            suites[1].manager.add_artifact(
                binary_package_artifact,
                user=self.scenario.user,
                variables={
                    "component": "main",
                    "section": "devel",
                    "priority": "optional",
                },
            )
        )
        suites[1].manager.remove_item(items[-1], user=self.scenario.user)

        # items[0] (bookworm/libhello1_1.0_amd64) and items[3]
        # (trixie/libhello_1.1_amd64) both match, but items[3] has a higher
        # version.  items[5] (trixie/libhello1_1.2_amd64) has been removed.
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

    def test_lookup_repository_index(self) -> None:
        """``lookup`` returns a matching repository index."""
        suites = [self.create_suite(name) for name in ("bookworm", "trixie")]
        for suite in suites:
            self.manager.add_collection(suite, user=self.scenario.user)

        items: list[CollectionItem] = []
        for suite, path in (
            (suites[0], "main/source/Sources.xz"),
            (suites[0], "main/binary-amd64/Packages.xz"),
            (suites[0], "Release"),
            (suites[1], "main/source/Sources.xz"),
            (suites[1], "main/binary-i386/Packages.xz"),
            (suites[1], "Release"),
        ):
            index_artifact = self.create_repository_index(PurePath(path).name)
            items.append(
                suite.manager.add_artifact(
                    index_artifact,
                    user=self.scenario.user,
                    variables={"path": path},
                )
            )

        index_artifact = self.create_repository_index("Packages.xz")
        items.append(
            suites[1].manager.add_artifact(
                index_artifact,
                user=self.scenario.user,
                variables={"path": "main/binary-amd64/Packages.xz"},
                replace=True,
            )
        )

        readme_artifact = self.create_repository_index("README")
        items.append(
            self.manager.add_artifact(
                readme_artifact,
                user=self.scenario.user,
                variables={"path": "README"},
            )
        )

        self.assertEqual(
            self.manager.lookup("index:dists/bookworm/main/source/Sources.xz"),
            items[0],
        )
        self.assertEqual(
            self.manager.lookup(
                "index:dists/bookworm/main/binary-amd64/Packages.xz"
            ),
            items[1],
        )
        self.assertEqual(
            self.manager.lookup("index:dists/bookworm/Release"), items[2]
        )
        self.assertEqual(
            self.manager.lookup("index:dists/trixie/main/source/Sources.xz"),
            items[3],
        )
        # items[4] and items[6] would both match, but only items[6] is
        # active.
        self.assertEqual(
            self.manager.lookup(
                "index:dists/trixie/main/binary-amd64/Packages.xz"
            ),
            items[6],
        )
        self.assertEqual(
            self.manager.lookup("index:dists/trixie/Release"), items[5]
        )
        self.assertEqual(self.manager.lookup("index:README"), items[7])
        self.assertIsNone(self.manager.lookup("index:missing"))
        self.assertIsNone(self.manager.lookup("index:dists/bookworm/missing"))
