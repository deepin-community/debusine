# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for DebDiff work request plugin."""
import textwrap
from typing import ClassVar
from unittest import mock

from django.template.response import SimpleTemplateResponse
from rest_framework import status
from unidiff.patch import PatchedFile

from debusine.artifacts.models import (
    ArtifactCategory,
    ArtifactData,
    DebianBinaryPackage,
    DebianSourcePackage,
    DebianUpload,
)
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    WorkRequest,
    Workspace,
)
from debusine.tasks import DebDiff
from debusine.test.django import TestCase
from debusine.web.templatetags.artifacts import artifact_link
from debusine.web.views.debdiff import DebDiffViewWorkRequestPlugin
from debusine.web.views.tests.utils import ViewTestMixin


class DebDiffViewTests(ViewTestMixin, TestCase):
    """Tests for DebDiffView."""

    workspace: ClassVar[Workspace]
    work_request: ClassVar[WorkRequest]
    debdiff_artifact: ClassVar[Artifact]

    @classmethod
    def create_artifact_with_relates(
        cls, artifact: Artifact, target_artifact_category: ArtifactCategory
    ) -> None:
        data: ArtifactData

        match target_artifact_category:
            case ArtifactCategory.UPLOAD:
                data = DebianUpload(
                    type="dpkg",
                    changes_fields={
                        "Architecture": "amd64",
                        "Files": [{"name": "hello_1.0_amd64.deb"}],
                    },
                )
            case ArtifactCategory.BINARY_PACKAGE:
                data = DebianBinaryPackage(
                    srcpkg_name="hello_1.0_amd64.deb",
                    srcpkg_version="1.0",
                    deb_fields={
                        "Package": "hello",
                        "Version": "1.0",
                        "Architecture": "amd64",
                    },
                    deb_control_files=[],
                )
            case ArtifactCategory.SOURCE_PACKAGE:
                data = DebianSourcePackage(
                    name="hello",
                    version="1.0",
                    type="dpkg",
                    dsc_fields={},
                )
            case _ as unreachable:
                raise NotImplementedError(f"{unreachable!r} not supported")

        target, _ = cls.playground.create_artifact(
            category=target_artifact_category, data=data
        )

        ArtifactRelation.objects.create(
            artifact=artifact,
            target=target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

    def test_button_content(self) -> None:
        """Test DebDiff button found and debdiff contents is included."""
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: b""},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.deb", "new": "hello-2.deb"},
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        debdiff_button = self.assertHasElement(
            tree, "//button[@id='nav-debdiff-tab']"
        )
        self.assertTextContentEqual(debdiff_button, "DebDiff")

        debdiff_contents = self.assertHasElement(
            tree, "//div[@id='nav-debdiff']"
        )
        h2 = debdiff_contents.xpath(".//h2")

        self.assertEqual(h2, ["Request"])

        # Assert content is displayed by default
        self.assertIn("show active", debdiff_contents.attrib["class"])

        files_contents = self.assertHasElement(tree, "//div[@id='nav-files']")
        self.assertNotIn("show active", files_contents.attrib["class"])

    def test_binary_artifacts_identical(self) -> None:
        debdiff_report_identical = textwrap.dedent(
            """
            File lists identical (after any substitutions)

            No differences were encountered between the control files
            """
        ).encode("utf-8")
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report_identical},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.deb", "new": "hello-2.deb"},
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert original and new files
        original = self.assertHasElement(tree, "//li[@id='original']")
        self.assertTextContentEqual(original, "Original: hello-1.deb")

        new = self.assertHasElement(tree, "//li[@id='new']")
        self.assertTextContentEqual(new, "New: hello-2.deb")

        # Assert text no changes reported
        no_changes_reported = self.assertHasElement(
            tree, "//p[@id='debdiff-report-no-differences']"
        )
        self.assertTextContentEqual(
            no_changes_reported, "DebDiff did not report any differences."
        )

    def test_binary_cannot_parse_too_short(self) -> None:
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: b""},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.deb", "new": "hello-2.deb"},
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        error_paragraph = self.assertHasElement(
            tree, "//div[@id='error-parsing-debdiff-output']"
        )

        self.assertTextContentEqual(
            error_paragraph,
            "Error parsing debdiff output: "
            "Cannot parse any information from debdiff output ",
        )

    def test_binary_cannot_parse_line(self) -> None:
        artifact, _ = self.playground.create_artifact(
            paths={
                DebDiff.CAPTURE_OUTPUT_FILENAME: b"Some line\n"
                b"\n"
                b"Files in second .changes but not in first\n"
                b"\n"
                b"Unrecognised\tline\n"
            },
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.deb", "new": "hello-2.deb"},
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        error_paragraph = self.assertHasElement(
            tree, "//div[@id='error-parsing-debdiff-output']"
        )

        self.assertTextContentEqual(
            error_paragraph,
            "Error parsing debdiff output: "
            "Failed to parse line: 'Unrecognised\\tline'",
        )

    def test_binary_all_sections_with_changes(self) -> None:
        debdiff_report = textwrap.dedent(
            """
            some debdiff text header"

            Files in second .deb but not in first
            -------------------------------------
            -rw-r--r--  root/root   /etc/simplemonitor/monitor.ini
            -rw-r--r--  root/root   DEBIAN/conffiles

            Files in first .deb but not in second
            ------------------------------------
            -rw-r--r--  root/root   /usr/lib/python3/dist-packages/pyaarlo
            -rw-r--r--  root/root   /usr/share/doc/python3-pyaarlo/READ.gz

            Control files: lines which differ (wdiff format)
            ------------------------------------------------
            Description: [-one description-] {+another description+}
            Homepage: [-https://github.com/twrecked/pyaarlo-]
            {+https://simplemonitor.readthedocs.io+}"""
        ).encode("utf-8")
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.deb", "new": "hello-2.deb"},
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert files in original not in new
        files_in_original_not_in_new = self.assertHasElement(
            tree, "//h4[@id='files-in-original-not-in-new']"
        )
        files = self.assertHasElement(
            files_in_original_not_in_new, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            files,
            "-rw-r--r--  root/root   /usr/lib/python3/dist-packages/pyaarlo\n"
            "-rw-r--r--  root/root   /usr/share/doc/python3-pyaarlo/READ.gz\n",
        )

        # Assert files in new not in original
        files_in_new_not_in_original = self.assertHasElement(
            tree, "//h4[@id='files-in-new-not-in-original']"
        )
        files = self.assertHasElement(
            files_in_new_not_in_original, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            files,
            "-rw-r--r--  root/root   /etc/simplemonitor/monitor.ini\n"
            "-rw-r--r--  root/root   DEBIAN/conffiles\n",
        )

        # Assert control diffs changes
        control_diffs_changes = self.assertHasElement(
            tree, "//h4[@id='control-diffs-changes']"
        )
        files = self.assertHasElement(
            control_diffs_changes, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            files,
            "Description: [-one description-] {+another description+}\n"
            "Homepage: [-https://github.com/twrecked/pyaarlo-] "
            "{+https://simplemonitor.readthedocs.io+}\n",
        )

    def test_binary_all_sections_with_no_control_package_changes(self) -> None:
        debdiff_report = textwrap.dedent(
            """
            File lists identical (after any substitutions)

            No differences were encountered between the control files of package python3-ping3

            No differences were encountered between the control files of package simplemonitor
            """  # noqa: E501
        ).encode("utf-8")

        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={
                "original": "python3-ping-1.deb simplemonitor-1.deb",
                "new": "python3-ping-2.deb simplemonitor-2.deb",
            },
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert control diffs changes
        control_diffs_changes = self.assertHasElement(
            tree, "//h4[@id='control-diffs-changes']"
        )
        text = self.assertHasElement(
            control_diffs_changes, "following-sibling::p[1]"
        )
        self.assertTextContentEqual(
            text,
            "No differences were encountered "
            "between the control files of packages:",
        )

        ul = self.assertHasElement(
            control_diffs_changes, "following-sibling::ul[1]"
        )

        li_elements = ul.xpath("./li")

        self.assertEqual(
            [li.text.strip() for li in li_elements],
            ["python3-ping3", "simplemonitor"],
        )

    def test_binary_control_package_changes_per_package(self) -> None:
        debdiff_report = textwrap.dedent(
            """
            [The following lists of changes regard files as different if they have
            different names, permissions or owners.]

            Control files of package python3-ping3: lines which differ (wdiff format)
            -------------------------------------------------------------------------
            Installed-Size: [-100-] {+101+}
            Version: [-4.0.4-2-] {+4.0.4-3+}

            Control files of package simplemonitor: lines which differ (wdiff format)
            -------------------------------------------------------------------------
            Installed-Size: [-2013-] {+2043+}
            Version: [-1.12.1-1-] {+1.13.0-1+}
            """  # noqa: E501
        ).encode("utf-8")
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={
                "original": "python3-ping-1.deb simplemonitor-1.deb",
                "new": "python3-ping-2.deb simplemonitor-2.deb",
            },
        )
        self.create_artifact_with_relates(artifact, ArtifactCategory.UPLOAD)

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert control diffs changes
        control_diffs_changes = self.assertHasElement(
            tree, "//h4[@id='control-diffs-changes']"
        )

        # check python3-ping3
        python3_ping3_title = self.assertHasElement(
            control_diffs_changes, "following-sibling::h5[1]"
        )
        self.assertTextContentEqual(
            python3_ping3_title, "Package: python3-ping3"
        )
        python3_ping3_change = self.assertHasElement(
            python3_ping3_title, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            python3_ping3_change,
            "Installed-Size: [-100-] {+101+}\n"
            "Version: [-4.0.4-2-] {+4.0.4-3+}\n",
        )

        # check simplemonitor
        simplemonitor_title = self.assertHasElement(
            control_diffs_changes, "following-sibling::h5[2]"
        )
        self.assertTextContentEqual(
            simplemonitor_title, "Package: simplemonitor"
        )
        simplemonitor_change = self.assertHasElement(
            simplemonitor_title, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            simplemonitor_change,
            "Installed-Size: [-2013-] {+2043+}\n"
            "Version: [-1.12.1-1-] {+1.13.0-1+}\n",
        )

    def test_binary_set_of_debs(self) -> None:
        debdiff_report = textwrap.dedent(
            """\
            Same package name appears more than once (possibly due to renaming): simplemonitor
            [The following lists of changes regard files as different if they have
            different names, permissions or owners.]

            Files in second set of .debs but not in first
            ---------------------------------------------
            -rw-r--r--  root/root   /lib/s/system/simplemonitor.service
            -rw-r--r--  root/root   /simplemonitor-1.1.0.dist-info/METADATA

            Files in first set of .debs but not in second
            ---------------------------------------------
            -rw-r--r--  root/root   .../s.../INSTALLER

            Control files of package simplemonitor: lines which differ (wdiff format)
            -------------------------------------------------------------------------
            Depends: adduser, python3-arrow, python3-boto3, python3-colorlog, [-python3-icmplib,-] python3-importlib-metadata, python3-jinja2, python3-paho-mqtt, python3-paramiko, python3-ping3, python3-psutil, python3-pyaarlo, python3-requests, python3-ring-doorbell, python3-twilio, python3:any, [-libjs-jquery (>= 3.6.0),-] libjs-sphinxdoc (>= [-8.1),-] {+5.2),+} sphinx-rtd-theme-common (>= [-3.0.2+dfsg)-] {+1.2.0+dfsg)+}
             [-.-]
            [- The package include a systemd service.-]
            Installed-Size: [-2298-] {+2006+}
            [-Recommends: systemd-]
            Version: [-1.14.0a-3-] {+1.12.0-1+}
            """  # noqa: E501
        ).encode("utf-8")

        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={
                "original": (
                    "python3-ping3_4.0.4-2_all.deb "
                    "simplemonitor_1.12.1-1_all.deb"
                ),
                "new": (
                    "python3-ping3_4.0.4-3_all.deb "
                    "simplemonitor_1.13.0-1_all.deb"
                ),
            },
        )
        self.create_artifact_with_relates(
            artifact, ArtifactCategory.BINARY_PACKAGE
        )

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert files in original not in new
        files_in_original_not_in_new = self.assertHasElement(
            tree, "//h4[@id='files-in-original-not-in-new']"
        )
        files = self.assertHasElement(
            files_in_original_not_in_new, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            files,
            "-rw-r--r--  root/root   .../s.../INSTALLER\n",
        )

        files_in_new_not_in_original = self.assertHasElement(
            tree, "//h4[@id='files-in-new-not-in-original']"
        )
        files = self.assertHasElement(
            files_in_new_not_in_original, "following-sibling::pre[1]"
        )
        self.assertTextContentEqual(
            files,
            "-rw-r--r--  root/root   /lib/s/system/simplemonitor.service\n"
            "-rw-r--r--  root/root   /simplemonitor-1.1.0.dist-info/METADATA\n",
        )

    def test_source(self) -> None:
        debdiff_report = textwrap.dedent(
            """\
            diff -u -N original/added.txt new/added.txt
            --- original/added.txt	1970-01-01 01:00:00.000000000 +0100
            +++ new/added.txt	2025-03-26 12:42:27.672906377 +0000
            @@ -0,0 +1 @@
            +new file
            diff -u -N original/changed.txt new/changed.txt
            --- original/changed.txt	2025-03-26 12:41:44.191924503 +0000
            +++ new/changed.txt	2025-03-26 12:42:21.945798421 +0000
            @@ -1,3 +1,2 @@
             1
             2
            -3
            diff -u -N original/removed.txt new/removed.txt
            --- original/removed.txt	2025-03-26 12:42:37.607381416 +0000
            +++ new/removed.txt	1970-01-01 01:00:00.000000000 +0100
            @@ -1 +0,0 @@
            -removed file
        """
        ).encode("utf-8")
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.dsc", "new": "hello-2.dsc"},
        )
        self.create_artifact_with_relates(
            artifact, ArtifactCategory.SOURCE_PACKAGE
        )
        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert files basic templating
        summary_changes = self.assertHasElement(
            tree, "//h2[@id='source-summary-of-changes']"
        )
        files = self.assertHasElement(
            summary_changes, "following-sibling::ul[1]"
        )

        self.assertTextContentEqual(
            files, "new/added.txt new/changed.txt new/removed.txt"
        )

        # Assert context data is correct
        assert isinstance(response, SimpleTemplateResponse)
        assert response.context_data is not None
        self.assertEqual(
            response.context_data["debdiff_source_summary"],
            [
                {
                    "diff_line_number": 2,
                    "operation": "added",
                    "path": "new/added.txt",
                },
                {
                    "diff_line_number": 7,
                    "operation": "modified",
                    "path": "new/changed.txt",
                },
                {
                    "diff_line_number": 14,
                    "operation": "removed",
                    "path": "new/removed.txt",
                },
            ],
        )

    def test_source_unexpected_file_status(self) -> None:
        debdiff_report = textwrap.dedent(
            """\
            --- a.txt	2025-03-27 08:38:25.845875929 +0000
            +++ b.txt	2025-03-27 08:38:43.129990008 +0000
            @@ -1 +1 @@
            -1
            +2
        """
        ).encode("utf-8")

        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.dsc", "new": "hello-2.dsc"},
        )
        self.create_artifact_with_relates(
            artifact, ArtifactCategory.SOURCE_PACKAGE
        )

        mock_file = mock.create_autospec(PatchedFile, instance=True)
        mock_file.is_added_file = False
        mock_file.is_removed_file = False
        mock_file.is_modified_file = False
        mock_file.path = "a.txt"

        with mock.patch(
            "debusine.web.views.debdiff.PatchSet", return_value=[mock_file]
        ):
            response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        error_paragraph = self.assertHasElement(
            tree, "//div[@id='error-parsing-debdiff-output']"
        )

        self.assertTextContentEqual(
            error_paragraph,
            "Error parsing debdiff output: "
            "Unexpected file status in diff: a.txt",
        )

    def test_source_artifacts_identical(self) -> None:
        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: b""},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.dsc", "new": "hello-2.dsc"},
        )
        self.create_artifact_with_relates(
            artifact, ArtifactCategory.SOURCE_PACKAGE
        )

        response = self.client.get(artifact.get_absolute_url())

        tree = self.assertResponseHTML(response)

        # Assert original and new as expected
        original = self.assertHasElement(tree, "//li[@id='original']")
        self.assertTextContentEqual(original, "Original: hello-1.dsc")
        new = self.assertHasElement(tree, "//li[@id='new']")
        self.assertTextContentEqual(new, "New: hello-2.dsc")

        # Assert text no changes reported
        no_changes_reported = self.assertHasElement(
            tree, "//p[@id='debdiff-report-no-differences']"
        )
        self.assertTextContentEqual(
            no_changes_reported, "DebDiff did not report any differences."
        )


class DebDiffViewWorkRequestPluginTests(TestCase):
    """Tests for DebDiffViewWorkRequestPlugin."""

    SAMPLE_TASK_DATA = {
        "input": {"source_artifacts": [421, 123]},
        "host_architecture": "amd64",
        "environment": "debian/match:codename=bookworm",
        "extra_flags": ["--dirs", "--nocontrol"],
    }

    def test_get_context_data_no_artifacts(self) -> None:
        work_request = self.playground.create_work_request(
            task_name="debdiff",
            task_data=self.SAMPLE_TASK_DATA,
            dynamic_task_data={"environment_id": 1},
        )
        plugin = DebDiffViewWorkRequestPlugin(work_request)

        self.assertEqual(
            plugin.get_context_data(),
            {
                "description_data": {
                    "environment_id": 1,
                    "extra_arguments": ["--dirs", "--nocontrol"],
                },
                "description_template": "web/_debdiff-description.html",
            },
        )

    def test_get_context_data(self) -> None:
        work_request = self.playground.create_work_request(
            task_name="debdiff", task_data=self.SAMPLE_TASK_DATA
        )
        work_request.dynamic_task_data = {
            "environment_id": 1,
            "input_source_artifacts_ids": [2, 3],
            "input_binary_artifacts_ids": [[4, 5], [6, 7]],
        }
        work_request.save()

        response = self.client.get(work_request.get_absolute_url())

        # Check context
        ctx = response.context

        description_data, description_template = (
            ctx["description_data"],
            ctx["description_template"],
        )

        self.assertEqual(
            description_data,
            {
                "binary_artifacts_new_ids": [6, 7],
                "binary_artifacts_original_ids": [4, 5],
                "environment_id": 1,
                "extra_arguments": ["--dirs", "--nocontrol"],
                "source_artifact_new_id": 3,
                "source_artifact_original_id": 2,
            },
        )

        self.assertEqual(description_template, "web/_debdiff-description.html")

        tree = self.assertResponseHTML(response)

        source_artifacts = self.assertHasElement(
            tree, "//p[@id='description-source_artifacts']"
        )
        self.assertTextContentEqual(
            source_artifacts,
            f"Compare source packages {artifact_link(2)} and "
            f"{artifact_link(3)} with debdiff",
        )

        self.assertFalse(tree.xpath("//p[@id='description-binary_artifacts']"))

        environment = self.assertHasElement(
            tree, "//li[@id='description-executed_in_environment']"
        )
        self.assertTextContentEqual(
            environment, f"Executed in environment {artifact_link(1)}"
        )

        extra_arguments = self.assertHasElement(
            tree, "//li[@id='description-extra_arguments']"
        )
        self.assertTextContentEqual(
            extra_arguments, "Extra arguments: --dirs --nocontrol"
        )

    def test_get_context_data_description_data_empty(self) -> None:
        """'description_data' is {}: 'dynamic_data' is None."""
        work_request = self.playground.create_work_request(
            task_name="debdiff", task_data=self.SAMPLE_TASK_DATA
        )
        self.assertIsNone(work_request.dynamic_task_data)

        response = self.client.get(work_request.get_absolute_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.context["description_data"], {})
