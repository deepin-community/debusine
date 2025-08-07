# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the makesourcepackageupload task support on the worker."""
from pathlib import Path
from unittest import mock
from unittest.mock import call

from debusine.artifacts import Upload
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianSourcePackage,
)
from debusine.client.models import (
    LookupResultType,
    LookupSingleResponse,
    RemoteArtifact,
)
from debusine.tasks import MakeSourcePackageUpload, TaskConfigError
from debusine.tasks.models import MakeSourcePackageUploadDynamicData
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_system_tarball_data,
)


class MakeSourcePackageUploadTests(
    ExternalTaskHelperMixin[MakeSourcePackageUpload], TestCase
):
    """Test the MakeSourcePackageUpload class."""

    SAMPLE_TASK_DATA = {
        "environment": "debian/match:codename=bookworm",
        "input": {"source_artifact": 421},
    }

    def setUp(self) -> None:  # noqa: D102
        self.configure_task()

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning with python -m unittest."""
        if self.task._debug_log_files_directory is not None:
            self.task._debug_log_files_directory.cleanup()

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:format=tarball:"
                    "backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (421, None): ArtifactInfo(
                    id=421,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianSourcePackage(
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
            }
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            MakeSourcePackageUploadDynamicData(
                environment_id=1,
                input_source_artifact_id=421,
                subject="hello",
            ),
        )

    def test_compute_dynamic_data_raise_task_config_error_source(self) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:format=tarball:"
                    "backend=unshare",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.source_artifact
                (421, None): ArtifactInfo(
                    id=421,
                    category=ArtifactCategory.BINARY_PACKAGE,
                    data=DebianSourcePackage(
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
            }
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"input.source_artifact: unexpected artifact category: "
            r"'debian:binary-package'. Valid categories: "
            r"\['debian:source-package'\]",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_configure_fails_with_missing_required_data(  # noqa: D102
        self,
    ) -> None:
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"input": {}})

    def test_fetch_input(self) -> None:
        """Test fetch_input: call fetch_artifact(artifact_id, directory)."""
        directory = self.create_temporary_directory()
        source_artifact = self.fake_debian_source_package_artifact()
        self.configure_task()
        self.task.work_request_id = 5
        self.task.dynamic_data = MakeSourcePackageUploadDynamicData(
            environment_id=1, input_source_artifact_id=source_artifact.id
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = source_artifact

        with mock.patch.object(
            self.task, "fetch_artifact", autospec=True, return_value=True
        ) as fetch_artifact_mocked:
            result = self.task.fetch_input(directory)

        self.assertTrue(result)
        fetch_artifact_mocked.assert_called_once_with(
            source_artifact.id, directory
        )

    def test_fetch_input_wrong_category(self) -> None:
        """Test fetch_input when input isn't a source package."""
        directory = self.create_temporary_directory()
        source_artifact = create_artifact_response(id=12)
        self.configure_task()
        self.task.work_request_id = 5
        self.task.dynamic_data = MakeSourcePackageUploadDynamicData(
            environment_id=1, input_source_artifact_id=source_artifact.id
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = source_artifact

        with mock.patch.object(
            self.task, "fetch_artifact", autospec=True
        ) as fetch_artifact_mocked:
            result = self.task.fetch_input(directory)

        self.assertFalse(result)

        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name) / "fetch_input.log"
        ).read_text()
        self.assertEqual(
            log_file_contents,
            (
                "input.source_artifact points to a Testing, not the "
                "expected debian:source-package.\n"
            ),
        )

        fetch_artifact_mocked.assert_not_called()

    def test_execute(self) -> None:
        """Test full (mocked) execution."""
        self.configure_task()
        self.task.work_request_id = 2
        self.task.workspace_name = "testing"
        self.task.dynamic_data = MakeSourcePackageUploadDynamicData(
            environment_id=1, input_source_artifact_id=1
        )
        download_directory = self.create_temporary_directory()

        debusine_mock = self.mock_debusine()
        debusine_mock.lookup_single.return_value = LookupSingleResponse(
            result_type=LookupResultType.ARTIFACT, artifact=1
        )
        debusine_mock.download_artifact.return_value = True
        debusine_mock.upload_artifact.return_value = RemoteArtifact(
            id=2, workspace=self.task.workspace_name
        )

        f_in_contents = "Format: 3.0 (quilt)"
        (f_in := download_directory / "file.dsc").write_text(f_in_contents)
        (f_out := download_directory / "file_source.changes")
        self.write_changes_file(f_out, [f_in])

        self.patch_prepare_executor_instance()

        self.assertTrue(self.task.configure_for_execution(download_directory))

        self.assertEqual(self.task._dsc_path, f_in)
        self.assertEqual(
            self.task._cmdline(),
            [
                "bash",
                "-x",
                "-e",
                str(download_directory / "makesourcepackageupload.sh"),
                str(f_in),
                str(f_out),
                '',
                '',
            ],
        )

        self.task.upload_artifacts(download_directory, execution_success=True)

        debusine_mock.upload_artifact.assert_called_once_with(
            Upload.create(changes_file=f_out),
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )
        debusine_mock.relation_create.assert_has_calls(
            [mock.call(2, 1, "extends"), mock.call(2, 1, "relates-to")]
        )

    def test_optional_arguments(self) -> None:
        """Test optional arguments."""
        self.configure_task(override={"since_version": "2.10-1~"})
        self.task._shell_script = Path("script")
        self.task._dsc_path = Path("in")
        self.task._changes_path = Path("out")
        expected = ["bash", "-x", "-e", "script", "in", "out"]
        self.assertEqual(
            self.task._cmdline(),
            expected + ["2.10-1~", ""],
        )

        self.configure_task(
            override={"target_distribution": "bullseye-security"}
        )
        self.task._shell_script = Path("script")
        self.task._dsc_path = Path("in")
        self.task._changes_path = Path("out")
        self.assertEqual(
            self.task._cmdline(),
            expected + ["", "bullseye-security"],
        )

        self.configure_task(
            override={
                "since_version": "1 0",
                "target_distribution": "buster elts",
            }
        )
        self.task._shell_script = Path("script")
        self.task._dsc_path = Path("in")
        self.task._changes_path = Path("out")
        self.assertEqual(
            self.task._cmdline(),
            expected + ["1 0", "buster elts"],
        )

    def test_upload_artifacts(self) -> None:
        """upload_artifact() and relation_create() is called."""
        self.task.dynamic_data = MakeSourcePackageUploadDynamicData(
            environment_id=1, input_source_artifact_id=1
        )
        download_directory = self.create_temporary_directory()

        # Create file that will be attached when uploading the artifacts
        f_in_contents = "Format: 3.0 (quilt)"
        (f_in := download_directory / "file.dsc").write_text(f_in_contents)
        (f_out := download_directory / "file_source.changes")
        self.write_changes_file(f_out, [f_in])
        self.task._changes_path = f_out

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        workspace_name = "testing"

        uploaded_artifacts = [
            RemoteArtifact(id=10, workspace=workspace_name),
        ]

        debusine_mock.upload_artifact.side_effect = uploaded_artifacts

        # self.task.workspace_name is set by the Worker
        # and is the workspace that downloads the artifact
        # containing the files needed for MakeSourcePackageUpload
        self.task.workspace_name = workspace_name

        # The worker set self.task.work_request_id of the task
        work_request_id = 147
        self.task.work_request_id = work_request_id

        with mock.patch.object(self.task, "executor_instance", autospec=True):
            self.task.upload_artifacts(
                download_directory, execution_success=True
            )

        # Debusine Mock upload_artifact expected calls
        upload_artifact_calls = []
        makesourcepackageupload_artifact = Upload.create(changes_file=f_out)

        upload_artifact_calls.append(
            call(
                makesourcepackageupload_artifact,
                workspace=workspace_name,
                work_request=work_request_id,
            )
        )

        # Debusine mock relation_create expected calls
        relation_create_calls = []
        relation_create_calls.append(
            call(uploaded_artifacts[0].id, 1, "relates-to")
        )

        # Assert that the artifacts were uploaded and relations created
        debusine_mock.upload_artifact.assert_has_calls(upload_artifact_calls)
        debusine_mock.relation_create.assert_has_calls(relation_create_calls)

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "prepare source package upload")
