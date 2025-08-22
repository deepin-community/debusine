# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for extracting signing input from other artifacts."""

import json
import logging
import shutil
import subprocess
from functools import partial
from pathlib import Path, PurePath
from typing import Any, AnyStr
from unittest import mock

from debusine.artifacts import LocalArtifact, SigningInputArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianUpload,
    EmptyArtifactData,
)
from debusine.artifacts.playground import ArtifactPlayground
from debusine.client.models import (
    ArtifactResponse,
    RelationType,
    RemoteArtifact,
)
from debusine.tasks import ExtractForSigning, TaskConfigError
from debusine.tasks.extract_for_signing import ExtractError
from debusine.tasks.models import (
    ExtractForSigningDynamicData,
    LookupMultiple,
    WorkerType,
)
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_file_response,
    create_remote_artifact,
    create_system_tarball_data,
)


def _mock_run(
    args: list[str], run_as_root: bool = False, **kwargs: Any  # noqa: U100
) -> subprocess.CompletedProcess[AnyStr]:
    """Mock InstanceInterface.run using subprocess.run."""
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    return subprocess.run(args, **kwargs)


class ExtractForSigningTests(
    ExternalTaskHelperMixin[ExtractForSigning], TestCase
):
    """Tests for ExtractForSigning."""

    SAMPLE_TASK_DATA = {
        "input": {"template_artifact": 2, "binary_artifacts": [3, 4]},
        "environment": "debian/match:codename=bookworm",
    }

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.configure_task()

    def tearDown(self) -> None:
        """Delete debug log files directory if it exists."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    def test_can_run_on(self) -> None:
        """can_run_on returns True if unshare is available."""
        self.assertTrue(
            self.task.can_run_on(
                {
                    "system:worker_type": WorkerType.EXTERNAL,
                    "executor:unshare:available": True,
                    "extractforsigning:version": self.task.TASK_VERSION,
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
                    "extractforsigning:version": self.task.TASK_VERSION + 1,
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
                    "extractforsigning:version": self.task.TASK_VERSION,
                }
            )
        )

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:format=tarball:"
                    "backend=unshare:variant=",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.template_artifact
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.BINARY_PACKAGE,
                    data=DebianBinaryPackage(
                        srcpkg_name="template",
                        srcpkg_version="1.0",
                        deb_fields={"Package": "hello"},
                        deb_control_files=[],
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (LookupMultiple.parse_obj([3, 4]), None): [
                    ArtifactInfo(
                        id=3,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            deb_fields={"Package": "unsigned-1"},
                            deb_control_files=[],
                        ),
                    ),
                    ArtifactInfo(
                        id=4,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            deb_fields={"Package": "unsigned-2"},
                            deb_control_files=[],
                        ),
                    ),
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            ExtractForSigningDynamicData(
                environment_id=1,
                input_template_artifact_id=2,
                input_binary_artifacts_ids=[3, 4],
                subject="hello",
            ),
        )

    def test_compute_dynamic_data_from_upload(self) -> None:
        """compute_dynamic_data follows relations from upload artifacts."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:format=tarball:"
                    "backend=unshare:variant=",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.template_artifact
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.BINARY_PACKAGE,
                    data=DebianBinaryPackage(
                        srcpkg_name="template",
                        srcpkg_version="1.0",
                        deb_fields={"Package": "hello"},
                        deb_control_files=[],
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (LookupMultiple.parse_obj([3, 4]), None): [
                    ArtifactInfo(
                        id=3,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "all",
                                "Files": [{"name": "unsigned-doc_1.0_all.deb"}],
                            },
                        ),
                    ),
                    ArtifactInfo(
                        id=4,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Files": [
                                    {"name": "unsigned-1_1.0_amd64.deb"},
                                    {"name": "unsigned-2_1.0_amd64.deb"},
                                ],
                            },
                        ),
                    ),
                ]
            },
            relations={
                (
                    (3, 4),
                    ArtifactCategory.BINARY_PACKAGE,
                    RelationType.EXTENDS,
                ): [
                    ArtifactInfo(
                        id=artifact_id,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            deb_fields={"Package": binary_package_name},
                            deb_control_files=[],
                        ),
                    )
                    for artifact_id, binary_package_name in (
                        (5, "unsigned-doc"),
                        (6, "unsigned-1"),
                        (7, "unsigned-2"),
                    )
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            ExtractForSigningDynamicData(
                environment_id=1,
                input_template_artifact_id=2,
                input_binary_artifacts_ids=[5, 6, 7],
                subject="hello",
            ),
        )

    def test_compute_dynamic_data_raise_task_config_error_template(
        self,
    ) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:format=tarball:"
                    "backend=unshare:variant=",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.template_artifact
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=EmptyArtifactData(),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (LookupMultiple.parse_obj([3, 4]), None): [
                    ArtifactInfo(
                        id=3,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            deb_fields={"Package": "unsigned-1"},
                            deb_control_files=[],
                        ),
                    ),
                    ArtifactInfo(
                        id=4,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            deb_fields={"Package": "unsigned-2"},
                            deb_control_files=[],
                        ),
                    ),
                ]
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.template_artifact: unexpected artifact category: "
            r"'debian:source-package'. "
            r"Valid categories: \['debian:binary-package'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_compute_dynamic_data_raise_task_config_error_binary(
        self,
    ) -> None:
        task_db = FakeTaskDatabase(
            single_lookups={
                # environment
                (
                    "debian/match:codename=bookworm:format=tarball:"
                    "backend=unshare:variant=",
                    CollectionCategory.ENVIRONMENTS,
                ): ArtifactInfo(
                    id=1,
                    category=ArtifactCategory.SYSTEM_TARBALL,
                    data=create_system_tarball_data(),
                ),
                # input.template_artifact
                (2, None): ArtifactInfo(
                    id=2,
                    category=ArtifactCategory.BINARY_PACKAGE,
                    data=DebianBinaryPackage(
                        srcpkg_name="template",
                        srcpkg_version="1.0",
                        deb_fields={"Package": "hello"},
                        deb_control_files=[],
                    ),
                ),
            },
            multiple_lookups={
                # input.binary_artifacts
                (LookupMultiple.parse_obj([3, 4]), None): [
                    ArtifactInfo(
                        id=3,
                        category=ArtifactCategory.BINARY_PACKAGES,
                        data=DebianBinaryPackages(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            version="1.0",
                            architecture="amd64",
                            packages=["unsigned-1"],
                        ),
                    ),
                    ArtifactInfo(
                        id=4,
                        category=ArtifactCategory.BINARY_PACKAGE,
                        data=DebianBinaryPackage(
                            srcpkg_name="unsigned",
                            srcpkg_version="1.0",
                            deb_fields={"Package": "unsigned-2"},
                            deb_control_files=[],
                        ),
                    ),
                ]
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.binary_artifacts\[0\]: unexpected artifact category: "
            r"'debian:binary-packages'. "
            r"Valid categories: \['debian:binary-package', 'debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        self.assertEqual(self.task.get_input_artifacts_ids(), [])

        self.task.dynamic_data = ExtractForSigningDynamicData(
            environment_id=1,
            input_template_artifact_id=2,
            input_binary_artifacts_ids=[3, 4],
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [1, 2, 3, 4])

    def test_fetch_input_template_wrong_category(self) -> None:
        """fetch_input checks the category of the template artifact."""
        self.task.work_request_id = 147
        self.task.dynamic_data = ExtractForSigningDynamicData(
            environment_id=1,
            input_template_artifact_id=2,
            input_binary_artifacts_ids=[3, 4],
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.return_value = create_artifact_response(
            id=2, category=ArtifactCategory.TEST
        )

        self.assertFalse(
            self.task.fetch_input(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_input.log"
            ).read_text(),
            "Expected template_artifact to be of category "
            "debian:binary-package; got debusine:test\n",
        )

    def test_fetch_input_template_udeb(self) -> None:
        """fetch_input fails if the template does not have a .deb."""
        self.task.work_request_id = 147
        self.task.dynamic_data = ExtractForSigningDynamicData(
            environment_id=1,
            input_template_artifact_id=2,
            input_binary_artifacts_ids=[3, 4],
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.return_value = create_artifact_response(
            id=2,
            category=ArtifactCategory.BINARY_PACKAGE,
            data={"deb_fields": {"Package": "hello-signed-template"}},
            files={"hello-signed-template.udeb": create_file_response()},
        )

        self.assertFalse(
            self.task.fetch_input(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_input.log"
            ).read_text(),
            "Expected template_artifact file name to match *.deb; "
            "got hello-signed-template.udeb\n",
        )

    def _create_binary_package_response(
        self, artifact_id: int, name: str, version: str
    ) -> ArtifactResponse:
        return create_artifact_response(
            id=artifact_id,
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": name,
                "srcpkg_version": version,
                "deb_fields": {"Package": name},
                "deb_control_files": [],
            },
            files={f"{name}_{version}_amd64.deb": create_file_response()},
        )

    def test_fetch_input_gathers_information(self) -> None:
        """fetch_input gathers information from artifacts."""
        self.task.work_request_id = 147
        self.task.dynamic_data = ExtractForSigningDynamicData(
            environment_id=1,
            input_template_artifact_id=2,
            input_binary_artifacts_ids=[3, 4],
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.side_effect = [
            self._create_binary_package_response(
                2, "hello-signed-template", "1.0+1"
            ),
            self._create_binary_package_response(3, "hello", "1.0-1"),
            self._create_binary_package_response(4, "libhello1", "1.0-1"),
        ]
        destination = self.create_temporary_directory()

        self.assertTrue(self.task.fetch_input(destination))

        self.assertEqual(self.task._template_deb_name, "hello-signed-template")
        self.assertEqual(
            self.task._template_path,
            destination / "template" / "hello-signed-template_1.0+1_amd64.deb",
        )
        self.assertEqual(
            self.task._binary_artifacts, {"hello": 3, "libhello1": 4}
        )
        self.assertEqual(
            self.task._binary_paths,
            {
                "hello": destination / "binary" / "hello_1.0-1_amd64.deb",
                "libhello1": (
                    destination / "binary" / "libhello1_1.0-1_amd64.deb"
                ),
            },
        )

    def test_configure_for_execution(self) -> None:
        """configure_for_execution starts an executor instance."""
        download_directory = self.create_temporary_directory()
        mocked_prepare_executor_instance = (
            self.patch_prepare_executor_instance()
        )

        self.assertTrue(self.task.configure_for_execution(download_directory))

        mocked_prepare_executor_instance.assert_called_once_with()

    def test_pull_from_executor(self) -> None:
        """_pull_from_executor pulls a directory from the executor using tar."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.reset_mock()
        self.task.executor_instance.run.return_value = (
            subprocess.CompletedProcess([], 0)
        )

        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()
        source = self.task._remote_execute_directory / "directory"
        target = self.task._local_execute_directory / "directory"
        source_tar = self.task._remote_execute_directory / "directory.tar"
        target_tar = self.task._local_execute_directory / "directory.tar"

        with mock.patch("subprocess.run") as mock_run:
            self.task._pull_from_executor(source, target)

        self.task.executor_instance.run.assert_called_once_with(
            [
                "tar",
                "-C",
                str(self.task._remote_execute_directory),
                "-cf",
                f"{source}.tar",
                "directory",
            ],
            run_as_root=False,
            cwd=self.task._remote_execute_directory,
            env=None,
            stdout=mock.ANY,
            stderr=mock.ANY,
        )
        self.task.executor_instance.file_pull.assert_called_once_with(
            source_tar, target_tar
        )
        mock_run.assert_called_once_with(
            ["tar", "--one-top-level", "-xf", target_tar],
            cwd=self.task._local_execute_directory,
        )

    def test_extract_binary_error(self) -> None:
        """_extract_binary raises ExtractError if dpkg-deb exits non-zero."""
        execute_directory = self.create_temporary_directory()

        with (
            self.assertRaisesRegex(ExtractError, "dpkg-deb exited with code 1"),
            mock.patch.object(self.task, "run_cmd", return_value=1),
        ):
            self.task._extract_binary(
                Path("foo.deb"), execute_directory / "hello-signed-template"
            )

    def test_extract_binary_success(self) -> None:
        """_extract_binary calls dpkg-deb properly."""
        execute_directory = self.create_temporary_directory()

        with (
            self.assertLogs("debusine.tasks", level=logging.INFO) as log,
            mock.patch.object(
                self.task, "run_cmd", return_value=0
            ) as mock_run_cmd,
        ):
            self.task._extract_binary(
                Path("foo.deb"), execute_directory / "hello-signed-template"
            )

        mock_run_cmd.assert_called_once_with(
            [
                "dpkg-deb",
                "-x",
                "foo.deb",
                str(execute_directory / "hello-signed-template"),
            ],
            execute_directory,
        )
        self.assertEqual(
            log.output,
            [
                f"INFO:debusine.tasks:Executing: dpkg-deb -x foo.deb "
                f"{execute_directory}/hello-signed-template",
                "INFO:debusine.tasks:dpkg-deb exited with code 0",
            ],
        )

    def test_read_manifest(self) -> None:
        """_read_manifest extracts and parses the manifest."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.file_pull.side_effect = shutil.copy2
        self.task.executor_instance.run.side_effect = _mock_run
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()
        self.task._template_deb_name = "hello-signed-template"
        self.task._template_path = (
            download_directory
            / "template"
            / "hello-signed-template_1.0+1_amd64.deb"
        )
        manifest_path = PurePath(
            "usr/share/code-signing", self.task._template_deb_name, "files.json"
        )
        self.task._template_path.parent.mkdir()
        ArtifactPlayground.write_deb_file(
            self.task._template_path,
            data_files={manifest_path: json.dumps({"packages": {}}).encode()},
        )

        self.assertEqual(self.task._read_manifest(), {"packages": {}})

    def test_make_signing_input_artifact_invalid_package_name(self) -> None:
        """_make_signing_input_artifact fails if a package name is invalid."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        self.task._binary_paths = {}
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()

        with self.assertRaisesRegex(
            ExtractError, "'_invalid' is not a valid package name"
        ):
            self.task._make_signing_input_artifact("_invalid", {})

    def test_make_signing_input_artifact_file_with_parent_segment(self) -> None:
        """_make_signing_input_artifact fails if a file name contains ".."."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.side_effect = _mock_run
        hello_path = download_directory / "binary" / "hello_1.0-1_amd64.deb"
        hello_path.parent.mkdir()
        ArtifactPlayground.write_deb_file(hello_path)
        self.task._binary_paths = {"hello": hello_path}
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()

        with (
            self.assertRaisesRegex(
                ExtractError,
                "File name 'usr/../../etc/passwd' may not contain '..' "
                "segments",
            ),
            mock.patch.object(
                self.task, "_pull_from_executor", side_effect=shutil.copytree
            ),
        ):
            self.task._make_signing_input_artifact(
                "hello",
                {
                    "files": [
                        {"sig_type": "efi", "file": "usr/../../etc/passwd"}
                    ]
                },
            )

    def test_make_signing_input_artifact_absolute_file(self) -> None:
        """_make_signing_input_artifact fails if a file name is absolute."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.side_effect = _mock_run
        hello_path = download_directory / "binary" / "hello_1.0-1_amd64.deb"
        hello_path.parent.mkdir()
        ArtifactPlayground.write_deb_file(hello_path)
        self.task._binary_paths = {"hello": hello_path}
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()

        with (
            self.assertRaisesRegex(
                ExtractError, "File name '/boot/vmlinuz' may not be absolute"
            ),
            mock.patch.object(
                self.task, "_pull_from_executor", side_effect=shutil.copytree
            ),
        ):
            self.task._make_signing_input_artifact(
                "hello",
                {"files": [{"sig_type": "efi", "file": "/boot/vmlinuz"}]},
            )

    def test_make_signing_input_artifact_symlink_escape(self) -> None:
        """_make_signing_input_artifact fails if a file escapes the tree."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.side_effect = _mock_run
        hello_path = download_directory / "binary" / "hello_1.0-1_amd64.deb"
        hello_path.parent.mkdir()
        ArtifactPlayground.write_deb_file(
            hello_path, data_symlinks={PurePath("evil-symlink"): PurePath("..")}
        )
        self.task._binary_paths = {"hello": hello_path}
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()

        with (
            self.assertRaisesRegex(
                ExtractError,
                "File name 'evil-symlink/etc/passwd' may not traverse symlinks "
                "to outside the package",
            ),
            mock.patch.object(
                self.task,
                "_pull_from_executor",
                side_effect=partial(shutil.copytree, symlinks=True),
            ),
        ):
            self.task._make_signing_input_artifact(
                "hello",
                {
                    "files": [
                        {"sig_type": "efi", "file": "evil-symlink/etc/passwd"}
                    ]
                },
            )

    def test_make_signing_input_artifact_valid(self) -> None:
        """_make_signing_input_artifact handles valid input."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.side_effect = _mock_run
        hello_path = download_directory / "binary" / "hello_1.0-1_amd64.deb"
        hello_path.parent.mkdir()
        ArtifactPlayground.write_deb_file(
            hello_path, data_files={PurePath("boot/vmlinuz"): b"vmlinuz"}
        )
        self.task._binary_paths = {"hello": hello_path}
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()

        with mock.patch.object(
            self.task, "_pull_from_executor", side_effect=shutil.copytree
        ):
            artifact = self.task._make_signing_input_artifact(
                "hello",
                {"files": [{"sig_type": "efi", "file": "boot/vmlinuz"}]},
            )

        self.assertEqual(
            artifact,
            SigningInputArtifact.create(
                [self.task._local_execute_directory / "hello/boot/vmlinuz"],
                self.task._local_execute_directory,
                binary_package_name="hello",
            ),
        )

    def test_make_signing_input_artifact_trusted_certs(self) -> None:
        """_make_signing_input_artifact passes through trusted_certs."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.side_effect = _mock_run
        hello_path = download_directory / "binary" / "hello_1.0-1_amd64.deb"
        hello_path.parent.mkdir()
        ArtifactPlayground.write_deb_file(
            hello_path, data_files={PurePath("boot/vmlinuz"): b"vmlinuz"}
        )
        self.task._binary_paths = {"hello": hello_path}
        self.task._remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()

        with mock.patch.object(
            self.task, "_pull_from_executor", side_effect=shutil.copytree
        ):
            artifact = self.task._make_signing_input_artifact(
                "hello",
                {
                    "trusted_certs": ["0" * 64],
                    "files": [{"sig_type": "efi", "file": "boot/vmlinuz"}],
                },
            )

        self.assertEqual(
            artifact,
            SigningInputArtifact.create(
                [self.task._local_execute_directory / "hello/boot/vmlinuz"],
                self.task._local_execute_directory,
                trusted_certs=["0" * 64],
                binary_package_name="hello",
            ),
        )

    def test_upload_artifacts_execution_failed(self) -> None:
        """upload_artifacts does not upload anything if execution failed."""
        self.task.work_request_id = 147
        debusine_mock = self.mock_debusine()
        execute_directory = self.create_temporary_directory()

        self.task.upload_artifacts(execute_directory, execution_success=False)

        debusine_mock.upload_artifact.assert_not_called()
        debusine_mock.relation_create.assert_not_called()

    def test_upload_artifacts_execution_succeeded(self) -> None:
        """upload_artifacts uploads if execution succeeded."""
        directory = self.create_temporary_directory()
        (directory / "hello/boot").mkdir(parents=True)
        (directory / "hello/boot/vmlinuz").write_bytes(b"vmlinuz")
        self.task.work_request_id = 147
        self.task.workspace_name = "testing"
        self.task.dynamic_data = ExtractForSigningDynamicData(
            environment_id=1,
            input_template_artifact_id=2,
            input_binary_artifacts_ids=[3, 4],
        )
        self.task._binary_artifacts = {"hello": 3}
        self.task._signing_input_artifacts = {
            "hello": SigningInputArtifact.create(
                [directory / "hello/boot/vmlinuz"], directory
            )
        }
        debusine_mock = self.mock_debusine()
        debusine_mock.upload_artifact.return_value = create_remote_artifact(
            id=5, workspace=self.task.workspace_name
        )
        execute_directory = self.create_temporary_directory()

        self.task.upload_artifacts(execute_directory, execution_success=True)

        debusine_mock.upload_artifact.assert_called_once_with(
            self.task._signing_input_artifacts["hello"],
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )
        debusine_mock.relation_create.assert_has_calls(
            [
                mock.call(
                    5,
                    self.task.dynamic_data.input_template_artifact_id,
                    "relates-to",
                ),
                mock.call(
                    5, self.task._binary_artifacts["hello"], "relates-to"
                ),
            ]
        )

    def test_cleanup_without_local_execute_directory(self) -> None:
        """cleanup() works even if _local_execute_directory hasn't been set."""
        # Just check that it can be called without raising an exception.
        self.task.cleanup()

    def test_execute_end_to_end(self) -> None:
        """End-to-end execution works."""
        self.task.work_request_id = 147
        self.task.workspace_name = "testing"
        self.task.dynamic_data = ExtractForSigningDynamicData(
            environment_id=1,
            input_template_artifact_id=2,
            input_binary_artifacts_ids=[3, 4],
        )

        # Create some test files.
        directory = self.create_temporary_directory()
        template_deb_path = directory / "hello-signed-template_1.0+1_amd64.deb"
        code_signing_path = PurePath(
            "usr/share/code-signing/hello-signed-template"
        )
        ArtifactPlayground.write_deb_file(
            template_deb_path,
            data_files={
                (code_signing_path / "files.json"): json.dumps(
                    {
                        "packages": {
                            "hello": {
                                "files": [
                                    {"sig_type": "efi", "file": "boot/vmlinuz"}
                                ]
                            },
                            "grub-efi-amd64-bin": {
                                "files": [
                                    {
                                        "sig_type": "efi",
                                        "file": "usr/lib/grub/grubx64.efi",
                                    }
                                ]
                            },
                        }
                    }
                ).encode()
            },
        )
        hello_path = directory / "hello_1.0-1_amd64.deb"
        ArtifactPlayground.write_deb_file(
            hello_path, data_files={PurePath("boot/vmlinuz"): b"vmlinuz"}
        )
        grub_path = directory / "grub-efi-amd64-bin_2.12-2_amd64.deb"
        ArtifactPlayground.write_deb_file(
            grub_path,
            data_files={PurePath("usr/lib/grub/grubx64.efi"): b"grub"},
        )

        # Set up a mock client.
        debusine_mock = self.mock_debusine()
        artifacts = {
            2: self._create_binary_package_response(
                2, "hello-signed-template", "1.0+1"
            ),
            3: self._create_binary_package_response(3, "hello", "1.0-1"),
            4: self._create_binary_package_response(
                4, "grub-efi-amd64-bin", "2.12-2"
            ),
        }
        artifact_files = {
            (2, "hello-signed-template_1.0+1_amd64.deb"): template_deb_path,
            (3, "hello_1.0-1_amd64.deb"): hello_path,
            (4, "grub-efi-amd64-bin_2.12-2_amd64.deb"): grub_path,
        }
        uploaded_artifact_ids = {
            ArtifactCategory.SIGNING_INPUT: 5,
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: 6,
        }
        output_path = self.create_temporary_directory()

        def download_artifact(
            artifact_id: int, destination: Path, tarball: bool = False
        ) -> ArtifactResponse:
            assert not tarball
            for file_name in artifacts[artifact_id].files:
                target = destination / file_name
                target.parent.mkdir(parents=True, exist_ok=True)
                assert not target.exists()
                shutil.copy2(artifact_files[(artifact_id, file_name)], target)
            return artifacts[artifact_id]

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            if local_artifact.category == ArtifactCategory.SIGNING_INPUT:
                for name, path in local_artifact.files.items():
                    assert not Path(name).is_absolute()
                    (output_path / name).parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    shutil.copy(path, output_path / name)
            return create_remote_artifact(
                id=uploaded_artifact_ids[local_artifact.category],
                workspace="testing",
            )

        debusine_mock.download_artifact.side_effect = download_artifact
        debusine_mock.upload_artifact.side_effect = upload_artifact

        with (
            mock.patch.object(
                self.task, "_prepare_executor_instance", autospec=True
            ),
            mock.patch.object(
                self.task, "executor_instance", autospec=True
            ) as mock_executor_instance,
        ):
            mock_executor_instance.file_pull.side_effect = shutil.copy2
            mock_executor_instance.run.side_effect = _mock_run
            self.assertTrue(self.task.execute())

        self.assertEqual(debusine_mock.upload_artifact.call_count, 3)
        self.assertEqual(
            (output_path / "hello/boot/vmlinuz").read_bytes(), b"vmlinuz"
        )
        self.assertEqual(
            (
                output_path / "grub-efi-amd64-bin/usr/lib/grub/grubx64.efi"
            ).read_bytes(),
            b"grub",
        )

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "extract signing input")
