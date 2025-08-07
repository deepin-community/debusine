# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the DebianEnvironmentsManager."""
import datetime

from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import Collection, CollectionItem
from debusine.server.collections import (
    DebianEnvironmentsManager,
    ItemAdditionError,
    ItemRemovalError,
)
from debusine.test.django import TestCase


class DebianEnvironmentsManagerTests(TestCase):
    """Tests for DebianEnvironmentsManager."""

    def setUp(self) -> None:
        """Set up tests."""
        self.user = self.playground.get_default_user()

        self.workspace = self.playground.get_default_workspace()

        self.collection = Collection.objects.create(
            name="Debian",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=self.workspace,
        )

        self.manager = DebianEnvironmentsManager(collection=self.collection)

    def test_init_wrong_collection_category_raise_value_error(self) -> None:
        """Init raise ValueError: wrong collection category."""
        category = "debian:something-else"
        collection = Collection.objects.create(
            name="Name is not used",
            category=category,
            workspace=self.workspace,
        )

        msg = f'^DebianEnvironmentsManager cannot manage "{category}" category$'

        with self.assertRaisesRegex(ValueError, msg):
            DebianEnvironmentsManager(collection)

    @context.disable_permission_checks()
    def test_do_add_artifact(self) -> None:
        """Test do_add_artifact adds the artifact."""
        data = {"codename": "bookworm", "architecture": "amd64"}
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={**data, "with_dev": True},
        )

        collection_item = self.manager.add_artifact(artifact, user=self.user)

        collection_item.refresh_from_db()

        self.assertEqual(collection_item.name, "tarball:bookworm:amd64")
        self.assertEqual(
            collection_item.data, {**data, "variant": None, "backend": None}
        )

    @context.disable_permission_checks()
    def test_do_add_artifact_raise_item_addition_error(self) -> None:
        """Test do_add_artifact raise error: duplicated CollectionItem data."""
        data = {"codename": "bookworm", "architecture": "amd64"}
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL, data=data
        )

        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=data,
        )

        self.manager.add_artifact(artifact_1, user=self.user)

        with self.assertRaisesRegex(
            ItemAdditionError, "db_collectionitem_unique_active_name"
        ):
            self.manager.add_artifact(artifact_2, user=self.user)

    @context.disable_permission_checks()
    def test_do_add_artifact_override_codename(self) -> None:
        """`do_add_artifact` can be told to override the codename."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={"codename": "bookworm", "architecture": "amd64"},
        )

        collection_item = self.manager.add_artifact(
            artifact, user=self.user, variables={"codename": "trixie"}
        )

        collection_item.refresh_from_db()

        self.assertEqual(collection_item.name, "tarball:trixie:amd64")
        self.assertEqual(
            collection_item.data,
            {
                "codename": "trixie",
                "architecture": "amd64",
                "variant": None,
                "backend": None,
            },
        )

    @context.disable_permission_checks()
    def test_do_add_artifact_variant(self) -> None:
        """`do_add_artifact` can be told to set a variant name."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_IMAGE,
            data={"codename": "bookworm", "architecture": "amd64"},
        )

        collection_item = self.manager.add_artifact(
            artifact, user=self.user, variables={"variant": "autopkgtest"}
        )

        collection_item.refresh_from_db()

        self.assertEqual(
            collection_item.name, "image:bookworm:amd64:autopkgtest:"
        )
        self.assertEqual(
            collection_item.data,
            {
                "codename": "bookworm",
                "architecture": "amd64",
                "variant": "autopkgtest",
                "backend": None,
            },
        )

    @context.disable_permission_checks()
    def test_do_add_artifact_backend(self) -> None:
        """`do_add_artifact` can be told to set a backend name."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_IMAGE,
            data={"codename": "bookworm", "architecture": "amd64"},
        )

        collection_item = self.manager.add_artifact(
            artifact, user=self.user, variables={"backend": "unshare"}
        )

        collection_item.refresh_from_db()

        self.assertEqual(collection_item.name, "image:bookworm:amd64::unshare")
        self.assertEqual(
            collection_item.data,
            {
                "codename": "bookworm",
                "architecture": "amd64",
                "variant": None,
                "backend": "unshare",
            },
        )

    @context.disable_permission_checks()
    def test_do_add_artifact_replace(self) -> None:
        """do_add_artifact can replace an existing artifact."""
        workflow = self.playground.create_workflow()

        data = {"codename": "bookworm", "architecture": "amd64"}
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={**data, "with_dev": True},
        )
        collection_item = self.manager.add_artifact(artifact, user=self.user)
        artifact2, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={**data, "with_dev": True},
        )

        collection_item2 = self.manager.add_artifact(
            artifact2, user=self.user, replace=True, workflow=workflow
        )

        collection_item.refresh_from_db()
        self.assertEqual(collection_item.artifact, artifact)
        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertEqual(collection_item.removed_by_workflow, workflow)
        self.assertIsNotNone(collection_item.removed_at)
        self.assertEqual(collection_item2.name, "tarball:bookworm:amd64")
        self.assertEqual(collection_item2.artifact, artifact2)
        self.assertIsNone(collection_item2.removed_at)

    @context.disable_permission_checks()
    def test_do_add_artifact_replace_nonexistent(self) -> None:
        """Replacing a nonexistent artifact is allowed."""
        data = {"codename": "bookworm", "architecture": "amd64"}
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={**data, "with_dev": True},
        )

        collection_item = self.manager.add_artifact(
            artifact, user=self.user, replace=True
        )

        self.assertEqual(collection_item.name, "tarball:bookworm:amd64")
        self.assertEqual(collection_item.artifact, artifact)

    @context.disable_permission_checks()
    def test_do_remove_artifact(self) -> None:
        """Test do_remove_artifact removes the artifact."""
        data = {"codename": "bookworm", "architecture": "amd64"}

        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={**data, "with_dev": True},
        )

        collection_item = self.manager.add_artifact(artifact, user=self.user)

        # Test removing the artifact from the collection
        self.manager.remove_artifact(artifact, user=self.user)

        collection_item.refresh_from_db()

        # The artifact is not removed yet (retention period applies)
        self.assertEqual(collection_item.artifact, artifact)

        self.assertEqual(collection_item.removed_by_user, self.user)
        self.assertIsInstance(collection_item.removed_at, datetime.datetime)

    @context.disable_permission_checks()
    def test_do_remove_collection_raise_item_removal_error(self) -> None:
        """
        Test do_remove_collection raise ItemRemovalError.

        No Collections can be added or removed in
        debian:environments collection.
        """
        msg = (
            f'^Cannot remove collections from '
            f'"{self.manager.COLLECTION_CATEGORY}"$'
        )
        collection = Collection.objects.create(
            name="Some-collection",
            category="Some category",
            workspace=self.workspace,
        )

        with self.assertRaisesRegex(ItemRemovalError, msg):
            self.manager.do_remove_collection(collection, user=self.user)

    def test_do_add_collection_raise_item_addition_error(self) -> None:
        """
        Test do_add_collection raise ItemAdditionError.

        No Collections can be added or removed in
        debian:environments collection.
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

    def test_lookup_not_enough_colons_raise_lookup_error(self) -> None:
        """Test lookup raise LookupError: unexpected number of colons."""
        msg = '^Unexpected lookup format: "a"$'

        with self.assertRaisesRegex(LookupError, msg):
            self.manager.lookup("a")

    def test_lookup_unexpected_format_raise_lookup_error(self) -> None:
        """Test lookup raise LookupError: invalid format."""
        msg = '^Unexpected lookup format: "targz:codename=bookworm"$'

        with self.assertRaisesRegex(LookupError, msg):
            self.manager.lookup("targz:codename=bookworm")

    def test_lookup_return_none(self) -> None:
        """`lookup` returns None if there are no matches."""
        self.assertIsNone(
            self.manager.lookup("match:codename=bookworm:architecture=amd64")
        )
        self.assertIsNone(self.manager.lookup("name:nonexistent"))

    @context.disable_permission_checks()
    def test_lookup_return_matching_collection_item(self) -> None:
        """Test lookup return artifacts."""
        artifact_1, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={"codename": "bookworm", "architecture": "amd64"},
        )
        item_1 = self.manager.add_artifact(artifact_1, user=self.user)

        artifact_2, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={"codename": "bookworm", "architecture": "amd64"},
        )
        item_2 = self.manager.add_artifact(
            artifact_2,
            user=self.user,
            variables={"variant": "buildd", "backend": "unshare"},
        )

        artifact_3, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={"codename": "bookworm", "architecture": "i386"},
        )
        item_3 = self.manager.add_artifact(artifact_3, user=self.user)

        artifact_4, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={"codename": "trixie", "architecture": "amd64"},
        )
        item_4 = self.manager.add_artifact(artifact_4, user=self.user)

        artifact_5, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_IMAGE,
            data={"codename": "bookworm", "architecture": "amd64"},
        )
        item_5 = self.manager.add_artifact(artifact_5, user=self.user)

        # Next one is not returned by lookup because is removed.  (We
        # created it manually because self.manager.add_artifact would fail
        # with a constraint violation before we have a chance to mark the
        # artifact as being removed.)
        artifact_6, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_IMAGE,
            data={"codename": "bookworm", "architecture": "amd64"},
        )
        CollectionItem.objects.create(
            parent_collection=self.collection,
            name="image:bookworm:amd64",
            artifact=artifact_6,
            child_type=CollectionItem.Types.ARTIFACT,
            category=artifact_6.category,
            data={
                "codename": "bookworm",
                "architecture": "amd64",
                "variant": None,
                "backend": None,
            },
            created_by_user=self.user,
            removed_at=timezone.now(),
            removed_by_user=self.user,
        )

        # CollectionItem of type BARE should not exist in this collection
        # (the manager does not allow to add it). Add one to see that is
        # filtered out and not included in the result / cause problems
        CollectionItem.objects.create(
            child_type=CollectionItem.Types.BARE,
            created_by_user=self.user,
            parent_collection=self.collection,
            category="system:tarball",
            name="something",
            data={"codename": "bookworm", "arch": "amd64"},
        )

        # item_[1235] all match, but item_5 was created last.
        self.assertEqual(self.manager.lookup("match:codename=bookworm"), item_5)

        # item_[123] all match, but item_3 was created last.
        self.assertEqual(
            self.manager.lookup("match:format=tarball:codename=bookworm"),
            item_3,
        )

        # item_[124] all match, but item_4 was created last.
        self.assertEqual(
            self.manager.lookup("match:format=tarball:architecture=amd64"),
            item_4,
        )

        # item_1 and item_2 both match, but item_2 was created later.
        self.assertEqual(
            self.manager.lookup(
                "match:format=tarball:codename=bookworm:architecture=amd64"
            ),
            item_2,
        )

        self.assertEqual(
            self.manager.lookup(
                "match:format=tarball:codename=bookworm:architecture=amd64:"
                "variant="
            ),
            item_1,
        )
        self.assertEqual(
            self.manager.lookup(
                "match:format=tarball:codename=bookworm:architecture=amd64:"
                "variant=buildd"
            ),
            item_2,
        )
        self.assertEqual(
            self.manager.lookup(
                "match:format=tarball:codename=bookworm:architecture=amd64:"
                "backend=unshare"
            ),
            item_2,
        )
        self.assertEqual(
            self.manager.lookup(
                "match:format=image:codename=bookworm:architecture=amd64"
            ),
            item_5,
        )

        self.assertEqual(
            self.manager.lookup("name:tarball:bookworm:amd64"), item_1
        )
        self.assertEqual(
            self.manager.lookup("name:tarball:bookworm:amd64:buildd:unshare"),
            item_2,
        )
        self.assertEqual(
            self.manager.lookup("name:image:bookworm:amd64"), item_5
        )
