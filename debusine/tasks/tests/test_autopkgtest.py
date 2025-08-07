# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the Autopkgtest class."""
import copy
import datetime
import shlex
import textwrap
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, call, create_autospec, patch

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.local_artifact import AutopkgtestArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianAutopkgtest,
    DebianAutopkgtestResult,
)
from debusine.artifacts.models import DebianAutopkgtestResultStatus as rs
from debusine.artifacts.models import (
    DebianAutopkgtestSource,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianSourcePackage,
    DebianUpload,
)
from debusine.client.debusine import Debusine
from debusine.client.models import (
    ArtifactResponse,
    FileResponse,
    FilesResponseType,
)
from debusine.tasks import Autopkgtest, TaskConfigError
from debusine.tasks.executors import UnshareExecutor
from debusine.tasks.models import (
    AutopkgtestDynamicData,
    AutopkgtestNeedsInternet,
    AutopkgtestTimeout,
    BackendType,
    LookupMultiple,
    WorkerType,
)
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import create_system_tarball_data


class AutopkgtestTests(ExternalTaskHelperMixin[Autopkgtest], TestCase):
    """Tests for Autopkgtest."""

    SAMPLE_TASK_DATA = {
        "input": {
            "source_artifact": 5,
            "binary_artifacts": [10, 11],
        },
        "host_architecture": "amd64",
        "environment": "debian/match:codename=bookworm",
    }

    def setUp(self) -> None:
        """Initialize test."""
        self.configure_task()

        # self.task.workspace_name and self.task.work_request_id are set by
        # the Worker.
        self.task.workspace_name = "testing"
        self.task.work_request_id = 170
        self.task.dynamic_data = AutopkgtestDynamicData(
            environment_id=1,
            input_source_artifact_id=5,
            input_binary_artifacts_ids=[10, 11],
        )

    def tearDown(self) -> None:
        """Delete debug log files directory if it exists."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()

    def mock_executor(self) -> None:
        """Set up a fake unshare executor."""
        mock = create_autospec(UnshareExecutor, instance=True)
        executor = cast(UnshareExecutor, mock)
        self.task.executor = executor
        executor.system_image = self.fake_system_tarball_artifact()
        mock.autopkgtest_virt_server.return_value = "unshare"

    def assert_parse_summary_file_expected(
        self,
        autopkgtest_summary: str,
        expected: dict[str, DebianAutopkgtestResult],
    ) -> None:
        """Call self.autopkgtest.parse_summary_file(summary), assert result."""
        summary_file = self.create_temporary_file(
            contents=autopkgtest_summary.encode("utf-8")
        )
        actual = Autopkgtest._parse_summary_file(summary_file)

        self.assertEqual(actual, expected)

    def test_parse_summary_file_empty(self) -> None:
        """Parse an empty file."""
        contents = ""
        expected: dict[str, DebianAutopkgtestResult] = {}

        self.assert_parse_summary_file_expected(contents, expected)

    def test_parse_summary_file_two_tests(self) -> None:
        """Parse file with two tests and two different results."""
        contents = textwrap.dedent(
            """\
            unit-tests-server    PASS
            integration-tests-task-sbuild FLAKY
        """
        )
        expected = {
            "unit-tests-server": DebianAutopkgtestResult(status=rs.PASS),
            "integration-tests-task-sbuild": DebianAutopkgtestResult(
                status=rs.FLAKY
            ),
        }

        self.assert_parse_summary_file_expected(contents, expected)

    def test_parse_summary_file_badpkg(self) -> None:
        """Parse file with badpkg report."""
        contents = textwrap.dedent(
            """\
                blame: arg:/tmp/foo.deb deb:foo /tmp/foo.dsc
                badpkg: Test dependencies are unsatisfiable.
                erroneous package: Test dependencies are unsatisfiable.
                testbed failure: unexpected eof from the testbed
                quitting: unexpected error, see log
            """
        )
        expected: dict[str, DebianAutopkgtestResult] = {}

        with self.assertLogs("debusine.tasks.autopkgtest", level="INFO") as cm:
            self.assert_parse_summary_file_expected(contents, expected)
        self.assertEqual(
            cm.output,
            [
                "INFO:debusine.tasks.autopkgtest:Autopkgtest error: blame: "
                "arg:/tmp/foo.deb deb:foo /tmp/foo.dsc",
                "INFO:debusine.tasks.autopkgtest:Autopkgtest error: badpkg: "
                "Test dependencies are unsatisfiable.",
                "INFO:debusine.tasks.autopkgtest:Autopkgtest error: erroneous "
                "package: Test dependencies are unsatisfiable.",
                "INFO:debusine.tasks.autopkgtest:Autopkgtest error: testbed "
                "failure: unexpected eof from the testbed",
                "INFO:debusine.tasks.autopkgtest:Autopkgtest error: quitting: "
                "unexpected error, see log",
            ],
        )

    def test_parse_summary_file_expected_results(self) -> None:
        """Parse file with valid results."""
        contents = textwrap.dedent(
            """\
            test-name-1    PASS
            test-name-2     FAIL
            test-name-3     SKIP
            test-name-4     FLAKY
            *               SKIP
            """
        )
        expected = {
            "test-name-1": DebianAutopkgtestResult(status=rs.PASS),
            "test-name-2": DebianAutopkgtestResult(status=rs.FAIL),
            "test-name-3": DebianAutopkgtestResult(status=rs.SKIP),
            "test-name-4": DebianAutopkgtestResult(status=rs.FLAKY),
            "*": DebianAutopkgtestResult(status=rs.SKIP),
        }
        self.assert_parse_summary_file_expected(contents, expected)

    def test_parse_summary_file_unexpected_result_raise_value_error(
        self,
    ) -> None:
        """Parse file with unexpected result: raise ValueError."""
        contents = "test-name-1: something"
        with self.assertRaisesRegex(
            ValueError, f"Line with unexpected result: {contents}"
        ):
            self.assert_parse_summary_file_expected(contents, {})

    def test_parse_summary_file_one_test_with_details(self) -> None:
        """Parse a file with details."""
        contents = """autodep8-python3     PASS (superficial)"""
        expected = {
            "autodep8-python3": DebianAutopkgtestResult(
                status=rs.PASS,
                details="(superficial)",
            ),
        }

        self.assert_parse_summary_file_expected(contents, expected)

    def test_parse_summary_file_with_details_with_space(self) -> None:
        """Parse a file with details containing a space."""
        contents = textwrap.dedent(
            """\
            unit-tests-server    PASS
            unit-tests-client  FAIL (the details)
            unit-tests-worker  SKIP stderr: Paths: foo not set, '/tmp/test'
        """
        )
        expected = {
            "unit-tests-server": DebianAutopkgtestResult(status=rs.PASS),
            "unit-tests-client": DebianAutopkgtestResult(
                status=rs.FAIL,
                details="(the details)",
            ),
            "unit-tests-worker": DebianAutopkgtestResult(
                status=rs.SKIP,
                details="stderr: Paths: foo not set, '/tmp/test'",
            ),
        }

        self.assert_parse_summary_file_expected(contents, expected)

    def test_parse_summary_file_invalid_line(self) -> None:
        """Parse a file with an invalid line."""
        # Format of each line should be "test-name test-status"
        contents = "invalid"
        with self.assertRaisesRegex(ValueError, "^Failed to parse line"):
            self.assert_parse_summary_file_expected(contents, {})

    def test_configure_valid_schema(self) -> None:
        """Valid task data do not raise any exception."""
        self.configure_task()

    def test_configure_missing_required_source_artifact(self) -> None:
        """Missing required field "source_artifact": raise exception."""
        task_no_source_artifact = copy.deepcopy(self.SAMPLE_TASK_DATA)
        del cast(dict[str, str], task_no_source_artifact["input"])[
            "source_artifact"
        ]

        with self.assertRaises(TaskConfigError):
            self.configure_task(task_data=task_no_source_artifact)

    def test_configure_missing_required_binary_artifacts(self) -> None:
        """Missing required field "binary_artifacts": raise exception."""
        task_no_binary_artifacts = copy.deepcopy(self.SAMPLE_TASK_DATA)
        del cast(dict[str, str], task_no_binary_artifacts["input"])[
            "binary_artifacts"
        ]

        with self.assertRaises(TaskConfigError):
            self.configure_task(task_data=task_no_binary_artifacts)

    def test_configure_accept_context_artifacts(self) -> None:
        """Optional field "context_artifacts": do not raise exception."""
        task_context_artifacts_ids = copy.deepcopy(self.SAMPLE_TASK_DATA)
        cast(dict[str, list[int]], task_context_artifacts_ids["input"])[
            "context_artifacts"
        ] = [20, 21]

        self.configure_task(task_data=task_context_artifacts_ids)

    def test_configure_input_additional_property(self) -> None:
        """Additional property in input: raise exception."""
        additional_property = copy.deepcopy(self.SAMPLE_TASK_DATA)
        cast(dict[str, str], additional_property["input"])["something"] = "test"

        with self.assertRaises(TaskConfigError):
            self.configure_task(task_data=additional_property)

    def test_configure_additional_property(self) -> None:
        """Additional property: raise exception."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"something": "test"})

    def test_configure_host_architecture_is_required(self) -> None:
        """Missing required field "host_architecture": raise exception."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(remove=["host_architecture"])

    def test_configure_environment_is_required(self) -> None:
        """Missing required field "environment": raise exception."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(remove=["environment"])

    def test_configure_backend_choices(self) -> None:
        """Field "backend" accept correct values."""
        for backend in ["auto", "incus-lxc", "incus-vm", "unshare"]:
            self.configure_task(override={"backend": backend})

    def test_configure_backend_invalid_choice(self) -> None:
        """Field "backend" do not accept unexpected value."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"backend": "invalid"})

    def test_configure_backend_default_value(self) -> None:
        """Field "backend" default value is "auto"."""
        self.configure_task(remove=["backend"])
        self.assertEqual(self.task.data.backend, "auto")

    def test_configure_backend_include_tests(self) -> None:
        """Field "include_tests" is allowed."""
        self.configure_task(override={"include_tests": ["a", "b"]})

    def test_configure_backend_exclude_tests(self) -> None:
        """Field "exclude_tests" is allowed."""
        self.configure_task(override={"exclude_tests": ["a", "b"]})

    def test_configure_debug_level(self) -> None:
        """Field "debug_level" is allowed."""
        self.configure_task(override={"debug_level": 0})
        self.configure_task(override={"debug_level": 3})

    def test_configure_debug_level_too_small_too_big(self) -> None:
        """Field "debug_level" must be between 0 and 3."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"debug_level": -1})

        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"debug_level": 4})

    def test_configure_debug_level_default_to_0(self) -> None:
        """Field "debug_level" default value is 0."""
        self.configure_task()
        self.assertEqual(self.task.data.debug_level, 0)

    def test_configure_extra_apt_sources(self) -> None:
        """Field "extra_repositories" is allowed."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.net",
                        "suite": "bookworm",
                        "components": ["main"],
                    }
                ]
            }
        )

    def test_configure_use_packages_from_base_repository(self) -> None:
        """Field "use_package_from_base_repository" is allowed."""
        self.configure_task(
            override={"use_packages_from_base_repository": True}
        )

    def test_configure_use_packages_from_base_repository_default_is_false(
        self,
    ) -> None:
        """Field "use_package_from_base_repository" default value is False."""
        self.configure_task()
        self.assertFalse(self.task.data.use_packages_from_base_repository)

    def test_configure_needs_internet_accepted_values(self) -> None:
        """Field "needs_internet" accept valid values."""
        for needs_internet in ["run", "try", "skip"]:
            self.configure_task(override={"needs_internet": needs_internet})

    def test_configure_needs_internet_reject_unexpected_value(self) -> None:
        """Field "needs_internet" do not accept unexpected value."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"needs_internet": "yes"})

    def test_configure_needs_internet_default_value(self) -> None:
        """Field "needs_internet" default value is "run"."""
        self.configure_task()
        self.assertEqual(self.task.data.needs_internet, "run")

    def test_configure_extra_environment(self) -> None:
        """Field "extra_environment" is accepted."""
        self.configure_task(
            override={"extra_environment": {"var1": "foo1", "var2": "foo2"}}
        )

    def test_configure_fail_on_defaults(self) -> None:
        """Field "fail_on" default values are the expected ones."""
        self.configure_task()
        self.assertEqual(
            dict(self.task.data.fail_on),
            {"failed_test": True, "flaky_test": False, "skipped_test": False},
        )

    def test_configure_fail_on_failed_skipped_test_defaults(self) -> None:
        """Field "fail_on" use defaults when specifying only "flaky_test"."""
        self.configure_task(override={"fail_on": {"flaky_test": False}})

        self.assertEqual(
            dict(self.task.data.fail_on),
            {"failed_test": True, "flaky_test": False, "skipped_test": False},
        )

    def test_configure_flaky_skipped_test_defaults(self) -> None:
        """Field "fail_on" use defaults when specifying only "failed_test"."""
        self.configure_task(override={"fail_on": {"failed_test": False}})

        self.assertEqual(
            dict(self.task.data.fail_on),
            {"failed_test": False, "flaky_test": False, "skipped_test": False},
        )

    def test_configure_fail_on_unexpected(self) -> None:
        """Field "fail_on" with invalid key: raise TaskConfigError."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"fail_on": {"non-valid": False}})

    def test_configure_timeout_valid_values(self) -> None:
        """Field "timeout" accept the expected keys."""
        for timeout_key in [
            "global",
            "factor",
            "short",
            "install",
            "test",
            "copy",
        ]:
            self.configure_task(override={"timeout": {timeout_key: 600}})

    def test_configure_timeout_non_valid_values(self) -> None:
        """Field "timeout" invalid values must be > 0: raise TaskConfigError."""
        for timeout_key, value in [
            ("global", -1),
            ("factor", -10),
            ("short", -10),
            ("install", -20),
            ("test", -30),
            ("copy", -10),
        ]:
            with self.assertRaises(TaskConfigError):
                self.configure_task(override={"timeout": {timeout_key: value}})

    def test_configure_timeout_non_valid_key(self) -> None:
        """Field "timeout" invalid key: raise TaskConfigError."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"timeout": {"non-valid-key": 600}})

    def test_configure_timeout_invalid_value(self) -> None:
        """Field "timeout" do not accept unexpected value."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"timeout": "something"})

    def test_analyze_worker(self) -> None:
        """Test the analyze_worker() method."""
        self.mock_is_command_available({"autopkgtest": True})
        metadata = self.task.analyze_worker()
        self.assertEqual(metadata["autopkgtest:available"], True)

    def test_analyze_worker_autopkgtest_not_available(self) -> None:
        """analyze_worker() handles autopkgtest not being available."""
        self.mock_is_command_available({"autopkgtest": False})
        metadata = self.task.analyze_worker()
        self.assertEqual(metadata["autopkgtest:available"], False)

    def test_can_run_on(self) -> None:
        """can_run_on returns True if unshare and autopkgtest are available."""
        architecture = self.task.data.host_architecture
        self.assertTrue(
            self.task.can_run_on(
                {
                    "system:worker_type": WorkerType.EXTERNAL,
                    "system:architectures": [architecture],
                    "executor:unshare:available": True,
                    "autopkgtest:available": True,
                    "autopkgtest:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_mismatched_task_version(self) -> None:
        """can_run_on returns False for mismatched task versions."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:worker_type": WorkerType.EXTERNAL,
                    "executor:unshare:available": True,
                    "autopkgtest:available": True,
                    "autopkgtest:version": self.task.TASK_VERSION + 1,
                }
            )
        )

    def test_can_run_on_unshare_not_available(self) -> None:
        """can_run_on returns False if unshare is not available."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:worker_type": WorkerType.EXTERNAL,
                    "executor:unshare:available": False,
                    "autopkgtest:available": True,
                    "autopkgtest:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_can_run_on_autopkgtest_not_available(self) -> None:
        """can_run_on returns False if autopkgtest is not available."""
        self.assertFalse(
            self.task.can_run_on(
                {
                    "system:worker_type": WorkerType.EXTERNAL,
                    "executor:unshare:available": True,
                    "autopkgtest:available": False,
                    "autopkgtest:version": self.task.TASK_VERSION,
                }
            )
        )

    def create_binary_packages_data(
        self, srcpkg_name: str
    ) -> DebianBinaryPackages:
        """Create sample `debian:binary-packages` artifact data."""
        return DebianBinaryPackages(
            srcpkg_name=srcpkg_name,
            srcpkg_version="1.0",
            version="1.0",
            architecture="amd64",
            packages=[],
        )

    def create_upload_data(self, name: str) -> DebianUpload:
        """Create sample `debian:upload` artifact data."""
        return DebianUpload(
            type="dpkg",
            changes_fields={
                "Architecture": "amd64",
                "Files": [{"name": f"{name}_1.0_amd64.deb"}],
            },
        )

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        binary_artifacts_lookup = LookupMultiple.parse_obj([10, 11])
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
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
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=10,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=self.create_binary_packages_data("hello"),
                    ),
                    ArtifactInfo(
                        id=11,
                        category=ArtifactCategory.UPLOAD,
                        data=self.create_upload_data("hello"),
                    ),
                ],
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            AutopkgtestDynamicData(
                environment_id=1,
                input_source_artifact_id=5,
                input_binary_artifacts_ids=[10, 11],
                input_context_artifacts_ids=[],
                subject="hello",
                runtime_context="amd64:unshare",
                configuration_context="sid",
            ),
        )

    def test_compute_dynamic_data_context(self) -> None:
        """Dynamic data includes input context artifact IDs if relevant."""
        binary_artifacts_lookup = LookupMultiple.parse_obj([10, 11])
        self.task.data.input.context_artifacts = LookupMultiple.parse_obj(
            {"collection": "bookworm@debian:suite"}
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
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
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=10,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=self.create_binary_packages_data("hello"),
                    ),
                    ArtifactInfo(
                        id=11,
                        category=ArtifactCategory.UPLOAD,
                        data=self.create_upload_data("hello"),
                    ),
                ],
                # input.context_artifacts
                (self.task.data.input.context_artifacts, None): [
                    ArtifactInfo(
                        id=2,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=self.create_binary_packages_data("context"),
                    ),
                    ArtifactInfo(
                        id=3,
                        category=ArtifactCategory.UPLOAD,
                        data=self.create_upload_data("context"),
                    ),
                ],
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            AutopkgtestDynamicData(
                environment_id=1,
                input_source_artifact_id=5,
                input_binary_artifacts_ids=[10, 11],
                input_context_artifacts_ids=[2, 3],
                subject="hello",
                runtime_context="amd64:unshare",
                configuration_context="sid",
            ),
        )

    def test_compute_dynamic_data_raise_task_config_error(self) -> None:
        """
        Test compute_dynamic_data raise TaskConfigError.

        source_artifact's category is unexpected.
        """
        binary_artifacts_lookup = LookupMultiple.parse_obj([10, 11])
        self.task.data.input.context_artifacts = LookupMultiple.parse_obj(
            {"collection": "bookworm@debian:suite"}
        )
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:architecture=amd64:"
                    "format=tarball:backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (5, None): ArtifactInfo(
                    id=5,
                    category=ArtifactCategory.BINARY_PACKAGE,
                    data=DebianBinaryPackage(
                        srcpkg_name="hello",
                        srcpkg_version="1.0",
                        deb_fields={},
                        deb_control_files=[],
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=10,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=self.create_binary_packages_data("hello"),
                    ),
                    ArtifactInfo(
                        id=11,
                        category=ArtifactCategory.UPLOAD,
                        data=self.create_upload_data("hello"),
                    ),
                ],
                # input.context_artifacts
                (self.task.data.input.context_artifacts, None): [
                    ArtifactInfo(
                        id=2,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=self.create_binary_packages_data("context"),
                    ),
                    ArtifactInfo(
                        id=3,
                        category=ArtifactCategory.UPLOAD,
                        data=self.create_upload_data("context"),
                    ),
                ],
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            "^input.source_artifact: unexpected artifact "
            "category: 'debian:binary-package'. Valid categories: "
            r"\['debian:source-package', 'debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_cmdline_executor_not_set(self) -> None:
        """Raise AssertionError: self.executor is not set."""
        msg = (
            r"^self\.executor not set - self\._prepare_for_execution\(\) "
            r"must be called before _cmdline\(\)$"
        )
        self.assertRaisesRegex(AssertionError, msg, self.task._cmdline)

    def test_cmdline_minimum_options(self) -> None:
        """Command line has the minimum options."""
        self.mock_executor()
        minimum_options = [
            "autopkgtest",
            "--apt-upgrade",
            f"--output-dir={Autopkgtest.ARTIFACT_DIR}",
            f"--summary={Autopkgtest.SUMMARY_FILE}",
            "--no-built-binaries",
        ]
        cmdline = self.task._cmdline()
        self.assertEqual(cmdline[: len(minimum_options)], minimum_options)

    def test_cmdline_include_tests(self) -> None:
        """Command line include --test-name=XXX."""
        self.mock_executor()
        include_tests = ["test1", "test2"]

        self.task.data.include_tests = include_tests

        cmdline = self.task._cmdline()

        for include_test in include_tests:
            self.assertIn(f"--test-name={include_test}", cmdline)

    def test_cmdline_exclude_tests(self) -> None:
        """Command line include --skip-test=XXX."""
        self.mock_executor()
        exclude_tests = ["test1", "test2"]

        self.task.data.exclude_tests = exclude_tests

        cmdline = self.task._cmdline()

        for exclude_test in exclude_tests:
            self.assertIn(f"--skip-test={exclude_test}", cmdline)

    def test_cmdline_debug_level(self) -> None:
        """Command line include "-dd" (debug_level)."""
        self.mock_executor()
        self.task.data.debug_level = 2

        cmdline = self.task._cmdline()

        self.assertIn("-dd", cmdline)

    def test_cmdline_extra_repositories_jessie(self) -> None:
        """Ensure the --add-apt-source= parameter is passed to autopkgtest."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "unstable",
                        "components": ["main"],
                    },
                    {
                        "url": "http://example.net/",
                        "suite": "flat/",
                    },
                ],
            }
        )
        self.mock_executor()
        assert self.task.executor
        self.task.executor.system_image.data["codename"] = "jessie"
        cmdline = self.task._cmdline()

        self.assertIn(
            "--add-apt-source=deb http://example.com/ unstable main", cmdline
        )
        self.assertIn("--add-apt-source=deb http://example.net/ flat/", cmdline)

    def test_cmdline_extra_repositories_trixie(self) -> None:
        """Ensure the --copy= parameter is passed to autopkgtest."""
        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)
        self.task._prepare_executor()
        self.task.extra_repository_sources = [
            Path("/tmp/extra_repository_0.sources")
        ]
        self.task.extra_repository_keys = [
            Path("/tmp/key.asc"),
        ]
        cmdline = self.task._cmdline()

        self.assertIn(
            (
                "--copy=/tmp/extra_repository_0.sources:"
                "/etc/apt/sources.list.d/extra_repository_0.sources"
            ),
            cmdline,
        )
        self.assertIn("--copy=/tmp/key.asc:/etc/apt/keyrings/key.asc", cmdline)

    def test_cmdline_apt_default_release_not_included(self) -> None:
        """Command line NOT include "--apt-default-release={distribution}."""
        self.mock_executor()
        self.task.data.use_packages_from_base_repository = False
        cmdline = self.task._cmdline()
        self.assertNotIn("--apt-default-release=bookworm", cmdline)

    def test_cmdline_apt_default_release_included(self) -> None:
        """Command line include --"apt-default-release=distribution"."""
        self.mock_executor()
        self.task.data.use_packages_from_base_repository = True
        cmdline = self.task._cmdline()
        self.assertIn("--apt-default-release=bookworm", cmdline)

    def test_cmdline_environment(self) -> None:
        """Command line include "--env=VAR=VALUE" for environment variables."""
        self.mock_executor()
        extra_environment = {"DEBUG": "1", "LOG": "NONE"}
        self.task.data.extra_environment = extra_environment

        cmdline = self.task._cmdline()

        for var, value in extra_environment.items():
            self.assertIn(f"--env={var}={value}", cmdline)

    def test_cmdline_includes_executor(self) -> None:
        """Command line include "-- backend"."""
        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)

        self.task._prepare_executor()

        cmdline = self.task._cmdline()

        backend = "unshare"
        backend_index = cmdline.index(backend)
        self.assertGreater(backend_index, 0)
        self.assertEqual(
            cmdline[backend_index + 1 :],
            [
                "--arch",
                "amd64",
                "--release",
                "bookworm",
                "--tarball",
                str(self.image_cache_path / "42/system.tar.xz"),
            ],
        )

    def test_cmdline_add_targets(self) -> None:
        """Targets for autopkgtest are added."""
        self.mock_executor()
        binary_path = Path("/tmp/download-dir/package.deb")
        source_path = Path("/tmp/download-dir/package.dsc")

        self.task._autopkgtest_targets = [binary_path, source_path]
        cmdline = self.task._cmdline()

        binary_path_index = cmdline.index(str(binary_path))

        self.assertGreater(binary_path_index, 0)
        self.assertEqual(cmdline[binary_path_index + 1], str(source_path))

    def test_needs_internet(self) -> None:
        """Command line include "--needs-internet"."""
        self.mock_executor()
        needs_internet = AutopkgtestNeedsInternet.TRY

        self.task.data.needs_internet = needs_internet

        cmdline = self.task._cmdline()

        self.assertIn(f"--needs-internet={needs_internet}", cmdline)

    def test_timeout(self) -> None:
        """Command line include "--timeout-KEY=VALUE."."""
        self.mock_executor()
        timeout = {"global": 60, "factor": 600}

        self.task.data.timeout = AutopkgtestTimeout(**timeout)

        cmdline = self.task._cmdline()

        for key, value in timeout.items():
            self.assertIn(f"--timeout-{key}={value}", cmdline)
        for key in ("global_", "short", "install", "test", "copy", "copy_"):
            self.assertNotIn(
                f"--timeout-{key}", [arg.split("=")[0] for arg in cmdline]
            )

    def test_cmdline_unshare_copy_resolv_conf(self) -> None:
        """In unshare mode, we copy /etc/resolv.conf if it exists."""
        self.mock_executor()
        self.task.data.backend = BackendType.UNSHARE

        with patch("pathlib.Path.is_file", return_value=True):
            cmdline = self.task._cmdline()

        self.assertIn("--copy=/etc/resolv.conf:/etc/resolv.conf", cmdline)

    def test_cmdline_unshare_missing_resolv_conf(self) -> None:
        """In unshare mode, we don't copy /etc/resolv.conf if it's missing."""
        self.mock_executor()
        self.task.data.backend = BackendType.UNSHARE

        with patch("pathlib.Path.is_file", return_value=False):
            cmdline = self.task._cmdline()

        self.assertNotIn("--copy=/etc/resolv.conf:/etc/resolv.conf", cmdline)

    def test_cmdline_not_unshare_does_not_copy_resolv_conf(self) -> None:
        """For non-unshare backends, we don't copy /etc/resolv.conf."""
        self.mock_executor()
        self.task.data.backend = BackendType.INCUS_LXC

        with patch("pathlib.Path.is_file", return_value=True):
            cmdline = self.task._cmdline()

        self.assertNotIn("--copy=/etc/resolv.conf:/etc/resolv.conf", cmdline)

    def test_select_default_backend(self) -> None:
        """We select Autopkgtest.DEFAULT_BACKEND if backend is auto."""
        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)

        self.task.data.backend = BackendType.AUTO

        self.task._prepare_executor()

        assert self.task.executor

        self.assertEqual(
            self.task.executor.backend_name,
            Autopkgtest.DEFAULT_BACKEND,
        )

    def test_select_specified_backend(self) -> None:
        """We select the specified backend if backend is not auto."""
        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)

        # FIXME: Don't use the default when we support more backends.
        self.task.data.backend = BackendType.UNSHARE

        self.task._prepare_executor()

        assert self.task.executor

        self.assertEqual(self.task.executor.backend_name, "unshare")

    def test_environment_codename_executor(self) -> None:
        """Test _environment_codename gets the executor distribution."""
        self.mock_executor()
        self.assertEqual(self.task._environment_codename(), "bookworm")

    def test_environment_codename_executor_unconfigured(self) -> None:
        """Test _environment_codename raises ValueError without executor."""
        with self.assertRaisesRegex(
            ValueError,
            r"self\.executor not set, call _prepare_executor\(\) first",
        ):
            self.task._environment_codename()

    def test_fetch_input_no_context_artifacts(self) -> None:
        """Download "source_artifact" and "binary_artifacts"."""
        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)
        workspace = "Testing"
        debusine_mock.download_artifact.return_value = ArtifactResponse(
            id=10,
            workspace=workspace,
            category="Test",
            created_at=datetime.datetime.now(),
            data={},
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/not-used"
            ),
            files=FilesResponseType({}),
            files_to_upload=[],
        )

        workspace = "Testing"
        source_package_url = "http://example.com/artifact/15/hello.dsc"

        files = {
            "hello.orig.tar.gz": FileResponse(
                size=4000,
                checksums={},
                type="file",
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl,
                    "http://example.com/artifact/1/hello.orig.tar.gz",
                ),
            ),
            "hello.dsc": FileResponse(
                size=258,
                checksums={},
                type="file",
                url=pydantic.parse_obj_as(pydantic.AnyUrl, source_package_url),
            ),
        }

        data_input = self.task.data.input

        debusine_mock.download_artifact.return_value = ArtifactResponse(
            id=10,
            workspace=workspace,
            category="Test",
            created_at=datetime.datetime.now(),
            data={},
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/not-used"
            ),
            files=FilesResponseType(files),
            files_to_upload=[],
        )

        destination = self.create_temporary_directory()

        self.task.fetch_input(destination)

        calls = [call(data_input.source_artifact, destination, tarball=False)]

        for binary_artifact_id in data_input.binary_artifacts:
            calls.append(call(binary_artifact_id, destination, tarball=False))

        debusine_mock.download_artifact.assert_has_calls(calls)

        self.assertEqual(self.task._source_package_url, source_package_url)
        self.assertEqual(self.task._source_package_path, "hello.dsc")

    def test_fetch_input_context_artifacts(self) -> None:
        """Download the "context_artifacts"."""
        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)

        destination = self.create_temporary_directory()

        data_input = self.task.data.input
        data_input.context_artifacts = LookupMultiple.parse_obj([50, 51])
        assert self.task.dynamic_data
        self.task.dynamic_data.input_context_artifacts_ids = [50, 51]

        self.task.fetch_input(destination)

        calls = []
        for context_artifact_id in data_input.context_artifacts:
            calls.append(call(context_artifact_id, destination, tarball=False))

        debusine_mock.download_artifact.assert_has_calls(calls)

    def test_configure_for_execution(self) -> None:
        """Autopkgtest configure_for_execution() return True."""
        self.mock_executor()

        directory = self.create_temporary_directory()

        self.task._source_package_path = "hello.dsc"

        dsc = self.write_dsc_example_file(
            directory / self.task._source_package_path
        )

        self.assertTrue(self.task.configure_for_execution(directory))

        self.assertEqual(self.task._source_package_name, dsc["Source"])
        self.assertEqual(self.task._source_package_version, dsc["Version"])

    def test_configure_for_execution_invalid_dsc(self) -> None:
        """configure_for_execution() handles an invalid .dsc file."""
        directory = self.create_temporary_directory()
        self.task._source_package_path = "hello.dsc"
        (directory / self.task._source_package_path).touch()

        self.assertFalse(self.task.configure_for_execution(directory))

        assert self.task._debug_log_files_directory

        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()
        self.assertEqual(
            log_file_contents, "hello.dsc is not a valid .dsc file\n"
        )

    def setup_task_succeeded(
        self, test_result: str, fail_on_config: dict[str, bool]
    ) -> Path:
        """
        Write autopkgtest summary file with one test output, configure task.

        :param test_result: autopkgtest test result (FAIL, FLAKY...)
        :param fail_on_config: configure task with fail_on = fail_on_config.
        :return: directory with the summary file.
        """
        directory = self.create_temporary_directory()
        (directory / Autopkgtest.ARTIFACT_DIR).mkdir()
        summary_file = directory / Autopkgtest.SUMMARY_FILE
        summary_file.write_text(test_result)

        self.configure_task(override={"fail_on": fail_on_config})

        return directory

    def test_task_succeeded_failed_test_fail_on_failed(self) -> None:
        """Test failed, fail on failed_test=True. Failure."""
        directory = self.setup_task_succeeded(
            "test_name\tFAIL", {"failed_test": True}
        )
        self.assertFalse(self.task.task_succeeded(0, directory))

    def test_task_succeeded_failed_test_do_not_fail_on_failed(self) -> None:
        """Test failed, fail on failed_test=False. Success."""
        directory = self.setup_task_succeeded(
            "test_name\tFAIL", {"failed_test": False}
        )
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_task_succeed_flaky_test_fail_on_flaky_failure(self) -> None:
        """Test is flaky, fail on flaky_test=True. Failure."""
        directory = self.setup_task_succeeded(
            "test_name\tFLAKY", {"flaky_test": True}
        )
        self.assertFalse(self.task.task_succeeded(0, directory))

    def test_task_succeed_flaky_test_do_not_fail_on_flaky_success(self) -> None:
        """Test is flaky, fail on flaky_test=False. Success."""
        directory = self.setup_task_succeeded(
            "test_name\tFLAKY", {"flaky_test": False}
        )
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_task_succeeded_skipped_test_fail_on_skipped_success(self) -> None:
        """Test is skipped, fail on skipped_test=True. Failure."""
        directory = self.setup_task_succeeded(
            "test_name\tSKIP", {"skipped_test": True}
        )
        self.assertFalse(self.task.task_succeeded(0, directory))

    def test_task_succeeded_skipped_test_do_not_fail_on_skipped_failure(
        self,
    ) -> None:
        """Test is skipped, fail on skipped_test=False. Success."""
        directory = self.setup_task_succeeded(
            "test_name\tSKIP", {"skipped_test": False}
        )
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_task_succeeded_ignore_return_code(self) -> None:
        """Summary file is inconclusive and ignore return value."""
        directory = self.setup_task_succeeded(
            "foo: nothing", {"failed_test": False}
        )
        self.assertTrue(self.task.task_succeeded(4, directory))

    def test_task_succeeded_flaky_return_code(self) -> None:
        """Summary file is inconclusive and return value 2."""
        directory = self.setup_task_succeeded(
            "foo: nothing", {"flaky_test": True}
        )
        self.assertFalse(self.task.task_succeeded(2, directory))

    def test_task_succeeded_skip_return_code(self) -> None:
        """Summary file is inconclusive and return value 8."""
        directory = self.setup_task_succeeded(
            "foo: nothing", {"skipped_test": True}
        )
        self.assertFalse(self.task.task_succeeded(8, directory))

    def test_task_succeeded_fail_on_return_code(self) -> None:
        """Summary file is inconclusive fail on return value."""
        directory = self.setup_task_succeeded(
            "erroneous package: Test dependencies are unsatisfiable.",
            {"failed_test": True},
        )
        self.assertFalse(self.task.task_succeeded(12, directory))

    def test_upload_artifacts(self) -> None:
        """Upload the AutopkgtestArtifact."""
        exec_dir = self.create_temporary_directory()

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()
        self.mock_executor()

        # Worker would set the server
        self.task.configure_server_access(debusine_mock)

        results = {
            "testname": DebianAutopkgtestResult(status=rs.PASS),
        }
        self.task._parsed = results

        self.task._source_package_path = "test"
        self.task._source_package_name = "hello"
        self.task._source_package_version = "2.0.5b"
        self.task._source_package_url = "https://some-url.com/test"

        self.task.upload_artifacts(exec_dir, execution_success=True)

        calls = []

        expected_autopkgtest_artifact = AutopkgtestArtifact.create(
            exec_dir,
            DebianAutopkgtest(
                results=results,
                cmdline=shlex.join(self.task._cmdline()),
                source_package=DebianAutopkgtestSource(
                    name="hello",
                    version="2.0.5b",
                    url=pydantic.parse_obj_as(
                        pydantic.AnyUrl, "https://some-url.com/test"
                    ),
                ),
                architecture=self.task.data.host_architecture,
                distribution="debian:bookworm",
            ),
        )

        calls.append(
            call(
                expected_autopkgtest_artifact,
                workspace=self.task.workspace_name,
                work_request=self.task.work_request_id,
            )
        )

        debusine_mock.upload_artifact.assert_has_calls(calls)

    def test_upload_artifacts_without_configure_server_access(self) -> None:
        """upload_artifacts() asserts that self.debusine is set."""
        self.assertRaisesRegex(
            AssertionError,
            r"^self\.debusine not set",
            self.task.upload_artifacts,
            self.create_temporary_directory(),
            execution_success=True,
        )

    def test_upload_artifacts_without_executor(self) -> None:
        """upload_artifacts() asserts that self.executor is set."""
        self.task.debusine = MagicMock(spec=Debusine)

        msg = (
            r"^self\.executor not set - self\._prepare_for_execution\(\) "
            r"must be called before upload_artifacts\(\)$"
        )
        self.assertRaisesRegex(
            AssertionError,
            msg,
            self.task.upload_artifacts,
            self.create_temporary_directory(),
            execution_success=True,
        )

    def test_check_directory_for_consistency_missing_summary(self) -> None:
        """Return missing ARTIFACT_DIR/summary file."""
        build_directory = self.create_temporary_directory()

        self.assertEqual(
            self.task.check_directory_for_consistency_errors(build_directory),
            [f"'{self.task.SUMMARY_FILE}' does not exist"],
        )

    def test_check_directory_for_consistency_all_good(self) -> None:
        """Return no errors ARTIFACT_DIR/summary file."""
        build_directory = self.create_temporary_directory()
        (build_directory / self.task.ARTIFACT_DIR).mkdir()
        (build_directory / self.task.ARTIFACT_DIR / "summary").write_text(
            "test-results"
        )

        self.assertEqual(
            self.task.check_directory_for_consistency_errors(build_directory),
            [],
        )

    def test_get_label_dynamic_data_is_none(self) -> None:
        """Test get_label if dynamic_data.subject is None."""
        self.assertEqual(self.task.get_label(), "autopkgtest")

    def test_get_label_dynamic_data_subject_is_none(self) -> None:
        """Test get_label if dynamic_data is None."""
        self.task.dynamic_data = AutopkgtestDynamicData(
            environment_id=1,
            input_source_artifact_id=2,
            input_binary_artifacts_ids=[],
        )
        self.assertEqual(self.task.get_label(), "autopkgtest")

    def test_get_label_dynamic_data_subject_is_hello(self) -> None:
        """Test get_label if dynamic_data.subject is set."""
        self.task.dynamic_data = AutopkgtestDynamicData(
            environment_id=1,
            input_source_artifact_id=2,
            input_binary_artifacts_ids=[],
            subject="hello",
        )
        self.assertEqual(self.task.get_label(), "autopkgtest hello")
