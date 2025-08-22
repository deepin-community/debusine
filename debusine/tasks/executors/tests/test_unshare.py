# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the UnshareExecutor class."""

import os
import shlex
import stat
from pathlib import Path, PurePath
from shutil import rmtree
from subprocess import PIPE
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, call

import psutil

from debusine.client.debusine import Debusine
from debusine.tasks import Noop
from debusine.tasks.executors.base import (
    ExecutorStatistics,
    ImageNotDownloadedError,
    InstanceNotRunning,
    executor_class,
)
from debusine.tasks.executors.unshare import UnshareExecutor, UnshareInstance
from debusine.tasks.tests.helper_mixin import ExternalTaskHelperMixin
from debusine.test import TestCase


class UnshareExecutorTests(ExternalTaskHelperMixin[Noop], TestCase):
    """Unit tests for UnshareExecutor."""

    def setUp(self) -> None:
        """Mock the Debusine API for tests."""
        super().setUp()
        self.debusine_api = MagicMock(spec=Debusine)
        self.image_artifact = self.mock_image_download(self.debusine_api)

        self.executor = UnshareExecutor(self.debusine_api, 42)

    def test_backend_name(self) -> None:
        """Test that the backend_name attribute was set."""
        self.assertEqual(UnshareExecutor.backend_name, "unshare")

    def test_available(self) -> None:
        """Test that available() returns True if unshare is available."""
        self.mock_is_command_available({"unshare": True})
        self.assertTrue(UnshareExecutor.available())

    def test_available_no_unshare(self) -> None:
        """Test that available() returns False if unshare is not available."""
        self.mock_is_command_available({"unshare": False})
        self.assertFalse(UnshareExecutor.available())

    def test_instantiation_fetches_artifact(self) -> None:
        """Test that instantiating UnshareExecutor fetches the artifact."""
        self.assertEqual(self.executor.system_image, self.image_artifact)

    def test_download_image(self) -> None:
        """Test that download_image calls ImageCache.download_image."""
        response = self.executor.download_image()
        expected_path = self.image_cache_path / "42/system.tar.xz"
        self.assertEqual(self.executor._local_path, expected_path)
        self.assertEqual(response, str(expected_path))

    def test_image_name(self) -> None:
        """Test that image_name returns the previously downloaded image path."""
        self.executor.download_image()
        response = self.executor.image_name()
        expected_path = self.image_cache_path / "42/system.tar.xz"
        self.assertEqual(response, str(expected_path))

    def test_image_name_not_downloaded(self) -> None:
        """Test that image_name fails if there is no name."""
        self.assertRaises(ImageNotDownloadedError, self.executor.image_name)

    def test_autopkgtest_virt_server(self) -> None:
        """Test that autopkgtest_virt_server returns unshare."""
        self.assertEqual(self.executor.autopkgtest_virt_server(), "unshare")

    def test_autopkgtest_virt_args(self) -> None:
        """Test that autopkgtest_virt_args returns sane arguments."""
        self.executor._local_path = Path("/not/used/path/system.tar.xz")
        self.assertEqual(
            self.executor.autopkgtest_virt_args(),
            [
                "--arch",
                "amd64",
                "--release",
                "bookworm",
                "--tarball",
                str(self.executor._local_path),
            ],
        )

    def test_autopkgtest_virt_args_before_download(self) -> None:
        """Test that autopkgtest_virt_args requires download."""
        self.assertRaises(
            ImageNotDownloadedError, self.executor.autopkgtest_virt_args
        )

    def test_executor_class_finds_unshare(self) -> None:
        """Test that executor_class() supports unshare."""
        instance = executor_class("unshare")
        self.assertEqual(instance, UnshareExecutor)

    def test_create(self) -> None:
        """Test create() return UnshareInstance instance."""
        self.executor._local_path = self.create_temporary_file()
        unshare_instance = self.executor.create()

        self.assertIsInstance(unshare_instance, UnshareInstance)

        self.assertEqual(unshare_instance._image, self.executor._local_path)

    def test_create_raise_runtime_error(self) -> None:
        """Test create() raise ImageNotDownloadedError: _local_path is None."""
        msg_starts_with = r"^UnshareExecutor\.download_image\(\) should have"
        with self.assertRaisesRegex(ImageNotDownloadedError, msg_starts_with):
            self.executor.create()

    def test_get_statistics_without_instance(self) -> None:
        """`get_statistics` returns some statistics without an instance."""
        memory = psutil.virtual_memory()

        with mock.patch("psutil.virtual_memory", return_value=memory):
            statistics = UnshareExecutor.get_statistics(None)

        self.assertEqual(
            statistics,
            ExecutorStatistics(
                memory=memory.used,
                available_memory=memory.total,
                cpu_count=psutil.cpu_count() or 1,
            ),
        )

    def test_get_statistics_with_instance(self) -> None:
        """`get_statistics` returns all statistics with an instance."""
        image_file = self.create_temporary_file()
        instance = UnshareInstance(image_file)
        instance._extracted = self.create_temporary_directory()
        instance._root.mkdir()
        statvfs = os.statvfs(instance._root)
        memory = psutil.virtual_memory()

        with (
            mock.patch("psutil.virtual_memory", return_value=memory),
            mock.patch("os.statvfs", return_value=statvfs),
        ):
            statistics = UnshareExecutor.get_statistics(instance)

        self.assertEqual(
            statistics,
            ExecutorStatistics(
                disk_space=(
                    (statvfs.f_blocks - statvfs.f_bfree) * statvfs.f_frsize
                ),
                memory=memory.used,
                available_disk_space=statvfs.f_blocks * statvfs.f_frsize,
                available_memory=memory.total,
                cpu_count=psutil.cpu_count() or 1,
            ),
        )


class UnshareInstanceTests(TestCase):
    """Tests for UnshareInstance class."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.image_file = self.create_temporary_file()

        self.instance = UnshareInstance(self.image_file)

        (
            self.run_unshare_command_patcher,
            self.run_unshare_command_mocked,
        ) = self.patch_run_unshare_command()

    def tearDown(self) -> None:
        """Remove any unpacked instances during tests."""
        if self.instance._extracted:
            rmtree(self.instance._extracted)
        super().tearDown()

    def patch_run_unshare_command(self) -> tuple[Any, MagicMock]:
        """Patch _run_share_cmd() and return its patcher and mock."""
        patcher = mock.patch(
            "debusine.tasks.executors.unshare."
            "UnshareInstance._run_unshare_command"
        )
        mocked = patcher.start()

        self.addCleanup(patcher.stop)
        return patcher, mocked

    def test_start(self) -> None:
        """start() uncompress the image."""
        _, run_unshare_command_mocked = self.patch_run_unshare_command()

        self.instance._image = MagicMock(spec=Path)

        self.instance.start()

        assert self.instance._extracted is not None
        self.assertEqual(
            stat.S_IMODE(self.instance._extracted.stat().st_mode), 0o755
        )

        root = str(self.instance._root)

        image_file_open_context = self.instance._image.open(
            "rb"
        ).__enter__.return_value

        expected_calls = [
            call(["--map-root-user", "chown", "1:1", root]),
            call(
                [
                    "--setuid=0",
                    "--setgid=0",
                    "tar",
                    "-C",
                    root,
                    "--xz",
                    "-x",
                ],
                stdin=image_file_open_context,
            ),
        ]

        run_unshare_command_mocked.assert_has_calls(expected_calls)

    def test_stop(self) -> None:
        """stop() delete the contents using unshare."""
        self.instance.start()

        extracted = self.instance._extracted
        assert extracted is not None
        root = self.instance._root

        self.instance.stop()

        self.run_unshare_command_mocked.assert_called_with(
            ["--map-root-user", "rm", "-rf", str(root)]
        )

        self.assertIsNone(self.instance._extracted)
        self.assertFalse(extracted.exists())

    def test_restart(self) -> None:
        """restart() is noop for a running instance."""
        self.instance.start()
        self.instance.restart()

    def test_do_file_push(self) -> None:
        """do_file_push() call _run_unshare_command."""
        self.instance.start()

        uid = 0
        gid = 101

        target = PurePath("/tmp/some_file.txt")

        inner_file = self.instance._root / target.relative_to("/")

        inner_parent_directory = inner_file.parent
        inner_parent_directory.mkdir(parents=True)

        source = MagicMock(spec=Path)
        source_open_context = source.open("rb").__enter__.return_value

        self.instance.do_file_push(source, target, uid, gid, mode=0o644)

        expected_args = [
            f"--setuid={uid}",
            f"--setgid={gid}",
            "sh",
            "-c",
            f"cat > {inner_file} && chmod 644 {inner_file}",
        ]

        self.run_unshare_command_mocked.assert_called_with(
            expected_args, stdin=source_open_context, check=True
        )

    def test_file_pull(self) -> None:
        """file_pull() copies the file."""
        self.instance.start()

        source = PurePath("/etc/hosts")
        target = self.create_temporary_file()

        self.instance.file_pull(source, target)

        self.run_unshare_command_mocked.assert_called_with(
            [
                "--setuid=0",
                "--setgid=0",
                "--map-root-user",
                "cp",
                "--no-preserve=ownership,mode",
                str(self.instance._root / source.relative_to("/")),
                str(target),
            ],
            check=True,
        )

    def test_run_as_non_root(self) -> None:
        """run(): call _run_unshare_command and return its return value."""
        self.instance.start()
        cmd = ["lintian", "--info"]
        self.instance._uid_map["_debusine"] = 42
        actual = self.instance.run(cmd)
        root = shlex.quote(str(self.instance._root))
        self.run_unshare_command_mocked.assert_has_calls(
            [
                call(
                    [
                        "--setuid=0",
                        "--setgid=0",
                        "--kill-child",
                        "--pid",
                        "--mount",
                        f"--mount-proc={self.instance._root}/proc",
                        "sh",
                        "-c",
                        f"mount --rbind /dev {root}/dev && "
                        f"exec /usr/sbin/chroot {root} "
                        f"/sbin/runuser -u _debusine -- {' '.join(cmd)}",
                    ],
                    stderr=PIPE,
                    stdout=PIPE,
                    text=None,
                ),
            ],
        )

        self.assertEqual(actual, self.run_unshare_command_mocked.return_value)

    def test_run_as_root(self) -> None:
        """run(): call _run_unshare_command as root and return."""
        self.instance.start()
        cmd = ["lintian", "--info"]
        actual = self.instance.run(cmd, run_as_root=True)
        root = shlex.quote(str(self.instance._root))
        self.run_unshare_command_mocked.assert_has_calls(
            [
                call(
                    [
                        "--setuid=0",
                        "--setgid=0",
                        "--kill-child",
                        "--pid",
                        "--mount",
                        f"--mount-proc={self.instance._root}/proc",
                        "sh",
                        "-c",
                        f"mount --rbind /dev {root}/dev && "
                        f"exec /usr/sbin/chroot {root} "
                        f"{' '.join(cmd)}",
                    ],
                    stderr=PIPE,
                    stdout=PIPE,
                    text=None,
                ),
            ],
        )

        self.assertEqual(actual, self.run_unshare_command_mocked.return_value)

    def patch_unshare_command_args(self, command: str, args: list[str]) -> None:
        """Patch UnshareExecutor._unshare_command and _unshare_default_args."""
        unshare_command = mock.patch(
            "debusine.tasks.executors.unshare."
            "UnshareInstance.unshare_command",
            new=command,
        )
        unshare_command.start()
        self.addCleanup(unshare_command.stop)

        unshare_default_args = mock.patch(
            "debusine.tasks.executors.unshare."
            "UnshareInstance._unshare_default_args",
            new=args,
        )
        unshare_default_args.start()
        self.addCleanup(unshare_default_args.stop)

    def test_run_unshare_command(self) -> None:
        """run_unshare_command() runs a command, output is as expected."""
        self.run_unshare_command_patcher.stop()
        self.patch_unshare_command_args("echo", ["-n"])

        output = "test"
        completed = UnshareInstance._run_unshare_command([output], text=True)

        self.assertEqual(completed.stdout, output)
        self.assertEqual(completed.returncode, 0)

    def test_run_unshare_command_with_stderr_stdout(self) -> None:
        """run_unshare_command() runs a command with stderr and stdout."""
        self.run_unshare_command_patcher.stop()

        self.patch_unshare_command_args("sh", ["-c"])

        stderr_contents = "hello stderr"
        stdout_contents = "hello stdout"

        stderr_file_path = self.create_temporary_file()
        stdout_file_path = self.create_temporary_file()

        with stderr_file_path.open("wb") as stderr_file:
            with stdout_file_path.open("wb") as stdout_file:
                UnshareInstance._run_unshare_command(
                    [
                        f"printf %s '{stderr_contents}' 1>&2; "
                        f"printf %s '{stdout_contents}'",
                    ],
                    stderr=stderr_file.fileno(),
                    stdout=stdout_file.fileno(),
                )

        self.assertEqual(stderr_file_path.read_text(), stderr_contents)
        self.assertEqual(stdout_file_path.read_text(), stdout_contents)

    def test_root(self) -> None:
        """_root property returns self._extracted / root."""
        self.instance._extracted = self.create_temporary_directory()

        self.assertEqual(self.instance._root, self.instance._extracted / "root")

    def test_root_raise_runtime_error(self) -> None:
        """start() must be called before using _root property."""
        msg_starts_with = (
            r"^UnshareInstance.start\(\) must be called before using _root$"
        )
        with self.assertRaisesRegex(InstanceNotRunning, msg_starts_with):
            self.instance._root
