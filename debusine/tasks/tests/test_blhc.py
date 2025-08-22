# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the blhc task support on the worker."""
import itertools
from pathlib import Path
from unittest.mock import call

from debusine.artifacts import BlhcArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    DebianPackageBuildLog,
    EmptyArtifactData,
)
from debusine.client.models import LookupResultType, LookupSingleResponse
from debusine.tasks import Blhc, TaskConfigError
from debusine.tasks.models import BlhcDynamicData
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import create_remote_artifact


class BlhcTests(ExternalTaskHelperMixin[Blhc], TestCase):
    """Test the Blhc class."""

    SAMPLE_TASK_DATA = {
        "input": {"artifact": 421},
    }

    def setUp(self) -> None:
        super().setUp()
        self.task = Blhc(self.SAMPLE_TASK_DATA)

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning with python -m unittest."""
        if self.task._debug_log_files_directory is not None:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def test_configure_fails_with_missing_required_data(
        self,
    ) -> None:
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"input": {}})

    def test_configure_with_extra_flag(self) -> None:
        """Configuration included "extra_flags". Saved, no exceptions."""
        extra_flags = ["--bindnow"]

        directory = self.create_temporary_directory()
        (directory / "file.build").write_text("")

        self.configure_task(override={"extra_flags": extra_flags})

        self.patch_prepare_executor_instance()

        self.assertEqual(self.task.data.extra_flags, extra_flags)

        self.assertTrue(self.task.configure_for_execution(directory))

    def test_configure_with_invalid_flag(self) -> None:
        """Configuration included "extra_flags". Saved, no exceptions."""
        extra_flags = ["--unknown-flag"]

        directory = self.create_temporary_directory()
        (directory / "file.build").write_text("")

        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"extra_flags": extra_flags})

    def test_configure_additional_properties_in_input(self) -> None:
        """Assert no additional properties in task_data input."""
        error_msg = "extra fields not permitted"
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            self.configure_task(
                task_data={
                    "input": {
                        "artifact": 5,
                        "some_additional_property": 87,
                    }
                }
            )

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # input.artifact
                (421, None): ArtifactInfo(
                    id=421,
                    category=ArtifactCategory.PACKAGE_BUILD_LOG,
                    data=DebianPackageBuildLog(
                        source="hello",
                        version="1.0",
                        architecture="amd64",
                        filename="hello_1.0_amd64.buildlog",
                    ),
                )
            }
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            BlhcDynamicData(
                input_artifact_id=421, subject="hello", runtime_context=None
            ),
        )

    def test_compute_dynamic_data_raise_task_config_error(self) -> None:
        """Artifact input.artifact has unexpected category."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # input.artifact
                (421, None): ArtifactInfo(
                    id=421,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=EmptyArtifactData(),
                )
            }
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.artifact: unexpected artifact category: "
            r"'debian:source-package'. "
            r"Valid categories: \['debian:package-build-log'\]$",
        ):
            self.task.compute_dynamic_data(task_db),

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        self.assertEqual(self.task.get_input_artifacts_ids(), [])

        self.task.dynamic_data = BlhcDynamicData(input_artifact_id=1)
        self.assertEqual(self.task.get_input_artifacts_ids(), [1])

        self.task.dynamic_data = BlhcDynamicData(
            input_artifact_id=1, environment_id=2
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [1, 2])

    def test_configure_for_execution_from_artifact_error_no_build_logs(
        self,
    ) -> None:
        """configure_for_execution() no .build files: return False."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        (file1 := download_directory / "file1.dsc").write_text("")
        (file2 := download_directory / "file2.changes").write_text("")

        self.assertFalse(self.task.configure_for_execution(download_directory))

        files = sorted([str(file1), str(file2)])
        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()
        self.assertEqual(
            log_file_contents,
            f"No *.build files to be analyzed. Files: {files}\n",
        )

    def test_cmdline_with_extra_flags(self) -> None:
        """Cmdline add extra_flags."""
        extra_flags = ["--bindnow"]
        self.configure_task(override={"extra_flags": extra_flags})
        self.task._blhc_target = self.create_temporary_file()

        cmdline = self.task._cmdline()

        self.assertIn(extra_flags[0], cmdline)
        self.assertEqual(cmdline[-1], str(self.task._blhc_target))

    def test_task_succeeded_empty_file_return_true(self) -> None:
        """task_succeeded() for an empty file return True."""
        directory = self.create_temporary_directory()
        (directory / Blhc.CAPTURE_OUTPUT_FILENAME).write_text("")

        self.configure_task()
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_upload_artifacts(self) -> None:
        """upload_artifact() and relation_create() is called."""
        exec_dir = self.create_temporary_directory()

        # Create file that will be attached when uploading the artifacts
        blhc_output = exec_dir / self.task.CAPTURE_OUTPUT_FILENAME
        blhc_output.write_text("NONVERBOSE BUILD:    Compiling gkrust\n")

        # Used to verify the relations
        self.task._source_artifacts_ids = [1]

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        workspace_name = "testing"

        uploaded_artifacts = [
            create_remote_artifact(id=10, workspace=workspace_name),
        ]

        debusine_mock.upload_artifact.side_effect = uploaded_artifacts

        # self.task.workspace_name is set by the Worker
        # and is the workspace that downloads the artifact
        # containing the files needed for Blhc
        self.task.workspace_name = workspace_name

        # The worker set self.task.work_request_id of the task
        work_request_id = 147
        self.task.work_request_id = work_request_id

        self.task.upload_artifacts(exec_dir, execution_success=True)

        # Debusine Mock upload_artifact expected calls
        upload_artifact_calls = []
        blhc_artifact = BlhcArtifact.create(blhc_output=blhc_output)

        upload_artifact_calls.append(
            call(
                blhc_artifact,
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

    def test_execute(self) -> None:
        """Test full (mocked) execution."""
        self.configure_task(override={"input": {'artifact': 1}})
        self.task.work_request_id = 2
        self.task.workspace_name = "testing"
        self.task.dynamic_data = BlhcDynamicData(input_artifact_id=1)
        download_directory = self.create_temporary_directory()

        debusine_mock = self.mock_debusine()
        debusine_mock.lookup_single.return_value = LookupSingleResponse(
            result_type=LookupResultType.ARTIFACT, artifact=1
        )
        debusine_mock.download_artifact.return_value = True
        debusine_mock.upload_artifact.return_value = create_remote_artifact(
            id=2, workspace=self.task.workspace_name
        )

        self.assertTrue(self.task.fetch_input(download_directory))

        debusine_mock.download_artifact.assert_called_once_with(
            1, download_directory, tarball=False
        )

        f1_contents = "Compiling gkrust v0.1.0 (/build/rust)"
        (file1 := download_directory / "file.build").write_text(f1_contents)

        self.patch_prepare_executor_instance()

        self.assertTrue(self.task.configure_for_execution(download_directory))

        self.assertEqual(self.task._blhc_target, file1)
        self.assertEqual(
            self.task._cmdline(),
            [
                "blhc",
                "--debian",
                str(file1),
            ],
        )

        # Create file that will be attached when uploading the artifacts
        blhc_output = download_directory / self.task.CAPTURE_OUTPUT_FILENAME
        blhc_output.write_text(
            "NONVERBOSE BUILD:    Compiling gkrust v0.1.0 (/build/rust)\n"
        )

        self.task.upload_artifacts(download_directory, execution_success=True)

        debusine_mock.upload_artifact.assert_called_once_with(
            BlhcArtifact.create(
                blhc_output=download_directory / Blhc.CAPTURE_OUTPUT_FILENAME
            ),
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )
        debusine_mock.relation_create.assert_called_once_with(
            2, 1, "relates-to"
        )

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "blhc")
