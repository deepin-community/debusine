# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Executor basic interface."""
from pathlib import Path, PurePath
from subprocess import CalledProcessError, CompletedProcess, PIPE
from typing import Any, AnyStr, Literal
from unittest.mock import Mock, call, patch

from debusine.tasks.executors.base import (
    ExecutorStatistics,
    InstanceInterface,
    InstanceNotRunning,
    InstanceRunning,
    _backends,
    analyze_worker_all_executors,
)
from debusine.test import TestCase


class TestInstance(InstanceInterface):
    """Instance used for testing."""

    __test__ = False

    started: bool = False

    def is_started(self) -> bool:
        """Return the started status."""
        return self.started

    def do_start(self) -> None:
        """Not implemented."""
        raise NotImplementedError()

    def do_stop(self) -> None:
        """Not implemented."""
        raise NotImplementedError()

    def do_restart(self) -> None:
        """Not implemented."""
        raise NotImplementedError()

    def do_file_push(
        self, source: Path, target: PurePath, uid: int, gid: int, mode: int
    ) -> None:
        """Not implemented."""
        raise NotImplementedError()

    def do_file_pull(self, source: PurePath, target: Path) -> None:
        """Not implemented."""
        raise NotImplementedError()

    def do_run(
        self,
        args: list[str],
        text: Literal[True] | Literal[False] | None = None,
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[AnyStr]:
        """Not implemented."""
        raise NotImplementedError()

    @classmethod
    def get_statistics(
        cls, instance: InstanceInterface | None
    ) -> ExecutorStatistics:
        """Not implemented."""
        raise NotImplementedError()


class ExecutorTests(TestCase):
    """Unit tests for the Executor basic interface."""

    def test_backends_populated(self) -> None:
        """Test that we have populated some backends."""
        self.assertGreater(len(_backends), 0)

    def test_analyze_worker_all_executors_no_backends_available(self) -> None:
        """analyze_worker_all_executors copes with no available backends."""
        self.mock_is_command_available({})
        executors_metadata = analyze_worker_all_executors()
        self.assertNotEqual(executors_metadata, {})
        for key, value in executors_metadata.items():
            self.assertRegex(key, r"^executor:.*:available$")
            self.assertFalse(value)

    def test_analyze_worker_all_executors_unshare_available(self) -> None:
        """analyze_worker_all_executors reports available backends."""
        self.mock_is_command_available({"unshare": True})
        executors_metadata = analyze_worker_all_executors()
        self.assertTrue(executors_metadata["executor:unshare:available"])


class InstanceTests(TestCase):
    """Unit tests for the Instance basic interface."""

    def setUp(self) -> None:
        """Configure a test instance."""
        self.instance = TestInstance()

    def patch_instance(self, method: str) -> Mock:
        """Mock method on self.instance."""
        patcher = patch.object(self.instance, method, autospec=True)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_start(self) -> None:
        """Test that start calls do_start()."""
        do_start = self.patch_instance("do_start")
        self.instance.start()
        do_start.assert_called_once_with()

    def test_start_started(self) -> None:
        """Test that start requires a stopped instance."""
        self.instance.started = True
        with self.assertRaisesRegex(
            InstanceRunning, "Cannot start a started instance"
        ):
            self.instance.start()

    def test_stop(self) -> None:
        """Test that stop calls do_stop()."""
        self.instance.started = True
        do_stop = self.patch_instance("do_stop")
        self.instance.stop()
        do_stop.assert_called_once_with()

    def test_stop_stopped(self) -> None:
        """Test that stop requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot stop a stopped instance"
        ):
            self.instance.stop()

    def test_restart(self) -> None:
        """Test that restart calls do_restart()."""
        self.instance.started = True
        do_restart = self.patch_instance("do_restart")
        self.instance.restart()
        do_restart.assert_called_once_with()

    def test_restart_stopped(self) -> None:
        """Test that restart requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot restart a stopped instance"
        ):
            self.instance.restart()

    def test_file_push(self) -> None:
        """Test that file_push calls do_file_push()."""
        self.instance.started = True
        do_file_push = self.patch_instance("do_file_push")
        source = Path("source")
        dest = Path("/dest")
        self.instance.file_push(source, dest)
        do_file_push.assert_called_once_with(source, dest, 0, 0, 0o644)

    def test_file_push_stopped(self) -> None:
        """Test that file_push requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot push files to a stopped instance"
        ):
            self.instance.file_push(Path("source"), Path("/dest"))

    def test_file_push_relative(self) -> None:
        """Test that file_push requires a relative destination."""
        self.instance.started = True
        with self.assertRaisesRegex(
            ValueError, "target must be an absolute path"
        ):
            self.instance.file_push(Path("source"), Path("dest"))

    def test_file_pull(self) -> None:
        """Test that file_pull calls do_file_pull()."""
        self.instance.started = True
        do_file_pull = self.patch_instance("do_file_pull")
        source = Path("/source")
        dest = Path("dest")
        self.instance.file_pull(source, dest)
        do_file_pull.assert_called_once_with(source, dest)

    def test_file_pull_stopped(self) -> None:
        """Test that file_pull requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot pull files from a stopped instance"
        ):
            self.instance.file_pull(Path("/source"), Path("dest"))

    def test_file_pull_relative(self) -> None:
        """Test that file_pull requires a relative source."""
        self.instance.started = True
        with self.assertRaisesRegex(
            ValueError, "source must be an absolute path"
        ):
            self.instance.file_pull(Path("source"), Path("dest"))

    def test_directory_push(self) -> None:
        """Test that directory_push calls do_directory_push()."""
        self.instance.started = True
        do_directory_push = self.patch_instance("do_directory_push")
        source = Path("source")
        dest = Path("/dest")
        self.instance.directory_push(source, dest)
        do_directory_push.assert_called_once_with(source, dest, 0, 0)

    def test_directory_push_stopped(self) -> None:
        """Test that directory_push requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot push files to a stopped instance"
        ):
            self.instance.directory_push(Path("source"), Path("/dest"))

    def test_directory_push_relative(self) -> None:
        """Test that directory_push requires a relative destination."""
        self.instance.started = True
        with self.assertRaisesRegex(
            ValueError, "target must be an absolute path"
        ):
            self.instance.directory_push(Path("source"), Path("dest"))

    def test_do_directory_push(self) -> None:
        """do_directory_push creates the directories and copies the files."""
        self.instance.started = True

        temp_directory = self.create_temporary_directory()
        (source_directory := temp_directory / "source").mkdir()
        source_directory.chmod(0o755)
        (sub_directory := source_directory / "sub-directory").mkdir()
        sub_directory.chmod(0o755)
        (sub_sub_directory := sub_directory / "sub-sub-directory").mkdir()
        sub_sub_directory.chmod(0o755)

        (file1 := source_directory / "file1.txt").touch()
        file1.chmod(0o640)
        (file2 := sub_directory / "file2.sh").touch()
        file2.chmod(0o755)

        target = PurePath("/somewhere")

        mkdir_mock = self.patch_instance("do_mkdir")
        do_file_push_mock = self.patch_instance("do_file_push")
        self.instance.do_directory_push(source_directory, target, 101, 102)

        mkdir_mock.assert_has_calls(
            [
                call(PurePath("/somewhere/source"), 101, 102, 0o755, False),
                call(
                    PurePath("/somewhere/source/sub-directory"),
                    101,
                    102,
                    0o755,
                    False,
                ),
                call(
                    PurePath(
                        "/somewhere/source/sub-directory/sub-sub-directory"
                    ),
                    101,
                    102,
                    0o755,
                    False,
                ),
            ]
        )
        do_file_push_mock.assert_has_calls(
            [
                call(
                    Path(source_directory / "file1.txt"),
                    PurePath("/somewhere/source/file1.txt"),
                    101,
                    102,
                    0o640,
                ),
                call(
                    Path(source_directory / "sub-directory/file2.sh"),
                    PurePath("/somewhere/source/sub-directory/file2.sh"),
                    101,
                    102,
                    0o755,
                ),
            ]
        )

    def test_run(self) -> None:
        """Test that run calls do_run()."""
        self.instance.started = True
        do_run = self.patch_instance("do_run")
        self.instance.run(["cmd", "--arg"])
        do_run.assert_called_once_with(
            ["cmd", "--arg"], stderr=PIPE, stdout=PIPE
        )

    def test_run_stopped(self) -> None:
        """Test that run requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot run commands on a stopped instance"
        ):
            self.instance.run(["foo"])

    def test_run_check_output(self) -> None:
        """Test that _run_check_output calls run()."""
        with patch.object(self.instance, "run") as run_mock:
            self.instance._run_check_output(["true"])
        run_mock.assert_called_once_with(
            ["true"],
            check=True,
            run_as_root=True,
            stderr=None,
            stdout=PIPE,
            text=True,
        )

    def test_get_uid_uncached(self) -> None:
        """Test _get_uid when not cached."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.return_value = "testuser:x:101:105:Test User:/foo:/bin/sh"
            self.assertEqual(self.instance._get_uid("testuser"), 101)
        run_mock.assert_called_once_with(["getent", "passwd", "testuser"])
        self.assertEqual(self.instance._uid_map["testuser"], 101)

    def test_get_uid_cached(self) -> None:
        """Test _get_uid when cached."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            self.assertEqual(self.instance._get_uid("root"), 0)
        run_mock.assert_not_called()

    def test_get_uid_unknown(self) -> None:
        """Test _get_uid when not existent."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = CalledProcessError(
                cmd=["getent", "passwd", "testuser"], returncode=2
            )
            with self.assertRaisesRegex(
                KeyError, "User not found in instance: testuser"
            ):
                self.instance._get_uid("testuser")
        self.assertNotIn("testuser", self.instance._uid_map)

    def test_get_uid_unexpected_error(self) -> None:
        """Test _get_uid when not existent."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = CalledProcessError(
                cmd=["getent", "passwd", "testuser"], returncode=1
            )
            with self.assertRaises(CalledProcessError):
                self.instance._get_uid("testuser")

    def test_get_gid_uncached(self) -> None:
        """Test _get_gid when not cached."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.return_value = "testgroup:x:102:"
            self.assertEqual(self.instance._get_gid("testgroup"), 102)
        run_mock.assert_called_once_with(["getent", "group", "testgroup"])
        self.assertEqual(self.instance._gid_map["testgroup"], 102)

    def test_get_gid_cached(self) -> None:
        """Test _get_gid when cached."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            self.assertEqual(self.instance._get_gid("root"), 0)
        run_mock.assert_not_called()

    def test_get_gid_unknown(self) -> None:
        """Test _get_gid when not existent."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = CalledProcessError(
                cmd=["getent", "group", "testgroup"], returncode=2
            )
            with self.assertRaisesRegex(
                KeyError, "Group not found in instance: testgroup"
            ):
                self.instance._get_gid("testgroup")
        self.assertNotIn("testgroup", self.instance._gid_map)

    def test_get_gid_unexpected_error(self) -> None:
        """Test _get_gid when not existent."""
        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = CalledProcessError(
                cmd=["getent", "group", "testgroup"], returncode=1
            )
            with self.assertRaises(CalledProcessError):
                self.instance._get_gid("testgroup")

    def test_create_user_exists(self) -> None:
        """Test create_user when user exists."""
        self.instance._uid_map["testuser"] = 42
        with patch.object(self.instance, "_run_check_output") as run_mock:
            self.assertEqual(self.instance.create_user("testuser"), 42)
        run_mock.assert_not_called()

    def test_create_user_not_exists(self) -> None:
        """Test create_user when user doesn't exist."""

        def create_user(args: list[str]) -> str:
            """Fake for run_check_output that creates a user."""
            if args[0] == "useradd":
                self.instance._uid_map["testuser"] = 42
                return ""
            raise CalledProcessError(cmd=args, returncode=2)

        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = create_user
            self.assertEqual(self.instance.create_user("testuser"), 42)
        run_mock.assert_has_calls(
            [
                call(["getent", "passwd", "testuser"]),
                call(["useradd", "testuser"]),
            ]
        )

    def test_create_user_default_username(self) -> None:
        """Test create_user when no username is supplied."""
        self.instance._uid_map["_debusine"] = 42
        with patch.object(self.instance, "_run_check_output") as run_mock:
            self.assertEqual(self.instance.create_user(), 42)
        run_mock.assert_not_called()

    def test_mkdir(self) -> None:
        """Test that mkdir calls do_mkdir()."""
        self.instance.started = True
        do_mkdir = self.patch_instance("do_mkdir")
        dest = Path("/dest")
        self.instance.mkdir(dest)
        do_mkdir.assert_called_once_with(dest, 0, 0, 0o755, False)

    def test_mkdir_stopped(self) -> None:
        """Test that mkdir requires a started instance."""
        with self.assertRaisesRegex(
            InstanceNotRunning, "Cannot make directories in a stopped instance"
        ):
            self.instance.mkdir(Path("/dest"))

    def test_mkdir_relative(self) -> None:
        """Test that mkdir requires a relative target."""
        self.instance.started = True
        with self.assertRaisesRegex(
            ValueError, "target must be an absolute path"
        ):
            self.instance.mkdir(Path("dest"))

    def test_do_mkdir(self) -> None:
        """do_mkdir() use run() as expected."""
        uid = 101
        gid = 102
        target = PurePath("/etc/dir1")

        with patch.object(self.instance, "_run_check_output") as run_mocked:
            self.instance.do_mkdir(target, uid, gid, 0o755, False)

        run_expected_calls = [
            call(["mkdir", "--mode=755", str(target)]),
            call(["chown", f"{uid}:{gid}", "/etc/dir1"]),
        ]
        run_mocked.assert_has_calls(run_expected_calls)

    def test_do_mkdir_parents(self) -> None:
        """do_mkdir() passes --parents."""
        uid = 101
        gid = 102
        target = PurePath("/etc/dir1")

        with patch.object(self.instance, "_run_check_output") as run_mocked:
            self.instance.do_mkdir(target, uid, gid, 0o755, parents=True)

        run_expected_calls = [
            call(["mkdir", "--mode=755", "--parents", str(target)]),
            call(["chown", f"{uid}:{gid}", "/etc/dir1"]),
        ]
        run_mocked.assert_has_calls(run_expected_calls)

    def test_do_mkdir_root(self) -> None:
        """do_mkdir() skips chown for root."""
        target = PurePath("/etc/dir1")

        with patch.object(self.instance, "_run_check_output") as run_mocked:
            self.instance.do_mkdir(target, 0, 0, 0o755, False)

        run_mocked.assert_called_once_with(["mkdir", "--mode=755", str(target)])

    def test_do_mkdir_exists(self) -> None:
        """do_mkdir() raise FileExistsError."""
        self.instance.started = True
        target = PurePath("/some/directory")

        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = CalledProcessError(
                cmd=["mkdir"],
                returncode=1,
                stderr=(
                    f"mkdir: cannot create directory ‘{target}’: File exists"
                ),
            )
            msg = rf'^Cannot create directory: "{target}" already exists$'
            with self.assertRaisesRegex(FileExistsError, msg):
                self.instance.do_mkdir(target, 0, 0, 0o755, False)

    def test_do_mkdir_unexpected_error(self) -> None:
        """do_mkdir() raise CalledProcessError on an unknown error."""
        self.instance.started = True
        target = PurePath("/some/directory")

        with patch.object(self.instance, "_run_check_output") as run_mock:
            run_mock.side_effect = CalledProcessError(
                cmd=["mkdir"], returncode=1, stderr="mkdir: Something Happened!"
            )
            with self.assertRaises(CalledProcessError):
                self.instance.do_mkdir(target, 0, 0, 0o755, False)
