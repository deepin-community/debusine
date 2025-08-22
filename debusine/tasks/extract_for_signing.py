# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to extract signing input from other artifacts."""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePath
from typing import Any

from debusine.artifacts import SigningInputArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    get_binary_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask
from debusine.tasks.models import (
    ExtractForSigningData,
    ExtractForSigningDynamicData,
)
from debusine.tasks.server import TaskDatabaseInterface

# https://www.debian.org/doc/debian-policy/ch-controlfields.html#s-f-source
_re_package_name = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")


class ExtractError(Exception):
    """An error occurred while extracting signing input."""


class ExtractForSigning(
    RunCommandTask[ExtractForSigningData, ExtractForSigningDynamicData],
    BaseTaskWithExecutor[ExtractForSigningData, ExtractForSigningDynamicData],
):
    """Task to extract signing input from other artifacts."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the task."""
        super().__init__(task_data, dynamic_task_data)

        # Set by fetch_input.
        self._template_deb_name: str | None = None
        self._template_path: Path | None = None
        self._binary_artifacts: dict[str, int] | None = None
        self._binary_paths: dict[str, Path] | None = None

        # Set by run.
        self._remote_execute_directory: PurePath | None = None
        self._local_execute_directory: Path | None = None
        self._signing_input_artifacts: (
            dict[str, SigningInputArtifact] | None
        ) = None

    def _cmdline(self) -> list[str]:
        """Unused abstract method from RunCommandTask."""
        raise NotImplementedError()

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """Check if the specified worker can run the task."""
        if not super().can_run_on(worker_metadata):
            return False

        executor_available_key = f"executor:{self.backend}:available"
        return bool(worker_metadata.get(executor_available_key, False))

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> ExtractForSigningDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: binary package name of ``template_artifact``
        """
        template_artifact = task_database.lookup_single_artifact(
            self.data.input.template_artifact
        )

        self.ensure_artifact_categories(
            configuration_key="input.template_artifact",
            category=template_artifact.category,
            expected=(ArtifactCategory.BINARY_PACKAGE,),
        )
        assert isinstance(template_artifact.data, DebianBinaryPackage)
        binary_package_name = get_binary_package_name(template_artifact.data)

        binary_artifacts = task_database.lookup_multiple_artifacts(
            self.data.input.binary_artifacts
        )
        individual_binary_artifact_ids = []
        upload_artifact_ids = []
        for i, binary_artifact in enumerate(binary_artifacts):
            self.ensure_artifact_categories(
                configuration_key=f"input.binary_artifacts[{i}]",
                category=binary_artifact.category,
                expected=(
                    ArtifactCategory.BINARY_PACKAGE,
                    ArtifactCategory.UPLOAD,
                ),
            )
            match binary_artifact.category:
                case ArtifactCategory.BINARY_PACKAGE:
                    individual_binary_artifact_ids.append(binary_artifact.id)
                case ArtifactCategory.UPLOAD:
                    upload_artifact_ids.append(binary_artifact.id)
                case _ as unreachable:
                    raise AssertionError(
                        f"Unexpected artifact category: {unreachable}"
                    )
        if upload_artifact_ids:
            individual_binary_artifact_ids.extend(
                task_database.find_related_artifacts(
                    upload_artifact_ids,
                    ArtifactCategory.BINARY_PACKAGE,
                    relation_type=RelationType.EXTENDS,
                ).get_ids()
            )

        return ExtractForSigningDynamicData(
            environment_id=self.get_environment(
                task_database,
                self.data.environment,
                default_category=CollectionCategory.ENVIRONMENTS,
            ).id,
            input_template_artifact_id=template_artifact.id,
            input_binary_artifacts_ids=individual_binary_artifact_ids,
            subject=binary_package_name,
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return [
            self.dynamic_data.environment_id,
            self.dynamic_data.input_template_artifact_id,
            *self.dynamic_data.input_binary_artifacts_ids,
        ]

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data is not None

        (template_destination := destination / "template").mkdir()
        artifact = self.fetch_artifact(
            self.dynamic_data.input_template_artifact_id, template_destination
        )
        if artifact.category != ArtifactCategory.BINARY_PACKAGE:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"Expected template_artifact to be of category "
                    f"{ArtifactCategory.BINARY_PACKAGE}; got "
                    f"{artifact.category}"
                ],
            )
            return False
        self._template_deb_name = artifact.data["deb_fields"]["Package"]
        # Checked by BinaryPackage validator.
        assert len(artifact.files) == 1
        template_file = list(artifact.files)[0]
        if not template_file.endswith(".deb"):
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"Expected template_artifact file name to match *.deb; "
                    f"got {template_file}"
                ],
            )
            return False
        self._template_path = template_destination / template_file

        (binary_destination := destination / "binary").mkdir()
        self._binary_artifacts = {}
        self._binary_paths = {}
        for artifact_id in self.dynamic_data.input_binary_artifacts_ids:
            artifact = self.fetch_artifact(artifact_id, binary_destination)
            # Checked by build_dynamic_data.
            assert artifact.category == ArtifactCategory.BINARY_PACKAGE
            binary_deb_name = artifact.data["deb_fields"]["Package"]
            self._binary_artifacts[binary_deb_name] = artifact_id
            # Checked by BinaryPackage validator.
            assert len(artifact.files) == 1
            binary_file = list(artifact.files)[0]
            self._binary_paths[binary_deb_name] = (
                binary_destination / binary_file
            )

        return True

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """Configure task: create and start an executor instance."""
        self._prepare_executor_instance()

        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")

        return True

    def _run_cmd_or_raise(
        self,
        cmd: list[str],
        execute_directory: PurePath,
        *,
        override_cmd_name: str | None = None,
    ) -> None:
        cmd_name = override_cmd_name or cmd[0]
        self.logger.info("Executing: %s", shlex.join(cmd))
        returncode = self.run_cmd(cmd, Path(execute_directory))
        self.logger.info("%s exited with code %s", cmd_name, returncode)
        if returncode != 0:
            raise ExtractError(f"{cmd_name} exited with code {returncode}")

    def _pull_from_executor(self, source: PurePath, target: Path) -> None:
        """Pull a subdirectory from the executor, preserving timestamps."""
        assert self.executor_instance is not None
        assert self._remote_execute_directory is not None
        assert self._local_execute_directory is not None

        # Guard against accidentally passing
        # {local,remote}_execute_directory directly.
        assert source != self._remote_execute_directory
        assert target != self._local_execute_directory

        source_tar_path = PurePath(f"{source}.tar")
        target_tar_path = Path(f"{target}.tar")
        self._run_cmd_or_raise(
            [
                "tar",
                # Executors don't currently support cwd:
                # https://salsa.debian.org/freexian-team/debusine/-/issues/434
                "-C",
                str(source.parent),
                "-cf",
                str(source_tar_path),
                source.name,
            ],
            source.parent,
        )
        self.executor_instance.file_pull(source_tar_path, target_tar_path)
        subprocess.run(
            ["tar", "--one-top-level", "-xf", target_tar_path],
            cwd=target.parent,
        )

    def _extract_binary(self, deb: PurePath, target: PurePath) -> None:
        """Extract a binary package."""
        self._run_cmd_or_raise(
            ["dpkg-deb", "-x", str(deb), str(target)],
            target.parent,
        )

    def _read_manifest(self) -> dict[str, Any]:
        """Read the files.json manifest from the template artifact."""
        assert self.executor_instance is not None
        assert self._template_deb_name is not None
        assert self._template_path is not None
        assert self._remote_execute_directory is not None
        assert self._local_execute_directory is not None

        remote_template_path = (
            self._remote_execute_directory / self._template_deb_name
        )
        local_files_json_path = self._local_execute_directory / "files.json"

        self._extract_binary(self._template_path, remote_template_path)
        self.executor_instance.file_pull(
            remote_template_path
            / "usr"
            / "share"
            / "code-signing"
            / self._template_deb_name
            / "files.json",
            local_files_json_path,
        )
        manifest = json.loads(local_files_json_path.read_text())
        # For mypy.  If the manifest isn't a JSON object, we'll get a
        # run-time error almost immediately afterwards anyway.
        assert isinstance(manifest, dict)
        return manifest

    def _make_signing_input_artifact(
        self, package: str, metadata: dict[str, Any]
    ) -> SigningInputArtifact:
        """
        Check a package's metadata and make a signing-input artifact.

        :raises ExtractError: if the package's metadata is invalid.
        """
        assert self.executor_instance is not None
        assert self._binary_paths is not None
        assert self._remote_execute_directory is not None
        assert self._local_execute_directory is not None

        if _re_package_name.match(package) is None:
            raise ExtractError(f"'{package}' is not a valid package name")

        remote_binary_path = self._remote_execute_directory / package
        self._extract_binary(self._binary_paths[package], remote_binary_path)

        local_binary_path = self._local_execute_directory / package
        self._pull_from_executor(remote_binary_path, local_binary_path)

        signing_input_paths: list[Path] = []
        for file in metadata["files"]:
            file_path = PurePath(file["file"])
            if ".." in file_path.parts:
                raise ExtractError(
                    f"File name '{file_path}' may not contain '..' segments"
                )
            if file_path.is_absolute():
                raise ExtractError(
                    f"File name '{file_path}' may not be absolute"
                )
            if not (
                (local_binary_path / file_path)
                .resolve()
                .is_relative_to(local_binary_path)
            ):
                raise ExtractError(
                    f"File name '{file_path}' may not traverse symlinks to "
                    f"outside the package"
                )
            signing_input_paths.append(local_binary_path / file_path)

        return SigningInputArtifact.create(
            signing_input_paths,
            local_binary_path.parent,
            trusted_certs=metadata.get("trusted_certs"),
            binary_package_name=package,
        )

    def run(self, execute_directory: Path) -> bool:
        """Do the main extraction work."""
        assert self.executor_instance is not None
        assert self._binary_paths is not None

        self._remote_execute_directory = PurePath(execute_directory)
        self._local_execute_directory = Path(
            tempfile.mkdtemp(prefix="debusine-extract-for-signing-")
        )

        manifest = self._read_manifest()
        self._signing_input_artifacts = {
            package: self._make_signing_input_artifact(package, metadata)
            for package, metadata in manifest["packages"].items()
        }

        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload artifacts for the task."""
        assert self.work_request_id is not None
        assert self.debusine is not None

        if execution_success:
            assert self.dynamic_data is not None
            assert self._binary_artifacts is not None
            assert self._signing_input_artifacts is not None

            for package, artifact in self._signing_input_artifacts.items():
                uploaded_artifact = self.debusine.upload_artifact(
                    artifact,
                    workspace=self.workspace_name,
                    work_request=self.work_request_id,
                )
                for related_to_id in (
                    self.dynamic_data.input_template_artifact_id,
                    self._binary_artifacts[package],
                ):
                    self.debusine.relation_create(
                        uploaded_artifact.id,
                        related_to_id,
                        RelationType.RELATES_TO,
                    )

    def cleanup(self) -> None:
        """Clean up after running the task."""
        if self._local_execute_directory is not None:
            shutil.rmtree(self._local_execute_directory)
        super().cleanup()

    def get_label(self) -> str:
        """Return the task label."""
        return "extract signing input"
