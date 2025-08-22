# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""MMDebstrap task, extending the SystemBootstrap ontology."""
import shlex
from pathlib import Path
from typing import Any

from debusine import utils
from debusine.artifacts.local_artifact import DebianSystemTarballArtifact
from debusine.tasks.models import MmDebstrapData
from debusine.tasks.systembootstrap import SystemBootstrap


class MmDebstrap(SystemBootstrap[MmDebstrapData]):
    """Implement MmDebstrap: extends the ontology SystemBootstrap."""

    _OUTPUT_SYSTEM_FILE = "system.tar.xz"
    _VAR_LIB_DPKG = "var_lib_dpkg"
    _TEST_SBIN_INIT_RETURN_CODE_FILE = "test-sbin-init"

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

    def _cmdline(self) -> list[str]:
        """
        Return mmdebstrap command line.

        Use configuration of self.data.
        """
        chroot_source = self._chroot_sources_file
        cmd = [
            "mmdebstrap",
            "--mode=unshare",
            "--format=tar",
            f"--architectures={self.data.bootstrap_options.architecture}",
            "--verbose",
            "--hook-dir=/usr/share/mmdebstrap/hooks/maybe-jessie-or-older",
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

        # Used by `upload_artifacts`
        cmd.extend(
            [
                "--customize-hook=download /etc/os-release "
                f"{shlex.quote(self._OS_RELEASE_FILE)}",
                "--customize-hook=copy-out /var/lib/dpkg "
                f"{shlex.quote(self._VAR_LIB_DPKG)}",
            ]
        )

        # Remove network-related files that mmdebstrap copies from the host
        # (/etc/resolv.conf symlinks are OK, since those don't break
        # reproducibility)
        cmd.append('--customize-hook=rm -f "$1/etc/hostname"')
        cmd.append(
            '--customize-hook='
            'test -h "$1/etc/resolv.conf" || rm -f "$1/etc/resolv.conf"'
        )

        # customization_script
        if script := self._customization_script:
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
        test_sbin_init_chroot = "test_sbin_init"
        cmd.extend(
            [
                '--customize-hook='
                '(test -x "$1/sbin/init" || test -h "$1/sbin/init") ; '
                f'echo $? > "$1/{test_sbin_init_chroot}"',
                f'--customize-hook=download test_sbin_init '
                f'{shlex.quote(self._TEST_SBIN_INIT_RETURN_CODE_FILE)}',
                f'--customize-hook=rm "$1/{test_sbin_init_chroot}"',
            ]
        )

        if variant := self.data.bootstrap_options.variant:
            cmd.append(f"--variant={variant}")

        if extra_packages := self.data.bootstrap_options.extra_packages:
            cmd.append("--include=" + ",".join(extra_packages))

        cmd.extend(
            [
                "",  # suite (defaults the one in sources_file.name
                self._OUTPUT_SYSTEM_FILE,  # output file
                "",  # mirror: defaults to the one in sources_file.name
                str(self._host_sources_file),  # sources file
            ]
        )

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
        system_file = execute_dir / self._OUTPUT_SYSTEM_FILE

        bootstrap_options = self.data.bootstrap_options
        vendor = self._get_value_os_release(
            execute_dir / self._OS_RELEASE_FILE, "ID"
        )

        main_bootstrap_repository = self.data.bootstrap_repositories[0]
        try:
            codename = self._get_value_os_release(
                execute_dir / self._OS_RELEASE_FILE, "VERSION_CODENAME"
            )
        except KeyError:
            # jessie doesn't provide VERSION_CODENAME
            codename = main_bootstrap_repository.suite

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

        pkglist = self._get_pkglist(execute_dir / self._VAR_LIB_DPKG)
        with_init = self._get_with_init(
            execute_dir / self._TEST_SBIN_INIT_RETURN_CODE_FILE
        )

        artifact = DebianSystemTarballArtifact.create(
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
                "with_init": with_init,
                # TODO / XXX: is "mirror" meant to be the first repository?
                # or "mirrors" and list all of them?
                # Or we could duplicate all the bootstrap_repositories...
                "mirror": main_bootstrap_repository.mirror,
            },
        )

        self.debusine.upload_artifact(
            artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

    @staticmethod
    def _get_with_init(test_init_return_code_file: Path) -> bool:
        return test_init_return_code_file.read_text().rstrip() == "0"
