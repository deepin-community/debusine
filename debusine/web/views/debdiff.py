# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""View for DebDiff work request."""
import re
from collections import defaultdict
from enum import StrEnum
from typing import Any, TypedDict, cast

from unidiff import PatchSet

from debusine.artifacts.models import ArtifactCategory, TaskTypes
from debusine.db.models import ArtifactRelation, FileInArtifact
from debusine.tasks import DebDiff
from debusine.web.views.artifacts import ArtifactPlugin
from debusine.web.views.files import FileWidget
from debusine.web.views.work_request import WorkRequestPlugin


class OperationEnum(StrEnum):
    """Operations that are done in a file in the patch."""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class DebDiffFileSummary(TypedDict):
    """Summary of a file-level change in a source debdiff."""

    path: str
    diff_line_number: int
    operation: OperationEnum


class DebDiffSection(StrEnum):
    """Sections of a debdiff output."""

    ADDED_FILES = "added_files"
    REMOVED_FILES = "removed_files"
    CONTROL_DIFFS = "control_diffs"
    CONTROL_DIFFS_PACKAGES = "control_diffs_packages"
    CONTROL_DIFFS_PACKAGES_NO_CHANGES = "control_diffs_packages_no_changes"


class DebDiffBinaryResult(TypedDict):
    """Defines the result structure of a parsed debdiff binary section."""

    added_files: str
    removed_files: str
    control_diffs: str
    control_diffs_packages: dict[str, str]
    control_diffs_packages_no_changes: list[str]


class DebDiffView(ArtifactPlugin):
    """View for DebDiff Artifact."""

    artifact_category = ArtifactCategory.DEBDIFF
    template_name = "web/artifact-detail.html"
    name = "debdiff"

    object_name = "artifact"

    @classmethod
    def _summarise_debdiff_source(
        cls, debdiff: str
    ) -> list[DebDiffFileSummary]:
        patch = PatchSet(debdiff)

        summary: list[DebDiffFileSummary] = []

        for file in patch:
            if file.is_added_file:
                operation = OperationEnum.ADDED
            elif file.is_removed_file:
                operation = OperationEnum.REMOVED
            elif file.is_modified_file:
                operation = OperationEnum.MODIFIED
            else:
                raise ValueError(f"Unexpected file status in diff: {file.path}")

            # first diff line number of the first hunk of the file
            #
            # subtracts 3: skip back over the one-line hunk header (@@) and the
            # two-line file header (--- and +++)
            diff_line_number = file[0][0].diff_line_no - 3

            summary.append(
                {
                    "path": file.path,
                    "diff_line_number": diff_line_number,
                    "operation": operation,
                }
            )

        return summary

    @staticmethod
    def _detect_section_package(
        line: str,
    ) -> tuple[DebDiffSection | None, str | None]:
        """Detect if a line is the start of a new debdiff section or package."""
        control_files_re = re.compile(r"^Control files of package (\S+):")
        no_differences_re = re.compile(
            r"^No differences were encountered between "
            r"the control files of package (\S+)$"
        )

        if line in (
            "Files in second .changes but not in first",
            "Files in second .deb but not in first",
            "Files in second set of .debs but not in first",
        ):
            return DebDiffSection.ADDED_FILES, None

        elif line in (
            "Files in first .changes but not in second",
            "Files in first .deb but not in second",
            "Files in first set of .debs but not in second",
        ):
            return DebDiffSection.REMOVED_FILES, None

        elif line.startswith("Control files: lines which differ"):
            return DebDiffSection.CONTROL_DIFFS, None

        elif m := control_files_re.match(line):
            return DebDiffSection.CONTROL_DIFFS_PACKAGES, m.group(1)

        elif m := no_differences_re.match(line):
            return DebDiffSection.CONTROL_DIFFS_PACKAGES_NO_CHANGES, m.group(1)

        return None, None

    @staticmethod
    def _append_to_result(
        result: DebDiffBinaryResult,
        section: DebDiffSection,
        package: str | None,
        line: str,
    ) -> None:
        value = result[section.value]

        match value:
            case str():
                result[section.value] = value + line + "\n"
            case dict():
                subsection = value

                # If it's a dict we have a package and we have information for
                # each package to add
                assert package is not None

                subsection[package] += line + "\n"

            case _ as unreachable:
                raise NotImplementedError(f"{unreachable!r} not implemented")

    @classmethod
    def _parse_debdiff_binary(cls, debdiff: str) -> DebDiffBinaryResult:
        result: DebDiffBinaryResult = {
            DebDiffSection.ADDED_FILES.value: "",
            DebDiffSection.REMOVED_FILES.value: "",
            DebDiffSection.CONTROL_DIFFS.value: "",
            DebDiffSection.CONTROL_DIFFS_PACKAGES.value: defaultdict(str),
            DebDiffSection.CONTROL_DIFFS_PACKAGES_NO_CHANGES.value: [],
        }

        current_section: DebDiffSection | None = None
        current_package: str | None = None
        has_data = False

        for line in debdiff.splitlines():
            line = line.rstrip()

            if set(line) == {"-"}:
                # Line is a header underline
                continue

            if line == "":
                current_section = None
                current_package = None
                continue

            if (
                line
                == "No differences were encountered between the control files"
            ):
                has_data = True
                break

            section, package = cls._detect_section_package(line)

            has_data |= section is not None

            if section == DebDiffSection.CONTROL_DIFFS_PACKAGES_NO_CHANGES:
                assert package is not None
                result[
                    DebDiffSection.CONTROL_DIFFS_PACKAGES_NO_CHANGES.value
                ].append(package)

            if section is not None:
                current_section = section
                current_package = package
                continue

            if current_section is None and has_data:
                raise ValueError(f"Failed to parse line: {line!r}")

            if current_section is not None:
                cls._append_to_result(
                    result, current_section, current_package, line
                )

        if not has_data:
            raise ValueError("Cannot parse any information from debdiff output")

        return result

    @staticmethod
    def _read_file(file_in_artifact: FileInArtifact) -> str:
        """Read debdiff.txt file from debdiff_artifact and return it."""
        artifact = file_in_artifact.artifact
        scope = artifact.workspace.scope
        file = file_in_artifact.file

        with (
            scope.download_file_backend(file)
            .get_entry(file)
            .get_temporary_local_path() as path
        ):
            return path.read_text()

    def get_context_data(self) -> dict[str, Any]:
        """Return the context."""
        debdiff_txt_file_in_artifact = self.artifact.fileinartifact_set.get(
            path=DebDiff.CAPTURE_OUTPUT_FILENAME
        )
        debdiff_contents = self._read_file(debdiff_txt_file_in_artifact)

        debdiff_absolute_path = debdiff_txt_file_in_artifact.get_absolute_url()

        related_to = self.artifact.relations.filter(
            type=ArtifactRelation.Relations.RELATES_TO
        ).first()
        assert related_to is not None

        slug = "debdiff"

        specialized_tab = {
            "specialized_tab": {
                "label": "DebDiff",
                "slug": slug,
                "template": "web/_debdiff-artifact-detail.html",
            }
        }

        if related_to.target.category == ArtifactCategory.SOURCE_PACKAGE:
            # The original and new artifacts are source artifacts
            debdiff_parser_error = None
            try:
                debdiff_source_summary = self._summarise_debdiff_source(
                    debdiff_contents
                )
            except ValueError as exc:
                debdiff_source_summary = None
                debdiff_parser_error = str(exc)

            debdiff_source_file_widget = FileWidget.create(
                debdiff_txt_file_in_artifact,
                file_tag=slug,
            )

            return {
                **specialized_tab,
                "debdiff_artifact_url": debdiff_absolute_path,
                "debdiff_source_summary": debdiff_source_summary,
                "debdiff_source_file_widget": debdiff_source_file_widget,
                "debdiff_parser_error": debdiff_parser_error,
                "debdiff_differences_reported": debdiff_source_summary
                is not None
                and len(debdiff_source_summary) != 0,
            }

        else:
            # The original and new artifacts were binary artifacts
            debdiff_parser_error = None
            try:
                debdiff_binary_parsed = cast(
                    dict[str, Any], self._parse_debdiff_binary(debdiff_contents)
                )
                # Avoid using .items notation in the template: if a package
                # would be named "items" then it would not iterate
                debdiff_binary_parsed["control_diffs_packages_items"] = (
                    debdiff_binary_parsed["control_diffs_packages"].items()
                )
            except ValueError as exc:
                debdiff_binary_parsed = None
                debdiff_parser_error = str(exc)

            return {
                **specialized_tab,
                "debdiff_artifact_url": debdiff_absolute_path,
                "debdiff_binary_parsed": debdiff_binary_parsed,
                "debdiff_parser_error": debdiff_parser_error,
                "debdiff_differences_reported": debdiff_binary_parsed
                and any(
                    debdiff_binary_parsed[section.value]
                    for section in DebDiffSection
                ),
            }


class DebDiffViewWorkRequestPlugin(WorkRequestPlugin):
    """View for DebDiff work request."""

    task_type = TaskTypes.WORKER
    task_name = "debdiff"

    def do_get_description_data(self) -> dict[str, Any]:
        """Return data used for the description."""
        dynamic_data = self.task.dynamic_data
        assert dynamic_data is not None

        data: dict[str, Any] = {}

        if (
            source_artifacts := dynamic_data.input_source_artifacts_ids
        ) is not None:
            data["source_artifact_original_id"] = source_artifacts[0]
            data["source_artifact_new_id"] = source_artifacts[1]

        if (
            binary_artifacts := dynamic_data.input_binary_artifacts_ids
        ) is not None:
            data["binary_artifacts_original_ids"] = binary_artifacts[0]
            data["binary_artifacts_new_ids"] = binary_artifacts[1]

        data["environment_id"] = dynamic_data.environment_id
        data["extra_arguments"] = self.task.data.extra_flags

        return data

    def get_context_data(self) -> dict[str, Any]:
        """Return context_data."""
        return {
            "description_template": "web/_debdiff-description.html",
            "description_data": self.get_description_data(),
        }
