# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to delete expired objects."""

import contextlib
import io
from collections.abc import Generator
from datetime import datetime
from typing import Any, NoReturn

import django
from django.core.management import CommandParser
from django.db import connection, transaction
from django.db.backends.utils import CursorWrapper
from django.db.models import ProtectedError, Q, QuerySet
from django.utils import timezone

from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    CollectionItem,
    File,
    FileInArtifact,
    FileInStore,
    Group,
    Token,
    WorkRequest,
    Workspace,
    work_requests,
)
from debusine.db.models.workspaces import DeleteWorkspaces
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to expire artifacts."""

    help = "Delete expired artifacts if no other artifact depends on them"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the delete_expired command."""
        parser.add_argument(
            "--dry-run",
            help="Trial run: do not delete anything",
            action="store_true",
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Delete expired artifacts."""
        with DeleteOperation(
            out=self.stdout,
            err=self.stderr,
            dry_run=options["dry_run"],
            verbosity=options["verbosity"],
        ) as operation:
            delete_expired_workrequests = DeleteExpiredWorkRequests(operation)
            delete_expired_workrequests.run()

            delete_expired_artifacts = DeleteExpiredArtifacts(operation)
            delete_expired_artifacts.run()

            delete_expired_ephemeral_groups = DeleteExpiredEphemeralGroups(
                operation
            )
            delete_expired_ephemeral_groups.run()

            delete_expired_workspaces = DeleteExpiredWorkspaces(operation)
            delete_expired_workspaces.run()

            delete_expired_tokens = DeleteExpiredTokens(operation)
            delete_expired_tokens.run()

        raise SystemExit(0)


class LockTableFailed(BaseException):
    """Raised if a "LOCK TABLE" failed."""


class DeleteOperation:
    """Coordinate multiple delete operations."""

    def __init__(
        self,
        *,
        out: io.TextIOBase,
        err: io.TextIOBase,
        dry_run: bool = True,
        verbosity: int = 0,
    ) -> None:
        """
        Initialize object.

        :param out: to write information of deleted artifacts.
        :param err: to write errors that might happen during the deletion.
        :param dry_run: If True, no changes will be made to the database or
            files; instead the method will only simulate the execution
            of the actions that would have been taken.
        :param verbosity: If True, the method will provide detailed information
            about the execution progress and results.
        """
        self._out: io.TextIOBase = out
        self._err: io.TextIOBase = err

        self._verbosity: int = verbosity
        self.dry_run: bool = dry_run
        # datetime where all delete operations started
        self.initial_time: datetime = (
            timezone.now()
        )  # time used for all the checks

    def __enter__(self) -> "DeleteOperation":
        """Context manager start."""
        if self.dry_run:
            self.verbose("dry-run mode: no changes will be made\n")
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager end."""
        if self.dry_run:
            self.verbose("dry-run mode: no changes were made\n")
        return None

    def error(self, message: str) -> None:
        """Print an error message."""
        self._err.write(message)

    def verbose(self, message: str) -> None:
        """Print a verbose message."""
        if self._verbosity > 1:
            self._out.write(message)


class DeleteExpiredArtifacts:
    """
    Delete expired artifacts.

    Delete expired artifacts that don't have a dependent artifact (direct or
    indirect via other artifacts).

    Delete other artifact/files associated models.

    Implemented using Mark and Sweep.
    """

    def __init__(self, operation: DeleteOperation) -> None:
        """
        Initialize object.

        :param operation: class used for output
        """
        self.operation = operation

        self._lock_timeout_secs: float = 5

    @staticmethod
    def _tables_to_lock() -> list[str]:
        return [
            Artifact._meta.db_table,
            ArtifactRelation._meta.db_table,
            FileInArtifact._meta.db_table,
            FileInStore._meta.db_table,
            File._meta.db_table,
        ]

    @contextlib.contextmanager
    def lock_tables(
        self, table_names: list[str]
    ) -> Generator[CursorWrapper, None, None]:
        """
        Lock the tables or raise LockTableFailed.

        :param table_names: tables to try to lock.
        """
        with connection.cursor() as cursor, transaction.atomic():
            cursor.execute(
                f"SET LOCAL lock_timeout = '{self._lock_timeout_secs}s'"
            )

            try:
                for table_name in table_names:
                    cursor.execute(
                        f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"
                    )
            except django.db.utils.OperationalError:
                raise LockTableFailed(
                    f"Lock timed out ({self._lock_timeout_secs} seconds)"
                )

            yield cursor

    def run(self) -> None:
        """Delete the expired artifacts (with dry_run or verbosity levels)."""
        try:
            with self.lock_tables(self._tables_to_lock()):
                CollectionItem.objects.drop_full_history(
                    self.operation.initial_time
                )
                CollectionItem.objects.drop_metadata(
                    self.operation.initial_time
                )
                artifact_ids = self._mark_to_keep()

                delete_files_from_stores = self._sweep(artifact_ids)

                if self.operation.dry_run:
                    transaction.set_rollback(True)
            with self.lock_tables(self._tables_to_lock()):
                # delete_files_from_stores is empty for --dry-run because
                # self._delete_artifact() does not return any files to delete
                # if self._dry_run is True

                self._delete_files_from_stores(delete_files_from_stores)
        except LockTableFailed as exc:
            self.operation.error(f"{exc}. Try again.\n")  # noqa: G004

    def _mark_to_keep(self) -> set[int]:
        """
        Return artifact ids that must be kept.

        These artifacts cannot be deleted: are not expired or are expired
        but there is an ArtifactRelation such as
        target=non_expired_artifact, artifact=expired_artifact.
        """
        marked_to_keep: set[int] = set()
        visited: set[int] = set()

        not_expired = Artifact.objects.not_expired(self.operation.initial_time)
        part_of_collection = (
            Artifact.objects.part_of_collection_with_retains_artifacts()
        )

        for artifact in not_expired | part_of_collection:
            self._traverse_and_mark_from_artifact(
                artifact.id, marked_to_keep, visited
            )

        return marked_to_keep

    def _delete_artifact(self, artifact: Artifact) -> set[File]:
        """
        Delete the specified Artifact and related models.

        Delete ArtifactRelation with target or artifact referencing artifact and
        the associated FileInArtifact files for this artifact.

        If the files were associated only to this artifact, it also deletes
        the database entries for the files and adds the removal of the file
        in the store for when the transaction is committed.

        :return: files that might be able to be deleted (if they don't exist
          in another artifact besides the ones that is being deleted).
        """
        if self.operation.dry_run:
            return set()

        for collection_item in artifact.collection_items.all():
            collection_item.parent_collection.manager.remove_artifact(artifact)
            artifact.collection_items.update(artifact=None)

        delete_filesobjs = set()

        ArtifactRelation.objects.filter(
            Q(target=artifact) | Q(artifact=artifact)
        ).delete()

        for file_in_artifact in FileInArtifact.objects.filter(
            artifact=artifact
        ):
            fileobj = file_in_artifact.file
            file_in_artifact.delete()

            # Maybe this artifact was the only one to have this file.
            # The file needs to be deleted from the store later on,
            # after the deletion of Artifacts have been committed.

            delete_filesobjs.add(fileobj)

        artifact.delete()

        return delete_filesobjs

    def _delete_files_from_stores(self, file_objs: set[File]) -> None:
        """
        Delete the files from stores.

        Files are not deleted at the same time as the artifacts. They need
        to be deleted after the transaction and only if no other FileInArtifact
        has the file.
        """
        if len(file_objs) > 0:
            self.operation.verbose("Deleting files from the store\n")

        for fileobj in file_objs:
            if not FileInArtifact.objects.filter(file=fileobj).exists():
                with transaction.atomic():
                    # This is an orphaned file. Delete the file and
                    # its dependencies
                    for file_in_store in FileInStore.objects.filter(
                        file=fileobj
                    ):
                        store = file_in_store.store.get_backend_object()
                        store.remove_file(fileobj)

                        try:
                            fileobj.delete()
                        except ProtectedError:
                            # Perhaps it's not possible to delete the file yet:
                            # it might be in another store
                            pass

    def _sweep(self, artifact_ids: set[int]) -> set[File]:
        """
        Delete artifacts that are not in artifact_ids and its relations.

        :return: File objects that can potentially be deleted (if not used
          by any other artifact).
        """
        delete_files_from_stores = set()
        number_of_artifacts_deleted = 0
        with transaction.atomic():
            for artifact_expired in Artifact.objects.expired(
                self.operation.initial_time
            ).order_by("id"):
                if (artifact_id := artifact_expired.id) not in artifact_ids:
                    number_of_artifacts_deleted += 1
                    files_to_delete = self._delete_artifact(artifact_expired)
                    delete_files_from_stores.update(files_to_delete)

                    self.operation.verbose(f"Deleted artifact {artifact_id}\n")

        if number_of_artifacts_deleted == 0:
            self.operation.verbose("There were no expired artifacts\n")

        return delete_files_from_stores

    def _traverse_and_mark_from_artifact(
        self, artifact_id: int, marked_to_keep: set[int], visited: set[int]
    ) -> None:
        if artifact_id in visited:
            return

        visited.add(artifact_id)
        marked_to_keep.add(artifact_id)

        for artifact_targeting in Artifact.objects.filter(
            targeted_by__artifact_id=artifact_id
        ):
            self._traverse_and_mark_from_artifact(
                artifact_targeting.id, marked_to_keep, visited
            )


class DeleteExpiredWorkRequests:
    """
    Delete expired work requests.

    Delete work requests that don't have unexpired child work requests.

    Delete also the work requests' internal collection if present.
    """

    to_delete: work_requests.DeleteUnused

    def __init__(self, operation: DeleteOperation) -> None:
        """
        Initialize object.

        :param operation: class used for output
        """
        self.operation = operation

    def perform_deletions(self) -> None:
        """Delete objects found in self.work_requests and self.collections."""
        self.to_delete.perform_deletions()

    def run(self) -> None:
        """Find and optionally delete the expired work requests."""
        with transaction.atomic():
            self.to_delete = work_requests.DeleteUnused(
                WorkRequest.objects.expired(at=self.operation.initial_time)
            )
            if self.to_delete.work_requests:
                self.operation.verbose(
                    f"Deleting {len(self.to_delete.work_requests)}"
                    " expired work requests"
                    f" and {len(self.to_delete.collections)}"
                    " expired collections\n"
                )

                # FIXME: do we enumerate each deleted work request to stdout?

                if not self.operation.dry_run:
                    self.perform_deletions()
            else:
                self.operation.verbose("There were no expired work requests\n")


class DeleteExpiredEphemeralGroups:
    """Delete expired exphemeral groups."""

    groups: QuerySet[Group]

    def __init__(self, operation: DeleteOperation) -> None:
        """
        Initialize object.

        :param operation: class used for output
        """
        self.operation = operation

    def perform_deletions(self) -> None:
        """Delete previously found unused ephemeral groups."""
        self.groups.delete()

    def run(self) -> None:
        """Find and optionally delete the expired work requests."""
        with transaction.atomic():
            self.groups = Group.objects.unused_ephemeral()
            if count := len(self.groups):
                self.operation.verbose(
                    f"Deleting {count} unused ephemeral groups\n"
                )
                if not self.operation.dry_run:
                    self.perform_deletions()
            else:
                self.operation.verbose(
                    "There were no unused ephemeral groups\n"
                )


class DeleteExpiredWorkspaces:
    """
    Delete expired workspaces.

    Delete workspaces that have expiration_delay set, ``expiration_delay`` days
    after ``max(created_at, last_task_completion_time)``
    """

    #: Work requests to delete
    to_delete: DeleteWorkspaces

    def __init__(self, operation: DeleteOperation) -> None:
        """
        Initialize object.

        :param operation: class used for output
        """
        self.operation = operation

    def perform_deletions(self) -> None:
        """Delete objects found in self.work_requests and self.collections."""
        self.to_delete.perform_deletions()

    def run(self) -> None:
        """Find and optionally delete the expired work requests."""
        with transaction.atomic():
            self.to_delete = DeleteWorkspaces(
                Workspace.objects.exclude(expiration_delay__isnull=True)
                .with_expiration_time()
                .filter(expiration_time__lt=self.operation.initial_time)
            )
            if self.to_delete.workspaces:
                self.operation.verbose(
                    f"Deleting {len(self.to_delete.workspaces)}"
                    " expired workspaces\n"
                )
                if not self.operation.dry_run:
                    self.perform_deletions()
            else:
                self.operation.verbose("There were no expired workspaces\n")


class DeleteExpiredTokens:
    """Delete expired tokens."""

    tokens: QuerySet[Token]

    def __init__(self, operation: DeleteOperation) -> None:
        """
        Initialize object.

        :param operation: class used for output
        """
        self.operation = operation

    def perform_deletions(self) -> None:
        """Delete previously-found expired tokens."""
        self.tokens.delete()

    def run(self) -> None:
        """Find and optionally delete expired tokens."""
        with transaction.atomic():
            self.tokens = Token.objects.expired(at=self.operation.initial_time)
            if count := len(self.tokens):
                self.operation.verbose(f"Deleting {count} expired tokens\n")
                if not self.operation.dry_run:
                    self.perform_deletions()
            else:
                self.operation.verbose("There were no expired tokens\n")
