# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for client task configuration handling."""

import logging
import os
import shutil
import subprocess
from collections.abc import Collection
from pathlib import Path
from typing import Any, ClassVar
from unittest import SkipTest, mock

import yaml

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import DebusineTaskConfiguration, TaskTypes
from debusine.client.models import TaskConfigurationCollection
from debusine.client.task_configuration import (
    GitTaskConfigurationRepository,
    InvalidRepository,
    ItemInFile,
    LocalTaskConfigurationRepository,
    Manifest,
    PullStats,
    RemoteTaskConfigurationRepository,
    TaskConfigKey,
)
from debusine.test import TestCase


class PullStatsTests(TestCase):
    """Test PullStats class."""

    def test_has_changes(self) -> None:
        for args, expected in (
            ((0, 0, 0, 0), False),
            ((1, 0, 0, 0), True),
            ((0, 1, 0, 0), True),
            ((0, 0, 1, 0), True),
            ((0, 0, 0, 1), False),
            ((1, 1, 1, 1), True),
        ):
            with self.subTest(args=args):
                stats = PullStats(*args)
                self.assertEqual(stats.has_changes(), expected)


class TaskConfigKeyTests(TestCase):
    """Test TaskConfigKey class."""

    def test_from_item(self) -> None:
        for item, expected in (
            (
                DebusineTaskConfiguration(
                    task_type=TaskTypes.WORKER, task_name="noop"
                ),
                TaskConfigKey(TaskTypes.WORKER, "noop", None, None),
            ),
            (
                DebusineTaskConfiguration(
                    task_type=TaskTypes.WORKER,
                    task_name="noop",
                    subject="subject",
                    context="context",
                ),
                TaskConfigKey(TaskTypes.WORKER, "noop", "subject", "context"),
            ),
        ):
            with self.subTest(item=item):
                self.assertEqual(TaskConfigKey.from_item(item), expected)


class TaskConfigurationRepositoryTestsBase(TestCase):
    """Base for TaskConfigurationRepository tests."""

    config1: ClassVar[DebusineTaskConfiguration]
    config2: ClassVar[DebusineTaskConfiguration]
    template: ClassVar[DebusineTaskConfiguration]
    logger: ClassVar[logging.Logger]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.config1 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        cls.config2 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            context="context",
            subject="subject",
        )
        cls.template = DebusineTaskConfiguration(
            template="template", comment="this is a template"
        )
        cls.logger = logging.getLogger("debusine.client.tests")

    def manifest(
        self,
        workspace: str = "System",
        pk: int = 42,
        name: str = "default",
        data: dict[str, Any] | None = None,
    ) -> Manifest:
        """Create a Manifest."""
        return Manifest(
            workspace=workspace,
            collection=TaskConfigurationCollection(
                id=pk, name=name, data=data or {}
            ),
        )


class RemoteTaskConfigurationRepositoryTestsBase(
    TaskConfigurationRepositoryTestsBase
):
    """Test RemoteTaskConfigurationRepository class."""

    def test_from_items(self) -> None:
        repo = RemoteTaskConfigurationRepository.from_items(
            self.manifest(), [self.config1, self.config2, self.template]
        )
        self.assertEqual(repo.manifest, self.manifest())
        self.assertEqual(
            repo.entries,
            {
                TaskConfigKey.from_item(self.config1): self.config1,
                TaskConfigKey.from_item(self.config2): self.config2,
                TaskConfigKey.from_item(self.template): self.template,
            },
        )


class LocalTaskConfigurationRepositoryTestsBase(
    TaskConfigurationRepositoryTestsBase
):
    """Base for LocalTaskConfigurationRepository tests."""

    path_config1: ClassVar[Path]
    path_config2: ClassVar[Path]
    path_template: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path_config1 = Path("configs/config1.yaml")
        cls.path_config2 = Path("configs/config2.yaml")
        cls.path_template = Path("templates/template.yaml")

    def setUp(self) -> None:
        super().setUp()
        self.root = self.create_temporary_directory()

    def make_remote_repo(
        self, items: list[DebusineTaskConfiguration] | None = None
    ) -> RemoteTaskConfigurationRepository:
        repo = RemoteTaskConfigurationRepository(self.manifest())
        for item in items or []:
            repo.add(item)
        return repo

    def write(
        self,
        relpath: Path,
        item: pydantic.BaseModel | Collection[pydantic.BaseModel],
    ) -> None:
        """Write an item to a file in self.root."""
        path = self.root / relpath
        path.parent.mkdir(exist_ok=True, parents=True)
        with path.open("wt") as fd:
            if isinstance(item, pydantic.BaseModel):
                yaml.safe_dump(item.dict(), fd)
            else:
                yaml.safe_dump([i.dict() for i in item], fd)

    def list_files(self) -> list[Path]:
        """List files present in self.root."""
        res: list[Path] = []
        for cur_root, dirs, files in os.walk(self.root):
            for name in files:
                res.append(
                    Path(os.path.join(cur_root, name)).relative_to(self.root)
                )
        return sorted(res)

    def assertFileContents(
        self,
        relpath: Path,
        expected: pydantic.BaseModel | Collection[pydantic.BaseModel],
    ) -> None:
        """Check that path points to a YAML file with the given contents."""
        path = self.root / relpath
        with path.open() as fd:
            actual = yaml.safe_load(fd)
        if isinstance(expected, pydantic.BaseModel):
            self.assertEqual(actual, expected.dict())
        else:
            self.assertEqual(actual, [i.dict() for i in expected])


class LocalTaskConfigurationRepositoryTests(
    LocalTaskConfigurationRepositoryTestsBase
):
    """Test LocalTaskConfigurationRepository class."""

    def make_local_repo(
        self,
    ) -> LocalTaskConfigurationRepository:
        """Create a LocalTaskConfigurationRepository with manifest."""
        return LocalTaskConfigurationRepository(self.root)

    def test_is_dirty(self) -> None:
        repo = self.make_local_repo()
        self.assertFalse(repo.is_dirty())

    def test_git_commit(self) -> None:
        repo = self.make_local_repo()
        self.assertIsNone(repo.git_commit())

    def test_has_commit(self) -> None:
        repo = self.make_local_repo()
        self.assertFalse(repo.has_commit("0" * 40))

    def test_save_manifest_annotate_exceptions(self) -> None:
        repo = self.make_local_repo()
        repo.manifest = self.manifest()
        (repo.root / "MANIFEST").mkdir()
        with self.assertRaises(OSError) as exc:
            repo.write_manifest()
        self.assertEqual(
            exc.exception.__notes__, [f"manifest: {repo.root}/MANIFEST"]
        )

    def test_init_empty_dir(self) -> None:
        repo = self.make_local_repo()
        self.assertEqual(repo.root, self.root)
        self.assertEqual(repo.manifest_path, self.root / "MANIFEST")
        self.assertIsNone(repo.manifest)
        self.assertEqual(repo.entries, {})

    def test_init(self) -> None:
        self.write(Path("MANIFEST"), self.manifest())
        self.write(self.path_config1, self.config1)
        self.write(self.path_config2, self.config2)
        self.write(self.path_template, self.template)

        repo = self.make_local_repo()
        self.assertEqual(repo.root, self.root)
        self.assertEqual(repo.manifest_path, self.root / "MANIFEST")
        self.assertEqual(repo.manifest, self.manifest())
        self.assertEqual(
            repo.entries,
            {
                TaskConfigKey.from_item(self.config1): ItemInFile(
                    self.config1, self.path_config1
                ),
                TaskConfigKey.from_item(self.config2): ItemInFile(
                    self.config2, self.path_config2
                ),
                TaskConfigKey.from_item(self.template): ItemInFile(
                    self.template, self.path_template
                ),
            },
        )

    def test_init_multiple_items_per_file(self) -> None:
        self.write(Path("MANIFEST"), self.manifest())
        self.write(self.path_config1, [self.config1, self.config2])
        self.write(self.path_template, self.template)

        repo = self.make_local_repo()
        self.assertEqual(repo.root, self.root)
        self.assertEqual(repo.manifest_path, self.root / "MANIFEST")
        self.assertEqual(repo.manifest, self.manifest())
        self.assertEqual(
            repo.entries,
            {
                TaskConfigKey.from_item(self.config1): ItemInFile(
                    self.config1, self.path_config1, 0
                ),
                TaskConfigKey.from_item(self.config2): ItemInFile(
                    self.config2, self.path_config1, 1
                ),
                TaskConfigKey.from_item(self.template): ItemInFile(
                    self.template, self.path_template
                ),
            },
        )

    def test_iter_items(self) -> None:
        self.write(self.path_config1, self.config1)
        self.write(self.path_config2, self.config2)
        self.write(self.path_template, self.template)
        self.assertCountEqual(
            list(LocalTaskConfigurationRepository.iter_items(self.root)),
            [
                ItemInFile(self.config1, self.path_config1),
                ItemInFile(self.config2, self.path_config2),
                ItemInFile(self.template, self.path_template),
            ],
        )

    def test_iter_items_file_with_many(self) -> None:
        self.write(self.path_config1, [self.config1, self.config2])
        self.write(self.path_template, self.template)
        self.assertCountEqual(
            list(LocalTaskConfigurationRepository.iter_items(self.root)),
            [
                ItemInFile(self.config1, self.path_config1, 0),
                ItemInFile(self.config2, self.path_config1, 1),
                ItemInFile(self.template, self.path_template),
            ],
        )

    def test_iter_items_ignore_non_yaml(self) -> None:
        (self.root / "README").write_text("Thank you for reading")
        (self.root / "data.json").write_text("{}")
        self.assertEqual(
            list(LocalTaskConfigurationRepository.iter_items(self.root)), []
        )

    def test_iter_items_invalid_yaml(self) -> None:
        (self.root / "test.yaml").write_text("{")
        with self.assertRaisesRegex(
            yaml.parser.ParserError, r"parsing a flow node"
        ) as exc:
            self.assertEqual(
                list(LocalTaskConfigurationRepository.iter_items(self.root)), []
            )
        self.assertEqual(exc.exception.__notes__[0], "source: test.yaml")

    def test_iter_items_invalid_data(self) -> None:
        (self.root / "test.yaml").write_text("test: true")
        with self.assertRaisesRegex(
            ValueError,
            (
                r"task_type, task_name should be set"
                r" for non-template task configuration entries"
            ),
        ) as exc:
            self.assertEqual(
                list(LocalTaskConfigurationRepository.iter_items(self.root)), []
            )
        self.assertEqual(exc.exception.__notes__[0], "source: test.yaml")

    def test_write_manifest_unset(self) -> None:
        repo = self.make_local_repo()
        with self.assertRaisesRegex(
            InvalidRepository, "Repository manifest has not been set"
        ):
            repo.write_manifest()

    def test_write_manifest(self) -> None:
        repo = self.make_local_repo()
        repo.manifest = self.manifest()
        self.assertFalse(repo.manifest_path.exists())
        repo.write_manifest()
        self.assertTrue(repo.manifest_path.exists())
        self.assertFileContents(Path("MANIFEST"), repo.manifest)

    def test_pull_new_repo(self) -> None:
        server_repo = self.make_remote_repo([self.config1, self.template])
        local_repo = self.make_local_repo()
        with self.assertLogs(self.logger) as log:
            stats = local_repo.pull(server_repo, self.logger)
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            [
                "new/templates/template.yaml: new item",
                "new/worker_noop/any_any.yaml: new item",
            ],
        )
        self.assertEqual(stats, PullStats(added=2))
        self.assertCountEqual(
            self.list_files(),
            [
                path_manifest := Path("MANIFEST"),
                path_template := Path("new/templates/template.yaml"),
                path_config1 := Path("new/worker_noop/any_any.yaml"),
            ],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFileContents(path_template, self.template)
        self.assertFileContents(path_config1, self.config1)

    def test_pull_existing_repo(self) -> None:
        server_repo = self.make_remote_repo([self.config1, self.template])

        path_manifest = Path("MANIFEST")
        path_config1 = Path("config.yaml")
        path_template = Path("template.yaml")

        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, self.config1)
        self.write(path_template, self.template)
        local_repo = self.make_local_repo()

        with self.assertNoLogs(self.logger.name):
            stats = local_repo.pull(server_repo, self.logger)
        self.assertEqual(stats, PullStats(unchanged=2))
        self.assertCountEqual(
            self.list_files(),
            [
                path_manifest,
                path_template,
                path_config1,
            ],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFileContents(path_template, self.template)
        self.assertFileContents(path_config1, self.config1)

    def test_pull_update(self) -> None:
        old = self.config1
        new = self.config1.copy(update={"comment": "updated"})

        server_repo = self.make_remote_repo([new])

        path_manifest = Path("MANIFEST")
        path_config1 = Path("config.yaml")
        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, old)
        local_repo = self.make_local_repo()

        with self.assertLogs(self.logger) as log:
            stats = local_repo.pull(server_repo, self.logger)
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            ["config.yaml: item updated"],
        )
        self.assertEqual(stats, PullStats(updated=1))
        self.assertCountEqual(
            self.list_files(),
            [
                path_manifest,
                path_config1,
            ],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFileContents(path_config1, new)

    def test_pull_update_preserves_mtime(self) -> None:
        old = self.config1
        new = self.config1.copy(update={"comment": "updated"})

        server_repo = self.make_remote_repo([new, self.config2])

        path_manifest = Path("MANIFEST")
        path_config1 = Path("config.yaml")
        path_config2 = Path("config2.yaml")
        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, old)
        self.write(path_config2, self.config2)
        orig_stat1 = (self.root / path_config1).stat()
        orig_stat2 = (self.root / path_config2).stat()
        local_repo = self.make_local_repo()

        with self.assertLogs(self.logger) as log:
            stats = local_repo.pull(server_repo, self.logger)
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            ["config.yaml: item updated"],
        )
        self.assertEqual(stats, PullStats(updated=1, unchanged=1))
        self.assertCountEqual(
            self.list_files(),
            [
                path_manifest,
                path_config1,
                path_config2,
            ],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFileContents(path_config1, new)
        self.assertFileContents(path_config2, self.config2)
        self.assertLessEqual(
            orig_stat1.st_mtime, (self.root / path_config1).stat().st_mtime
        )
        self.assertEqual(
            orig_stat2.st_mtime, (self.root / path_config2).stat().st_mtime
        )

    def test_pull_update_second_in_file(self) -> None:
        old = self.config1
        new = self.config1.copy(update={"comment": "updated"})

        server_repo = self.make_remote_repo([new])

        path_manifest = Path("MANIFEST")
        path_config1 = Path("config.yaml")
        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, [self.config2, old])
        local_repo = self.make_local_repo()

        with self.assertLogs(self.logger) as log:
            stats = local_repo.pull(server_repo, self.logger)
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            ["config.yaml: item updated"],
        )
        self.assertEqual(stats, PullStats(updated=1))
        self.assertCountEqual(
            self.list_files(),
            [
                path_manifest,
                path_config1,
            ],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFileContents(path_config1, [self.config2, new])

    def test_pull_update_manifest_collection_data(self) -> None:
        server_repo = self.make_remote_repo()
        server_repo.manifest = self.manifest(data={"test": True})

        path_manifest = Path("MANIFEST")
        self.write(path_manifest, self.manifest())
        local_repo = self.make_local_repo()

        with self.assertNoLogs(self.logger.name):
            stats = local_repo.pull(server_repo, self.logger)
        self.assertEqual(stats, PullStats())
        self.assertCountEqual(self.list_files(), [path_manifest])
        self.assertFileContents(path_manifest, server_repo.manifest)

    def test_pull_delete(self) -> None:
        server_repo = self.make_remote_repo()

        path_manifest = Path("MANIFEST")
        path_config1 = Path("config.yaml")
        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, self.config1)
        local_repo = self.make_local_repo()

        with self.assertLogs(self.logger) as log:
            stats = local_repo._pull(
                server_repo, self.logger, preserve_deleted=False
            )
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            ["config.yaml: item deleted"],
        )
        self.assertEqual(stats, PullStats(deleted=1))
        self.assertCountEqual(
            self.list_files(),
            [path_manifest],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)

    def test_pull_delete_in_bundle(self) -> None:
        server_repo = self.make_remote_repo([self.config2])

        path_manifest = Path("MANIFEST")
        path_config1 = Path("config.yaml")
        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, [self.config1, self.config2])
        local_repo = self.make_local_repo()

        with self.assertLogs(self.logger) as log:
            stats = local_repo._pull(
                server_repo, self.logger, preserve_deleted=False
            )
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            ["config.yaml: item deleted"],
        )
        self.assertEqual(stats, PullStats(deleted=1, unchanged=1))
        self.assertCountEqual(
            self.list_files(),
            [path_manifest, path_config1],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFileContents(path_config1, self.config2)

    def test_pull_delete_clean_empty_dirs(self) -> None:
        server_repo = self.make_remote_repo()

        path_manifest = Path("MANIFEST")
        path_config1 = Path("configs/config.yaml")
        self.write(path_manifest, server_repo.manifest)
        self.write(path_config1, self.config1)
        local_repo = self.make_local_repo()

        self.assertTrue((self.root / "configs").is_dir())
        with self.assertLogs(self.logger) as log:
            stats = local_repo._pull(
                server_repo, self.logger, preserve_deleted=False
            )
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            ["configs/config.yaml: item deleted"],
        )
        self.assertEqual(stats, PullStats(deleted=1))
        self.assertCountEqual(
            self.list_files(),
            [path_manifest],
        )
        self.assertFileContents(path_manifest, server_repo.manifest)
        self.assertFalse((self.root / "configs").exists())

    def test_pull_repo_manifest_mismatch(self) -> None:
        server_repo = self.make_remote_repo()

        path_manifest = Path("MANIFEST")
        self.write(path_manifest, self.manifest(pk=7))
        local_repo = self.make_local_repo()

        with self.assertRaisesRegex(
            InvalidRepository,
            r"repo to pull refers to collection System/default \(42\)"
            r" while the checkout has collection System/default \(7\)",
        ):
            local_repo.pull(server_repo, self.logger)

        self.assertCountEqual(self.list_files(), [Path("MANIFEST")])

    def test_is_checkout(self) -> None:
        self.assertFalse(
            LocalTaskConfigurationRepository.is_checkout(self.root)
        )
        (self.root / "MANIFEST").write_text("")
        self.assertTrue(LocalTaskConfigurationRepository.is_checkout(self.root))

    def test_read_manifest_annotates_exceptions(self) -> None:
        with self.assertRaises(OSError) as exc:
            LocalTaskConfigurationRepository.read_manifest(self.root)
        self.assertEqual(
            exc.exception.__notes__, [f"manifest: {self.root}/MANIFEST"]
        )


class GitTaskConfigurationRepositoryTests(
    LocalTaskConfigurationRepositoryTestsBase
):
    """Test GitTaskConfigurationRepository class."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if shutil.which("git") is None:  # pragma: no cover
            raise SkipTest("git is not installed")

    def setUp(self) -> None:
        super().setUp()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")

    def make_local_repo(
        self,
    ) -> GitTaskConfigurationRepository:
        """Create a LocalTaskConfigurationRepository with manifest."""
        return GitTaskConfigurationRepository(self.root)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a git command in the repository and return its output."""
        cmd = ["git", "-C", self.root.as_posix()]
        cmd.extend(args)
        return subprocess.run(
            cmd, cwd=self.root, capture_output=True, text=True, check=True
        )

    def commit(self, message: str = "Commit message") -> None:
        """Commit changes in self.root."""
        self.git("add", ".")
        self.git("commit", "-m", message)

    def test_from_path(self) -> None:
        repo1 = LocalTaskConfigurationRepository.from_path(self.root)
        self.assertIsInstance(repo1, GitTaskConfigurationRepository)
        repo2 = LocalTaskConfigurationRepository.from_path(
            self.create_temporary_directory()
        )
        self.assertIsInstance(repo2, LocalTaskConfigurationRepository)

    def test_git_error_annotates_exception(self) -> None:
        repo = self.make_local_repo()
        shutil.rmtree(self.root / ".git")
        with self.assertRaises(subprocess.CalledProcessError) as exc:
            repo.git(["rev-parse", "HEAD"], check=True)
        self.assertEqual(exc.exception.__notes__[0][:8], "stderr:\n")

    def test_is_git(self) -> None:
        self.assertTrue(GitTaskConfigurationRepository.is_git(self.root))
        self.assertFalse(
            GitTaskConfigurationRepository.is_git(
                self.create_temporary_directory()
            )
        )
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(GitTaskConfigurationRepository.is_git(self.root))

    def test_is_dirty_empty(self) -> None:
        repo = self.make_local_repo()
        self.assertFalse(repo.is_dirty())

    def test_is_dirty_uncommitted_manifest(self) -> None:
        repo = self.make_local_repo()
        repo.manifest_path.write_text("test")
        self.assertTrue(repo.is_dirty())

    def test_is_dirty_uncommitted_yaml(self) -> None:
        repo = self.make_local_repo()
        self.write(Path("config.yaml"), self.config1)
        self.assertTrue(repo.is_dirty())

    def test_is_dirty_dir(self) -> None:
        repo = self.make_local_repo()
        (self.root / "testdir").mkdir()
        (self.root / "testdir" / "testfile").write_text("")
        self.assertTrue(repo.is_dirty())

    def test_is_dirty_uncommitted_other(self) -> None:
        repo = self.make_local_repo()
        self.write(Path("README"), self.config1)
        self.assertFalse(repo.is_dirty())

    def test_git_commit_empty(self) -> None:
        repo = self.make_local_repo()
        self.assertIsNone(repo.git_commit())

    def test_git_commit(self) -> None:
        repo = self.make_local_repo()
        repo.manifest = self.manifest()
        repo.write_manifest()
        self.commit()
        self.assertFalse(repo.is_dirty())
        self.assertIsNotNone(repo.git_commit())

    def test_has_commit(self) -> None:
        repo = self.make_local_repo()
        repo.manifest = self.manifest()
        repo.write_manifest()
        self.commit()
        first_commit = repo.git_commit()
        assert first_commit is not None
        self.write(Path("config1.yaml"), self.config1)
        self.commit()
        second_commit = repo.git_commit()
        assert second_commit is not None
        self.write(Path("config2.yaml"), self.config2)
        self.commit()
        third_commit = repo.git_commit()
        assert third_commit is not None
        self.assertTrue(repo.has_commit(first_commit))
        self.assertTrue(repo.has_commit(second_commit))
        self.assertTrue(repo.has_commit(third_commit))

        missing_commit = (
            ("0" if first_commit[0] != "0" else "1")
            + ("0" if second_commit[1] != "0" else "1")
            + ("0" if third_commit[2] != "0" else "1")
            + first_commit[3:]
        )
        self.assertFalse(repo.has_commit(missing_commit))

    def test_pull_dirty(self) -> None:
        server_repo = self.make_remote_repo()
        self.write(Path("config.yaml"), self.config1)
        local_repo = self.make_local_repo()
        with self.assertRaisesRegex(
            InvalidRepository,
            r"git working directory is dirty: please commit changes"
            r" before pulling from debusine",
        ):
            local_repo.pull(server_repo, self.logger)

    def test_pull(self) -> None:
        commit = "0" * 40
        server_repo = self.make_remote_repo()
        server_repo.manifest.collection.data["git_commit"] = commit
        self.write(Path("MANIFEST"), self.manifest(data={"git_commit": commit}))
        self.commit()
        local_repo = self.make_local_repo()
        with self.assertNoLogs(self.logger.name):
            local_repo.pull(server_repo, self.logger)
