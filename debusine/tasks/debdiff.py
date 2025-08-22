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
from tempfile import mkdtemp
from typing import Any

from debusine import utils
from debusine.artifacts import DebDiffArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianSourcePackage,
    DebianUpload,
    get_source_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask, TaskConfigError
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

        # list of directories that fetch_input() downloaded the artifacts
        self._original_dirs: list[Path] = []
        self._new_targets_dirs: list[Path] = []

        # list of files that configure_for_execution() found to pass to debdiff
        self._original_targets: list[Path] = []
        self._new_targets: list[Path] = []

    @staticmethod
    def _validate_binary_original_new_count(
        original_count: int, new_count: int
    ) -> None:
        """
        Validate original_count and new_count for binary artifacts.

        Raise TaskConfigError for invalid combinations.
        """
        if original_count == 0 or new_count == 0:
            raise TaskConfigError(
                f"input.binary_artifacts[0] and input.binary_artifacts[1] "
                f"cannot have zero artifacts "
                f"(got {original_count} and {new_count})"
            )

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

            if any(artifact is None for artifact in source_artifacts):
                raise TaskConfigError(
                    f"Could not look up one of source_artifacts. "
                    f"Source artifacts lookup result: {source_artifacts}"
                )

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
            binary_artifacts_original = task_database.lookup_multiple_artifacts(
                self.data.input.binary_artifacts[0]
            )
            binary_artifacts_new = task_database.lookup_multiple_artifacts(
                self.data.input.binary_artifacts[1]
            )

            self._validate_binary_original_new_count(
                len(binary_artifacts_original), len(binary_artifacts_new)
            )

            source_package_names = set()

            binary_artifact_category = None
            for multiple_artifacts in [
                binary_artifacts_original,
                binary_artifacts_new,
            ]:
                binary_artifacts_ids.append(multiple_artifacts.get_ids())

                for artifact in multiple_artifacts:
                    # TODO: improve configuration_key including the index?
                    self.ensure_artifact_categories(
                        configuration_key="input.binary_artifacts",
                        category=artifact.category,
                        expected=(
                            ArtifactCategory.UPLOAD,
                            ArtifactCategory.BINARY_PACKAGE,
                        ),
                    )

                    assert isinstance(
                        artifact.data,
                        (DebianUpload, DebianBinaryPackage),
                    )
                    if binary_artifact_category is None:
                        binary_artifact_category = artifact.category

                    if binary_artifact_category != artifact.category:
                        raise TaskConfigError(
                            f'All binary artifacts must have the same '
                            f'category. Found "{binary_artifact_category}" '
                            f'and "{artifact.category}"'
                        )

                    source_package_names.add(
                        get_source_package_name(artifact.data)
                    )

                if (
                    binary_artifact_category != ArtifactCategory.BINARY_PACKAGE
                    and len(multiple_artifacts) > 1
                ):
                    raise TaskConfigError(
                        "If binary_artifacts source and new contain more than "
                        "one artifact all must be of category "
                        "debian:binary-package. Found: "
                        f"{binary_artifact_category}"
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

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
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

        cmd.extend(self.data.extra_flags)

        original_targets = map(str, self._original_targets)
        new_targets = map(str, self._new_targets)

        use_from_notation = self._original_targets[0].suffix in (
            ".deb",
            ".udeb",
        )

        if use_from_notation:
            cmd += ["--from", *original_targets, "--to", *new_targets]
        else:
            cmd += [*original_targets, *new_targets]

        return cmd

    def _fetch_artifact_to_temporary(
        self, artifact_id: int, directory: Path
    ) -> Path:
        artifact_directory = Path(
            mkdtemp(dir=directory, prefix=f"{artifact_id}_")
        )
        self.fetch_artifact(artifact_id, artifact_directory)

        return artifact_directory

    def fetch_input(self, destination: Path) -> bool:
        """Download the required artifacts."""
        assert self.dynamic_data

        if self.dynamic_data.input_source_artifacts_ids:
            assert len(self.dynamic_data.input_source_artifacts_ids) == 2
            self._original_dirs.append(
                self._fetch_artifact_to_temporary(
                    self.dynamic_data.input_source_artifacts_ids[0], destination
                )
            )

            self._new_targets_dirs.append(
                self._fetch_artifact_to_temporary(
                    self.dynamic_data.input_source_artifacts_ids[1], destination
                )
            )

        if self.dynamic_data.input_binary_artifacts_ids:
            assert len(self.dynamic_data.input_binary_artifacts_ids) == 2

            for artifact_id in self.dynamic_data.input_binary_artifacts_ids[0]:
                self._original_dirs.append(
                    self._fetch_artifact_to_temporary(artifact_id, destination)
                )

            for artifact_id in self.dynamic_data.input_binary_artifacts_ids[1]:
                self._new_targets_dirs.append(
                    self._fetch_artifact_to_temporary(artifact_id, destination)
                )

        return True

    @staticmethod
    def _find_file(path: Path) -> Path:

        file = utils.find_file_suffixes(path, [".changes"])

        if file is None:
            file = utils.find_file_suffixes(path, [".deb", ".udeb", ".dsc"])

        # file cannot be None because we ensure the artifact category and
        # artifacts are previously validated
        assert file is not None

        return file

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """
        Set self._(original|new)_targets to the relevant files.

        Use self._original_dirs and self._new_dirs for finding the specific
        targets for debdiff command.

        :param download_directory: unused
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
            [
                "apt-get",
                "--yes",
                "--no-install-recommends",
                "install",
                "devscripts",
                "diffstat",  # for --diffstat
                "patchutils",  # used to compare .diff.gz files
                "wdiff",  # for --wdiff-source-control
            ],
            run_as_root=True,
            check=True,
        )

        for original_dir in self._original_dirs:
            self._original_targets.append(self._find_file(original_dir))

        for target_dir in self._new_targets_dirs:
            self._new_targets.append(self._find_file(target_dir))

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
