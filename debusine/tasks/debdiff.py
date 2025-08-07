# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to use debdiff in debusine."""
from itertools import chain
from pathlib import Path
from typing import Any

from debusine import utils
from debusine.artifacts import DebDiffArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSourcePackage,
    DebianUpload,
    get_source_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask
from debusine.tasks.models import DebDiffData, DebDiffDynamicData
from debusine.tasks.server import TaskDatabaseInterface


class DebDiff(
    RunCommandTask[DebDiffData, DebDiffDynamicData],
    BaseTaskWithExecutor[DebDiffData, DebDiffDynamicData],
):
    """Task to use debdiff in debusine."""

    TASK_VERSION = 1

    CAPTURE_OUTPUT_FILENAME = "debdiff.txt"

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        # list of packages to be diffed
        self._original_targets: list[Path] = []
        self._new_targets: list[Path] = []

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> DebDiffDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: ``source:NAME_OF_PACKAGE`` if ``input.source_artifacts``
          is set, otherwise ``binary:NAME_OF_PACKAGE`` if all binary artifacts
          in ``input.binary_artifacts`` originate from the same source package
        """
        subject = None
        source_artifacts_ids = []

        if self.data.input.source_artifacts:
            source_artifacts = [
                task_database.lookup_single_artifact(artifact)
                for artifact in self.data.input.source_artifacts
            ]

            for source_artifact in source_artifacts:
                # TODO: improve configuration_key including the index?
                self.ensure_artifact_categories(
                    configuration_key="input.source_artifacts",
                    category=source_artifact.category,
                    expected=(ArtifactCategory.SOURCE_PACKAGE,),
                )

                if subject is None:
                    assert isinstance(source_artifact.data, DebianSourcePackage)

                    package_name = get_source_package_name(source_artifact.data)
                    subject = f"source:{package_name}"

                source_artifacts_ids.append(source_artifact.id)

        binary_artifacts_ids = []

        if self.data.input.binary_artifacts:
            binary_artifacts = [
                task_database.lookup_multiple_artifacts(artifact)
                for artifact in self.data.input.binary_artifacts
            ]

            source_package_names = set()

            for binary_artifact in binary_artifacts:
                binary_artifacts_ids.append(binary_artifact.get_ids())

                for artifact in binary_artifact:
                    # TODO: improve configuration_key including the index?
                    self.ensure_artifact_categories(
                        configuration_key="input.binary_artifacts",
                        category=artifact.category,
                        expected=(ArtifactCategory.UPLOAD,),
                    )

                    assert isinstance(
                        artifact.data,
                        (DebianUpload,),
                    )
                    source_package_names.add(
                        get_source_package_name(artifact.data)
                    )

            if subject is None and len(source_package_names) == 1:
                subject = f"binary:{source_package_names.pop()}"

        return DebDiffDynamicData(
            environment_id=self.get_environment(
                task_database,
                self.data.environment,
                default_category=CollectionCategory.ENVIRONMENTS,
            ).id,
            input_source_artifacts_ids=source_artifacts_ids or None,
            input_binary_artifacts_ids=binary_artifacts_ids or None,
            subject=subject,
        )

    def get_source_artifacts_ids(self) -> list[int]:
        """Return the list of source artifact IDs used by this task."""
        if self.dynamic_data is None:
            return []

        input_ids = self.dynamic_data.input_source_artifacts_ids or []
        binary_ids = self.dynamic_data.input_binary_artifacts_ids or []

        return list(chain(input_ids, *binary_ids))

    def _cmdline(self) -> list[str]:
        """
        Build the debdiff command line.

        Use configuration of self.data and self.debdiff_target.
        """
        cmd = [
            "debdiff",
        ]

        if extra_flags := self.data.extra_flags:
            for flag in extra_flags:
                # we already checked that the flags are sane...
                cmd.append(flag)

        cmd.extend(str(path) for path in self._original_targets)
        cmd.extend(str(path) for path in self._new_targets)

        return cmd

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data

        (original_directory := destination / "original").mkdir()
        (new_directory := destination / "new").mkdir()

        if self.dynamic_data.input_source_artifacts_ids:
            assert len(self.dynamic_data.input_source_artifacts_ids) == 2
            self.fetch_artifact(
                self.dynamic_data.input_source_artifacts_ids[0],
                original_directory,
            )
            self.fetch_artifact(
                self.dynamic_data.input_source_artifacts_ids[1],
                new_directory,
            )

        if self.dynamic_data.input_binary_artifacts_ids:
            assert len(self.dynamic_data.input_binary_artifacts_ids) == 2
            for artifact_id in self.dynamic_data.input_binary_artifacts_ids[0]:
                self.fetch_artifact(artifact_id, original_directory)
            for artifact_id in self.dynamic_data.input_binary_artifacts_ids[1]:
                self.fetch_artifact(artifact_id, new_directory)

        return True

    def configure_for_execution(self, download_directory: Path) -> bool:
        """
        Set self._(original|new)_targets to the relevant files.

        :param download_directory: where to search the files
        :return: True if valid files were found
        """
        self._prepare_executor_instance()

        if self.executor_instance is None:
            raise AssertionError("self.executor_instance cannot be None")

        assert self.dynamic_data is not None

        self.executor_instance.run(
            ["apt-get", "update"], run_as_root=True, check=True
        )
        self.executor_instance.run(
            # diffstat needed for the flag --diffstat
            ["apt-get", "--yes", "install", "devscripts", "diffstat"],
            run_as_root=True,
            check=True,
        )

        file_ending = ".dsc"
        if self.dynamic_data.input_binary_artifacts_ids:
            file_ending = ".changes"

        original_files = utils.find_files_suffixes(
            download_directory / "original", [file_ending]
        )

        if len(original_files) == 0:
            list_of_files = sorted(
                map(str, (download_directory / "original").iterdir())
            )
            self.append_to_log_file(
                "configure_for_execution.log",
                [
                    f"No original *{file_ending} file to be analyzed. "
                    f"Files: {list_of_files}"
                ],
            )
            return False

        self._original_targets.append(original_files[0])

        new_files = utils.find_files_suffixes(
            download_directory / "new", [file_ending]
        )

        if len(new_files) == 0:
            list_of_files = sorted(
                map(str, (download_directory / "new").iterdir())
            )
            self.append_to_log_file(
                "configure_for_execution.log",
                [
                    f"No new *{file_ending} file to be analyzed. "
                    f"Files: {list_of_files}"
                ],
            )
            return False

        self._new_targets.append(new_files[0])

        return True

    def upload_artifacts(
        self, exec_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload the DebDiffArtifact with the files and relationships."""
        assert self.dynamic_data
        if not self.debusine:
            raise AssertionError("self.debusine not set")

        debdiff_file = exec_directory / self.CAPTURE_OUTPUT_FILENAME

        original = " ".join(path.name for path in self._original_targets)
        new = " ".join(path.name for path in self._new_targets)
        debdiff_artifact = DebDiffArtifact.create(
            debdiff_output=debdiff_file, original=original, new=new
        )

        uploaded = self.debusine.upload_artifact(
            debdiff_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

        for source_artifact_id in self._source_artifacts_ids:
            self.debusine.relation_create(
                uploaded.id, source_artifact_id, RelationType.RELATES_TO
            )

        if (
            binary_artifacts_ids := self.dynamic_data.input_binary_artifacts_ids
        ) is not None:
            for binary_artifact_id in chain.from_iterable(binary_artifacts_ids):
                self.debusine.relation_create(
                    uploaded.id, binary_artifact_id, RelationType.RELATES_TO
                )

    def task_succeeded(
        self, returncode: int | None, execute_directory: Path  # noqa: U100
    ) -> bool:
        """
        Evaluate task output and return success.

        We don't actually check the output, but use the return code of
        debdiff.

        :return: True for success, False failure.
        """
        return returncode in [0, 1]

    def get_label(self) -> str:
        """Return the task label."""
        # TODO: copy the source package information in dynamic task data and
        # use them here if available
        return "debdiff"
