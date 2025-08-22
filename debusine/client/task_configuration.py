# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine task configuration repository management."""

import logging
import os
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Any, NamedTuple, Self, TypeAlias

import yaml

from debusine.artifacts.models import DebusineTaskConfiguration, TaskTypes
from debusine.client.models import (
    StrictBaseModel,
    TaskConfigurationCollection,
    model_to_json_serializable_dict,
)


class InvalidRepository(Exception):
    """The local repository is not valid or does not match the remote one."""


class PullStats(NamedTuple):
    """Statistics for a pull operation."""

    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0

    def has_changes(self) -> bool:
        """Check if anything was changed."""
        return self.added > 0 or self.updated > 0 or self.deleted > 0


class TaskConfigKey(NamedTuple):
    """Identifier for a non-template task configuration item."""

    task_type: TaskTypes | None = None
    task_name: str | None = None
    subject: str | None = None
    context: str | None = None
    template: str | None = None

    @classmethod
    def from_item(cls, item: DebusineTaskConfiguration) -> Self:
        """Create a key to identify a non-template DebusineTaskConfiguration."""
        if item.template is None:
            assert item.task_type is not None
            assert item.task_name is not None
            return cls(
                task_type=item.task_type,
                task_name=item.task_name,
                subject=item.subject,
                context=item.context,
            )
        else:
            return cls(template=item.template)


class ItemInFile(NamedTuple):
    """An item, optionally annotated with a path."""

    item: DebusineTaskConfiguration
    #: File where the item is stored
    path: Path
    #: Position of this item in the file
    position: int = 0


class Manifest(StrictBaseModel):
    """Repository metadata."""

    workspace: str
    collection: TaskConfigurationCollection


class TaskConfigurationRepositoryBase:
    """Base class for task configuration repositories."""

    Manifest: TypeAlias = Manifest


class RemoteTaskConfigurationRepository(TaskConfigurationRepositoryBase):
    """Task configuration repository populated from a remote server."""

    def __init__(self, manifest: Manifest) -> None:
        """Initialize with a manifest."""
        #: Repository metadata
        self.manifest = manifest
        #: Task configuration entries
        self.entries: dict[TaskConfigKey, DebusineTaskConfiguration] = {}

    def add(self, item: DebusineTaskConfiguration) -> None:
        """Add an item to the repository."""
        self.entries[TaskConfigKey.from_item(item)] = item

    @classmethod
    def from_items(
        cls, manifest: Manifest, items: Iterable[DebusineTaskConfiguration]
    ) -> Self:
        """Load contents from a stream of items."""
        res = cls(manifest)
        for item in items:
            res.add(item)
        return res


class LocalTaskConfigurationRepository(TaskConfigurationRepositoryBase):
    """Task configuration repository stored in the local file system."""

    def __init__(self, root: Path) -> None:
        """Initialize an empty repository."""
        #: Repository metadata
        self.manifest: Manifest | None
        #: Task configuration entries
        self.entries: dict[TaskConfigKey, ItemInFile]

        #: On-disk root
        self.root = root
        #: Path of the manifest file
        self.manifest_path = self.root / "MANIFEST"

        self.reload()

    def reload(self) -> None:
        """Load/reload contents."""
        self.manifest = None
        self.entries = {}

        #: Load manifest from disk
        if self.manifest_path.exists():
            self.manifest = self.read_manifest(self.root)

        #: Load items from disk
        for item in self.iter_items(self.root):
            self.track(item)

    def is_dirty(self) -> bool:
        """Check if the git repository is dirty."""
        return False

    def git_commit(self) -> str | None:
        """Return the hash of the current git commit."""
        return None

    def has_commit(self, commit: str) -> bool:  # noqa: U100
        """Check if the given commit hash is in our history."""
        return False

    def track(self, item: ItemInFile) -> None:
        """Add an ItemInFile to the list of entries to track."""
        self.entries[TaskConfigKey.from_item(item.item)] = item

    def write_manifest(self) -> None:
        """Write repository manifest from disk."""
        if self.manifest is None:
            raise InvalidRepository("Repository manifest has not been set")
        try:
            with self.manifest_path.open("wt") as fd:
                yaml.safe_dump(self.manifest.dict(), fd)
        except Exception as exc:
            exc.add_note(f"manifest: {self.manifest_path}")
            raise

    def make_default_path(self, item: DebusineTaskConfiguration) -> Path:
        """Compose a path where we can save a new item."""
        if item.template:
            return Path("new") / "templates" / f"{item.template}.yaml"
        else:
            assert item.task_type is not None
            assert item.task_name is not None
            return (
                Path("new")
                / f"{item.task_type.lower()}_{item.task_name}"
                / f"{item.subject or 'any'}_{item.context or 'any'}.yaml"
            )

    def _merge_manifest(self, manifest: Manifest) -> None:
        if self.manifest is None:
            self.manifest = manifest.copy()
        elif (
            self.manifest.workspace != manifest.workspace
            or self.manifest.collection.id != manifest.collection.id
            or self.manifest.collection.name != manifest.collection.name
        ):
            raise InvalidRepository(
                "repo to pull refers to collection"
                f" {manifest.workspace}/{manifest.collection.name}"
                f" ({manifest.collection.id})"
                " while the checkout has collection"
                f" {self.manifest.workspace}/{self.manifest.collection.name}"
                f" ({self.manifest.collection.id})"
            )
        else:
            self.manifest.collection.data = manifest.collection.data.copy()

    def _pull(
        self,
        repo: RemoteTaskConfigurationRepository,
        logger: logging.Logger,
        preserve_deleted: bool = True,
    ) -> PullStats:
        """
        Pull changes from another repository.

        :param repo: repository to pull from
        :param logger: logger to use to give user feedback about what changed
        :param preserve_deleted: if True, items deleted in repo will be
               preserved in self; if False, items deleted in repo will be
               deleted in self.
        """
        added, updated, deleted, unchanged = 0, 0, 0, 0
        self._merge_manifest(repo.manifest)

        # Update existing items
        for key in list(repo.entries.keys() & self.entries.keys()):
            old = self.entries[key]
            new = repo.entries[key]
            # Acquire preserving our path
            if old.item == new:
                unchanged += 1
                continue
            logger.info("%s: item updated", old.path)
            self.entries[key] = ItemInFile(new, old.path)
            updated += 1

        # Add new items
        for key in repo.entries.keys() - self.entries.keys():
            remote_item = repo.entries[key]
            # Generate a target path for new items
            local_item = ItemInFile(
                remote_item, self.make_default_path(remote_item)
            )
            logger.info("%s: new item", local_item.path)
            self.entries[key] = local_item
            added += 1

        # Delete or preserve items not in repo
        deleted_paths: list[Path] = []
        if not preserve_deleted:
            for key in list(self.entries.keys() - repo.entries.keys()):
                logger.info("%s: item deleted", self.entries[key].path)
                old = self.entries[key]
                deleted_paths.append(old.path)
                del self.entries[key]
                deleted += 1

        # Write out results to filesystem
        self.write_manifest()
        self.write_entries()

        return PullStats(
            added=added, updated=updated, deleted=deleted, unchanged=unchanged
        )

    def write_entries(self) -> None:
        """Write entries to disk."""
        # Organize items by path and position
        entries_by_path: defaultdict[Path, list[ItemInFile]] = defaultdict(list)
        for item in self.entries.values():
            entries_by_path[item.path].append(item)
        for items in entries_by_path.values():
            items.sort(key=lambda i: i.position)

        # Gather existing files
        existing_relpaths = [
            path.relative_to(self.root) for path in self.iter_files(self.root)
        ]

        # Paths that have been deleted
        deleted_paths: list[Path] = []

        # Update workdir to match entries
        for relpath in existing_relpaths:
            path = self.root / relpath
            if entries := entries_by_path.pop(relpath, None):
                # Read existing contents
                with path.open() as fd:
                    old_data = yaml.safe_load(fd)

                new_data: Any
                if len(entries) == 1:
                    new_data = model_to_json_serializable_dict(entries[0].item)
                else:
                    new_data = [
                        model_to_json_serializable_dict(e.item) for e in entries
                    ]

                # If the file content is unchanged, leave it alone
                if old_data == new_data:
                    continue

                # Else rewrite it
                with path.open("wt") as fd:
                    yaml.safe_dump(new_data, fd)
            else:
                # If no items exist for a file, delete it
                path.unlink()
                deleted_paths.append(path)

        # Write out new files
        for relpath, entries in entries_by_path.items():
            if len(entries) == 1:
                new_data = model_to_json_serializable_dict(entries[0].item)
            else:
                # new_data = [
                #     model_to_json_serializable_dict(e.item) for e in entries
                # ]
                raise NotImplementedError(
                    "Writing new files with multiple entries is not supported,"
                    " since new entries are never bundled into a single file"
                )

            path = self.root / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wt") as fd:
                yaml.safe_dump(new_data, fd)

        # Remove empty directories which contained files that we have just
        # deleted
        deleted_parents = {path.parent for path in deleted_paths}
        for parent in deleted_parents:
            if len(list(parent.iterdir())) == 0:
                parent.rmdir()

        # Reload from disk since positions may have changed if elements have
        # been removed mid-file
        self.reload()

    def pull(
        self,
        repo: RemoteTaskConfigurationRepository,
        logger: logging.Logger,
    ) -> PullStats:
        """
        Pull changes from another repository.

        :param repo: repository to pull from
        :param logger: logger to use to give user feedback about what changed
        :param preserve_deleted: if True, items deleted in repo will be
               preserved in self; if False, items deleted in repo will be
               deleted in self.
        """
        return self._pull(repo, logger, preserve_deleted=True)

    @classmethod
    def iter_files(cls, root: Path) -> Generator[Path, None, None]:
        """Generate a sequence of all yaml files in path."""
        for cur_root, dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".yaml"):
                    continue
                yield Path(os.path.join(cur_root, name))

    @classmethod
    def iter_items(cls, root: Path) -> Generator[ItemInFile, None, None]:
        """Generate DebusineTaskConfiguration items for all files in a path."""
        for path in cls.iter_files(root):
            relpath = path.relative_to(root)
            try:
                with path.open() as fd:
                    data = yaml.safe_load(fd)

                if isinstance(data, list):
                    for pos, item in enumerate(data):
                        yield ItemInFile(
                            item=DebusineTaskConfiguration(**item),
                            path=relpath,
                            position=pos,
                        )
                else:
                    yield ItemInFile(
                        item=DebusineTaskConfiguration(**data),
                        path=relpath,
                    )
            except Exception as e:
                e.add_note(f"source: {relpath}")
                raise

    @classmethod
    def is_checkout(cls, root: Path) -> bool:
        """Check if there is a task configuration checkout at path."""
        return (root / "MANIFEST").exists()

    @classmethod
    def read_manifest(cls, root: Path) -> Manifest:
        """Load manifest data from the repo at the given path."""
        path = root / "MANIFEST"
        try:
            with path.open() as fd:
                data = yaml.safe_load(fd)
            return Manifest(**data)
        except Exception as exc:
            exc.add_note(f"manifest: {path}")
            raise

    @classmethod
    def from_path(cls, root: Path) -> "LocalTaskConfigurationRepository":
        """Instantiate a local repository from a path."""
        if GitTaskConfigurationRepository.is_git(root):
            return GitTaskConfigurationRepository(root)
        else:
            return LocalTaskConfigurationRepository(root)


class GitTaskConfigurationRepository(LocalTaskConfigurationRepository):
    """LocalTaskConfigurationRepository which is a git repository."""

    def git(
        self, args: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in the repository and return its output."""
        cmd = ["git", "-C", self.root.as_posix()] + args
        res = subprocess.run(cmd, capture_output=True, text=True)
        if check and res.returncode != 0:
            try:
                res.check_returncode()
            except Exception as e:
                e.add_note("stderr:\n" + res.stderr)
                raise
        return res

    def is_dirty(self) -> bool:
        """Check if the git repository is dirty."""
        res = self.git(["status", "--porcelain"], check=True)
        for line in res.stdout.splitlines():
            if line.endswith("/"):
                return True
            elif line.endswith(".yaml"):
                return True
            elif line[3:] == "MANIFEST":
                return True
        return False

    def git_commit(self) -> str | None:
        """Return the hash of the current git commit."""
        res = self.git(["rev-parse", "HEAD"], check=False)
        if res.returncode == 0:
            return res.stdout.strip()
        # rev-parse may fail if the git repo has just been initialized
        return None

    def has_commit(self, commit: str) -> bool:
        """Check if the given commit hash is in our history."""
        res = self.git(
            ["merge-base", "--is-ancestor", commit, "HEAD"], check=False
        )
        if res.returncode == 0:
            return True
        return False

    def pull(
        self,
        repo: RemoteTaskConfigurationRepository,
        logger: logging.Logger,
    ) -> PullStats:
        """
        Pull changes from another repository.

        :param repo: repository to pull from
        :param logger: logger to use to give user feedback about what changed
        """
        if self.is_dirty():
            raise InvalidRepository(
                "git working directory is dirty: please commit changes"
                " before pulling from debusine"
            )

        return self._pull(repo, logger, preserve_deleted=False)

    @classmethod
    def is_git(cls, path: Path) -> bool:
        """
        Check if the given path contains a git repository.

        If git is not installed, it returns False.
        """
        if (git := shutil.which("git")) is None:
            return False
        res = subprocess.run(
            [git, "-C", path.as_posix(), "rev-parse"], capture_output=True
        )
        return res.returncode == 0
