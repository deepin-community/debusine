# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin to perform storage maintenance."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from django.conf import settings
from django.core.management import CommandParser
from django.db import transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    CollectionItem,
    File,
    FileInStore,
    FileStore,
    FileStoreInScope,
    FileUpload,
    Scope,
)
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.file_backend.interface import FileBackendEntryInterface


class Command(DebusineBaseCommand):
    """Command to report and fix inconsistencies."""

    help = "Report and fix inconsistencies in Debusine's DB and storage"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the vacuum_storage command."""
        parser.add_argument(
            "--dry-run",
            help="Trial run: do not modify anything",
            action="store_true",
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Run the checks and fixes."""
        dry_run = options["dry_run"]
        with transaction.atomic():
            self._delete_unreferenced_files(dry_run=dry_run)
        self._delete_empty_directories_in_the_store_directory(dry_run=dry_run)
        storage_policies_correct = self._apply_storage_policies(dry_run=dry_run)
        with transaction.atomic():
            self._delete_incomplete_old_artifacts(dry_run=dry_run)
        with transaction.atomic():
            self._delete_orphan_files_from_upload_directory(dry_run=dry_run)
        with transaction.atomic():
            missing_files_from_stores_correct = (
                self._report_missing_files_stores_referenced_by_the_db()
            )
        with transaction.atomic():
            missing_files_from_upload_correct = (
                self._report_missing_files_referenced_by_db_file_upload()
            )

        raise SystemExit(
            int(
                not storage_policies_correct
                or not missing_files_from_stores_correct
                or not missing_files_from_upload_correct
            )
        )

    def _delete_unreferenced_files(self, *, dry_run: bool) -> None:
        """Delete files in stores that are not referenced by the DB."""
        self.print_verbose("Checking orphan files from stores")
        one_day_ago = timezone.now() - timedelta(days=1)

        # We consider only files older than a day old in order to allow a
        # grace period for files that are in the process of being created,
        # to avoid possible race conditions where this job deletes them
        # while we're still in the middle of creating references to them.
        # TODO: At the moment we can only filter by mtime in some backends,
        # although this isn't an inherent limitation.
        for store in FileStore.objects.filter(
            backend__in={
                FileStore.BackendChoices.LOCAL,
                FileStore.BackendChoices.S3,
            }
        ):
            established_entries_in_storage = set(
                store.get_backend_object().list_entries(
                    mtime_filter=lambda mtime: mtime < one_day_ago
                )
            )
            entries_in_db = self._entries_from_files_in_file_store_db(store)
            entries_to_delete = established_entries_in_storage - entries_in_db

            for entry_to_delete in entries_to_delete:
                if not dry_run:
                    entry_to_delete.remove()

                self.print_verbose(f"Deleted {entry_to_delete}")

    @staticmethod
    def _path_older_than_one_day(path: Path) -> bool:
        one_day_ago = datetime.now() - timedelta(days=1)
        file_modification_time = datetime.fromtimestamp(path.stat().st_mtime)
        return file_modification_time < one_day_ago

    def _delete_empty_directories_in_the_store_directory(
        self, *, dry_run: bool
    ) -> None:
        """
        Delete empty directories in the store directory.

        This method might leave new empty directories (by design).

        If the directory a/b/c is deleted: now the directory a/b might be empty.
        This method could then check if a/b is empty but:

        - mtime of a/b is the recent date
        - if it deleted a/b it could be that the LocalFileBackend is adding
           a file in a/b and could fail (since LocalFileBackend is creating
           the subdirectories incrementally).

        To fix this it could have a locking mechanism (DB based or file
        based).

        For now: empty directories might be eventually deleted but not
        "right now".
        """
        self.print_verbose("Checking empty directories")

        store_directory_directories = self._list_directory(
            settings.DEBUSINE_STORE_DIRECTORY,
            directories_only=True,
            filters=[self._path_older_than_one_day],
        )

        def path_length(directory: Path) -> int:
            return len(str(directory))

        store_directories_sorted = sorted(
            store_directory_directories, key=path_length, reverse=True
        )

        for directory in store_directories_sorted:
            # The directory is empty (and was modified more than a day ago):
            # it can be deleted
            if not dry_run:
                try:
                    directory.rmdir()
                except OSError:
                    # Probably a file has been added between _list_directory()
                    # call and the rmdir()
                    continue
            self.print_verbose(f"Deleted empty directory {directory}")

    def _copy_file(
        self,
        *,
        scope: Scope,
        source: FileStore,
        target: FileStore,
        file: File,
        move: bool,
        dry_run: bool,
    ) -> None:
        """
        Copy a file between two file stores.

        If `move` is True, then also remove the file from the source.
        """
        if dry_run:
            verb = "Would move" if move else "Would copy"
        else:
            source_backend = source.get_backend_object()
            target_backend = target.get_backend_object()
            with source_backend.get_temporary_local_path(file) as local_path:
                target_backend.add_file(local_path, file)
            if move:
                source_backend.remove_file(file)
            verb = "Moved" if move else "Copied"

        self.print_verbose(
            f"{scope}: {verb} {file.hash_digest.hex()} from {source.name} "
            f"to {target.name}"
        )

    def _remove_file(
        self, *, scope: Scope, store: FileStore, file: File, dry_run: bool
    ) -> None:
        """Remove a file from a file store."""
        if dry_run:
            verb = "Would remove"
        else:
            store.get_backend_object().remove_file(file)
            verb = "Removed"

        self.print_verbose(
            f"{scope}: {verb} {file.hash_digest.hex()} from {store.name}"
        )

    def _drain_file(
        self,
        *,
        scope: Scope,
        policies: FileStoreInScope,
        store: FileStore,
        file: File,
        dry_run: bool,
    ) -> bool:
        """Drain a file from a file store in whatever way makes sense."""
        if scope.download_file_stores(file).exclude(id=store.id).exists():
            # The file can already be downloaded from some other store, so
            # we just need to remove it from this one.
            self._remove_file(
                scope=scope, store=store, file=file, dry_run=dry_run
            )
        else:
            # We can't easily optimize this by doing it in bulk for all
            # files, as the answer may change if stores exceed their
            # soft_max_size or max_size.
            upload_stores = scope.upload_file_stores(
                file, enforce_soft_limits=True
            ).exclude(id=store.id)
            if policies.drain_to is not None:
                upload_stores = upload_stores.filter(name=policies.drain_to)
            upload_store = upload_stores.first()
            if upload_store is None:
                if policies.drain_to is not None:
                    self.stderr.write(
                        f"{scope}: Wanted to drain {store.name} of "
                        f"{file.hash_digest.hex()}, but {policies.drain_to} "
                        f"cannot accept it"
                    )
                else:
                    self.stderr.write(
                        f"{scope}: Wanted to drain {store.name} of "
                        f"{file.hash_digest.hex()}, but it has no other upload "
                        f"file stores"
                    )
                return False

            self._copy_file(
                scope=scope,
                source=store,
                target=upload_store,
                file=file,
                move=True,
                dry_run=dry_run,
            )

        return True

    def _apply_populate_policy(self, scope: Scope, *, dry_run: bool) -> bool:
        """
        Apply per-scope `populate` file storage policy.

        This may involve copying files between stores.
        """
        ok = True

        for store in scope.file_stores.filter(
            filestoreinscope__populate=True
        ).order_by("id"):
            policies = scope.filestoreinscope_set.get(file_store=store)
            # Enforced by a check constraint on FileStoreInScope.
            assert not policies.drain
            assert not policies.read_only

            files_to_populate = (
                File.objects.filter(artifact__workspace__scope=scope)
                .exclude(filestore=store)
                .order_by("sha256")
            )
            if files_to_populate.exists():
                self.print_verbose(f"{scope}: Populating {store.name}")

            for file in files_to_populate.distinct().annotate(
                # Similar to Scope.download_file_stores, but in bulk and
                # excluding the store being populated.
                download_store_id=Subquery(
                    FileStore.objects.exclude(id=store.id)
                    .exclude(filestoreinscope__write_only=True)
                    .filter(files=OuterRef("id"))
                    .order_by(
                        F("filestoreinscope__download_priority").desc(
                            nulls_last=True
                        ),
                        F("filestoreinscope__upload_priority").desc(
                            nulls_last=True
                        ),
                    )
                    .values("id")[:1]
                )
            ):
                # Handle each file in a separate transaction; population may
                # take some time, and we don't want to have to wait for the
                # entire store to be populated before files added to it are
                # visible, or to roll back all the new FileInStore rows
                # because one copy failed.
                with transaction.atomic():
                    # Because this is in a separate transaction from the
                    # query that found files to populate, it's possible that
                    # the file has since been deleted.  If so, we can ignore
                    # it.
                    try:
                        file.refresh_from_db()
                    except File.DoesNotExist:
                        continue

                    if file.download_store_id is None:
                        self.stderr.write(
                            f"{scope}: Wanted to populate {store.name} with "
                            f"{file.hash_digest.hex()}, but it has no other "
                            f"download file stores"
                        )
                        ok = False
                        continue

                    if (
                        not scope.upload_file_stores(
                            file,
                            enforce_soft_limits=True,
                            include_write_only=True,
                        )
                        .filter(id=store.id)
                        .exists()
                    ):
                        # This might be slightly premature (we could have
                        # run into an unusually large file, and could
                        # perhaps populate some smaller files), but stores
                        # with the `populate` policy set ultimately need to
                        # have enough capacity to hold everything anyway.
                        self.stderr.write(
                            f"{scope}: Stopping population of {store.name} to "
                            f"avoid exceeding size limits"
                        )
                        ok = False
                        break

                    download_store = FileStore.objects.get(
                        id=file.download_store_id
                    )
                    self._copy_file(
                        scope=scope,
                        source=download_store,
                        target=store,
                        file=file,
                        move=False,
                        dry_run=dry_run,
                    )

        return ok

    def _apply_drain_policy(self, scope: Scope, *, dry_run: bool) -> bool:
        """
        Apply per-scope `drain` file storage policy.

        This may involve moving files between stores.
        """
        ok = True

        for store in scope.file_stores.filter(
            filestoreinscope__drain=True
        ).order_by("id"):
            policies = scope.filestoreinscope_set.get(file_store=store)
            # Enforced by a check constraint on FileStoreInScope.
            assert not policies.populate

            files_to_drain = File.objects.filter(
                artifact__workspace__scope=scope, filestore=store
            ).order_by("sha256")
            if files_to_drain.exists():
                self.print_verbose(f"{scope}: Draining {store.name}")

            for file in files_to_drain.distinct():
                # Handle each file in a separate transaction; draining may
                # take some time, and we don't want to have to wait for the
                # entire store to be drained before files moved from it to
                # other stores are visible, or to roll back all the new
                # FileInStore rows because one move failed.
                with transaction.atomic():
                    # Because this is in a separate transaction from the
                    # query that found files to drain, it's possible that
                    # the file has since been deleted.  If so, we can ignore
                    # it.
                    try:
                        file.refresh_from_db()
                    except File.DoesNotExist:
                        continue

                    if not self._drain_file(
                        scope=scope,
                        policies=policies,
                        store=store,
                        file=file,
                        dry_run=dry_run,
                    ):
                        ok = False

        return ok

    def _apply_max_size_limits(self, scope: Scope, *, dry_run: bool) -> bool:
        """
        Apply `soft_max_size` and `max_size` limits.

        This may involve moving files between stores.
        """
        ok = True

        # TODO: This doesn't handle the per-scope soft_max_size policy yet,
        # since that involves calculating a per-scope-and-store total size
        # and that's a bit involved.
        for store in scope.file_stores.filter(
            Q(soft_max_size__lt=F("total_size"))
            | Q(max_size__lt=F("total_size"))
        ).order_by("id"):
            policies = scope.filestoreinscope_set.get(file_store=store)

            if policies.drain and dry_run:
                # In dry-run mode, we'll already have drained this store in
                # a previous step, so trying to do so again here just
                # produces confusing output.
                continue

            # Pre-compute the volume of data we need to drain, as otherwise
            # we can't provide a useful dry-run simulation.  This approach
            # isn't perfect because something else might still be adding
            # files to a store whose soft_max_size is exceeded, but the next
            # vacuum_storage run will catch up with that.
            size_to_drain = max(
                [
                    store.total_size - limit
                    for limit in (store.soft_max_size, store.max_size)
                    if limit is not None
                ]
            )
            self.print_verbose(
                f"{scope}: Draining up to {size_to_drain} bytes from "
                f"{store.name}"
            )

            # It's not completely clear what we should do if a store's
            # soft_max_size/max_size is exceeded and the store is linked to
            # multiple scopes.  In principle we could try draining files
            # from any of its scopes.  For now we just take the simple
            # approach of draining files from each scope in order as long as
            # we need to, but in future we might instead decide to have some
            # kind of priority system.
            for file in (
                File.objects.filter(
                    artifact__workspace__scope=scope, filestore=store
                )
                .distinct()
                .order_by("artifact__created_at", F("size").desc(), "sha256")
            ):
                # Handle each file in a separate transaction; draining may
                # take some time, and we don't want to have to wait for the
                # entire store to be drained before files moved from it to
                # other stores are visible, or to roll back all the new
                # FileInStore rows because one move failed.
                with transaction.atomic():
                    # Because this is in a separate transaction from the
                    # query that found files to drain, it's possible that
                    # the file has since been deleted.  If so, we can ignore
                    # it.
                    try:
                        file.refresh_from_db()
                    except File.DoesNotExist:
                        continue

                    if self._drain_file(
                        scope=scope,
                        policies=policies,
                        store=store,
                        file=file,
                        dry_run=dry_run,
                    ):
                        size_to_drain -= file.size
                        if size_to_drain <= 0:
                            break
                    else:
                        ok = False

        return ok

    def _apply_storage_policies(self, *, dry_run: bool) -> bool:
        """
        Apply file storage policies.

        This may involve copying or moving files between stores in order to
        satisfy the `populate` and `drain` policies and the store-level
        `soft_max_size` and `max_size` limits.
        """
        ok = True

        for scope in Scope.objects.order_by("id"):
            if not self._apply_populate_policy(scope, dry_run=dry_run):
                ok = False

            if not self._apply_drain_policy(scope, dry_run=dry_run):
                ok = False

            if not self._apply_max_size_limits(scope, dry_run=dry_run):
                ok = False

        return ok

    def _delete_incomplete_old_artifacts(self, *, dry_run: bool) -> None:
        """Delete artifacts that are incomplete and created > 1 day ago."""
        self.print_verbose("Checking incomplete artifacts")

        one_day_ago = timezone.now() - timedelta(days=1)

        to_delete = (
            Artifact.objects.filter(created_at__lt=one_day_ago)
            .annotate_complete()
            .filter(complete=False)
            # TODO: Incomplete artifacts ending up as collection items is
            # most likely a bug, but at the moment it does happen
            # occasionally.  Exclude such cases and let expiry take care of
            # them instead.
            .exclude(id__in=CollectionItem.objects.values("artifact"))
            .prefetch_related("fileinartifact_set")
        ).order_by("id")

        ArtifactRelation.objects.filter(
            Q(artifact__in=to_delete) | Q(target__in=to_delete)
        ).delete()

        for artifact in to_delete:
            for file_in_artifact in artifact.fileinartifact_set.all():
                if not dry_run:
                    if hasattr(file_in_artifact, "fileupload"):
                        file_in_artifact.fileupload.delete()

                    file_in_artifact.delete()

            artifact_id = artifact.id
            if not dry_run:
                artifact.delete()

            self.print_verbose(f"Deleted incomplete artifact {artifact_id}")

    def _delete_orphan_files_from_upload_directory(
        self, *, dry_run: bool
    ) -> None:
        """Delete files from the upload directory that are not referenced."""
        self.print_verbose("Checking orphan files in upload directory")

        paths_in_disk = self._list_directory(
            settings.DEBUSINE_UPLOAD_DIRECTORY,
            filters=[self._path_older_than_one_day],
        )

        paths_in_db = set()
        for file_upload in FileUpload.objects.all():
            paths_in_db.add(file_upload.absolute_file_path())

        files_to_remove = paths_in_disk - paths_in_db

        for file_to_remove in files_to_remove:
            if not dry_run:
                file_to_remove.unlink(missing_ok=True)

            self.print_verbose(f"Deleted {file_to_remove}")

    def _report_missing_files_stores_referenced_by_the_db(self) -> bool:
        """
        Report DB referencing files that do not exist in storage.

        It cannot be fixed automatically.
        """
        self.print_verbose("Checking missing files from stores")
        ok = True

        for store in FileStore.objects.order_by("id"):
            # TODO: We should do this in a way that doesn't require holding
            # both sets in memory.
            entries_in_storage = set(store.get_backend_object().list_entries())
            entries_in_db = self._entries_from_files_in_file_store_db(store)
            orphan_entries = entries_in_db - entries_in_storage

            for orphan_entry in orphan_entries:
                self.stderr.write(
                    f"File in FileStore {store.name} but not in storage: "
                    f"{orphan_entry}"
                )

            ok = ok and (len(orphan_entries) == 0)

        return ok

    @staticmethod
    def _entries_from_files_in_file_store_db(
        store: FileStore,
    ) -> set[FileBackendEntryInterface[Any, Any]]:
        store_backend = store.get_backend_object()
        # TODO: This should probably also filter to files that are in any of
        # the scopes configured to use this store.  After `debusine-admin
        # scope manage --remove-file-store`, it's possible for the store to
        # contain files that are no longer in any of its scopes, and those
        # should be cleaned up.
        return {
            store_backend.get_entry(file_in_store.file)
            for file_in_store in FileInStore.objects.filter(store=store)
        }

    def _report_missing_files_referenced_by_db_file_upload(self) -> bool:
        """Report DB referencing upload files that do not exist in disk."""
        self.print_verbose("Checking missing files from upload")

        files_in_disk = self._list_directory(settings.DEBUSINE_UPLOAD_DIRECTORY)

        files_in_db = {
            file_upload.absolute_file_path()
            for file_upload in FileUpload.objects.all()
        }

        orphan_files = files_in_db - files_in_disk

        for orphan_file in orphan_files:
            self.stderr.write(
                f"File in FileUpload but not on disk: {orphan_file}"
            )

        return len(orphan_files) == 0

    @staticmethod
    def _list_directory(
        directory: Path | str,
        *,
        files_only: bool = False,
        directories_only: bool = False,
        filters: list[Callable[[Path], bool]] | None = None,
    ) -> set[Path]:
        if files_only and directories_only:
            raise ValueError(
                'Parameters "files_only" and "directories_only" '
                'are incompatible'
            )

        final_filters: list[Callable[[Path], bool]] = []

        if files_only:
            final_filters.append(lambda x: x.is_file())

        if directories_only:
            final_filters.append(lambda x: x.is_dir())

        if filters is not None:
            final_filters.extend(filters)

        # Pathlib.glob documentation: "Using the “**” pattern in large
        # directory trees may consume an inordinate amount of time."
        # Consider changing this with something else (e.g. os.walk; or
        # yielding paths and checking the DB per each file/set of files if
        # this becomes a problem).
        paths = Path(directory).glob("**/*")

        filtered_paths = set()

        for path in paths:
            if all(f(path) for f in final_filters):
                filtered_paths.add(path)

        return filtered_paths
