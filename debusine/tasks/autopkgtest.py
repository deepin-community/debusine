# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to use autopkgtest in debusine."""
import logging
import re
import shlex
from pathlib import Path
from typing import Any, TypeAlias, cast

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine import utils
from debusine.artifacts.local_artifact import AutopkgtestArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianAutopkgtest,
    DebianAutopkgtestResult,
    DebianAutopkgtestResultStatus,
    DebianAutopkgtestSource,
    DebianSourcePackage,
    DebianSystemImage,
    DebianSystemTarball,
    DebianUpload,
    get_source_package_name,
)
from debusine.tasks import (
    BaseTaskWithExecutor,
    ExtraRepositoryMixin,
    RunCommandTask,
)
from debusine.tasks.models import AutopkgtestData, AutopkgtestDynamicData
from debusine.tasks.server import TaskDatabaseInterface

log = logging.getLogger(__name__)

ParsedSummaryFile: TypeAlias = dict[str, DebianAutopkgtestResult]


class Autopkgtest(
    ExtraRepositoryMixin[AutopkgtestData, AutopkgtestDynamicData],
    RunCommandTask[AutopkgtestData, AutopkgtestDynamicData],
    BaseTaskWithExecutor[AutopkgtestData, AutopkgtestDynamicData],
):
    """Task to use autopkgtest in debusine."""

    TASK_VERSION = 1

    ARTIFACT_DIR = "artifact-dir"
    SUMMARY_FILE = "artifact-dir/summary"

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        self._source_package_name: str | None = None
        self._source_package_version: str | None = None
        self._source_package_url: str | None = None
        self._source_package_path: str | None = None

        self._parsed: ParsedSummaryFile | None = None
        self._autopkgtest_targets: list[Path] = []

    @classmethod
    def analyze_worker(cls) -> dict[str, Any]:
        """Report metadata for this task on this worker."""
        metadata = super().analyze_worker()

        available_key = cls.prefix_with_task_name("available")
        metadata[available_key] = utils.is_command_available("autopkgtest")

        return metadata

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """Check if the specified worker can run the task."""
        if not super().can_run_on(worker_metadata):
            return False

        executor_available_key = f"executor:{self.backend}:available"
        available_key = self.prefix_with_task_name("available")
        return bool(
            worker_metadata.get(executor_available_key, False)
            and worker_metadata.get(available_key, False)
        )

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> AutopkgtestDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: source package name out of ``input:source_artifact``
        :runtime_context: ``host_architecture:executor_backend``
        :configuration_context: codename of ``environment``
        """
        input_source_artifact = task_database.lookup_single_artifact(
            self.data.input.source_artifact
        )

        self.ensure_artifact_categories(
            configuration_key="input.source_artifact",
            category=input_source_artifact.category,
            expected=(ArtifactCategory.SOURCE_PACKAGE, ArtifactCategory.UPLOAD),
        )
        assert isinstance(
            input_source_artifact.data, (DebianSourcePackage, DebianUpload)
        )
        package_name = get_source_package_name(input_source_artifact.data)

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

        return AutopkgtestDynamicData(
            environment_id=environment.id,
            input_source_artifact_id=input_source_artifact.id,
            input_binary_artifacts_ids=task_database.lookup_multiple_artifacts(
                self.data.input.binary_artifacts
            ).get_ids(),
            input_context_artifacts_ids=task_database.lookup_multiple_artifacts(
                self.data.input.context_artifacts
            ).get_ids(),
            subject=package_name,
            runtime_context=f"{self.host_architecture()}:{self.backend}",
            configuration_context=environment.data.codename,
        )

    def check_directory_for_consistency_errors(
        self, build_directory: Path  # noqa: U100
    ) -> list[str]:
        """Autopkgtest ARTIFACT_DIR/summary file does not exist."""
        summary_path = build_directory / self.SUMMARY_FILE
        if not summary_path.exists():
            return [f"'{self.SUMMARY_FILE}' does not exist"]

        return []

    @staticmethod
    def _parse_summary_file(
        summary_file: Path,
    ) -> ParsedSummaryFile:
        """
        Parse autopkgtest summary file (from autopkgtest --summary).

        :param summary_file: file to parse.
        :return: dictionary with the result. Structure:
            .. code-block::
                {"test-name-1": {"status": "PASS"},
                 "test-name-2: {"status": "FAIL", "details": "partial"},
                }

            "status": always in the dictionary (PASS, FAIL, FLAKY, SKIP or
            any other status written by autopkgtest)
            "details": only in the dictionary if details are found
            The test-name may be "*" if it wasn't known.
            If there's a testbed failure, the dictionary will be empty.
        :raises: ValueError if a line cannot be parsed
        """  # noqa: RST301
        parsed = {}
        with summary_file.open() as file:
            for line in file.readlines():
                line = line.rstrip()

                m = re.match(r"(?P<error>[a-z ]+): (?P<details>.*)", line)
                if m is not None:
                    log.info(
                        "Autopkgtest error: %s: %s",
                        m.group("error"),
                        m.group("details"),
                    )
                    continue

                m = re.match(
                    r"(?P<name>\S+)\s+(?P<status>\S+)"
                    r"(?:\s+(?P<details>.*))?",
                    line,
                )

                if m is None:
                    raise ValueError(f"Failed to parse line: {line}")

                name = m.group("name")
                result = m.group("status")

                if result not in ("PASS", "FAIL", "SKIP", "FLAKY"):
                    raise ValueError(f"Line with unexpected result: {line}")

                parsed[name] = DebianAutopkgtestResult(
                    status=DebianAutopkgtestResultStatus(result)
                )

                if details := m.group("details"):
                    parsed[name].details = details

        return parsed

    def _environment_codename(self) -> str:
        """Return the codename of the environment we're testing on."""
        if self.executor is None:
            raise ValueError(
                "self.executor not set, call _prepare_executor() first"
            )
        return cast(str, self.executor.system_image.data["codename"])

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data

        artifact = self.fetch_artifact(
            self.dynamic_data.input_source_artifact_id, destination
        )

        for file_path, file_data in artifact.files.items():
            if file_path.endswith(".dsc"):
                self._source_package_url = file_data.url
                self._source_package_path = file_path
                break

        for artifact_id in self.dynamic_data.input_binary_artifacts_ids:
            self.fetch_artifact(artifact_id, destination)

        for artifact_id in self.dynamic_data.input_context_artifacts_ids:
            self.fetch_artifact(artifact_id, destination)

        self._prepare_executor()
        self.write_extra_repository_config(
            codename=self._environment_codename(),
            destination=destination,
        )

        return True

    def _cmdline(self) -> list[str]:
        """
        Return autopkgtest command line (idempotent).

        Use configuration of self.data.
        """
        if not self.executor:
            raise AssertionError(
                "self.executor not set - self._prepare_for_execution() "
                "must be called before _cmdline()"
            )

        cmd = [
            "autopkgtest",
            "--apt-upgrade",
            f"--output-dir={self.ARTIFACT_DIR}",
            f"--summary={self.SUMMARY_FILE}",
            "--no-built-binaries",
        ]

        for include_test in self.data.include_tests:
            cmd.append(f"--test-name={include_test}")

        for exclude_test in self.data.exclude_tests:
            cmd.append(f"--skip-test={exclude_test}")

        if debug_level := self.data.debug_level:
            cmd.append("-" + "d" * debug_level)

        cmd += self._cmdline_extra_repository()

        if self.data.use_packages_from_base_repository:
            release = self._environment_codename()
            cmd.append(f"--apt-default-release={release}")

        for variable, value in self.data.extra_environment.items():
            cmd.append(f"--env={variable}={value}")

        cmd.append(f"--needs-internet={self.data.needs_internet}")

        if self.data.timeout is not None:
            for key, timeout in self.data.timeout.dict(by_alias=True).items():
                if timeout is not None:
                    cmd.append(f"--timeout-{key}={timeout}")

        if self.backend == "unshare" and Path("/etc/resolv.conf").is_file():
            # autopkgtest >= 5.31 copies /etc/resolv.conf from the host, but
            # earlier versions don't.  Force this, since we can't rely on
            # resolver configuration built into the system tarball.
            cmd.append("--copy=/etc/resolv.conf:/etc/resolv.conf")

        cmd.extend(map(str, self._autopkgtest_targets))

        cmd.append("--")
        cmd.append(self.executor.autopkgtest_virt_server())
        cmd.extend(self.executor.autopkgtest_virt_args())

        return cmd

    def _cmdline_extra_repository(self) -> list[str]:
        """Generate command line arguments for extra_repositories."""
        args: list[str] = []
        if self.supports_deb822_sources(self._environment_codename()):
            # autopkgtest has native deb822 support coming soon: #1089736
            for path in self.extra_repository_sources:
                args.append(
                    f"--copy={path}:/etc/apt/sources.list.d/{path.name}"
                )
        else:
            for source in self.iter_oneline_sources():
                args.append(f"--add-apt-source={source}")

        # apt < 2.3.10 has no support for keys embedded in Signed-By
        for path in self.extra_repository_keys:
            args.append(f"--copy={path}:/etc/apt/keyrings/{path.name}")
        return args

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """Gather information used later on (_cmdline(), upload_artifacts())."""
        # Not yet strictly guaranteed, because the source artifact might be
        # of the wrong type; see
        # https://salsa.debian.org/freexian-team/debusine/-/issues/207.
        assert self._source_package_path is not None

        # Used by upload_artifacts()
        dsc_file = download_directory / self._source_package_path
        dsc = utils.read_dsc(dsc_file)

        if dsc is None:
            self.append_to_log_file(
                "configure_for_execution.log",
                [f"{self._source_package_path} is not a valid .dsc file"],
            )
            return False

        self._source_package_name = dsc["source"]
        self._source_package_version = dsc["version"]

        # Used by _cmdline():
        self._autopkgtest_targets = utils.find_files_suffixes(
            download_directory, [".deb"]
        )
        self._autopkgtest_targets.append(dsc_file)

        return True

    def task_succeeded(
        self, returncode: int | None, execute_directory: Path  # noqa: U100
    ) -> bool:
        """
        Parse the summary file and return success.

        Use self.data.fail_on.
        """
        self._parsed = self._parse_summary_file(
            execute_directory / self.SUMMARY_FILE
        )

        fail_on = self.data.fail_on

        for result in self._parsed.values():
            if (
                (result.status == "FAIL" and fail_on.failed_test)
                or (result.status == "FLAKY" and fail_on.flaky_test)
                or (result.status == "SKIP" and fail_on.skipped_test)
            ):
                return False

        if returncode == 4 and not fail_on.failed_test:
            return True

        if returncode == 2 and fail_on.flaky_test:
            return False

        if returncode == 8 and fail_on.skipped_test:
            return False

        # Return True if autopkgtest has run successfully.
        # 0 all tests passed
        # 2 at least one test was skipped (or at least one flaky test failed)
        # 8 no tests in this package, or all non-superficial tests were skipped
        return returncode in {0, 2, 8}

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload AutopkgtestArtifact with the files, data and relationships."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        if not self.executor:
            raise AssertionError(
                "self.executor not set - self._prepare_for_execution() "
                "must be called before upload_artifacts()"
            )

        img_data = self.executor.system_image.data

        assert self._parsed is not None
        assert self._source_package_path is not None
        assert self._source_package_name is not None
        assert self._source_package_version is not None
        assert self._source_package_url is not None

        autopkgtest_artifact = AutopkgtestArtifact.create(
            execute_directory / self.ARTIFACT_DIR,
            DebianAutopkgtest(
                results=self._parsed,
                cmdline=shlex.join(self._cmdline()),
                source_package=DebianAutopkgtestSource(
                    name=self._source_package_name,
                    version=self._source_package_version,
                    url=pydantic.parse_obj_as(
                        pydantic.AnyUrl, self._source_package_url
                    ),
                ),
                architecture=self.data.host_architecture,
                distribution=f"{img_data['vendor']}:{img_data['codename']}",
            ),
        )

        self.debusine.upload_artifact(
            autopkgtest_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

    def get_label(self) -> str:
        """Return the task label."""
        return (
            f"autopkgtest {subject}"
            if (subject := self.get_subject())
            else "autopkgtest"
        )
