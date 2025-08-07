# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Merge the ``.changes`` files from multiple uploads."""

import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from debian.deb822 import Changes

import debusine.utils
from debusine.artifacts import Upload
from debusine.artifacts.models import (
    ArtifactCategory,
    DebianUpload,
    get_source_package_name,
)
from debusine.client.models import RelationType
from debusine.tasks import BaseExternalTask
from debusine.tasks.models import MergeUploadsData, MergeUploadsDynamicData
from debusine.tasks.server import TaskDatabaseInterface


class MergeUploadsError(Exception):
    """An error raised while merging upload artifacts."""


class MergeUploads(BaseExternalTask[MergeUploadsData, MergeUploadsDynamicData]):
    """
    Combines multiple debian:upload artifacts into a single one.

    This is in preparation for uploading them together.
    """

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize (constructor)."""
        super().__init__(task_data, dynamic_task_data)
        self._changes_paths: list[Path] = []
        self._upload_artifact: Upload | None = None

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> MergeUploadsDynamicData:
        """
        Resolve artifact lookups for this task.

        :subject: shared source package name across all uploads
        """
        upload_artifacts = task_database.lookup_multiple_artifacts(
            self.data.input.uploads
        )

        source_package_names = set()
        for upload_artifact in upload_artifacts:
            self.ensure_artifact_categories(
                configuration_key="input.uploads",
                category=upload_artifact.category,
                expected=(ArtifactCategory.UPLOAD,),
            )
            assert isinstance(upload_artifact.data, DebianUpload)

            source_package_names.add(
                get_source_package_name(upload_artifact.data)
            )

        subject = (
            source_package_names.pop()
            if len(source_package_names) == 1
            else None
        )

        return MergeUploadsDynamicData(
            input_uploads_ids=upload_artifacts.get_ids(), subject=subject
        )

    def fetch_input(self, destination: Path) -> bool:
        """Populate work directory with user-specified binary artifact(s)."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")
        assert self.dynamic_data

        for upload_id in self.dynamic_data.input_uploads_ids:
            artifact = self.debusine.artifact_get(upload_id)
            if artifact.category != ArtifactCategory.UPLOAD:
                self.append_to_log_file(
                    "fetch_input.log",
                    [
                        f"input.uploads points to a "
                        f"{artifact.category}, not the expected "
                        f"{ArtifactCategory.UPLOAD}."
                    ],
                )
                return False
            self.fetch_artifact(upload_id, destination)

        return True

    def configure_for_execution(self, download_directory: Path) -> bool:
        r"""
        Find the .changes files to merge.

        Set self._changes_paths to the relevant files.

        :param download_directory: where to find the .dsc file
          (downloaded via fetch_input)

        :return: True if valid files were found
        """
        # Find the files to merge or early exit if not files
        self._changes_paths = debusine.utils.find_files_suffixes(
            download_directory, [".changes"]
        )
        # Ensure we've got 1 .changes file per upload, see:
        # debusine.artifacts.local_artifacts.Upload.files_contain_changes
        assert len(self._changes_paths) >= 1

        return True

    @staticmethod
    def _read_changes(path: Path) -> Changes:
        """Read a .changes file."""
        with open(path) as f:
            return Changes(f)

    @staticmethod
    def _check_simple_fields(all_changes: Sequence[Changes]) -> None:
        """Check whether simple fields in some .changes files are consistent."""
        if all_changes[0]["Format"] != "1.8":
            raise MergeUploadsError(
                f"Unknown .changes format: {all_changes[0]['Format']}"
            )

        for field in ("Format", "Source", "Version"):
            values = [changes[field] for changes in all_changes]
            if len(set(values)) != 1:
                raise MergeUploadsError(
                    f"{field} fields do not match: {values}"
                )

    @staticmethod
    def _check_descriptions(all_changes: Sequence[Changes]) -> None:
        """Check that descriptions in some .changes files are consistent."""
        changes_description_re = re.compile(r"^ ([^ ]+) - (.+)")
        descriptions: dict[str, str] = {}
        for changes in all_changes:
            for description_line in changes.get("Description", "").splitlines():
                if not description_line:
                    continue
                elif m := changes_description_re.match(description_line):
                    name, description = m.groups()
                    if (
                        name in descriptions
                        and descriptions[name] != description
                    ):
                        raise MergeUploadsError(
                            f"Descriptions for {name} do not match: "
                            f"{descriptions[name]!r} != {description!r}"
                        )
                    descriptions[name] = description

    @staticmethod
    def _check_checksums(all_changes: Sequence[Changes]) -> None:
        """Check that checksums in some .changes files are consistent."""
        checksums: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
        for changes in all_changes:
            for field in changes:
                if field.lower().startswith(
                    "checksums-"
                ) and field.lower() not in {
                    "checksums-sha1",
                    "checksums-sha256",
                }:
                    raise MergeUploadsError(
                        f"Unsupported checksum field: {field}"
                    )

            for field in ("Files", "Checksums-Sha1", "Checksums-Sha256"):
                for checksum in changes.get(field, []):
                    name = checksum["name"]
                    if (
                        name in checksums[field]
                        and checksums[field][name] != checksum
                    ):
                        raise MergeUploadsError(
                            f"Entries in {field} for {name} do not match: "
                            f"{checksums[field][name]} != {checksum}"
                        )
                    checksums[field][name] = checksum

    def _merge_descriptions(self, merged: Changes, to_merge: Changes) -> None:
        """
        Merge Description from ``to_merge`` into ``merged``.

        Works around a bug fixed here:
        https://salsa.debian.org/python-debian-team/python-debian/-/merge_requests/148
        """
        if to_merge.get("Description"):
            if merged.get("Description"):
                for item in to_merge["Description"].splitlines():
                    if item not in merged["Description"].splitlines():
                        merged["Description"] += "\n" + item
            else:
                merged["Description"] = to_merge["Description"]

    def merge_changes(self, all_changes: Sequence[Changes]) -> Changes:
        """Merge some .changes files."""
        self._check_simple_fields(all_changes)
        self._check_descriptions(all_changes)
        self._check_checksums(all_changes)

        merged = all_changes[0]
        for to_merge in all_changes[1:]:
            merged.merge_fields("Binary", to_merge)
            merged.merge_fields("Architecture", to_merge)
            self._merge_descriptions(merged, to_merge)

            for field in ("Files", "Checksums-Sha1", "Checksums-Sha256"):
                existing = {
                    tuple(checksum.items()) for checksum in merged[field]
                }
                for checksum in to_merge[field]:
                    assert tuple(checksum.items()) not in existing
                    merged[field].append(checksum)

        merged.order_before("Binary", "Source")
        merged.order_before("Description", "Changes")

        return merged

    def make_upload_artifact(self, merged: Changes) -> Upload:
        """Make an Upload artifact from a merged .changes file."""
        # Write merged .changes file to the same directory as the files it
        # references so that Upload can validate them, named according to
        # the same "multi" suffix convention as mergechanges(1) from
        # devscripts.
        ref_path = self._changes_paths[0]
        version_without_epoch = re.sub(r"^\d+:", "", merged["Version"])
        merged_changes_path = ref_path.with_name(
            f"{merged['Source']}_{version_without_epoch}_multi.changes"
        )
        with open(merged_changes_path, "w") as f:
            merged.dump(f, text_mode=True)
        return Upload.create(changes_file=merged_changes_path)

    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Do the main work of the task."""
        all_changes = [self._read_changes(path) for path in self._changes_paths]
        merged = self.merge_changes(all_changes)
        self._upload_artifact = self.make_upload_artifact(merged)
        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Create DebianUpload artifact and relationships."""
        if not self.debusine:
            raise AssertionError("self.debusine not set")
        assert self.dynamic_data
        assert self._upload_artifact is not None

        changes_uploaded = self.debusine.upload_artifact(
            self._upload_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

        for input_upload_id in self.dynamic_data.input_uploads_ids:
            self.debusine.relation_create(
                changes_uploaded.id,
                input_upload_id,
                RelationType.EXTENDS,
            )

    def get_label(self) -> str:
        """Return the task label."""
        return "merge package uploads"
