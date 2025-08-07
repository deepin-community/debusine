# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for MmDebstrap class."""
import copy
import shlex
from pathlib import Path
from typing import cast
from unittest import mock
from unittest.mock import call

from debusine.artifacts.local_artifact import DebianSystemTarballArtifact
from debusine.tasks import MmDebstrap
from debusine.tasks.models import (
    BaseDynamicTaskData,
    MmDebstrapBootstrapOptions,
    MmDebstrapData,
    WorkerType,
)
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase


class MmDebstrapTests(ExternalTaskHelperMixin[MmDebstrap], TestCase):
    """Unit tests for MmDebstrap class."""

    SAMPLE_TASK_DATA = {
        "bootstrap_options": {
            "architecture": "amd64",
            "extra_packages": ["hello", "python3"],
            "use_signed_by": True,
        },
        "bootstrap_repositories": [
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "components": ["main", "contrib"],
                "check_signature_with": "system",
            },
            {
                "types": ["deb-src"],
                "mirror": "https://example.com",
                "suite": "bullseye",
                "components": ["main"],
                "check_signature_with": "system",
                "keyring": {"url": "https://example.com/keyring.gpg"},
            },
        ],
    }

    def setUp(self) -> None:
        """Initialize test."""
        self.configure_task()

    def test_compute_dynamic_data(self) -> None:
        """Test compute_dynamic_data."""
        task_database = FakeTaskDatabase()

        self.assertEqual(
            self.task.compute_dynamic_data(task_database),
            BaseDynamicTaskData(
                subject="bookworm",
                runtime_context="amd64:None:hello,python3",
                configuration_context="amd64",
            ),
        )

    def test_configure_task(self) -> None:
        """self.configure_task() does not raise any exception."""
        self.configure_task(self.SAMPLE_TASK_DATA)

    def test_configure_task_defaults(self) -> None:
        """self.configure_task() set default values."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "essential",
            "extra_packages": ["hello", "bye"],
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})

        self.assertTrue(self.task.data.bootstrap_options.use_signed_by)

    def test_analyze_worker(self) -> None:
        """Test the analyze_worker() method."""
        self.mock_is_command_available({"mmdebstrap": True})
        metadata = self.task.analyze_worker()
        self.assertEqual(metadata["mmdebstrap:available"], True)

    def test_analyze_worker_mmdebstrap_not_available(self) -> None:
        """analyze_worker() handles mmdebstrap not being available."""
        self.mock_is_command_available({"mmdebstrap": False})
        metadata = self.task.analyze_worker()
        self.assertEqual(metadata["mmdebstrap:available"], False)

    def test_can_run_on(self) -> None:
        """can_run_on returns True if mmdebstrap is available."""
        self.assertTrue(
            self.task.can_run_on(
                {
                    "system:architectures": ["amd64"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "mmdebstrap:available": True,
                    "mmdebstrap:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_mismatched_task_version(self) -> None:
        """can_run_on returns False for mismatched task versions."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:architectures": ["amd64"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "mmdebstrap:available": True,
                    "mmdebstrap:version": self.task.TASK_VERSION + 1,
                }
            )
        )

    def test_can_run_on_missing_tool(self) -> None:
        """can_run_on returns False if mmdebstrap is not available."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:architectures": ["amd64"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "mmdebstrap:available": False,
                    "mmdebstrap:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_wrong_architecture(self) -> None:
        """can_run_on returns False if the task is for a different arch."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:architectures": ["arm64"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "mmdebstrap:available": True,
                    "mmdebstrap:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_cmdline_add_keyrings(self) -> None:
        """Command line has the keyring files."""
        self.task._host_sources_file = Path("/somewhere/some-file.sources")
        keyring_1 = self.create_temporary_file()
        keyring_2 = self.create_temporary_file()
        self.task._upload_keyrings = [keyring_1, keyring_2]
        self.task._keyrings = [keyring_1]

        cmdline = self.task._cmdline()
        self.assertIn(
            f"--customize-hook=upload {keyring_1} "
            f"/etc/apt/keyrings-debusine/{keyring_1.name}",
            cmdline,
        )
        self.assertIn(
            f"--customize-hook=upload {keyring_2} "
            f"/etc/apt/keyrings-debusine/{keyring_2.name}",
            cmdline,
        )

        self.assertIn(
            f"--keyring={keyring_1}",
            cmdline,
        )

        self.assertNotIn(
            f"--keyring={keyring_2}",
            cmdline,
        )

    def test_cmdline_minimum_options(self) -> None:
        """Command line has minimum options."""
        self.task._host_sources_file = Path("/somewhere/some-file.sources")
        self.task._chroot_sources_file = self.create_temporary_file()

        os_release_file = shlex.quote(self.task._OS_RELEASE_FILE)
        var_lib_dpkg = shlex.quote(self.task._VAR_LIB_DPKG)
        test_sbin_init = shlex.quote(self.task._TEST_SBIN_INIT_RETURN_CODE_FILE)

        expected = [
            "mmdebstrap",
            "--mode=unshare",
            "--format=tar",
            "--architectures=amd64",
            "--verbose",
            "--hook-dir=/usr/share/mmdebstrap/hooks/maybe-jessie-or-older",
            (
                '--customize-hook=cd "$1" && '
                "find etc/apt/sources.list.d -type f -delete"
            ),
            (
                f"--customize-hook=upload {self.task._chroot_sources_file} "
                "/etc/apt/sources.list.d/file.sources"
            ),
            '--customize-hook=mkdir "$1/etc/apt/keyrings-debusine"',
            f"--customize-hook=download /etc/os-release {os_release_file}",
            f"--customize-hook=copy-out /var/lib/dpkg {var_lib_dpkg}",
            '--customize-hook=rm -f "$1/etc/hostname"',
            '--customize-hook='
            'test -h "$1/etc/resolv.conf" || rm -f "$1/etc/resolv.conf"',
            '--customize-hook='
            '(test -x "$1/sbin/init" || test -h "$1/sbin/init") ; '
            'echo $? > "$1/test_sbin_init"',
            f'--customize-hook=download test_sbin_init {test_sbin_init}',
            '--customize-hook=rm "$1/test_sbin_init"',
            "--include=hello,python3",
            "",
            "system.tar.xz",
            "",
            str(self.task._host_sources_file),
        ]

        cmdline = self.task._cmdline()
        self.assertEqual(cmdline, expected)

    def test_cmdline_variant(self) -> None:
        """_cmdline() include --variant=bootstrap_options['variant']."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "essential",
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        self.assertIn("--variant=essential", cmdline)

    def test_cmdline_no_extra_packages(self) -> None:
        """_cmdline() without extra_packages."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "essential",
            "use_signed_by": True,
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        for arg in cmdline:
            self.assertFalse(arg.startswith("--include="))

    def test_cmdline_extra_packages(self) -> None:
        """_cmdline() include --include=extra_packages."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "essential",
            "extra_packages": ["hello", "bye"],
            "use_signed_by": True,
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        self.assertIn("--include=hello,bye", cmdline)

    def test_cmdline_keyring_package(self) -> None:
        """_cmdline() include --include=keyring_0 --include=keyring_1."""
        task_data = copy.deepcopy(self.SAMPLE_TASK_DATA)

        bootstrap_repos = cast(
            list[dict[str, str]], task_data["bootstrap_repositories"]
        )
        bootstrap_repos[0]["keyring_package"] = "keyring_0"
        bootstrap_repos[1]["keyring_package"] = "keyring_1"

        self.configure_task(task_data)
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()

        self.assertIn("--include=keyring_0", cmdline)
        self.assertIn("--include=keyring_1", cmdline)

    def test_cmdline_customization_script(self) -> None:
        """_cmdline() include customization_script arguments."""
        self.task._customization_script = (
            self.create_temporary_directory() / "customization_script"
        )
        cmdline = self.task._cmdline()

        customization_script = self.task._customization_script
        script_name = "customization_script"
        self.assertIn(
            f"--customize-hook=upload {customization_script} /{script_name}",
            cmdline,
        )
        self.assertIn(f'--customize-hook=chmod 555 "$1/{script_name}"', cmdline)
        self.assertIn(f'--customize-hook=chroot "$1" /{script_name}', cmdline)
        self.assertIn(f'--customize-hook=rm "$1/{script_name}"', cmdline)

        self.assertIn("", cmdline)

    def test_fetch_input(self) -> None:
        """Test fetch_input method."""
        # Directory does not need to exist: it is not used
        directory = Path()
        self.assertTrue(self.task.fetch_input(directory))

    def test_upload_artifacts(self) -> None:
        """Test upload_artifacts()."""
        directory = self.create_temporary_directory()

        system_tarball = directory / MmDebstrap._OUTPUT_SYSTEM_FILE
        system_tarball.write_bytes(b"Generated tarball")

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        os_release_data = self.write_os_release(
            directory / self.task._OS_RELEASE_FILE
        )
        (directory / self.task._TEST_SBIN_INIT_RETURN_CODE_FILE).write_text("0")

        packages = self.patch_subprocess_run_pkglist()

        mirror = (
            MmDebstrapData.parse_obj(self.SAMPLE_TASK_DATA)
            .bootstrap_repositories[0]
            .mirror
        )
        bootstrap_options = MmDebstrapBootstrapOptions.parse_obj(
            self.SAMPLE_TASK_DATA["bootstrap_options"]
        )

        self.task.upload_artifacts(directory, execution_success=True)

        calls = []

        expected_system_artifact = DebianSystemTarballArtifact.create(
            system_tarball,
            data={
                "variant": None,
                "architecture": bootstrap_options.architecture,
                "vendor": os_release_data["ID"],
                "codename": os_release_data["VERSION_CODENAME"],
                "pkglist": packages,
                "with_dev": True,
                "with_init": True,
                "mirror": mirror,
            },
        )

        calls.append(
            call(
                expected_system_artifact,
                workspace=self.task.workspace_name,
                work_request=self.task.work_request_id,
            )
        )

        debusine_mock.upload_artifact.assert_has_calls(calls)

    def test_upload_artifacts_jessie(self) -> None:
        """Test upload_artifacts() for a jessie tarball."""
        directory = self.create_temporary_directory()

        system_tarball = directory / MmDebstrap._OUTPUT_SYSTEM_FILE
        system_tarball.write_bytes(b"Generated tarball")

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        os_release_data = self.write_os_release(
            directory / self.task._OS_RELEASE_FILE, codename="jessie", version=8
        )
        (directory / self.task._TEST_SBIN_INIT_RETURN_CODE_FILE).write_text("0")

        packages = self.patch_subprocess_run_pkglist()

        mirror = (
            MmDebstrapData.parse_obj(self.SAMPLE_TASK_DATA)
            .bootstrap_repositories[0]
            .mirror
        )
        bootstrap_options = MmDebstrapBootstrapOptions.parse_obj(
            self.SAMPLE_TASK_DATA["bootstrap_options"]
        )
        self.task.data.bootstrap_repositories[0].suite = "jessie"

        self.task.upload_artifacts(directory, execution_success=True)

        calls = []

        expected_system_artifact = DebianSystemTarballArtifact.create(
            system_tarball,
            data={
                "variant": None,
                "architecture": bootstrap_options.architecture,
                "vendor": os_release_data["ID"],
                "codename": "jessie",
                "pkglist": packages,
                "with_dev": True,
                "with_init": True,
                "mirror": mirror,
            },
        )

        calls.append(
            call(
                expected_system_artifact,
                workspace=self.task.workspace_name,
                work_request=self.task.work_request_id,
            )
        )

        debusine_mock.upload_artifact.assert_has_calls(calls)

    def test_upload_artifacts_sid(self) -> None:
        """Test upload_artifacts() for a sid tarball."""
        directory = self.create_temporary_directory()

        system_tarball = directory / MmDebstrap._OUTPUT_SYSTEM_FILE
        system_tarball.write_bytes(b"Generated tarball")

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        os_release_data = self.write_os_release(
            directory / self.task._OS_RELEASE_FILE,
            codename="trixie",
            version=13,
        )
        (directory / self.task._TEST_SBIN_INIT_RETURN_CODE_FILE).write_text("0")

        packages = self.patch_subprocess_run_pkglist()

        mirror = (
            MmDebstrapData.parse_obj(self.SAMPLE_TASK_DATA)
            .bootstrap_repositories[0]
            .mirror
        )
        bootstrap_options = MmDebstrapBootstrapOptions.parse_obj(
            self.SAMPLE_TASK_DATA["bootstrap_options"]
        )
        self.task.data.bootstrap_repositories[0].suite = "unstable"

        self.task.upload_artifacts(directory, execution_success=True)

        calls = []

        expected_system_artifact = DebianSystemTarballArtifact.create(
            system_tarball,
            data={
                "variant": None,
                "architecture": bootstrap_options.architecture,
                "vendor": os_release_data["ID"],
                "codename": "sid",
                "pkglist": packages,
                "with_dev": True,
                "with_init": True,
                "mirror": mirror,
            },
        )

        calls.append(
            call(
                expected_system_artifact,
                workspace=self.task.workspace_name,
                work_request=self.task.work_request_id,
            )
        )

        debusine_mock.upload_artifact.assert_has_calls(calls)

    def test_upload_artifacts_do_nothing(self) -> None:
        """Test upload_artifacts() doing nothing: execution_success=False."""
        self.mock_debusine()
        self.task.upload_artifacts(Path(), execution_success=False)

    def test_variant_enums(self) -> None:
        """Check MmDebstrap variants enums."""
        systembootstrap_variants = ["buildd", "minbase"]
        mmdebstrap_variants = [
            "-",
            "apt",
            "custom",
            "debootstrap",
            "essential",
            "extract",
            "important",
            "required",
            "standard",
        ]

        for variant in systembootstrap_variants + mmdebstrap_variants:
            with self.subTest(variant=variant):
                self.configure_task(
                    override={
                        "bootstrap_options": {
                            "architecture": "amd64",
                            "variant": variant,
                            "extra_packages": ["hello"],
                            "use_signed_by": True,
                        }
                    }
                )

    def patch_subprocess_run_pkglist(self) -> dict[str, str]:
        """Patch subprocess.run() and return dictionary with name -> version."""
        mock_result = mock.MagicMock()
        packages = {"aardvark-dns:amd64": "1.0.4", "abcde": "1:2.4.3"}

        mock_result.stdout = (
            "\n".join([f"{pkg}\t{ver}" for pkg, ver in packages.items()]) + "\n"
        )

        patch = mock.patch(
            "subprocess.run", autospec=True, return_value=mock_result
        )
        patch.start()
        self.addCleanup(patch.stop)

        return packages

    def test_get_with_init(self) -> None:
        """Test _get_with_init()."""
        file = self.create_temporary_file(contents=b"0\n")
        self.assertTrue(self.task._get_with_init(file))

        file.write_text("1\n")
        self.assertFalse(self.task._get_with_init(file))
