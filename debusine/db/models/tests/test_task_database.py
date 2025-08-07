# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for database interaction for worker tasks on the server."""

from typing import ClassVar

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.client.models import RelationType
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    TaskDatabase,
    WorkRequest,
    Workspace,
)
from debusine.tasks.models import LookupMultiple
from debusine.tasks.server import ArtifactInfo
from debusine.test.django import TestCase
from debusine.test.test_utils import create_system_tarball_data


class TaskDatabaseTests(TestCase):
    """Test database interaction in worker tasks."""

    workspace: ClassVar[Workspace]
    work_request: ClassVar[WorkRequest]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.workspace = cls.playground.create_workspace(
            name="test", public=True
        )
        cls.work_request = cls.playground.create_work_request(
            task_name="noop", workspace=cls.workspace
        )

    @context.disable_permission_checks()
    def test_lookup_single_artifact(self) -> None:
        """Look up a single artifact."""
        collection = self.playground.create_collection(
            "debian", CollectionCategory.ENVIRONMENTS, workspace=self.workspace
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data=create_system_tarball_data(
                codename="bookworm", architecture="amd64"
            ),
        )
        collection.manager.add_artifact(
            artifact, user=self.playground.get_default_user()
        )
        task_db = TaskDatabase(self.work_request)

        self.assertEqual(
            task_db.lookup_single_artifact(
                "debian@debian:environments/match:codename=bookworm"
            ),
            ArtifactInfo(
                id=artifact.id, category=artifact.category, data=artifact.data
            ),
        )
        self.assertEqual(
            task_db.lookup_single_artifact(
                "debian/match:codename=bookworm",
                default_category=CollectionCategory.ENVIRONMENTS,
            ),
            ArtifactInfo(
                id=artifact.id, category=artifact.category, data=artifact.data
            ),
        )
        with self.assertRaisesRegex(
            LookupError,
            "'debian' does not specify a category and the context does not "
            "supply a default",
        ):
            task_db.lookup_single_artifact("debian/match:codename=bookworm")

    @context.disable_permission_checks()
    def test_lookup_multiple_artifacts(self) -> None:
        """Look up multiple artifacts."""
        collection = self.playground.create_collection(
            "debian", CollectionCategory.ENVIRONMENTS, workspace=self.workspace
        )
        artifacts: list[Artifact] = []
        for codename in "bookworm", "trixie":
            artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL,
                data=create_system_tarball_data(
                    codename=codename, architecture="amd64"
                ),
            )
            collection.manager.add_artifact(
                artifact, user=self.playground.get_default_user()
            )
            artifacts.append(artifact)
        task_db = TaskDatabase(self.work_request)

        self.assertCountEqual(
            task_db.lookup_multiple_artifacts(
                LookupMultiple.parse_obj(
                    {"collection": "debian@debian:environments"}
                )
            ),
            [
                ArtifactInfo(
                    id=artifact.id,
                    category=artifact.category,
                    data=artifact.data,
                )
                for artifact in artifacts
            ],
        )
        self.assertCountEqual(
            task_db.lookup_multiple_artifacts(
                LookupMultiple.parse_obj({"collection": "debian"}),
                default_category=CollectionCategory.ENVIRONMENTS,
            ),
            [
                ArtifactInfo(
                    id=artifact.id,
                    category=artifact.category,
                    data=artifact.data,
                )
                for artifact in artifacts
            ],
        )
        with self.assertRaisesRegex(
            LookupError,
            "'debian' does not specify a category and the context does not "
            "supply a default",
        ):
            task_db.lookup_multiple_artifacts(
                LookupMultiple.parse_obj({"collection": "debian"})
            )

    @context.disable_permission_checks()
    def test_find_related_artifacts(self) -> None:
        """Find artifacts via relations."""
        upload_artifacts = self.playground.create_upload_artifacts(
            binaries=["libhello1", "hello"]
        )
        task_db = TaskDatabase(self.work_request)

        self.assertCountEqual(
            task_db.find_related_artifacts(
                [upload_artifacts.upload.id], ArtifactCategory.SOURCE_PACKAGE
            ),
            [],
        )
        self.assertCountEqual(
            task_db.find_related_artifacts(
                [upload_artifacts.upload.id],
                ArtifactCategory.SOURCE_PACKAGE,
                relation_type=RelationType.EXTENDS,
            ),
            [
                ArtifactInfo(
                    id=upload_artifacts.source.id,
                    category=upload_artifacts.source.category,
                    data=upload_artifacts.source.data,
                )
            ],
        )
        self.assertCountEqual(
            task_db.find_related_artifacts(
                [upload_artifacts.upload.id], ArtifactCategory.BINARY_PACKAGE
            ),
            [],
        )
        self.assertCountEqual(
            task_db.find_related_artifacts(
                [upload_artifacts.upload.id],
                ArtifactCategory.BINARY_PACKAGE,
                relation_type=RelationType.EXTENDS,
            ),
            [
                ArtifactInfo(
                    id=artifact.id,
                    category=artifact.category,
                    data=artifact.data,
                )
                for artifact in upload_artifacts.binaries
            ],
        )

        self.playground.create_artifact_relation(
            artifact=upload_artifacts.upload,
            target=upload_artifacts.source,
            relation_type=ArtifactRelation.Relations.RELATES_TO,
        )
        self.assertCountEqual(
            task_db.find_related_artifacts(
                [upload_artifacts.upload.id], ArtifactCategory.SOURCE_PACKAGE
            ),
            [
                ArtifactInfo(
                    id=upload_artifacts.source.id,
                    category=upload_artifacts.source.category,
                    data=upload_artifacts.source.data,
                )
            ],
        )

    def test_lookup_single_collection(self) -> None:
        """Look up a single collection."""
        collection = self.playground.create_collection(
            "debian", CollectionCategory.ENVIRONMENTS, workspace=self.workspace
        )
        task_db = TaskDatabase(self.work_request)

        self.assertEqual(
            task_db.lookup_single_collection("debian@debian:environments"),
            collection.id,
        )
        self.assertEqual(
            task_db.lookup_single_collection(
                "debian",
                default_category=CollectionCategory.ENVIRONMENTS,
            ),
            collection.id,
        )
        with self.assertRaisesRegex(
            LookupError,
            "'debian' does not specify a category and the context does not "
            "supply a default",
        ):
            task_db.lookup_single_collection("debian")

    def test_lookup_singleton_collection(self) -> None:
        """Look up a singleton collection."""
        collection = self.playground.create_singleton_collection(
            CollectionCategory.PACKAGE_BUILD_LOGS, workspace=self.workspace
        )
        task_db = TaskDatabase(self.work_request)

        self.assertEqual(
            task_db.lookup_singleton_collection(
                CollectionCategory.PACKAGE_BUILD_LOGS
            ),
            collection.id,
        )
        self.assertIsNone(
            task_db.lookup_singleton_collection(CollectionCategory.TASK_HISTORY)
        )
