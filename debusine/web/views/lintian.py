# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""View for Lintian work request."""
import json
import uuid
from collections.abc import Iterable
from typing import Any

from debusine.artifacts import LintianArtifact
from debusine.artifacts.models import BaseArtifactDataModel, TaskTypes
from debusine.db.models import Artifact, FileInArtifact
from debusine.tasks import Lintian
from debusine.web.views.work_request import WorkRequestPlugin


class LintianAnalysisOccurrence(BaseArtifactDataModel):
    """A location where a Lintian tag occurred."""

    pointer: str
    note: str
    comment: str


class LintianAnalysisTag(BaseArtifactDataModel):
    """Information about a Lintian tag, with its occurrences grouped."""

    explanation: str
    severity: str
    uuid: uuid.UUID
    occurrences: list[LintianAnalysisOccurrence]


class LintianView(WorkRequestPlugin):
    """View for Lintian work request."""

    task_type = TaskTypes.WORKER
    task_name = "lintian"

    @classmethod
    def _artifacts_to_tags(
        cls, artifacts: Iterable[Artifact]
    ) -> dict[str, dict[str, LintianAnalysisTag]]:
        """
        Receive a list of artifacts and return dict with file names and tags.

        :param artifacts: each contains an "analysis.json" file with "tags"
          key, with a list of tags. Each tag is such as:
            {
               "severity": "pedantic",
               "package": "hello",
               "tag": "license-problem",
               "explanation": "the explanation",
               "comment": "a comment",
               "note": "bar",
               "pointer": "debian/copyright"
            }
        :return: a dictionary of filenames to dictionary containing the tags
          grouped by occurrence. For example:
            {
                "hello_2.10-3.dsc": {
                    "license-problem": LintianAnalysisTag(
                        explanation="the explanation",
                        severity="pedantic",
                        uuid="used_by_the_view",
                        occurrences=[
                            LintianAnalysisOccurrence(
                                pointer="debian/copyright"
                                note="bar",
                                comment="a comment",
                            ),
                        ]
                    }
                }
            }

        """  # noqa: RST201, RST203, RST301
        # "tags": intermediate data structure: contains each file (.dsc,
        # .deb, etc.) with each tag for the file with all the information
        # (explanation, pointer, comment, etc.).
        tags: dict[str, dict[str, list[LintianAnalysisOccurrence]]] = {}

        # "tags_information": intermediate data structure with each tag_name
        # containing a dictionary with the common data: explanation, pointer
        # and severity
        tags_information: dict[str, dict[str, str]] = {}

        for analysis in cls._get_analyses(artifacts):
            for tag in analysis["tags"]:
                package = tag["package"]
                filename = analysis["summary"]["package_filename"][package]

                tag_name = tag["tag"]
                # Explanation and severity is the same for each instance
                # of this tag (incorporated in tags_with_information).
                tag_occurrence = LintianAnalysisOccurrence(
                    pointer=tag["pointer"],
                    note=tag["note"].replace("\\n", "\n"),
                    comment=tag["comment"],
                )
                tags_information[tag_name] = {
                    "explanation": tag["explanation"],
                    "severity": tag["severity"],
                }

                tags.setdefault(filename, {}).setdefault(tag_name, []).append(
                    tag_occurrence
                )

        # "tags_with_information" is the final structure as explained
        # in the docstring
        tags_with_information: dict[str, dict[str, LintianAnalysisTag]] = {}

        for filename, tags_in_filename in tags.items():
            tags_with_information[filename] = {}
            for tag_name, occurrences in tags_in_filename.items():
                tags_with_information[filename][tag_name] = LintianAnalysisTag(
                    **tags_information[tag_name],
                    uuid=uuid.uuid4(),
                    occurrences=occurrences,
                )

        return tags_with_information

    @classmethod
    def _get_summaries(
        cls, artifacts: Iterable[Artifact]
    ) -> list[dict[str, Any]]:
        """Return list of all the artifacts.data["summary"] in artifacts."""
        return [artifact.data["summary"] for artifact in artifacts]

    @staticmethod
    def _get_analyses(artifacts: Iterable[Artifact]) -> list[dict[str, Any]]:
        """Return a list with all the parsed analysis.json in artifacts."""
        analyses: list[dict[str, Any]] = []

        for artifact in artifacts:
            try:
                analysis_in_artifact = artifact.fileinartifact_set.get(
                    path="analysis.json", complete=True
                )
            except FileInArtifact.DoesNotExist:
                continue

            scope = artifact.workspace.scope
            fileobj = analysis_in_artifact.file
            file_backend = scope.download_file_backend(fileobj)

            with file_backend.get_stream(fileobj) as file:
                analysis = json.loads(file.read())

            analyses.append(analysis)

        return analyses

    @staticmethod
    def _find_lintian_txt_url_path(artifacts: Iterable[Artifact]) -> str | None:
        """
        Return the URL path for the lintian.txt file.

        The path is for any of the lintian.txt found in artifacts.
        """
        for artifact in artifacts:
            for file_in_artifact in artifact.fileinartifact_set.filter(
                complete=True
            ).order_by("id"):
                if file_in_artifact.path == Lintian.CAPTURE_OUTPUT_FILENAME:
                    return (
                        artifact.get_absolute_url_download()
                        + Lintian.CAPTURE_OUTPUT_FILENAME
                    )

        return None

    @classmethod
    def _count_by_severity(
        cls, artifacts: Iterable[Artifact]
    ) -> dict[str, int]:
        tags_count_severity = Lintian.generate_severity_count_zero()
        tags_count_severity["overridden"] = 0

        for summary in cls._get_summaries(artifacts):
            for severity, count in summary.get(
                "tags_count_by_severity", {}
            ).items():
                tags_count_severity[severity] += count

        return tags_count_severity

    @staticmethod
    def _list_files(artifact: Artifact | None) -> list[str]:
        if artifact is not None:
            return [
                file.path
                for file in artifact.fileinartifact_set.filter(
                    complete=True
                ).order_by("path")
            ]
        else:
            return []

    @classmethod
    def _get_request_data(cls, task: Lintian) -> dict[str, Any]:
        """Prepare the requested data to be displayed."""
        dynamic_data = task.dynamic_data

        request_data: dict[str, Any] = {
            "source_artifact": {
                "lookup": task.data.input.source_artifact,
                "id": None,
                "files": [],
            },
            "binary_artifacts": {
                "lookup": task.data.input.binary_artifacts.export(),
                "artifacts": [],
            },
        }

        if dynamic_data is not None:
            if dynamic_data.input_source_artifact_id is not None:
                try:
                    source_artifact = Artifact.objects.get(
                        id=dynamic_data.input_source_artifact_id
                    )
                except Artifact.DoesNotExist:
                    pass
                else:
                    request_data["source_artifact"].update(
                        {
                            "id": source_artifact.id,
                            "files": cls._list_files(source_artifact),
                            "artifact": source_artifact,
                        }
                    )

            binary_artifacts = {
                artifact.id: artifact
                for artifact in Artifact.objects.filter(
                    id__in=dynamic_data.input_binary_artifacts_ids
                )
            }
            for binary_artifact_id in dynamic_data.input_binary_artifacts_ids:
                binary_artifact = binary_artifacts.get(binary_artifact_id)
                request_data["binary_artifacts"]["artifacts"].append(
                    {
                        "id": binary_artifact_id,
                        "files": cls._list_files(binary_artifact),
                        "artifact": binary_artifact,
                    }
                )

        return request_data

    def do_get_description_data(self) -> dict[str, Any]:
        """Parse metadata."""
        dynamic_data = self.task.dynamic_data
        assert dynamic_data is not None

        data = self.task.data

        return {
            "package_name": dynamic_data.subject,
            "source_artifact_id": dynamic_data.input_source_artifact_id,
            "binary_artifacts_ids": dynamic_data.input_binary_artifacts_ids,
            "environment_id": dynamic_data.environment_id,
            "fail_on_severity": data.fail_on_severity,
            "host_architecture": data.host_architecture,
            "target_distribution": data.target_distribution,
            "include_tags": data.include_tags,
            "exclude_tags": data.exclude_tags,
        }

    def get_context_data(self) -> dict[str, Any]:
        """Return the context."""
        task = self.task
        assert isinstance(task, Lintian)

        artifacts = self.work_request.artifact_set.filter(
            category=LintianArtifact._category
        ).order_by("id")

        lintian_txt_path = self._find_lintian_txt_url_path(artifacts)

        return {
            "specialized_tab": {
                "label": "Lintian",
                "slug": "lintian",
                "template": "web/_lintian-work_request-detail.html",
            },
            "description_template": "web/_lintian-description.html",
            "description_data": self.get_description_data(),
            "request_data": self._get_request_data(task),
            "result": self.work_request.result,
            "lintian_txt_path": lintian_txt_path,
            "fail_on_severity": task.data.fail_on_severity,
            "tags": self._artifacts_to_tags(artifacts),
            "tags_count_severity": self._count_by_severity(artifacts),
        }
