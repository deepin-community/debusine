# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command delete_expired."""

import contextlib
import io
import threading
from collections.abc import Iterable, Iterator
from datetime import timedelta
from typing import Any
from unittest import mock

from django.db import OperationalError, connection, transaction
from django.db.models.manager import Manager
from django.utils import timezone

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Collection,
    CollectionItem,
    File,
    FileInArtifact,
    FileInStore,
    FileStore,
    FileUpload,
    Group,
    Token,
    WorkRequest,
    WorkflowTemplate,
    default_file_store,
    default_workspace,
)
from debusine.db.playground import scenarios
from debusine.db.tests.utils import (
    RunInParallelTransaction,
    RunInThreadAndCloseDBConnections,
)
from debusine.django.management.tests import call_command
from debusine.server.collections import DebianEnvironmentsManager
from debusine.server.management.commands.delete_expired import (
    DeleteExpiredArtifacts,
    DeleteExpiredEphemeralGroups,
    DeleteExpiredTokens,
    DeleteExpiredWorkRequests,
    DeleteExpiredWorkspaces,
    DeleteOperation,
)
from debusine.test.django import TestCase, TransactionTestCase
from debusine.test.test_utils import tomorrow


class DeleteExpiredCommandTests(TestCase):
    """Tests for delete_expired command."""

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        artifact_1, _ = cls.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()
        artifact_2, _ = cls.playground.create_artifact(expiration_delay=1)
        artifact_2.created_at = timezone.now() - timedelta(days=2)
        artifact_2.save()

        ArtifactRelation.objects.create(
            artifact=artifact_1,
            target=artifact_2,
            type=ArtifactRelation.Relations.BUILT_USING,
        )

    def test_delete_expired(self) -> None:
        """Test delete_expired command delete artifacts."""
        self.assertEqual(Artifact.objects.count(), 2)
        self.assertEqual(ArtifactRelation.objects.count(), 1)

        now = timezone.now()

        func_dfh = mock.Mock()
        func_dm = mock.Mock()
        func_tzn = mock.Mock()
        func_tzn.return_value = now
        with (
            mock.patch(
                "debusine.db.models.collections"
                ".CollectionItemManager.drop_full_history",
                func_dfh,
            ),
            mock.patch(
                "debusine.db.models.collections"
                ".CollectionItemManager.drop_metadata",
                func_dm,
            ),
            mock.patch("django.utils.timezone.now", func_tzn),
        ):
            stdout, stderr, retval = call_command("delete_expired")
        func_dfh.assert_called_once_with(now)
        func_dm.assert_called_once_with(now)

        self.assertEqual(Artifact.objects.count(), 0)
        self.assertEqual(ArtifactRelation.objects.count(), 0)

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(retval, 0)

    def test_delete_expired_dry_run_verbose(self) -> None:
        """Test delete_expired artifacts dry-run: no deletion."""
        artifact_id_1, artifact_id_2 = Artifact.objects.all()

        # Add an artifact with a file
        artifact_id_3, _ = self.playground.create_artifact(
            paths=["README"], create_files=True, expiration_delay=1
        )
        artifact_id_3.created_at = timezone.now() - timedelta(days=2)
        artifact_id_3.save()

        collection_1 = self.playground.create_collection(
            "internal", CollectionCategory.WORKFLOW_INTERNAL
        )
        work_request_1 = self.playground.create_work_request(
            created_at=timezone.now() - timedelta(days=365),
            expiration_delay=timedelta(days=1),
            internal_collection=collection_1,
        )

        create_time = timezone.now() - timedelta(days=30)
        with mock.patch("django.utils.timezone.now", return_value=create_time):
            self.playground.create_workspace(
                name="ws", expiration_delay=timedelta(days=7)
            )

        self.playground.create_worker_activation_token(
            expire_at=timezone.now() - timedelta(days=1)
        )

        num_artifacts = Artifact.objects.count()
        num_artifact_relations = ArtifactRelation.objects.count()
        num_files_in_store = FileInStore.objects.all().count()
        num_work_requests = WorkRequest.objects.all().count()
        num_collections = Collection.objects.all().count()
        num_tokens = Token.objects.count()

        stdout, stderr, retval = call_command(
            "delete_expired", "--dry-run", verbosity=2
        )

        self.assertEqual(
            stdout,
            "dry-run mode: no changes will be made\n"
            + _format_deleted_work_requests([work_request_1], [collection_1])
            + _format_deleted_artifacts(
                {artifact_id_1, artifact_id_2, artifact_id_3}
            )
            + "There were no unused ephemeral groups\n"
            + "Deleting 1 expired workspaces\n"
            + "Deleting 1 expired tokens\n"
            + "dry-run mode: no changes were made\n",
        )

        # Artifacts, ArtifactRelation didn't get deleted
        self.assertEqual(Artifact.objects.count(), num_artifacts)
        self.assertEqual(
            ArtifactRelation.objects.count(), num_artifact_relations
        )

        # Files didn't get deleted
        self.assertEqual(FileInStore.objects.all().count(), num_files_in_store)

        # Work requests and their internal collections didn't get deleted
        self.assertEqual(WorkRequest.objects.all().count(), num_work_requests)
        self.assertEqual(Collection.objects.all().count(), num_collections)

        # Tokens didn't get deleted
        self.assertEqual(Token.objects.count(), num_tokens)

    def test_delete_expired_dry_run(self) -> None:
        """Test --dry-run without verbose does not write to stdout."""
        stdout, stderr, retval = call_command("delete_expired", "--dry-run")

        # dry-run without verbose: no messages are written to stdout.
        self.assertEqual(stdout, "")

    def test_delete_expired_verbose(self) -> None:
        """Test delete_expired verbose: print information."""
        artifact_id_1 = Artifact.objects.all()[0]
        artifact_id_2 = Artifact.objects.all()[1]
        collection_1 = self.playground.create_collection(
            "internal", CollectionCategory.WORKFLOW_INTERNAL
        )
        collection_1.manager.add_artifact(
            artifact_id_1,
            user=self.playground.get_default_user(),
            name="artifact1",
        )
        collection_1.manager.add_artifact(
            artifact_id_2,
            user=self.playground.get_default_user(),
            name="artifact2",
        )
        work_request_1 = self.playground.create_work_request(
            created_at=timezone.now() - timedelta(days=365),
            expiration_delay=timedelta(days=1),
            internal_collection=collection_1,
        )
        create_time = timezone.now() - timedelta(days=30)
        with mock.patch("django.utils.timezone.now", return_value=create_time):
            self.playground.create_workspace(
                name="ws", expiration_delay=timedelta(days=7)
            )
        self.assertEqual(WorkRequest.objects.count(), 1)
        num_collections = Collection.objects.count()
        group = self.playground.create_group(name="test", ephemeral=True)
        token = self.playground.create_worker_activation_token(
            expire_at=timezone.now() - timedelta(days=1)
        )

        stdout, stderr, retval = call_command("delete_expired", verbosity=2)

        self.assertEqual(
            stdout,
            _format_deleted_work_requests([work_request_1], [collection_1])
            + _format_deleted_artifacts({artifact_id_1, artifact_id_2})
            + "Deleting 1 unused ephemeral groups\n"
            + "Deleting 1 expired workspaces\n"
            + "Deleting 1 expired tokens\n",
        )
        self.assertEqual(Artifact.objects.count(), 0)
        self.assertEqual(ArtifactRelation.objects.count(), 0)
        self.assertEqual(WorkRequest.objects.count(), 0)
        self.assertEqual(Collection.objects.count(), num_collections - 1)
        self.assertQuerySetEqual(Group.objects.filter(pk=group.pk), [])
        self.assertQuerySetEqual(Token.objects.filter(pk=token.pk), [])

    def test_delete_expired_verbose_nothing_deleted(self) -> None:
        """Test delete_expired verbose: print no artifact expired."""
        Artifact.objects.update(created_at=tomorrow())
        stdout, stderr, retval = call_command("delete_expired", verbosity=2)

        self.assertEqual(
            stdout,
            _format_deleted_work_requests([], [])
            + "There were no expired artifacts\n"
            + "There were no unused ephemeral groups\n"
            + "There were no expired workspaces\n"
            + "There were no expired tokens\n",
        )


class DeleteExpiredArtifactsTests(TestCase):
    """Tests for the DeleteExpiredArtifacts class."""

    playground_memory_file_store = False

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.operation = DeleteOperation(out=self.out, err=self.err)
        self.operation.initial_time = timezone.now()
        self.delete_expired = DeleteExpiredArtifacts(self.operation)

    def test_mark_to_keep_artifact_no_expiration_date(self) -> None:
        """One artifact, not expired: keep it."""
        artifact, _ = self.playground.create_artifact(expiration_delay=0)

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(), {artifact}
        )

    def test_mark_to_keep_artifact_expires_tomorrow(self) -> None:
        """One artifact, not expired (expires tomorrow): keep it."""
        artifact, _ = self.playground.create_artifact(expiration_delay=1)

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(), {artifact}
        )

    def test_mark_to_keep_only_artifact_is_expired(self) -> None:
        """One artifact, expired yesterday. No artifacts are kept."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()

        collection_parent_1 = Collection.objects.create(
            name="collection-1",
            category="collection-1",
            workspace=default_workspace(),
            retains_artifacts=Collection.RetainsArtifacts.NEVER,
        )
        CollectionItem.objects.create(
            name="test-2",
            category="test-2",
            parent_collection=collection_parent_1,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_1,
            created_by_user=self.playground.get_default_user(),
        )

        self.assertQuerySetEqual(self.delete_expired._mark_to_keep(), set())

    def test_mark_to_keep_only_artifact_is_retained(self) -> None:
        """One artifact, expired yesterday. retains_artifacts is set."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()

        collection_parent_1 = Collection.objects.create(
            name="collection-1",
            category="collection-1",
            workspace=default_workspace(),
        )
        CollectionItem.objects.create(
            name="test-2",
            category="test-2",
            parent_collection=collection_parent_1,
            child_type=CollectionItem.Types.ARTIFACT,
            artifact=artifact_1,
            created_by_user=self.playground.get_default_user(),
        )

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(), {artifact_1}
        )

    def test_mark_to_keep_artifacts_retained_by_workflow(self) -> None:
        """RetainsArtifacts.WORKFLOWS retains while the workflow is active."""
        template = WorkflowTemplate.objects.create(
            name="test", workspace=default_workspace(), task_name="noop"
        )
        collections: dict[WorkRequest.Statuses, Collection] = {}
        artifacts: dict[WorkRequest.Statuses, Artifact] = {}
        for status in WorkRequest.Statuses:
            workflow = self.playground.create_workflow(
                template,
                task_data={},
            )
            workflow.status = status
            workflow.save()
            assert workflow.internal_collection is not None
            collections[status] = workflow.internal_collection
            collections[status].retains_artifacts = (
                Collection.RetainsArtifacts.WORKFLOW
            )
            collections[status].save()
            artifacts[status], _ = self.playground.create_artifact(
                expiration_delay=1
            )
            artifacts[status].created_at = timezone.now() - timedelta(days=2)
            artifacts[status].save()
            collections[status].manager.add_artifact(
                artifacts[status],
                user=self.playground.get_default_user(),
                name=str(status),
            )

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(),
            {
                artifacts[WorkRequest.Statuses.PENDING],
                artifacts[WorkRequest.Statuses.RUNNING],
                artifacts[WorkRequest.Statuses.BLOCKED],
            },
            ordered=False,
        )

    def test_mark_to_keep_all_artifacts_non_expired_or_targeted(self) -> None:
        """Keep both artifacts: the expired is targeted by the not-expired."""
        artifact_expired, _ = self.playground.create_artifact(
            expiration_delay=1
        )
        artifact_expired.created_at = timezone.now() - timedelta(days=2)
        artifact_expired.save()
        artifact_not_expired, _ = self.playground.create_artifact(
            expiration_delay=1
        )

        self.playground.create_artifact_relation(
            artifact_not_expired, artifact_expired
        )

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(),
            {artifact_expired, artifact_not_expired},
            ordered=False,
        )

    def test_mark_to_keep_no_artifact_all_expired_two(self) -> None:
        """Two artifacts (related), both expired."""
        artifact_expired_1, _ = self.playground.create_artifact(
            expiration_delay=1
        )
        artifact_expired_1.created_at = timezone.now() - timedelta(days=2)
        artifact_expired_1.save()
        artifact_expired_2, _ = self.playground.create_artifact(
            expiration_delay=1
        )
        artifact_expired_2.created_at = timezone.now() - timedelta(days=2)
        artifact_expired_2.save()

        self.playground.create_artifact_relation(
            artifact_expired_1, artifact_expired_2
        )

        self.assertQuerySetEqual(self.delete_expired._mark_to_keep(), set())

    def test_mark_to_keep_no_artifact_all_expired_three(self) -> None:
        """All artifacts are expired."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()
        artifact_2, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_2.created_at = timezone.now() - timedelta(days=2)
        artifact_2.save()
        artifact_3, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_3.created_at = timezone.now() - timedelta(days=2)
        artifact_3.save()

        self.playground.create_artifact_relation(artifact_2, artifact_1)
        self.playground.create_artifact_relation(artifact_3, artifact_2)

        self.assertQuerySetEqual(self.delete_expired._mark_to_keep(), set())

    def test_mark_to_keep_two_artifacts_non_expired_and_targeted(self) -> None:
        """Three artifacts, keep two (non-expired and target of non-expired)."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()
        artifact_2, _ = self.playground.create_artifact(expiration_delay=0)
        artifact_3, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_3.created_at = timezone.now() - timedelta(days=2)
        artifact_3.save()

        self.playground.create_artifact_relation(artifact_2, artifact_1)
        self.playground.create_artifact_relation(artifact_3, artifact_2)

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(),
            {artifact_1, artifact_2},
            ordered=False,
        )

    def test_mark_to_keep_isolated(self) -> None:
        """Two expired and one non-expired isolated."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()
        artifact_2, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_2.created_at = timezone.now() - timedelta(days=2)
        artifact_2.save()
        artifact_3, _ = self.playground.create_artifact(expiration_delay=0)

        self.playground.create_artifact_relation(artifact_2, artifact_1)
        self.playground.create_artifact_relation(artifact_1, artifact_2)

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(), {artifact_3}
        )

    def test_mark_to_keep_artifacts_circular_dependency(self) -> None:
        """Artifacts are not expired (keep), have a circular dependency."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=0)
        artifact_2, _ = self.playground.create_artifact(expiration_delay=0)

        self.playground.create_artifact_relation(artifact_2, artifact_1)
        self.playground.create_artifact_relation(artifact_1, artifact_2)

        self.assertQuerySetEqual(
            self.delete_expired._mark_to_keep(),
            {artifact_1, artifact_2},
            ordered=False,
        )

    def test_sweep_delete_artifacts(self) -> None:
        """Sweep() delete the artifacts and print progress."""
        artifact_1, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_1.created_at = timezone.now() - timedelta(days=2)
        artifact_1.save()
        artifact_2, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_2.created_at = timezone.now() - timedelta(days=2)
        artifact_2.save()

        self.playground.create_artifact_relation(artifact_2, artifact_1)
        self.playground.create_artifact_relation(artifact_1, artifact_2)

        artifact_3, _ = self.playground.create_artifact(expiration_delay=1)
        artifact_3.created_at = timezone.now() - timedelta(days=2)
        artifact_3.save()
        artifact_4, _ = self.playground.create_artifact(expiration_delay=0)

        self.playground.create_artifact_relation(artifact_4, artifact_3)

        artifacts_to_keep = self.delete_expired._mark_to_keep()

        self.operation._verbosity = 2
        self.operation.dry_run = False
        self.delete_expired._sweep(artifacts_to_keep)

        self.assertEqual(Artifact.objects.all().count(), 2)
        self.assertEqual(
            self.out.getvalue(),
            _format_deleted_artifacts({artifact_1, artifact_2}),
        )

    def test_sweep_delete_artifacts_no_retains(self) -> None:
        """Sweep() delete the artifacts from the collections."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL,
            data={
                "codename": "bookworm",
                "architecture": "amd64",
                "variant": "apt",
                "with_dev": True,
            },
            expiration_delay=1,
        )
        artifact.created_at = timezone.now() - timedelta(days=2)
        artifact.save()

        collection = Collection.objects.create(
            name="Debian",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=default_workspace(),
            retains_artifacts=Collection.RetainsArtifacts.NEVER,
        )

        manager = DebianEnvironmentsManager(collection=collection)
        manager.add_artifact(
            artifact,
            user=self.playground.get_default_user(),
            variables={"codename": "trixie"},
        )

        artifacts_to_keep = self.delete_expired._mark_to_keep()

        self.operation._verbosity = 2
        self.operation.dry_run = False
        self.delete_expired._sweep(artifacts_to_keep)

        self.assertEqual(Artifact.objects.all().count(), 0)
        self.assertEqual(
            self.out.getvalue(),
            _format_deleted_artifacts({artifact}),
        )

    def test_delete_artifact_all_related_files(self) -> None:
        """_delete_artifact() deletes all the artifact's related models."""
        uploaded_file_name = "README"
        uploading_file_name = "uploading"
        artifact, _ = self.playground.create_artifact(
            paths=[uploaded_file_name], create_files=True
        )
        FileUpload.objects.create(
            file_in_artifact=FileInArtifact.objects.create(
                artifact=artifact,
                path=uploading_file_name,
                file=self.playground.create_file(),
                complete=False,
            ),
            path="temp_file_uploading",
        )

        # Both files were added in the artifact; one was added in the store,
        # and the other is uploading
        self.assertEqual(FileInArtifact.objects.all().count(), 2)
        self.assertEqual(FileInStore.objects.all().count(), 1)
        self.assertEqual(FileUpload.objects.all().count(), 1)
        self.assertEqual(File.objects.all().count(), 2)

        # Retrieve the store_backend for the uploaded file
        uploaded_fileobj = File.objects.get(
            artifact=artifact, fileinartifact__path=uploaded_file_name
        )
        store_backend = FileStore.objects.get(
            files=uploaded_fileobj
        ).get_backend_object()

        # The file exists
        filepath = store_backend.get_local_path(uploaded_fileobj)
        assert filepath is not None
        self.assertTrue(filepath.exists())

        # The Artifact is going to be deleted and its two files with it
        self.operation.dry_run = False
        self.delete_expired._delete_artifact(artifact)

        self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())
        self.assertEqual(FileInArtifact.objects.all().count(), 0)
        self.assertEqual(FileUpload.objects.all().count(), 0)

        # FileInStore and File are not deleted: they are deleted after
        # the artifact is deleted in a different transaction (done in
        # DeleteExpiredArtifacts.run() )
        self.assertEqual(FileInStore.objects.all().count(), 1)
        self.assertEqual(File.objects.all().count(), 2)

        self.assertTrue(filepath.exists())

    def test_delete_artifact_not_related_files(self) -> None:
        """
        Two files created, _delete_artifact() delete the Artifact.

        Check that only the files that are supposed to be deleted are deleted.
        """
        file_to_keep_name = "README"  # used in two artifacts
        file_to_delete_name = (
            "another-file"  # used only in the deleted artifact
        )

        # Create the artifacts
        artifact_to_delete, _ = self.playground.create_artifact(
            paths=[file_to_keep_name, file_to_delete_name], create_files=True
        )
        artifact_to_keep, _ = self.playground.create_artifact()

        # Get both files
        fias_to_delete = {
            fia.path: fia for fia in artifact_to_delete.fileinartifact_set.all()
        }
        file_to_keep = fias_to_delete[file_to_keep_name].file
        file_to_delete = fias_to_delete[file_to_delete_name].file

        # Add file_to_keep in the artifact that is not being deleted
        # (so the file is kept)
        fia_to_keep = FileInArtifact.objects.create(
            artifact=artifact_to_keep, file=file_to_keep, complete=True
        )

        store_backend = FileInStore.objects.get(
            file=file_to_keep
        ).store.get_backend_object()

        file_to_keep_path = store_backend.get_local_path(file_to_keep)
        assert file_to_keep_path is not None
        file_to_delete_path = store_backend.get_local_path(file_to_delete)
        assert file_to_delete_path is not None

        # Both files exist on disk
        self.assertTrue(file_to_keep_path.exists())
        self.assertTrue(file_to_delete_path.exists())

        self.operation.dry_run = False

        self.delete_expired._delete_artifact(artifact_to_delete)

        # Only the expected FileInArtifact rows are deleted
        for fia in fias_to_delete.values():
            self.assertFalse(FileInArtifact.objects.filter(id=fia.id).exists())
        self.assertTrue(
            FileInArtifact.objects.filter(id=fia_to_keep.id).exists()
        )

        # Both files still exist on disk (they would be deleted by
        # DeleteExpiredArtifacts._delete_files_from_stores)
        self.assertTrue(file_to_keep_path.exists())
        self.assertTrue(file_to_delete_path.exists())

    def test_delete_artifact_dry_run(self) -> None:
        """_delete_artifact() does nothing: running in dry-run."""
        artifact, _ = self.playground.create_artifact(expiration_delay=1)
        artifact.created_at = timezone.now() - timedelta(days=2)
        artifact.save()

        # By default DeleteExpiredArtifacts runs in dry-run
        self.assertTrue(self.operation.dry_run)

        self.delete_expired._delete_artifact(artifact)

        # Artifact was not deleted
        self.assertTrue(Artifact.objects.filter(id=artifact.id).exists())

    def test_delete_files_from_stores(self) -> None:
        """_delete_files_from_stores deletes the file."""
        fileobj = self.playground.create_file()
        FileInStore.objects.create(store=default_file_store(), file=fileobj)

        self.delete_expired._delete_files_from_stores()

        self.assertFalse(File.objects.filter(id=fileobj.id).exists())

    def test_delete_files_from_multiple_stores(self) -> None:
        """_delete_files_from_stores deletes files from more than one store."""
        fileobj = self.playground.create_file()

        # Add the file in the default_file_store()
        FileInStore.objects.create(store=default_file_store(), file=fileobj)

        # Add the same file in a secondary store
        secondary_store = FileStore.objects.create(
            name="Test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": "/"},
        )
        FileInStore.objects.create(store=secondary_store, file=fileobj)

        # _delete_files_from_stores should delete the file from multiple stores
        self.delete_expired._delete_files_from_stores()

        # And the file is gone...
        self.assertFalse(File.objects.filter(id=fileobj.id).exists())

    def test_delete_files_from_stores_file_was_re_added(self) -> None:
        """
        _delete_files_from_stores does not delete the file.

        The file exist in another Artifact: cannot be deleted.
        """
        fileobj = self.playground.create_file()

        artifact, _ = self.playground.create_artifact()
        FileInArtifact.objects.create(artifact=artifact, file=fileobj)

        self.delete_expired._delete_files_from_stores()

        self.assertTrue(File.objects.filter(id=fileobj.id).exists())


class DeleteExpiredArtifactsTransactionTests(TransactionTestCase):
    """Tests for DeleteExpiredArtifacts that require transactions."""

    playground_memory_file_store = False

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.workspace = self.playground.get_default_workspace()

        self.artifact_1, _ = self.playground.create_artifact(
            expiration_delay=1, paths=["README"], create_files=True
        )
        self.artifact_1.created_at = timezone.now() - timedelta(days=2)
        self.artifact_1.save()
        self.artifact_2, _ = self.playground.create_artifact(expiration_delay=1)
        self.artifact_2.created_at = timezone.now() - timedelta(days=2)
        self.artifact_2.save()

        self.artifact_relation = self.playground.create_artifact_relation(
            self.artifact_1, self.artifact_2
        )

        self.out = io.StringIO()
        self.err = io.StringIO()

        self.operation = DeleteOperation(out=self.out, err=self.err)
        self.delete_expired = DeleteExpiredArtifacts(self.operation)

    def assert_delete_expired_run_failed(
        self,
        *,
        expected_artifacts: int,
        expected_artifact_relations: int,
        timeout: float,
        err: io.StringIO,
    ) -> None:
        """Assert result of deletion of artifacts when lock table failed."""
        self.assertEqual(Artifact.objects.all().count(), expected_artifacts)
        self.assertEqual(
            ArtifactRelation.objects.all().count(), expected_artifact_relations
        )

        self.assertEqual(
            err.getvalue(),
            f"Lock timed out ({timeout} seconds). Try again.\n",
        )

    def test_run_cannot_lock_artifact_relation_return_false(self) -> None:
        """
        run() cannot lock the tables of Artifact or ArtifactRelation.

        There is another transaction (created via RunInParallelTransaction).
        DeleteExpiredArtifacts.run() cannot lock the tables.
        """
        instances = [self.artifact_1, self.artifact_relation]

        for instance in instances:
            err = io.StringIO()
            self.operation._err = err

            delete_expired = DeleteExpiredArtifacts(self.operation)
            delete_expired._lock_timeout_secs = 0.1

            with self.subTest(instance._meta.object_name):
                expected_artifacts = Artifact.objects.all().count()
                expected_relations = ArtifactRelation.objects.all().count()

                manager = instance._meta.default_manager
                assert manager is not None

                thread = RunInParallelTransaction(
                    lambda: manager.select_for_update().get(pk=instance.pk)
                )

                thread.start_transaction()

                with self.operation:
                    self.operation._verbosity = 1
                    self.operation.dry_run = False
                    delete_expired.run()

                thread.stop_transaction()

                self.assert_delete_expired_run_failed(
                    expected_artifacts=expected_artifacts,
                    expected_artifact_relations=expected_relations,
                    timeout=delete_expired._lock_timeout_secs,
                    err=err,
                )

    @staticmethod
    def check_table_locked_for(manager: Manager[Any]) -> bool:
        """
        Call manager.first(). Expect timeout.

        If it does not time out: raise self.failureException().
        """
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '0.1s';")

            try:
                with transaction.atomic():
                    manager.first()
            except OperationalError:
                table_was_locked = True
            else:
                table_was_locked = False  # pragma: no cover

        return table_was_locked

    def test_tables_locked_on_sweep(self) -> None:
        """Check relevant tables are locked when _sweep() is called."""

        def wait_for_check(*args: Any, **kwargs: Any) -> set[File]:
            """Notify that _sweep() is called, wait for the check."""
            sweep_is_called.set()
            wait_for_check_tables_locked.wait()
            return set()

        sweep_is_called = threading.Event()
        wait_for_check_tables_locked = threading.Event()

        patcher = mock.patch.object(
            self.delete_expired, "_sweep", autospec=True
        )
        mocked_sweep = patcher.start()
        mocked_sweep.side_effect = wait_for_check
        self.addCleanup(patcher.stop)

        with self.operation:
            self.operation._verbosity = 2
            self.operation.dry_run = False
            delete_expired_run = RunInThreadAndCloseDBConnections(
                self.delete_expired.run
            )
            delete_expired_run.start_in_thread()

            sweep_is_called.wait()

            self.assertTrue(self.check_table_locked_for(Artifact.objects))
            self.assertTrue(
                self.check_table_locked_for(ArtifactRelation.objects)
            )
            self.assertTrue(self.check_table_locked_for(FileInArtifact.objects))
            self.assertTrue(self.check_table_locked_for(FileInStore.objects))
            self.assertTrue(self.check_table_locked_for(File.objects))

            wait_for_check_tables_locked.set()

            delete_expired_run.join()

    def test_on_commit_cleanup_delete_files(self) -> None:
        """File is deleted from the store if the transaction is committed."""
        artifact, _ = self.playground.create_artifact(
            paths=["README"], create_files=True, expiration_delay=1
        )
        artifact.created_at = timezone.now() - timedelta(days=2)
        artifact.save()

        message_artifacts_deleted = _format_deleted_artifacts(
            Artifact.objects.all()
        )

        self.assertTrue(Artifact.objects.filter(id=artifact.id).exists())
        file_in_store = FileInStore.objects.latest("id")
        fileobj = file_in_store.file
        store_backend = file_in_store.store.get_backend_object()

        file_path = store_backend.get_local_path(fileobj)
        assert file_path is not None
        self.assertTrue(file_path.exists())

        with self.operation:
            self.operation._verbosity = 2
            self.operation.dry_run = False
            self.delete_expired.run()

        self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())

        # The file does not exist anymore
        self.assertFalse(file_path.exists())

        # Expect two files to be deleted: README from each of
        # self.artifact_1 and artifact.
        self.assertEqual(
            self.out.getvalue(),
            message_artifacts_deleted + "Deleting 2 files from the store\n",
        )

    def test_deleting_files_artifact_table_is_locked(self) -> None:
        """Files get deleted while the Artifact table is still locked."""
        # Delete a relation and an artifact, and create an artifact
        # that expires tomorrow. This is to simplify the test
        self.artifact_relation.delete()
        self.artifact_2.delete()
        self.playground.create_artifact(expiration_delay=1)

        patcher = mock.patch.object(
            self.delete_expired,
            "_delete_files_from_stores",
            autospec=True,
        )
        delete_files_mocked = patcher.start()
        run_in_thread = RunInThreadAndCloseDBConnections(
            self.check_table_locked_for, Artifact.objects
        )
        delete_files_mocked.side_effect = run_in_thread.run_and_wait
        self.addCleanup(patcher.stop)

        with self.operation:
            self.operation._verbosity = 1
            self.operation.dry_run = False
            self.delete_expired.run()

        delete_files_mocked.assert_called()

    def test_delete_files_from_stores_raise_artifact_is_deleted(self) -> None:
        """Artifact is deleted even if the file store deletion fail."""
        exception = RuntimeError
        patcher = mock.patch.object(
            self.delete_expired,
            "_delete_files_from_stores",
            autospec=True,
        )
        delete_files_mocked = patcher.start()
        delete_files_mocked.side_effect = exception
        self.addCleanup(patcher.stop)

        with self.operation:
            self.operation._verbosity = 0
            self.operation.dry_run = False
            try:
                self.delete_expired.run()
            except exception:
                pass

        # _delete_files_from_stores raised an exception. Assert that the
        # artifact_2 deletion transaction is committed
        self.assertFalse(
            Artifact.objects.filter(id=self.artifact_2.id).exists()
        )


class DeleteExpiredWorkRequestsTests(TestCase):
    """Tests for the DeleteExpiredWorkRequests class."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.operation = DeleteOperation(
            out=self.out, err=self.err, dry_run=False
        )
        self.operation.initial_time = timezone.now()
        self.delete_expired = DeleteExpiredWorkRequests(self.operation)

    @contextlib.contextmanager
    def mock_perform_deletions(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock perform_deletions."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteExpiredWorkRequests.perform_deletions"
        ) as perform_deletions:
            yield perform_deletions

    @contextlib.contextmanager
    def mock_verbose(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock operation.verbose."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteOperation.verbose"
        ) as verbose:
            yield verbose

    def assert_dry_run_does_not_delete(self) -> None:
        """Make sure that, with dry_run, perform_deletions is not called."""
        old_dry_run = self.operation.dry_run
        self.operation.dry_run = True
        try:
            with self.mock_perform_deletions() as perform_deletions:
                self.delete_expired.run()
            perform_deletions.assert_not_called()
        finally:
            self.operation.dry_run = old_dry_run

    def test_no_expired(self) -> None:
        """No expired work requests."""
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertEqual(self.delete_expired.to_delete.work_requests, set())
        self.assertEqual(self.delete_expired.to_delete.collections, set())
        perform_deletions.assert_not_called()
        verbose.assert_called_once_with("There were no expired work requests\n")

    def test_one_expired(self) -> None:
        """One expired work request."""
        wr1 = self.playground.create_work_request(expired=True)
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertEqual(self.delete_expired.to_delete.work_requests, {wr1})
        self.assertEqual(self.delete_expired.to_delete.collections, set())
        perform_deletions.assert_called()
        verbose.assert_called_once_with(
            "Deleting 1 expired work requests and 0 expired collections\n"
        )
        self.assert_dry_run_does_not_delete()

    def test_multiple_expired(self) -> None:
        """Multiple expired work requests."""
        wr1 = self.playground.create_work_request(expired=True)
        wr2 = self.playground.create_work_request(expired=True)
        wr3 = self.playground.create_work_request(parent=wr2, expired=True)
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertEqual(
            self.delete_expired.to_delete.work_requests, {wr1, wr2, wr3}
        )
        self.assertEqual(self.delete_expired.to_delete.collections, set())
        perform_deletions.assert_called()
        verbose.assert_called_once_with(
            "Deleting 3 expired work requests and 0 expired collections\n"
        )
        self.assert_dry_run_does_not_delete()


class DeleteExpiredEphemeralGroupsTest(TestCase):
    """Tests for the DeleteExpiredEphemeralGroups class."""

    scenario = scenarios.DefaultScopeUser()

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.operation = DeleteOperation(
            out=self.out, err=self.err, dry_run=False
        )
        self.operation.initial_time = timezone.now()
        self.delete_expired = DeleteExpiredEphemeralGroups(self.operation)

    @contextlib.contextmanager
    def mock_perform_deletions(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock perform_deletions."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteExpiredEphemeralGroups.perform_deletions"
        ) as perform_deletions:
            yield perform_deletions

    @contextlib.contextmanager
    def mock_verbose(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock operation.verbose."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteOperation.verbose"
        ) as verbose:
            yield verbose

    def assert_dry_run_does_not_delete(self) -> None:
        """Make sure that, with dry_run, perform_deletions is not called."""
        old_dry_run = self.operation.dry_run
        self.operation.dry_run = True
        try:
            with self.mock_perform_deletions() as perform_deletions:
                self.delete_expired.run()
            perform_deletions.assert_not_called()
        finally:
            self.operation.dry_run = old_dry_run

    def test_no_unused(self) -> None:
        """No unused ephemeral groups."""
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.groups, [])
        perform_deletions.assert_not_called()
        verbose.assert_called_once_with(
            "There were no unused ephemeral groups\n"
        )

    def test_one_unused(self) -> None:
        """One unused ephemeral group."""
        group = self.playground.create_group(name="test", ephemeral=True)
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.groups, [group])
        perform_deletions.assert_called()
        verbose.assert_called_once_with("Deleting 1 unused ephemeral groups\n")
        self.assert_dry_run_does_not_delete()

    def test_ephemeral_but_used(self) -> None:
        """One ephemeral group in use."""
        group = self.playground.create_group(name="test", ephemeral=True)
        group.assign_role(self.scenario.scope, self.scenario.scope.Roles.OWNER)
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.groups, [])
        perform_deletions.assert_not_called()
        verbose.assert_called_once_with(
            "There were no unused ephemeral groups\n"
        )


class DeleteExpiredWorkspacesTests(TestCase):
    """Tests for the DeleteExpiredWorkspaces class."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.operation = DeleteOperation(
            out=self.out, err=self.err, dry_run=False
        )
        self.operation.initial_time = timezone.now()
        self.delete_expired = DeleteExpiredWorkspaces(self.operation)

    @contextlib.contextmanager
    def mock_perform_deletions(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock perform_deletions."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteExpiredWorkspaces.perform_deletions"
        ) as perform_deletions:
            yield perform_deletions

    @contextlib.contextmanager
    def mock_verbose(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock operation.verbose."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteOperation.verbose"
        ) as verbose:
            yield verbose

    def assert_dry_run_does_not_delete(self) -> None:
        """Make sure that, with dry_run, perform_deletions is not called."""
        old_dry_run = self.operation.dry_run
        self.operation.dry_run = True
        try:
            with self.mock_perform_deletions() as perform_deletions:
                self.delete_expired.run()
            perform_deletions.assert_not_called()
        finally:
            self.operation.dry_run = old_dry_run

    def test_no_expired(self) -> None:
        """No expired workspaces."""
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.to_delete.workspaces, [])
        perform_deletions.assert_not_called()
        verbose.assert_called_once_with("There were no expired workspaces\n")

    def test_one_expired(self) -> None:
        """One expired workspace."""
        create_time = timezone.now() - timedelta(days=30)
        with mock.patch("django.utils.timezone.now", return_value=create_time):
            ws = self.playground.create_workspace(
                name="ws", expiration_delay=timedelta(days=7)
            )

        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(
            self.delete_expired.to_delete.workspaces.all(), [ws]
        )
        perform_deletions.assert_called()
        verbose.assert_called_once_with("Deleting 1 expired workspaces\n")
        self.assert_dry_run_does_not_delete()

    def test_multiple_expired(self) -> None:
        """Multiple expired workspaces."""
        create_time = timezone.now() - timedelta(days=30)
        with mock.patch("django.utils.timezone.now", return_value=create_time):
            ws1 = self.playground.create_workspace(
                name="ws1", expiration_delay=timedelta(days=7)
            )
            ws2 = self.playground.create_workspace(
                name="ws2", expiration_delay=timedelta(days=7)
            )
            ws3 = self.playground.create_workspace(
                name="ws3", expiration_delay=timedelta(days=7)
            )
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(
            self.delete_expired.to_delete.workspaces,
            [ws1, ws2, ws3],
            ordered=False,
        )
        perform_deletions.assert_called()
        verbose.assert_called_once_with("Deleting 3 expired workspaces\n")
        self.assert_dry_run_does_not_delete()


class DeleteExpiredTokensTest(TestCase):
    """Tests for the DeleteExpiredTokens class."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.operation = DeleteOperation(
            out=self.out, err=self.err, dry_run=False
        )
        self.operation.initial_time = timezone.now()
        self.delete_expired = DeleteExpiredTokens(self.operation)

    @contextlib.contextmanager
    def mock_perform_deletions(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock perform_deletions."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteExpiredTokens.perform_deletions"
        ) as perform_deletions:
            yield perform_deletions

    @contextlib.contextmanager
    def mock_verbose(self) -> Iterator[mock.MagicMock]:
        """Shortcut to mock operation.verbose."""
        with mock.patch(
            "debusine.server.management.commands.delete_expired"
            ".DeleteOperation.verbose"
        ) as verbose:
            yield verbose

    def assert_dry_run_does_not_delete(self) -> None:
        """Make sure that, with dry_run, perform_deletions is not called."""
        old_dry_run = self.operation.dry_run
        self.operation.dry_run = True
        try:
            with self.mock_perform_deletions() as perform_deletions:
                self.delete_expired.run()
            perform_deletions.assert_not_called()
        finally:
            self.operation.dry_run = old_dry_run

    def test_no_expired(self) -> None:
        """No expired tokens."""
        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.tokens, [])
        perform_deletions.assert_not_called()
        verbose.assert_called_once_with("There were no expired tokens\n")

    def test_one_expired(self) -> None:
        """One unused token."""
        token = self.playground.create_worker_activation_token(
            expire_at=timezone.now() - timedelta(seconds=1)
        )

        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.tokens, [token])
        perform_deletions.assert_called()
        verbose.assert_called_once_with("Deleting 1 expired tokens\n")
        self.assert_dry_run_does_not_delete()

    def test_not_expired(self) -> None:
        """Tokens in use."""
        self.playground.create_bare_token()
        self.playground.create_user_token()
        self.playground.create_worker_token()
        self.playground.create_worker_activation_token(
            expire_at=timezone.now() + timedelta(days=1)
        )

        with (
            self.mock_perform_deletions() as perform_deletions,
            self.mock_verbose() as verbose,
        ):
            self.delete_expired.run()

        self.assertQuerySetEqual(self.delete_expired.tokens, [])
        perform_deletions.assert_not_called()
        verbose.assert_called_once_with("There were no expired tokens\n")


def _format_deleted_work_requests(
    work_requests: list[WorkRequest], collections: list[Collection]
) -> str:
    message = ""

    if work_requests or collections:
        message += (
            f"Deleting {len(work_requests)} expired work requests"
            f" and {len(collections)} expired collections\n"
        )
    else:
        message += "There were no expired work requests\n"

    return message


def _format_deleted_artifacts(artifacts: Iterable[Artifact]) -> str:
    message = ""

    for artifact in sorted(artifacts, key=lambda x: x.id):
        message += f"Deleted artifact {artifact.id}\n"

    return message
