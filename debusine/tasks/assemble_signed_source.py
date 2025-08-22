# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to assemble a signed source package."""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path, PurePath
from typing import Any

from debian.changelog import Changelog
from debian.deb822 import Changes

from debusine.artifacts import (
    BinaryPackage,
    SigningOutputArtifact,
    SourcePackage,
    Upload,
)
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    get_binary_package_name,
)
from debusine.assets import KeyPurpose
from debusine.client.models import ArtifactResponse, RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask
from debusine.tasks.models import (
    AssembleSignedSourceData,
    AssembleSignedSourceDynamicData,
)
from debusine.tasks.server import TaskDatabaseInterface

# https://www.debian.org/doc/debian-policy/ch-controlfields.html#s-f-source
_re_package_name = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")


class AssembleError(Exception):
    """An error occurred while assembling a signed source package."""


class AssembleSignedSource(
    RunCommandTask[AssembleSignedSourceData, AssembleSignedSourceDynamicData],
    BaseTaskWithExecutor[
        AssembleSignedSourceData, AssembleSignedSourceDynamicData
    ],
):
    """Task to assemble a signed source package."""

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
        self._template_deb_file: Path | None = None
        self._signed_files: dict[tuple[KeyPurpose, str], Path] = {}
        self._built_using_ids: list[int] | None = None

        # Set by run.
        self._remote_execute_directory: PurePath | None = None
        self._local_execute_directory: Path | None = None
        self._source_package_artifact: SourcePackage | None = None
        self._upload_artifact: Upload | None = None

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
    ) -> AssembleSignedSourceDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: binary package name out of ``template``
        """
        template = task_database.lookup_single_artifact(self.data.template)

        self.ensure_artifact_categories(
            configuration_key="template",
            category=template.category,
            expected=(ArtifactCategory.BINARY_PACKAGE,),
        )
        assert isinstance(template.data, DebianBinaryPackage)
        package_name = get_binary_package_name(template.data)

        return AssembleSignedSourceDynamicData(
            environment_id=self.get_environment(
                task_database,
                self.data.environment,
                default_category=CollectionCategory.ENVIRONMENTS,
            ).id,
            template_id=template.id,
            signed_ids=task_database.lookup_multiple_artifacts(
                self.data.signed
            ).get_ids(),
            subject=package_name,
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return [
            self.dynamic_data.environment_id,
            self.dynamic_data.template_id,
            *self.dynamic_data.signed_ids,
        ]

    def _check_category(
        self, artifact: ArtifactResponse, expected_category: ArtifactCategory
    ) -> bool:
        if artifact.category != expected_category:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"Expected template of category {expected_category}; got "
                    f"{artifact.category}"
                ],
            )
            return False
        return True

    def _get_related_artifact_ids(
        self,
        artifact_id: int,
        target_category: ArtifactCategory,
        relation_type: RelationType = RelationType.RELATES_TO,
    ) -> Generator[int, None, None]:
        assert self.debusine is not None

        for relation in self.debusine.relation_list(artifact_id=artifact_id):
            if relation.type == relation_type:
                artifact = self.debusine.artifact_get(relation.target)
                if artifact.category == target_category:
                    yield relation.target

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data

        template_dir = destination / "template-artifact"
        template_dir.mkdir()
        template_artifact = self.fetch_artifact(
            self.dynamic_data.template_id, template_dir
        )
        if not self._check_category(
            template_artifact, ArtifactCategory.BINARY_PACKAGE
        ):
            return False
        deb_files = [
            file for file in template_artifact.files if file.endswith(".deb")
        ]
        if len(deb_files) != 1:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"Expected exactly one .deb package in template; got "
                    f"{deb_files}"
                ],
            )
            return False
        self._template_deb_name = BinaryPackage.create_data(
            template_artifact.data
        ).deb_fields["Package"]
        self._template_deb_file = template_dir / deb_files[0]

        for artifact_id in self.dynamic_data.signed_ids:
            signed_dir = destination / "signed-artifacts" / str(artifact_id)
            signed_dir.mkdir(parents=True)
            signed_artifact = self.fetch_artifact(artifact_id, signed_dir)
            if not self._check_category(
                signed_artifact, ArtifactCategory.SIGNING_OUTPUT
            ):
                return False
            signed_data = SigningOutputArtifact.create_data(
                signed_artifact.data
            )
            for result in signed_data.results:
                if result.output_file is not None:
                    self._signed_files[(signed_data.purpose, result.file)] = (
                        signed_dir / result.output_file
                    )

            self._built_using_ids = []
            for signing_input_id in self._get_related_artifact_ids(
                artifact_id, ArtifactCategory.SIGNING_INPUT
            ):
                for binary_package_id in self._get_related_artifact_ids(
                    signing_input_id, ArtifactCategory.BINARY_PACKAGE
                ):
                    self._built_using_ids.append(binary_package_id)

        return True

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """Configure task: ensure that the executor has dpkg-dev installed."""
        self._prepare_executor_instance()

        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")

        self.executor_instance.run(
            ["apt-get", "update"], run_as_root=True, check=True
        )
        self.executor_instance.run(
            ["apt-get", "--yes", "install", "dpkg-dev"],
            run_as_root=True,
            check=True,
        )

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
            raise AssembleError(f"{cmd_name} exited with code {returncode}")

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

    def _push_to_executor(self, source: Path, target: PurePath) -> None:
        """Push a subdirectory to the executor, preserving timestamps."""
        assert self.executor_instance is not None
        assert self._local_execute_directory is not None
        assert self._remote_execute_directory is not None

        # Guard against accidentally passing
        # {local,remote}_execute_directory directly.
        assert source != self._local_execute_directory
        assert target != self._remote_execute_directory

        source_tar_path = Path(f"{source}.tar")
        target_tar_path = PurePath(f"{target}.tar")
        subprocess.run(
            ["tar", "-cf", source_tar_path, source.name], cwd=source.parent
        )
        self.executor_instance.file_push(source_tar_path, target_tar_path)
        self._run_cmd_or_raise(
            [
                "tar",
                # Executors don't currently support cwd:
                # https://salsa.debian.org/freexian-team/debusine/-/issues/434
                "-C",
                str(target.parent),
                "--one-top-level",
                "-xf",
                str(target_tar_path),
            ],
            target.parent,
        )

    def _extract_binary(self, deb: PurePath, target: PurePath) -> None:
        """Extract a binary package."""
        self._run_cmd_or_raise(
            ["dpkg-deb", "-x", str(deb), str(target)],
            target.parent,
        )

    def _check_template(self, code_signing_path: Path) -> None:
        """Check that the template is well-formed."""
        source_template_path = code_signing_path / "source-template"
        for file in (
            "debian/source/format",
            "debian/changelog",
            "debian/control",
            "debian/copyright",
            "debian/rules",
        ):
            if not (source_template_path / file).exists():
                raise AssembleError(
                    f"Required file {file} missing from template"
                )
        source_format = (
            (source_template_path / "debian" / "source" / "format")
            .read_text()
            .rstrip("\n")
        )
        if source_format != "3.0 (native)":
            raise AssembleError(
                f"Expected template source format '3.0 (native)'; "
                f"got '{source_format}'"
            )
        for file in ("debian/source/options", "debian/source/local-options"):
            if (source_template_path / file).exists():
                raise AssembleError(f"Template may not contain {file}")

    def _add_signed_files(
        self, code_signing_path: Path, source_path: Path
    ) -> None:
        """Add signed files to the source directory being assembled."""
        manifest = json.loads((code_signing_path / "files.json").read_text())
        for package, metadata in manifest["packages"].items():
            if _re_package_name.match(package) is None:
                raise AssembleError(f"'{package}' is not a valid package name")
            for file in metadata["files"]:
                file_path = PurePath(file["file"])
                if ".." in file_path.parts:
                    raise AssembleError(
                        f"File name '{file_path}' may not contain '..' segments"
                    )
                if file_path.is_absolute():
                    raise AssembleError(
                        f"File name '{file_path}' may not be absolute"
                    )
                purpose: KeyPurpose
                match file["sig_type"]:
                    case "efi":
                        purpose = KeyPurpose.UEFI
                    case _:
                        raise AssembleError(
                            f"Cannot handle sig_type '{file['sig_type']}'"
                        )
                qualified_file = f"{package}/{file['file']}"
                if (
                    signed_file := self._signed_files.get(
                        (purpose, qualified_file)
                    )
                ) is None:
                    raise AssembleError(
                        f"{purpose} signature of '{qualified_file}' not "
                        f"available"
                    )
                signature_path = (
                    source_path
                    / "debian"
                    / "signatures"
                    / package
                    / f"{file['file']}.sig"
                )
                signature_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(signed_file, signature_path)

    def _build_source_package(self, local_source_path: Path) -> PurePath:
        """
        Build a source package in the executor.

        Returns the path to the built `.changes` file in the executor.
        """
        assert self._remote_execute_directory is not None

        remote_source_path = self._remote_execute_directory / "source"
        self._push_to_executor(local_source_path, remote_source_path)
        self._run_cmd_or_raise(
            [
                # Executors don't currently support cwd:
                # https://salsa.debian.org/freexian-team/debusine/-/issues/434
                "sh",
                "-c",
                f"cd {remote_source_path} && dpkg-source -b .",
            ],
            local_source_path,
            override_cmd_name="dpkg-source",
        )
        with open(local_source_path / "debian" / "changelog") as changelog_file:
            changelog = Changelog(changelog_file, max_blocks=1)
        remote_changes_path = (
            remote_source_path.parent
            / f"{changelog.package}_{changelog.version}_source.changes"
        )
        distribution = changelog.distributions.split()[0]
        dpkg_genchanges = [
            "dpkg-genchanges",
            "-S",
            f"-DDistribution={distribution}",
            "-UCloses",
            f"-O{remote_changes_path}",
        ]
        self._run_cmd_or_raise(
            [
                # Executors don't currently support cwd:
                # https://salsa.debian.org/freexian-team/debusine/-/issues/434
                "sh",
                "-c",
                f"cd {remote_source_path} && {shlex.join(dpkg_genchanges)}",
            ],
            local_source_path,
            override_cmd_name="dpkg-genchanges",
        )
        return remote_changes_path

    def _create_artifacts(self, remote_changes_path: PurePath) -> None:
        """Create artifacts for the built source package."""
        assert self.executor_instance is not None
        assert self._local_execute_directory is not None

        local_changes_path = (
            self._local_execute_directory / remote_changes_path.name
        )
        self.executor_instance.file_pull(
            remote_changes_path, local_changes_path
        )
        with open(local_changes_path) as changes_file:
            changes = Changes(changes_file)
        local_source_package_paths: list[Path] = []
        for checksum in changes["Files"]:
            local_source_package_paths.append(
                local_changes_path.parent / checksum["name"]
            )
            self.executor_instance.file_pull(
                remote_changes_path.parent / checksum["name"],
                local_source_package_paths[-1],
            )
        self._source_package_artifact = SourcePackage.create(
            name=changes["Source"],
            version=changes["Version"],
            files=local_source_package_paths,
        )
        self._upload_artifact = Upload.create(changes_file=local_changes_path)

    def run(self, execute_directory: Path) -> bool:
        """Do the main assembly work."""
        assert self._template_deb_name is not None
        assert self._template_deb_file is not None

        self._remote_execute_directory = PurePath(execute_directory)
        self._local_execute_directory = Path(
            tempfile.mkdtemp(prefix="debusine-assemble-signed-source-")
        )
        remote_template_path = self._remote_execute_directory / "template"
        local_template_path = self._local_execute_directory / "template"
        local_source_path = self._local_execute_directory / "source"

        # Running dpkg-deb on a user-provided file carries some risk, so we
        # do it within an executor, and then fetch the result using an
        # intermediate tar process.
        self._extract_binary(self._template_deb_file, remote_template_path)
        self._pull_from_executor(remote_template_path, local_template_path)
        local_code_signing_path = (
            local_template_path
            / "usr"
            / "share"
            / "code-signing"
            / self._template_deb_name
        )
        shutil.copytree(
            local_code_signing_path / "source-template", local_source_path
        )
        self._check_template(local_code_signing_path)
        self._add_signed_files(local_code_signing_path, local_source_path)
        changes_path = self._build_source_package(local_source_path)
        self._create_artifacts(changes_path)

        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload artifacts for the task."""
        assert self.work_request_id is not None
        assert self.debusine is not None

        if execution_success:
            assert self._built_using_ids is not None
            assert self._source_package_artifact is not None
            assert self._upload_artifact is not None

            uploaded_source_package_artifact = self.debusine.upload_artifact(
                self._source_package_artifact,
                workspace=self.workspace_name,
                work_request=self.work_request_id,
            )
            for built_using_id in self._built_using_ids:
                self.debusine.relation_create(
                    uploaded_source_package_artifact.id,
                    built_using_id,
                    RelationType.BUILT_USING,
                )
            uploaded_upload_artifact = self.debusine.upload_artifact(
                self._upload_artifact,
                workspace=self.workspace_name,
                work_request=self.work_request_id,
            )
            self.debusine.relation_create(
                uploaded_upload_artifact.id,
                uploaded_source_package_artifact.id,
                RelationType.EXTENDS,
            )

    def cleanup(self) -> None:
        """Clean up after running the task."""
        if self._local_execute_directory is not None:
            shutil.rmtree(self._local_execute_directory)
        super().cleanup()

    def get_label(self) -> str:
        """Return the task label."""
        return "assemble signed source"
