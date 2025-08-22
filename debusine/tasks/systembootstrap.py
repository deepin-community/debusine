# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Ontology definition of SystemBootstrap."""
import abc
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Generic, TypeVar, cast
from urllib.parse import urljoin

import requests
from debian.deb822 import Deb822, Release

from debusine.client.client_utils import get_url_contents_sha256sum
from debusine.client.exceptions import ContentValidationError
from debusine.tasks import DefaultDynamicData, RunCommandTask
from debusine.tasks.models import (
    BaseDynamicTaskData,
    SystemBootstrapData,
    SystemBootstrapRepository,
    SystemBootstrapRepositoryCheckSignatureWith,
    SystemBootstrapRepositoryKeyring,
)
from debusine.tasks.server import TaskDatabaseInterface

SBD = TypeVar("SBD", bound=SystemBootstrapData)


class SystemBootstrap(
    abc.ABC,
    RunCommandTask[SBD, BaseDynamicTaskData],
    DefaultDynamicData[SBD],
    Generic[SBD],
):
    """Implement ontology SystemBootstrap."""

    TASK_VERSION = 1

    _OS_RELEASE_FILE = "os-release"

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize SystemBootstrap."""
        super().__init__(task_data, dynamic_task_data)
        # keyrings that will be uploaded into the chroot
        self._upload_keyrings: list[Path] = []

        # all the keyrings that have been downloaded
        self._keyrings: list[Path] = []

        self._host_sources_file: Path | None = None
        self._chroot_sources_file: Path | None = None

        # customization_script file
        self._customization_script: Path | None = None

    @staticmethod
    def _download_key(
        repository: SystemBootstrapRepository,
        *,
        keyring_directory: Path,
    ) -> str:
        # Using cast because repository's validator ensures keyring is set
        # when using external repositories
        repo_keyring = cast(
            SystemBootstrapRepositoryKeyring, repository.keyring
        )
        keyring, actual_sha256sum = get_url_contents_sha256sum(
            repo_keyring.url, 100 * 1024 * 1024, allow_file=True
        )

        if expected := repo_keyring.sha256sum:
            if actual_sha256sum != expected:
                raise ContentValidationError(
                    f"sha256 mismatch for keyring repository "
                    f"{repository.mirror}. "
                    f"Actual: {actual_sha256sum} expected: {expected}"
                )

        # Disable auto-deletion: the file will be deleted together
        # with keyring_directory when the RunCommandTask finishes
        file = tempfile.NamedTemporaryFile(
            dir=keyring_directory,
            prefix="keyring-repo-",
            # Detect ASCII armoring:
            # https://www.rfc-editor.org/rfc/rfc4880#section-6.2
            suffix=".asc" if keyring.startswith(b"-----BEGIN") else ".gpg",
            delete=False,
        )
        Path(file.name).write_bytes(keyring)
        return file.name

    @classmethod
    def _deb822_source(
        cls,
        repository: SystemBootstrapRepository,
        *,
        keyring_directory: Path,
        use_signed_by: bool,
    ) -> Deb822:
        """
        Create a deb822 from repository and return it.

        :raise: ContentValidationError if the repository["keyring"]["sha256sum"]
          does not match the one from the downloaded keyring
        :param keyring_directory: directory to save the gpg keys
        :param use_signed_by: add Signed-By in the repository
        :return: repository
        """
        deb822 = Deb822()

        deb822["Types"] = " ".join(repository.types)
        deb822["URIs"] = repository.mirror
        deb822["Suites"] = repository.suite

        if (components := repository.components) is None:
            components = cls._list_components_for_suite(
                repository.mirror, repository.suite
            )

        deb822["Components"] = " ".join(components)

        if repository.check_signature_with == "no-check":
            deb822["Trusted"] = "yes"

        if (
            repository.check_signature_with
            == SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL
        ):
            key_file = SystemBootstrap._download_key(
                repository, keyring_directory=keyring_directory
            )
            if use_signed_by:
                deb822["Signed-By"] = key_file

        return deb822

    @classmethod
    def _write_deb822s(cls, deb822s: list[Deb822], destination: Path) -> None:
        with destination.open("wb") as f:
            for deb822 in deb822s:
                deb822.dump(f)
                f.write(b"\n")

    def _generate_deb822_sources(
        self,
        repositories: list[SystemBootstrapRepository],
        *,
        keyrings_dir: Path,
        use_signed_by: bool,
    ) -> list[Deb822]:
        """
        Return list of Deb822 repositories.

        :param keyrings_dir: write gpg keys into it.
        :use_signed_by: if True, add Signed-By to the repository with the path
          to the file.
        """
        deb822s = []
        for repository in repositories:
            deb822s.append(
                self._deb822_source(
                    repository,
                    keyring_directory=keyrings_dir,
                    use_signed_by=use_signed_by,
                )
            )

        return deb822s

    @classmethod
    def _list_components_for_suite(
        cls, mirror_url: str, suite: str
    ) -> list[str]:
        """
        Return components listed in the Release file.

        :raises ValueError: if components cannot be found or invalid names.
        """
        release_url = urljoin(mirror_url + "/", f"dists/{suite}/Release")
        try:
            components = Release(requests.get(release_url).iter_lines())[
                "components"
            ].split()
        except KeyError:
            raise ValueError(f"Cannot find components in {release_url}")

        assert isinstance(components, list)
        for component in components:
            if re.search("^[A-Za-z][-/A-Za-z]*$", component) is None:
                raise ValueError(
                    f'Invalid component name from {release_url}: '
                    f'"{component}" must start with [A-Za-z] and have only '
                    f'[-/A-Za-z] characters'
                )

        return components

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """
        Compute dynamic data.

        :subject: suite name in the first ``bootstrap_repositories``
        :runtime_context: ``$architecture:$variant:$extra_packages`` of
          ``bootstrap_options``
        :configuration_context: ``architecture`` of ``bootstrap_options``
        """
        suite_name = self.data.bootstrap_repositories[0].suite

        architecture = self.data.bootstrap_options.architecture
        variant = self.data.bootstrap_options.variant
        extra_packages = ",".join(
            sorted(self.data.bootstrap_options.extra_packages)
        )
        return BaseDynamicTaskData(
            subject=suite_name,
            runtime_context=f"{architecture}:{variant}:{extra_packages}",
            configuration_context=architecture,
        )

    def configure_for_execution(self, download_dir: Path) -> bool:
        """Create file.sources and add it into the log."""
        # Create file.sources (keyrings are downloaded, if needed)
        keyrings_dir = download_dir / "keyrings"

        # mmdebstrap uses the subuid for using the keyring files. The directory
        # where the keyring files are saved must be accessible for the
        # mmdebstrap subuid user (and not only by the debusine-worker user).
        download_dir.chmod(0o755)
        keyrings_dir.mkdir(mode=0o755)

        host_sources = self._generate_deb822_sources(
            self.data.bootstrap_repositories,
            keyrings_dir=keyrings_dir,
            use_signed_by=self.data.bootstrap_options.use_signed_by,
        )

        # Make the files readable by any user (same reason as
        # above for the directory) and add them in self._keyrings
        # so cmdline use them
        for keyring in keyrings_dir.iterdir():
            keyring.chmod(0o644)
            self._keyrings.append(keyring)

        chroot_sources = [host_source.copy() for host_source in host_sources]

        # Change the path of the keyrings from the host paths to the
        # chroot path
        for chroot_source, task_repository in zip(
            chroot_sources, self.data.bootstrap_repositories
        ):
            if (signed_by := chroot_source.get("Signed-By")) is None:
                # Nothing needs to be done
                continue

            # If Signed-By has been set by _generate_deb822_sources, it means
            # that the repository specified a keyring
            assert task_repository.keyring is not None

            if task_repository.keyring.install:
                signed_by_path = Path(signed_by)
                filename = signed_by_path.name
                chroot_source["Signed-By"] = (
                    "/etc/apt/keyrings-debusine/" + filename
                )
                self._upload_keyrings.append(signed_by_path)
            else:
                # The key is not installed in the chroot, no "Signed-By"
                del chroot_source["Signed-By"]

        self._host_sources_file = download_dir / "host.sources"
        self._chroot_sources_file = download_dir / "chroot.sources"

        self._write_deb822s(host_sources, self._host_sources_file)
        self._write_deb822s(chroot_sources, self._chroot_sources_file)

        # Add the host and chroot source files for debugging purposes
        self.append_to_log_file(
            self._host_sources_file.name,
            self._host_sources_file.read_text().splitlines(),
        )

        self.append_to_log_file(
            self._chroot_sources_file.name,
            self._chroot_sources_file.read_text().splitlines(),
        )

        if script := self.data.customization_script:
            self._customization_script = download_dir / "customization_script"
            self._customization_script.write_text(script)

        return True

    def get_label(self) -> str:
        """Return the task label."""
        return "bootstrap a system tarball"

    @staticmethod
    def _get_source_components(apt_sources: Path) -> list[str]:
        """Return the components for the first APT source in apt_sources."""
        for source in Deb822.iter_paragraphs(apt_sources.read_text()):
            components = cast(str, source.get("Components", ""))
            return components.split()
        return []

    @staticmethod
    def _get_value_os_release(os_release: Path, key: str) -> str:
        """Parse file with the format /etc/os-release, return value for key."""
        # In https://www.freedesktop.org/software/systemd/man/latest/os-release.html  # noqa: E501
        # it specifies that the file is a "newline-separated list of
        # environment-like shell-compatible variable assignments".
        # Thus, shlex.split() is used it removes the quotes if they are in
        # any value

        for key_value in shlex.split(os_release.read_text()):
            k, v = key_value.split("=", 1)

            if key == k:
                return v

        raise KeyError(key)

    @staticmethod
    def _get_pkglist(var_lib_dpkg: Path) -> dict[str, str]:
        """
        Execute dpkg-query --admindir=var_lib_dpkg/dpkg --show.

        :return: dictionary with package names and versions.
        """
        cmd = [
            "dpkg-query",
            f"--admindir={var_lib_dpkg}/dpkg",
            "--show",
        ]

        process = subprocess.run(
            cmd, check=True, text=True, capture_output=True
        )

        result = {}
        for line in process.stdout.splitlines():
            name, version = line.split("\t")
            result[name] = version

        return result
