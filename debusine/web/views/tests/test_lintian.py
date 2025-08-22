# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for Lintian work request plugin."""
import copy
import json
import uuid
from pathlib import Path
from typing import ClassVar

from django.db.models import F
from django.template.loader import get_template

from debusine.artifacts import LintianArtifact, PackageBuildLog
from debusine.artifacts.models import DebianLintian, DebianLintianSummary
from debusine.db.models import Artifact, FileInArtifact, WorkRequest, Workspace
from debusine.tasks.models import (
    LintianData,
    LintianDynamicData,
    LintianFailOnSeverity,
    LintianInput,
    LookupMultiple,
)
from debusine.test.django import TestCase
from debusine.web.views.lintian import LintianView


class LintianViewTests(TestCase):
    """Tests for LintianView."""

    fail_on_severity: ClassVar[LintianFailOnSeverity]
    package_name: ClassVar[str]
    workspace: ClassVar[Workspace]
    binary_artifact: ClassVar[Artifact]
    source_artifact: ClassVar[Artifact]
    work_request: ClassVar[WorkRequest]
    package_source_file: ClassVar[str]
    package_binary_file_1: ClassVar[str]
    package_binary_file_2: ClassVar[str]

    DESCRIPTION_LINTIAN_RUN_ID = "description-lintian_run"

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.fail_on_severity = LintianFailOnSeverity.WARNING
        cls.workspace = cls.playground.create_workspace(
            name="Public", public=True
        )

        source_artifact, _ = cls.playground.create_artifact(
            paths=["hello.dsc"],
            create_files=True,
            workspace=cls.workspace,
        )
        binary_artifact_1, _ = cls.playground.create_artifact(
            paths=["hello-1.deb"],
            create_files=True,
            workspace=cls.workspace,
        )
        binary_artifact_2, _ = cls.playground.create_artifact(
            paths=["hello-2.deb"],
            create_files=True,
            workspace=cls.workspace,
        )

        cls.work_request = cls.playground.create_work_request(
            result=WorkRequest.Results.SUCCESS,
            task_data=LintianData(
                input=LintianInput(
                    source_artifact=source_artifact.id,
                    binary_artifacts=LookupMultiple.parse_obj(
                        [binary_artifact_1.id, binary_artifact_2.id]
                    ),
                ),
                fail_on_severity=cls.fail_on_severity,
            ),
            task_name="lintian",
            workspace=cls.workspace,
        )
        cls.package_name = "hello"

        cls.package_source_file = "hello_2.10-3.dsc"
        cls.package_binary_file_1 = "hello_2.10-3.deb"
        cls.package_binary_file_2 = "libhello_2.10-3.deb"

        summary_binary_package = DebianLintian(
            architecture="all",
            summary=DebianLintianSummary(
                package_filename={
                    "hello": cls.package_binary_file_1,
                    "libhello": cls.package_binary_file_2,
                },
                tags_found=[],
                overridden_tags_found=[],
                lintian_version="2.116.3",
                distribution="debian/bookworm",
                tags_count_by_severity={},
            ),
        ).dict()

        cls.binary_artifact, _ = cls.playground.create_artifact(
            paths=["summary.json", "lintian.txt"],
            create_files=True,
            workspace=cls.workspace,
        )
        cls.binary_artifact.category = LintianArtifact._category
        cls.binary_artifact.data = summary_binary_package
        cls.binary_artifact.created_by_work_request = cls.work_request
        cls.binary_artifact.save()

        summary_source_package = DebianLintian(
            architecture="source",
            summary=DebianLintianSummary(
                package_filename={
                    "hello": cls.package_source_file,
                },
                tags_found=[],
                overridden_tags_found=[],
                lintian_version="2.116.3",
                distribution="debian/bookworm",
                tags_count_by_severity={},
            ),
        ).dict()

        cls.source_artifact, _ = cls.playground.create_artifact(
            paths=["lintian.txt"],
            create_files=True,
            workspace=cls.workspace,
        )
        cls.source_artifact.category = LintianArtifact._category
        cls.source_artifact.data = summary_source_package
        cls.source_artifact.created_by_work_request = cls.work_request
        cls.source_artifact.save()

        build_log_artifact, _ = cls.playground.create_artifact(
            paths=["some-file.log"],
            create_files=True,
            workspace=cls.workspace,
        )
        build_log_artifact.category = PackageBuildLog._category
        build_log_artifact.save()

    def add_file_in_artifact(
        self,
        artifact: Artifact,
        path: str,
        contents: bytes,
        complete: bool = True,
    ) -> None:
        """
        Add file in the artifact.

        :param artifact: artifact that the file is added to
        :param path: path in the artifact
        :param contents: contents written into the file that is added in the
          artifact
        """
        file_backend = artifact.workspace.scope.file_stores.order_by(
            F("filestoreinscope__upload_priority").desc(nulls_last=True)
        )[0].get_backend_object()
        if complete:
            fileobj = self.playground.create_file_in_backend(
                file_backend, contents
            )
        else:
            fileobj = self.playground.create_file(contents)
        FileInArtifact.objects.create(
            artifact=artifact, path=path, file=fileobj, complete=complete
        )

    def create_analysis(self) -> Path:
        """Create an analysis file with tags (without the summary)."""
        contents = json.dumps(
            {
                "summary": {"package_filename": {"hello": "hello_2.10-3.dsc"}},
                "tags": [
                    {
                        "severity": "pedantic",
                        "package": "hello",
                        "tag": "license-problem-gfdl-non-official-text",
                        "explanation": "The given source file is licensed...",
                        "comment": "this is a comment",
                        "note": "invariant part is\\n: with no invariant...",
                        "pointer": "debian/copyright",
                    },
                    {
                        "severity": "pedantic",
                        "package": "hello",
                        "tag": "license-problem-gfdl-non-official-text",
                        "explanation": "The given source file is licensed...",
                        "comment": "",
                        "note": "invariant part is: with no invariant...",
                        "pointer": "doc/hello.info",
                    },
                    {
                        "severity": "warning",
                        "package": "hello",
                        "tag": "some-other-tag",
                        "explanation": "",
                        "comment": "",
                        "note": "some-other-tag note...",
                        "pointer": "src/some-other-tag-file",
                    },
                ],
            }
        ).encode("utf-8")
        return self.create_temporary_file(contents=contents)

    def test_get_context_data(self) -> None:
        """Test get_context_data() return the expected context data."""
        lintian_txt_path = (
            self.binary_artifact.get_absolute_url_download() + "lintian.txt"
        )
        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        # Verify some keys in separate methods
        context_data.pop("tags")
        context_data.pop("tags_count_severity")
        context_data.pop("request_data")

        self.assertEqual(
            context_data,
            {
                "result": self.work_request.result,
                "lintian_txt_path": lintian_txt_path,
                "fail_on_severity": self.fail_on_severity,
                "description_data": {},
                "description_template": "web/_lintian-description.html",
                "specialized_tab": {
                    "label": "Lintian",
                    "slug": "lintian",
                    "template": "web/_lintian-work_request-detail.html",
                },
            },
        )

    def test_get_context_data_verify_tag_count_severity(self) -> None:
        """Test get_context_data(): verify "tags_count_severity"."""
        self.binary_artifact.data["summary"] = {
            "package_filename": {"hello": "hello_2.10-3.deb"},
            "tags_count_by_severity": {
                "classification": 0,
                "error": 0,
                "experimental": 0,
                "info": 0,
                "overridden": 0,
                "pedantic": 2,
                "warning": 1,
            },
        }
        self.binary_artifact.save()

        self.source_artifact.data["summary"] = {
            "package_filename": {"hello": "hello_2.10-3.dsc"},
            "tags_count_by_severity": {
                "classification": 0,
                "error": 0,
                "experimental": 0,
                "info": 0,
                "overridden": 5,
                "pedantic": 1,
                "warning": 0,
            },
        }
        self.source_artifact.save()

        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        self.assertEqual(
            context_data["tags_count_severity"],
            {
                "classification": 0,
                "error": 0,
                "experimental": 0,
                "info": 0,
                "overridden": 5,
                "pedantic": 3,
                "warning": 1,
            },
        )

    def test_get_context_data_verify_tags(self) -> None:
        """Test get_context_data(): verify "tags"."""
        self.add_file_in_artifact(
            self.binary_artifact,
            "analysis.json",
            self.create_analysis().read_bytes(),
        )

        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        expected = {
            "hello_2.10-3.dsc": {
                "license-problem-gfdl-non-official-text": {
                    "explanation": "The given source file is licensed...",
                    "severity": "pedantic",
                    "occurrences": [
                        {
                            "pointer": "debian/copyright",
                            "note": "invariant part is\n: with no invariant...",
                            "comment": "this is a comment",
                        },
                        {
                            "pointer": "doc/hello.info",
                            "note": "invariant part is: with no invariant...",
                            "comment": "",
                        },
                    ],
                },
                "some-other-tag": {
                    "explanation": "",
                    "severity": "warning",
                    "occurrences": [
                        {
                            "pointer": "src/some-other-tag-file",
                            "note": "some-other-tag note...",
                            "comment": "",
                        }
                    ],
                },
            }
        }

        actual = {
            filename: {tag_name: tag.dict() for tag_name, tag in tags.items()}
            for filename, tags in context_data["tags"].items()
        }

        uuid1 = actual["hello_2.10-3.dsc"][
            "license-problem-gfdl-non-official-text"
        ].pop("uuid")
        self.assertIsInstance(uuid1, uuid.UUID)

        uuid2 = actual["hello_2.10-3.dsc"]["some-other-tag"].pop("uuid")
        self.assertIsInstance(uuid2, uuid.UUID)

        self.assertEqual(actual, expected)

    def test_get_context_data_verify_request(self) -> None:
        """Test get_context_data(): verify "request_data"."""
        task_data_input = self.work_request.task_data["input"]
        self.work_request.dynamic_task_data = LintianDynamicData(
            input_source_artifact_id=task_data_input["source_artifact"],
            input_binary_artifacts_ids=task_data_input["binary_artifacts"],
        ).dict(exclude_unset=True)
        self.work_request.save()

        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        expected = {
            "source_artifact": {
                "files": ["hello.dsc"],
                "id": task_data_input["source_artifact"],
                "lookup": task_data_input["source_artifact"],
                "artifact": Artifact.objects.get(
                    id=task_data_input["source_artifact"]
                ),
            },
            "binary_artifacts": {
                "lookup": task_data_input["binary_artifacts"],
                "artifacts": [
                    {
                        "files": ["hello-1.deb"],
                        "id": task_data_input["binary_artifacts"][0],
                        "artifact": Artifact.objects.get(
                            id=task_data_input["binary_artifacts"][0]
                        ),
                    },
                    {
                        "files": ["hello-2.deb"],
                        "id": task_data_input["binary_artifacts"][1],
                        "artifact": Artifact.objects.get(
                            id=task_data_input["binary_artifacts"][1]
                        ),
                    },
                ],
            },
        }

        self.assertEqual(context_data["request_data"], expected)

    def test_get_context_data_no_dynamic_task_data(self) -> None:
        """If there is no dynamic data, context data omits artifact info."""
        task_data_input = self.work_request.task_data["input"]

        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        self.assertEqual(
            context_data["request_data"]["source_artifact"],
            {
                "lookup": task_data_input["source_artifact"],
                "id": None,
                "files": [],
            },
        )
        self.assertEqual(
            context_data["request_data"]["binary_artifacts"],
            {"lookup": task_data_input["binary_artifacts"], "artifacts": []},
        )

    def test_get_context_data_missing_source_artifact(self) -> None:
        """If the source artifact is missing, context data omits its files."""
        task_data_input = self.work_request.task_data["input"]
        self.work_request.dynamic_task_data = LintianDynamicData(
            input_source_artifact_id=task_data_input["source_artifact"],
            input_binary_artifacts_ids=task_data_input["binary_artifacts"],
        ).dict(exclude_unset=True)
        self.work_request.save()
        source_artifact_id = task_data_input["source_artifact"]
        source_artifact = Artifact.objects.get(id=source_artifact_id)
        source_artifact.files.clear()
        source_artifact.delete()
        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        self.assertEqual(
            context_data["request_data"]["source_artifact"],
            {"lookup": source_artifact_id, "id": None, "files": []},
        )

    def test_get_context_data_missing_binary_artifact(self) -> None:
        """If a binary artifact is missing, context data omits its files."""
        task_data_input = self.work_request.task_data["input"]
        self.work_request.dynamic_task_data = LintianDynamicData(
            input_source_artifact_id=task_data_input["source_artifact"],
            input_binary_artifacts_ids=task_data_input["binary_artifacts"],
        ).dict(exclude_unset=True)
        self.work_request.save()
        binary_artifact_0_id = task_data_input["binary_artifacts"][0]
        binary_artifact_0 = Artifact.objects.get(id=binary_artifact_0_id)
        binary_artifact_0.files.clear()
        binary_artifact_0.delete()
        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        self.assertEqual(
            context_data["request_data"]["binary_artifacts"],
            {
                "lookup": task_data_input["binary_artifacts"],
                "artifacts": [
                    {
                        "files": [],
                        "id": task_data_input["binary_artifacts"][0],
                        "artifact": None,
                    },
                    {
                        "files": ["hello-2.deb"],
                        "id": task_data_input["binary_artifacts"][1],
                        "artifact": Artifact.objects.get(
                            id=task_data_input["binary_artifacts"][1]
                        ),
                    },
                ],
            },
        )

    def test_get_context_data_skips_incomplete_files(self) -> None:
        """get_context_data() skips incomplete files."""
        self.source_artifact.fileinartifact_set.update(complete=False)
        self.binary_artifact.fileinartifact_set.update(complete=False)
        self.add_file_in_artifact(
            self.binary_artifact,
            "analysis.json",
            self.create_analysis().read_bytes(),
            complete=False,
        )

        lintian_view = LintianView(self.work_request)
        context_data = lintian_view.get_context_data()

        self.assertIsNone(context_data["lintian_txt_path"])
        self.assertEqual(context_data["tags"], {})

    def test_template_output(self) -> None:
        """Generic output of the template."""
        task_data_input = self.work_request.task_data["input"]
        self.work_request.dynamic_task_data = LintianDynamicData(
            input_source_artifact_id=task_data_input["source_artifact"],
            input_binary_artifacts_ids=task_data_input["binary_artifacts"],
        ).dict(exclude_unset=True)
        self.work_request.save()
        self.add_file_in_artifact(
            self.binary_artifact,
            "analysis.json",
            self.create_analysis().read_bytes(),
        )

        response = self.client.get(self.work_request.get_absolute_url())

        result_output = get_template("web/_work_request-result.html").render(
            {"result": self.work_request.result}
        )
        self.assertContains(response, f"Result: {result_output}", html=True)
        self.assertContains(
            response, f"Fail on: {self.fail_on_severity}", html=True
        )

        lintian_txt_path = (
            self.binary_artifact.get_absolute_url_download() + "lintian.txt"
        )
        self.assertContains(
            response,
            f'<a href="{lintian_txt_path}">Lintian output</a>',
            html=True,
        )

        # Test code contains the list (testing part of the list) of
        # the summary of the tags
        self.assertContains(
            response,
            "<li>error: 0</li>",
            html=True,
        )
        self.assertContains(
            response,
            "<li>warning: 0</li>",
            html=True,
        )

        task_data_input = self.work_request.task_data["input"]

        tag = response.context["tags"]["hello_2.10-3.dsc"][
            "license-problem-gfdl-non-official-text"
        ].occurrences[0]

        tag_output = get_template("web/_lintian_tag.html").render({"tag": tag})
        self.assertContains(response, f"<li>{tag_output}</li>", html=True)

    def test_template_output_include_note_pointer_comment(self) -> None:
        """Template output of _lintian_tag.html include all the information."""
        tag = {
            "note": "invariant part is:\nwith no invariant",
            "pointer": "debian/copyright",
            "comment": "upstream",
        }

        rendered = get_template("web/_lintian_tag.html").render({"tag": tag})

        self.assertIn("invariant part is:<br>with no invariant", rendered)
        self.assertIn(f"<code>[{tag['pointer']}]</code>", rendered)
        self.assertIn("Comment:", rendered)
        self.assertIn(f"<pre>{tag['comment']}</pre>", rendered)

    def test_template_output_do_not_include_non_needed_information(
        self,
    ) -> None:
        """Template _lintian_tag.html does not output note, comment, pointer."""
        tag = {"note": "", "pointer": "", "comment": ""}
        rendered = get_template("web/_lintian_tag.html").render({"tag": tag})

        self.assertIn("Lintian did not output a note for this tag", rendered)
        self.assertNotIn("<code>", rendered)
        self.assertNotIn("Comment:", rendered)
        self.assertNotIn("<pre></pre>", rendered)

    def test_template_output_no_source_artifact(self) -> None:
        """The view renders OK if there was no source artifact in the input."""
        del self.work_request.task_data["input"]["source_artifact"]
        self.work_request.dynamic_task_data = LintianDynamicData(
            input_binary_artifacts_ids=self.work_request.task_data["input"][
                "binary_artifacts"
            ],
        ).dict(exclude_unset=True)
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())

        self.assertNotContains(response, "Source artifact", html=True)

    def test_template_output_no_binary_artifacts(self) -> None:
        """The view renders OK if there was no binary artifact in the input."""
        del self.work_request.task_data["input"]["binary_artifacts"]
        self.work_request.dynamic_task_data = LintianDynamicData().dict(
            exclude_unset=True
        )
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertNotContains(response, "Binary artifact", html=True)

    def test_find_lintian_txt_url_path_no_artifacts(self) -> None:
        """_find_lintian_txt_url_path() with no artifacts: return None."""
        self.assertIsNone(LintianView._find_lintian_txt_url_path([]))

    def test_find_lintian_txt_url_path_artifact_no_files(self) -> None:
        """_find_lintian_txt_url_path(), artifact no files: return None."""
        artifact, _ = self.playground.create_artifact()

        self.assertIsNone(LintianView._find_lintian_txt_url_path([artifact]))

    def test_template_output_no_lintian_txt(self) -> None:
        """The view renders OK if no lintian.txt was output."""
        self.source_artifact.fileinartifact_set.all().delete()
        self.binary_artifact.fileinartifact_set.all().delete()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertNotContains(response, "Lintian output", html=True)

    def test_show_configured_task_data(self) -> None:
        """The view shows configured task data if present."""
        orig_source_id = self.work_request.task_data["input"]["source_artifact"]
        configured_source_id = orig_source_id + 42

        # Try unconfigured
        self.work_request.configured_task_data = None
        self.work_request.dynamic_task_data = None
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertEqual(
            response.context["request_data"]["source_artifact"]["lookup"],
            orig_source_id,
        )

        # Try configured
        self.work_request.configured_task_data = copy.deepcopy(
            self.work_request.task_data
        )
        self.work_request.configured_task_data["input"][
            "source_artifact"
        ] = configured_source_id
        self.work_request.save()
        response = self.client.get(self.work_request.get_absolute_url())
        self.assertEqual(
            response.context["request_data"]["source_artifact"]["lookup"],
            configured_source_id,
        )

    def test_description_data(self) -> None:
        self.work_request.task_data["include_tags"] = [
            "included-1",
            "included-2",
        ]
        self.work_request.task_data["exclude_tags"] = [
            "excluded-1",
            "excluded-2",
        ]

        # Set some dynamic_data
        task_data_input = self.work_request.task_data["input"]
        self.work_request.dynamic_task_data = LintianDynamicData(
            input_source_artifact_id=task_data_input["source_artifact"],
            input_binary_artifacts_ids=task_data_input["binary_artifacts"],
            environment_id=0,
            subject="hello",
        ).dict(exclude_unset=True)
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())

        # Check contents of context
        ctx = response.context
        description_data, description_template = (
            ctx["description_data"],
            ctx["description_template"],
        )
        self.assertEqual(
            description_data,
            {
                "source_artifact_id": task_data_input["source_artifact"],
                "binary_artifacts_ids": task_data_input["binary_artifacts"],
                "environment_id": 0,
                "exclude_tags": ["excluded-1", "excluded-2"],
                "include_tags": ["included-1", "included-2"],
                "fail_on_severity": "warning",
                "host_architecture": None,
                "package_name": "hello",
                "target_distribution": "debian:unstable",
            },
        )
        self.assertEqual(description_template, "web/_lintian-description.html")

        # Check HTML output
        tree = self.assertResponseHTML(response)

        lintian_run = self.assertHasElement(
            tree, f"//p[@id='{self.DESCRIPTION_LINTIAN_RUN_ID}']"
        )
        self.assertTextContentEqual(
            lintian_run, "Lintian run for hello package."
        )

        source_packages = self.assertHasElement(
            tree, "//li[@id='description-source_artifacts']"
        )
        self.assertTextContentEqual(
            source_packages, "Source packages: debusine:test"
        )

        binary_packages = self.assertHasElement(
            tree, "//li[@id='description-binary_artifacts']"
        )
        self.assertTextContentEqual(
            binary_packages, "Binary packages: debusine:test, debusine:test"
        )

        environment = self.assertHasElement(
            tree, "//li[@id='description-environment']"
        )
        self.assertTextContentEqual(
            environment, "Executed in environment 0 (deleted)"
        )

        fail_on_severity = self.assertHasElement(
            tree, "//li[@id='description-fail_on_severity']"
        )
        self.assertTextContentEqual(
            fail_on_severity, "Fail on severity: warning"
        )

        host_architecture = self.assertHasElement(
            tree, "//li[@id='description-host_architecture']"
        )
        self.assertTextContentEqual(
            host_architecture, "Host architecture: auto"
        )

        target_distribution = self.assertHasElement(
            tree, "//li[@id='description-target_distribution']"
        )
        self.assertTextContentEqual(
            target_distribution, "Target distribution: debian:unstable"
        )

        include_tags = self.assertHasElement(
            tree, "//li[@id='description-include_tags']"
        )
        self.assertTextContentEqual(
            include_tags, "Include tags: included-1 included-2"
        )

        exclude_tags = self.assertHasElement(
            tree, "//li[@id='description-exclude_tags']"
        )
        self.assertTextContentEqual(
            exclude_tags, "Exclude tags: excluded-1 excluded-2"
        )

    def test_description_data_not_configured(self) -> None:
        response = self.client.get(self.work_request.get_absolute_url())

        # Check contents of context
        ctx = response.context
        description_data, description_template = (
            ctx["description_data"],
            ctx["description_template"],
        )

        self.assertEqual(description_data, {})
        self.assertEqual(description_template, "web/_lintian-description.html")

        # Does not contain description related information because of no data
        tree = self.assertResponseHTML(response)
        self.assertFalse(
            tree.xpath(f"//p[@id='{self.DESCRIPTION_LINTIAN_RUN_ID}']")
        )
