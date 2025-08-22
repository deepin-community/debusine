# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Helper mixin for the integration tests."""

import os
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Any

import requests
import yaml
from debian.deb822 import Deb822

from debusine.artifacts.local_artifact import BinaryPackage, SourcePackage
from debusine.artifacts.models import ArtifactCategory
from debusine.test import TestCase
from utils.client import Client


class IntegrationTestHelpersMixin(TestCase):
    """Utility methods for the integration tests."""

    @staticmethod
    @contextmanager
    def _temporary_directory() -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory(
            prefix="debusine-integration-tests-"
        ) as temp_directory:
            yield Path(temp_directory)

    @classmethod
    def make_apt_environment(self, apt_path: Path) -> dict[str, str]:
        """Make a suitable process environment for running APT commands."""
        return {**os.environ, "APT_CONFIG": str(apt_path / "etc/apt/apt.conf")}

    @classmethod
    @contextmanager
    def apt_indexes(
        cls,
        suite_name: str,
        url: str = "https://deb.debian.org/debian",
        signed_by: str = "/usr/share/keyrings/debian-archive-keyring.gpg",
        architectures: list[str] | None = None,
    ) -> Generator[Path, None, None]:
        """Fetch APT indexes for a suite."""
        if architectures is None:
            architectures = [
                subprocess.check_output(
                    ["dpkg", "--print-architecture"], text=True
                ).strip()
            ]

        with cls._temporary_directory() as apt_path:
            (apt_path / "etc/apt/apt.conf.d").mkdir(parents=True)
            (apt_path / "etc/apt/preferences.d").mkdir(parents=True)
            (apt_path / "etc/apt/sources.list.d").mkdir(parents=True)
            (apt_path / "var/lib/apt/lists/partial").mkdir(parents=True)
            (apt_path / "etc/apt/apt.conf").write_text(
                dedent(
                    f"""\
                    APT::Architecture "{architectures[0]}";
                    APT::Architectures "{','.join(architectures)}";
                    Dir "{apt_path}";
                    """
                )
            )
            if "\n" in signed_by:
                # A multi-line Signed-By is an embedded public key.
                signed_by = "\n" + "\n".join(
                    f" {line}" if line else " ."
                    for line in signed_by.splitlines()
                )
            source = {
                "Types": "deb deb-src",
                "URIs": url,
                "Suites": suite_name,
                "Components": "main",
                "Signed-By": signed_by,
            }
            (apt_path / "etc/apt/sources.list.d/test.sources").write_text(
                str(Deb822(source))
            )
            subprocess.check_call(
                ["apt-get", "update"], env=cls.make_apt_environment(apt_path)
            )
            yield apt_path

    @classmethod
    def _create_artifact(
        cls,
        artifact_type: ArtifactCategory,
        data: dict[str, Any],
        files: list[Path],
        base_directory: Path,
    ) -> int:
        """
        Create an artifact.

        :param artifact_type: type of artifact (e.g. debian:source-package,
          debian:binary-packages).
        :param data: type-specific data to attach to the artifact.
        :param files: list of paths to files to attach to the artifact.
        :param base_directory: base directory for files to upload (to allow
          calculating relative paths)
        """
        with cls._temporary_directory() as temp_path:
            data_yaml = temp_path / "artifact-data.yaml"
            data_yaml.write_text(yaml.safe_dump(data))

            artifact_id = Client.execute_command(
                "create-artifact",
                artifact_type,
                "--workspace",
                "System",
                "--data",
                data_yaml,
                "--upload-base-directory",
                base_directory,
                "--upload",
                *files,
            )["artifact_id"]
            assert isinstance(artifact_id, int)
            return artifact_id

    @classmethod
    def _create_apt_artifact(
        cls,
        apt_path: Path,
        package_name: str,
        apt_get_command: str,
        artifact_type: ArtifactCategory,
    ) -> int:
        """
        Download files using apt and create an artifact from them.

        :param suite_name: suite to download from.
        :param package_name: package contained in the artifact.
        :param apt_get_command: e.g. "source", "download".
        :param artifact_type: type of artifact (e.g. debian:source-package,
          debian:binary-packages).
        """
        with cls._temporary_directory() as temp_path:
            subprocess.check_call(
                ["apt-get", apt_get_command, "--download-only", package_name],
                cwd=temp_path,
                env=cls.make_apt_environment(apt_path),
            )

            files = list(temp_path.iterdir())

            if artifact_type == ArtifactCategory.SOURCE_PACKAGE:
                [dsc_file] = [
                    file for file in files if file.name.endswith(".dsc")
                ]
                version = dsc_file.name.split("_")[1]
                src_artifact = SourcePackage.create(
                    name=package_name, version=version, files=files
                )
                data = src_artifact.data.dict()
            elif artifact_type == ArtifactCategory.BINARY_PACKAGE:
                bin_artifact = BinaryPackage.create(file=files[0])
                data = bin_artifact.data.dict()
            else:
                raise NotImplementedError(
                    f"cannot generate artifact data for {artifact_type}"
                )

            return cls._create_artifact(artifact_type, data, files, temp_path)

    @classmethod
    def create_artifact_source(cls, apt_path: Path, package_name: str) -> int:
        """Create a source artifact with hello files."""
        return cls._create_apt_artifact(
            apt_path, package_name, "source", ArtifactCategory.SOURCE_PACKAGE
        )

    @classmethod
    def create_artifact_binary(cls, apt_path: Path, package_name: str) -> int:
        """Create a binary artifact."""
        return cls._create_apt_artifact(
            apt_path, package_name, "download", ArtifactCategory.BINARY_PACKAGE
        )

    @classmethod
    def create_artifact_upload(
        cls, apt_path: Path, package_names: list[str]
    ) -> int:
        """Create an upload artifact containing some binary packages."""
        with cls._temporary_directory() as temp_path:
            for package_name in package_names:
                subprocess.check_call(
                    ["apt-get", "download", "--download-only", package_name],
                    cwd=temp_path,
                    env=cls.make_apt_environment(apt_path),
                )

            files = sorted(temp_path.iterdir())
            changes_file = temp_path / f"{package_names[0]}.changes"
            cls.write_changes_file(changes_file, files)

            artifact_id = Client.execute_command(
                "import-debian-artifact",
                "--workspace",
                "System",
                changes_file,
            )["artifact_id"]
            assert isinstance(artifact_id, int)
            return artifact_id

    @classmethod
    def create_artifact_build_logs(cls, filename: str, contents: str) -> int:
        """
        Create a build-log artifact.

        :param filename: filename that is being created
        :param contents: contents of the file being created
        """
        with cls._temporary_directory() as temp_path:
            artifact_type = ArtifactCategory.PACKAGE_BUILD_LOG

            (temp_path / filename).write_text(contents)
            files = list(temp_path.iterdir())

            data = {
                "source": "test",
                "version": "1.0",
                "filename": filename,
            }

            return cls._create_artifact(artifact_type, data, files, temp_path)

    @classmethod
    def create_artifact_signing_input(
        cls, binary_package_name: str, files: dict[str, bytes]
    ) -> int:
        """
        Create a signing-input artifact.

        :param binary_package_name: name of the binary package this represents.
        :param files: mapping of filenames to contents, each of which is to
          be signed
        :return: the created artifact ID
        """
        with cls._temporary_directory() as temp_path:
            artifact_type = ArtifactCategory.SIGNING_INPUT

            for filename, contents in files.items():
                (temp_path / filename).parent.mkdir(parents=True, exist_ok=True)
                (temp_path / filename).write_bytes(contents)

            return cls._create_artifact(
                artifact_type,
                {
                    "binary_package_name": binary_package_name,
                },
                [temp_path / filename for filename in files],
                temp_path,
            )

    @staticmethod
    def print_work_request_debug_logs(work_request_id: int) -> None:
        """Print work request's log's - used for debugging."""
        work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        for artifact in work_request["artifacts"]:
            if artifact["category"] == ArtifactCategory.WORK_REQUEST_DEBUG_LOGS:
                for path, file_information in artifact["files"].items():
                    print("------ FILE:", path)
                    print(">>>>>>>>>>>")
                    print(requests.get(file_information["url"]).text)
                    print("<<<<<<<<<<<")

    @staticmethod
    def create_system_images_mmdebstrap(
        suites: list[str], backend: str = "unshare"
    ) -> None:
        """
        Create debian:system-image artifacts and add them to a collection.

        :param suites: suites of the system images
        """
        template_data = {
            "vendor": "debian",
            "targets": [
                {
                    "codenames": suites,
                    "backends": [backend],
                    "architectures": [
                        subprocess.check_output(
                            ["dpkg", "--print-architecture"], text=True
                        ).strip()
                    ],
                    "mmdebstrap_template": {
                        "bootstrap_options": {"variant": "apt"},
                        "bootstrap_repositories": [
                            {
                                "mirror": "http://deb.debian.org/debian",
                                "components": ["main"],
                            }
                        ],
                    },
                }
            ],
        }

        workflow_template_result = Client.execute_command(
            "create-workflow-template",
            f"mmdebstrap-environment-{'-'.join(suites)}",
            "update_environments",
            stdin=yaml.safe_dump(template_data),
        )
        assert workflow_template_result.returncode == 0

        workflow_id = Client.execute_command(
            "create-workflow",
            f"mmdebstrap-environment-{'-'.join(suites)}",
            stdin=yaml.safe_dump({}),
        )["workflow_id"]

        # The worker should get the new work request and start executing it
        assert Client.wait_for_work_request_completed(
            workflow_id, "success", timeout=1800 * len(suites)
        )
