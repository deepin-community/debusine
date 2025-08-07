# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""View for DebDiff work request."""
from enum import StrEnum
from typing import Any, TypedDict

from unidiff import PatchSet

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import ArtifactRelation, FileInArtifact
from debusine.tasks import DebDiff
from debusine.web.views.artifacts import ArtifactPlugin
from debusine.web.views.files import FileWidget


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
    CONTROL_DIFFS = "control_diff"


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
    def _parse_debdiff_binary(debdiff: str) -> dict[str, str]:
        result: dict[str, str] = {
            DebDiffSection.ADDED_FILES: "",
            DebDiffSection.REMOVED_FILES: "",
            DebDiffSection.CONTROL_DIFFS: "",
        }

        current_section: DebDiffSection | None = None
        has_data = False

        for line in debdiff.splitlines():
            line = line.rstrip()

            if set(line) == {"-"}:
                # Line is a header underline
                continue

            if line == "":
                current_section = None
                continue

            if (
                line
                == "No differences were encountered between the control files"
            ):
                has_data = True
                break

            if line in (
                "Files in second .changes but not in first",
                "Files in second .deb but not in first",
            ):
                has_data = True
                current_section = DebDiffSection.ADDED_FILES
                continue

            elif line in (
                "Files in first .changes but not in second",
                "Files in first .deb but not in second",
            ):
                has_data = True
                current_section = DebDiffSection.REMOVED_FILES
                continue

            elif line.startswith("Control files: lines which differ"):
                has_data = True
                current_section = DebDiffSection.CONTROL_DIFFS
                continue

            if current_section is None and has_data:
                raise ValueError(f"Failed to parse line: {line!r}")

            if current_section is not None:
                result[current_section] += line + "\n"

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
                debdiff_binary_parsed = self._parse_debdiff_binary(
                    debdiff_contents
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
                    debdiff_binary_parsed[section] != ""
                    for section in DebDiffSection
                ),
            }
