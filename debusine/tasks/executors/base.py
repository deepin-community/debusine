# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Interface for executor backends."""
import os
from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path, PurePath
from stat import S_IMODE
from subprocess import CalledProcessError, CompletedProcess, PIPE
from typing import Any, AnyStr, ClassVar, Literal, Optional, overload

from debusine.artifacts.models import ArtifactCategory
from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse
from debusine.tasks.models import BaseTaskDataModel

_backends = {}


class InstanceRunning(Exception):
    """Raised if the instance tries to start a started instance."""


class InstanceNotRunning(Exception):
    """Raised if the instance tries to use an instance that is not running."""


class ImageNotDownloadedError(Exception):
    """Raised if an Executor tries to use an image before downloading it."""


class ImageImportError(Exception):
    """Raised if an Executor fails to import an environment image."""


class ExecutorImageCategory(StrEnum):
    """Artifact categories that executors may expect for images."""

    TARBALL = str(ArtifactCategory.SYSTEM_TARBALL)
    IMAGE = str(ArtifactCategory.SYSTEM_IMAGE)


class ExecutorStatistics(BaseTaskDataModel):
    """
    Statistics for an executor.

    Similar to :py:class:`debusine.tasks.models.RuntimeStatistics`, but
    decoupled from that since tasks involve more than their executor.
    """

    #: Maximum disk space used, in bytes.
    disk_space: int | None = None

    #: Maximum RAM used, in bytes.
    memory: int | None = None

    #: Available disk space, in bytes; may be rounded.  (This is usually a
    #: property of the worker, but virtual machines can have more limited
    #: disk space.)
    available_disk_space: int | None = None

    #: Available RAM, in bytes; may be rounded.  (This is usually a property
    #: of the worker, but virtual machines can have more limited RAM.)
    available_memory: int | None = None

    #: Number of CPU cores.  (This is usually a property of the worker, but
    #: virtual machines can have different numbers of cores.)
    cpu_count: int | None = None


class ExecutorInterface(ABC):
    """
    Interface for managing an executor.

    All executors must support this minimal interface.
    """

    system_image: ArtifactResponse
    backend_name: str
    image_category: ClassVar[ExecutorImageCategory]

    def __init_subclass__(cls, /, backend_name: str, **kwargs: Any) -> None:
        """Register the backend in _backends."""
        super().__init_subclass__(**kwargs)
        cls.backend_name = backend_name
        _backends[backend_name] = cls

    @abstractmethod
    def __init__(self, debusine_api: Debusine, system_image_id: int) -> None:
        """
        Instantiate an ExecutorInterface.

        :param debusine_api: The object to use the debusine client API
        :param system_image_id: An artifact ID pointing to the system
          tarball or disk image to use (as appropriate for each technology)
        """

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """Determine whether this executor is available for operation."""

    @abstractmethod
    def download_image(self) -> str:
        """
        Make the image available locally.

        Fetch the image from artifact storage, if it isn't already available,
        and make it available locally.

        Return a path to the image or name, as appropriate for the backend.
        """

    @abstractmethod
    def image_name(self) -> str:
        """Return the path or name as returned from download_image()."""

    @abstractmethod
    def create(self) -> "InstanceInterface":
        """
        Create an InstanceInterface instance, using the image.

        Returns a new, stopped InstanceInterface.

        Each of these instances are pristine copies of the image, without any
        shared state between them.
        """

    @abstractmethod
    def autopkgtest_virt_server(self) -> str:
        """Return the name of the autopkgtest-virt-server for this backend."""

    @abstractmethod
    def autopkgtest_virt_args(self) -> list[str]:
        """
        Generate the arguments to drive an autopkgtest-virt-server.

        Tasks using tools that support the autopkgtest-virt-server interface
        can call this to generate the correct commandline for using this image.
        """

    @classmethod
    @abstractmethod
    def get_statistics(
        cls, instance: Optional["InstanceInterface"]
    ) -> ExecutorStatistics:
        """
        Return statistics for this executor.

        The worker may start collecting statistics as soon as the task
        starts, before an instance has been created.  Some statistics may
        not be available until an instance has been created, depending on
        the executor.
        """


class InstanceInterface(ABC):
    """
    Interface for execution within an instance of an execution environment.

    All execution environments support this minimal interface.

    Instances are ephemeral containers / VMs.
    """

    _uid_map: dict[str, int]
    _gid_map: dict[str, int]
    non_root_user: str = "_debusine"

    def __init__(self) -> None:
        """Initialize an InstanceInterface."""
        self._uid_map = {"root": 0}
        self._gid_map = {"root": 0}

    @abstractmethod
    def is_started(self) -> bool:
        """Determine if the instance is started."""

    def start(self) -> None:
        """
        Start the environment, making it ready to run commands.

        VM backends and containers that run init will boot the instance.
        """
        if self.is_started():
            raise InstanceRunning("Cannot start a started instance")
        self.do_start()

    @abstractmethod
    def do_start(self) -> None:
        """
        Start the environment, making it ready to run commands.

        VM backends and containers that run init will boot the instance.
        Called by start().
        """

    def stop(self) -> None:
        """Stop the environment, cleaning up afterwards."""
        if not self.is_started():
            raise InstanceNotRunning("Cannot stop a stopped instance")
        self.do_stop()

    @abstractmethod
    def do_stop(self) -> None:
        """Stop the environment, cleaning up afterwards."""

    def restart(self) -> None:
        """Restart the environment."""
        if not self.is_started():
            raise InstanceNotRunning("Cannot restart a stopped instance")
        self.do_restart()

    @abstractmethod
    def do_restart(self) -> None:
        """Restart the environment."""

    def file_push(
        self,
        source: Path,
        target: PurePath,
        user: str = "root",
        group: str = "root",
        mode: int = 0o644,
    ) -> None:
        """
        Copy a file into the environment.

        source is a regular file.
        target is the target file-name within an existing directory in the
        instance.

        Timestamps are not expected to be retained.
        """
        if not self.is_started():
            raise InstanceNotRunning("Cannot push files to a stopped instance")
        if not target.is_absolute():
            raise ValueError(f"target must be an absolute path (was: {target})")
        uid = self._get_uid(user)
        gid = self._get_gid(group)
        self.do_file_push(source, target, uid, gid, mode)

    @abstractmethod
    def do_file_push(
        self, source: Path, target: PurePath, uid: int, gid: int, mode: int
    ) -> None:
        """
        Copy a file into the environment.

        source is a regular file.
        target is the target file-name within an existing directory in the
        instance.

        Timestamps are not expected to be retained.
        """

    def file_pull(self, source: PurePath, target: Path) -> None:
        """
        Copy a file out of the environment.

        source is a regular file.
        target is the target file-name within an existing directory on the
        host.

        Timestamps are not expected to be retained.
        """
        if not self.is_started():
            raise InstanceNotRunning(
                "Cannot pull files from a stopped instance"
            )
        if not source.is_absolute():
            raise ValueError(f"source must be an absolute path (was: {source})")
        self.do_file_pull(source, target)

    @abstractmethod
    def do_file_pull(self, source: PurePath, target: Path) -> None:
        """
        Copy a file out of the environment.

        source is a regular file.
        target is the target file-name within an existing directory on the
        host.

        Only regular files are supported.
        Timestamps are not expected to be retained.
        """

    def directory_push(
        self,
        source: Path,
        target: PurePath,
        user: str = "root",
        group: str = "root",
    ) -> None:
        """
        Copy a directory (recursively) into the environment.

        source is a directory.
        target is an existing directory that source will be copied into.
        Files will belong to user and group.
        File modes will be retained.

        Only regular files and directories are supported.
        Timestamps are not expected to be retained.
        """
        if not self.is_started():
            raise InstanceNotRunning("Cannot push files to a stopped instance")
        if not target.is_absolute():
            raise ValueError(f"target must be an absolute path (was: {target})")
        uid = self._get_uid(user)
        gid = self._get_gid(group)
        self.do_directory_push(source, target, uid, gid)

    def do_directory_push(
        self, source: Path, target: PurePath, uid: int, gid: int
    ) -> None:
        """
        Copy a directory (recursively) into the environment.

        source is a directory.
        target is an existing directory that source will be copied into.
        Files will belong to user and group.
        File modes will be retained.

        Only regular files and directories are supported.
        Timestamps are not expected to be retained.
        """
        mode = S_IMODE(source.stat().st_mode)
        self.do_mkdir(target / source.name, uid, gid, mode, parents=False)

        for absolute_path_str, dir_names, file_names in os.walk(source):
            dir_path = Path(absolute_path_str)
            target_relative_dir_path = dir_path.relative_to(source.parent)

            for dir_name in dir_names:
                source_dir_path = dir_path / dir_name
                target_dir = target / target_relative_dir_path / dir_name
                mode = S_IMODE(source_dir_path.stat().st_mode)
                self.do_mkdir(target_dir, uid, gid, mode, parents=False)

            for file_name in file_names:
                source_file_path = dir_path / file_name
                target_file_path = target / target_relative_dir_path / file_name
                mode = S_IMODE(source_file_path.stat().st_mode)

                self.do_file_push(
                    source_file_path, target_file_path, uid, gid, mode
                )

    def mkdir(
        self,
        target: PurePath,
        user: str = "root",
        group: str = "root",
        mode: int = 0o755,
        parents: bool = False,
    ) -> None:
        """
        Create directory inside the instance.

        Owned by user and group, with the specified mode.
        Create parent directories if necessary if parents is True.

        If target exists: raise FileExistsError().
        """
        if not self.is_started():
            raise InstanceNotRunning(
                "Cannot make directories in a stopped instance"
            )
        if not target.is_absolute():
            raise ValueError(f"target must be an absolute path (was: {target})")
        uid = self._get_uid(user)
        gid = self._get_gid(group)
        self.do_mkdir(target, uid, gid, mode, parents)

    def do_mkdir(
        self, target: PurePath, uid: int, gid: int, mode: int, parents: bool
    ) -> None:
        """
        Create directory inside the instance.

        Owned by user and group, with the specified mode.
        Create parent directories if necessary if parents is True.

        If target exists: raise FileExistsError().
        """
        cmd = ["mkdir", f"--mode={mode:o}"]
        if parents:
            cmd.append("--parents")
        cmd.append(str(target))
        try:
            self._run_check_output(cmd)
        except CalledProcessError as e:
            if e.returncode == 1 and e.stderr.endswith("File exists"):
                raise FileExistsError(
                    f'Cannot create directory: "{target}" already exists'
                )
            raise
        if uid != 0 and gid != 0:
            self._run_check_output(["chown", f"{uid}:{gid}", str(target)])

    @overload
    def run(
        self,
        args: list[str],
        *,
        text: Literal[True],
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[str]: ...

    @overload
    def run(
        self,
        args: list[str],
        *,
        text: Literal[False] | None = None,
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[bytes]: ...

    def run(
        self,
        args: list[str],
        **kwargs: Any,
    ) -> CompletedProcess[AnyStr]:
        """
        Run a command in the environment.

        Arguments behave as if passed to `subprocess.run`.
        """
        if not self.is_started():
            raise InstanceNotRunning(
                "Cannot run commands on a stopped instance"
            )
        kwargs.setdefault("stderr", PIPE)
        kwargs.setdefault("stdout", PIPE)
        return self.do_run(args, **kwargs)

    @overload
    @abstractmethod
    def do_run(
        self,
        args: list[str],
        text: Literal[True],
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[str]: ...

    @overload
    @abstractmethod
    def do_run(
        self,
        args: list[str],
        text: Literal[False] | None = None,
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[bytes]: ...

    @abstractmethod
    def do_run(
        self,
        args: list[str],
        text: Literal[False] | Literal[True] | None = None,
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[AnyStr]:
        """
        Run a command in the environment.

        Arguments behave as if passed to `subprocess.run`.
        """

    def _run_check_output(self, args: list[str]) -> str:
        """Run args as root in the instance, return output."""
        return self.run(
            args,
            text=True,
            run_as_root=True,
            stdout=PIPE,
            stderr=None,
            check=True,
        ).stdout

    def _get_uid(self, username: str) -> int:
        """Look up the User ID for username inside the instance."""
        if username not in self._uid_map:
            try:
                entry = self._run_check_output(["getent", "passwd", username])
            except CalledProcessError as e:
                if e.returncode == 2:
                    raise KeyError(f"User not found in instance: {username}")
                raise
            uid = int(entry.split(":")[2])
            self._uid_map[username] = uid
        return self._uid_map[username]

    def _get_gid(self, group: str) -> int:
        """Look up the Group ID for group inside the instance."""
        if group not in self._gid_map:
            try:
                entry = self._run_check_output(["getent", "group", group])
            except CalledProcessError as e:
                if e.returncode == 2:
                    raise KeyError(f"Group not found in instance: {group}")
                raise
            gid = int(entry.split(":")[2])
            self._gid_map[group] = gid
        return self._gid_map[group]

    def create_user(self, username: str | None = None) -> int:
        """Provision a non-root user (if necessary) and return its user id."""
        if username is None:
            username = self.non_root_user
        try:
            return self._get_uid(username)
        except KeyError:
            self._run_check_output(["useradd", username])
            return self._get_uid(username)


def executor_class(
    backend: str,
) -> type[ExecutorInterface]:
    """
    Get the Executor class for a backend name.

    :param backend: The name of the executor backend.
    :return: An ExecutorInterface class.
    """
    if backend not in _backends:
        raise NotImplementedError(
            f"Support for backend {backend} is not yet implemented."
        )

    return _backends[backend]


def analyze_worker_all_executors() -> dict[str, Any]:
    """
    Return dictionary with metadata for each executor backend.

    This method is called on the worker to collect information about the
    worker. The information is stored as a set of key-value pairs in a
    dictionary.

    That information is then reused on the scheduler to be fed to
    :py:meth:`debusine.tasks.BaseTask.can_run_on` and determine if a task is
    suitable to be executed on the worker.
    """
    return {
        f"executor:{backend}:available": executor_class.available()
        for backend, executor_class in _backends.items()
    }
