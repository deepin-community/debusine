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
from unidiff.patch import PatchedFile

from debusine.artifacts.models import (
    ArtifactCategory,
    ArtifactData,
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

    def test_test_binary_cannot_parse_line(self) -> None:
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
