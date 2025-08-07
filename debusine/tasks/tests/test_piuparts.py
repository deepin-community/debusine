# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the piuparts task support on the worker."""
import datetime
import lzma
import os
import shlex
import tarfile
import textwrap
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from unittest import mock
from unittest.mock import MagicMock, call

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSystemTarball,
    DebianUpload,
    EmptyArtifactData,
)
from debusine.client.models import (
    ArtifactResponse,
    FileResponse,
    FilesResponseType,
    StrMaxLength255,
)
from debusine.tasks import Piuparts, TaskConfigError
from debusine.tasks.models import LookupMultiple, PiupartsDynamicData
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import create_system_tarball_data


class PiupartsTests(ExternalTaskHelperMixin[Piuparts], TestCase):
    """Test the Piuparts class."""

    SAMPLE_TASK_DATA = {
        "input": {"binary_artifacts": [421]},
        "host_architecture": "amd64",
        "environment": "debian/match:codename=bookworm",
        "base_tgz": 44,
    }

    _binary_upload_data = DebianUpload(
        type="dpkg",
        changes_fields={
            "Architecture": "amd64",
            "Files": [{"name": "hello_1.0_amd64.deb"}],
            "Source": "hello",
        },
    )

    def setUp(self) -> None:  # noqa: D102
        self.task = Piuparts(self.SAMPLE_TASK_DATA)
        self.task.dynamic_data = PiupartsDynamicData(
            environment_id=1, input_binary_artifacts_ids=[421], base_tgz_id=44
        )

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning with python -m unittest."""
        if self.task._debug_log_files_directory is not None:
            self.task._debug_log_files_directory.cleanup()

    def test_configure_fails_with_missing_required_data(  # noqa: D102
        self,
    ) -> None:
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"input": {"artifact": 1}})

    def test_configure_environment_is_required(self) -> None:
        """Missing required field "environment": raise exception."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(remove=["environment"])

    def test_configure_base_tgz_is_required(self) -> None:
        """Missing required field "base_tgz": raise exception."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(remove=["base_tgz"])

    def test_configure_host_architecture_is_required(self) -> None:
        """Missing required field "host_architecture": raise exception."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(remove=["host_architecture"])

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        binary_artifacts_lookup = LookupMultiple.parse_obj([421])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # base_tgz
                (44, CollectionCategory.ENVIRONMENTS): ArtifactInfo(
                    id=44,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.UPLOAD,
                        data=self._binary_upload_data,
                    )
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            PiupartsDynamicData(
                environment_id=1,
                input_binary_artifacts_ids=[421],
                base_tgz_id=44,
                subject="hello",
                runtime_context="sid:amd64",
                configuration_context="sid",
            ),
        )

    def test_compute_dynamic_data_base_tgz_constraints(self) -> None:
        """Dynamic data applies environment-like constraints to base_tgz."""
        self.task = Piuparts(
            {
                **self.SAMPLE_TASK_DATA,
                "base_tgz": "debian/match:codename=bookworm",
            }
        )
        binary_artifacts_lookup = LookupMultiple.parse_obj([421])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # base_tgz
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=44,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(codename="bookworm"),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.UPLOAD,
                        data=self._binary_upload_data,
                    )
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            PiupartsDynamicData(
                environment_id=1,
                input_binary_artifacts_ids=[421],
                base_tgz_id=44,
                subject="hello",
                runtime_context="bookworm:amd64",
                configuration_context="bookworm",
            ),
        )

    def test_compute_dynamic_raise_config_task_error_wrong_environment(
        self,
    ) -> None:
        """
        Test compute_dynamic_data raise TaskConfigError.

        environment artifact category is unexpected.
        """
        binary_artifacts_lookup = LookupMultiple.parse_obj([421])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # base_tgz
                (44, CollectionCategory.ENVIRONMENTS): ArtifactInfo(
                    id=44,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=EmptyArtifactData(),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.UPLOAD,
                        data=EmptyArtifactData(),
                    )
                ]
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            "^base_tgz: unexpected artifact category: 'debian:source-package'. "
            r"Valid categories: \['debian:system-tarball'\]$",
        ):
            self.task.compute_dynamic_data(task_db),

    def test_compute_dynamic_raise_config_task_error_wrong_binary_artifact(
        self,
    ) -> None:
        """
        Test compute_dynamic_data raise TaskConfigError.

        binary_artifact artifact category is unexpected.
        """
        binary_artifacts_lookup = LookupMultiple.parse_obj([421])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # base_tgz
                (44, CollectionCategory.ENVIRONMENTS): ArtifactInfo(
                    id=44,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.SYSTEM_TARBALL,
                        data=EmptyArtifactData(),
                    )
                ]
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            "^input.binary_artifact: unexpected artifact category: "
            r"'debian:system-tarball'. Valid categories: "
            r"\['debian:binary-packages', 'debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_cmdline_without_usr_sbin(self) -> None:
        """_cmdline adds `/usr/sbin` to `PATH` if it isn't there."""
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            self.assertEqual(
                self.task._cmdline()[:3],
                ["env", "PATH=/usr/sbin:/usr/bin:/bin", "piuparts"],
            )

    def test_cmdline_with_usr_sbin(self) -> None:
        """_cmdline doesn't add `/usr/sbin` to `PATH` if it's already there."""
        with mock.patch.dict(
            os.environ, {"PATH": "/usr/sbin:/sbin:/usr/bin:/bin"}
        ):
            self.assertEqual(
                self.task._cmdline()[:3],
                ["env", "PATH=/usr/sbin:/sbin:/usr/bin:/bin", "piuparts"],
            )

    def test_cmdline_as_root(self) -> None:
        """_cmdline_as_root() return True."""
        self.assertTrue(self.task._cmdline_as_root())

    def patch_fetch_artifact_for_basetgz(
        self,
        *,
        with_dev: bool,
        with_resolvconf_symlink: bool = False,
        codename: str = "bookworm",
        filename: str = "system.tar.xz",
    ) -> MagicMock:
        """Patch self.task.fetch_artifact(), pretend to download basetgz."""
        patcher = mock.patch.object(self.task, "fetch_artifact", autospec=True)
        mocked = patcher.start()

        def _create_basetgz_file(
            artifact_id: int,
            destination: Path,
            default_category: CollectionCategory | None = None,  # noqa: U100
        ) -> ArtifactResponse:
            with tarfile.open(destination / filename, "w:xz") as tar:
                tarinfo = tarfile.TarInfo(name="./foo")
                tarinfo.size = 12
                tar.addfile(tarinfo, BytesIO(b"A tar member"))
                if with_resolvconf_symlink:
                    tarinfo = tarfile.TarInfo(name="./etc")
                    tarinfo.type = tarfile.DIRTYPE
                    tar.addfile(tarinfo)
                    tarinfo = tarfile.TarInfo(name="./etc/resolv.conf")
                    tarinfo.type = tarfile.SYMTYPE
                    tarinfo.linkname = "../run/systemd/resolve/stub-resolv.conf"
                    tar.addfile(tarinfo)

            fake_url = pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://not-used"
            )

            data = DebianSystemTarball(
                filename=filename,
                vendor="debian",
                codename=codename,
                mirror=fake_url,
                variant=None,
                pkglist={},
                architecture="amd64",
                with_dev=with_dev,
                with_init=False,
            )

            file_model = FileResponse(
                size=100,
                checksums={
                    "sha256": pydantic.parse_obj_as(StrMaxLength255, "not-used")
                },
                type="file",
                url=fake_url,
            )

            files = FilesResponseType({"system.tar.xz": file_model})

            return ArtifactResponse(
                id=artifact_id,
                workspace="System",
                category=ArtifactCategory.SYSTEM_TARBALL,
                created_at=datetime.datetime.now(),
                data=data.dict(),
                download_tar_gz_url=pydantic.parse_obj_as(
                    pydantic.AnyUrl,
                    f"http://localhost/artifact/{artifact_id}?archive=tar.gz",
                ),
                files_to_upload=[],
                files=files,
            )

        mocked.side_effect = _create_basetgz_file

        self.addCleanup(patcher.stop)

        return mocked

    def test_configure_for_execution_from_artifact_id_error_no_debs(
        self,
    ) -> None:
        """configure_for_execution() no .deb-s: return False."""
        download_directory = self.create_temporary_directory()
        (file1 := download_directory / "file1.dsc").write_text("")
        (file2 := download_directory / "file2.changes").write_text("")

        self.assertFalse(self.task.configure_for_execution(download_directory))

        files = sorted([str(file1), str(file2)])
        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()
        self.assertEqual(
            log_file_contents,
            f"There must be at least one *.deb file. "
            f"Current files: {files}\n",
        )

    def test_configure_for_execution_prepare_executor_base_tgz(self) -> None:
        """configure_for_execution() prepare the executor and base tgz."""
        download_directory = self.create_temporary_directory()
        (download_directory / "package1.deb").write_bytes(b"pkg1")

        self.patch_prepare_executor_instance()

        with mock.patch.object(
            self.task, "_prepare_base_tgz", autospec=True
        ) as prepare_base_tgz_mocked:
            self.assertTrue(
                self.task.configure_for_execution(download_directory)
            )

        run_called_with = [
            call(
                ["apt-get", "update"],
                run_as_root=True,
                check=True,
                stdout=mock.ANY,
                stderr=mock.ANY,
            ),
            call(
                ["apt-get", "--yes", "install", "piuparts", "mmdebstrap"],
                run_as_root=True,
                check=True,
                stdout=mock.ANY,
                stderr=mock.ANY,
            ),
        ]
        assert isinstance(self.task.executor_instance, MagicMock)
        self.task.executor_instance.run.assert_has_calls(run_called_with)

        prepare_base_tgz_mocked.assert_called_with(download_directory)

    def test_prepare_base_tgz_raise_assertion_error(self) -> None:
        """_prepare_base_tgz raise AssertionError: executor_instance is None."""
        msg = r"^self\.executor_instance cannot be None$"
        self.assertRaisesRegex(
            AssertionError,
            msg,
            self.task._prepare_base_tgz,
            self.create_temporary_directory(),
        )

    def test_prepare_base_tgz_download_base_tgz_no_processing(self) -> None:
        """
        _prepare_base_tgz does not do any artifact processing (with_dev=False).

        * Download the artifact to the correct directory
        * Set self.task._base_tar to it
        """
        codename = "bullseye"
        filename = "system-image.tar.xz"
        fetch_artifact_mocked = self.patch_fetch_artifact_for_basetgz(
            with_dev=False, codename=codename, filename=filename
        )

        # Use mock's side effect to set self.task.executor_instance
        self.patch_prepare_executor_instance()
        self.task._prepare_executor_instance()

        download_directory = self.create_temporary_directory()

        self.task._prepare_base_tgz(download_directory)

        destination_dir = download_directory / "base_tar"

        fetch_artifact_mocked.assert_called_with(
            self.task.data.base_tgz, destination_dir
        )
        self.assertEqual(self.task._base_tar, destination_dir / filename)

    def test_prepare_base_tgz_download_base_tgz_remove_dev_files(self) -> None:
        r"""_prepare_base_tgz download the artifact and remove /dev/\*."""
        # Use mock's side effect to set self.task.executor_instance
        self.patch_prepare_executor_instance()
        self.task._prepare_executor_instance()

        download_directory = self.create_temporary_directory()
        destination_dir = download_directory / "base_tar"

        fetch_artifact_mocked = self.patch_fetch_artifact_for_basetgz(
            with_dev=True
        )

        system_tar = destination_dir / "system.tar.gz"

        self.task._prepare_base_tgz(download_directory)

        fetch_artifact_mocked.assert_called_with(
            self.task.data.base_tgz, destination_dir
        )

        # Downloaded system.tar.xz, then processed it and wrote the result
        # to system.tar.gz
        self.assertEqual(self.task._base_tar, system_tar)

        assert isinstance(self.task.executor_instance, MagicMock)
        self.assertEqual(
            self.task.executor_instance.run.call_args[0][0],
            [
                "mmtarfilter",
                "--path-exclude=/dev/*",
            ],
        )

        reading_from_file = self.task.executor_instance.run.call_args[1][
            "stdin"
        ]
        writing_to_file = self.task.executor_instance.run.call_args[1]["stdout"]

        self.assertEqual(reading_from_file.mode, "rb")
        self.assertEqual(
            reading_from_file.name, str(destination_dir / "system.tar.xz")
        )

        self.assertEqual(writing_to_file.mode, "wb")
        self.assertEqual(
            writing_to_file.name, str(destination_dir / "system.tar")
        )

        self.assertEqual(
            list(destination_dir.iterdir()), [destination_dir / "system.tar.gz"]
        )

    def test_prepare_base_tgz_download_base_tgz_resolveconf(self) -> None:
        r"""_prepare_base_tgz download the artifact and install resolv.conf."""
        # Use mock's side effect to set self.task.executor_instance
        self.patch_prepare_executor_instance()
        self.task._prepare_executor_instance()

        download_directory = self.create_temporary_directory()
        destination_dir = download_directory / "base_tar"

        fetch_artifact_mocked = self.patch_fetch_artifact_for_basetgz(
            with_dev=False, with_resolvconf_symlink=True
        )

        def fake_mmtarfilter(
            args: list[str], stdin: BinaryIO, stdout: BinaryIO  # noqa: U100
        ) -> None:
            """Fake for run() that decompresses the tar."""
            stdout.write(lzma.decompress(stdin.read()))

        assert isinstance(self.task.executor_instance, MagicMock)
        self.task.executor_instance.run.side_effect = fake_mmtarfilter

        self.task._prepare_base_tgz(download_directory)

        fetch_artifact_mocked.assert_called_with(
            self.task.data.base_tgz, destination_dir
        )

        self.assertEqual(
            self.task.executor_instance.run.call_args[0][0],
            [
                "mmtarfilter",
                "--path-exclude=/etc/resolv.conf",
            ],
        )

        # Downloaded system.tar.xz, then processed it and wrote the result
        # to system.tar.gz
        system_tar = destination_dir / "system.tar.gz"
        self.assertEqual(self.task._base_tar, system_tar)

        with tarfile.open(system_tar, "r:gz") as tar:
            self.assertTrue(tar.getmember("./etc/resolv.conf").isreg())

    def prepare_scripts(self, codename: str) -> str:
        """Build and read the post_chroot_unpack_debusine script."""
        directory = self.create_temporary_directory()
        with mock.patch.object(self.task, "_base_tar_data", autospec=True):
            assert self.task._base_tar_data
            self.task._base_tar_data.codename = codename
            self.task._prepare_scripts(directory)
        script = directory / "post_chroot_unpack_debusine"
        self.assertTrue(script.exists())
        return script.read_text()

    def test_prepare_scripts_jessie(self) -> None:
        """_prepare_scripts with oneline apt sources."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.net",
                        "suite": "bookworm",
                        "components": ["main"],
                        "signing_key": "KEY A",
                    },
                ]
            }
        )
        script = self.prepare_scripts("jessie")
        self.assertIn("mkdir -p /etc/apt/keyrings\n", script)
        key = shlex.quote("KEY A\n")
        self.assertIn(
            f"printf %s {key} > /etc/apt/keyrings/extra_apt_key_0.asc\n",
            script,
        )
        source = shlex.quote(
            "deb [signed-by=/etc/apt/keyrings/extra_apt_key_0.asc] "
            "http://example.net bookworm main\n"
        )
        self.assertIn(
            (
                f"printf %s {source} > "
                f"/etc/apt/sources.list.d/extra_repository_0.list\n"
            ),
            script,
        )

    def test_prepare_scripts_buster(self) -> None:
        """_prepare_scripts with deb822 signed-by apt sources."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.net",
                        "suite": "bookworm",
                        "components": ["main"],
                        "signing_key": "KEY A",
                    },
                ]
            }
        )
        script = self.prepare_scripts("buster")
        self.assertIn("mkdir -p /etc/apt/keyrings\n", script)
        key = shlex.quote("KEY A\n")
        self.assertIn(
            f"printf %s {key} > /etc/apt/keyrings/extra_apt_key_0.asc\n",
            script,
        )
        source = shlex.quote(
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.net
                Suites: bookworm
                Components: main
                Signed-By: /etc/apt/keyrings/extra_apt_key_0.asc
                """
            )
        )
        self.assertIn(
            (
                f"printf %s {source} > "
                f"/etc/apt/sources.list.d/extra_repository_0.sources\n"
            ),
            script,
        )

    def test_prepare_scripts_bookworm(self) -> None:
        """_prepare_scripts with deb822 signed-by apt sources."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.net",
                        "suite": "bookworm",
                        "components": ["main"],
                        "signing_key": "KEY A",
                    },
                ]
            }
        )
        script = self.prepare_scripts("bookworm")
        self.assertNotIn("/etc/apt/keyrings", script)
        source = shlex.quote(
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.net
                Suites: bookworm
                Components: main
                Signed-By:
                 KEY A
                """
            )
        )
        self.assertIn(
            (
                f"printf %s {source} > "
                f"/etc/apt/sources.list.d/extra_repository_0.sources\n"
            ),
            script,
        )

    def test_execute(self) -> None:
        """Test full (mocked) execution."""
        self.configure_task(override={"input": {'binary_artifacts': [1, 2]}})
        self.task.dynamic_data = PiupartsDynamicData(
            environment_id=7, input_binary_artifacts_ids=[1, 2], base_tgz_id=42
        )
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()

        fetch_artifact_mocked = self.patch_fetch_artifact_for_basetgz(
            with_dev=True, codename="trixie"
        )
        self.assertTrue(self.task.fetch_input(download_directory))
        fetch_artifact_mocked.assert_any_call(1, download_directory)
        fetch_artifact_mocked.assert_any_call(2, download_directory)

        (file2 := download_directory / "file2.deb").write_text("")
        (file1 := download_directory / "file1.deb").write_text("")
        (file3 := download_directory / "makedev_2.3.1-97_all.deb").write_text(
            ""
        )

        self.assertTrue(self.task.configure_for_execution(download_directory))

        self.assertEqual(self.task._deb_files, [file1, file2, file3])
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            self.assertEqual(
                self.task._cmdline(),
                [
                    "env",
                    "PATH=/usr/sbin:/usr/bin:/bin",
                    "piuparts",
                    "--keep-sources-list",
                    "--allow-database",
                    "--warn-on-leftovers-after-purge",
                    f"--basetgz={self.task._base_tar}",
                    f"--scriptsdir={self.task._scripts_dir}",
                    str(file1),
                    str(file2),
                    str(file3),
                ],
            )

        self.task.upload_artifacts(download_directory, execution_success=True)

    def test_get_label_dynamic_data_is_none(self) -> None:
        """Test get_label if dynamic_data.subject is None."""
        self.assertEqual(self.task.get_label(), "piuparts")

    def test_get_label_dynamic_data_subject_is_none(self) -> None:
        """Test get_label if dynamic_data is None."""
        self.task.dynamic_data = PiupartsDynamicData(
            environment_id=1, input_binary_artifacts_ids=[], base_tgz_id=2
        )
        self.assertEqual(self.task.get_label(), "piuparts")

    def test_get_label_dynamic_data_subject_is_hello(self) -> None:
        """Test get_label if dynamic_data.subject is set."""
        self.task.dynamic_data = PiupartsDynamicData(
            environment_id=1,
            input_binary_artifacts_ids=[],
            base_tgz_id=2,
            subject="hello",
        )
        self.assertEqual(self.task.get_label(), "piuparts hello")
