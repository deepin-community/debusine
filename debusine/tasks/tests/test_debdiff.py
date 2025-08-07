# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the debdiff task support on the worker."""
import itertools
from pathlib import Path
from unittest.mock import call

from debusine.artifacts import DebDiffArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianSourcePackage,
    DebianUpload,
    EmptyArtifactData,
)
from debusine.client.models import (
    LookupResultType,
    LookupSingleResponse,
    RemoteArtifact,
)
from debusine.tasks import DebDiff, TaskConfigError
from debusine.tasks.models import DebDiffDynamicData, LookupMultiple
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import create_system_tarball_data


class DebDiffTests(ExternalTaskHelperMixin[DebDiff], TestCase):
    """Test the DebDiff class."""

    SAMPLE_TASK_DATA = {
        "input": {"source_artifacts": [421, 123]},
        "host_architecture": "amd64",
        "environment": "debian/match:codename=bookworm",
    }

    def setUp(self) -> None:  # noqa: D102
        self.task = DebDiff(self.SAMPLE_TASK_DATA)

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning with python -m unittest."""
        if self.task._debug_log_files_directory is not None:
            self.task._debug_log_files_directory.cleanup()

    def test_configure_fails_with_missing_required_data(  # noqa: D102
        self,
    ) -> None:
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"source_artifacts": {}})

    def test_configure_with_extra_flag(self) -> None:
        """Configuration included "extra_flags". Saved, no exceptions."""
        extra_flags = ["--dirs"]

        directory = self.create_temporary_directory()
        (original_directory := directory / "original").mkdir()
        (original_directory / "file.dsc").write_text("")
        (new_directory := directory / "new").mkdir()
        (new_directory / "file.dsc").write_text("")

        self.configure_task(override={"extra_flags": extra_flags})
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=[1, 2],
        )

        self.patch_prepare_executor_instance()

        self.assertEqual(self.task.data.extra_flags, extra_flags)

        self.assertTrue(self.task.configure_for_execution(directory))

    def test_configure_with_invalid_flag(self) -> None:
        """Invalid extra flags are rejected."""
        extra_flags = ["--unknown-flag"]

        directory = self.create_temporary_directory()
        (directory / "file.dsc").write_text("")

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
                # input.source_artifacts
                (421, None): ArtifactInfo(
                    id=421,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianSourcePackage(
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
                (123, None): ArtifactInfo(
                    id=123,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianSourcePackage(
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
            }
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            DebDiffDynamicData(
                environment_id=1,
                input_source_artifacts_ids=[421, 123],
                input_binary_artifacts_ids=None,
                subject="source:hello",
            ),
        )

    def test_compute_dynamic_subject_binary_artifacts(self) -> None:
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
            },
            multiple_lookups={
                # binary artifacts
                (LookupMultiple.parse_obj([500]), None): [
                    ArtifactInfo(
                        id=500,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Source": "hello",
                                "Files": [{"name": "hello.deb"}],
                            },
                        ),
                    ),
                ],
                (LookupMultiple.parse_obj([501]), None): [
                    ArtifactInfo(
                        id=501,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Source": "hello",
                                "Files": [{"name": "hello.deb"}],
                            },
                        ),
                    ),
                ],
            },
        )

        self.configure_task(
            override={"input": {"binary_artifacts": [[500], [501]]}}
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            DebDiffDynamicData(
                environment_id=1,
                input_source_artifacts_ids=None,
                input_binary_artifacts_ids=[[500], [501]],
                subject="binary:hello",
            ),
        )

    def test_compute_dynamic_subject_is_none(self) -> None:
        """
        Test compute_dynamic_data set subject is None.

        There is no source package, and binary packages are from different
        source packages.
        """
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
            },
            multiple_lookups={
                # binary artifacts
                (LookupMultiple.parse_obj([500]), None): [
                    ArtifactInfo(
                        id=500,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Source": "hello",
                                "Files": [{"name": "hello.deb"}],
                            },
                        ),
                    ),
                ],
                (LookupMultiple.parse_obj([501]), None): [
                    ArtifactInfo(
                        id=501,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Source": "linux-base",
                                "Files": [{"name": "hello.deb"}],
                            },
                        ),
                    ),
                ],
            },
        )

        self.configure_task(
            override={"input": {"binary_artifacts": [[500], [501]]}}
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            DebDiffDynamicData(
                environment_id=1,
                input_source_artifacts_ids=None,
                input_binary_artifacts_ids=[[500], [501]],
                subject=None,
            ),
        )

    def test_compute_dynamic_data_raise_task_config_error_source(self) -> None:
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
                # input.source_artifacts
                (421, None): ArtifactInfo(
                    id=421,
                    category=ArtifactCategory.BINARY_PACKAGES,
                    data=DebianSourcePackage(
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
                (123, None): ArtifactInfo(
                    id=123,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=DebianSourcePackage(
                        name="hello", version="1.0", type="dpkg", dsc_fields={}
                    ),
                ),
            }
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.source_artifacts: unexpected artifact category: "
            r"'debian:binary-packages'. "
            r"Valid categories: \['debian:source-package'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_compute_dynamic_data_raise_task_config_error_binary(self) -> None:
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
            },
            multiple_lookups={
                # binary artifacts
                (LookupMultiple.parse_obj([500]), None): [
                    ArtifactInfo(
                        id=500,
                        category=ArtifactCategory.SOURCE_PACKAGE,
                        data=EmptyArtifactData(),
                    ),
                ],
                (LookupMultiple.parse_obj([501]), None): [
                    ArtifactInfo(
                        id=501,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="linux-base",
                            srcpkg_version="1.0",
                            deb_fields={},
                            deb_control_files=[],
                        ),
                    ),
                ],
            },
        )

        self.configure_task(
            override={"input": {"binary_artifacts": [[500], [501]]}}
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.binary_artifacts: unexpected artifact category: "
            r"'debian:source-package'. Valid categories: \['debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db),

    def test_get_source_artifacts_ids_dynamic_dynamic_data_none(self) -> None:
        self.task.dynamic_data = None
        self.assertEqual(self.task.get_source_artifacts_ids(), [])

    def test_get_source_artifacts_ids_source_artifacts(self) -> None:
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=[1, 2],
            input_binary_artifacts_ids=None,
        )
        self.assertEqual(self.task.get_source_artifacts_ids(), [1, 2])

    def test_get_source_artifacts_ids_binary_artifacts(self) -> None:
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=None,
            input_binary_artifacts_ids=[[1, 2], [3, 4]],
        )
        self.assertEqual(self.task.get_source_artifacts_ids(), [1, 2, 3, 4])

    def test_get_source_artifacts_ids_source_binary_artifacts(self) -> None:
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=[1, 2],
            input_binary_artifacts_ids=[[3, 4]],
        )
        self.assertEqual(self.task.get_source_artifacts_ids(), [1, 2, 3, 4])

    def test_fetch_input_source(self) -> None:
        """Test fetching different source inputs."""
        self.configure_task(override={"input": {'source_artifacts': [1, 2]}})
        self.task.work_request_id = 2
        self.task.workspace_name = "testing"
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=[1, 2],
            input_binary_artifacts_ids=None,
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

        self.assertTrue(self.task.fetch_input(download_directory))

    def test_fetch_input_binary(self) -> None:
        """Test fetching different binary inputs."""
        self.configure_task(
            override={"input": {'binary_artifacts': [[1, 2], [3, 4]]}}
        )
        self.task.work_request_id = 2
        self.task.workspace_name = "testing"
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=None,
            input_binary_artifacts_ids=[[1, 2], [3, 4]],
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

        self.assertTrue(self.task.fetch_input(download_directory))

    def test_configure_for_execution_from_artifact_error_no_build_logs_org(
        self,
    ) -> None:
        """configure_for_execution() no .dsc files in original: return False."""
        self.patch_prepare_executor_instance()
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=[1, 2],
        )
        download_directory = self.create_temporary_directory()
        (original_directory := download_directory / "original").mkdir()
        (file := original_directory / "file1.changes").write_text("")
        (new_directory := download_directory / "new").mkdir()
        (new_directory / "file2.dsc").write_text("")

        self.assertFalse(self.task.configure_for_execution(download_directory))

        assert self.task._debug_log_files_directory is not None
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()
        self.assertEqual(
            log_file_contents,
            f"No original *.dsc file to be analyzed. Files: {[str(file)]}\n",
        )

    def test_configure_for_execution_from_artifact_error_no_build_logs(
        self,
    ) -> None:
        """configure_for_execution() no .dsc files in new: return False."""
        self.patch_prepare_executor_instance()
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=[1, 2],
        )
        download_directory = self.create_temporary_directory()
        (original_directory := download_directory / "original").mkdir()
        (original_directory / "file1.dsc").write_text("")
        (new_directory := download_directory / "new").mkdir()
        (file := new_directory / "file2.changes").write_text("")

        self.assertFalse(self.task.configure_for_execution(download_directory))

        assert self.task._debug_log_files_directory is not None
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()
        self.assertEqual(
            log_file_contents,
            f"No new *.dsc file to be analyzed. Files: {[str(file)]}\n",
        )

    def test_configure_for_execution_binaries(self) -> None:
        """configure_for_execution() two .changes files: return True."""
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_source_artifacts_ids=None,
            input_binary_artifacts_ids=[[1, 2], [3, 4]],
        )
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        (original_directory := download_directory / "original").mkdir()
        (original_directory / "file1.changes").write_text("")
        (new_directory := download_directory / "new").mkdir()
        (new_directory / "file2.changes").write_text("")

        self.assertTrue(self.task.configure_for_execution(download_directory))

    def test_cmdline_with_extra_flags(self) -> None:
        """Cmdline add extra_flags."""
        extra_flags = ["--dirs"]
        self.configure_task(override={"extra_flags": extra_flags})
        self.task._original_targets = [self.create_temporary_file()]

        cmdline = self.task._cmdline()

        self.assertIn(extra_flags[0], cmdline)
        self.assertEqual(cmdline[-1], str(self.task._original_targets[0]))

    def test_task_succeeded_empty_file_return_true(self) -> None:
        """task_succeeded() for an empty file return True."""
        directory = self.create_temporary_directory()
        (directory / DebDiff.CAPTURE_OUTPUT_FILENAME).write_text("")

        self.configure_task()
        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_upload_artifacts(self) -> None:
        """upload_artifact() and relation_create() is called."""
        exec_dir = self.create_temporary_directory()

        # Create file that will be attached when uploading the artifacts
        debdiff_output = exec_dir / self.task.CAPTURE_OUTPUT_FILENAME
        debdiff_output.write_text("1c1\n< Source: foo\n---\n> Source: bar\n")

        # Used to verify the relations
        self.task._source_artifacts_ids = [1]
        binary_artifact_id_1 = 10
        binary_artifact_id_2 = 11
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1,
            input_binary_artifacts_ids=[
                [binary_artifact_id_1],
                [binary_artifact_id_2],
            ],
        )

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        workspace_name = "testing"

        uploaded_artifacts = [
            RemoteArtifact(id=10, workspace=workspace_name),
        ]

        debusine_mock.upload_artifact.side_effect = uploaded_artifacts

        # self.task.workspace_name is set by the Worker
        # and is the workspace that downloads the artifact
        # containing the files needed for debdiff
        self.task.workspace_name = workspace_name

        # The worker set self.task.work_request_id of the task
        work_request_id = 147
        self.task.work_request_id = work_request_id

        self.task.upload_artifacts(exec_dir, execution_success=True)

        # Debusine Mock upload_artifact expected calls
        upload_artifact_calls = []
        debdiff_artifact = DebDiffArtifact.create(
            debdiff_output=debdiff_output, original="", new=""
        )

        upload_artifact_calls.append(
            call(
                debdiff_artifact,
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

        for uploaded_artifact, binary_artifact_id in itertools.product(
            uploaded_artifacts, [binary_artifact_id_1, binary_artifact_id_2]
        ):
            relation_create_calls.append(
                call(uploaded_artifact.id, binary_artifact_id, "relates-to")
            )

        # Assert that the artifacts were uploaded and relations created
        debusine_mock.upload_artifact.assert_has_calls(upload_artifact_calls)
        debusine_mock.relation_create.assert_has_calls(relation_create_calls)

    def test_execute(self) -> None:
        """Test full (mocked) execution."""
        self.configure_task(override={"input": {'source_artifacts': [1, 2]}})
        self.task.work_request_id = 2
        self.task.workspace_name = "testing"
        self.task.dynamic_data = DebDiffDynamicData(
            environment_id=1, input_source_artifacts_ids=[1, 2]
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

        self.assertTrue(self.task.fetch_input(download_directory))

        assert debusine_mock.download_artifact.call_count == 2
        debusine_mock.download_artifact.assert_has_calls(
            [
                call(1, download_directory / "original", tarball=False),
                call(2, download_directory / "new", tarball=False),
            ]
        )

        f1_contents = "Source: foo"
        (file1 := download_directory / "original" / "file1.dsc").write_text(
            f1_contents
        )

        f2_contents = "Source: bar"
        (file2 := download_directory / "new" / "file2.dsc").write_text(
            f2_contents
        )

        self.patch_prepare_executor_instance()

        self.assertTrue(self.task.configure_for_execution(download_directory))

        self.assertEqual(self.task._original_targets, [file1])
        self.assertEqual(self.task._new_targets, [file2])
        self.assertEqual(
            self.task._cmdline(),
            [
                "debdiff",
                str(file1),
                str(file2),
            ],
        )

        # Create file that will be attached when uploading the artifacts
        debdiff_output = download_directory / self.task.CAPTURE_OUTPUT_FILENAME
        debdiff_output.write_text("1c1\n< Source: foo\n---\n> Source: bar\n")

        self.task.upload_artifacts(download_directory, execution_success=True)

        debusine_mock.upload_artifact.assert_called_once_with(
            DebDiffArtifact.create(
                debdiff_output=download_directory
                / DebDiff.CAPTURE_OUTPUT_FILENAME,
                original=file1.name,
                new=file2.name,
            ),
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )
        assert debusine_mock.relation_create.call_count == 2
        debusine_mock.relation_create.assert_has_calls(
            [call(2, 1, "relates-to"), call(2, 2, "relates-to")]
        )

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "debdiff")
