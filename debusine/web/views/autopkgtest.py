# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""View for Autopkgtest work request."""

from functools import cached_property
from typing import Any

from debusine.artifacts import AutopkgtestArtifact
from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import Artifact
from debusine.tasks import Autopkgtest
from debusine.tasks.models import TaskTypes
from debusine.web.views.work_request import WorkRequestPlugin


class AutopkgtestView(WorkRequestPlugin):
    """View for Autopkgtest work request."""

    template_name = "web/autopkgtest-detail.html"
    task_type = TaskTypes.WORKER
    task_name = "autopkgtest"

    @cached_property
    def task(self) -> Autopkgtest:
        """Return the task to display."""
        return Autopkgtest(
            self.work_request.used_task_data,
            self.work_request.dynamic_task_data,
        )

    @staticmethod
    def _get_fail_on_scenarios(fail_on: dict[str, bool]) -> list[str]:
        """Process fail_on task data into a list of strings for viewing."""
        return [
            key.replace("_test", "")
            for key, value in fail_on.items()
            if key.endswith("_test") and value
        ]

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

    @property
    def _source_artifact(self) -> dict[str, Any]:
        task_data = self.task.data
        source_artifact: dict[str, Any] = {
            "lookup": task_data.input.source_artifact,
            "id": None,
            "artifact": None,
            "files": [],
        }

        if (dynamic_data := self.task.dynamic_data) is not None:
            pk = dynamic_data.input_source_artifact_id
            source_artifact["id"] = pk
            try:
                source_artifact["artifact"] = artifact = Artifact.objects.get(
                    id=pk
                )
            except Artifact.DoesNotExist:
                pass
            else:
                source_artifact["files"] = self._list_files(artifact)
        return source_artifact

    def _get_request_data(self) -> dict[str, Any]:
        """Return data about the request."""
        task = self.task
        task_data = task.data
        dynamic_data = task.dynamic_data

        # TODO: It would be useful to show the request data after default
        # values have been set, but unfortunately these are only set by the
        # task after it loads data from the database and aren't saved
        # anywhere else.
        request_data: dict[str, Any] = {
            "source_artifact": self._source_artifact,
            "source_name": None,
            "source_version": None,
            "binary_artifacts": {
                "lookup": task_data.input.binary_artifacts.export(),
                "artifacts": [],
            },
            "context_artifacts": {
                "lookup": task_data.input.context_artifacts.export(),
                "artifacts": [],
            },
            "host_architecture": task_data.host_architecture,
            "vendor": None,
            "codename": None,
            "backend": task_data.backend,
            "include_tests": task_data.include_tests,
            "exclude_tests": task_data.exclude_tests,
            "debug_level": task_data.debug_level,
            "extra_repositories": task_data.extra_repositories,
            "use_packages_from_base_repository": (
                task_data.use_packages_from_base_repository
            ),
            "extra_environment": task_data.extra_environment,
            "needs_internet": task_data.needs_internet,
            "fail_on_scenarios": self._get_fail_on_scenarios(
                task_data.fail_on.dict()
            ),
            "timeout": task_data.timeout,
        }

        # This information is in the result artifact's data if it exists,
        # but it won't necessarily exist - for example, a testbed failure
        # will leave us without a result artifact.
        if dynamic_data is not None:
            try:
                environment_artifact = Artifact.objects.get(
                    id=dynamic_data.environment_id
                )
            except Artifact.DoesNotExist:
                pass
            else:
                if environment_artifact.category in {
                    ArtifactCategory.SYSTEM_TARBALL,
                    ArtifactCategory.SYSTEM_IMAGE,
                }:
                    request_data["vendor"] = environment_artifact.data.get(
                        "vendor"
                    )
                    request_data["codename"] = environment_artifact.data.get(
                        "codename"
                    )

            if source_artifact := request_data["source_artifact"]["artifact"]:
                if source_artifact.category == ArtifactCategory.SOURCE_PACKAGE:
                    request_data.update(
                        {
                            "source_name": source_artifact.data.get("name"),
                            "source_version": source_artifact.data.get(
                                "version"
                            ),
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
                        "files": self._list_files(binary_artifact),
                        "artifact": binary_artifact,
                    }
                )

            context_artifacts = {
                artifact.id: artifact
                for artifact in Artifact.objects.filter(
                    id__in=dynamic_data.input_context_artifacts_ids
                )
            }
            for context_artifact_id in dynamic_data.input_context_artifacts_ids:
                context_artifact = context_artifacts.get(context_artifact_id)
                request_data["context_artifacts"]["artifacts"].append(
                    {
                        "id": context_artifact_id,
                        "files": self._list_files(context_artifact),
                    }
                )

        request_data["task_description"] = (
            f"{request_data['source_name'] or 'UNKNOWN'}_"
            f"{request_data['source_version'] or 'UNKNOWN'}_"
            f"{request_data['host_architecture']}"
        )
        if request_data["vendor"] and request_data["codename"]:
            request_data[
                "task_description"
            ] += f" in {request_data['vendor']}:{request_data['codename']}"

        return request_data

    @staticmethod
    def _get_result_data(
        artifact: Artifact,
    ) -> list[tuple[str, str, str | None]]:
        """Return a list of tuples describing individual test results."""
        result_data = []
        for name, result in sorted(artifact.data.get("results", {}).items()):
            result_data.append((name, result["status"], result.get("details")))
        return result_data

    def get_context_data(self) -> dict[str, Any]:
        """Return the context."""
        result_artifact = (
            self.work_request.artifact_set.filter(
                category=AutopkgtestArtifact._category
            )
            .order_by("id")
            .first()
        )

        context_data: dict[str, Any] = {
            "request_data": self._get_request_data(),
            "result": self.work_request.result,
        }
        if result_artifact:
            context_data["result_artifact"] = result_artifact
            context_data["result_data"] = self._get_result_data(result_artifact)
        return context_data
