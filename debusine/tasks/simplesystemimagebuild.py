# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""SimpleSystemImageBuild Task, extending the SystemImageBuild ontology."""
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from debusine import utils
from debusine.artifacts.local_artifact import DebianSystemImageArtifact
from debusine.tasks.models import DiskImageFormat, SystemImageBuildData
from debusine.tasks.systembootstrap import SystemBootstrap


class SimpleSystemImageBuild(SystemBootstrap[SystemImageBuildData]):
    """Implement SimpleSystemImageBuild using debefivm-create."""

    _OUTPUT_SYSTEM_FILE = "system.img"
    _VAR_LIB_DPKG = "var_lib_dpkg"

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize SimpleSystemImageBuild."""
        super().__init__(task_data, dynamic_task_data)

    @classmethod
    def analyze_worker(cls) -> dict[str, Any]:
        """Report metadata for this task on this worker."""
        metadata = super().analyze_worker()

        available_key = cls.prefix_with_task_name("available")
        metadata[available_key] = utils.is_command_available("mmdebstrap")

        return metadata

    def host_architecture(self) -> str:
        """Return architecture."""
        return self.data.bootstrap_options.architecture

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """Check if the specified worker can run the task."""
        if not super().can_run_on(worker_metadata):
            return False

        available_key = self.prefix_with_task_name("available")
        if not worker_metadata.get(available_key, False):
            return False

        return self.host_architecture() in worker_metadata.get(
            "system:architectures", []
        )

    def get_label(self) -> str:
        """Return the task label."""
        return "bootstrap a system image"

    def _cmdline(self) -> list[str]:
        """
        Return debefivm-create command line.

        Use configuration of self.data.
        """
        chroot_source = self._chroot_sources_file
        cmd = [
            "debefivm-create",
            f"--architecture={self.data.bootstrap_options.architecture}",
            f"--release={self.data.bootstrap_repositories[0].suite}",
            f"--rootsize={self.data.disk_image.partitions[0].size}G",
            f"--output={self._OUTPUT_SYSTEM_FILE}",  # output file
            "--",
            f"{self.data.bootstrap_repositories[0].mirror}",
            "--verbose",
            # Set "find"'s cwd to "$1". Otherwise, find cwd is the
            # execute_directory as created by RunCommandTask._execute()
            # which is not readable by the mmdebstrap's subuid (find runs
            # under mmdebstrap's subuid already in --mode=unshare). In this
            # case find fails with
            # "Failed to restore initial working directory"
            (
                '--customize-hook=cd "$1" && '
                "find etc/apt/sources.list.d -type f -delete"
            ),
            (
                f"--customize-hook=upload {chroot_source} "
                "/etc/apt/sources.list.d/file.sources"
            ),
        ]

        # Begin deal with the keyrings
        # Upload keyrings needed in the chroot
        keyrings_dir = "/etc/apt/keyrings-debusine"
        cmd.append(f'--customize-hook=mkdir "$1{keyrings_dir}"')

        for keyring in self._upload_keyrings:
            cmd.append(
                f"--customize-hook=upload {keyring} "
                f"{keyrings_dir}/{keyring.name}"
            )

        # Add --keyring for each keyring downloaded by
        for keyring in self._keyrings:
            cmd.append(f"--keyring={keyring}")

        for repository in self.data.bootstrap_repositories:
            if package := repository.keyring_package:
                cmd.append(f"--include={package}")
        # End deal with the keyrings

        # customization_script
        if script := self._customization_script:
            # special case autopkgtest script
            # as it needs to run outside of the chroot
            if script == Path(
                "/usr/share/autopkgtest/setup-commands/setup-testbed"
            ):
                cmd.append(f"--customize-hook={script}")
            else:
                script_name = script.name
                cmd.extend(
                    [
                        f"--customize-hook=upload {script} /{script_name}",
                        f'--customize-hook=chmod 555 "$1/{script_name}"',
                        f'--customize-hook=chroot "$1" /{script_name}',
                        f'--customize-hook=rm "$1/{script_name}"',
                    ]
                )

        # Used by `upload_artifacts`
        cmd.extend(
            [
                (
                    "--customize-hook=download /etc/os-release "
                    f"{shlex.quote(self._OS_RELEASE_FILE)}"
                ),
                (
                    "--customize-hook=tar-out /var/lib/dpkg "
                    f"{shlex.quote(self._VAR_LIB_DPKG)}.tar"
                ),
            ]
        )

        overlay_dir = (
            "/usr/lib/python3/dist-packages/debusine/tasks/data/overlays"
        )
        for overlay, path, mode in (
            ("incus-agent", "lib/systemd/system/incus-agent.service", 0o644),
            ("incus-agent", "lib/systemd/incus-agent-setup", 0o755),
            ("incus-agent", "lib/udev/rules.d/99-incus-agent.rules", 0o644),
            (
                "systemd-boot",
                "etc/kernel/postinst.d/zz-update-systemd-boot",
                0o755,
            ),
            (
                "systemd-boot",
                "etc/kernel/postrm.d/zz-update-systemd-boot",
                0o755,
            ),
        ):
            cmd.extend(
                [
                    (
                        f"--customize-hook=upload "
                        f"{overlay_dir}/{overlay}/{path} /{path}"
                    ),
                    f'--customize-hook=chmod {mode:o} "$1/{path}"',
                ]
            )

        if variant := self.data.bootstrap_options.variant:
            cmd.append(f"--variant={variant}")

        extra_packages = self.data.bootstrap_options.extra_packages

        kernel = self.data.disk_image.kernel_package
        if kernel is None:
            # Only introduced in bullseye
            # We require the generic kernel for 9p support (#1027174)
            kernel = "linux-image-generic"
        extra_packages.append(kernel)

        assert extra_packages
        cmd.append("--include=" + ",".join(extra_packages))

        cmd.append(str(self._host_sources_file))

        return cmd

    def fetch_input(self, destination: Path) -> bool:  # noqa: U100
        """Do nothing: no artifacts need to be downloaded."""
        return True

    def upload_artifacts(
        self, execute_dir: Path, *, execution_success: bool
    ) -> None:
        """Upload generated artifacts."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        if not execution_success:
            return

        if self.data.disk_image.format == DiskImageFormat.QCOW2:
            system_file = execute_dir / f"{self.data.disk_image.filename}.qcow2"
            subprocess.check_call(
                [
                    "qemu-img",
                    "convert",
                    "-O",
                    "qcow2",
                    execute_dir / self._OUTPUT_SYSTEM_FILE,
                    system_file,
                ]
            )
        else:
            system_file = (
                execute_dir / f"{self.data.disk_image.filename}.tar.xz"
            )
            subprocess.check_call(
                [
                    "tar",
                    "--create",
                    "--auto-compress",
                    "--file",
                    system_file,
                    execute_dir / self._OUTPUT_SYSTEM_FILE,
                ]
            )

        bootstrap_options = self.data.bootstrap_options
        vendor = self._get_value_os_release(
            execute_dir / self._OS_RELEASE_FILE, "ID"
        )

        main_bootstrap_repository = self.data.bootstrap_repositories[0]
        codename = self._get_value_os_release(
            execute_dir / self._OS_RELEASE_FILE, "VERSION_CODENAME"
        )

        # /etc/os-release reports the testing release for unstable (#341)
        if main_bootstrap_repository.suite in ("unstable", "sid"):
            codename = "sid"

        components: list[str]
        if self.data.bootstrap_repositories[0].components:
            components = self.data.bootstrap_repositories[0].components
        elif self._chroot_sources_file:
            components = self._get_source_components(self._chroot_sources_file)
        else:
            raise AssertionError("self._chroot_sources_file not set")

        os.mkdir(execute_dir / self._VAR_LIB_DPKG)
        subprocess.check_call(
            [
                "tar",
                "-C",
                execute_dir / self._VAR_LIB_DPKG,
                "-xf",
                execute_dir / f"{self._VAR_LIB_DPKG}.tar",
            ],
        )
        pkglist = self._get_pkglist(execute_dir / self._VAR_LIB_DPKG)

        artifact = DebianSystemImageArtifact.create(
            system_file,
            data={
                "variant": bootstrap_options.variant,
                "architecture": bootstrap_options.architecture,
                "vendor": vendor,
                "codename": codename,
                "components": components,
                "pkglist": pkglist,
                # with_dev: with the current setup (in mmdebstrap),
                # the devices in /dev are always created
                "with_dev": True,
                "with_init": True,
                # TODO / XXX: is "mirror" meant to be the first repository?
                # or "mirrors" and list all of them?
                # Or we could duplicate all the bootstrap_repositories...
                "mirror": main_bootstrap_repository.mirror,
                "image_format": self.data.disk_image.format,
                "filesystem": self.data.disk_image.partitions[0].filesystem,
                "size": (self.data.disk_image.partitions[0].size * 10**9),
                "boot_mechanism": "efi",
            },
        )

        self.debusine.upload_artifact(
            artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )
