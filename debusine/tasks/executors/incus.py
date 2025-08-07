# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""The incus executor backend."""
import importlib.resources
import logging
import random
import string
import subprocess
import tarfile
import textwrap
import time
from abc import abstractmethod
from copy import copy
from io import BytesIO
from pathlib import Path, PurePath
from subprocess import (
    CalledProcessError,
    CompletedProcess,
    DEVNULL,
    check_call,
    check_output,
)
from tarfile import TarFile, TarInfo
from tempfile import TemporaryDirectory
from typing import Any, AnyStr, ClassVar, Literal, overload

import yaml
from prometheus_client.parser import text_string_to_metric_families

from debusine import utils
from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse
from debusine.tasks.executors.base import (
    ExecutorImageCategory,
    ExecutorInterface,
    ExecutorStatistics,
    ImageImportError,
    InstanceInterface,
    InstanceNotRunning,
)
from debusine.tasks.executors.images import ImageCache

# https://linuxcontainers.org/incus/docs/main/architectures/
DEB_TO_INCUS_ARCH = {
    "amd64": "x86_64",
    "arm64": "aarch64",
    "armel": "armv6l",
    "armhf": "armv7l",
    "i386": "i686",
    "loong64": "loongarch64",
    "mips64el": "mips64",
    "mipsel": "mips",
    "powerpc": "ppc",
    "ppc64": "ppc64",
    "ppc64el": "ppc64le",
    "riscv64": "riscv64",
    "s390x": "s390x",
}

TEMPLATES = {
    "hosts.tpl": textwrap.dedent(
        """\
        127.0.1.1   {{ instance.name }}
        127.0.0.1   localhost
        ::1         localhost ip6-localhost ip6-loopback
        ff02::1     ip6-allnodes
        ff02::2     ip6-allrouters
        """
    ),
    "hostname.tpl": "{{ instance.name }}",
}

log = logging.getLogger(__name__)


def run_incus_cmd(*args: str, suppress_stderr: bool = False) -> str:
    """Execute an incus CLI command, return the output."""
    cmd = ["incus"]
    cmd.extend(args)
    log.debug("Executing %r", cmd)
    return check_output(
        cmd,
        text=True,
        encoding="utf-8",
        stderr=DEVNULL if suppress_stderr else None,
    )


class IncusImageCache(ImageCache):
    """
    Extensions to ImageCache to import images into Incus.

    Adds hooks for adding files to the import tarball, expecting to be
    subclassed for LXC and VM details.
    """

    def image_in_cache(self, artifact: ArtifactResponse) -> bool:
        """Return whether or not artifact is available in the local cache."""
        try:
            run_incus_cmd(
                "image", "info", f"artifact/{artifact.id}", suppress_stderr=True
            )
        except CalledProcessError:
            return False
        return True

    def process_downloaded_image(self, artifact: ArtifactResponse) -> None:
        """
        Handle any necessary post-download processing of the image.

        Locate the downloaded image using cache_artifact_image_path() and store
        it as for a backend, if the result needs to be stored in the cache.

        This only needs to be done once, after the image is first downloaded.
        """
        src_path = self.cache_artifact_image_path(
            artifact.id, artifact.data["filename"]
        )
        with TemporaryDirectory(prefix="debusine-incus-image-build-") as wd:
            dest_path = self.build_incus_image(src_path, Path(wd), artifact)
            log.info(
                "Importing %s %i into Incus", artifact.category, artifact.id
            )
            run_incus_cmd(
                "image",
                "import",
                str(dest_path),
                "--alias",
                f"artifact/{artifact.id}",
            )

    def _image_metadata(self, artifact: ArtifactResponse) -> dict[str, Any]:
        """Return the metadata for the given artifact."""
        architecture = artifact.data["architecture"]
        vendor = artifact.data["vendor"]
        codename = artifact.data["codename"]
        return {
            "architecture": DEB_TO_INCUS_ARCH.get(architecture, architecture),
            "creation_date": int(artifact.created_at.timestamp()),
            "profiles": ["debusine"],
            "properties": {
                "description": (
                    f"Debusine Artifact #{artifact.id}: {artifact.category} "
                    f"for {vendor} {codename} {architecture}"
                ),
                "os": vendor,
                "release": codename,
                "debusine_artifact_id": artifact.id,
                "debian_architecture": architecture,
            },
            "templates": {
                "/etc/hostname": {
                    "when": ["create", "copy"],
                    "create_only": False,
                    "template": "hostname.tpl",
                    "properties": {},
                },
                "/etc/hosts": {
                    "when": ["create", "copy"],
                    "create_only": False,
                    "template": "hosts.tpl",
                    "properties": {},
                },
            },
        }

    def build_incus_image(
        self, src_path: Path, workdir: Path, artifact: ArtifactResponse
    ) -> Path:
        """
        Convert an incus environment image into an incus image.

        :param src_path: The incus image to convert.
        :param workdir: A temporary directory to work in and store output in.
        :param artifact: The incus image artifact.

        All the real work is done in prepare_incus_image(); this just opens the
        files.
        """
        log.info("Converting %s %i for Incus", artifact.category, artifact.id)
        dest_path = workdir / "incus.tar.xz"
        with tarfile.open(dest_path, "w:xz") as dest:
            self.prepare_incus_image(src_path, dest, artifact)
        return dest_path

    @abstractmethod
    def prepare_incus_image(
        self,
        source: Path,
        dest: TarFile,
        artifact: ArtifactResponse,
    ) -> None:
        """
        Import the image in source into incus image tarball dest.

        Expected to be overridden to include the image itself.
        """

    def _add_incus_metadata(
        self, dest: TarFile, artifact: ArtifactResponse
    ) -> None:
        """
        Write incus metadata into dest.

        This method is available for use in prepare_incus_image().
        """
        self.add_tar_file(
            dest, "metadata.yaml", yaml.dump(self._image_metadata(artifact))
        )

        self.add_tar_directory(dest, "templates")
        for name, content in TEMPLATES.items():
            self.add_tar_file(dest, f"templates/{name}", content)

    def add_tar_directory(
        self, tarball: TarFile, name: str, mode: int = 0o755
    ) -> None:
        """Add a directory named name to tarball."""
        tarinfo = TarInfo(name=name)
        tarinfo.type = tarfile.DIRTYPE
        tarinfo.mode = mode
        tarball.addfile(tarinfo)

    def add_tar_file(
        self, tarball: TarFile, name: str, data: str, mode: int = 0o644
    ) -> None:
        """Add a file named name to tarball, containing data."""
        encoded = data.encode("utf-8")
        tarinfo = TarInfo(name=name)
        tarinfo.mode = mode
        tarinfo.size = len(encoded)
        tarball.addfile(tarinfo, BytesIO(encoded))

    def add_tar_symlink(
        self,
        tarball: TarFile,
        name: str,
        dest: str,
    ) -> None:
        """Add a symlink named name to tarball."""
        tarinfo = TarInfo(name=name)
        tarinfo.type = tarfile.SYMTYPE
        tarinfo.linkname = dest
        tarball.addfile(tarinfo)


class IncusLXCImageCache(IncusImageCache):
    """Process LXC container images on import."""

    backend = "incus-lxc"

    def prepare_incus_image(
        self, source: Path, dest: TarFile, artifact: ArtifactResponse
    ) -> None:
        """Import the image in source into incus image tarball dest."""
        self._add_incus_metadata(dest, artifact)

        rootfs = Path("rootfs")
        parent_directories = {
            rootfs / "etc": False,
            rootfs / "etc/systemd": False,
            rootfs / "etc/systemd/system": False,
            rootfs / "etc/systemd/system-generators": False,
            rootfs / "etc/systemd/system/multi-user.target.wants": False,
        }
        has_init = False
        init_paths = {
            rootfs / "sbin/init",
            rootfs / "usr/sbin/init",
        }

        # Copy tarball contents into rootfs/
        # We assume it starts with a root directory
        with tarfile.open(source) as source_tar:
            for entry in source_tar:
                dest_path = rootfs / entry.name.lstrip("/")

                if dest_path in init_paths:
                    has_init = True

                if dest_path in parent_directories:
                    parent_directories[dest_path] = True

                # Can be replaced with entry.replace(name=...) in Python 3.12
                dest_entry = copy(entry)
                dest_entry.name = str(dest_path)
                if entry.isreg():
                    dest.addfile(dest_entry, source_tar.extractfile(entry))
                elif entry.islnk():
                    # Hard-links in tarfiles are stored as an absolute path
                    # (relative to the tar) reference to the shared file
                    dest_entry.linkname = str(
                        rootfs / entry.linkname.lstrip("/")
                    )
                    dest.addfile(dest_entry)
                else:
                    dest.addfile(dest_entry)

        if not has_init:
            raise ImageImportError(
                "Image doesn't contain an init, not bootable in LXC"
            )

        # Create parent directories for the additions below
        for path, present in parent_directories.items():
            if not present:
                self.add_tar_directory(dest, str(path))

        # Enable systemd-networkd, to bring up host0
        self.add_tar_symlink(
            dest,
            (
                "rootfs/etc/systemd/system/multi-user.target.wants/"
                "systemd-networkd.service"
            ),
            "/lib/systemd/system/systemd-networkd.service",
        )
        self.add_tar_symlink(
            dest,
            (
                "rootfs/etc/systemd/system/network-online.target.wants/"
                "systemd-networkd-wait-online.service"
            ),
            "/lib/systemd/system/systemd-networkd-wait-online.service",
        )
        # Install lxc.generator. Contains hacks for systemd < 257
        self.add_tar_file(
            dest,
            "rootfs/etc/systemd/system-generators/lxc",
            importlib.resources.files(__package__)
            .joinpath("data")
            .joinpath("lxc.generator")
            .read_text(encoding="utf-8"),
            mode=0o755,
        )


class IncusVMImageCache(IncusImageCache):
    """Process VM container images on import."""

    backend = "incus-vm"

    def convert_raw_image(self, source: Path) -> Path:
        """Convert a raw image to qcow2."""
        workdir = source.parent
        with tarfile.open(source) as tar:
            contents = tar.getnames()
            if len(contents) != 1:
                raise AssertionError(
                    "Raw image tarballs are expected to contain 1 file"
                )
            name = contents[0]
            if "/" in name:
                raise AssertionError(
                    "Raw image tarballs must not include any directories"
                )
            tar.extract(name, path=workdir, set_attrs=False)
            raw = workdir / name

        dest = workdir / "image.qcow2"
        check_call(
            [
                "qemu-img",
                "convert",
                "-f",
                "raw",
                "-O",
                "qcow2",
                str(raw),
                str(dest),
            ]
        )
        raw.unlink()
        return dest

    def prepare_incus_image(
        self, source: Path, dest: TarFile, artifact: ArtifactResponse
    ) -> None:
        """Import the image in source into incus image tarball dest."""
        self._add_incus_metadata(dest, artifact)

        if artifact.data["image_format"] == "raw":
            source = self.convert_raw_image(source)

        entry = tarfile.TarInfo(name="rootfs.img")
        entry.size = source.stat().st_size
        with source.open("rb") as image:
            dest.addfile(entry, image)

        if artifact.data["image_format"] == "raw":
            source.unlink()


class IncusExecutorMixin:
    """Support the common behaviour between Incus containers and VMs."""

    system_image: ArtifactResponse
    image_cache_class: type[ImageCache]
    image_category: ClassVar[ExecutorImageCategory]
    incus_driver: str
    _image_id: str | None = None

    def __init__(self, debusine_api: Debusine, system_image_id: int):
        """
        Instantiate an IncusExecutor.

        :param debusine_api: The object to use the debusine client API.
        :param system_image_id: An artifact ID pointing to the system tarball.
        """
        self._extracted: Path | None = None
        self._image_cache = self.image_cache_class(
            debusine_api, self.image_category
        )
        self.system_image = self._image_cache.image_artifact(system_image_id)

    @classmethod
    def available(cls) -> bool:
        """Determine whether this executor is available for operation."""
        if not utils.is_command_available("incus"):
            return False
        if not utils.is_command_available("autopkgtest-virt-incus"):
            return False

        try:
            output = run_incus_cmd("info")
        except CalledProcessError:
            return False

        info = yaml.safe_load(output)
        drivers = [
            driver.strip()
            for driver in info["environment"]["driver"].split("|")
        ]
        return cls.incus_driver in drivers

    def download_image(self) -> str:
        """
        Make the image available locally.

        Fetch the image from artifact storage, if it isn't already available,
        and make it available locally.

        Return a path to the image or name, as appropriate for the backend.
        """
        self._image_cache.download_image(self.system_image)
        return self.image_name()

    def image_name(self) -> str:
        """Return the alias of the imported image."""
        return f"artifact/{self.system_image.id}"

    def create(self) -> "IncusInstance":
        """
        Create an IncusInstance using the imported image.

        Returns a new, stopped UnshareInstance.
        """
        return IncusInstance(self.image_name())

    def autopkgtest_virt_server(self) -> str:
        """Return the name of the autopkgtest-virt-server for this backend."""
        return "incus"

    @classmethod
    def clean_up_image(cls, artifact_id: int) -> None:
        """Remove incus images from the cache."""
        run_incus_cmd("image", "rm", f"artifact/{artifact_id}")

    @classmethod
    def get_statistics(
        cls, instance: InstanceInterface | None
    ) -> ExecutorStatistics:
        """Return statistics for this executor."""
        statistics = ExecutorStatistics()

        if instance is not None and instance.is_started():
            assert isinstance(instance, IncusInstance)
            assert instance._instance is not None

            free_disk_space: int | None = None
            free_memory: int | None = None
            for family in text_string_to_metric_families(
                subprocess.run(
                    ["incus", "query", "/1.0/metrics"],
                    capture_output=True,
                    check=True,
                    text=True,
                ).stdout
            ):

                def sum_relevant_samples() -> float:
                    return sum(
                        sample.value
                        for sample in family.samples
                        if sample.labels.get("name") == instance._instance
                    )

                match family.name:
                    case "incus_cpu_effective_total":
                        statistics.cpu_count = int(sum_relevant_samples())
                    case "incus_filesystem_avail_bytes":
                        free_disk_space = int(sum_relevant_samples())
                    case "incus_filesystem_size_bytes":
                        statistics.available_disk_space = int(
                            sum_relevant_samples()
                        )
                    case "incus_memory_MemFree_bytes":
                        free_memory = int(sum_relevant_samples())
                    case "incus_memory_MemTotal_bytes":
                        statistics.available_memory = int(
                            sum_relevant_samples()
                        )

            if (
                statistics.available_disk_space is not None
                and free_disk_space is not None
            ):
                statistics.disk_space = (
                    statistics.available_disk_space - free_disk_space
                )
            if (
                statistics.available_memory is not None
                and free_memory is not None
            ):
                # TODO: This is an approximation; in particular, it doesn't take
                # swap into account.  Incus's metrics don't seem to include
                # available swap space, so we'd probably need to do something
                # more complicated to sum all the relevant categories of used
                # memory.
                statistics.memory = statistics.available_memory - free_memory

        return statistics


class IncusLXCExecutor(
    IncusExecutorMixin, ExecutorInterface, backend_name="incus-lxc"
):
    """Support the Incus LXC Container executor."""

    image_cache_class = IncusLXCImageCache
    image_category = ExecutorImageCategory.TARBALL
    incus_driver = "lxc"

    def autopkgtest_virt_args(self) -> list[str]:
        """Generate the arguments to drive an autopkgtest-virt-server."""
        image_id = self.image_name()
        return [image_id, "--", "--profile", "debusine"]


class IncusVMExecutor(
    IncusExecutorMixin, ExecutorInterface, backend_name="incus-vm"
):
    """Support the Incus VM executor."""

    image_cache_class = IncusVMImageCache
    image_category = ExecutorImageCategory.IMAGE
    incus_driver = "qemu"

    def autopkgtest_virt_args(self) -> list[str]:
        """Generate the arguments to drive an autopkgtest-virt-server."""
        image_id = self.image_name()
        return ["--vm", image_id, "--", "--profile", "debusine"]


class IncusInstance(InstanceInterface):
    """Support instances of the Incus executor."""

    _image: str
    _instance: str | None = None

    def __init__(self, image: str):
        """Initialize the object."""
        super().__init__()
        self._image = image

    def is_started(self) -> bool:
        """Determine if the instance is started."""
        return self._instance is not None

    @classmethod
    def _generate_instance_name(cls) -> str:
        """Generate an unused incus instance name."""
        while True:
            rnd = [random.choice(string.ascii_lowercase) for i in range(6)]
            candidate = "debusine-" + "".join(rnd)
            try:
                run_incus_cmd("info", candidate)
            except CalledProcessError:
                return candidate

    def do_start(self) -> None:
        """Start an ephemeral instance."""
        name = self._generate_instance_name()
        run_incus_cmd(
            "launch", "--ephemeral", "--profile", "debusine", self._image, name
        )
        self._instance = name
        self._spin_until_started()

    def _spin_until_started(
        self, sleep_time: float = 0.2, timeout: float = 60.0
    ) -> None:
        """Wait for an instance to become ready."""
        assert self._instance is not None
        start_time = time.monotonic()
        end_time = start_time + timeout
        while time.monotonic() < end_time:
            p = subprocess.run(
                [
                    "incus",
                    "exec",
                    self._instance,
                    "systemctl",
                    "is-system-running",
                ],
                capture_output=True,
                text=True,
            )
            stdout = p.stdout.strip()
            stderr = p.stderr.strip()
            if stdout in ("running", "degraded", "maintenance"):
                return
            elif stdout in ("initializing", "starting"):
                pass
            elif (
                p.returncode == 1
                and stderr == "Error: VM agent isn't currently running"
            ):
                pass
            elif p.returncode == 1 and stderr.startswith(
                "Failed to connect to system scope bus via local transport:"
            ):
                pass
            else:
                log.warning(
                    "Unexpected response from exec systemctl "
                    "is-system-running: returncode: %i, stdout: %s, stderr: %s",
                    p.returncode,
                    p.stdout,
                    p.stderr,
                )
            time.sleep(sleep_time)
        raise InstanceNotRunning(
            f"Incus Instance {self._instance} hasn't booted "
            "or is missing incus-agent"
        )

    def do_stop(self) -> None:
        """Stop the instance."""
        assert self._instance is not None
        run_incus_cmd("stop", self._instance)
        self._instance = None

    def do_restart(self) -> None:
        """Restart the instance."""
        assert self._instance is not None
        run_incus_cmd("restart", self._instance)
        self._spin_until_started()

    def do_file_push(
        self, source: Path, target: PurePath, uid: int, gid: int, mode: int
    ) -> None:
        """
        Copy a file into the environment.

        source is a regular file.
        target is the target file-name within an existing directory in the
        instance.

        Files are owned by the same owner as their parent directory.
        Timestamps are not expected to be retained.
        """
        assert self._instance is not None
        assert target.is_absolute()
        run_incus_cmd(
            "file",
            "push",
            "--uid",
            str(uid),
            "--gid",
            str(gid),
            "--mode",
            f"{mode:o}",
            str(source),
            self._instance + str(target),
        )

    def do_file_pull(self, source: PurePath, target: Path) -> None:
        """
        Copy a file out of the environment.

        source is a regular file.
        target is the target file-name within an existing directory on the
        host.

        Timestamps are not expected to be retained.
        """
        assert self._instance is not None
        assert source.is_absolute()
        run_incus_cmd("file", "pull", self._instance + str(source), str(target))

    def do_directory_push(
        self, source: Path, target: PurePath, uid: int, gid: int
    ) -> None:
        """
        Copy a directory (recursively) into the environment.

        source is a directory.
        target is an existing directory that source will be copied into.

        Only regular files are supported.
        Timestamps are not expected to be retained.
        New directories and files are owned by the same owner as their parent
        directory.
        """
        assert self._instance is not None
        assert target.is_absolute()
        run_incus_cmd(
            "file",
            "push",
            "--recursive",
            str(source),
            self._instance + str(target),
        )
        run_incus_cmd(
            "exec",
            self._instance,
            "--",
            "chown",
            "-R",
            f"{uid}:{gid}",
            str(target / source.name),
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
        assert self._instance is not None
        cmd = ["incus", "exec", self._instance]

        if not run_as_root:
            uid = self.create_user()
            cmd += ["--user", str(uid)]

        cmd.append("--")
        cmd += args

        return subprocess.run(cmd, text=text, **kwargs)
