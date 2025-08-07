# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to use blhc in debusine."""

from pathlib import Path
from typing import Any

from debusine import utils
from debusine.artifacts import BlhcArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianPackageBuildLog,
    get_source_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask
from debusine.tasks.models import BlhcData, BlhcDynamicData
from debusine.tasks.server import TaskDatabaseInterface


class Blhc(
    RunCommandTask[BlhcData, BlhcDynamicData],
    BaseTaskWithExecutor[BlhcData, BlhcDynamicData],
):
    """Task to use blhc (build-log hardening check) in debusine."""

    TASK_VERSION = 1

    CAPTURE_OUTPUT_FILENAME = "blhc.txt"

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        # list of build logs to be checked
        self._blhc_target: Path | None = None

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> BlhcDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: source package name out of the analyzed build log
        """
        input_artifact = task_database.lookup_single_artifact(
            self.data.input.artifact
        )

        self.ensure_artifact_categories(
            configuration_key="input.artifact",
            category=input_artifact.category,
            expected=(ArtifactCategory.PACKAGE_BUILD_LOG,),
        )
        assert isinstance(input_artifact.data, DebianPackageBuildLog)
        package_name = get_source_package_name(input_artifact.data)

        return BlhcDynamicData(
            environment_id=(
                None
                if self.data.environment is None
                else self.get_environment(
                    task_database,
                    self.data.environment,
                    default_category=CollectionCategory.ENVIRONMENTS,
                ).id
            ),
            input_artifact_id=input_artifact.id,
            subject=package_name,
        )

    def _cmdline(self) -> list[str]:
        """
        Build the blhc command line.

        Use configuration of self.data and self.blhc_target.
        """
        cmd = [
            "blhc",
            "--debian",
        ]

        if extra_flags := self.data.extra_flags:
            for flag in extra_flags:
                # we already checked that the flags are sane...
                cmd.append(flag)

        cmd.append(str(self._blhc_target))

        return cmd

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data

        self.fetch_artifact(self.dynamic_data.input_artifact_id, destination)

        return True

    def configure_for_execution(self, download_directory: Path) -> bool:
        """
        Find .build files in the input artifacts.

        Set self._blhc_target to the relevant file.

        :param download_directory: where to search the files
        :return: True if valid files were found
        """
        self._prepare_executor_instance()

        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")

        self.executor_instance.run(
            ["apt-get", "update"], run_as_root=True, check=True
        )
        self.executor_instance.run(
            ["apt-get", "--yes", "install", "blhc"],
            run_as_root=True,
            check=True,
        )

        build_files = utils.find_files_suffixes(download_directory, [".build"])

        if len(build_files) == 0:
            list_of_files = sorted(map(str, download_directory.iterdir()))
            self.append_to_log_file(
                "configure_for_execution.log",
                [
                    f"No *.build files to be analyzed. "
                    f"Files: {list_of_files}"
                ],
            )
            return False

        self._blhc_target = build_files[0]

        return True

    def upload_artifacts(
        self, exec_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload the BlhcArtifact with the files and relationships."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        blhc_file = exec_directory / self.CAPTURE_OUTPUT_FILENAME

        blhc_artifact = BlhcArtifact.create(blhc_output=blhc_file)

        uploaded = self.debusine.upload_artifact(
            blhc_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

        for source_artifact_id in self._source_artifacts_ids:
            self.debusine.relation_create(
                uploaded.id, source_artifact_id, RelationType.RELATES_TO
            )

    def task_succeeded(
        self, returncode: int | None, execute_directory: Path  # noqa: U100
    ) -> bool:
        """
        Evaluate task output and return success.

        We don't actually check the output, but use the return code of
        blhc.

        :return: True for success, False failure.
        """
        return returncode in [0, 1]

    def get_label(self) -> str:
        """Return the task label."""
        # TODO: copy the source package information in dynamic task data and
        # use them here if available
        return "blhc"
