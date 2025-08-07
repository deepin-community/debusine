# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Autopkgtest work request plugin."""

from django.http import HttpResponseBase
from django.template.loader import get_template

from debusine.artifacts import AutopkgtestArtifact
from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import Artifact, WorkRequest, Workspace
from debusine.tasks.models import (
    AutopkgtestData,
    AutopkgtestDynamicData,
    AutopkgtestInput,
    LookupMultiple,
    LookupSingle,
)
from debusine.test.django import TestCase
from debusine.web.views.autopkgtest import AutopkgtestView


class AutopkgtestViewTests(TestCase):
    """Tests for AutopkgtestView."""

    def create_work_request_for_artifacts(
        self,
        environment_artifact: Artifact,
        source_artifact: Artifact,
        binary_artifacts: list[Artifact],
        context_artifacts: list[Artifact] | None = None,
        workspace: Workspace | None = None,
    ) -> WorkRequest:
        """Create an autopkgtest work request for some artifacts."""
        environment_lookup: int | str
        if workspace is None:
            workspace = self.playground.get_default_workspace()
        if "vendor" in environment_artifact.data:
            environments_collection = self.playground.create_collection(
                environment_artifact.data["vendor"],
                CollectionCategory.ENVIRONMENTS,
                workspace=workspace,
            )
            environments_collection.manager.add_artifact(
                environment_artifact,
                user=self.playground.get_default_user(),
                variables={"backend": "unshare"},
            )
            environment_lookup = "{vendor}/match:codename={codename}".format(
                **environment_artifact.data
            )
        else:
            environment_lookup = environment_artifact.id
        task_data_input = AutopkgtestInput(
            source_artifact=source_artifact.id,
            binary_artifacts=LookupMultiple.parse_obj(
                [binary_artifact.id for binary_artifact in binary_artifacts]
            ),
        )
        if context_artifacts:
            task_data_input.context_artifacts = LookupMultiple.parse_obj(
                [context_artifact.id for context_artifact in context_artifacts]
            )

        return self.playground.create_work_request(
            result=WorkRequest.Results.SUCCESS,
            task_data=AutopkgtestData(
                host_architecture="amd64",
                environment=environment_lookup,
                input=task_data_input,
            ),
            task_name="autopkgtest",
            workspace=workspace,
        )

    def create_autopkgtest_artifact(
        self, work_request: WorkRequest
    ) -> Artifact:
        """Create an AutopkgtestArtifact for a work request."""
        artifact, _ = self.playground.create_artifact(
            paths=["log", "summary"],
            work_request=work_request,
            create_files=True,
        )
        artifact.category = AutopkgtestArtifact._category
        artifact.data = {
            "results": {
                "command1": {"status": "PASS"},
                "command2": {"status": "FAIL", "details": "partial"},
            }
        }
        artifact.save()
        return artifact

    def test_get_context_data_simple(self) -> None:
        """get_context_data returns the expected data for a simple case."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "variant": "",
                },
                create_files=True,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            binary_artifact_1, _ = self.playground.create_artifact(
                paths=["hello-1.deb"], create_files=True
            )
            binary_artifact_2, _ = self.playground.create_artifact(
                paths=["hello-2.deb"], create_files=True
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact,
                source_artifact,
                [binary_artifact_1, binary_artifact_2],
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[
                    binary_artifact_1.id,
                    binary_artifact_2.id,
                ],
            ).dict()
            work_request.save()
            result_artifact = self.create_autopkgtest_artifact(work_request)
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()

        self.assertEqual(
            context_data,
            {
                "request_data": {
                    "backend": "auto",
                    "binary_artifacts": {
                        "lookup": [binary_artifact_1.id, binary_artifact_2.id],
                        "artifacts": [
                            {
                                "files": ["hello-1.deb"],
                                "id": binary_artifact_1.id,
                                "artifact": binary_artifact_1,
                            },
                            {
                                "files": ["hello-2.deb"],
                                "id": binary_artifact_2.id,
                                "artifact": binary_artifact_2,
                            },
                        ],
                    },
                    "codename": "bookworm",
                    "context_artifacts": {"lookup": [], "artifacts": []},
                    "debug_level": 0,
                    "exclude_tests": [],
                    "extra_repositories": None,
                    "extra_environment": {},
                    "fail_on_scenarios": ["failed"],
                    "host_architecture": "amd64",
                    "include_tests": [],
                    "needs_internet": "run",
                    "source_artifact": {
                        "files": ["hello.dsc"],
                        "id": source_artifact.id,
                        "lookup": source_artifact.id,
                        "artifact": source_artifact,
                    },
                    "source_name": "hello",
                    "source_version": "1.0",
                    "task_description": "hello_1.0_amd64 in debian:bookworm",
                    "timeout": None,
                    "use_packages_from_base_repository": False,
                    "vendor": "debian",
                },
                "result": work_request.result,
                "result_artifact": result_artifact,
                "result_data": [
                    ("command1", "PASS", None),
                    ("command2", "FAIL", "partial"),
                ],
            },
        )

    def test_get_context_data_with_context_artifacts(self) -> None:
        """Any context artifacts show up in the context data."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "variant": "",
                },
                create_files=True,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            context_artifact_1, _ = self.playground.create_artifact(
                paths=["hello-rdep-1.deb"], create_files=True
            )
            context_artifact_2, _ = self.playground.create_artifact(
                paths=["hello-rdep-2.deb"], create_files=True
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact,
                source_artifact,
                [],
                context_artifacts=[context_artifact_1, context_artifact_2],
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[],
                input_context_artifacts_ids=[
                    context_artifact_1.id,
                    context_artifact_2.id,
                ],
            ).dict()
            work_request.save()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()

        self.assertEqual(
            context_data["request_data"]["context_artifacts"],
            {
                "lookup": [context_artifact_1.id, context_artifact_2.id],
                "artifacts": [
                    {
                        "files": ["hello-rdep-1.deb"],
                        "id": context_artifact_1.id,
                    },
                    {
                        "files": ["hello-rdep-2.deb"],
                        "id": context_artifact_2.id,
                    },
                ],
            },
        )

    def test_context_data_no_artifacts(self) -> None:
        """If there are no output artifacts, context data omits that info."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "variant": "",
                },
                create_files=True,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertCountEqual(context_data.keys(), {"request_data", "result"})

    def test_context_data_no_dynamic_task_data(self) -> None:
        """If there is no dynamic data, context data omits artifact info."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertIsNone(context_data["request_data"]["codename"])
        self.assertIsNone(context_data["request_data"]["vendor"])
        self.assertEqual(
            context_data["request_data"]["task_description"],
            "UNKNOWN_UNKNOWN_amd64",
        )
        self.assertEqual(
            context_data["request_data"]["source_artifact"],
            {
                "lookup": source_artifact.id,
                "id": None,
                "files": [],
                "artifact": None,
            },
        )

    def test_context_data_missing_environment_artifact(self) -> None:
        """If the environment artifact is missing, context data omits it."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[],
            ).dict()
            work_request.save()
            environment_artifact.delete()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertIsNone(context_data["request_data"]["codename"])
        self.assertIsNone(context_data["request_data"]["vendor"])
        self.assertEqual(
            context_data["request_data"]["task_description"], "hello_1.0_amd64"
        )

    def test_context_data_environment_artifact_wrong_category(self) -> None:
        """We cope with the environment artifact having an odd category."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.TEST,
                create_files=True,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[],
            ).dict()
            work_request.save()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertIsNone(context_data["request_data"]["codename"])
        self.assertIsNone(context_data["request_data"]["vendor"])
        self.assertEqual(
            context_data["request_data"]["task_description"], "hello_1.0_amd64"
        )

    def test_context_data_missing_source_artifact(self) -> None:
        """If the source artifact is missing, context data omits its files."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[],
            ).dict()
            work_request.save()
            source_artifact_id = source_artifact.id
            source_artifact.files.clear()
            source_artifact.delete()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertEqual(
            context_data["request_data"]["source_artifact"],
            {
                "lookup": source_artifact_id,
                "id": source_artifact_id,
                "artifact": None,
                "files": [],
            },
        )

    def test_context_data_source_artifact_wrong_category(self) -> None:
        """We cope with the source artifact having an odd category."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "variant": "",
                },
                create_files=True,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.TEST,
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[],
            ).dict()
            work_request.save()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertIsNone(context_data["request_data"]["source_name"])
        self.assertIsNone(context_data["request_data"]["source_version"])
        self.assertEqual(
            context_data["request_data"]["task_description"],
            "UNKNOWN_UNKNOWN_amd64 in debian:bookworm",
        )

    def test_context_data_missing_binary_artifact(self) -> None:
        """If a binary artifact is missing, context data omits its files."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            binary_artifact_1, _ = self.playground.create_artifact(
                paths=["hello-1.deb"], create_files=True
            )
            binary_artifact_2, _ = self.playground.create_artifact(
                paths=["hello-2.deb"], create_files=True
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[
                    binary_artifact_1.id,
                    binary_artifact_2.id,
                ],
            ).dict()
            work_request.save()
            binary_artifact_1_id = binary_artifact_1.id
            binary_artifact_1.files.clear()
            binary_artifact_1.delete()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertEqual(
            context_data["request_data"]["binary_artifacts"],
            {
                "lookup": work_request.task_data["input"]["binary_artifacts"],
                "artifacts": [
                    {
                        "id": binary_artifact_1_id,
                        "files": [],
                        "artifact": None,
                    },
                    {
                        "id": binary_artifact_2.id,
                        "files": ["hello-2.deb"],
                        "artifact": binary_artifact_2,
                    },
                ],
            },
        )

    def test_get_context_data_skips_incomplete_files(self) -> None:
        """get_context_data() skips incomplete files."""
        environment_artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SYSTEM_TARBALL
        )
        source_artifact, _ = self.playground.create_artifact(
            paths=["hello.dsc"],
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={"name": "hello", "version": "1.0", "type": "dpkg"},
            create_files=True,
            skip_add_files_in_store=True,
        )
        work_request: WorkRequest = self.create_work_request_for_artifacts(
            environment_artifact, source_artifact, []
        )
        work_request.dynamic_task_data = AutopkgtestDynamicData(
            environment_id=environment_artifact.id,
            input_source_artifact_id=source_artifact.id,
            input_binary_artifacts_ids=[],
        ).dict()
        work_request.save()

        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()

        self.assertEqual(
            context_data["request_data"]["source_artifact"]["files"], []
        )

    def assert_contains_artifact_information(
        self,
        response: HttpResponseBase,
        description: str,
        artifact_lookup: LookupSingle,
        artifact_id: int,
    ) -> None:
        """Assert contains information of an artifact: link and files."""
        artifact = Artifact.objects.get(id=artifact_id)
        files = artifact.fileinartifact_set.order_by("path").values_list(
            "path", flat=True
        )
        artifact_path = artifact.get_absolute_url()
        artifact_link = f'<a href="{artifact_path}">#{artifact_id}</a>'
        files_li = "".join([f"<li>{file}</li>" for file in files])
        self.assertContains(
            response,
            f"<li>{description} ({artifact_lookup}: {artifact_link})"
            f"<ul>{files_li}</ul>"
            f"</li>",
            html=True,
        )

    def assert_contains_artifacts_information(
        self,
        response: HttpResponseBase,
        description: str,
        artifact_lookup: LookupMultiple,
        artifact_ids: list[int],
    ) -> None:
        """Assert contains information of a set of artifacts."""
        expected_response = (
            f"<li>{description} ({artifact_lookup.export()})<ul>"
        )
        for artifact_id in artifact_ids:
            artifact = Artifact.objects.get(id=artifact_id)
            files = artifact.fileinartifact_set.order_by("path").values_list(
                "path", flat=True
            )
            artifact_path = artifact.get_absolute_url()
            artifact_link = f'<a href="{artifact_path}">#{artifact_id}</a>'
            files_li = "".join([f"<li>{file}</li>" for file in files])
            expected_response += f"<li>{artifact_link}<ul>{files_li}</ul></li>"
        expected_response += "</ul></li>"
        self.assertContains(response, expected_response, html=True)

    def test_template_output(self) -> None:
        """Generic output of the template."""
        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(
                name="Public", public=True
            )
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "variant": "",
                },
                create_files=True,
                workspace=workspace,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
                workspace=workspace,
            )
            binary_artifact_1, _ = self.playground.create_artifact(
                paths=["hello-1.deb"],
                create_files=True,
                workspace=workspace,
            )
            binary_artifact_2, _ = self.playground.create_artifact(
                paths=["hello-2.deb"],
                create_files=True,
                workspace=workspace,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact,
                source_artifact,
                [binary_artifact_1, binary_artifact_2],
                workspace=workspace,
            )
            work_request.dynamic_task_data = AutopkgtestDynamicData(
                environment_id=environment_artifact.id,
                input_source_artifact_id=source_artifact.id,
                input_binary_artifacts_ids=[
                    binary_artifact_1.id,
                    binary_artifact_2.id,
                ],
            ).dict()
            work_request.save()
            self.create_autopkgtest_artifact(work_request)

        response = self.client.get(work_request.get_absolute_url())

        self.assertContains(
            response,
            "<title>Debusine - Autopkgtest run for hello_1.0_amd64 in "
            "debian:bookworm</title>",
            html=True,
        )
        self.assertContains(
            response,
            "<h1>Autopkgtest run for hello_1.0_amd64 in debian:bookworm</h1>",
            html=True,
        )
        work_request_generic_path = (
            work_request.get_absolute_url() + "?view=generic"
        )
        self.assertContains(
            response,
            f'<a href="{work_request_generic_path}">Generic view</a>',
            html=True,
        )
        result_output = get_template("web/_work_request-result.html").render(
            {"result": work_request.result}
        )
        self.assertContains(response, f"Result: {result_output}", html=True)

        self.assert_contains_artifact_information(
            response, "Source artifact", source_artifact.id, source_artifact.id
        )

        self.assert_contains_artifacts_information(
            response,
            "Binary artifacts",
            LookupMultiple.parse_obj(
                [binary_artifact_1.id, binary_artifact_2.id]
            ),
            [binary_artifact_1.id, binary_artifact_2.id],
        )

    def test_get_fail_on_scenarios(self) -> None:
        """_get_fail_on_scenarios processes the task's fail_on correctly."""
        sub_tests = [
            ({"failed_test": True}, ["failed"]),
            (
                {
                    "failed_test": False,
                    "flaky_test": True,
                    "skipped_test": False,
                },
                ["flaky"],
            ),
            (
                {
                    "failed_test": True,
                    "flaky_test": True,
                    "skipped_test": True,
                },
                ["failed", "flaky", "skipped"],
            ),
        ]

        for fail_on, expected_scenarios in sub_tests:
            with self.subTest(fail_on=fail_on):
                self.assertEqual(
                    AutopkgtestView._get_fail_on_scenarios(fail_on),
                    expected_scenarios,
                )

    def test_show_configured_task_data(self) -> None:
        """The view shows configured task data if present."""
        with context.disable_permission_checks():
            environment_artifact, _ = self.playground.create_artifact(
                paths=["system.tar.xz"],
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={
                    "vendor": "debian",
                    "codename": "bookworm",
                    "architecture": "amd64",
                    "variant": "",
                },
                create_files=True,
            )
            source_artifact, _ = self.playground.create_artifact(
                paths=["hello.dsc"],
                category=ArtifactCategory.SOURCE_PACKAGE,
                data={"name": "hello", "version": "1.0", "type": "dpkg"},
                create_files=True,
            )
            work_request: WorkRequest = self.create_work_request_for_artifacts(
                environment_artifact, source_artifact, []
            )

        # Try unconfigured
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertEqual(
            context_data["request_data"]["host_architecture"], "amd64"
        )

        # Try configured
        work_request.configured_task_data = work_request.task_data.copy()
        work_request.configured_task_data["host_architecture"] = "arm64"
        work_request.save()
        autopkgtest_view = AutopkgtestView(work_request)
        context_data = autopkgtest_view.get_context_data()
        self.assertEqual(
            context_data["request_data"]["host_architecture"], "arm64"
        )
