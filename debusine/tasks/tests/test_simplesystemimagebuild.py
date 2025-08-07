# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for SimpleSystemImageBuild class."""
import shlex
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from unittest.mock import call, patch

from debusine.artifacts.local_artifact import DebianSystemImageArtifact
from debusine.tasks import SimpleSystemImageBuild
from debusine.tasks.models import (
    BaseDynamicTaskData,
    DiskImageFormat,
    SystemBootstrapRepositoryCheckSignatureWith,
    SystemBootstrapRepositoryType,
    WorkerType,
)
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase

OVERLAYS = [
    (
        "--customize-hook=upload "
        "/usr/lib/python3/dist-packages/debusine/tasks/data/overlays/"
        "incus-agent/lib/systemd/system/incus-agent.service "
        "/lib/systemd/system/incus-agent.service"
    ),
    '--customize-hook=chmod 644 "$1/lib/systemd/system/incus-agent.service"',
    (
        "--customize-hook=upload "
        "/usr/lib/python3/dist-packages/debusine/tasks/data/overlays/"
        "incus-agent/lib/systemd/incus-agent-setup "
        "/lib/systemd/incus-agent-setup"
    ),
    '--customize-hook=chmod 755 "$1/lib/systemd/incus-agent-setup"',
    (
        "--customize-hook=upload "
        "/usr/lib/python3/dist-packages/debusine/tasks/data/overlays/"
        "incus-agent/lib/udev/rules.d/99-incus-agent.rules "
        "/lib/udev/rules.d/99-incus-agent.rules"
    ),
    '--customize-hook=chmod 644 "$1/lib/udev/rules.d/99-incus-agent.rules"',
    (
        "--customize-hook=upload "
        "/usr/lib/python3/dist-packages/debusine/tasks/data/overlays/"
        "systemd-boot/etc/kernel/postinst.d/zz-update-systemd-boot "
        "/etc/kernel/postinst.d/zz-update-systemd-boot"
    ),
    (
        "--customize-hook=chmod 755 "
        '"$1/etc/kernel/postinst.d/zz-update-systemd-boot"'
    ),
    (
        "--customize-hook=upload "
        "/usr/lib/python3/dist-packages/debusine/tasks/data/overlays/"
        "systemd-boot/etc/kernel/postrm.d/zz-update-systemd-boot "
        "/etc/kernel/postrm.d/zz-update-systemd-boot"
    ),
    (
        "--customize-hook=chmod 755 "
        '"$1/etc/kernel/postrm.d/zz-update-systemd-boot"'
    ),
]


class SimpleSystemImageBuildTests(
    ExternalTaskHelperMixin[SimpleSystemImageBuild], TestCase
):
    """Unit tests for SimpleSystemImageBuild class."""

    SAMPLE_TASK_DATA: dict[str, Any] = {
        "bootstrap_options": {
            "architecture": "amd64",
            "extra_packages": ["hello"],
            "use_signed_by": True,
        },
        "bootstrap_repositories": [
            {
                "mirror": "https://deb.debian.org/debian",
                "suite": "stable",
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
        "disk_image": {
            "format": "raw",
            "partitions": [
                {
                    "size": 2,
                    "filesystem": "ext4",
                },
            ],
        },
    }

    def setUp(self) -> None:
        """Initialize test."""
        self.configure_task()

    def test_configure_task(self) -> None:
        """self.configure_task() does not raise any exception."""
        self.configure_task(self.SAMPLE_TASK_DATA)

    def test_configure_task_defaults(self) -> None:
        """self.configure_task() set default values."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "minbase",
            "extra_packages": ["hello", "bye"],
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})

        self.maxDiff = None
        self.assertEqual(
            self.task.data.dict(exclude_unset=True),
            {
                'bootstrap_options': {
                    'architecture': 'amd64',
                    'extra_packages': ['hello', 'bye'],
                    'variant': "minbase",
                },
                'bootstrap_repositories': [
                    {
                        'check_signature_with': (
                            SystemBootstrapRepositoryCheckSignatureWith.SYSTEM
                        ),
                        'components': ['main', 'contrib'],
                        'mirror': 'https://deb.debian.org/debian',
                        'suite': 'stable',
                    },
                    {
                        'check_signature_with': (
                            SystemBootstrapRepositoryCheckSignatureWith.SYSTEM
                        ),
                        'components': ['main'],
                        'keyring': {
                            'url': 'https://example.com/keyring.gpg',
                        },
                        'mirror': 'https://example.com',
                        'suite': 'bullseye',
                        'types': [SystemBootstrapRepositoryType.DEB_SRC],
                    },
                ],
                'disk_image': {
                    'format': DiskImageFormat.RAW,
                    'partitions': [{'size': 2, 'filesystem': 'ext4'}],
                },
            },
        )

    def test_compute_dynamic_data(self) -> None:
        task_database = FakeTaskDatabase()
        self.assertEqual(
            self.task.compute_dynamic_data(task_database),
            BaseDynamicTaskData(
                subject="stable",
                runtime_context="amd64:None:hello",
                configuration_context="amd64",
            ),
        )

    def test_analyze_worker(self) -> None:
        """Test the analyze_worker() method."""
        self.mock_is_command_available({"mmdebstrap": True})
        metadata = self.task.analyze_worker()
        self.assertEqual(metadata["simplesystemimagebuild:available"], True)

    def test_analyze_worker_mmdebstrap_not_available(self) -> None:
        """analyze_worker() handles mmdebstrap not being available."""
        self.mock_is_command_available({"mmdebstrap": False})
        metadata = self.task.analyze_worker()
        self.assertEqual(metadata["simplesystemimagebuild:available"], False)

    def test_can_run_on(self) -> None:
        """can_run_on returns True if debefivm-create is available."""
        self.assertTrue(
            self.task.can_run_on(
                {
                    "system:architectures": ["amd64"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "simplesystemimagebuild:available": True,
                    "simplesystemimagebuild:version": self.task.TASK_VERSION,
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
                    "simplesystemimagebuild:available": True,
                    "simplesystemimagebuild:version": (
                        self.task.TASK_VERSION + 1
                    ),
                }
            )
        )

    def test_can_run_on_missing_tool(self) -> None:
        """can_run_on returns False if debefivm-create is not available."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:architectures": ["amd64"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "simplesystemimagebuild:available": False,
                    "simplesystemimagebuild:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_can_run_different_architecture(self) -> None:
        """can_run_on returns False if the architecture is different."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:architectures": ["alpha"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "simplesystemimagebuild:available": True,
                    "simplesystemimagebuild:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_can_run_compatible_architecture(self) -> None:
        """can_run_on returns True if the architecture is compatible."""
        bootstrap_options = {"architecture": "i386"}

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.assertTrue(
            self.task.can_run_on(
                {
                    "system:architectures": ["amd64", "i386"],
                    "system:worker_type": WorkerType.EXTERNAL,
                    "simplesystemimagebuild:available": True,
                    "simplesystemimagebuild:version": self.task.TASK_VERSION,
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

    def test_cmdline(self) -> None:
        """Command line has minimum options."""
        expected = [
            "/usr/share/debusine-worker/debefivm-create",
            "--architecture=amd64",
            "--mirror=https://deb.debian.org/debian",
            "--release=stable",
            "--rootsize=2G",
            "system.img",
            "--",
            "--verbose",
            "--hook-dir=/usr/share/mmdebstrap/hooks/maybe-jessie-or-older",
            (
                '--customize-hook=cd "$1" && '
                "find etc/apt/sources.list.d -type f -delete"
            ),
            "--customize-hook=upload None /etc/apt/sources.list.d/file.sources",
            '--customize-hook=mkdir "$1/etc/apt/keyrings-debusine"',
            "--customize-hook=download /etc/os-release os-release",
            "--customize-hook=tar-out /var/lib/dpkg var_lib_dpkg.tar",
        ]
        expected += OVERLAYS
        expected += [
            "--include=hello,linux-image-generic",
            "None",
        ]

        cmdline = self.task._cmdline()
        self.assertEqual(cmdline, expected)

    def test_cmdline_minimum_options(self) -> None:
        """Command line has minimum options."""
        self.task._host_sources_file = Path("/somewhere/some-file.sources")
        self.task._chroot_sources_file = self.create_temporary_file()

        os_release_file = shlex.quote(self.task._OS_RELEASE_FILE)
        var_lib_dpkg = shlex.quote(self.task._VAR_LIB_DPKG)

        expected = [
            '/usr/share/debusine-worker/debefivm-create',
            '--architecture=amd64',
            '--mirror=https://deb.debian.org/debian',
            '--release=stable',
            '--rootsize=2G',
            'system.img',
            '--',
            '--verbose',
            '--hook-dir=/usr/share/mmdebstrap/hooks/maybe-jessie-or-older',
            '--customize-hook=cd "$1" && '
            "find etc/apt/sources.list.d -type f -delete",
            (
                f"--customize-hook=upload {self.task._chroot_sources_file} "
                "/etc/apt/sources.list.d/file.sources"
            ),
            '--customize-hook=mkdir "$1/etc/apt/keyrings-debusine"',
            f"--customize-hook=download /etc/os-release {os_release_file}",
            f"--customize-hook=tar-out /var/lib/dpkg {var_lib_dpkg}.tar",
        ]
        expected += OVERLAYS
        expected += [
            '--include=hello,linux-image-generic',
            str(self.task._host_sources_file),
        ]

        cmdline = self.task._cmdline()
        self.assertEqual(cmdline, expected)

    def test_cmdline_variant(self) -> None:
        """_cmdline() include --variant=bootstrap_options['variant']."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "minbase",
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        self.assertIn("--variant=minbase", cmdline)

    def test_cmdline_no_extra_packages(self) -> None:
        """_cmdline() without extra_packages."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "minbase",
            "use_signed_by": True,
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        self.assertIn("--include=linux-image-generic", cmdline)

    def test_cmdline_extra_packages(self) -> None:
        """_cmdline() include --include=extra_packages."""
        bootstrap_options = {
            "architecture": "amd64",
            "variant": "minbase",
            "extra_packages": ["hello", "bye"],
            "use_signed_by": True,
        }

        self.configure_task(override={"bootstrap_options": bootstrap_options})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        self.assertIn("--include=hello,bye,linux-image-generic", cmdline)

    def test_cmdline_kernel_package(self) -> None:
        """_cmdline() include --include=kernel_package."""
        disk_image = {
            'format': DiskImageFormat.RAW,
            'bootloader': None,
            'filename': 'image',
            "kernel_package": "linux-image-amd64",
            'partitions': [
                {'size': 2, 'filesystem': 'ext4', 'mountpoint': 'none'}
            ],
        }

        self.configure_task(override={"disk_image": disk_image})
        self.task._host_sources_file = Path("some-file.sources")
        cmdline = self.task._cmdline()
        self.assertIn("--include=hello,linux-image-amd64", cmdline)

    def test_cmdline_keyring_package(self) -> None:
        """_cmdline() include --include=keyring_0 --include=keyring_1."""
        task_data = deepcopy(self.SAMPLE_TASK_DATA)

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

    def test_cmdline_customization_script_autopkgtest(self) -> None:
        """_cmdline() include autopkgtest customization_script arguments."""
        customization_script = Path(
            "/usr/share/autopkgtest/setup-commands/setup-testbed"
        )
        self.task._customization_script = customization_script
        cmdline = self.task._cmdline()
        self.assertIn(f"--customize-hook={customization_script}", cmdline)

    def test_fetch_input(self) -> None:
        """Test fetch_input method."""
        # Directory does not need to exist: it is not used
        directory = Path()
        self.assertTrue(self.task.fetch_input(directory))

    def test_upload_artifacts(self) -> None:
        """Test upload_artifacts()."""
        directory = self.create_temporary_directory()

        self.write_os_release(directory / self.task._OS_RELEASE_FILE)

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        system_image = Path()

        def mock_tar(cmd: list[str]) -> int:
            # var_lib_dpkg.tar
            if cmd[-2] == "-xf":
                return 0
            nonlocal system_image
            system_image = Path(cmd[-2])
            system_image.write_bytes(b"Generated image")
            return 0

        with patch('subprocess.check_call', mock_tar):
            self.task.upload_artifacts(directory, execution_success=True)

        calls = []

        bootstrap_options = cast(
            dict[str, Any], self.SAMPLE_TASK_DATA["bootstrap_options"]
        )
        bootstrap_repositories = cast(
            list[dict[str, Any]],
            self.SAMPLE_TASK_DATA["bootstrap_repositories"],
        )

        expected_system_artifact = DebianSystemImageArtifact.create(
            system_image,
            data={
                "variant": None,
                "architecture": bootstrap_options["architecture"],
                "vendor": "debian",
                "codename": "bookworm",
                "pkglist": {},
                "with_dev": True,
                "with_init": True,
                "mirror": bootstrap_repositories[0]["mirror"],
                "image_format": "raw",
                "filesystem": "ext4",
                "size": 2e9,
                "boot_mechanism": "efi",
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

    def test_upload_artifacts_unstable(self) -> None:
        """Test upload_artifacts on unstable()."""
        directory = self.create_temporary_directory()

        self.write_os_release(directory / self.task._OS_RELEASE_FILE)

        system_image = Path()

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        self.task.data.bootstrap_repositories[0].suite = "unstable"
        self.task.data.disk_image.format = DiskImageFormat.QCOW2

        def mock_qemu_img(cmd: list[str]) -> int:
            # var_lib_dpkg.tar
            if cmd[-2] == "-xf":
                return 0
            nonlocal system_image
            system_image = Path(cmd[-1])
            system_image.write_bytes(b"Generated image")
            return 0

        with patch('subprocess.check_call', mock_qemu_img):
            self.task.upload_artifacts(directory, execution_success=True)

        calls = []

        bootstrap_options = cast(
            dict[str, Any], self.SAMPLE_TASK_DATA["bootstrap_options"]
        )
        bootstrap_repositories = cast(
            list[dict[str, Any]],
            self.SAMPLE_TASK_DATA["bootstrap_repositories"],
        )

        expected_system_artifact = DebianSystemImageArtifact.create(
            system_image,
            data={
                "variant": None,
                "architecture": bootstrap_options["architecture"],
                "vendor": "debian",
                "codename": "sid",
                "pkglist": {},
                "with_dev": True,
                "with_init": True,
                "mirror": bootstrap_repositories[0]["mirror"],
                "image_format": "qcow2",
                "filesystem": "ext4",
                "size": 2e9,
                "boot_mechanism": "efi",
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

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "bootstrap a system image")
