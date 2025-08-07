# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Task to test Debian binary packages using piuparts.

This task follows the specification laid out here:
https://freexian-team.pages.debian.net/debusine/reference/tasks.html#piuparts-task
"""

import gzip
import os
import shlex
import shutil
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Any

import debusine.utils
from debusine.artifacts import BinaryPackages
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSystemTarball,
    DebianUpload,
    get_source_package_name,
)
from debusine.tasks import (
    BaseTaskWithExecutor,
    ExtraRepositoryMixin,
    RunCommandTask,
)
from debusine.tasks.executors import ExecutorImageCategory
from debusine.tasks.models import PiupartsData, PiupartsDynamicData
from debusine.tasks.server import TaskDatabaseInterface


class Piuparts(
    ExtraRepositoryMixin[PiupartsData, PiupartsDynamicData],
    RunCommandTask[PiupartsData, PiupartsDynamicData],
    BaseTaskWithExecutor[PiupartsData, PiupartsDynamicData],
):
    """Test Debian binary packages using piuparts."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize (constructor)."""
        super().__init__(task_data, dynamic_task_data)
        # *.deb paths. Set by self.configure_for_execution()
        self._deb_files: list[Path] = []

        # --basetgz=... option for piuparts cmdline.
        # Set by self.configure_for_execution()
        self._base_tar: Path | None = None
        self._base_tar_data: DebianSystemTarball | None = None
        self._scripts_dir: Path | None = None

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> PiupartsDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: space-separated list of source package names from
          ``input.binary_artifact``
        :runtime_context: ``codename:host_architecture`` where ``codename``
          is from ``base_tgz``
        :configuration_context: ``codename`` from ``base_tgz``
        """
        input_binary_artifacts = task_database.lookup_multiple_artifacts(
            self.data.input.binary_artifacts
        )
        base_tgz = self.get_environment(
            task_database,
            self.data.base_tgz,
            default_category=CollectionCategory.ENVIRONMENTS,
            image_category=ExecutorImageCategory.TARBALL,
            set_backend=False,
        )

        self.ensure_artifact_categories(
            configuration_key="base_tgz",
            category=base_tgz.category,
            expected=(ArtifactCategory.SYSTEM_TARBALL,),
        )
        assert isinstance(base_tgz.data, DebianSystemTarball)

        package_names = []
        for binary_artifact in input_binary_artifacts:
            self.ensure_artifact_categories(
                configuration_key="input.binary_artifact",
                category=binary_artifact.category,
                expected=(
                    ArtifactCategory.BINARY_PACKAGES,
                    ArtifactCategory.UPLOAD,
                ),
            )
            assert isinstance(
                binary_artifact.data, (BinaryPackages, DebianUpload)
            )
            package_names.append(get_source_package_name(binary_artifact.data))

        runtime_context = f"{base_tgz.data.codename}:{self.host_architecture()}"
        configuration_context = base_tgz.data.codename

        return PiupartsDynamicData(
            environment_id=self.get_environment(
                task_database,
                self.data.environment,
                default_category=CollectionCategory.ENVIRONMENTS,
            ).id,
            input_binary_artifacts_ids=input_binary_artifacts.get_ids(),
            base_tgz_id=base_tgz.id,
            subject=" ".join(sorted(package_names)),
            runtime_context=runtime_context,
            configuration_context=configuration_context,
        )

    def fetch_input(self, destination: Path) -> bool:
        """Populate work directory with user-specified binary artifact(s)."""
        assert self.dynamic_data

        for artifact_id in self.dynamic_data.input_binary_artifacts_ids:
            self.fetch_artifact(artifact_id, destination)
        return True

    def configure_for_execution(self, download_directory: Path) -> bool:
        r"""
        Find the .deb files for piuparts.

        Prepare executor, install "piuparts" in it and prepare
        debian:system-image (e.g. delete /dev/\* files).

        Set self._deb_files to the relevant files.

        :param download_directory: where to find the \*.deb files (downloaded
           via fetch_input) and where to download the chroot of
           debian:system-image (for piuparts --basetgz).
        :return: True if valid files were found
        """
        # Find the files to test or early exit if no files
        self._deb_files = debusine.utils.find_files_suffixes(
            download_directory, [".deb"]
        )
        # Ensure we've got >=1 .deb file(s)
        # Note: This could be moved into a more generic function shared across
        # tasks.
        if len(self._deb_files) == 0:
            list_of_files = sorted(map(str, download_directory.iterdir()))
            self.append_to_log_file(
                "configure_for_execution.log",
                [
                    f"There must be at least one *.deb file. "
                    f"Current files: {list_of_files}"
                ],
            )
            return False

        # Prepare executor
        self._prepare_executor_instance()

        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")

        # Prepare executor_instance to run piuparts
        self.run_executor_command(
            ["apt-get", "update"],
            log_filename="install.log",
            run_as_root=True,
            check=True,
        )

        # mmdebstrap to use mmtarfilter
        self.run_executor_command(
            ["apt-get", "--yes", "install", "piuparts", "mmdebstrap"],
            log_filename="install.log",
            run_as_root=True,
            check=True,
        )

        self._prepare_base_tgz(download_directory)
        self._scripts_dir = download_directory / "scripts"
        self._scripts_dir.mkdir()
        self._prepare_scripts(self._scripts_dir)

        return True

    def _prepare_base_tgz(self, download_directory: Path) -> None:
        """
        Download the base image for piuparts. Remove /dev/* if needed.

        Set self._base_tar to the file that is used by _cmdline.
        """
        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")
        assert self.dynamic_data

        (base_tar_dir := download_directory / "base_tar").mkdir()

        base_tgz_response = self.fetch_artifact(
            self.dynamic_data.base_tgz_id, base_tar_dir
        )
        self._base_tar_data = DebianSystemTarball.parse_obj(
            base_tgz_response.data
        )

        self._base_tar = base_tar_dir / base_tgz_response.data["filename"]
        assert self._base_tar  # For mypy, otherwise fails below with:
        #     error: Item "None" of "Path | None" has no \
        #     attribute "stem" [union-attr]

        # Is the base_tgz set up for systemd-resolved?
        resolvconf_symlink = False
        with tarfile.open(self._base_tar) as tar:
            for name in (
                "etc/resolv.conf",
                "/etc/resolv.conf",
                "./etc/resolv.conf",
            ):
                try:
                    if tar.getmember(name).issym():  # pragma: no cover
                        resolvconf_symlink = True
                        break
                except KeyError:
                    continue

        if not base_tgz_response.data["with_dev"] and not resolvconf_symlink:
            # No processing needs to be done, the image can stay as
            # .tar.xz (or the format retrieved)
            return

        # /dev/* and/or /etc/resolv.conf will be removed from the image.
        # Will create a .tar.gz file, and point self._base_tar to the new file.

        processed_tar_name = self._base_tar.stem.rsplit(".", 1)[0] + ".tar"
        processed_tar = self._base_tar.with_name(processed_tar_name)

        cmd = ["mmtarfilter"]
        if base_tgz_response.data["with_dev"]:
            cmd.append("--path-exclude=/dev/*")
        if resolvconf_symlink:
            cmd.append("--path-exclude=/etc/resolv.conf")

        with (
            self._base_tar.open("rb") as input_image,
            processed_tar.open("wb") as output_image,
        ):
            self.executor_instance.run(
                cmd, stdin=input_image, stdout=output_image
            )

        if resolvconf_symlink:
            with tarfile.open(processed_tar, "a") as tar:
                tarinfo = tarfile.TarInfo(name="./etc/resolv.conf")
                tarinfo.mode = 0o644
                tarinfo.size = 0
                tar.addfile(tarinfo, BytesIO(b""))

        # Compress the processed tarball for compatibility with piuparts < 1.3
        processed_tar_gz = processed_tar.with_suffix(".tar.gz")
        with (
            open(processed_tar, "rb") as uncompressed,
            gzip.open(processed_tar_gz, "wb") as compressed,
        ):
            shutil.copyfileobj(uncompressed, compressed)

        # Delete unused images
        self._base_tar.unlink()
        processed_tar.unlink()

        # Use the processed image
        self._base_tar = processed_tar_gz

    def _prepare_scripts(self, script_directory: Path) -> None:
        """Prepare scripts for piuparts."""
        if not self.data.extra_repositories:
            return
        if not self._base_tar_data:
            raise AssertionError(
                "self._base_tar_data not set, call _prepare_base_tgz first."
            )
        codename = self._base_tar_data.codename
        supports_deb822_sources = self.supports_deb822_sources(codename)
        inline_signed_by = self.supports_inline_signed_by(codename)
        post_unpack_script = script_directory / "post_chroot_unpack_debusine"
        with post_unpack_script.open("w") as f:
            f.write("#!/bin/sh\nset -eux\n")
            if not inline_signed_by:
                f.write("mkdir -p /etc/apt/keyrings\n")
            for i, repo in enumerate(self.data.extra_repositories):
                signed_by = None
                if not inline_signed_by and repo.signing_key:
                    signed_by = f"/etc/apt/keyrings/extra_apt_key_{i}.asc"
                    signing_key = shlex.quote(repo.signing_key + "\n")
                    f.write(f"printf %s {signing_key} > {signed_by}\n")

                if supports_deb822_sources:
                    source = repo.as_deb822_source(signed_by_filename=signed_by)
                    source_file = (
                        f"/etc/apt/sources.list.d/extra_repository_{i}.sources"
                    )
                else:
                    source = (
                        repo.as_oneline_source(signed_by_filename=signed_by)
                        + "\n"
                    )
                    source_file = (
                        f"/etc/apt/sources.list.d/extra_repository_{i}.list"
                    )
                f.write(f"printf %s {shlex.quote(source)} > {source_file}\n")

            f.write("echo DONE\n")
        post_unpack_script.chmod(0o755)

    def _cmdline(self) -> list[str]:
        """Build full piuparts command line."""
        cmdline = []

        path_env = os.environ["PATH"].split(":")
        if "/usr/sbin" not in path_env:
            path_env.insert(0, "/usr/sbin")

        cmdline.extend(["env", f"PATH={':'.join(path_env)}", "piuparts"])

        # Use --keep-sources-list (not --distribution=dist-variant /
        # --mirror / --extra-repo): we feed pre-generated tarballs and
        # we don't want piuparts to overwrite the APT configuration.
        # Also don't set --distribution/-d: we don't want to
        # work-around dated distros.conf aliasing (stable/testing/...)
        # and variants (-backports,-updates,/updates,-security/...).
        # Only use -d in the future if we need to enable --scriptsdir
        # and need to set $PIUPARTS_DISTRIBUTION.
        cmdline.append("--keep-sources-list")

        # Common options used in Debian piuparts services/CI
        cmdline.append("--allow-database")
        cmdline.append("--warn-on-leftovers-after-purge")

        # Use pre-generated tarballs (don't debootstrap on the fly)
        cmdline.append(f"--basetgz={self._base_tar}")

        # Configure APT sources, etc.
        cmdline.append(f"--scriptsdir={self._scripts_dir}")

        # Test the binary .deb files
        cmdline.extend(map(str, self._deb_files))

        return cmdline

    @staticmethod
    def _cmdline_as_root() -> bool:
        """Piuparts must run as root."""
        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """cmd-output.log is enough for now, upload nothing."""

    def get_label(self) -> str:
        """Return the task label."""
        return (
            f"piuparts {subject}"
            if (subject := self.get_subject())
            else "piuparts"
        )
