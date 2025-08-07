# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for lookups of items in collections."""

from collections.abc import Iterable, Sequence
from typing import Any, ClassVar, assert_never
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebusinePromise,
)
from debusine.client.models import LookupChildType
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    Group,
    User,
    WorkflowTemplate,
    Workspace,
    default_workspace,
)
from debusine.server.collections.debian_suite import DebianSuiteManager
from debusine.server.collections.lookup import (
    LookupResult,
    lookup_multiple,
    lookup_single,
    reconstruct_lookup,
)
from debusine.server.collections.tests.test_base import TestManager
from debusine.tasks.models import LookupMultiple, LookupSingle
from debusine.test.django import TestCase


class LookupMixin(TestCase):
    """Helpers for lookup tests."""

    user: ClassVar[User]
    owners: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.owners = cls.playground.create_group(
            name="Owners", users=[cls.user]
        )

    def create_artifact_item(
        self,
        parent_collection: Collection,
        name: str,
        *,
        category: ArtifactCategory = ArtifactCategory.TEST,
        artifact: Artifact | None = None,
        data: dict[str, Any] | None = None,
    ) -> CollectionItem:
        """Create a collection item holding an artifact."""
        return CollectionItem.objects.create(
            parent_collection=parent_collection,
            name=name,
            child_type=CollectionItem.Types.ARTIFACT,
            category=category,
            artifact=(
                artifact
                or self.playground.create_artifact(category=category)[0]
            ),
            data=data or {},
            created_by_user=self.user,
        )

    def create_collection_item(
        self,
        parent_collection: Collection,
        name: str,
        *,
        category: CollectionCategory = CollectionCategory.TEST,
        collection: Collection | None = None,
        data: dict[str, Any] | None = None,
    ) -> CollectionItem:
        """Create a collection item holding a collection."""
        return CollectionItem.objects.create(
            parent_collection=parent_collection,
            name=name,
            child_type=CollectionItem.Types.COLLECTION,
            category=category,
            collection=(
                collection or self.playground.create_collection(name, category)
            ),
            data=data or {},
            created_by_user=self.user,
        )

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
        self, name: str, version: str, architecture: str
    ) -> Artifact:
        """Create a minimal `debian:binary-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": name,
                "srcpkg_version": version,
                "deb_fields": {
                    "Package": name,
                    "Version": version,
                    "Architecture": architecture,
                },
                "deb_control_files": [],
            },
        )
        return artifact

    @staticmethod
    def make_result(
        obj: Artifact | Collection | CollectionItem,
    ) -> LookupResult:
        """Make a LookupResult from an underlying object."""
        match obj:
            case Artifact():
                return LookupResult(
                    result_type=CollectionItem.Types.ARTIFACT, artifact=obj
                )
            case Collection():
                return LookupResult(
                    result_type=CollectionItem.Types.COLLECTION, collection=obj
                )
            case CollectionItem():
                return LookupResult(
                    result_type=CollectionItem.Types(obj.child_type),
                    collection_item=obj,
                    artifact=obj.artifact,
                    collection=obj.collection,
                )
            case _ as unreachable:
                assert_never(unreachable)


class LookupSingleTests(LookupMixin, TestCase):
    """Tests for lookup_single."""

    def assert_lookup_equal(
        self,
        lookup: int | str,
        expected: Artifact | Collection | CollectionItem,
        *,
        user: User | bool | AnonymousUser = True,
        workspace: Workspace | None = None,
        **kwargs: Any,
    ) -> None:
        """Assert that a lookup's result is as expected."""
        if user is True:
            user = self.user
        elif user is False:
            user = AnonymousUser()

        self.assertEqual(
            lookup_single(
                lookup, workspace or default_workspace(), user=user, **kwargs
            ),
            self.make_result(expected),
        )

    def assert_lookup_fails(
        self,
        lookup: int | str,
        *,
        user: User | bool | AnonymousUser = True,
        workspace: Workspace | None = None,
        **kwargs: Any,
    ) -> None:
        """Assert that a lookup fails."""
        if user is True:
            user = self.user
        elif user is False:
            user = AnonymousUser()

        with self.assertRaisesRegex(
            KeyError, f"'{lookup}' does not exist or is hidden"
        ):
            lookup_single(
                lookup, workspace or default_workspace(), user=user, **kwargs
            )

    def test_empty_lookup(self) -> None:
        """An empty lookup raises LookupError."""
        with self.assertRaisesRegex(LookupError, "Empty lookup"):
            lookup_single("", default_workspace(), user=AnonymousUser())

    @context.disable_permission_checks()
    def test_specific_artifact(self) -> None:
        """`nnn@artifacts` finds a specific artifact."""
        artifacts = [self.playground.create_artifact()[0] for _ in range(3)]

        for i, artifact in enumerate(artifacts):
            lookup = f"{artifact.id}@artifacts"
            with self.subTest(lookup=lookup):
                self.assert_lookup_equal(lookup, artifact)

    @context.disable_permission_checks()
    def test_specific_artifact_integer(self) -> None:
        """Integer lookups find a specific artifact."""
        artifacts = [self.playground.create_artifact()[0] for _ in range(3)]

        for i, artifact in enumerate(artifacts):
            with self.subTest(artifact=artifact):
                self.assert_lookup_equal(
                    artifact.id,
                    artifact,
                    expect_type=LookupChildType.ARTIFACT,
                )
                with self.assertRaisesRegex(
                    LookupError,
                    "Integer lookups only work in contexts that expect an "
                    "artifact or a collection",
                ):
                    lookup_single(
                        artifact.id, default_workspace(), user=AnonymousUser()
                    )

    def test_specific_artifact_restricts_workspace(self) -> None:
        """`nnn@artifacts` requires the given workspace or a public one."""
        with context.disable_permission_checks():
            private_workspaces = []
            for i in range(2):
                workspace = self.playground.create_workspace(name=f"private{i}")
                self.owners.assign_role(workspace, "owner")
                private_workspaces.append(workspace)
            public_workspace = self.playground.create_workspace(
                name="public", public=True
            )
            artifacts = [
                self.playground.create_artifact(workspace=workspace)[0]
                for workspace in private_workspaces + [public_workspace]
            ]

        for artifact, workspace, visible in (
            (artifacts[0], private_workspaces[0], True),
            (artifacts[0], private_workspaces[1], False),
            (artifacts[0], public_workspace, False),
            (artifacts[1], private_workspaces[0], False),
            (artifacts[1], private_workspaces[1], True),
            (artifacts[1], public_workspace, False),
            (artifacts[2], private_workspaces[0], True),
            (artifacts[2], private_workspaces[1], True),
            (artifacts[2], public_workspace, True),
        ):
            lookup = f"{artifact.id}@artifacts"
            with self.subTest(lookup=lookup, workspace=workspace):
                if visible:
                    self.assert_lookup_equal(
                        lookup, artifact, workspace=workspace
                    )
                else:
                    with self.assertRaisesRegex(
                        KeyError, f"'{lookup}' does not exist or is hidden"
                    ):
                        lookup_single(lookup, workspace, user=AnonymousUser())

    def test_specific_collection(self) -> None:
        """`nnn@collections` finds a specific collection."""
        collections = [
            self.playground.create_collection(str(i), CollectionCategory.TEST)
            for i in range(3)
        ]

        for i, collection in enumerate(collections):
            lookup = f"{collection.id}@collections"
            with self.subTest(lookup=lookup):
                self.assert_lookup_equal(lookup, collection)

    def test_specific_collection_integer(self) -> None:
        """Integer lookups find a specific collection."""
        collections = [
            self.playground.create_collection(str(i), CollectionCategory.TEST)
            for i in range(3)
        ]

        for i, collection in enumerate(collections):
            with self.subTest(collection=collection):
                self.assert_lookup_equal(
                    collection.id,
                    collection,
                    expect_type=LookupChildType.COLLECTION,
                )
                with self.assertRaisesRegex(
                    LookupError,
                    "Integer lookups only work in contexts that expect an "
                    "artifact or a collection",
                ):
                    lookup_single(
                        collection.id, default_workspace(), user=AnonymousUser()
                    )

    def test_specific_collection_restricts_workspace(self) -> None:
        """`nnn@collections` requires the given workspace or a public one."""
        with context.disable_permission_checks():
            private_workspaces = []
            for i in range(2):
                workspace = self.playground.create_workspace(name=f"private{i}")
                self.owners.assign_role(workspace, "owner")
                private_workspaces.append(workspace)
            public_workspace = self.playground.create_workspace(
                name="public", public=True
            )
        collections = [
            self.playground.create_collection(
                str(i), CollectionCategory.TEST, workspace=workspace
            )
            for i, workspace in enumerate(
                private_workspaces + [public_workspace]
            )
        ]

        for collection, workspace, visible in (
            (collections[0], private_workspaces[0], True),
            (collections[0], private_workspaces[1], False),
            (collections[0], public_workspace, False),
            (collections[1], private_workspaces[0], False),
            (collections[1], private_workspaces[1], True),
            (collections[1], public_workspace, False),
            (collections[2], private_workspaces[0], True),
            (collections[2], private_workspaces[1], True),
            (collections[2], public_workspace, True),
        ):
            lookup = f"{collection.id}@collections"
            with self.subTest(lookup=lookup, workspace=workspace):
                if visible:
                    self.assert_lookup_equal(
                        lookup, collection, workspace=workspace
                    )
                else:
                    with self.assertRaisesRegex(
                        KeyError, f"'{lookup}' does not exist or is hidden"
                    ):
                        lookup_single(lookup, workspace, user=AnonymousUser())

    def test_user_restrictions(self) -> None:
        """`name@collections` respects user restrictions."""
        with context.disable_permission_checks():
            wpublic = self.playground.create_workspace(
                name="public", public=True
            )
            cpublic = self.playground.create_collection(
                "test", CollectionCategory.TEST, workspace=wpublic
            )
            wprivate = self.playground.create_workspace(
                name="private", public=False
            )
            self.owners.assign_role(wprivate, "owner")
            cprivate = self.playground.create_collection(
                "test", CollectionCategory.TEST, workspace=wprivate
            )
            wstart = self.playground.create_workspace(name="start", public=True)

        lookup = f"test@{CollectionCategory.TEST}"

        self.assert_lookup_fails(lookup, workspace=wstart)

        # Lookups in the workspace itself do check restrictions
        self.assert_lookup_equal(lookup, cpublic, user=False, workspace=wpublic)
        self.assert_lookup_equal(
            lookup, cpublic, user=self.user, workspace=wpublic
        )
        self.assert_lookup_fails(lookup, user=False, workspace=wprivate)
        self.assert_lookup_equal(
            lookup, cprivate, user=self.user, workspace=wprivate
        )

        # Inheritance chain is always followed for public datasets
        wstart.set_inheritance([wpublic])
        self.assert_lookup_equal(
            lookup, cpublic, user=AnonymousUser(), workspace=wstart
        )
        self.assert_lookup_equal(
            lookup, cpublic, user=self.user, workspace=wstart
        )

        # Inheritance chain on private datasets is followed only if logged in
        wstart.set_inheritance([wprivate])
        self.assert_lookup_fails(lookup, user=AnonymousUser(), workspace=wstart)
        self.assert_lookup_equal(
            lookup, cprivate, user=self.user, workspace=wstart
        )

        # Inheritance chain skips private datasets but can see public ones
        wstart.set_inheritance([wprivate, wpublic])
        self.assert_lookup_equal(
            lookup, cpublic, user=AnonymousUser(), workspace=wstart
        )
        self.assert_lookup_equal(
            lookup, cprivate, user=self.user, workspace=wstart
        )

    def test_internal_collection_not_in_workflow(self) -> None:
        """`internal@collections` is only valid in a workflow context."""
        with self.assertRaisesRegex(
            LookupError,
            "internal@collections is only valid in the context of a workflow",
        ):
            lookup_single(
                "internal@collections",
                default_workspace(),
                user=AnonymousUser(),
            )

    def test_internal_collection(self) -> None:
        """`internal@collections` works in a workflow context."""
        with context.disable_permission_checks():
            template = WorkflowTemplate.objects.create(
                name="test", workspace=default_workspace(), task_name="noop"
            )
            workflow_root = self.playground.create_workflow(
                template, task_data={}
            )
            self.playground.create_collection("test", CollectionCategory.TEST)

        assert workflow_root.internal_collection is not None
        self.assert_lookup_equal(
            "internal@collections",
            workflow_root.internal_collection,
            workflow_root=workflow_root,
        )

    def test_explicit_category(self) -> None:
        """Collections can be looked up using an explicit category."""
        collection = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE
        )
        self.playground.create_collection("trixie", CollectionCategory.SUITE)
        self.playground.create_collection("debian", CollectionCategory.TEST)

        self.assert_lookup_equal(
            f"bookworm@{CollectionCategory.SUITE}", collection
        )

    def test_explicit_category_restricts_workspace(self) -> None:
        """Collection lookups require the given workspace or a public one."""
        with context.disable_permission_checks():
            private_workspaces: list[Workspace] = []
            for i in range(2):
                workspace = self.playground.create_workspace(name=f"private{i}")
                self.owners.assign_role(workspace, "owner")
                private_workspaces.append(workspace)
            public_workspace = self.playground.create_workspace(
                name="public", public=True
            )
        collections = [
            self.playground.create_collection(
                workspace.name, CollectionCategory.TEST, workspace=workspace
            )
            for workspace in private_workspaces + [public_workspace]
        ]
        for workspace in private_workspaces:
            workspace.set_inheritance([public_workspace])

        for collection, workspace, visible in (
            (collections[0], private_workspaces[0], True),
            (collections[0], private_workspaces[1], False),
            (collections[0], public_workspace, False),
            (collections[1], private_workspaces[0], False),
            (collections[1], private_workspaces[1], True),
            (collections[1], public_workspace, False),
            (collections[2], private_workspaces[0], True),
            (collections[2], private_workspaces[1], True),
            (collections[2], public_workspace, True),
        ):
            lookup = f"{collection.name}@{CollectionCategory.TEST}"
            with self.subTest(lookup=lookup, workspace=workspace):
                if visible:
                    self.assert_lookup_equal(
                        lookup, collection, workspace=workspace
                    )
                else:
                    with self.assertRaisesRegex(
                        KeyError, f"'{lookup}' does not exist or is hidden"
                    ):
                        lookup_single(lookup, workspace, user=AnonymousUser())

    def test_implicit_category(self) -> None:
        """Collections can be looked up using an implicit category."""
        collection = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE
        )
        self.playground.create_collection("trixie", CollectionCategory.SUITE)
        self.playground.create_collection("bookworm", CollectionCategory.TEST)

        self.assert_lookup_equal(
            "bookworm", collection, default_category=CollectionCategory.SUITE
        )

    def test_no_default_category(self) -> None:
        """An initial segment with no `@` requires a default category."""
        with self.assertRaisesRegex(
            LookupError,
            "'debian' does not specify a category and the context does not "
            "supply a default",
        ):
            lookup_single("debian", default_workspace(), user=self.user)

    @context.disable_permission_checks()
    def test_collection_member(self) -> None:
        """Looking up collection members works."""
        self.playground.create_collection("bullseye", CollectionCategory.SUITE)
        bookworm = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE
        )
        trixie = self.playground.create_collection(
            "trixie", CollectionCategory.SUITE
        )
        src1 = self.create_source_package("src1", "1.0")
        src2 = self.create_source_package("src2", "2.0")
        items = []
        for suite, source_package_artifact in (
            (bookworm, src1),
            (bookworm, src2),
            (trixie, src2),
        ):
            items.append(
                DebianSuiteManager(suite).add_source_package(
                    source_package_artifact,
                    user=self.user,
                    component="main",
                    section="devel",
                )
            )

        self.assert_lookup_equal("bookworm@debian:suite/source:src1", items[0])
        self.assert_lookup_equal("bookworm@debian:suite/src2_2.0", items[1])
        with self.assertRaisesRegex(
            KeyError, "'bullseye@debian:suite' has no item 'source:src1'"
        ):
            lookup_single(
                "bullseye@debian:suite/source:src1",
                default_workspace(),
                user=self.user,
            )

    @context.disable_permission_checks()
    def test_no_lookup_through_artifact(self) -> None:
        """Lookup segments cannot continue after an artifact."""
        suite = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE
        )
        source_package_artifact = self.create_source_package("src1", "1.0")
        DebianSuiteManager(suite).add_source_package(
            source_package_artifact,
            user=self.user,
            component="main",
            section="devel",
        )

        with self.assertRaisesRegex(
            LookupError,
            "'bookworm@debian:suite/src1_1.0' is of type 'artifact'"
            " instead of expected 'collection'",
        ):
            lookup_single(
                "bookworm@debian:suite/src1_1.0/foo",
                default_workspace(),
                user=self.user,
            )

    @context.disable_permission_checks()
    def test_expect_type(self) -> None:
        """`expect_type` requires the result to be of that type."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.WORKFLOW_INTERNAL
        )
        bare_item = self.playground.create_bare_data_item(collection, "bare")
        artifact_item = self.create_artifact_item(collection, "artifact")
        collection_item = self.create_collection_item(
            collection, "sub-collection"
        )

        for name, expect_type, expected in (
            ("bare", LookupChildType.BARE, bare_item),
            ("bare", LookupChildType.ARTIFACT, None),
            ("bare", LookupChildType.COLLECTION, None),
            ("bare", LookupChildType.ANY, bare_item),
            ("artifact", LookupChildType.BARE, None),
            ("artifact", LookupChildType.ARTIFACT, artifact_item),
            ("artifact", LookupChildType.COLLECTION, None),
            ("artifact", LookupChildType.ANY, artifact_item),
            ("sub-collection", LookupChildType.BARE, None),
            ("sub-collection", LookupChildType.ARTIFACT, None),
            ("sub-collection", LookupChildType.COLLECTION, collection_item),
            ("sub-collection", LookupChildType.ANY, collection_item),
        ):
            lookup = f"collection@{CollectionCategory.WORKFLOW_INTERNAL}/{name}"
            with self.subTest(lookup=lookup, expect_type=expect_type):
                if expected is not None:
                    self.assert_lookup_equal(
                        lookup, expected, expect_type=expect_type
                    )
                else:
                    with self.assertRaisesRegex(
                        LookupError,
                        f"{lookup!r} is of type '.*'"
                        f" instead of expected {expect_type.name.lower()!r}",
                    ):
                        lookup_single(
                            lookup,
                            default_workspace(),
                            user=self.user,
                            expect_type=expect_type,
                        )


class LookupMultipleTests(LookupMixin, TestCase):
    """Tests for lookup_multiple."""

    def parse_lookup(
        self, data: dict[str, Any] | Sequence[int | str | dict[str, Any]]
    ) -> LookupMultiple:
        """Parse the lookup syntax."""
        return LookupMultiple.parse_obj(data)

    def assert_lookup_equal(
        self,
        lookup: dict[str, Any] | Sequence[int | str | dict[str, Any]],
        expected: Iterable[Artifact | Collection | CollectionItem],
        *,
        workspace: Workspace | None = None,
        **kwargs: Any,
    ) -> None:
        """Assert that a lookup's result is as expected."""
        self.assertEqual(
            lookup_multiple(
                self.parse_lookup(lookup),
                workspace or default_workspace(),
                user=self.user,
                **kwargs,
            ),
            tuple(self.make_result(item) for item in expected),
        )

    def assert_lookup_fails(
        self,
        lookup: dict[str, Any] | Sequence[int | str | dict[str, Any]],
        expected_message: str,
        *,
        workspace: Workspace | None = None,
        **kwargs: Any,
    ) -> None:
        """Assert that a lookup fails."""
        with self.assertRaisesRegex(KeyError, expected_message):
            lookup_multiple(
                self.parse_lookup(lookup),
                workspace or default_workspace(),
                user=self.user,
                **kwargs,
            )

    @context.disable_permission_checks()
    def test_not_collection(self) -> None:
        """The `collection` key does not identify a collection."""
        artifact = self.playground.create_artifact()[0]
        lookup = {"collection": f"{artifact.id}@artifacts"}

        with self.assertRaisesRegex(
            LookupError,
            f"'{artifact.id}@artifacts' is of type 'artifact'"
            f" instead of expected 'collection'",
        ):
            lookup_multiple(
                self.parse_lookup(lookup),
                default_workspace(),
                user=AnonymousUser(),
            )

    @context.disable_permission_checks()
    def test_child_type_bare(self) -> None:
        """Restrict to bare items."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        other_collection = self.playground.create_collection(
            "other-collection", CollectionCategory.TEST
        )
        bare1 = self.playground.create_bare_data_item(collection, "bare1")
        bare2 = self.playground.create_bare_data_item(collection, "bare2")
        self.create_artifact_item(collection, "artifact")
        self.create_collection_item(collection, "sub-collection")
        self.playground.create_bare_data_item(other_collection, "bare3")

        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "child_type": "bare",
            },
            (bare1, bare2),
        )

    @context.disable_permission_checks()
    def test_child_type_artifact(self) -> None:
        """Restrict to artifacts."""
        bookworm = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE
        )
        trixie = self.playground.create_collection(
            "trixie", CollectionCategory.SUITE
        )
        src1 = self.create_source_package("src1", "1.0")
        src2 = self.create_source_package("src2", "2.0")
        self.playground.create_bare_data_item(
            bookworm, "bare1", category=BareDataCategory.TEST
        )
        bare2 = self.playground.create_bare_data_item(
            bookworm,
            "bare2",
            category=BareDataCategory.PROMISE,
            data=DebusinePromise(
                promise_work_request_id=2,
                promise_workflow_id=1,
                promise_category=ArtifactCategory.TEST,
            ),
        )
        self.create_collection_item(bookworm, "collection")
        items = []
        for suite, source_package_artifact in (
            (bookworm, src1),
            (bookworm, src2),
            (trixie, src2),
        ):
            items.append(
                DebianSuiteManager(suite).add_source_package(
                    source_package_artifact,
                    user=self.user,
                    component="main",
                    section="devel",
                )
            )

        self.assert_lookup_equal(
            {"collection": "bookworm@debian:suite", "child_type": "artifact"},
            items[:2],
        )
        self.assert_lookup_equal(
            {
                "collection": "bookworm@debian:suite",
                "child_type": "artifact-or-promise",
            },
            [bare2] + items[:2],
        )
        self.assert_lookup_equal(
            {"collection": "bookworm@debian:suite"}, items[:2]
        )

    @context.disable_permission_checks()
    def test_child_type_collection(self) -> None:
        """Restrict to collections."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        other_collection = self.playground.create_collection(
            "other-collection", CollectionCategory.TEST
        )
        self.playground.create_bare_data_item(collection, "bare1")
        self.create_artifact_item(collection, "artifact")
        collection1 = self.create_collection_item(collection, "collection1")
        collection2 = self.create_collection_item(collection, "collection2")
        self.create_collection_item(other_collection, "collection3")

        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "child_type": "collection",
            },
            (collection1, collection2),
        )

    @context.disable_permission_checks()
    def test_child_type_any(self) -> None:
        """Don't restrict by child type."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        bare = self.playground.create_bare_data_item(collection, "bare")
        artifact = self.create_artifact_item(collection, "artifact")
        sub_collection = self.create_collection_item(
            collection, "sub-collection"
        )

        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "child_type": "any",
            },
            (bare, artifact, sub_collection),
        )

    def test_expect_type(self) -> None:
        """If `expect_type` is given, the child type must match it."""
        self.playground.create_collection("collection", CollectionCategory.TEST)

        for child_type, expect_type, allowed in (
            ("bare", LookupChildType.BARE, True),
            ("bare", LookupChildType.ARTIFACT, False),
            ("bare", LookupChildType.ARTIFACT_OR_PROMISE, True),
            ("bare", LookupChildType.COLLECTION, False),
            ("bare", LookupChildType.ANY, True),
            ("artifact", LookupChildType.BARE, False),
            ("artifact", LookupChildType.ARTIFACT, True),
            ("artifact", LookupChildType.ARTIFACT_OR_PROMISE, True),
            ("artifact", LookupChildType.COLLECTION, False),
            ("artifact", LookupChildType.ANY, True),
            ("collection", LookupChildType.BARE, False),
            ("collection", LookupChildType.ARTIFACT, False),
            ("collection", LookupChildType.ARTIFACT_OR_PROMISE, False),
            ("collection", LookupChildType.COLLECTION, True),
            ("collection", LookupChildType.ANY, True),
            ("any", LookupChildType.BARE, False),
            ("any", LookupChildType.ARTIFACT, False),
            ("any", LookupChildType.ARTIFACT_OR_PROMISE, False),
            ("any", LookupChildType.COLLECTION, False),
            ("any", LookupChildType.ANY, True),
        ):
            lookup = {
                "collection": f"collection@{CollectionCategory.TEST}",
                "child_type": child_type,
            }
            with self.subTest(lookup=lookup, expect_type=expect_type):
                if allowed:
                    self.assert_lookup_equal(
                        lookup, (), expect_type=expect_type
                    )
                else:
                    with self.assertRaisesRegex(
                        LookupError,
                        f"Only lookups for type {expect_type.name.lower()!r} "
                        f"are allowed here",
                    ):
                        lookup_multiple(
                            self.parse_lookup(lookup),
                            default_workspace(),
                            user=self.user,
                            expect_type=expect_type,
                        )

    @context.disable_permission_checks()
    def test_category(self) -> None:
        """Restrict by category."""
        bookworm = self.playground.create_collection(
            "bookworm", CollectionCategory.SUITE
        )
        src1 = self.create_source_package("src1", "1.0")
        src2 = self.create_source_package("src2", "2.0")
        bin1 = self.create_binary_package("bin1", "1.0", "amd64")
        bin2 = self.create_binary_package("bin2", "2.0", "s390x")
        items = []
        for source_package_artifact in (src1, src2):
            items.append(
                DebianSuiteManager(bookworm).add_source_package(
                    source_package_artifact,
                    user=self.user,
                    component="main",
                    section="devel",
                )
            )
        for binary_package_artifact in (bin1, bin2):
            items.append(
                DebianSuiteManager(bookworm).add_binary_package(
                    binary_package_artifact,
                    user=self.user,
                    component="main",
                    section="devel",
                    priority="optional",
                )
            )

        self.assert_lookup_equal(
            {
                "collection": "bookworm@debian:suite",
                "category": ArtifactCategory.SOURCE_PACKAGE,
            },
            items[:2],
        )
        self.assert_lookup_equal(
            {
                "collection": "bookworm@debian:suite",
                "category": ArtifactCategory.BINARY_PACKAGE,
            },
            items[2:],
        )

    @context.disable_permission_checks()
    def test_name_matcher(self) -> None:
        """Restrict by name."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        items = [
            self.create_artifact_item(collection, name)
            for name in ("foo", "foobar", "rebar")
        ]

        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "name": "foo",
            },
            (items[0],),
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "name__startswith": "foo",
            },
            items[:2],
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "name__endswith": "bar",
            },
            items[1:],
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "name__contains": "oo",
            },
            items[:2],
        )

    @context.disable_permission_checks()
    def test_data_matchers(self) -> None:
        """Restrict by data."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        item_specs: list[tuple[str, dict[str, Any]]] = [
            ("foo", {"package": "foo"}),
            ("foobar_1.0", {"package": "foobar", "version": "1.0"}),
            ("rebar", {"package": "rebar", "some_id": 123}),
        ]
        items = [
            self.create_artifact_item(collection, name, data=data)
            for name, data in item_specs
        ]

        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "data__package": "foo",
            },
            (items[0],),
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "data__package__startswith": "foo",
            },
            items[:2],
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "data__package__endswith": "bar",
            },
            items[1:],
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "data__package__contains": "oo",
            },
            items[:2],
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "data__version": "1.0",
            },
            (items[1],),
        )
        self.assert_lookup_equal(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "data__some_id": 123,
            },
            (items[2],),
        )

    @context.disable_permission_checks()
    def test_lookup_filters(self) -> None:
        """Restrict by custom lookup filter."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        item_specs: list[tuple[str, dict[str, Any]]] = [
            ("foo", {"package": "foo"}),
            ("foobar_1.0", {"package": "foobar", "version": "1.0"}),
            ("rebar", {"package": "rebar", "some_id": 123}),
        ]
        items = [
            self.create_artifact_item(collection, name, data=data)
            for name, data in item_specs
        ]
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        workflow_root = self.playground.create_workflow(template, task_data={})

        def do_lookup_filter(
            key: str, value: LookupSingle | LookupMultiple, **kwargs: Any
        ) -> Q:
            self.assertEqual(key, "test")
            self.assertIsInstance(value, LookupSingle)
            return Q(data__package=value)

        with mock.patch.object(
            TestManager, "do_lookup_filter", side_effect=do_lookup_filter
        ) as mock_do_lookup_filter:
            self.assert_lookup_equal(
                {
                    "collection": f"collection@{CollectionCategory.TEST}",
                    "lookup__test": "foo",
                },
                (items[0],),
            )
            mock_do_lookup_filter.assert_called_once_with(
                "test",
                "foo",
                workspace=default_workspace(),
                user=self.user,
                workflow_root=None,
            )
            mock_do_lookup_filter.reset_mock()

            self.assert_lookup_equal(
                {
                    "collection": f"collection@{CollectionCategory.TEST}",
                    "lookup__test": "foobar",
                },
                (items[1],),
                workflow_root=workflow_root,
            )
            mock_do_lookup_filter.assert_called_once_with(
                "test",
                "foobar",
                workspace=default_workspace(),
                user=self.user,
                workflow_root=workflow_root,
            )

    @context.disable_permission_checks()
    def test_alternatives(self) -> None:
        """The list syntax allows specifying several lookups at once."""
        trixie = self.playground.create_collection(
            "trixie", CollectionCategory.SUITE
        )
        items = [
            self.create_artifact_item(trixie, name, data=data)
            for name, data in (
                (
                    "libc6-dev_2.37-15",
                    {"package": "libc6-dev", "version": "2.37-15"},
                ),
                (
                    "libc6-dev_2.37-16",
                    {"package": "libc6-dev", "version": "2.37-16"},
                ),
                (
                    "debhelper_13.15.3_amd64",
                    {
                        "package": "debhelper",
                        "version": "13.15.3",
                        "architecture": "amd64",
                    },
                ),
                (
                    "debhelper_13.15.3_s390x",
                    {
                        "package": "debhelper",
                        "version": "13.15.3",
                        "architecture": "s390x",
                    },
                ),
                ("hello_1.0", {"package": "hello", "version": "1.0"}),
            )
        ]
        loose_artifacts = [
            self.playground.create_artifact()[0] for _ in range(3)
        ]

        self.assert_lookup_equal(
            [
                {
                    "collection": "trixie",
                    "data__package": "libc6-dev",
                    "data__version": "2.37-15",
                },
                {
                    "collection": "trixie",
                    "name__startswith": "debhelper_13.15.3_",
                },
                f"{loose_artifacts[0].id}@artifacts",
                loose_artifacts[1].id,
            ],
            [
                items[0],
                items[2],
                items[3],
                loose_artifacts[0],
                loose_artifacts[1],
            ],
            default_category=CollectionCategory.SUITE,
            expect_type=LookupChildType.ARTIFACT,
        )

    @context.disable_permission_checks()
    def test_artifact_expired(self) -> None:
        """If one artifact has expired, `lookup_multiple` raises LookupError."""
        artifacts = [self.playground.create_artifact()[0] for _ in range(3)]
        artifact_ids = [artifact.id for artifact in artifacts]
        artifacts[0].delete()

        self.assert_lookup_fails(
            artifact_ids,
            f"'{artifact_ids[0]}@artifacts' does not exist or is hidden",
            expect_type=LookupChildType.ARTIFACT,
        )

    @context.disable_permission_checks()
    def test_collection_removed(self) -> None:
        """If one collection is gone, `lookup_multiple` raises LookupError."""
        collections = [
            self.playground.create_collection(
                f"collection{i}", CollectionCategory.TEST
            )
            for i in range(3)
        ]
        items = [
            self.create_artifact_item(collection, "artifact")
            for collection in collections
        ]
        items[0].delete()
        collections[0].delete()

        self.assert_lookup_fails(
            [
                {
                    "collection": f"collection{i}@{CollectionCategory.TEST}",
                    "name": "artifact",
                }
                for i in range(3)
            ],
            f"'collection0@{CollectionCategory.TEST}' does not exist or is "
            f"hidden",
        )

    @context.disable_permission_checks()
    def test_query_count(self) -> None:
        """`lookup_multiple` makes a constant number of DB queries."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST
        )
        artifact_items = [
            self.create_artifact_item(collection, f"artifact{i}")
            for i in range(10)
        ]
        collection_items = [
            self.create_collection_item(collection, f"collection{i}")
            for i in range(10)
        ]

        # Each of these makes 4 queries:
        # 1. look up the workspace (in assert_lookup_equal)
        # *. check if the workspace is visible (shortcutted as it's public)
        # 2. find the containing collection
        # 3. list the set of collection items.
        with self.assertNumQueries(3):
            self.assert_lookup_equal(
                {"collection": f"collection@{CollectionCategory.TEST}"},
                artifact_items,
            )
        with self.assertNumQueries(3):
            self.assert_lookup_equal(
                {
                    "collection": f"collection@{CollectionCategory.TEST}",
                    "child_type": "collection",
                },
                collection_items,
            )


class ReconstructLookupTests(LookupMixin, TestCase):
    """Tests for reconstruct_lookup."""

    def test_collection_item(self) -> None:
        """A result with a collection item returns a lookup for that item."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        item_bare = self.playground.create_bare_data_item(collection, "bare")
        item_artifact = self.create_artifact_item(collection, "artifact")
        item_collection = self.create_collection_item(collection, "collection")
        workflow = self.playground.create_workflow()

        for item, name in (
            (item_bare, "bare"),
            (item_artifact, "artifact"),
            (item_collection, "collection"),
        ):
            self.assertEqual(
                reconstruct_lookup(self.make_result(item)),
                f"{collection.id}@collections/name:{name}",
            )
            self.assertEqual(
                reconstruct_lookup(
                    self.make_result(item), workflow_root=workflow
                ),
                f"{collection.id}@collections/name:{name}",
            )

    def test_collection_item_internal_collection(self) -> None:
        """Collection items in the internal collection are handled specially."""
        workflow = self.playground.create_workflow()
        assert workflow.internal_collection is not None
        item_bare = self.playground.create_bare_data_item(
            workflow.internal_collection, "bare"
        )
        item_artifact = self.create_artifact_item(
            workflow.internal_collection, "artifact"
        )
        item_collection = self.create_collection_item(
            workflow.internal_collection, "collection"
        )

        for item, name in (
            (item_bare, "bare"),
            (item_artifact, "artifact"),
            (item_collection, "collection"),
        ):
            self.assertEqual(
                reconstruct_lookup(
                    self.make_result(item), workflow_root=workflow
                ),
                f"internal@collections/name:{name}",
            )

    def test_artifact(self) -> None:
        """A result with an artifact returns a lookup for that artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )

        self.assertEqual(
            reconstruct_lookup(self.make_result(artifact)),
            f"{artifact.id}@artifacts",
        )

    def test_collection(self) -> None:
        """A result with a collection returns a lookup for that collection."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )

        self.assertEqual(
            reconstruct_lookup(self.make_result(collection)),
            f"{collection.id}@collections",
        )
        self.assertEqual(
            reconstruct_lookup(
                self.make_result(collection),
                workflow_root=self.playground.create_workflow(),
            ),
            f"{collection.id}@collections",
        )

    def test_collection_internal_collection(self) -> None:
        """The internal collection is handled specially."""
        workflow = self.playground.create_workflow()
        assert workflow.internal_collection is not None

        self.assertEqual(
            reconstruct_lookup(
                self.make_result(workflow.internal_collection),
                workflow_root=workflow,
            ),
            "internal@collections",
        )
