# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The unshare executor backend."""

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePath
from subprocess import CompletedProcess
from typing import Any, AnyStr, Literal, overload

import psutil

from debusine import utils
from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse
from debusine.tasks.executors.base import (
    ExecutorImageCategory,
    ExecutorInterface,
    ExecutorStatistics,
    ImageNotDownloadedError,
    InstanceInterface,
    InstanceNotRunning,
)
from debusine.tasks.executors.images import ImageCache


class UnshareExecutor(ExecutorInterface, backend_name="unshare"):
    """Support the unshare(1) executor."""

    system_image: ArtifactResponse
    _image_cache: ImageCache
    _local_path: Path | None = None

    image_category = ExecutorImageCategory.TARBALL

    def __init__(self, debusine_api: Debusine, system_image_id: int):
        """
        Instantiate an UnshareExecutor.

        :param debusine_api: The object to use the debusine client API.
        :param system_image_id: An artifact id pointing to the system tarball.
        """
        self._image_cache = ImageCache(debusine_api, self.image_category)
        self._extracted: Path | None = None
        self.system_image = self._image_cache.image_artifact(system_image_id)

    @classmethod
    def available(cls) -> bool:
        """Determine whether this executor is available for operation."""
        return utils.is_command_available(UnshareInstance.unshare_command)

    def download_image(self) -> str:
        """
        Make the image available locally.

        Fetch the image from artifact storage, if it isn't already available,
        and make it available locally.

        Return a path to the image or name, as appropriate for the backend.
        """
        self._local_path = self._image_cache.download_image(self.system_image)
        return self.image_name()

    def image_name(self) -> str:
        """Return the path to the downloaded image."""
        if self._local_path is None:
            raise ImageNotDownloadedError()
        return str(self._local_path)

    def create(self) -> "UnshareInstance":
        """
        Create an UnshareInstance using the downloaded image.

        Returns a new, stopped UnshareInstance.
        """
        if self._local_path is None:
            raise ImageNotDownloadedError(
                "UnshareExecutor.download_image() should have "
                "been called and set self._local_path"
            )

        return UnshareInstance(self._local_path)

    def autopkgtest_virt_server(self) -> str:
        """Return the name of the autopkgtest-virt-server for this backend."""
        return "unshare"

    def autopkgtest_virt_args(self) -> list[str]:
        """Generate the arguments to drive an autopkgtest-virt-server."""
        if self._local_path is None:
            raise ImageNotDownloadedError()
        return [
            "--arch",
            self.system_image.data["architecture"],
            "--release",
            self.system_image.data["codename"],
            "--tarball",
            str(self._local_path),
        ]

    @classmethod
    def get_statistics(
        cls, instance: InstanceInterface | None
    ) -> ExecutorStatistics:
        """Return statistics for this instance."""
        statistics = ExecutorStatistics()

        if instance is not None and instance.is_started():
            assert isinstance(instance, UnshareInstance)
            statvfs = os.statvfs(instance._root)
            statistics.disk_space = (
                statvfs.f_blocks - statvfs.f_bfree
            ) * statvfs.f_frsize
            statistics.available_disk_space = (
                statvfs.f_blocks * statvfs.f_frsize
            )

        memory = psutil.virtual_memory()
        statistics.memory = memory.used
        statistics.available_memory = memory.total

        statistics.cpu_count = psutil.cpu_count() or 1

        return statistics


class UnshareInstance(InstanceInterface):
    """Support instances of the unshare(1) executor."""

    unshare_command = "unshare"
    _unshare_default_args = ["--map-users=auto", "--map-groups=auto"]

    def __init__(self, image: Path):
        """Initialize the object."""
        super().__init__()
        self._image = image
        self._extracted: Path | None = None

    @property
    def _root(self) -> Path:
        if self._extracted is None:
            raise InstanceNotRunning(
                "UnshareInstance.start() must be called before using _root"
            )
        return self._extracted / "root"

    def is_started(self) -> bool:
        """Determine if the instance is started."""
        return self._extracted is not None

    def do_start(self) -> None:
        """Start the instance, making it ready to run commands."""
        # extracted directory is deleted by UnshareInstance.stop()
        self._extracted = Path(
            tempfile.mkdtemp(prefix="debusine-executor-unshare-")
        )

        # The directory, by default, has permissions 700. To extract the
        # file using subuids it needs wider permissions
        # If the directory that mkdtemp() used is restricted to the invoking
        # user (private tmp): then the permission of self._extracted.parent
        # may be insufficient to allow extraction in that namespace.
        self._extracted.chmod(0o755)

        self._root.mkdir()

        # Make / dir of the environment owned by "root:root" (1:1) in the
        # environment namespace
        # E.g. debusine-worker owns self._root. After the chown it's owned
        # by the first subuid:subgid of the user (which will be root:root
        # in the chroot)
        self._run_unshare_command(
            ["--map-root-user", "chown", "1:1", str(self._root)]
        )

        with self._image.open("rb") as image:
            self._run_unshare_command(
                [
                    "--setuid=0",
                    "--setgid=0",
                    "tar",
                    "-C",
                    str(self._root),
                    "--xz",
                    "-x",
                ],
                stdin=image,
            )
        # Copy the system resolv.conf into the instance, overriding any
        # systemd-resolved symlink
        self._run_unshare_command(
            [
                "--setuid=0",
                "--setgid=0",
                "rm",
                "--force",
                str(self._root / "etc" / "resolv.conf"),
            ]
        )
        self._run_unshare_command(
            [
                "--setuid=0",
                "--setgid=0",
                "cp",
                "--dereference",
                "/etc/resolv.conf",
                str(self._root / "etc" / "resolv.conf"),
            ]
        )

    def do_stop(self) -> None:
        """Stop the instance: delete the directory."""
        assert self._extracted is not None
        self._run_unshare_command(
            ["--map-root-user", "rm", "-rf", str(self._root)]
        )
        shutil.rmtree(self._extracted)

        self._extracted = None

    def do_restart(self) -> None:
        """Restart the environment (noop for unshare)."""
        pass

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
        inner_target = shlex.quote(str(self._root / target.relative_to("/")))

        with source.open("rb") as stdin:
            self._run_unshare_command(
                [
                    f"--setuid={uid}",
                    f"--setgid={gid}",
                    "sh",
                    "-c",
                    f"cat > {inner_target} && chmod {mode:o} {inner_target}",
                ],
                stdin=stdin,
                check=True,
            )

    def do_file_pull(self, source: PurePath, target: Path) -> None:
        """
        Copy a file out of the environment.

        source is a regular file.
        target is the target file-name within an existing directory on the
        host.

        Timestamps are not expected to be retained.
        """
        self._run_unshare_command(
            [
                "--setuid=0",
                "--setgid=0",
                "--map-root-user",
                "cp",
                "--no-preserve=ownership,mode",
                str(self._root / source.relative_to("/")),
                str(target),
            ],
            check=True,
        )

    @overload
    def do_run(
        self,
        args: list[str],
        text: Literal[True],
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[str]: ...

    @overload
    def do_run(
        self,
        args: list[str],
        text: Literal[False] | None = None,
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[bytes]: ...

    def do_run(
        self,
        args: list[str],
        text: Literal[False] | Literal[True] | None = None,
        run_as_root: bool = False,
        **kwargs: Any,
    ) -> CompletedProcess[AnyStr]:
        """
        Run a command (as root) in the instance.

        Arguments behave as if passed to `subprocess.run`.
        """
        if not run_as_root:
            username = self.non_root_user
            self.create_user()
            args = ["/sbin/runuser", "-u", username, "--"] + args

        root = shlex.quote(str(self._root))
        args_quoted = shlex.join(args)
        shell_cmd = (
            f"mount --rbind /dev {root}/dev && "
            f"exec /usr/sbin/chroot {root} {args_quoted}"
        )

        kwargs_with_text: dict[str, Any] = {**kwargs, "text": text}

        return self._run_unshare_command(
            [
                "--setuid=0",
                "--setgid=0",
                "--kill-child",
                "--pid",
                "--mount",
                f"--mount-proc={root}/proc",
                "sh",
                "-c",
                shell_cmd,
            ],
            **kwargs_with_text,
        )

    @overload
    @classmethod
    def _run_unshare_command(
        cls,
        args: list[str],
        *,
        text: Literal[True],
        **kwargs: Any,
    ) -> CompletedProcess[str]: ...

    @overload
    @classmethod
    def _run_unshare_command(
        cls,
        args: list[str],
        *,
        text: Literal[False] | None = None,
        **kwargs: Any,
    ) -> CompletedProcess[bytes]: ...

    @classmethod
    def _run_unshare_command(
        cls, args: list[str], **kwargs: Any
    ) -> CompletedProcess[AnyStr]:
        """
        Run args in unshare.

        kwargs are passed to subprocess.run. By default, "stdout" and "stderr"
        are subprocess.PIPE.

        Uses cls.unshare_command and cls._unshare_default_args for the command
        and default arguments.
        """
        cmd = [cls.unshare_command] + cls._unshare_default_args + args

        extra_kwargs: dict[str, Any] = {}

        if "stderr" not in kwargs:
            extra_kwargs["stderr"] = subprocess.PIPE

        if "stdout" not in kwargs:
            extra_kwargs["stdout"] = subprocess.PIPE

        return subprocess.run(cmd, **kwargs, **extra_kwargs)
