# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Task to build Debian packages with sbuild.

This task implements the PackageBuild generic task for its task_data:
https://freexian-team.pages.debian.net/debusine/reference/tasks/ontology-generic-tasks.html#task-packagebuild
"""

import email.utils
import os
from pathlib import Path
from typing import Any, cast

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

import debian.deb822 as deb822
import yaml

import debusine.utils
from debusine.artifacts import (
    BinaryPackage,
    BinaryPackages,
    PackageBuildLog,
    SigningInputArtifact,
    Upload,
)
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSourcePackage,
    DebianSystemImage,
    DebianSystemTarball,
    DoseDistCheck,
    get_source_package_name,
)
from debusine.client.models import RelationType, RemoteArtifact
from debusine.tasks import (
    BaseTaskWithExecutor,
    ExtraRepositoryMixin,
    RunCommandTask,
)
from debusine.tasks.models import SbuildData, SbuildDynamicData
from debusine.tasks.sbuild_validator_mixin import SbuildValidatorMixin
from debusine.tasks.server import TaskDatabaseInterface
from debusine.utils import read_dsc


class Sbuild(
    SbuildValidatorMixin,
    ExtraRepositoryMixin[SbuildData, SbuildDynamicData],
    RunCommandTask[SbuildData, SbuildDynamicData],
    BaseTaskWithExecutor[SbuildData, SbuildDynamicData],
):
    """Task implementing a Debian package build with sbuild."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the sbuild task."""
        super().__init__(task_data, dynamic_task_data)
        self.chroots: list[str] | None = None
        self.builder = "sbuild"

        # dsc_file Path. Set by self.fetch_input()
        self._dsc_file: Path | None = None
        self._extra_packages: list[Path] = []

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> SbuildDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: source package name
        :runtime_context:
          ``$build_components:$host_architecture:$build_architecture`` or
          None if ``build_options`` is provided
        :configuration_context: codename of ``environment``
        """
        input_source_artifact = task_database.lookup_single_artifact(
            self.data.input.source_artifact
        )
        self.ensure_artifact_categories(
            configuration_key="input.source_artifact",
            category=input_source_artifact.category,
            expected=(ArtifactCategory.SOURCE_PACKAGE,),
        )
        assert isinstance(input_source_artifact.data, DebianSourcePackage)

        environment = self.get_environment(
            task_database,
            self.data.environment,
            default_category=CollectionCategory.ENVIRONMENTS,
        )
        self.ensure_artifact_categories(
            configuration_key="environment",
            category=environment.category,
            expected=(
                ArtifactCategory.SYSTEM_TARBALL,
                ArtifactCategory.SYSTEM_IMAGE,
            ),
        )
        assert isinstance(
            environment.data, (DebianSystemTarball, DebianSystemImage)
        )

        DEBUSINE_FQDN = task_database.get_server_setting("DEBUSINE_FQDN")

        if self._build_options():
            # Do not record stats for non-standard builds
            runtime_context = None
        else:
            build_components = "+".join(sorted(self.data.build_components))

            # Currently defining build_architecture for Sbuild is not
            # supported. It defaults to the host_architecture
            build_architecture = self.data.host_architecture

            runtime_context = (
                f"{build_components}:"
                f"{self.data.host_architecture}:"
                f"{build_architecture}"
            )

        return SbuildDynamicData(
            environment_id=environment.id,
            input_source_artifact_id=input_source_artifact.id,
            input_extra_binary_artifacts_ids=(
                task_database.lookup_multiple_artifacts(
                    self.data.input.extra_binary_artifacts
                ).get_ids()
            ),
            binnmu_maintainer=f"Debusine <noreply@{DEBUSINE_FQDN}>",
            subject=get_source_package_name(input_source_artifact.data),
            runtime_context=runtime_context,
            configuration_context=environment.data.codename,
        )

    def get_source_artifacts_ids(self) -> list[int]:
        """
        Return the list of source artifact IDs used by this task.

        This refers to the artifacts actually used by the task. If
        dynamic_data is empty, this returns the empty list.
        """
        if not self.dynamic_data:
            return []
        result: list[int] = []
        if val := self.dynamic_data.environment_id:
            result.append(val)
        result.append(self.dynamic_data.input_source_artifact_id)
        result.extend(self.dynamic_data.input_extra_binary_artifacts_ids)
        return result

    @classmethod
    def analyze_worker(cls) -> dict[str, Any]:
        """Report metadata for this task on this worker."""
        metadata = super().analyze_worker()

        available_key = cls.prefix_with_task_name("available")
        metadata[available_key] = debusine.utils.is_command_available("sbuild")

        return metadata

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """Check the specified worker can run the requested task."""
        if not super().can_run_on(worker_metadata):
            return False

        available_key = self.prefix_with_task_name("available")
        if not worker_metadata.get(available_key, False):
            return False

        executor_available_key = f"executor:{self.backend}:available"
        if not worker_metadata.get(executor_available_key, False):
            return False
        if self.backend != "unshare":
            if not worker_metadata.get("autopkgtest:available", False):
                return False

        return True

    def fetch_input(self, destination: Path) -> bool:
        """Download the source artifact."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")
        assert self.dynamic_data

        source_artifact_id = self.dynamic_data.input_source_artifact_id
        artifact = self.debusine.artifact_get(source_artifact_id)
        if artifact.category != ArtifactCategory.SOURCE_PACKAGE:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"input.source_artifact points to a "
                    f"{artifact.category}, not the expected "
                    f"{ArtifactCategory.SOURCE_PACKAGE}."
                ],
            )
            return False
        for file in artifact.files:
            if file.endswith(".dsc"):
                self._dsc_file = destination / file
                break
        else:
            raise AssertionError("No .dsc file found in source package.")
        self.fetch_artifact(source_artifact_id, destination)

        for artifact_id in self.dynamic_data.input_extra_binary_artifacts_ids:
            artifact = self.debusine.artifact_get(artifact_id)
            if artifact.category in (
                ArtifactCategory.BINARY_PACKAGE,
                ArtifactCategory.BINARY_PACKAGES,
                ArtifactCategory.UPLOAD,
            ):
                for file in artifact.files:
                    if file.endswith(".deb"):
                        self._extra_packages.append(destination / file)
            else:
                self.append_to_log_file(
                    "fetch_input.log",
                    [
                        f"input.extra_binary_artifacts includes artifact "
                        f"{artifact_id}, a {artifact.category}, not the "
                        f"expected {ArtifactCategory.BINARY_PACKAGE}, "
                        f"{ArtifactCategory.BINARY_PACKAGES}, or "
                        f"{ArtifactCategory.UPLOAD}."
                    ],
                )
                return False
            self.fetch_artifact(artifact_id, destination)

        self._prepare_executor()

        self.write_extra_repository_config(
            codename=self._environment_codename(), destination=destination
        )

        return True

    def _environment_codename(self) -> str:
        """Return the codename of the environment we're building in."""
        if self.executor is None:
            raise ValueError(
                "self.executor not set, call _prepare_executor() first"
            )

        return cast(str, self.executor.system_image.data["codename"])

    def _cmdline(self) -> list[str]:
        """
        Build the sbuild command line.

        Use self.data and self._dsc_file.
        """
        cmd = [
            self.builder,
            "--purge-deps=never",
            # Use a higher-level workflow instead to run both sbuild and
            # lintian, such as debian_pipeline.
            "--no-run-lintian",
        ]
        if "any" in self.data.build_components:
            cmd.append("--arch-any")
        else:
            cmd.append("--no-arch-any")
        if "all" in self.data.build_components:
            cmd.append("--arch-all")
        else:
            cmd.append("--no-arch-all")
        if "source" in self.data.build_components:
            cmd.append("--source")
        else:
            cmd.append("--no-source")

        cmd.append("--arch=" + self.data.host_architecture)
        cmd += self._cmdline_backend()
        cmd += self._cmdline_extra_repository()
        cmd += self._cmdline_binnmu()

        if self.data.build_profiles:
            cmd.append(f"--profiles={','.join(self.data.build_profiles)}")

        # BD-Uninstallable
        cmd.append("--bd-uninstallable-explainer=dose3")

        for package in self._extra_packages:
            cmd.append(f"--extra-package={package}")

        cmd.append(str(self._dsc_file))

        return cmd

    def _cmdline_backend(self) -> list[str]:
        """Generate command line arguments for the backend and environment."""
        distribution = self._environment_codename()
        args: list[str] = [f"--dist={distribution}"]

        assert self.executor
        if self.backend == "unshare":
            args += [
                "--chroot-mode=unshare",
                f"--chroot={self.executor.image_name()}",
                # Remove any dangling resolv.conf symlink (from
                # systemd-resolved installed in the environment, #1071736)
                "--chroot-setup-commands=rm -f /etc/resolv.conf",
            ]
        else:
            virt_server = self.executor.autopkgtest_virt_server()
            args += [
                "--chroot-mode=autopkgtest",
                f"--autopkgtest-virt-server={virt_server}",
            ] + [
                f"--autopkgtest-virt-server-opt={opt.replace('%', '%%')}"
                for opt in self.executor.autopkgtest_virt_args()
            ]
        return args

    def _cmdline_extra_repository(self) -> list[str]:
        """Generate the command line arguments for extra_repositories."""
        args: list[str] = []

        if self.supports_deb822_sources(self._environment_codename()):
            # sbuild has no native deb822 support, yet: #1089735
            for path in self.extra_repository_sources:
                args.append(
                    f"--pre-build-commands=cat {path} | "
                    f"%SBUILD_CHROOT_EXEC sh -c 'cat > "
                    f"/etc/apt/sources.list.d/{path.name}'"
                )
        else:
            for source in self.iter_oneline_sources():
                args.append(f"--extra-repository={source}")

        # apt < 2.3.10 has no support for keys embedded in Signed-By
        for path in self.extra_repository_keys:
            args.append(
                f"--pre-build-commands=cat {path} | "
                f"%SBUILD_CHROOT_EXEC sh -c 'mkdir -p /etc/apt/keyrings && "
                f"cat > /etc/apt/keyrings/{path.name}'"
            )

        return args

    def _cmdline_binnmu(self) -> list[str]:
        """Generate the command line arguments for binnmu."""
        if self.data.binnmu is None:
            return []

        args: list[str] = [
            f"--make-binNMU={self.data.binnmu.changelog}",
            f"--append-to-version={self.data.binnmu.suffix}",
            "--binNMU=0",
        ]
        if self.data.binnmu.timestamp is not None:
            rfc5322date = email.utils.format_datetime(
                self.data.binnmu.timestamp
            )
            args.append(f"--binNMU-timestamp={rfc5322date}")
        maintainer: str | None
        if self.data.binnmu.maintainer is not None:
            maintainer = str(self.data.binnmu.maintainer)
        else:
            assert self.dynamic_data
            maintainer = self.dynamic_data.binnmu_maintainer
        args.append(f"--maintainer={maintainer}")
        return args

    def _build_options(self) -> list[str]:
        return [
            profile
            for profile in self.data.build_profiles or []
            if profile in ("nocheck", "nodoc")
        ]

    def _cmd_env(self) -> dict[str, str] | None:
        """Set DEB_BUILD_OPTIONS for build profiles."""
        if build_options := self._build_options():
            env = dict(os.environ)
            env["DEB_BUILD_OPTIONS"] = " ".join(
                os.environ.get("DEB_BUILD_OPTIONS", "").split() + build_options
            )
            return env
        return None

    def task_succeeded(
        self,
        returncode: int | None,  # noqa: U100
        execute_directory: Path,  # noqa: U100
    ) -> bool:
        """
        Check whether the task succeeded.

        `sbuild` returns 0 for "Status: skipped" (i.e. host architecture not
        supported by source package); we consider that a failure.
        """
        if not super().task_succeeded(returncode, execute_directory):
            return False

        build_log_path = debusine.utils.find_file_suffixes(
            execute_directory, [".build"]
        )
        if build_log_path is None:
            return False

        status = ""
        # Reading the whole build log is inefficient, but shouldn't usually
        # be too bad in practice.
        with open(build_log_path, errors="replace") as f:
            for line in f:
                if line.startswith("Status: "):
                    status = line[len("Status: ") :].rstrip("\n")
        return status == "successful"

    @staticmethod
    def _extract_dose3_explanation(
        build_log_path: Path,
    ) -> DoseDistCheck | None:
        """Isolate and parse dose3 output in BD-Uninstallable scenario."""
        output = ''
        with open(build_log_path, errors='replace') as f:
            # find start of dose-debcheck output
            while (line := f.readline()) != '':
                if line == '(I)Dose_applications: Solving...\n':
                    break

            # exit if no/invalid output
            if not (line := f.readline()).startswith('output-version'):
                return None
            output += line

            # grab until next section
            while (line := f.readline()) != '':
                if line.startswith('+----'):
                    break
                output += line

            # convert & validate to Pydantic structure
            # Use yaml.CBaseLoad to work-around malformed yaml
            # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=834059#22
            # https://gitlab.com/irill/dose3/-/issues/18
            parsed_dict = yaml.load(output, yaml.CBaseLoader)
            try:
                return DoseDistCheck.parse_obj(parsed_dict)
            except pydantic.ValidationError:
                return None

    def _upload_package_build_log(
        self,
        build_directory: Path,
        source: str,
        version: str,
        execution_success: bool,
    ) -> RemoteArtifact | None:
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        build_log_path = debusine.utils.find_file_suffixes(
            build_directory, [".build"]
        )
        if build_log_path is None:
            return None

        explanation = None
        if not execution_success:
            # BD-Uninstallable: look for the dose3 output
            explanation = self._extract_dose3_explanation(build_log_path)

        package_build_log = PackageBuildLog.create(
            source=source,
            version=version,
            file=build_log_path,
            bd_uninstallable=explanation,
        )

        return self.debusine.upload_artifact(
            package_build_log,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

    def _upload_binary_upload(
        self, build_directory: Path
    ) -> RemoteArtifact | None:
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        changes_path = debusine.utils.find_file_suffixes(
            build_directory, [".changes"]
        )
        if changes_path is None:
            return None

        artifact_binary_upload = Upload.create(
            changes_file=changes_path,
        )
        return self.debusine.upload_artifact(
            artifact_binary_upload,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

    def _upload_signing_input(
        self, build_directory: Path
    ) -> RemoteArtifact | None:
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        changes_path = debusine.utils.find_file_suffixes(
            build_directory, [".changes"]
        )
        if changes_path is None:
            return None

        artifact_signing_input = SigningInputArtifact.create(
            [changes_path], build_directory
        )
        return self.debusine.upload_artifact(
            artifact_signing_input,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

    def _create_binary_package_local_artifacts(
        self,
        build_directory: Path,
        dsc: deb822.Dsc,
        architecture: str,
        suffixes: list[str],
    ) -> list[BinaryPackage | BinaryPackages]:
        deb_paths = debusine.utils.find_files_suffixes(
            build_directory, suffixes
        )

        artifacts: list[BinaryPackage | BinaryPackages] = []
        for deb_path in deb_paths:
            artifacts.append(BinaryPackage.create(file=deb_path))
        artifacts.append(
            BinaryPackages.create(
                srcpkg_name=dsc["source"],
                srcpkg_version=dsc["version"],
                version=dsc["version"],
                architecture=architecture,
                files=deb_paths,
            )
        )
        return artifacts

    def _upload_binary_packages(
        self, build_directory: Path, dsc: deb822.Dsc
    ) -> list[RemoteArtifact]:
        r"""Upload \*.deb and \*.udeb files."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        host_arch = self.data.host_architecture

        packages = []

        if "any" in self.data.build_components:
            prefix = "_" + host_arch
            packages.extend(
                self._create_binary_package_local_artifacts(
                    build_directory,
                    dsc,
                    host_arch,
                    [prefix + ".deb", prefix + ".udeb"],
                )
            )

        if "all" in self.data.build_components:
            prefix = "_all"
            packages.extend(
                self._create_binary_package_local_artifacts(
                    build_directory,
                    dsc,
                    "all",
                    [prefix + ".deb", prefix + ".udeb"],
                )
            )

        remote_artifacts: list[RemoteArtifact] = []
        for package in packages:
            if package.files:
                remote_artifacts.append(
                    self.debusine.upload_artifact(
                        package,
                        workspace=self.workspace_name,
                        work_request=self.work_request_id,
                    )
                )

        return remote_artifacts

    def _create_remote_binary_packages_relations(
        self,
        remote_build_log: RemoteArtifact | None,
        remote_binary_upload: RemoteArtifact | None,
        remote_binary_packages: list[RemoteArtifact],
        remote_signing_input: RemoteArtifact | None,
    ) -> None:
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        for remote_binary_package in remote_binary_packages:
            for source_artifact_id in self._source_artifacts_ids:
                self.debusine.relation_create(
                    remote_binary_package.id,
                    source_artifact_id,
                    RelationType.BUILT_USING,
                )

            if remote_build_log is not None:
                self.debusine.relation_create(
                    remote_build_log.id,
                    remote_binary_package.id,
                    RelationType.RELATES_TO,
                )

            if remote_binary_upload is not None:
                self.debusine.relation_create(
                    remote_binary_upload.id,
                    remote_binary_package.id,
                    RelationType.EXTENDS,
                )
                self.debusine.relation_create(
                    remote_binary_upload.id,
                    remote_binary_package.id,
                    RelationType.RELATES_TO,
                )

        if (
            remote_binary_upload is not None
            and remote_signing_input is not None
        ):
            self.debusine.relation_create(
                remote_signing_input.id,
                remote_binary_upload.id,
                RelationType.RELATES_TO,
            )

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """
        Configure Task: set variables needed for the build() step.

        Return True if configuration worked, False, if there was a problem.
        """
        if self._dsc_file is None or not self._dsc_file.exists():
            self.append_to_log_file(
                "configure_for_execution.log",
                ["Input source package not found."],
            )
            return False

        return True

    def upload_artifacts(
        self, directory: Path, *, execution_success: bool
    ) -> None:
        """
        Upload the artifacts from directory.

        :param directory: directory containing the files that
          will be uploaded.
        :param execution_success: if False skip uploading .changes and
          *.deb/*.udeb
        """
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        dsc = read_dsc(self._dsc_file)

        if dsc is not None:
            # Upload the .build file (PackageBuildLog)
            remote_build_log = self._upload_package_build_log(
                directory, dsc["source"], dsc["version"], execution_success
            )

            if remote_build_log is not None:
                for source_artifact_id in self._source_artifacts_ids:
                    self.debusine.relation_create(
                        remote_build_log.id,
                        source_artifact_id,
                        RelationType.RELATES_TO,
                    )

            if execution_success:
                # Upload the *.deb/*.udeb files (BinaryPackages)
                remote_binary_packages = self._upload_binary_packages(
                    directory, dsc
                )

                # Upload the .changes and the rest of the files
                remote_binary_changes = self._upload_binary_upload(directory)

                # Upload the .changes on its own as signing input
                remote_signing_input = self._upload_signing_input(directory)

                # Create the relations
                self._create_remote_binary_packages_relations(
                    remote_build_log,
                    remote_binary_changes,
                    remote_binary_packages,
                    remote_signing_input,
                )

    def get_label(self) -> str:
        """Return the task label."""
        return (
            f"build {subject}"
            if (subject := self.get_subject())
            else "build a package"
        )
