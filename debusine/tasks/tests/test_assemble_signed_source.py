# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for assembling signed source packages."""

import json
import logging
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePath
from textwrap import dedent
from typing import Any, AnyStr
from unittest import mock

from debian import deb822
from debusine.artifacts import LocalArtifact, SourcePackage, Upload
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebusineSigningOutput,
    EmptyArtifactData,
)
from debusine.artifacts.playground import ArtifactPlayground
from debusine.assets import KeyPurpose
from debusine.client.models import (
    ArtifactResponse,
    RelationsResponse,
    RemoteArtifact,
)
from debusine.tasks import AssembleSignedSource, TaskConfigError
from debusine.tasks.assemble_signed_source import AssembleError
from debusine.tasks.models import (
    AssembleSignedSourceDynamicData,
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


class AssembleSignedSourceTests(
    ExternalTaskHelperMixin[AssembleSignedSource], TestCase
):
    """Tests for AssembleSignedSource."""

    SAMPLE_TASK_DATA = {
        "environment": "debian/match:codename=bookworm",
        "template": 11,
        # TODO: We should test multiple signed artifacts once more than one
        # KeyPurpose is defined.
        "signed": [12],
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
                    "assemblesignedsource:version": self.task.TASK_VERSION,
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
                    "assemblesignedsource:version": self.task.TASK_VERSION + 1,
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
                    "assemblesignedsource:version": self.task.TASK_VERSION,
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
                # template
                (11, None): ArtifactInfo(
                    id=11,
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
                # signed
                (LookupMultiple.parse_obj([12]), None): [
                    ArtifactInfo(
                        id=12,
                        category=ArtifactCategory.SIGNING_OUTPUT,
                        data=DebusineSigningOutput(
                            purpose=KeyPurpose.UEFI, fingerprint="", results=[]
                        ),
                    )
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            AssembleSignedSourceDynamicData(
                environment_id=1,
                template_id=11,
                signed_ids=[12],
                subject="hello",
                runtime_context=None,
            ),
        )

    def test_compute_dynamic_data_subject_raise_task_config_error(self) -> None:
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
                # template
                (11, None): ArtifactInfo(
                    id=11,
                    category=ArtifactCategory.SOURCE_PACKAGE,
                    data=EmptyArtifactData(),
                ),
            },
            multiple_lookups={
                # signed
                (LookupMultiple.parse_obj([12]), None): [
                    ArtifactInfo(
                        id=12,
                        category=ArtifactCategory.SIGNING_OUTPUT,
                        data=DebusineSigningOutput(
                            purpose=KeyPurpose.UEFI, fingerprint="", results=[]
                        ),
                    )
                ]
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^template: unexpected artifact category: "
            r"'debian:source-package'. Valid categories: "
            r"\['debian:binary-package'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        self.assertEqual(self.task.get_input_artifacts_ids(), [])

        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1,
            template_id=2,
            signed_ids=[3],
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [1, 2, 3])

    def test_fetch_input_template_wrong_category(self) -> None:
        """fetch_input checks the category of the template artifact."""
        self.task.work_request_id = 147
        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1, template_id=11, signed_ids=[12]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.return_value = create_artifact_response(
            id=11, category=ArtifactCategory.TEST
        )

        self.assertFalse(
            self.task.fetch_input(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_input.log"
            ).read_text(),
            "Expected template of category debian:binary-package; got "
            "debusine:test\n",
        )

    def test_fetch_input_template_no_files(self) -> None:
        """fetch_input fails if the template has no .deb files."""
        self.task.work_request_id = 147
        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1, template_id=11, signed_ids=[12]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.return_value = create_artifact_response(
            id=11,
            category=ArtifactCategory.BINARY_PACKAGE,
            files={"foo": create_file_response()},
        )

        self.assertFalse(
            self.task.fetch_input(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_input.log"
            ).read_text(),
            "Expected exactly one .deb package in template; got []\n",
        )

    def test_fetch_input_template_multiple_deb_files(self) -> None:
        """fetch_input fails if the template has multiple .deb files."""
        self.task.work_request_id = 147
        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1, template_id=11, signed_ids=[12]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.return_value = create_artifact_response(
            id=11,
            category=ArtifactCategory.BINARY_PACKAGE,
            files={
                "a.deb": create_file_response(),
                "b.deb": create_file_response(),
            },
        )

        self.assertFalse(
            self.task.fetch_input(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_input.log"
            ).read_text(),
            "Expected exactly one .deb package in template; got "
            "['a.deb', 'b.deb']\n",
        )

    def _create_binary_package_response(
        self, artifact_id: int
    ) -> ArtifactResponse:
        return create_artifact_response(
            id=artifact_id,
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": "hello-signed",
                "srcpkg_version": "1.0+1",
                "deb_fields": {"Package": "hello-signed"},
                "deb_control_files": [],
            },
            files={"hello-signed_1.0+1_amd64.deb": create_file_response()},
        )

    def test_fetch_input_signed_wrong_category(self) -> None:
        """fetch_input checks the category of signed artifacts."""
        self.task.work_request_id = 147
        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1, template_id=11, signed_ids=[12]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.side_effect = [
            self._create_binary_package_response(11),
            create_artifact_response(id=12, category=ArtifactCategory.TEST),
        ]

        self.assertFalse(
            self.task.fetch_input(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_input.log"
            ).read_text(),
            "Expected template of category debusine:signing-output; got "
            "debusine:test\n",
        )

    def test_fetch_input_signed_gathers_files_and_built_using(self) -> None:
        """fetch_input gathers information from signed artifacts."""
        self.task.work_request_id = 147
        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1, template_id=11, signed_ids=[12]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.download_artifact.side_effect = [
            self._create_binary_package_response(11),
            create_artifact_response(
                id=12,
                category=ArtifactCategory.SIGNING_OUTPUT,
                data={
                    "purpose": "uefi",
                    "fingerprint": "0" * 64,
                    "results": [
                        {
                            "file": "hello/hello-1.efi",
                            "output_file": "hello/hello-1.efi.sig",
                        },
                        {"file": "hello/hello-2.efi", "error_message": "Boom"},
                    ],
                },
            ),
        ]
        artifact_categories = {
            2: ArtifactCategory.TEST,
            3: ArtifactCategory.BINARY_PACKAGE,
            4: ArtifactCategory.SIGNING_INPUT,
        }
        relations = {
            4: [(2, "extends"), (3, "relates-to")],
            12: [(2, "relates-to"), (4, "relates-to")],
        }

        def relation_list(artifact_id: int) -> RelationsResponse:
            return RelationsResponse.parse_obj(
                [
                    {
                        "id": 1,
                        "artifact": artifact_id,
                        "target": target_id,
                        "type": relation_type,
                    }
                    for target_id, relation_type in relations[artifact_id]
                ]
            )

        def artifact_get(artifact_id: int) -> ArtifactResponse:
            return create_artifact_response(
                id=6, category=artifact_categories[artifact_id]
            )

        debusine_mock.relation_list.side_effect = relation_list
        debusine_mock.artifact_get.side_effect = artifact_get
        destination = self.create_temporary_directory()

        self.assertTrue(self.task.fetch_input(destination))

        self.assertEqual(
            self.task._signed_files,
            {
                (KeyPurpose.UEFI, "hello/hello-1.efi"): (
                    destination
                    / "signed-artifacts"
                    / "12"
                    / "hello"
                    / "hello-1.efi.sig"
                )
            },
        )
        self.assertEqual(self.task._built_using_ids, [3])

    def test_configure_for_execution(self) -> None:
        """configure_for_execution installs dependencies."""
        download_directory = self.create_temporary_directory()
        mocked_prepare_executor_instance = (
            self.patch_prepare_executor_instance()
        )

        self.assertTrue(self.task.configure_for_execution(download_directory))

        mocked_prepare_executor_instance.assert_called_once_with()
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.assert_has_calls(
            [
                mock.call.run(
                    ["apt-get", "update"], run_as_root=True, check=True
                ),
                mock.call.run(
                    ["apt-get", "--yes", "install", "dpkg-dev"],
                    run_as_root=True,
                    check=True,
                ),
            ]
        )

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

    def test_push_to_executor(self) -> None:
        """_push_to_executor pushes a directory to the executor using tar."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.run.reset_mock()
        self.task.executor_instance.run.return_value = (
            subprocess.CompletedProcess([], 0)
        )

        self.task._local_execute_directory = self.create_temporary_directory()
        self.task._remote_execute_directory = self.create_temporary_directory()
        source = self.task._local_execute_directory / "directory"
        target = self.task._remote_execute_directory / "directory"
        source_tar = self.task._local_execute_directory / "directory.tar"
        target_tar = self.task._remote_execute_directory / "directory.tar"

        with mock.patch("subprocess.run") as mock_run:
            self.task._push_to_executor(source, target)

        mock_run.assert_called_once_with(
            ["tar", "-cf", source_tar, "directory"],
            cwd=self.task._local_execute_directory,
        )
        self.task.executor_instance.file_push.assert_called_once_with(
            source_tar, target_tar
        )
        self.task.executor_instance.run.assert_called_once_with(
            [
                "tar",
                "-C",
                str(self.task._remote_execute_directory),
                "--one-top-level",
                "-xf",
                f"{target}.tar",
            ],
            run_as_root=False,
            cwd=self.task._remote_execute_directory,
            env=None,
            stdout=mock.ANY,
            stderr=mock.ANY,
        )

    def patch_executor_methods(self) -> tuple[mock.MagicMock, mock.MagicMock]:
        """
        Patch _pull_from_executor and _push_to_executor to do local copies.

        Returns (mock_pull_from_executor, mock_push_to_executor).
        """
        pull_from_executor_patcher = mock.patch.object(
            self.task, "_pull_from_executor", side_effect=shutil.copytree
        )
        mock_pull_from_executor = pull_from_executor_patcher.start()
        self.addCleanup(pull_from_executor_patcher.stop)

        push_to_executor_patcher = mock.patch.object(
            self.task, "_push_to_executor", side_effect=shutil.copytree
        )
        mock_push_to_executor = push_to_executor_patcher.start()
        self.addCleanup(push_to_executor_patcher.stop)

        return mock_pull_from_executor, mock_push_to_executor

    def test_extract_binary_error(self) -> None:
        """_extract_binary raises AssembleError if dpkg-deb exits non-zero."""
        execute_directory = self.create_temporary_directory()

        with (
            self.assertRaisesRegex(
                AssembleError, "dpkg-deb exited with code 1"
            ),
            mock.patch.object(self.task, "run_cmd", return_value=1),
        ):
            self.task._extract_binary(
                Path("foo.deb"), execute_directory / "template"
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
                Path("foo.deb"), execute_directory / "template"
            )

        mock_run_cmd.assert_called_once_with(
            ["dpkg-deb", "-x", "foo.deb", str(execute_directory / "template")],
            execute_directory,
        )
        self.assertEqual(
            log.output,
            [
                f"INFO:debusine.tasks:Executing: dpkg-deb -x foo.deb "
                f"{execute_directory}/template",
                "INFO:debusine.tasks:dpkg-deb exited with code 0",
            ],
        )

    def _create_source_template(self, source_template_path: Path) -> None:
        format_path = source_template_path / "debian/source/format"
        format_path.parent.mkdir(parents=True)
        format_path.write_text("3.0 (native)\n")
        for file in (
            "debian/changelog",
            "debian/control",
            "debian/copyright",
            "debian/rules",
        ):
            (source_template_path / file).touch()

    def test_check_template_required_file(self) -> None:
        """_check_template checks that various files are present."""
        code_signing_path = self.create_temporary_directory()
        source_template_path = code_signing_path / "source-template"
        self._create_source_template(source_template_path)
        for file in (
            "debian/source/format",
            "debian/changelog",
            "debian/control",
            "debian/copyright",
            "debian/rules",
        ):
            (source_template_path / file).unlink()
            with self.subTest(file=file):
                with self.assertRaisesRegex(
                    AssembleError, f"Required file {file} missing from template"
                ):
                    self.task._check_template(code_signing_path)
            (source_template_path / file).touch()

    def test_check_template_wrong_source_format(self) -> None:
        """_check_template requires a 3.0 (native) source format."""
        code_signing_path = self.create_temporary_directory()
        source_template_path = code_signing_path / "source-template"
        self._create_source_template(source_template_path)
        (source_template_path / "debian/source/format").write_text(
            "3.0 (quilt)\n"
        )
        with self.assertRaisesRegex(
            AssembleError,
            re.escape(
                "Expected template source format '3.0 (native)'; "
                "got '3.0 (quilt)'"
            ),
        ):
            self.task._check_template(code_signing_path)

    def test_check_template_forbidden_file(self) -> None:
        """_check_template checks that various files are absent."""
        code_signing_path = self.create_temporary_directory()
        source_template_path = code_signing_path / "source-template"
        self._create_source_template(source_template_path)
        for file in ("debian/source/options", "debian/source/local-options"):
            (source_template_path / file).touch()
            with self.subTest(file=file):
                with self.assertRaisesRegex(
                    AssembleError, f"Template may not contain {file}"
                ):
                    self.task._check_template(code_signing_path)
            (source_template_path / file).unlink()

    def test_check_template_ok(self) -> None:
        """_check_template accepts a well-formed template."""
        code_signing_path = self.create_temporary_directory()
        source_template_path = code_signing_path / "source-template"
        self._create_source_template(source_template_path)
        self.task._check_template(code_signing_path)

    def test_add_signed_files_invalid_package_name(self) -> None:
        """_add_signed_files fails if a package name is invalid."""
        code_signing_path = self.create_temporary_directory()
        source_path = self.create_temporary_directory()
        (code_signing_path / "files.json").write_text(
            json.dumps({"packages": {"_invalid": {}}})
        )

        with self.assertRaisesRegex(
            AssembleError, "'_invalid' is not a valid package name"
        ):
            self.task._add_signed_files(code_signing_path, source_path)

    def test_add_signed_files_file_with_parent_segment(self) -> None:
        """_add_signed_files fails if a file name contains ".."."""
        code_signing_path = self.create_temporary_directory()
        source_path = self.create_temporary_directory()
        (code_signing_path / "files.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "hello": {
                            "files": [
                                {
                                    "sig_type": "efi",
                                    "file": "usr/../../etc/passwd",
                                }
                            ]
                        }
                    }
                }
            )
        )

        with self.assertRaisesRegex(
            AssembleError,
            re.escape(
                "File name 'usr/../../etc/passwd' may not contain '..' segments"
            ),
        ):
            self.task._add_signed_files(code_signing_path, source_path)

    def test_add_signed_files_absolute_file(self) -> None:
        """_add_signed_files fails if a file name is absolute."""
        code_signing_path = self.create_temporary_directory()
        source_path = self.create_temporary_directory()
        (code_signing_path / "files.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "hello": {
                            "files": [
                                {"sig_type": "efi", "file": "/boot/vmlinuz"}
                            ]
                        }
                    }
                }
            )
        )

        with self.assertRaisesRegex(
            AssembleError, "File name '/boot/vmlinuz' may not be absolute"
        ):
            self.task._add_signed_files(code_signing_path, source_path)

    def test_add_signed_files_unhandled_sig_type(self) -> None:
        """_add_signed_files fails if a file has an unknown sig_type."""
        code_signing_path = self.create_temporary_directory()
        source_path = self.create_temporary_directory()
        (code_signing_path / "files.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "hello": {
                            "files": [
                                {"sig_type": "unknown", "file": "boot/vmlinuz"}
                            ]
                        }
                    }
                }
            )
        )

        with self.assertRaisesRegex(
            AssembleError, "Cannot handle sig_type 'unknown'"
        ):
            self.task._add_signed_files(code_signing_path, source_path)

    def test_add_signed_files_missing_signature(self) -> None:
        """_add_signed_files if a required signature is not available."""
        code_signing_path = self.create_temporary_directory()
        source_path = self.create_temporary_directory()
        (code_signing_path / "files.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "hello": {
                            "files": [
                                {"sig_type": "efi", "file": "boot/vmlinuz"}
                            ]
                        }
                    }
                }
            )
        )

        with self.assertRaisesRegex(
            AssembleError,
            "uefi signature of 'hello/boot/vmlinuz' not available",
        ):
            self.task._add_signed_files(code_signing_path, source_path)

    def test_add_signed_files_copies_signatures(self) -> None:
        """_add_signed_files copies signatures into the source path."""
        download_path = self.create_temporary_directory()
        code_signing_path = self.create_temporary_directory()
        source_path = self.create_temporary_directory()
        (code_signing_path / "files.json").write_text(
            json.dumps(
                {
                    "packages": {
                        "hello": {
                            "files": [
                                {"sig_type": "efi", "file": "boot/vmlinuz"}
                            ]
                        }
                    }
                }
            )
        )
        signed_vmlinuz_path = (
            download_path / "signed-artifacts" / "12" / "boot" / "vmlinuz"
        )
        signed_vmlinuz_path.parent.mkdir(parents=True)
        signed_vmlinuz_path.write_text("signed vmlinuz\n")
        self.task._signed_files = {
            (KeyPurpose.UEFI, "hello/boot/vmlinuz"): signed_vmlinuz_path
        }

        self.task._add_signed_files(code_signing_path, source_path)

        self.assertEqual(
            (
                source_path
                / "debian"
                / "signatures"
                / "hello"
                / "boot"
                / "vmlinuz.sig"
            ).read_text(),
            "signed vmlinuz\n",
        )

    def test_build_source_package_dpkg_source_error(self) -> None:
        """_build_source_package raises if dpkg-source exits non-zero."""
        self.patch_executor_methods()
        self.task._remote_execute_directory = self.create_temporary_directory()
        local_execute_directory = self.create_temporary_directory()
        local_source_path = local_execute_directory / "source"
        local_source_path.mkdir()

        with (
            self.assertRaisesRegex(
                AssembleError, "dpkg-source exited with code 1"
            ),
            mock.patch.object(self.task, "run_cmd", return_value=1),
        ):
            self.task._build_source_package(local_source_path)

    def test_build_source_package_dpkg_genchanges_error(self) -> None:
        """_build_source_package raises if dpkg-genchanges exits non-zero."""
        self.patch_executor_methods()
        self.task._remote_execute_directory = self.create_temporary_directory()
        local_execute_directory = self.create_temporary_directory()
        local_source_path = local_execute_directory / "source"
        self._create_source_template(local_source_path)
        now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        (local_source_path / "debian/changelog").write_text(
            dedent(
                f"""\
                hello (1.0-1) unstable; urgency=low

                  * Initial release.

                 -- Example Maintainer <example@example.org>  {now}
                """
            )
        )

        with (
            self.assertRaisesRegex(
                AssembleError, "dpkg-genchanges exited with code 1"
            ),
            mock.patch.object(self.task, "run_cmd", side_effect=[0, 1]),
        ):
            self.task._build_source_package(local_source_path)

    def test_build_source_package_success(self) -> None:
        """_build_source_package runs the correct commands."""
        self.patch_executor_methods()
        self.task._remote_execute_directory = self.create_temporary_directory()
        local_execute_directory = self.create_temporary_directory()
        remote_source_path = self.task._remote_execute_directory / "source"
        local_source_path = local_execute_directory / "source"
        self._create_source_template(local_source_path)
        now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        (local_source_path / "debian/changelog").write_text(
            dedent(
                f"""\
                hello (1.0-1) unstable; urgency=low

                  * Initial release.

                 -- Example Maintainer <example@example.org>  {now}
                """
            )
        )

        with (
            self.assertLogs("debusine.tasks", level=logging.INFO) as log,
            mock.patch.object(
                self.task, "run_cmd", return_value=0
            ) as mock_run_cmd,
        ):
            self.task._build_source_package(local_source_path)

        expected_changes_path = (
            remote_source_path.parent / "hello_1.0-1_source.changes"
        )
        mock_run_cmd.assert_has_calls(
            [
                mock.call(
                    [
                        "sh",
                        "-c",
                        f"cd {remote_source_path} && dpkg-source -b .",
                    ],
                    local_source_path,
                ),
                mock.call(
                    [
                        "sh",
                        "-c",
                        f"cd {remote_source_path} && "
                        f"dpkg-genchanges -S -DDistribution=unstable -UCloses "
                        f"-O{expected_changes_path}",
                    ],
                    local_source_path,
                ),
            ]
        )
        self.assertEqual(
            log.output,
            [
                f"INFO:debusine.tasks:Executing: "
                f"sh -c 'cd {remote_source_path} && dpkg-source -b .'",
                "INFO:debusine.tasks:dpkg-source exited with code 0",
                f"INFO:debusine.tasks:Executing: "
                f"sh -c 'cd {remote_source_path} && "
                f"dpkg-genchanges -S -DDistribution=unstable -UCloses "
                f"-O{expected_changes_path}'",
                "INFO:debusine.tasks:dpkg-genchanges exited with code 0",
            ],
        )

    def test_create_artifacts(self) -> None:
        """_create_artifacts creates artifacts for a source package."""
        self.patch_prepare_executor_instance()
        download_directory = self.create_temporary_directory()
        self.assertTrue(self.task.configure_for_execution(download_directory))
        assert isinstance(self.task.executor_instance, mock.MagicMock)
        self.task.executor_instance.file_pull.side_effect = shutil.copy2

        remote_execute_directory = self.create_temporary_directory()
        self.task._local_execute_directory = self.create_temporary_directory()
        remote_dsc_path = remote_execute_directory / "hello-signed_1.0+1.dsc"
        remote_changes_path = (
            remote_execute_directory / "hello-signed_1.0+1_source.changes"
        )
        ArtifactPlayground.write_deb822_file(
            deb822.Dsc,
            remote_dsc_path,
            [],
            source="hello-signed",
            version="1.0+1",
        )
        ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            remote_changes_path,
            [remote_dsc_path],
            source="hello-signed",
            version="1.0+1",
        )

        self.task._create_artifacts(remote_changes_path)

        self.assertEqual(
            self.task._source_package_artifact,
            SourcePackage.create(
                name="hello-signed",
                version="1.0+1",
                files=[
                    self.task._local_execute_directory / remote_dsc_path.name
                ],
            ),
        )
        self.assertEqual(
            self.task._upload_artifact,
            Upload.create(
                changes_file=(
                    self.task._local_execute_directory
                    / remote_changes_path.name
                )
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
        dsc_path = directory / "hello-signed_1.0+1.dsc"
        changes_path = directory / "hello-signed_1.0+1_source.changes"
        ArtifactPlayground.write_deb822_file(
            deb822.Dsc, dsc_path, [], source="hello-signed", version="1.0+1"
        )
        ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            changes_path,
            [dsc_path],
            source="hello-signed",
            version="1.0+1",
        )
        self.task.work_request_id = 147
        self.task.workspace_name = "testing"
        self.task._built_using_ids = [3]
        self.task._source_package_artifact = SourcePackage.create(
            name="hello-signed",
            version="1.0+1",
            files=[dsc_path],
        )
        self.task._upload_artifact = Upload.create(changes_file=changes_path)
        debusine_mock = self.mock_debusine()
        debusine_mock.upload_artifact.side_effect = [
            create_remote_artifact(id=20, workspace=self.task.workspace_name),
            create_remote_artifact(id=21, workspace=self.task.workspace_name),
        ]
        execute_directory = self.create_temporary_directory()

        self.task.upload_artifacts(execute_directory, execution_success=True)

        debusine_mock.upload_artifact.assert_has_calls(
            [
                mock.call(
                    self.task._source_package_artifact,
                    workspace=self.task.workspace_name,
                    work_request=self.task.work_request_id,
                ),
                mock.call(
                    self.task._upload_artifact,
                    workspace=self.task.workspace_name,
                    work_request=self.task.work_request_id,
                ),
            ]
        )
        debusine_mock.relation_create.assert_has_calls(
            [mock.call(20, 3, "built-using"), mock.call(21, 20, "extends")]
        )

    def test_cleanup_without_local_execute_directory(self) -> None:
        """cleanup() works even if _local_execute_directory hasn't been set."""
        # Just check that it can be called without raising an exception.
        self.task.cleanup()

    def test_execute_end_to_end(self) -> None:
        """End-to-end execution works."""
        self.task.work_request_id = 147
        self.task.workspace_name = "testing"
        self.task.dynamic_data = AssembleSignedSourceDynamicData(
            environment_id=1, template_id=11, signed_ids=[12]
        )

        # Create some test files.
        directory = self.create_temporary_directory()
        template_deb_path = directory / "hello-signed_1.0+1_amd64.deb"
        code_signing_path = PurePath("usr/share/code-signing/hello-signed")
        source_template_path = code_signing_path / "source-template"
        now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        ArtifactPlayground.write_deb_file(
            template_deb_path,
            source_name="hello-signed",
            source_version="1.0+1",
            data_files={
                (code_signing_path / "files.json"): json.dumps(
                    {
                        "packages": {
                            "hello": {
                                "files": [
                                    {"sig_type": "efi", "file": "boot/vmlinuz"}
                                ]
                            }
                        }
                    }
                ).encode(),
                (
                    source_template_path / "debian/source/format"
                ): b"3.0 (native)\n",
                source_template_path
                / "debian/changelog": dedent(
                    f"""\
                    hello-signed (1.0+1) unstable; urgency=low

                      * Initial release.

                     -- Example Maintainer <example@example.org>  {now}
                    """
                ).encode(),
                source_template_path
                / "debian/control": dedent(
                    """\
                    Source: hello-signed
                    Section: admin
                    Priority: optional
                    Maintainer: Example Maintainer <example@example.org>

                    Package: hello-signed
                    Architecture: amd64
                    Description: Example package
                    """
                ).encode(),
                source_template_path / "debian/copyright": b"",
                source_template_path / "debian/rules": b"",
            },
        )
        signed_vmlinuz_path = directory / "vmlinuz.sig"
        signed_vmlinuz_path.write_text("signed vmlinuz\n")

        # Set up a mock client.
        debusine_mock = self.mock_debusine()
        artifacts = {
            11: self._create_binary_package_response(11),
            12: create_artifact_response(
                id=12,
                category=ArtifactCategory.SIGNING_OUTPUT,
                data={
                    "purpose": "uefi",
                    "fingerprint": "0" * 64,
                    "results": [
                        {
                            "file": "hello/boot/vmlinuz",
                            "output_file": "hello/boot/vmlinuz.sig",
                        }
                    ],
                },
                files={"hello/boot/vmlinuz.sig": create_file_response()},
            ),
        }
        artifact_files = {
            (11, "hello-signed_1.0+1_amd64.deb"): template_deb_path,
            (12, "hello/boot/vmlinuz.sig"): signed_vmlinuz_path,
        }
        artifact_categories = {
            3: ArtifactCategory.BINARY_PACKAGE,
            4: ArtifactCategory.SIGNING_INPUT,
        }
        relations = {4: [(3, "relates-to")], 12: [(4, "relates-to")]}
        uploaded_artifact_ids = {
            ArtifactCategory.SOURCE_PACKAGE: 20,
            ArtifactCategory.UPLOAD: 21,
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS: 22,
        }
        signed_tarball_path = directory / "hello-signed_1.0+1.tar.xz"

        def download_artifact(
            artifact_id: int, destination: Path, tarball: bool = False
        ) -> ArtifactResponse:
            assert not tarball
            for file_name in artifacts[artifact_id].files:
                target = destination / file_name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact_files[(artifact_id, file_name)], target)
            return artifacts[artifact_id]

        def relation_list(artifact_id: int) -> RelationsResponse:
            return RelationsResponse.parse_obj(
                [
                    {
                        "id": 1,
                        "artifact": artifact_id,
                        "target": target_id,
                        "type": relation_type,
                    }
                    for target_id, relation_type in relations[artifact_id]
                ]
            )

        def artifact_get(artifact_id: int) -> ArtifactResponse:
            return create_artifact_response(
                id=6, category=artifact_categories[artifact_id]
            )

        def upload_artifact(
            local_artifact: LocalArtifact[Any], **kwargs: Any
        ) -> RemoteArtifact:
            if local_artifact.category == ArtifactCategory.SOURCE_PACKAGE:
                self.assertCountEqual(
                    local_artifact.files.keys(),
                    ["hello-signed_1.0+1.dsc", "hello-signed_1.0+1.tar.xz"],
                )
                shutil.copy(
                    local_artifact.files["hello-signed_1.0+1.tar.xz"],
                    signed_tarball_path,
                )
            return create_remote_artifact(
                id=uploaded_artifact_ids[local_artifact.category],
                workspace="testing",
            )

        debusine_mock.download_artifact.side_effect = download_artifact
        debusine_mock.relation_list.side_effect = relation_list
        debusine_mock.artifact_get.side_effect = artifact_get
        debusine_mock.upload_artifact.side_effect = upload_artifact

        def run(
            args: list[str],
            run_as_root: bool = False,  # noqa: U100
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[AnyStr]:
            if args[0] == "apt-get":
                return subprocess.CompletedProcess(args, 0)
            else:
                kwargs.setdefault("stdout", subprocess.PIPE)
                kwargs.setdefault("stderr", subprocess.PIPE)
                return subprocess.run(args, **kwargs)

        with (
            mock.patch.object(
                self.task, "_prepare_executor_instance", autospec=True
            ),
            mock.patch.object(
                self.task, "executor_instance", autospec=True
            ) as mock_executor_instance,
        ):
            mock_executor_instance.file_pull.side_effect = shutil.copy2
            mock_executor_instance.file_push.side_effect = shutil.copy2
            mock_executor_instance.run.side_effect = run
            self.assertTrue(self.task.execute())

        self.assertEqual(debusine_mock.upload_artifact.call_count, 3)
        with tarfile.open(signed_tarball_path) as tar:
            vmlinuz_sig_file = tar.extractfile(
                "source/debian/signatures/hello/boot/vmlinuz.sig"
            )
            assert vmlinuz_sig_file is not None
            self.assertEqual(vmlinuz_sig_file.read(), b"signed vmlinuz\n")

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "assemble signed source")
