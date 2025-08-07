# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unpack source package and run dpkg-genchanges on it."""
from pathlib import Path
from typing import Any

import debusine.utils
from debusine.artifacts import Upload
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSourcePackage,
    get_source_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseTaskWithExecutor, RunCommandTask
from debusine.tasks.models import (
    MakeSourcePackageUploadData,
    MakeSourcePackageUploadDynamicData,
)
from debusine.tasks.server import TaskDatabaseInterface


class MakeSourcePackageUpload(
    RunCommandTask[
        MakeSourcePackageUploadData, MakeSourcePackageUploadDynamicData
    ],
    BaseTaskWithExecutor[
        MakeSourcePackageUploadData, MakeSourcePackageUploadDynamicData
    ],
):
    """Makes a debian:upload artifact from a debian:source-package artifact."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize (constructor)."""
        super().__init__(task_data, dynamic_task_data)
        self._dsc_path: Path
        self._changes_path: Path
        self._shell_script: Path

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> MakeSourcePackageUploadDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: source package name of ``input.source_artifact``
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
        package_name = get_source_package_name(input_source_artifact.data)

        return MakeSourcePackageUploadDynamicData(
            environment_id=self.get_environment(
                task_database,
                self.data.environment,
                default_category=CollectionCategory.ENVIRONMENTS,
            ).id,
            input_source_artifact_id=input_source_artifact.id,
            subject=package_name,
        )

    def fetch_input(self, destination: Path) -> bool:
        """Populate work directory with user-specified source artifact."""
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

        self.fetch_artifact(source_artifact_id, destination)

        return True

    def configure_for_execution(self, download_directory: Path) -> bool:
        r"""
        Find the .dsc file for dpkg-source and dpkg-genchanges.

        Set self._dsc_file to the relevant file.
        Set self._changes_path to the target file.
        Set self._shell_script to a copy of an integration Bash script.

        :param download_directory: where to find the .dsc file
          (downloaded via fetch_input)

        :return: True if valid files were found
        """
        # Find the file to process or early exit if no files
        dsc_files = debusine.utils.find_files_suffixes(
            download_directory, [".dsc"]
        )
        # Ensure we've got 1 .dsc file, see:
        # debusine.artifacts.local_artifacts.SourcePackage.files_contain_one_dsc
        assert len(dsc_files) == 1

        self._dsc_path = dsc_files[0]
        # .changes in the same path as the .dsc for Upload.create()
        self._changes_path = self._dsc_path.with_name(
            self._dsc_path.stem + "_source.changes"
        )

        # Prepare a Bash script: we need to chdir which currently
        # isn't supported (#434), and run multiple commands in _cmdline().
        # Pass files as arguments to avoid shell escaping hell.
        self._shell_script = self._dsc_path.with_name(
            "makesourcepackageupload.sh"
        )
        with open(self._shell_script, "w") as f:
            f.write(
                """#!/bin/bash
download_directory=$(dirname "$1")
cd "$download_directory/"
dpkg-source -x "$1" package/
cd package/
[ -n "$3" ] && optional_args+=("-v$3")
[ -n "$4" ] && optional_args+=("-DDistribution=$4")
dpkg-genchanges -S -sa "${optional_args[@]}" > "$2"
ls -lh "$2"
"""
            )

        self._prepare_executor_instance()
        return True

    def _cmdline(self) -> list[str]:
        """Build full makesourcepackageupload.sh command line."""
        return [
            "bash",
            "-x",  # trace commands for debug log
            "-e",  # safely stop on error
            str(self._shell_script),
            str(self._dsc_path),
            str(self._changes_path),
            self.data.since_version or "",
            self.data.target_distribution or "",
        ]

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Create DebianUpload artifact and relationships."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")
        assert self.dynamic_data
        assert self.executor_instance

        self.executor_instance.file_pull(self._changes_path, self._changes_path)

        changes_artifact = Upload.create(
            changes_file=self._changes_path,
        )
        changes_uploaded = self.debusine.upload_artifact(
            changes_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

        self.debusine.relation_create(
            changes_uploaded.id,
            self.dynamic_data.input_source_artifact_id,
            RelationType.EXTENDS,
        )
        self.debusine.relation_create(
            changes_uploaded.id,
            self.dynamic_data.input_source_artifact_id,
            RelationType.RELATES_TO,
        )

    def get_label(self) -> str:
        """Return the task label."""
        return "prepare source package upload"
