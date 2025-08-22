# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Lintian class."""
import datetime
import itertools
import json
import textwrap
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, cast
from unittest import mock
from unittest.mock import MagicMock, call

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts import LintianArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianLintianSummary,
    DebianSourcePackage,
    DebianUpload,
    EmptyArtifactData,
)
from debusine.client.models import (
    FileResponse,
    FilesResponseType,
    StrMaxLength255,
)
from debusine.tasks import Lintian, TaskConfigError
from debusine.tasks.executors import InstanceInterface
from debusine.tasks.models import (
    LintianDynamicData,
    LintianFailOnSeverity,
    LookupMultiple,
)
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_remote_artifact,
    create_system_tarball_data,
)


class LintianTests(ExternalTaskHelperMixin[Lintian], TestCase):
    """Tests for Lintian."""

    SAMPLE_TASK_DATA = {
        "input": {
            "source_artifact": 5,
        },
        "environment": "debian/match:codename=bookworm:architecture=amd64",
    }

    def setUp(self) -> None:
        """Initialize test objects."""
        super().setUp()
        self.task = Lintian(self.SAMPLE_TASK_DATA)

        # Different tests might try to get the lintian version when
        # preparing the summary. It's not relevant and the CI system
        # might not have "lintian" installed
        self._get_lintian_version_patcher = self.patch_get_lintian_version(
            "1.0.0"
        )

    def tearDown(self) -> None:
        """Delete objects."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def assert_parse_output(
        self, output: str, expected: list[dict[str, str]]
    ) -> None:
        """
        Assert parsed output from Lintian._parse_output is as expected.

        :param output: A string that is saved into a file to be used
          by Lintian._parse_output
        :param expected: A list containing the expected parsed output
        """
        self.assertEqual(
            Lintian._parse_output(
                self.create_temporary_file(contents=output.encode("utf-8"))
            ),
            expected,
        )

    def test_parse_output_empty(self) -> None:
        """Parse an empty output."""
        output = ""
        expected: list[dict[str, str]] = []

        self.assert_parse_output(output, expected)

    def test_parse_output_only_N_lines(self) -> None:
        """Parse an output with only N: lines. They are ignored."""
        output = (
            "N:   This package does not use a machine-readable...\n"
            "N:   second line\n"
        )
        expected: list[dict[str, str]] = []

        self.assert_parse_output(output, expected)

    def test_parse_output_simple(self) -> None:
        """Parse tag without note, neither pointer."""
        output = "E: qnetload: copyright-contains-dh_make-todo-boilerplate"
        expected = [
            {
                "severity": "error",
                "package": "qnetload",
                "tag": "copyright-contains-dh_make-todo-boilerplate",
                "note": "",
                "pointer": "",
                "explanation": "",
                "comment": "",
            }
        ]

        self.assert_parse_output(output, expected)

    def test_parse_tag_line_bullseye(self) -> None:
        """Parse tag line on bullseye that is duplicated information: ignore."""
        output = "W: package-uses-deprecated-debhelper-compat-version"
        self.assertIsNone(Lintian._parse_tag_line(output))

    def test_parse_output_bullseye(self) -> None:
        """Ignore line with "W: {tag}" (not including package name)."""
        output = textwrap.dedent(
            """\
            W: hello source: package-uses-deprecated-debhelper-compat-version 9
            N:
            W: package-uses-deprecated-debhelper-compat-version
            N:
            N: The debhelper compatibility version used by this package...
            N:
            N: The compatibility version can be set...
            N:
            W: hello source: national-encoding debian/po/sv.po
            N:
            W: national-encoding
            N:
            N: A file is not valid UTF-8.
            N:
            N: Debian has used UTF-8 for many years...
            """
        )
        explanation_1 = textwrap.dedent(
            """\
            The debhelper compatibility version used by this package...

            The compatibility version can be set..."""
        )
        explanation_2 = textwrap.dedent(
            """\
            A file is not valid UTF-8.

            Debian has used UTF-8 for many years..."""
        )

        expected = [
            {
                "comment": "",
                "explanation": explanation_1,
                "note": "9",
                "package": "hello source",
                "pointer": "",
                "severity": "warning",
                "tag": "package-uses-deprecated-debhelper-compat-version",
            },
            {
                "comment": "",
                "explanation": explanation_2,
                "note": "debian/po/sv.po",
                "package": "hello source",
                "pointer": "",
                "severity": "warning",
                "tag": "national-encoding",
            },
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_with_explanation(self) -> None:
        """Parse tag with explanation."""
        output = textwrap.dedent(
            """\
            N:
            I: hello: hardening-no-bindnow [usr/bin/hello]
            N:
            N:   This package provides an ELF...
            N:
            N:   This is needed (together with "relro")...
            N:
            N:
            I: hello: typo-in-manual-page add [usr/share/man/man1/hello.1.gz:27]
            N:
            N:   Test
            """
        )

        explanation_1 = textwrap.dedent(
            """\
            This package provides an ELF...

            This is needed (together with "relro")..."""
        )

        explanation_2 = "Test"

        expected = [
            {
                "severity": "info",
                "package": "hello",
                "tag": "hardening-no-bindnow",
                "note": "",
                "pointer": "usr/bin/hello",
                "explanation": explanation_1,
                "comment": "",
            },
            {
                "severity": "info",
                "package": "hello",
                "tag": "typo-in-manual-page",
                "note": "add",
                "pointer": "usr/share/man/man1/hello.1.gz:27",
                "explanation": explanation_2,
                "comment": "",
            },
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_overridden(self) -> None:
        """Parse output with overridden and a comment."""
        output = textwrap.dedent(
            """\
            N:
            I: hello source: hardening-no-bindnow [usr/bin/hello]
            N:
            N:   This package provides an ELF...
            N:
            N:   This is needed (together with "relro")...
            N:
            N:
            N: Lintian detect a source file but is a hand generated example so
            N: it is ignored
            O: python-cloudscraper source: source-is-missing [tests/fixtures/js_challenge-27-05-2020.html]
            N:
            N:   The source of the following file is missing. Lintian checked a few
            N:   possible paths to find the source, and did not find it.
            N:
            N:   Please repack your package to include the source or add it to
            N:   "debian/missing-sources" directory.
            N:
            N:   Please note, that very-long-line-length-in-source-file tagged
            N:   files are likely tagged source-is-missing. It is a feature
            N:   not a bug.
            N:
            N:   Visibility: error
            N:   Show-Always: no
            N:   Check: files/source-missing
            N:
            N:
            N: Same as before
            O: python-cloudscraper source: source-is-missing [tests/fixtures/js_challenge1_16_05_2020.html]
            N:
            N:
            """  # noqa: E501
        )

        explanation_info = textwrap.dedent(
            """\
            This package provides an ELF...

            This is needed (together with "relro")..."""
        )

        explanation_overridden_1 = textwrap.dedent(
            """\
            The source of the following file is missing. Lintian checked a few
            possible paths to find the source, and did not find it.

            Please repack your package to include the source or add it to
            "debian/missing-sources" directory.

            Please note, that very-long-line-length-in-source-file tagged
            files are likely tagged source-is-missing. It is a feature
            not a bug.

            Visibility: error
            Show-Always: no
            Check: files/source-missing"""
        )

        comment_overridden_1 = textwrap.dedent(
            """\
            Lintian detect a source file but is a hand generated example so
            it is ignored"""
        )

        explanation_overridden_2 = ""
        comment_overridden_2 = "Same as before"

        expected = [
            {
                "severity": "info",
                "package": "hello source",
                "tag": "hardening-no-bindnow",
                "note": "",
                "pointer": "usr/bin/hello",
                "explanation": explanation_info,
                "comment": "",
            },
            {
                "severity": "overridden",
                "package": "python-cloudscraper source",
                "tag": "source-is-missing",
                "note": "",
                "pointer": "tests/fixtures/js_challenge-27-05-2020.html",
                "explanation": explanation_overridden_1,
                "comment": comment_overridden_1,
            },
            {
                "severity": "overridden",
                "package": "python-cloudscraper source",
                "tag": "source-is-missing",
                "note": "",
                "pointer": "tests/fixtures/js_challenge1_16_05_2020.html",
                "explanation": explanation_overridden_2,
                "comment": comment_overridden_2,
            },
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_source(self) -> None:
        """Parse tag with "package_name source."."""
        output = (
            "P: hello source: license-problem-gfdl-non-official-text invariant "
            "part is: with no invariant"
        )
        expected = [
            {
                "severity": "pedantic",
                "package": "hello source",
                "tag": "license-problem-gfdl-non-official-text",
                "note": "invariant part is: with no invariant",
                "pointer": "",
                "explanation": "",
                "comment": "",
            }
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_with_information_one_word(self) -> None:
        """Parse tag with note."""
        output = (
            "E: qnetload: description-contains-invalid-control-statement line 3"
        )
        expected = [
            {
                "severity": "error",
                "package": "qnetload",
                "tag": "description-contains-invalid-control-statement",
                "note": "line 3",
                "pointer": "",
                "explanation": "",
                "comment": "",
            }
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_with_information_and_file(self) -> None:
        """Parse tag for a source package with note and pointer."""
        output = (
            "E: qnetload source: the-tag some information [debian/changelog:33]"
        )
        expected = [
            {
                "severity": "error",
                "package": "qnetload source",
                "tag": "the-tag",
                "note": "some information",
                "pointer": "debian/changelog:33",
                "explanation": "",
                "comment": "",
            }
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_with_file(self) -> None:
        """Parse tag with pointer."""
        output = (
            "E: qnetload: changelog-is-dh_make-template "
            "[usr/share/doc/qnetload/changelog.Debian.gz:1]"
        )
        expected = [
            {
                "severity": "error",
                "package": "qnetload",
                "tag": "changelog-is-dh_make-template",
                "note": "",
                "pointer": "usr/share/doc/qnetload/changelog.Debian.gz:1",
                "explanation": "",
                "comment": "",
            }
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_severity_masked_ignored(self) -> None:
        """Parse output severity is Masked: ignored."""
        output = (
            "M: xserver-xorg-video-sisusb source: "
            "very-long-line-length-in-source-file 705 > 512 [configure:15910]"
        )
        expected: list[dict[str, str]] = []

        self.assert_parse_output(output, expected)

    def test_parse_output_severity_c(self) -> None:
        """Parse output severity is Classification."""
        output = (
            "C: xserver-xorg-video-sisusb source: "
            "very-long-line-length-in-source-file 705 > 512 [configure:15910]"
        )
        expected = [
            {
                "severity": "classification",
                "tag": "very-long-line-length-in-source-file",
                "package": "xserver-xorg-video-sisusb source",
                "note": "705 > 512",
                "pointer": "configure:15910",
                "explanation": "",
                "comment": "",
            }
        ]

        self.assert_parse_output(output, expected)

    def test_parse_output_raise_value_error(self) -> None:
        """Output cannot be parsed."""
        line = "XXX"
        with self.assertRaisesRegex(
            ValueError, f"Failed to parse line: {line}"
        ):
            Lintian._parse_output(
                self.create_temporary_file(contents=line.encode("utf-8"))
            )

    def test_task_succeeded_empty_file_return_true(self) -> None:
        """task_succeeded() for an empty file return True."""
        directory = self.create_temporary_directory()
        (directory / Lintian.CAPTURE_OUTPUT_FILENAME).write_text("")

        self.configure_task(
            override={"fail_on_severity": LintianFailOnSeverity.WARNING}
        )
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_task_succeed_severity_less_than_fail_on_return_true(self) -> None:
        """task_succeeded() severity < than fail_on_severity: return True."""
        directory = self.create_temporary_directory()
        (directory / Lintian.CAPTURE_OUTPUT_FILENAME).write_text(
            "W: cynthiune.app: vcs-obsolete"
        )

        self.task._package_name_to_filename = {
            "cynthiune.app": "cynthiune-app.deb"
        }
        self.task._architecture_to_packages["all"] = {"cynthiune.app"}

        self.configure_task(
            override={"fail_on_severity": LintianFailOnSeverity.ERROR}
        )

        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_task_succeeded_severity_equal_as_fail_on_return_false(
        self,
    ) -> None:
        """task_succeeded() severity == fail_on_severity: return False."""
        directory = self.create_temporary_directory()
        (directory / Lintian.CAPTURE_OUTPUT_FILENAME).write_text(
            "W: cynthiune.app: vcs-obsolete"
        )

        self.configure_task(
            override={"fail_on_severity": LintianFailOnSeverity.WARNING}
        )

        self.task._package_name_to_filename = {
            "cynthiune.app": "cynthiune-app.deb"
        }
        self.task._architecture_to_packages["all"] = {"cynthiune.app"}

        self.assertFalse(self.task.task_succeeded(0, directory))

    def test_task_succeeded_fail_on_none(self) -> None:
        """task_succeeded() fail_on_severity == "none": return True."""
        directory = self.create_temporary_directory()
        (directory / Lintian.CAPTURE_OUTPUT_FILENAME).write_text(
            "E: cynthiune.app: vcs-obsolete\nW: hello: invalid-something\n"
        )

        self.configure_task(
            override={"fail_on_severity": LintianFailOnSeverity.NONE}
        )

        self.task._package_name_to_filename = {
            "cynthiune.app": "cynthiune-app.deb",
            "hello": "hello.deb",
        }
        self.task._architecture_to_packages["all"] = {"cynthiune.app"}
        self.task._architecture_to_packages["amd64"] = {"hello"}

        # Succeeded (fail_on_severity: none)
        self.assertTrue(self.task.task_succeeded(0, directory))

    @staticmethod
    def get_analysis_no_tags() -> dict[str, Any]:
        """Return a basic analysis without any Lintian tags."""
        return {
            "summary": {
                "distribution": "debian:unstable",
                "lintian_version": "1.0.0",
                "overridden_tags_found": [],
                "package_filename": {},
                "tags_count_by_severity": {
                    "classification": 0,
                    "error": 0,
                    "experimental": 0,
                    "info": 0,
                    "overridden": 0,
                    "pedantic": 0,
                    "warning": 0,
                },
                "tags_found": [],
            },
            "tags": [],
            "version": 1,
        }

    def assert_architecture_to_analysis(
        self,
        output_config: dict[str, bool],
        expected_architecture_to_analysis: dict[str, dict[str, Any]],
    ) -> None:
        """
        _create_architecture_to_analysis return the expected output.

        The real analysis test is implemented in test_create_analysis(). This
        test is only to check that only the relevant architectures are
        returned.
        """
        self.configure_task(override={"output": output_config})
        for architecture in ("source", "all", "amd64"):
            self.task._architecture_to_packages[architecture] = set()

        lintian_file = self.create_temporary_file()
        lintian_file.write_text(
            "E: cynthiune.app: vcs-obsolete\n"
            "W: hello source: invalid-something\n"
        )

        self.assertEqual(
            self.task._create_architecture_to_analysis(lintian_file),
            expected_architecture_to_analysis,
        )

    def test_analyze_lintian_output_no_output_tasks(self) -> None:
        """analyze_lintian_output(): no analysis returned."""
        analysis_config = {
            "source_analysis": False,
            "binary_all_analysis": False,
            "binary_any_analysis": False,
        }
        expected_analysis: dict[str, dict[str, Any]] = {}
        self.assert_architecture_to_analysis(analysis_config, expected_analysis)

    def test_create_architecture_to_analysis_only_source(self) -> None:
        """test_create_architecture_to_analysis(): only source analysed."""
        analysis_config = {
            "source_analysis": True,
            "binary_all_analysis": False,
            "binary_any_analysis": False,
        }
        expected_analysis = {"source": self.get_analysis_no_tags()}
        self.assert_architecture_to_analysis(analysis_config, expected_analysis)

    def test_create_architecture_to_analysis_only_binary_all(self) -> None:
        """test_create_architecture_to_analysis(): only binary_all analysed."""
        analysis_config = {
            "source_analysis": False,
            "binary_all_analysis": True,
            "binary_any_analysis": False,
        }
        expected_analysis = {"all": self.get_analysis_no_tags()}
        self.assert_architecture_to_analysis(analysis_config, expected_analysis)

    def test_create_architecture_to_analysis_only_source_and_binary_all(
        self,
    ) -> None:
        """analyze_lintian_output(): only source and binary all analysed."""
        analysis_config = {
            "source_analysis": True,
            "binary_all_analysis": True,
            "binary_any_analysis": False,
        }
        expected_analysis = {
            "all": self.get_analysis_no_tags(),
            "source": self.get_analysis_no_tags(),
        }
        self.assert_architecture_to_analysis(analysis_config, expected_analysis)

    def test_create_architecture_to_analysis_only_binary_any(self) -> None:
        """analyze_lintian_output(): only binary any analysed."""
        analysis_config = {
            "source_analysis": False,
            "binary_all_analysis": False,
            "binary_any_analysis": True,
        }
        expected_analysis = {"amd64": self.get_analysis_no_tags()}
        self.assert_architecture_to_analysis(analysis_config, expected_analysis)

    def test_configure(self) -> None:
        """configure() with valid data. No exception is raised."""
        self.configure_task()

    def test_configure_invalid_data(self) -> None:
        """configure() with invalid data used, TaskConfigError is raised."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"extra_field": "something"})

    def test_configure_with_target_distribution(self) -> None:
        """Configuration included "target_distribution". Saved no exceptions."""
        distribution = "bookworm"
        self.configure_task(override={"target_distribution": distribution})
        self.assertEqual(self.task.data.target_distribution, distribution)

    def test_configure_with_fail_on_severity(self) -> None:
        """Configuration included "fail_on_severity". Saved, no exception."""
        severity = LintianFailOnSeverity.WARNING
        self.configure_task(override={"fail_on_severity": severity})
        self.assertEqual(self.task.data.fail_on_severity, severity)

    def test_configure_without_fail_on_severity(self) -> None:
        """Configuration does not include "fail_on_severity". Default is set."""
        self.configure_task()
        self.assertEqual(
            self.task.data.fail_on_severity, LintianFailOnSeverity.ERROR
        )
        self.assertEqual(self.task.data.target_distribution, "debian:unstable")

    def test_configure_without_output(self) -> None:
        """Configuration does not include "output". Check defaults."""
        self.configure_task()
        self.assertEqual(
            self.task.data.output,
            {
                "source_analysis": True,
                "binary_all_analysis": True,
                "binary_any_analysis": True,
            },
        )

    def test_configure_without_backend(self) -> None:
        """Configuration does not include "backend". Check default is "auto"."""
        self.configure_task()
        self.assertEqual(self.task.data.backend, "auto")

    def test_configure_include_backend_unshare(self) -> None:
        """Configuration with backend unshare is valid."""
        self.configure_task(override={"backend": "unshare"})
        self.assertEqual(self.task.data.backend, "unshare")

    def test_configure_without_any_input(self) -> None:
        """Configuration invalid: needs a source."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"input": {}})

    def test_configure_valid_task_artifact_id(self) -> None:
        """Configuration valid: does not raise any exception."""
        self.configure_task(override={"input": {"source_artifact": 5}})

    def test_configure_valid_task_binary_ids(self) -> None:
        """Configure valid: does not raise any exception."""
        self.configure_task(override={"input": {"binary_artifacts": [6, 7]}})

    def test_compute_dynamic_data(self) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianSourcePackage(
                        name="hello",
                        version="1.0",
                        type="dpkg",
                        dsc_fields={},
                    ),
                ),
            }
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            LintianDynamicData(
                environment_id=1,
                input_source_artifact_id=5,
                subject="hello",
                runtime_context="binary-all+binary-any+source:sid",
                configuration_context="sid",
            ),
        )

    def test_compute_dynamic_data_subject_debian_upload(self) -> None:
        """compute_dynamic_data subject is package name of source artifact."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Source": "hello",
                            "Architecture": "amd64",
                            "Files": [{"name": "hello_1.0_amd64.deb"}],
                        },
                    ),
                ),
            }
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db).subject, "hello"
        )

    def test_compute_dynamic_data_subject_runtime_context(self) -> None:
        """compute_dynamic_data runtime_context source only."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Source": "hello",
                            "Architecture": "amd64",
                            "Files": [{"name": "hello_1.0_amd64.deb"}],
                        },
                    ),
                ),
            }
        )

        self.configure_task(
            override={
                "output": {
                    "source_analysis": True,
                    "binary_all_analysis": False,
                    "binary_any_analysis": False,
                }
            }
        )

        dynamic_data = self.task.compute_dynamic_data(task_db)

        self.assertEqual(dynamic_data.subject, "hello")
        self.assertEqual(dynamic_data.runtime_context, "source:sid")

    def test_compute_dynamic_data_debian_binary_package(self) -> None:
        """
        compute_dynamic_data subject is package name of source artifact.

        There is a binary artifact with other package names.
        """
        binary_artifacts_lookup = LookupMultiple.parse_obj([6])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Source": "hello",
                            "Architecture": "amd64",
                            "Files": [{"name": "hello_1.0_amd64.deb"}],
                        },
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=6,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="hello-bin",
                            srcpkg_version="1.0",
                            deb_fields={
                                "Package": "hello-bin",
                                "Version": "1.0",
                                "Architecture": "amd64",
                            },
                            deb_control_files=[],
                        ),
                    ),
                ],
            },
        )

        self.configure_task(
            override={
                "input": {"source_artifact": 5, "binary_artifacts": [6]},
                "output": {"source_analysis": False},
            }
        )

        dynamic_data = self.task.compute_dynamic_data(task_db)
        self.assertEqual(dynamic_data.subject, "hello")
        self.assertEqual(
            dynamic_data.runtime_context, "binary-all+binary-any:sid"
        )

    def test_compute_dynamic_data_source_artifact_subject_no_binaries(
        self,
    ) -> None:
        """compute_dynamic_data set to source artifact without binaries."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Source": "hello",
                            "Architecture": "amd64",
                            "Files": [{"name": "hello_1.0_amd64.deb"}],
                        },
                    ),
                ),
            },
        )

        self.configure_task(remove=["environment"])

        dynamic_data = self.task.compute_dynamic_data(task_db)
        self.assertEqual(dynamic_data.subject, "hello")
        self.assertIsNone(dynamic_data.runtime_context)

    def test_compute_dynamic_data_debian_binary_packages_subject(self) -> None:
        """
        compute_dynamic_data set subject to binary package name.

        There are no source packages.
        """
        binary_artifacts_lookup = LookupMultiple.parse_obj([6])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=6,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=DebianBinaryPackages(
                            srcpkg_name="hello",
                            srcpkg_version="1.0",
                            version="1.0",
                            architecture="amd64",
                            packages=["hello"],
                        ),
                    )
                ],
            },
        )

        self.configure_task(override={"input": {"binary_artifacts": [6]}})

        self.assertEqual(
            self.task.compute_dynamic_data(task_db).subject, "hello"
        )

    def test_compute_dynamic_raise_config_task_error_wrong_environment(
        self,
    ) -> None:
        """
        Test compute_dynamic_data raise TaskConfigError.

        environment artifact category is unexpected.
        """
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=EmptyArtifactData(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Source": "hello",
                            "Architecture": "amd64",
                            "Files": [{"name": "hello_1.0_amd64.deb"}],
                        },
                    ),
                ),
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            "^environment: unexpected artifact category: "
            "'debian:source-package'. Valid categories: "
            r"\['debian:system-tarball'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_compute_dynamic_raise_config_task_error_wrong_source_artifact(
        self,
    ) -> None:
        """
        Test compute_dynamic_data raise TaskConfigError.

        source artifact category is unexpected.
        """
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare:variant=lintian",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=EmptyArtifactData(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.BINARY_PACKAGES,
                    data=EmptyArtifactData(),
                ),
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            "^input.source_artifact: unexpected artifact category: "
            "'debian:binary-packages'. "
            r"Valid categories: \['debian:source-package', 'debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_compute_dynamic_data_raise_task_config_error(
        self,
    ) -> None:
        """
        Test compute_dynamic_data raise TaskConfigError.

        binary_artifact's data is a wrong type.
        """
        binary_artifacts_lookup = LookupMultiple.parse_obj([6])
        task_db = FakeTaskDatabase(
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=6,
                        category=ArtifactCategory.SOURCE_PACKAGE,
                        data=EmptyArtifactData(),
                    )
                ],
            },
        )

        self.configure_task(
            override={"input": {"binary_artifacts": [6]}},
            remove=["environment"],
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.binary_artifacts\[0\]: unexpected artifact category: "
            r"'debian:source-package'. Valid categories: "
            r"\['debian:binary-package', 'debian:binary-packages', "
            r"'debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        self.assertEqual(self.task.get_input_artifacts_ids(), [])

        self.task.dynamic_data = LintianDynamicData(
            input_binary_artifacts_ids=[1, 2],
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [1, 2])

        self.task.dynamic_data = LintianDynamicData(
            input_source_artifact_id=1,
            input_binary_artifacts_ids=[2, 3],
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [1, 2, 3])

        self.task.dynamic_data = LintianDynamicData(
            input_source_artifact_id=1,
            environment_id=2,
            input_binary_artifacts_ids=[3, 4],
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [1, 2, 3, 4])

    def test_cmdline_minimum_options(self) -> None:
        """Test Lintian._cmdline minimum options."""
        minimum_cmd = [
            "lintian",
            "--no-cfg",
            "--display-level",
            ">=classification",
            "--display-experimental",
            "--info",
        ]
        self.configure_task()
        self.task._lintian_targets = [self.create_temporary_file()]
        self.assertEqual(self.task._cmdline()[: len(minimum_cmd)], minimum_cmd)

    def test_cmdline_minimum_options_jessie(self) -> None:
        """
        Test Lintian._cmdline minimum options for Jessie.

        Jessie's lintian does not have display-level classification. It uses
        pedantic instead.
        """
        self.configure_task(override={"target_distribution": "jessie"})
        self.task._lintian_targets = [self.create_temporary_file()]
        cmdline = self.task._cmdline()

        display_level_position = cmdline.index("--display-level")

        self.assertEqual(cmdline[display_level_position + 1], ">=pedantic")

    def test_cmdline_with_tags(self) -> None:
        """Cmdline add --tags."""
        tags = ["wish-script-but-no-wish-dep", "zero-byte-executable-in-path"]
        self.configure_task(override={"include_tags": tags})
        self.task._lintian_targets = [self.create_temporary_file()]

        cmdline = self.task._cmdline()

        self.assertIn(f"--tags={','.join(tags)}", cmdline)
        self.assertEqual(cmdline[-1], str(self.task._lintian_targets[0]))

    def test_cmdline_exclude_tags(self) -> None:
        """Cmdline add --suppress-tags."""
        tags = ["wayward-symbolic-link-target-in-source", "wrong-team"]

        self.configure_task(override={"exclude_tags": tags})
        self.task._lintian_targets = [self.create_temporary_file()]
        self.assertIn(f"--suppress-tags={','.join(tags)}", self.task._cmdline())

    def test_configure_for_execution_set_lintian_target(self) -> None:
        """One .dsc file: self.task._lintian_targets set to it."""
        directory = self.create_temporary_directory()

        dsc = self.create_temporary_file(suffix=".dsc", directory=directory)
        self.create_temporary_file(suffix=".tar.xz", directory=directory)

        self.patch_extract_package_name_arch().return_value = None, None

        prepare_executor_instance_mocked = (
            self.patch_prepare_executor_instance()
        )

        self.assertTrue(self.task.configure_for_execution(directory))

        prepare_executor_instance_mocked.assert_called_with()

        cast(MagicMock, self.task.executor_instance).assert_has_calls(
            [
                call.run(
                    ["apt-get", "update"],
                    run_as_root=True,
                    check=True,
                    stdout=mock.ANY,
                    stderr=mock.ANY,
                ),
                call.run(
                    ["apt-get", "--yes", "install", "lintian"],
                    run_as_root=True,
                    check=True,
                    stdout=mock.ANY,
                    stderr=mock.ANY,
                ),
            ]
        )

        self.assertEqual(self.task._lintian_targets, [dsc])

    def test_configure_no_dsc_file(self) -> None:
        """No files: configure_for_execution() return False."""
        download_directory = self.create_temporary_directory()

        ignored_file = "test.txt"

        self.patch_prepare_executor_instance()

        (download_directory / ignored_file).write_text("ignored content")

        self.assertFalse(self.task.configure_for_execution(download_directory))

        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()

        self.assertEqual(
            log_file_contents,
            f"No *.dsc, *.deb or *.udeb to be analyzed. "
            f"Files: ['{ignored_file}']\n",
        )

    def patch_extract_package_name_arch(self) -> MagicMock:
        """Patch self.task._extract_package_name_arch, return Mock."""
        patcher = mock.patch.object(
            self.task, "_extract_package_name_arch", autospec=True
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked

    def test_configure_for_execution_deb_and_udeb(self) -> None:
        """One .deb and one .udeb: self.task._lintian_targets has both."""
        directory = self.create_temporary_directory()

        file1 = self.create_temporary_file(suffix=".deb", directory=directory)
        file2 = self.create_temporary_file(suffix=".udeb", directory=directory)

        self.patch_extract_package_name_arch().return_value = None, None

        self.patch_prepare_executor_instance()

        success = self.task.configure_for_execution(directory)

        self.assertTrue(success)
        assert self.task._lintian_targets
        self.assertCountEqual(self.task._lintian_targets, [file1, file2])

    def test_configure_for_execution_create_mappings(self) -> None:
        """
        configure_for_execution() update member variables.

        Updates Lintian_architecture_to_packages,
        Lintian._package_name_to_filename.
        """
        directory = self.create_temporary_directory()

        # Create a binary package (.deb)
        binary_pkg_name = "hello"
        (
            binary_pkg_file := directory / f"{binary_pkg_name}_amd64.deb"
        ).write_text("test")

        # Create a source package (.dsc)
        source_pkg_file = directory / "hello_all.dsc"
        source_pkg_name = self.write_dsc_example_file(source_pkg_file)["Source"]
        source_pkg_name += " source"

        # Create a file that is ignored
        (ignored_file := directory / "package.tar.xz").write_text("not-used")

        extract_package_name_arch_patcher = mock.patch.object(
            self.task, "_extract_package_name_arch"
        )
        extract_file_name_mocked = extract_package_name_arch_patcher.start()
        filename_to_package_name_arch = {
            source_pkg_file.name: (source_pkg_name, "source"),
            ignored_file.name: (None, None),
            binary_pkg_file.name: (binary_pkg_name, "amd64"),
        }

        extract_file_name_mocked.side_effect = (
            lambda file: filename_to_package_name_arch.get(file.name)
        )
        self.addCleanup(extract_package_name_arch_patcher.stop)

        self.patch_prepare_executor_instance()

        self.task.configure_for_execution(directory)

        # self.task._package_name_to_filename is updated
        self.assertEqual(
            self.task._package_name_to_filename,
            {
                binary_pkg_name: binary_pkg_file.name,
                source_pkg_name: source_pkg_file.name,
            },
        )

        # self.task._architecture_to_packages is updated
        self.assertEqual(
            self.task._architecture_to_packages,
            {"source": {"hello source"}, "amd64": {"hello"}},
        )

    def patch_get_lintian_version(self, version: str) -> Any:
        """
        Patch Lintian._get_lintian_version().

        :param version: version returned by the mock
        :return: the patcher
        """
        patcher = mock.patch.object(Lintian, "_get_lintian_version")
        mocked = patcher.start()
        mocked.return_value = version
        self.addCleanup(patcher.stop)

        return patcher

    def test_configure_additional_properties_in_input(self) -> None:
        """Assert no additional properties in task_data input."""
        error_msg = "extra fields not permitted"
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            self.configure_task(
                override={
                    "input": {
                        "source_artifact": 5,
                        "binary_artifacts": [],
                        "some_additional_property": 87,
                    },
                }
            )

    def test_build_consistency_no_errors(self) -> None:
        """There are no consistency errors."""
        build_directory = self.create_temporary_directory()
        (build_directory / Lintian.CAPTURE_OUTPUT_FILENAME).write_text("")
        self.assertEqual(
            self.task.execution_consistency_errors(build_directory), []
        )

    def test_execution_consistency_errors(self) -> None:
        """There is one consistency error: no lintian.txt."""
        build_directory = self.create_temporary_directory()

        expected = f"{Lintian.CAPTURE_OUTPUT_FILENAME} not in {build_directory}"
        self.assertEqual(
            self.task.execution_consistency_errors(build_directory), [expected]
        )

    def test_upload_artifacts(self) -> None:
        """upload_artifact() and relation_create() is called."""
        exec_dir = self.create_temporary_directory()

        # Create file that will be attached when uploading the artifacts
        lintian_output = exec_dir / self.task.CAPTURE_OUTPUT_FILENAME
        lintian_output.write_text("E: hello: invalid-file\n")

        # Set the analysis that will be uploaded

        summary_data = {
            "tags_count_by_severity": {"error": 1},
            "tags_found": ["invalid-file"],
            "overridden_tags_found": [],
            "lintian_version": "1.0.0",
            "distribution": "debian:unstable",
        }
        architecture_to_analysis = {
            "source": {
                "summary": {
                    **summary_data,
                    "package_filename": {"hello": "hello_1.0.dsc"},
                }
            },
            "all": {
                "summary": {
                    **summary_data,
                    "package_filename": {"hello-doc": "hello-doc_1.0_all.deb"},
                }
            },
            "amd64": {
                "summary": {
                    **summary_data,
                    "package_filename": {"hello": "hello_1.0_amd64.deb"},
                }
            },
        }

        create_architecture_to_analysis_patcher = mock.patch.object(
            self.task, "_create_architecture_to_analysis", autospec=True
        )
        create_architecture_to_analysis_mocked = (
            create_architecture_to_analysis_patcher.start()
        )
        create_architecture_to_analysis_mocked.return_value = (
            architecture_to_analysis
        )
        self.addCleanup(create_architecture_to_analysis_patcher.stop)

        # Used ty verify the relations
        self.task._source_artifacts_ids = [1]

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        workspace_name = "testing"

        uploaded_artifacts = [
            create_remote_artifact(id=10, workspace=workspace_name),
            create_remote_artifact(id=11, workspace=workspace_name),
            create_remote_artifact(id=12, workspace=workspace_name),
        ]

        debusine_mock.upload_artifact.side_effect = uploaded_artifacts

        # self.task.workspace_name is set by the Worker
        # and is the workspace that downloads the artifact
        # containing the files needed for Lintian
        self.task.workspace_name = workspace_name

        # The worker set self.task.work_request_id of the task
        work_request_id = 147
        self.task.work_request_id = work_request_id

        self.task.upload_artifacts(exec_dir, execution_success=True)

        # Debusine Mock upload_artifact expected calls
        upload_artifact_calls = []
        for architecture, analysis in architecture_to_analysis.items():
            (
                analysis_file := exec_dir / f"analysis-{architecture}.json"
            ).write_text(json.dumps(analysis))
            lintian_artifact = LintianArtifact.create(
                analysis=analysis_file,
                lintian_output=lintian_output,
                architecture=architecture,
                summary=DebianLintianSummary.parse_obj(analysis["summary"]),
            )

            upload_artifact_calls.append(
                call(
                    lintian_artifact,
                    workspace=workspace_name,
                    work_request=work_request_id,
                )
            )

        # Debusine mock relation_create expected calls
        relation_create_calls = []
        for uploaded_artifact, source_artifact_id in itertools.product(
            uploaded_artifacts, self.task._source_artifacts_ids
        ):
            relation_create_calls.append(
                call(uploaded_artifact.id, source_artifact_id, "relates-to")
            )

        # Assert that the artifacts were uploaded and relations created
        debusine_mock.upload_artifact.assert_has_calls(upload_artifact_calls)
        debusine_mock.relation_create.assert_has_calls(relation_create_calls)

    def test_get_lintian_version_raise_assertion(self) -> None:
        """
        _get_lintian_version() raise AssertionError.

        self.task.executor_instance is None.
        """
        self._get_lintian_version_patcher.stop()
        msg = (
            r"^Task\.executor_instance must be set before "
            r"calling _get_lintian_version\(\)$"
        )
        self.assertRaisesRegex(
            AssertionError, msg, self.task._get_lintian_version
        )

    def test_get_lintian_version(self) -> None:
        """
        _get_lintian_version() return the correct Lintian version.

        It also set self.task._lintian_version and use it if called again.
        """
        self._get_lintian_version_patcher.stop()
        version = "2.22.33"

        self.task.executor_instance = mock.Mock(spec=InstanceInterface)

        run_result = mock.Mock(spec=CompletedProcess)
        run_result.stdout = f"{version}\n"

        self.task.executor_instance.run.return_value = run_result

        self.assertEqual(self.task._get_lintian_version(), version)

        self.task.executor_instance.run.assert_called_with(
            ["lintian", "--print-version"],
            encoding="utf-8",
            errors="ignore",
            text=True,
            check=True,
        )

    def test_create_analysis(self) -> None:
        """_create_analysis() return a correct analysis."""
        lintian_output = (
            b"E: twitter-bootstrap3: alien-tag source-contains-empty-directory [debian/source/lintian-overrides:9]\n"  # noqa: E501
            b"W: jabber-muc source: obsolete-url-in-packaging https://download.gna.org/mu-conference/ [debian/control]\n"  # noqa: E501
            b"W: hello: some-warning\n"
            b"O: twitter-bootstrap3: tag-overridden\n"
            b"O: hello: tag-overridden ignored package hello not in analysis\n"
        )
        lintian_file = self.create_temporary_file(contents=lintian_output)

        packages = {"twitter-bootstrap3", "jabber-muc source"}

        self.task._package_name_to_filename = {
            "twitter-bootstrap3": "twitter-bootstrap3",
            "jabber-muc source": "jabber-muc.dsc",
        }

        parsed_output = self.task._parse_output(lintian_file)
        parsed_output.sort(
            key=lambda x: (
                x["package"],
                -self.task._severities_to_level[
                    x["severity"]
                ],  # negative to sort from highest to lowest
                x["tag"],
                x["note"],
            )
        )

        actual = self.task._create_analysis(parsed_output, packages)

        summary = {
            "tags_count_by_severity": {
                "error": 1,
                "warning": 1,
                "info": 0,
                "pedantic": 0,
                "experimental": 0,
                "overridden": 1,
                "classification": 0,
            },
            "package_filename": {
                "jabber-muc": "jabber-muc.dsc",
                "twitter-bootstrap3": "twitter-bootstrap3",
            },
            "tags_found": ["alien-tag", "obsolete-url-in-packaging"],
            "overridden_tags_found": ["tag-overridden"],
            "lintian_version": "1.0.0",
            "distribution": "debian:unstable",
        }

        # Tags from the parsed_output without "hello" (it is not being
        # analyzed) and without " source" in the package name
        normalized_tags = []

        for tag in parsed_output:
            if tag["package"] == "hello":
                continue

            normalized_tags.append(
                {**tag, "package": tag["package"].removesuffix(" source")}
            )

        expected = {
            "tags": normalized_tags,
            "summary": summary,
            "version": self.task.TASK_VERSION,
        }
        self.assertEqual(actual, expected)

    def run_fetch_input(
        self,
        input_override: dict[str, Any],
        dynamic_data: LintianDynamicData,
        expected_artifact_ids: list[int],
    ) -> None:
        """Verify that Lintian.fetch_input() downloads artifacts."""
        workspace = "System"

        self.configure_task(override={"input": input_override})
        self.task.work_request_id = 147
        self.task.dynamic_data = dynamic_data
        debusine_mock = self.mock_debusine()
        destination = self.create_temporary_directory()

        expected_calls = []
        artifact_responses = []
        expected_artifact_id_to_filenames = {}

        for artifact_id in expected_artifact_ids:
            expected_calls.append(call(artifact_id, destination, tarball=False))

            file_in_artifact = self.create_temporary_file()
            file_model = FileResponse(
                size=file_in_artifact.stat().st_size,
                checksums={
                    "sha256": pydantic.parse_obj_as(StrMaxLength255, "not-used")
                },
                type="file",
                url=pydantic.parse_obj_as(pydantic.AnyUrl, "https://not-used"),
            )

            filename = f"pkg-{artifact_id}_amd64.deb"

            artifact_responses.append(
                create_artifact_response(
                    id=artifact_id,
                    workspace=workspace,
                    category="Test",
                    created_at=datetime.datetime.now(),
                    data={},
                    download_tar_gz_url=pydantic.parse_obj_as(
                        pydantic.AnyUrl, "https://example.com/not-used"
                    ),
                    files=FilesResponseType({filename: file_model}),
                    files_to_upload=[],
                )
            )

            expected_artifact_id_to_filenames[artifact_id] = {filename}

        debusine_mock.download_artifact.side_effect = artifact_responses

        self.assertTrue(self.task.fetch_input(destination))

        # Artifacts were downloaded
        self.assertEqual(
            debusine_mock.download_artifact.call_args_list, expected_calls
        )

    def test_fetch_input_only_binary_artifacts(self) -> None:
        """Lintian.fetch_input() download binary_artifacts_ids."""
        artifacts = [1, 2]
        self.run_fetch_input(
            input_override={"binary_artifacts": artifacts},
            dynamic_data=LintianDynamicData(input_binary_artifacts_ids=[1, 2]),
            expected_artifact_ids=artifacts,
        )

    def test_fetch_input_only_source_artifact(self) -> None:
        """Lintian.fetch_input() download source_artifact_id."""
        artifact_id = 5
        self.run_fetch_input(
            input_override={"source_artifact": artifact_id},
            dynamic_data=LintianDynamicData(
                input_source_artifact_id=artifact_id
            ),
            expected_artifact_ids=[artifact_id],
        )

    def test_fetch_input_source_and_binary_artifact(self) -> None:
        """Lintian.fetch_input() download binary and source artifacts."""
        source_artifact_id = 5
        binary_artifacts_ids = [6, 7]
        self.run_fetch_input(
            input_override={
                "source_artifact": source_artifact_id,
                "binary_artifacts": binary_artifacts_ids,
            },
            dynamic_data=LintianDynamicData(
                input_source_artifact_id=source_artifact_id,
                input_binary_artifacts_ids=binary_artifacts_ids,
            ),
            expected_artifact_ids=[source_artifact_id] + binary_artifacts_ids,
        )

    def test_extract_package_name_arch_source_file(self) -> None:
        """Lintian._extract_package_name_arch() return pkgname + source."""
        file = self.create_temporary_file(suffix=".dsc")
        package_name = self.write_dsc_example_file(file)["Source"]

        self.assertEqual(
            self.task._extract_package_name_arch(file),
            (package_name + " source", "source"),
        )

    def test_extract_package_name_arch_raise_invalid_suffix(self) -> None:
        """Lintian._extract_package_name_arch() invalid suffix: None, None."""
        self.assertEqual(
            self.task._extract_package_name_arch(Path("name.tmp")), (None, None)
        )

    def test_extract_package_name_arch_deb_udeb(self) -> None:
        """Lintian._extract_package_name_arch() use DebFile correctly."""
        for architecture in ["amd64", "all"]:
            with self.subTest(architecture=architecture):
                filenames = [
                    "package-name_amd64.deb",
                    "package-name_amd64.udeb",
                ]
                package_name = "package-name"

                mock_control = MagicMock()
                mock_control.debcontrol.return_value = {
                    "Package": package_name,
                    "Architecture": architecture,
                }

                mock_deb_file_instance = MagicMock()
                mock_deb_file_instance.control = mock_control

                debfile_patcher = mock.patch(
                    "debusine.tasks.lintian.debfile.DebFile"
                )
                debfile_mocked = debfile_patcher.start()
                debfile_mocked.return_value = mock_deb_file_instance

                self.addCleanup(debfile_patcher.stop)

                for filename in filenames:
                    with self.subTest(filename=filename):
                        self.assertEqual(
                            self.task._extract_package_name_arch(
                                Path(filename)
                            ),
                            (package_name, architecture),
                        )

                        debfile_mocked.assert_called_with(Path(filename))

    def test_configure_for_execution_invalid_dsc(self) -> None:
        """Lintian._extract_package_name_arch() handles an invalid .dsc file."""
        directory = self.create_temporary_directory()
        dsc = directory / "hello.dsc"
        dsc.touch()

        self.assertEqual(
            self.task._extract_package_name_arch(dsc), (None, None)
        )

        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()
        self.assertEqual(log_file_contents, f"{dsc} is not a valid .dsc file\n")

    def test_label_dynamic_data_is_none(self) -> None:
        """Test get_label if dynamic_data.subject is None."""
        self.assertEqual(self.task.get_label(), "lintian")

    def test_label_dynamic_data_subject_is_none(self) -> None:
        """Test get_label if dynamic_data is None."""
        self.task.dynamic_data = LintianDynamicData()
        self.assertEqual(self.task.get_label(), "lintian")

    def test_label_dynamic_data_subject_is_hello(self) -> None:
        """Test get_label if dynamic_data.subject is set."""
        self.task.dynamic_data = LintianDynamicData(subject="hello")
        self.assertEqual(self.task.get_label(), "lintian hello")
