# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the mergeuploads task support on the worker."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from unittest import mock
from unittest.mock import call

from debian import deb822
from debusine.artifacts import Upload
from debusine.artifacts.models import (
    ArtifactCategory,
    DebianUpload,
    EmptyArtifactData,
)
from debusine.artifacts.playground import ArtifactPlayground
from debusine.client.models import LookupResultType, LookupSingleResponse
from debusine.tasks import MergeUploads, TaskConfigError
from debusine.tasks.mergeuploads import MergeUploadsError
from debusine.tasks.models import LookupMultiple, MergeUploadsDynamicData
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_remote_artifact,
)


class MergeUploadsTests(ExternalTaskHelperMixin[MergeUploads], TestCase):
    """Test the MergeUploads class."""

    SAMPLE_TASK_DATA = {"input": {"uploads": [421, 666]}}

    def setUp(self) -> None:
        super().setUp()
        self.configure_task()

    def tearDown(self) -> None:
        """Delete directory to avoid ResourceWarning with python -m unittest."""
        if self.task._debug_log_files_directory is not None:
            self.task._debug_log_files_directory.cleanup()
        super().tearDown()

    @staticmethod
    def _make_files_field(paths: Iterable[Path]) -> list[dict[str, str]]:
        """Make a valid ``Files`` field."""
        return [
            {
                "md5sum": hashlib.md5(path.read_bytes()).hexdigest(),
                "name": path.name,
                "priority": "optional",
                "section": "unknown",
                "size": str(path.stat().st_size),
            }
            for path in paths
        ]

    @staticmethod
    def _make_checksums_field(
        hash_name: str, paths: Iterable[Path]
    ) -> list[dict[str, str]]:
        """Make a valid ``Checksums-*`` field."""
        return [
            {
                "name": path.name,
                hash_name: hashlib.new(
                    hash_name, path.read_bytes()
                ).hexdigest(),
                "size": str(path.stat().st_size),
            }
            for path in paths
        ]

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        binary_artifacts_lookup = LookupMultiple.parse_obj([421, 666])
        task_db = FakeTaskDatabase(
            multiple_lookups={
                # input.uploads
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Files": [{"name": "hello_1.0_amd64.deb"}],
                                "Source": "hello",
                            },
                        ),
                    ),
                    ArtifactInfo(
                        id=666,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "s390x",
                                "Files": [{"name": "hello_1.0_s390x.deb"}],
                                "Source": "hello",
                            },
                        ),
                    ),
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            MergeUploadsDynamicData(
                input_uploads_ids=[421, 666], subject="hello"
            ),
        )

    def test_compute_dynamic_data_subject_is_none(self) -> None:
        """Uploading packages with different source package names."""
        binary_artifacts_lookup = LookupMultiple.parse_obj([421, 666])
        task_db = FakeTaskDatabase(
            multiple_lookups={
                # input.uploads
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "amd64",
                                "Files": [{"name": "hello_1.0_amd64.deb"}],
                                "Source": "hello",
                            },
                        ),
                    ),
                    ArtifactInfo(
                        id=666,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "s390x",
                                "Files": [{"name": "hello_1.0_s390x.deb"}],
                                "Source": "python",
                            },
                        ),
                    ),
                ]
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            MergeUploadsDynamicData(input_uploads_ids=[421, 666], subject=None),
        )

    def test_compute_dynamic_raise_task_error_wrong_category(self) -> None:
        binary_artifacts_lookup = LookupMultiple.parse_obj([421, 666])
        task_db = FakeTaskDatabase(
            multiple_lookups={
                # input.uploads
                (binary_artifacts_lookup, None): [
                    ArtifactInfo(
                        id=421,
                        category=ArtifactCategory.SYSTEM_TARBALL,
                        data=EmptyArtifactData(),
                    ),
                    ArtifactInfo(
                        id=666,
                        category=ArtifactCategory.UPLOAD,
                        data=DebianUpload(
                            type="dpkg",
                            changes_fields={
                                "Architecture": "s390x",
                                "Files": [{"name": "hello_1.0_s390x.deb"}],
                                "Source": "python",
                            },
                        ),
                    ),
                ]
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^input.uploads: unexpected artifact category: "
            r"'debian:system-tarball'. Valid categories: \['debian:upload'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_get_input_artifacts_ids(self) -> None:
        """Test get_input_artifacts_ids."""
        self.assertEqual(self.task.get_input_artifacts_ids(), [])

        self.task.dynamic_data = MergeUploadsDynamicData(
            input_uploads_ids=[2, 3],
        )
        self.assertEqual(self.task.get_input_artifacts_ids(), [2, 3])

    def test_configure_fails_with_missing_required_data(
        self,
    ) -> None:
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"input": {}})

    def test_fetch_input(self) -> None:
        """Test fetch_input: call fetch_artifact(artifact_id, directory)."""
        directory = self.create_temporary_directory()
        artifact1 = self.fake_debian_upload_artifact()
        artifact2 = self.fake_debian_upload_artifact()
        artifact2.id += 1
        self.configure_task()
        self.task.work_request_id = 5
        self.task.dynamic_data = MergeUploadsDynamicData(
            input_uploads_ids=[artifact1.id, artifact2.id]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = artifact1

        with mock.patch.object(
            self.task, "fetch_artifact", autospec=True, return_value=True
        ) as fetch_artifact_mocked:
            result = self.task.fetch_input(directory)

        self.assertTrue(result)
        fetch_artifact_mocked.assert_has_calls(
            [
                mock.call(artifact1.id, directory / f"upload-{artifact1.id}"),
                mock.call(artifact2.id, directory / f"upload-{artifact2.id}"),
            ]
        )

    def test_fetch_input_wrong_category(self) -> None:
        """Test fetch_input when input isn't a package upload."""
        directory = self.create_temporary_directory()
        artifact1 = create_artifact_response(id=12)
        artifact2 = self.fake_debian_upload_artifact()
        self.configure_task()
        self.task.work_request_id = 5
        self.task.dynamic_data = MergeUploadsDynamicData(
            input_uploads_ids=[artifact1.id, artifact2.id]
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = artifact1

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
                "input.uploads points to a Testing, not the "
                "expected debian:upload.\n"
            ),
        )

        fetch_artifact_mocked.assert_not_called()

    def test_configure_for_execution_groups_changes_by_upload(self) -> None:
        directory = self.create_temporary_directory()
        changes_source = directory / "upload-9" / "foo_1.0_source.changes"
        changes_amd64 = directory / "upload-10" / "foo_1.0_amd64.changes"
        changes_i386 = directory / "upload-11" / "foo_1.0_i386.changes"
        for changes in (changes_source, changes_amd64, changes_i386):
            changes.parent.mkdir()
            changes.touch()

        self.task.configure_for_execution(directory)

        self.assertEqual(
            self.task._changes_paths,
            [changes_source, changes_amd64, changes_i386],
        )

    def test_merge_changes_unknown_format(self) -> None:
        """Only ``Format: 1.8`` is supported."""
        execute_directory = self.create_temporary_directory()
        with self.assertRaisesRegex(
            MergeUploadsError, "Unknown .changes format: 1.7"
        ):
            self.task.merge_changes(
                execute_directory, [deb822.Changes({"Format": "1.7"})]
            )

    def test_merge_changes_mismatched_simple_fields(self) -> None:
        """All ``Format``, ``Source``, and ``Version`` fields must match."""
        for field in ("Format", "Source", "Version"):
            with self.subTest(field=field):
                execute_directory = self.create_temporary_directory()
                all_changes = [
                    deb822.Changes(
                        {"Format": "1.8", "Source": "hello", "Version": "1.0"}
                    )
                    for _ in range(2)
                ]
                all_changes[1][field] = "mismatch"
                with self.assertRaisesRegex(
                    MergeUploadsError,
                    fr"{field} fields do not match: "
                    fr"\[{all_changes[0][field]!r}, 'mismatch'\]",
                ):
                    self.task.merge_changes(execute_directory, all_changes)

    def test_merge_changes_mismatched_descriptions(self) -> None:
        """The descriptions for each binary package must match."""
        execute_directory = self.create_temporary_directory()
        with self.assertRaisesRegex(
            MergeUploadsError,
            "Descriptions for mismatch do not match: 'left' != 'right'",
        ):
            self.task.merge_changes(
                execute_directory,
                [
                    deb822.Changes(
                        {
                            "Format": "1.8",
                            "Source": "hello",
                            "Version": "1.0",
                            "Description": (
                                "\n"
                                " unrecognized junk\n"
                                " match - matching description\n"
                                " unique-left - only in left\n"
                                " mismatch - left"
                            ),
                        }
                    ),
                    deb822.Changes(
                        {
                            "Format": "1.8",
                            "Source": "hello",
                            "Version": "1.0",
                            "Description": (
                                "\n"
                                " unrecognized junk\n"
                                " match - matching description\n"
                                " unique-right - only in right\n"
                                " mismatch - right"
                            ),
                        }
                    ),
                ],
            )

    def test_merge_changes_mismatched_files(self) -> None:
        """The entries for a given file in ``Files`` must match."""
        execute_directory = self.create_temporary_directory()
        with self.assertRaisesRegex(
            MergeUploadsError,
            r"Entries in Files for mismatch_1\.0_all.deb do not match: "
            r"\{'md5sum': '0002', 'size': '24', 'section': 'misc', "
            r"'priority': 'optional', 'name': 'mismatch_1\.0_all.deb'\} != "
            r"\{'md5sum': '0003', 'size': '32', 'section': 'misc', "
            r"'priority': 'optional', 'name': 'mismatch_1\.0_all.deb'\}",
        ):
            self.task.merge_changes(
                execute_directory,
                [
                    deb822.Changes(
                        {
                            "Format": "1.8",
                            "Source": "hello",
                            "Version": "1.0",
                            "Files": (
                                "\n"
                                " 0000 16 misc optional archdep_1.0_amd64.deb\n"
                                " 0001 20 misc optional match_1.0_all.deb\n"
                                " 0002 24 misc optional mismatch_1.0_all.deb"
                            ),
                        }
                    ),
                    deb822.Changes(
                        {
                            "Format": "1.8",
                            "Source": "hello",
                            "Version": "1.0",
                            "Files": (
                                "\n"
                                " 0000 16 misc optional archdep_1.0_amd64.deb\n"
                                " 0001 20 misc optional match_1.0_all.deb\n"
                                " 0003 32 misc optional mismatch_1.0_all.deb"
                            ),
                        }
                    ),
                ],
            )

    def test_merge_changes_mismatched_checksums(self) -> None:
        """The entries for a given file in ``Checksums-*`` must match."""
        for field, checksum in (
            ("Checksums-Sha1", "sha1"),
            ("Checksums-Sha256", "sha256"),
        ):
            with self.subTest(field=field):
                execute_directory = self.create_temporary_directory()
                with self.assertRaisesRegex(
                    MergeUploadsError,
                    fr"Entries in {field} for mismatch_1\.0_all.deb do not "
                    fr"match: "
                    fr"\{{'{checksum}': '0002', 'size': '24', "
                    fr"'name': 'mismatch_1\.0_all.deb'\}} != "
                    fr"\{{'{checksum}': '0003', 'size': '32', "
                    fr"'name': 'mismatch_1\.0_all.deb'\}}",
                ):
                    self.task.merge_changes(
                        execute_directory,
                        [
                            deb822.Changes(
                                {
                                    "Format": "1.8",
                                    "Source": "hello",
                                    "Version": "1.0",
                                    field: (
                                        "\n"
                                        " 0000 16 archdep_1.0_amd64.deb\n"
                                        " 0001 20 match_1.0_all.deb\n"
                                        " 0002 24 mismatch_1.0_all.deb"
                                    ),
                                }
                            ),
                            deb822.Changes(
                                {
                                    "Format": "1.8",
                                    "Source": "hello",
                                    "Version": "1.0",
                                    field: (
                                        "\n"
                                        " 0000 16 archdep_1.0_amd64.deb\n"
                                        " 0001 20 match_1.0_all.deb\n"
                                        " 0003 32 mismatch_1.0_all.deb"
                                    ),
                                }
                            ),
                        ],
                    )

    def test_merge_changes_unsupported_checksum(self) -> None:
        """Unsupported ``Checksums-*`` fields are an error."""
        execute_directory = self.create_temporary_directory()
        with self.assertRaisesRegex(
            MergeUploadsError,
            "Unsupported checksum field: Checksums-Unsupported",
        ):
            self.task.merge_changes(
                execute_directory,
                [
                    deb822.Changes(
                        {
                            "Format": "1.8",
                            "Source": "hello",
                            "Version": "1.0",
                            "Checksums-Unsupported": "",
                        }
                    )
                ],
            )

    def test_merge_changes(self) -> None:
        """``merge_changes`` merges everything and symlinks referenced files."""
        download_directory = self.create_temporary_directory()

        (upload_source := download_directory / "upload-1").mkdir()
        f_dsc = upload_source / "hello_1.0.dsc"
        f_dsc.write_bytes(f_dsc.name.encode())
        f_tar = upload_source / "hello_1.0.tar.xz"
        f_tar.write_bytes(f_tar.name.encode())
        f_changes_source = upload_source / "hello_1.0_source.changes"
        ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            f_changes_source,
            [f_dsc, f_tar],
            source="hello",
            version="1.0",
        )

        (upload_all := download_directory / "upload-2").mkdir()
        f_deb = upload_all / "hello_1.0_all.deb"
        f_deb.write_bytes(f_deb.name.encode())
        f_changes_all = upload_all / "hello_1.0_all.changes"
        changes_all = ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            f_changes_all,
            [f_deb],
            source="hello",
            binaries=["hello"],
            version="1.0",
        )

        self.task._changes_paths = [f_changes_source, f_changes_all]
        execute_directory = self.create_temporary_directory()

        merged_changes_path = self.task.merge_changes(
            execute_directory,
            [
                deb822.Changes(f_changes_source.read_bytes()),
                deb822.Changes(f_changes_all.read_bytes()),
            ],
        )

        self.assertEqual(
            deb822.Changes(merged_changes_path.read_bytes()),
            {
                "Architecture": "all source",
                "Binary": "hello",
                "Changes": changes_all["Changes"],
                "Checksums-Sha1": self._make_checksums_field(
                    "sha1", [f_dsc, f_tar, f_deb]
                ),
                "Checksums-Sha256": self._make_checksums_field(
                    "sha256", [f_dsc, f_tar, f_deb]
                ),
                "Date": changes_all["Date"],
                "Description": "\n hello - A Description",
                "Distribution": changes_all["Distribution"],
                "Files": self._make_files_field([f_dsc, f_tar, f_deb]),
                "Format": "1.8",
                "Maintainer": changes_all["Maintainer"],
                "Source": "hello",
                "Urgency": changes_all["Urgency"],
                "Version": "1.0",
            },
        )
        for target in (f_dsc, f_tar, f_deb):
            link = merged_changes_path.with_name(target.name)
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.readlink(), target)

    def test_merge_descriptions_left_only(self) -> None:
        """Merge ``Description`` where it is only on the left side."""
        merged = deb822.Changes({"Description": "\n foo - package foo"})
        to_merge = deb822.Changes()

        self.task._merge_descriptions(merged, to_merge)

        self.assertEqual(merged["Description"], "\n foo - package foo")

    def test_merge_descriptions_right_only(self) -> None:
        """Merge ``Description`` where it is only on the right side."""
        merged = deb822.Changes()
        to_merge = deb822.Changes({"Description": "\n foo - package foo"})

        self.task._merge_descriptions(merged, to_merge)

        self.assertEqual(merged["Description"], "\n foo - package foo")

    def test_merge_descriptions_both(self) -> None:
        """Merge ``Description`` where it is on both sides."""
        merged = deb822.Changes(
            {"Description": ("\n foo - package foo\n bar - package bar")}
        )
        to_merge = deb822.Changes(
            {
                "Description": (
                    "\n"
                    " bar - package bar\n"
                    " baz - package baz\n"
                    " quux - package quux"
                )
            }
        )

        self.task._merge_descriptions(merged, to_merge)

        self.assertEqual(
            merged["Description"],
            (
                "\n"
                " foo - package foo\n"
                " bar - package bar\n"
                " baz - package baz\n"
                " quux - package quux"
            ),
        )

    def test_execute(self) -> None:
        """Test full (mocked) execution."""
        self.task.work_request_id = 2
        self.task.workspace_name = "testing"
        self.task.dynamic_data = MergeUploadsDynamicData(
            input_uploads_ids=[1, 2]
        )
        download_directory = self.create_temporary_directory()

        debusine_mock = self.mock_debusine()
        debusine_mock.lookup_single.return_value = LookupSingleResponse(
            result_type=LookupResultType.ARTIFACT, artifact=1
        )
        debusine_mock.download_artifact.return_value = True
        debusine_mock.upload_artifact.return_value = create_remote_artifact(
            id=2, workspace=self.task.workspace_name
        )

        (upload_all := download_directory / "upload-1").mkdir()
        f_deb_all = upload_all / "meritous-data_1.5-1.1_all.deb"
        f_deb_all.write_bytes(f_deb_all.name.encode())
        f_changes_all = upload_all / "meritous_1.5-1.1_all.changes"
        changes_all = ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            f_changes_all,
            [f_deb_all],
            source="meritous",
            binaries=["meritous-data"],
            version="1.5-1.1",
        )

        (upload_amd64 := download_directory / "upload-2").mkdir()
        f_deb_lib = upload_amd64 / "libmeritous1_1.5-1.1_amd64.deb"
        f_deb_lib.write_bytes(f_deb_lib.name.encode())
        f_deb_prog = upload_amd64 / "meritous_1.5-1.1_amd64.deb"
        f_deb_prog.write_bytes(f_deb_prog.name.encode())
        f_changes_amd64 = upload_amd64 / "meritous_1.5-1.1_amd64.changes"
        ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            f_changes_amd64,
            [f_deb_lib, f_deb_prog],
            source="meritous",
            binaries=["libmeritous1", "meritous"],
            version="1.5-1.1",
        )

        self.assertTrue(self.task.configure_for_execution(download_directory))
        self.assertEqual(
            self.task._changes_paths, [f_changes_all, f_changes_amd64]
        )

        execute_directory = self.create_temporary_directory()
        self.assertTrue(self.task.run(execute_directory))
        assert self.task._upload_artifact is not None
        self.task.upload_artifacts(execute_directory, execution_success=True)

        self.assertEqual(
            self.task._upload_artifact.data,
            DebianUpload(
                type="dpkg",
                changes_fields={
                    "Architecture": "all amd64",
                    "Binary": "libmeritous1 meritous meritous-data",
                    "Changes": changes_all["Changes"],
                    "Checksums-Sha1": self._make_checksums_field(
                        "sha1", [f_deb_all, f_deb_lib, f_deb_prog]
                    ),
                    "Checksums-Sha256": self._make_checksums_field(
                        "sha256", [f_deb_all, f_deb_lib, f_deb_prog]
                    ),
                    "Date": changes_all["Date"],
                    "Description": (
                        "\n"
                        " meritous-data - A Description\n"
                        " libmeritous1 - A Description\n"
                        " meritous - A Description"
                    ),
                    "Distribution": changes_all["Distribution"],
                    "Files": self._make_files_field(
                        [f_deb_all, f_deb_lib, f_deb_prog]
                    ),
                    "Format": "1.8",
                    "Maintainer": changes_all["Maintainer"],
                    "Source": "meritous",
                    "Urgency": changes_all["Urgency"],
                    "Version": "1.5-1.1",
                },
            ),
        )
        debusine_mock.upload_artifact.assert_called_once_with(
            Upload.create(
                changes_file=(
                    execute_directory
                    / "merged"
                    / "meritous_1.5-1.1_multi.changes"
                )
            ),
            workspace=self.task.workspace_name,
            work_request=self.task.work_request_id,
        )
        debusine_mock.relation_create.assert_has_calls(
            [
                mock.call(2, 1, "extends"),
                mock.call(2, 2, "extends"),
            ]
        )

    def test_upload_artifacts(self) -> None:
        """upload_artifact() and relation_create() is called."""
        self.task.dynamic_data = MergeUploadsDynamicData(
            input_uploads_ids=[1, 2]
        )
        download_directory = self.create_temporary_directory()

        # Create file that will be attached when uploading the artifacts
        f_in_contents = "Format: 1.8"
        f_in1 = download_directory / "meritous_1.5-1.1_amd64.changes"
        f_in1.write_text(f_in_contents)
        f_in2 = download_directory / "meritous_1.5-1.1_all.changes"
        f_in2.write_text(f_in_contents)
        f_deb = download_directory / "meritous-data_1.5-1.1_all.deb"
        f_deb.touch()
        self.task._changes_paths = [f_in2, f_in1]
        f_merged = self.create_temporary_file()
        self.write_changes_file(f_merged, [f_deb])
        merged = deb822.Changes(f_merged.read_text())
        merged_changes_path = (
            download_directory / "meritous_1.5-1.1_multi.changes"
        )
        with open(merged_changes_path, "w") as f:
            merged.dump(f, text_mode=True)
        self.task._upload_artifact = self.task.make_upload_artifact(
            merged_changes_path
        )

        # Debusine.upload_artifact is mocked to verify the call only
        debusine_mock = self.mock_debusine()

        workspace_name = "testing"

        uploaded_artifacts = [
            create_remote_artifact(id=10, workspace=workspace_name),
        ]

        debusine_mock.upload_artifact.side_effect = uploaded_artifacts

        # self.task.workspace_name is set by the Worker
        # and is the workspace that downloads the artifact
        # containing the files needed for MergeUploads
        self.task.workspace_name = workspace_name

        # The worker set self.task.work_request_id of the task
        work_request_id = 147
        self.task.work_request_id = work_request_id

        execute_directory = self.create_temporary_directory()
        self.task.upload_artifacts(execute_directory, execution_success=True)

        # Assert that the artifacts were uploaded and relations created
        debusine_mock.upload_artifact.assert_called_once_with(
            self.task._upload_artifact,
            workspace=workspace_name,
            work_request=work_request_id,
        )

        # Debusine mock relation_create expected calls
        debusine_mock.relation_create.assert_has_calls(
            [
                call(uploaded_artifacts[0].id, 1, "extends"),
                call(uploaded_artifacts[0].id, 2, "extends"),
            ]
        )

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "merge package uploads")
