# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Incus executor."""

import tarfile
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from subprocess import CalledProcessError, CompletedProcess, DEVNULL, PIPE
from tempfile import NamedTemporaryFile, mkdtemp
from textwrap import dedent
from typing import Any, Literal, cast
from unittest.mock import ANY, MagicMock, call, create_autospec, patch

import yaml

from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse
from debusine.tasks import Noop
from debusine.tasks.executors.base import (
    ExecutorImageCategory,
    ExecutorInterface,
    ExecutorStatistics,
    ImageImportError,
    InstanceNotRunning,
    executor_class,
)
from debusine.tasks.executors.incus import (
    IncusExecutorMixin,
    IncusImageCache,
    IncusInstance,
    IncusLXCExecutor,
    IncusLXCImageCache,
    IncusVMExecutor,
    IncusVMImageCache,
    log,
    run_incus_cmd,
)
from debusine.tasks.tests.helper_mixin import ExternalTaskHelperMixin
from debusine.test import TestCase


class RunIncusCmdTest(TestCase):
    """Unit tests for run_incus_cmd."""

    @patch("debusine.tasks.executors.incus.check_output")
    def test_run_incus_cmd(self, check_output: MagicMock) -> None:
        """Test that run_incus_cmd is a thin wrapper around check_output."""
        check_output.return_value = "output"
        output = run_incus_cmd("testing", "123")
        self.assertEqual(output, "output")
        check_output.assert_called_with(
            ["incus", "testing", "123"],
            text=True,
            encoding="utf-8",
            stderr=None,
        )

    @patch("debusine.tasks.executors.incus.check_output")
    def test_run_incus_cmd_suppress_stderr(
        self, check_output: MagicMock
    ) -> None:
        """Test that run_incus_cmd can suppress stderr."""
        check_output.return_value = "output"
        output = run_incus_cmd("testing", "123", suppress_stderr=True)
        self.assertEqual(output, "output")
        check_output.assert_called_with(
            ["incus", "testing", "123"],
            text=True,
            encoding="utf-8",
            stderr=DEVNULL,
        )


class IncusImageCacheCommonTests(ExternalTaskHelperMixin[Noop], TestCase):
    """Unit tests that apply to all the Incus ImageCache implementations."""

    image_cache_cls: type[IncusImageCache]
    artifact: ArtifactResponse
    artifact_base_name: str
    artifact_category: ExecutorImageCategory

    def setUp(self) -> None:
        """Mock the image handling for tests."""
        super().setUp()
        self.debusine_api = create_autospec(Debusine)
        self.image_cache = self.image_cache_cls(
            self.debusine_api, self.artifact_category
        )
        self.image_cache.image_cache_path = Path("/tmp/not-used")

    def create_tar(
        self,
        path: Path | None = None,
        dirs: tuple[str, ...] = ("./", "./sbin"),
        files: tuple[str, ...] = ("./test-file", "./sbin/init"),
        special_files: bool = False,
    ) -> Path:
        """
        Create a test tar file, creating a temporary file if needed.

        The named dirs will be created at the beginning of the file.
        The named files will be created with dummy content.
        If special_files is set, a full range of file types will be included.
        """
        if path is None:
            path = Path(
                NamedTemporaryFile(
                    prefix="debusine-test-", suffix=".tar", delete=False
                ).name
            )
            cast(TestCase, self).addCleanup(path.unlink)

        mode: Literal["w", "w:xz"]
        if path.suffix == ".xz":
            mode = "w:xz"
        else:
            mode = "w"

        with tarfile.open(name=path, mode=mode) as tar:
            for dir_ in dirs:
                tarinfo = tarfile.TarInfo(name=dir_)
                tarinfo.type = tarfile.DIRTYPE
                tarinfo.mode = 0o755
                tar.addfile(tarinfo)
            for file in files:
                tarinfo = tarfile.TarInfo(name=file)
                tarinfo.size = 3
                tar.addfile(tarinfo, BytesIO(b"foo"))
            if special_files:
                tarinfo = tarfile.TarInfo(name="./special")
                tarinfo.type = tarfile.DIRTYPE
                tar.addfile(tarinfo)
                tarinfo = tarfile.TarInfo(name="./special/reg-file")
                tar.addfile(tarinfo, BytesIO(b"foo"))
                tarinfo = tarfile.TarInfo(name="./special/hard-link")
                tarinfo.type = tarfile.LNKTYPE
                tarinfo.linkname = "./special/reg-file"
                tar.addfile(tarinfo)
                tarinfo = tarfile.TarInfo(name="./special/sym-link")
                tarinfo.type = tarfile.SYMTYPE
                tarinfo.linkname = "/dev/null"
                tar.addfile(tarinfo)
                tarinfo = tarfile.TarInfo(name="./special/chr-dev")
                tarinfo.type = tarfile.CHRTYPE
                tarinfo.devmajor = 10
                tarinfo.devminor = 42
                tar.addfile(tarinfo)
                tarinfo = tarfile.TarInfo(name="./special/blk-dev")
                tarinfo.type = tarfile.BLKTYPE
                tarinfo.devmajor = 11
                tarinfo.devminor = 42
                tar.addfile(tarinfo)
                tarinfo = tarfile.TarInfo(name="./special/fifo-dev")
                tarinfo.type = tarfile.FIFOTYPE
                tar.addfile(tarinfo)

        return path

    @patch("debusine.tasks.executors.incus.run_incus_cmd")
    def test_image_in_cache_uncached(self, run_incus_cmd: MagicMock) -> None:
        """Test that image_in_cache fails to find a cached image."""
        run_incus_cmd.side_effect = CalledProcessError(
            cmd=["incus"], returncode=1
        )
        self.assertFalse(self.image_cache.image_in_cache(self.artifact))
        run_incus_cmd.assert_called_with(
            "image", "info", "artifact/42", suppress_stderr=True
        )

    @patch("debusine.tasks.executors.incus.run_incus_cmd")
    def test_image_in_cache_cached(self, run_incus_cmd: MagicMock) -> None:
        """Test that image_in_cache finds cached image."""
        run_incus_cmd.return_value = "Fingerprint: abc123\n"
        self.assertTrue(self.image_cache.image_in_cache(self.artifact))
        run_incus_cmd.assert_called_with(
            "image", "info", "artifact/42", suppress_stderr=True
        )

    @patch("debusine.tasks.executors.incus.run_incus_cmd")
    def test_process_downloaded_image(self, run_incus_cmd: MagicMock) -> None:
        """
        Test that process_downloaded_image calls build_incus_image and uploads.

        The real work is in build_incus_image(), so we mock everything and
        ensure that the expected calls are made.
        """
        built_path = Path("incus.tar.xz")
        with patch.object(
            self.image_cache,
            "build_incus_image",
            return_value=built_path,
        ) as build_incus_image:
            self.image_cache.process_downloaded_image(self.artifact)

        downloaded_path = self.image_cache.cache_artifact_image_path(
            42, self.artifact_base_name
        )
        build_incus_image.assert_called_with(
            downloaded_path, ANY, self.artifact
        )
        run_incus_cmd.assert_called_with(
            "image", "import", "incus.tar.xz", "--alias", "artifact/42"
        )

    def test_image_metadata(self) -> None:
        """Test that _image_metadata returns the correct metadata."""
        metadata = self.image_cache._image_metadata(self.artifact)

        expected = {
            "architecture": "x86_64",
            "creation_date": 1704067200,
            "profiles": ["debusine"],
            "properties": {
                "debian_architecture": "amd64",
                "debusine_artifact_id": 42,
                "description": (
                    f"Debusine Artifact #42: "
                    f"{self.artifact_category} for debian bookworm amd64"
                ),
                "os": "debian",
                "release": "bookworm",
            },
            "templates": {
                "/etc/hostname": {
                    "create_only": False,
                    "properties": {},
                    "template": "hostname.tpl",
                    "when": ["create", "copy"],
                },
                "/etc/hosts": {
                    "create_only": False,
                    "properties": {},
                    "template": "hosts.tpl",
                    "when": ["create", "copy"],
                },
            },
        }
        self.assertEqual(metadata, expected)

    def test_add_tar_directory(self) -> None:
        """Test that the add_tar_directory helper adds a directory."""
        buffer = BytesIO()
        with tarfile.TarFile(fileobj=buffer, mode="w") as tar:
            self.image_cache.add_tar_directory(tar, "foo")

        buffer.seek(0)

        with tarfile.TarFile(fileobj=buffer, mode="r") as tar:
            members = tar.getmembers()
            self.assertEqual(len(members), 1)
            member = members[0]
            self.assertEqual(member.name, "foo")
            self.assertTrue(member.isdir())
            self.assertEqual(member.mode, 0o755)
            self.assertEqual(member.uid, 0)
            self.assertEqual(member.gid, 0)

    def test_add_tar_file(self) -> None:
        """Test that the add_tar_file helper adds a file."""
        buffer = BytesIO()
        with tarfile.TarFile(fileobj=buffer, mode="w") as tar:
            self.image_cache.add_tar_file(tar, "foo", "bar")

        buffer.seek(0)

        with tarfile.TarFile(fileobj=buffer, mode="r") as tar:
            members = tar.getmembers()
            self.assertEqual(len(members), 1)
            member = members[0]
            self.assertEqual(member.name, "foo")
            self.assertTrue(member.isreg())
            self.assertEqual(member.mode, 0o644)
            self.assertEqual(member.uid, 0)
            self.assertEqual(member.gid, 0)
            member_file = tar.extractfile(member)
            assert member_file is not None
            self.assertEqual(member_file.read(), b"bar")

    def test_add_tar_symlink(self) -> None:
        """Test that the add_tar_symlink helper adds a symlink."""
        buffer = BytesIO()
        with tarfile.TarFile(fileobj=buffer, mode="w") as tar:
            self.image_cache.add_tar_symlink(tar, "foo", "bar")

        buffer.seek(0)

        with tarfile.TarFile(fileobj=buffer, mode="r") as tar:
            members = tar.getmembers()
            self.assertEqual(len(members), 1)
            member = members[0]
            self.assertEqual(member.name, "foo")
            self.assertTrue(member.issym())
            self.assertEqual(member.uid, 0)
            self.assertEqual(member.gid, 0)
            self.assertEqual(member.linkname, "bar")


class IncusLXCImageCacheTests(IncusImageCacheCommonTests):
    """Unit tests for IncusLXCImageCache."""

    image_cache_cls = IncusLXCImageCache
    artifact_base_name = "system.tar.xz"
    artifact_category = ExecutorImageCategory.TARBALL

    def setUp(self) -> None:
        """Mock the image handling for tests."""
        super().setUp()
        self.artifact = self.fake_system_tarball_artifact()

    def test_prepare_incus_image(self) -> None:
        """Test that prepare copies over the image and metadata."""
        source = self.create_tar(
            dirs=("./", "./etc", "./sbin"), special_files=True
        )
        dest_buf = BytesIO()
        with tarfile.TarFile(fileobj=dest_buf, mode="w") as dest_tar:
            self.image_cache.prepare_incus_image(
                source, dest_tar, self.artifact
            )

        dest_buf.seek(0)

        with tarfile.open(fileobj=dest_buf) as tar:
            names = tar.getnames()
            self.assertEqual(
                names,
                [
                    "metadata.yaml",
                    "templates",
                    "templates/hosts.tpl",
                    "templates/hostname.tpl",
                    "rootfs",
                    "rootfs/etc",
                    "rootfs/sbin",
                    "rootfs/test-file",
                    "rootfs/sbin/init",
                    "rootfs/special",
                    "rootfs/special/reg-file",
                    "rootfs/special/hard-link",
                    "rootfs/special/sym-link",
                    "rootfs/special/chr-dev",
                    "rootfs/special/blk-dev",
                    "rootfs/special/fifo-dev",
                    "rootfs/etc/systemd",
                    "rootfs/etc/systemd/system",
                    "rootfs/etc/systemd/system-generators",
                    "rootfs/etc/systemd/system/multi-user.target.wants",
                    (
                        "rootfs/etc/systemd/system/multi-user.target.wants/"
                        "systemd-networkd.service"
                    ),
                    (
                        "rootfs/etc/systemd/system/network-online.target.wants/"
                        "systemd-networkd-wait-online.service"
                    ),
                    "rootfs/etc/systemd/system-generators/lxc",
                ],
            )

            tarinfo = tar.getmember("rootfs/special/hard-link")
            self.assertEqual(tarinfo.linkname, "rootfs/special/reg-file")
            tarinfo = tar.getmember("rootfs/special/sym-link")
            self.assertEqual(tarinfo.linkname, "/dev/null")

    def test_build_incus_image(self) -> None:
        """Test that build_incus_image injects basic metadata into a new tar."""
        directory = self.create_temporary_directory()
        source_path = directory / "system.tar.xz"
        self.create_tar(source_path)
        workdir = directory / "workdir"
        workdir.mkdir()

        image = self.image_cache.build_incus_image(
            source_path, workdir, self.artifact
        )

        self.assertTrue(image.exists())
        with tarfile.open(image) as tar:
            names = set(tar.getnames())
            self.assertIn("metadata.yaml", names)
            metadata_file = tar.extractfile("metadata.yaml")
            assert metadata_file is not None
            metadata = yaml.safe_load(metadata_file)

        self.assertNotIn("test-file", names)
        self.assertEqual(
            metadata, self.image_cache._image_metadata(self.artifact)
        )

    def test_prepare_incus_image_requires_init(self) -> None:
        """Test that prepare only accepts bootable images."""
        source = self.create_tar(files=())
        dest_buf = BytesIO()
        with tarfile.TarFile(fileobj=dest_buf, mode="w") as dest_tar:
            with self.assertRaises(ImageImportError):
                self.image_cache.prepare_incus_image(
                    source, dest_tar, self.artifact
                )

    def test_prepare_incus_image_accepts_usrmerged_init(self) -> None:
        """Test that prepare accepts a /usr/sbin/init."""
        source = self.create_tar(
            dirs=("./", "./usr", "./usr/sbin"),
            files=("./usr/sbin/init",),
        )
        dest_buf = BytesIO()
        with tarfile.TarFile(fileobj=dest_buf, mode="w") as dest_tar:
            self.image_cache.prepare_incus_image(
                source, dest_tar, self.artifact
            )


class IncusVMImageCacheTests(IncusImageCacheCommonTests):
    """Unit tests for IncusVMImageCache."""

    image_cache_cls = IncusVMImageCache
    artifact_base_name = "image.qcow2"
    artifact_category = ExecutorImageCategory.IMAGE

    def setUp(self) -> None:
        """Mock the image handling for tests."""
        super().setUp()
        self.artifact = self.fake_system_image_artifact()

    def test_prepare_qcow2_incus_image(self) -> None:
        """Test that prepare converts qcow2 images."""
        with NamedTemporaryFile(
            prefix="debusine-test-", suffix=".qcow2", delete=False
        ) as source:
            source.write(b"qcow!")
            source_path = Path(source.name)
            self.addCleanup(source_path.unlink)
        dest_buf = BytesIO()
        with tarfile.TarFile(fileobj=dest_buf, mode="w") as dest_tar:
            self.image_cache.prepare_incus_image(
                source_path, dest_tar, self.artifact
            )

        dest_buf.seek(0)

        with tarfile.open(fileobj=dest_buf) as tar:
            names = tar.getnames()
            image_file = tar.extractfile("rootfs.img")
            assert image_file is not None
            image = image_file.read()

        self.assertEqual(
            names,
            [
                "metadata.yaml",
                "templates",
                "templates/hosts.tpl",
                "templates/hostname.tpl",
                "rootfs.img",
            ],
        )
        self.assertEqual(image, b"qcow!")

    @patch("debusine.tasks.executors.incus.check_call")
    def test_prepare_raw_incus_image(self, check_call: MagicMock) -> None:
        """Test that prepare converts raw images."""
        self.artifact.data["image_format"] = "raw"
        self.artifact.data["filename"] = "image.tar.xz"

        directory = Path(mkdtemp(prefix="debusine-tests-"))
        self.addCleanup(rmtree, directory)
        source = self.create_tar(
            path=directory / "image.tar.xz",
            files=("img.raw",),
            dirs=(),
        )
        dest_buf = BytesIO()

        def fake_convert(args: list[str]) -> None:
            """Fake for subprocess.check_call."""
            assert args[:2] == ["qemu-img", "convert"]
            with Path(args[-1]).open("wb") as f:
                f.write(b"qcow!")

        check_call.side_effect = fake_convert

        with tarfile.TarFile(fileobj=dest_buf, mode="w") as dest_tar:
            self.image_cache.prepare_incus_image(
                source, dest_tar, self.artifact
            )

        extracted = directory / "img.raw"
        self.assertFalse(extracted.exists())

        check_call.assert_called_with(
            [
                "qemu-img",
                "convert",
                "-f",
                "raw",
                "-O",
                "qcow2",
                str(extracted),
                str(directory / "image.qcow2"),
            ]
        )

        dest_buf.seek(0)

        with tarfile.open(fileobj=dest_buf) as tar:
            names = tar.getnames()
            image_file = tar.extractfile("rootfs.img")
            assert image_file is not None
            image = image_file.read()
            metadata_file = tar.extractfile("metadata.yaml")
            assert metadata_file is not None
            metadata = yaml.safe_load(metadata_file)

        self.assertEqual(
            names,
            [
                "metadata.yaml",
                "templates",
                "templates/hosts.tpl",
                "templates/hostname.tpl",
                "rootfs.img",
            ],
        )
        self.assertEqual(image, b"qcow!")
        self.assertEqual(
            metadata, self.image_cache._image_metadata(self.artifact)
        )


class IncusExecutorCommonTests(ExternalTaskHelperMixin[Noop], TestCase):
    """Unit tests that apply to both Incus Executors."""

    executor_cls: type[ExecutorInterface]
    uses_images: bool

    def setUp(self) -> None:
        """Mock the Debusine API for tests."""
        super().setUp()
        self.debusine_api = MagicMock(spec=Debusine)
        self.image_artifact = self.mock_image_download(
            self.debusine_api, system_image=self.uses_images
        )

        run_incus_cmd_patcher = patch(
            "debusine.tasks.executors.incus.run_incus_cmd"
        )
        self.run_incus_cmd = run_incus_cmd_patcher.start()
        self.addCleanup(run_incus_cmd_patcher.stop)

        self.executor = self.executor_cls(self.debusine_api, 42)

    def test_available(self) -> None:
        """Test that available() returns True if incus is available."""
        self.mock_is_command_available(
            {
                "incus": True,
                "autopkgtest-virt-incus": True,
            }
        )
        self.run_incus_cmd.return_value = "environment:\n  driver: lxc | qemu\n"
        self.assertTrue(self.executor_cls.available())
        self.run_incus_cmd.assert_called_once_with("info")

    def test_available_no_incus(self) -> None:
        """Test that available() returns False if incus is not available."""
        self.mock_is_command_available(
            {
                "incus": False,
                "autopkgtest-virt-incus": True,
            }
        )
        self.run_incus_cmd.return_value = "environment:\n  driver: lxc | qemu\n"
        self.assertFalse(self.executor_cls.available())

    def test_available_no_autopkgtest_virt_incus(self) -> None:
        """Not available() if autopkgtest-virt-incus is not available."""
        self.mock_is_command_available(
            {
                "incus": True,
                "autopkgtest-virt-incus": False,
            }
        )
        self.run_incus_cmd.return_value = "environment:\n  driver: lxc | qemu\n"
        self.assertFalse(self.executor_cls.available())

    def test_available_no_incus_permission(self) -> None:
        """Test that available() returns False if the worker can't run incus."""
        self.mock_is_command_available(
            {
                "incus": True,
                "autopkgtest-virt-incus": True,
            }
        )
        self.run_incus_cmd.side_effect = CalledProcessError(
            cmd=("incus", "info"), returncode=1
        )
        self.assertFalse(self.executor_cls.available())

    def test_instantiation_fetches_artifact(self) -> None:
        """Test that instantiating the Executor fetches the artifact."""
        self.assertEqual(self.executor.system_image, self.image_artifact)

    def test_download_image(self) -> None:
        """Test that download_image calls the ImageCache.download_image."""
        self.image_artifact = self.mock_image_download(
            self.debusine_api, create_image=True, system_image=self.uses_images
        )

        def fake_run_incus_cmd(*args: str, **kwargs: Any) -> str:
            """Fake for run_incus_cmd."""
            if args == ("image", "info", "artifact/42"):
                raise CalledProcessError(cmd=("incus",) + args, returncode=1)
            if args[:2] == ("image", "import"):
                return "Fingerprint: abc123\n"
            raise AssertionError(f"Unexpected args: {args}")

        self.run_incus_cmd.side_effect = fake_run_incus_cmd

        response = self.executor.download_image()

        self.run_incus_cmd.assert_any_call(
            "image", "info", "artifact/42", suppress_stderr=True
        )
        self.run_incus_cmd.assert_called_with(
            "image", "import", ANY, "--alias", "artifact/42"
        )
        expected_name = "artifact/42"
        self.assertEqual(response, expected_name)

    def test_image_name(self) -> None:
        """Test that image_name returns the expected image name."""
        response = self.executor.image_name()
        expected_name = "artifact/42"
        self.assertEqual(response, expected_name)

    def test_autopkgtest_virt_server(self) -> None:
        """Test that autopkgtest_virt_server returns incus."""
        self.assertEqual(self.executor.autopkgtest_virt_server(), "incus")

    def test_create(self) -> None:
        """Test create() return IncusInstance instance."""
        incus_instance = self.executor.create()

        self.assertIsInstance(incus_instance, IncusInstance)

    @patch("debusine.tasks.executors.incus.run_incus_cmd")
    def test_clean_up_image(self, run_incus_cmd: MagicMock) -> None:
        """Test that IncusImageCache.clean_up_image removes incus images."""
        assert issubclass(self.executor_cls, IncusExecutorMixin)
        self.executor_cls.clean_up_image(42)
        run_incus_cmd.assert_called_once_with("image", "rm", "artifact/42")

    def test_get_statistics_without_instance(self) -> None:
        """`get_statistics` returns empty statistics without an instance."""
        self.assertEqual(
            self.executor_cls.get_statistics(None), ExecutorStatistics()
        )

    def test_get_statistics_with_instance(self) -> None:
        """`get_statistics` returns all statistics with an instance."""
        instance = IncusInstance("image-name")
        instance._instance = "instance"
        run_result = MagicMock()
        run_result.returncode = 0
        run_result.stdout = dedent(
            """\
            # HELP incus_cpu_effective_total The total number of effective CPUs.
            # TYPE incus_cpu_effective_total gauge
            incus_cpu_effective_total{name="instance"} 2
            incus_cpu_effective_total{name="other-instance"} 4
            # HELP incus_filesystem_avail_bytes The number of available space in bytes.
            # TYPE incus_filesystem_avail_bytes gauge
            incus_filesystem_avail_bytes{mountpoint="/",name="instance"} 1e+10
            incus_filesystem_avail_bytes{mountpoint="/build",name="instance"} 6.7e+10
            incus_filesystem_avail_bytes{mountpoint="/",name="other-instance"} 1e+10
            # HELP incus_filesystem_size_bytes The size of the filesystem in bytes.
            # TYPE incus_filesystem_size_bytes gauge
            incus_filesystem_size_bytes{mountpoint="/",name="instance"} 2e+10
            incus_filesystem_size_bytes{mountpoint="/build",name="instance"} 7.4e+10
            incus_filesystem_size_bytes{mountpoint="/",name="other-instance"} 2e+10
            # HELP incus_memory_MemFree_bytes The amount of free memory.
            # TYPE incus_memory_MemFree_bytes gauge
            incus_memory_MemFree_bytes{name="instance"} 2.0e+9
            incus_memory_MemFree_bytes{name="other-instance"} 3.1e+9
            # HELP incus_memory_MemTotal_bytes The amount of used memory.
            # TYPE incus_memory_MemTotal_bytes gauge
            incus_memory_MemTotal_bytes{name="instance"} 2.1e+9
            incus_memory_MemTotal_bytes{name="other-instance"} 3.2e+9
            # HELP incus_warnings_total The number of active warnings.
            # TYPE incus_warnings_total counter
            incus_warnings_total 0
            """  # noqa: E501
        )

        with patch(
            "subprocess.run", autospec=True, return_value=run_result
        ) as mock_run:
            statistics = self.executor_cls.get_statistics(instance)

        mock_run.assert_called_once_with(
            ["incus", "query", "/1.0/metrics"],
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertEqual(
            statistics,
            ExecutorStatistics(
                memory=100_000_000,
                disk_space=17_000_000_000,
                available_memory=2_100_000_000,
                available_disk_space=94_000_000_000,
                cpu_count=2,
            ),
        )

    def test_get_statistics_with_instance_limited_details(self) -> None:
        """`get_statistics` only returns statistics that are available."""
        instance = IncusInstance("image-name")
        instance._instance = "instance"
        run_result = MagicMock()
        run_result.returncode = 0
        run_result.stdout = dedent(
            """\
            # HELP incus_cpu_effective_total The total number of effective CPUs.
            # TYPE incus_cpu_effective_total gauge
            incus_cpu_effective_total{name="instance"} 2
            """  # noqa: E501
        )

        with patch(
            "subprocess.run", autospec=True, return_value=run_result
        ) as mock_run:
            statistics = self.executor_cls.get_statistics(instance)

        mock_run.assert_called_once_with(
            ["incus", "query", "/1.0/metrics"],
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertEqual(statistics, ExecutorStatistics(cpu_count=2))


class IncusLXCExecutorTests(IncusExecutorCommonTests):
    """Unit tests for IncusLXCExecutor."""

    executor_cls = IncusLXCExecutor
    uses_images = False

    def test_backend_name(self) -> None:
        """Test that the backend_name attribute was set."""
        self.assertEqual(self.executor_cls.backend_name, "incus-lxc")

    def test_executor_class_finds_incus_lxc(self) -> None:
        """Test that executor_class() supports incus-lxc."""
        instance = executor_class("incus-lxc")
        self.assertEqual(instance, self.executor_cls)

    def test_available_no_driver(self) -> None:
        """Test available() returns False if the lxc driver isn't there."""
        self.mock_is_command_available(
            {
                "incus": True,
                "autopkgtest-virt-incus": True,
            }
        )
        self.run_incus_cmd.return_value = "environment:\n  driver: qemu\n"
        self.assertFalse(self.executor_cls.available())

    def test_available_has_driver(self) -> None:
        """Test available() returns True if the lxc driver is there."""
        self.mock_is_command_available(
            {
                "incus": True,
                "autopkgtest-virt-incus": True,
            }
        )
        self.run_incus_cmd.return_value = "environment:\n  driver: lxc\n"
        self.assertTrue(self.executor_cls.available())

    def test_autopkgtest_virt_args(self) -> None:
        """Test that autopkgtest_virt_args returns sane arguments."""
        self.assertEqual(
            self.executor.autopkgtest_virt_args(),
            ["artifact/42", "--", "--profile", "debusine"],
        )


class IncusVMExecutorTests(IncusExecutorCommonTests):
    """Unit tests for IncusVMExecutor."""

    executor_cls = IncusVMExecutor
    uses_images = True

    def test_backend_name(self) -> None:
        """Test that the backend_name attribute was set."""
        self.assertEqual(self.executor_cls.backend_name, "incus-vm")

    def test_executor_class_finds_incus_vm(self) -> None:
        """Test that executor_class() supports incus-vm."""
        instance = executor_class("incus-vm")
        self.assertEqual(instance, self.executor_cls)

    def test_available_no_driver(self) -> None:
        """Test available() returns False if the qemu driver isn't there."""
        self.mock_is_command_available({"incus": True})
        self.run_incus_cmd.return_value = "environment:\n  driver: lxc\n"
        self.assertFalse(self.executor_cls.available())

    def test_available_has_driver(self) -> None:
        """Test available() returns True if the qemu driver is there."""
        self.mock_is_command_available(
            {
                "incus": True,
                "autopkgtest-virt-incus": True,
            }
        )
        self.run_incus_cmd.return_value = "environment:\n  driver: qemu\n"
        self.assertTrue(self.executor_cls.available())

    def test_autopkgtest_virt_args(self) -> None:
        """Test that autopkgtest_virt_args returns sane arguments."""
        self.assertEqual(
            self.executor.autopkgtest_virt_args(),
            ["--vm", "artifact/42", "--", "--profile", "debusine"],
        )


class IncusInstanceTests(TestCase):
    """Tests for IncusInstance class."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.instance = IncusInstance("image-name")

        run_incus_cmd_patcher = patch(
            "debusine.tasks.executors.incus.run_incus_cmd"
        )
        self.run_incus_cmd = run_incus_cmd_patcher.start()
        self.addCleanup(run_incus_cmd_patcher.stop)

        def fake_run_incus_cmd(*args: str) -> str:
            """
            Fake for run_incus_cmd.

            Pretend to operate normally.
            """
            if args[0] == "info":  # _generate_instance_name() probes
                raise CalledProcessError(cmd=("incus",) + args, returncode=1)
            elif args[0] == "launch":
                return "Launching\n"
            elif args[0] == "stop":
                return ""
            elif args[0] == "restart":
                return ""
            elif args[0] == "file":  # push/pull
                return ""
            raise AssertionError(f"Unexpected args: {args}")

        self.run_incus_cmd.side_effect = fake_run_incus_cmd

    def patch_spin_until_started(self) -> None:
        """Patch _spin_until_started() out for testing."""
        patcher = patch.object(self.instance, "_spin_until_started")
        self._spin_until_started_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_generate_instance_name(self) -> None:
        """Test that _generate_instance_name() picks an unused name."""
        rejected = None

        def fake_run_incus_cmd(*args: str) -> str:
            """
            Fake for run_incus_cmd.

            Reject the first name tested, accept the second.
            """
            nonlocal rejected
            if args[0] != "info":
                raise AssertionError(f"Unexpected args: {args}")
            if rejected:
                raise CalledProcessError(cmd=("incus",) + args, returncode=1)
            else:
                rejected = args[1]
                return "Fingerprint: abc123\n"

        self.run_incus_cmd.side_effect = fake_run_incus_cmd

        instance_name = IncusInstance._generate_instance_name()

        self.assertIsNotNone(rejected)
        self.assertNotEqual(rejected, instance_name)
        calls = self.run_incus_cmd.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], call("info", rejected))
        self.assertEqual(calls[1], call("info", instance_name))

    def test_start(self) -> None:
        """Test that start() launches an instance."""
        self.patch_spin_until_started()
        self.instance.start()

        # First call would have been generating an instance name
        self.assertEqual(len(self.run_incus_cmd.call_args_list), 2)
        self.run_incus_cmd.assert_called_with(
            "launch", "--ephemeral", "--profile", "debusine", "image-name", ANY
        )
        self.assertIsNotNone(self.instance._instance)
        self._spin_until_started_mock.assert_called_once_with()

    @patch("subprocess.run")
    def test_spin_until_started(self, run: MagicMock) -> None:
        """Test that spin_until_started() spins until it succeeds."""
        count = 0

        def fake_run(args: list[str], **kwargs: Any) -> CompletedProcess[str]:
            """
            Fake for subprocess.run().

            Pretend to spin once.
            """
            nonlocal count
            returncode = 0
            stdout = ""
            stderr = ""
            if count == 0:
                returncode = 1
                stderr = "Error: VM agent isn't currently running\n"
            elif count == 1:
                returncode = 1
                stderr = (
                    "Failed to connect to system scope bus via local "
                    "transport: No such file or directory\n"
                )
            elif count == 2:
                returncode = 1
                stdout = "initializing\n"
            elif count == 3:
                returncode = 1
                stdout = "starting\n"
            else:
                stdout = "running\n"
            count += 1
            return CompletedProcess(
                args=args,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        run.side_effect = fake_run

        self.instance._instance = "fake-instance"
        self.instance._spin_until_started(sleep_time=0, timeout=2)

        run.assert_called_with(
            [
                "incus",
                "exec",
                self.instance._instance,
                "systemctl",
                "is-system-running",
            ],
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_spin_until_started_unexpected_failure(
        self, run: MagicMock
    ) -> None:
        """Test that spin_until_started() may raise InstanceNotRunning."""
        count = 0

        def fake_run(args: list[str], **kwargs: Any) -> CompletedProcess[str]:
            """
            Fake for subprocess.run().

            Pretend to spin once.
            """
            nonlocal count
            returncode = 0
            stdout = ""
            stderr = ""
            if count == 0:
                returncode = 1
                stderr = "Something weird"
            else:
                returncode = 0
                stdout = "running\n"
            count += 1
            return CompletedProcess(
                args=args,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        run.side_effect = fake_run

        self.instance._instance = "fake-instance"

        with self.assertLogsContains(
            "Unexpected response from exec systemctl is-system-running: "
            "returncode: 1, stdout: , stderr: Something weird",
            logger=log,
        ):
            self.instance._spin_until_started(sleep_time=0, timeout=5)

    def test_spin_until_started_times_out(self) -> None:
        """Test that spin_until_started() times out."""
        self.instance._instance = "fake-instance"
        with self.assertRaisesRegex(
            InstanceNotRunning,
            "Incus Instance fake-instance hasn't booted or is missing "
            "incus-agent",
        ):
            self.instance._spin_until_started(timeout=0)

    def test_restart(self) -> None:
        """Test that restart() restarts an instance."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance
        self.run_incus_cmd.reset_mock()

        self.instance.restart()

        self.run_incus_cmd.assert_called_with("restart", instance_name)
        self.assertIsNotNone(self.instance._instance)

    def test_stop(self) -> None:
        """Test that stop() stops an instance."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance
        self.run_incus_cmd.reset_mock()

        self.instance.stop()

        self.run_incus_cmd.assert_called_with("stop", instance_name)
        self.assertIsNone(self.instance._instance)

    def test_file_push(self) -> None:
        """Test that file_push() copies a file into an instance."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance
        self.run_incus_cmd.reset_mock()

        self.instance.file_push(Path("/some/input"), Path("/tmp/destination"))

        self.run_incus_cmd.assert_called_with(
            "file",
            "push",
            "--uid",
            "0",
            "--gid",
            "0",
            "--mode",
            "644",
            "/some/input",
            f"{instance_name}/tmp/destination",
        )

    def test_directory_push(self) -> None:
        """Test that directory_push() copies a directory into an instance."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance
        self.run_incus_cmd.reset_mock()
        self.run_incus_cmd.side_effect = None

        self.instance.directory_push(Path("/some/directory"), Path("/tmp"))

        self.run_incus_cmd.assert_has_calls(
            [
                call(
                    "file",
                    "push",
                    "--recursive",
                    "/some/directory",
                    f"{instance_name}/tmp",
                ),
                call(
                    "exec",
                    instance_name,
                    "--",
                    "chown",
                    "-R",
                    "0:0",
                    "/tmp/directory",
                ),
            ]
        )

    def test_file_pull(self) -> None:
        """Test that file_pull() copies a file into an instance."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance
        self.run_incus_cmd.reset_mock()

        self.instance.file_pull(Path("/etc/hosts"), Path("/tmp/destination"))

        self.run_incus_cmd.assert_called_with(
            "file", "pull", f"{instance_name}/etc/hosts", "/tmp/destination"
        )

    @patch("subprocess.run")
    def test_run(self, run: MagicMock) -> None:
        """Test that run() executes commands as root."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance
        self.run_incus_cmd.reset_mock()

        self.instance._uid_map["_debusine"] = 1042

        self.instance.run(["true"])

        run.assert_called_with(
            ["incus", "exec", instance_name, "--user", "1042", "--", "true"],
            text=None,
            stdout=PIPE,
            stderr=PIPE,
        )

    @patch("subprocess.run")
    def test_run_as_root(self, run: MagicMock) -> None:
        """Test that run() executes commands as non-root."""
        self.patch_spin_until_started()
        self.instance.start()
        instance_name = self.instance._instance

        self.instance.run(["true", "--help"], run_as_root=True)

        run.assert_called_with(
            ["incus", "exec", instance_name, "--", "true", "--help"],
            text=None,
            stdout=PIPE,
            stderr=PIPE,
        )


# Avoid running tests from common base classes.
del IncusImageCacheCommonTests
del IncusExecutorCommonTests
