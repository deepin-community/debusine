# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for classes in local_artifacts.py."""

import json
import re
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

import debian.deb822 as deb822

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

import debusine.artifacts.models as data_models
from debusine.artifacts import (
    BinaryPackage,
    BinaryPackages,
    BlhcArtifact,
    DebDiffArtifact,
    LintianArtifact,
    LocalArtifact,
    PackageBuildLog,
    RepositoryIndex,
    SigningInputArtifact,
    SigningOutputArtifact,
    SourcePackage,
    Upload,
    WorkRequestDebugLogs,
)
from debusine.artifacts.local_artifact import (
    AutopkgtestArtifact,
    DebianSystemImageArtifact,
    DebianSystemTarballArtifact,
    deb822dict_to_dict,
)
from debusine.assets import KeyPurpose
from debusine.client.debusine import serialize_local_artifact
from debusine.client.models import FileRequest, StrMaxLength255
from debusine.test import TestCase


def unregister_local_artifact_subclass(
    category: data_models.ArtifactCategory,
) -> None:
    """
    Unregister an artifact category.

    Note that the only way to register a category is to define a new subclass.
    """
    del LocalArtifact._local_artifacts_category_to_class[category]


class LocalArtifactTests(TestCase):
    """Tests for LocalArtifact class."""

    def setUp(self) -> None:
        """Set up test."""  # noqa: D202
        super().setUp()

        # Register a custom artifact category, taking care that it doesn't
        # pollute other tests.
        class TestArtifact(LocalArtifact[data_models.EmptyArtifactData]):
            """Custom artifact for testing."""

            _category = data_models.ArtifactCategory.TEST

        self.addCleanup(
            unregister_local_artifact_subclass, TestArtifact._category
        )

        self.category = TestArtifact._category
        self.data = data_models.EmptyArtifactData()
        self.files: dict[str, Path] = {}

        self.local_artifact = TestArtifact(
            category=self.category,
            files=self.files,
            data=self.data,
        )

    def test_add_local_file_in_root(self) -> None:
        """add_local_file() add the file in .files."""
        file = self.create_temporary_file()
        self.local_artifact.add_local_file(file)

        self.assertEqual(
            self.local_artifact.files,
            {file.name: file},
        )

    def test_add_local_file_in_override_name(self) -> None:
        """add_local_file() with override_name."""
        file = self.create_temporary_file()

        name = "new_name.txt"
        self.local_artifact.add_local_file(file, override_name=name)

        self.assertEqual(self.local_artifact.files, {name: file})

    def test_add_local_file_artifact_base_dir_does_not_exist(self) -> None:
        """add_local_file() raise ValueError: directory does not exist."""
        file = self.create_temporary_file()
        artifact_base_dir = Path("/does/not/exist")
        with self.assertRaisesRegex(
            ValueError,
            f'"{artifact_base_dir}" does not exist or is not a directory',
        ):
            self.local_artifact.add_local_file(
                file, artifact_base_dir=artifact_base_dir
            )

    def test_add_local_file_relative_to_artifact_base_dir_does_not_exist(
        self,
    ) -> None:
        """add_local_file() raise ValueError: filename does not exist."""
        artifact_base_dir = self.create_temporary_directory()
        filename = Path("README.txt")

        filename_absolute = artifact_base_dir / filename

        with self.assertRaisesRegex(
            ValueError, f'"{filename_absolute}" does not exist'
        ):
            self.local_artifact.add_local_file(
                filename, artifact_base_dir=artifact_base_dir
            )

    def test_add_local_file_absolute_does_not_exist(self) -> None:
        """add_local_file() raise ValueError: filename does not exist."""
        empty_dir = self.create_temporary_directory()

        filename = empty_dir / "a-file-does-not-exist.txt"

        with self.assertRaisesRegex(ValueError, f'"{filename}" does not exist'):
            self.local_artifact.add_local_file(filename)

    def test_add_local_file_is_not_a_file(self) -> None:
        """add_local_file() raise ValueError: expected file is a directory."""
        empty_dir = self.create_temporary_directory()

        with self.assertRaisesRegex(ValueError, f'"{empty_dir}" is not a file'):
            self.local_artifact.add_local_file(empty_dir)

    def test_add_local_file_relative_to_artifact_base_dir(self) -> None:
        """add_local_file() add relative file from an absolute directory."""
        artifact_base_dir = self.create_temporary_directory()
        filename = Path("README.txt")

        (file := artifact_base_dir / filename).write_bytes(b"")

        self.local_artifact.add_local_file(
            filename, artifact_base_dir=artifact_base_dir
        )

        self.assertEqual(self.local_artifact.files, {'README.txt': file})

    def test_add_local_file_artifact_raise_value_error_dir_is_relative(
        self,
    ) -> None:
        """add_local_file() raise ValueError if base_dir is relative."""
        directory = Path("does-not-exist")
        file = directory / "README.txt"
        with self.assertRaisesRegex(
            ValueError, f'"{directory}" must be absolute'
        ):
            self.local_artifact.add_local_file(
                file, artifact_base_dir=directory
            )

    def test_add_local_file_absolute_path_no_artifact_base_dir(self) -> None:
        """add_local_file() adds file. No artifact path or artifact_dir."""
        file = self.create_temporary_file()

        self.local_artifact.add_local_file(file)

        self.assertEqual(
            self.local_artifact.files,
            {file.name: file},
        )

    def test_add_local_file_duplicated_raise_value_error(self) -> None:
        """add_local_file() raise ValueError: file is already added."""
        file = self.create_temporary_file()
        self.local_artifact.add_local_file(file)

        with self.assertRaisesRegex(
            ValueError,
            r"^"
            + re.escape(
                f"File with the same path ({file.name}) "
                f'is already in the artifact ("{file}" and "{file}")'
            ),
        ):
            self.local_artifact.add_local_file(file)

    def test_add_local_file_already_remote(self) -> None:
        """add_local_file() raise ValueError: remote file is already added."""
        file = self.create_temporary_file()
        file_request = FileRequest.create_from(file)
        self.local_artifact.add_remote_file(file.name, file_request)

        with self.assertRaisesRegex(
            ValueError,
            "^"
            + re.escape(
                f"File with the same path ({file.name}) "
                f'is already in the artifact ("{file_request}" and "{file}")'
            ),
        ):
            self.local_artifact.add_local_file(file)

    def test_add_remote_file(self) -> None:
        """add_remote_file() adds a file that does not exist locally."""
        file_request = FileRequest(
            size=1,
            checksums={
                "sha256": pydantic.parse_obj_as(StrMaxLength255, "0" * 64)
            },
            type="file",
        )
        self.local_artifact.add_remote_file("path", file_request)

        self.assertEqual(
            self.local_artifact.remote_files, {"path": file_request}
        )

    def test_add_remote_file_already_local(self) -> None:
        """add_remote_file() raise ValueError: local file is already added."""
        file = self.create_temporary_file()
        self.local_artifact.add_local_file(file)
        file_request = FileRequest.create_from(file)

        with self.assertRaisesRegex(
            ValueError,
            r"^"
            + re.escape(
                f"File with the same path ({file.name}) "
                f'is already in the artifact ("{file}" and "{file_request}")'
            ),
        ):
            self.local_artifact.add_remote_file(file.name, file_request)

    def test_validate_category(self) -> None:
        """Creating a new artifact validates its category."""
        self.assertRaisesRegex(
            ValueError,
            r"Invalid category: 'nonexistent'",
            WorkRequestDebugLogs,
            category="nonexistent",
            files=self.files,
            data=self.data,
        )

    def test_validate_files_length(self) -> None:
        """_validate_files_length return the files."""
        files = {"package1.deb": Path(), "package2.deb": Path()}
        self.assertEqual(
            LocalArtifact._validate_files_length(files, len(files)), files
        )

    def test_validate_files_length_raise_error(self) -> None:
        """_validate_files_length raise ValueError."""
        files: dict[str, Path] = {}
        expected = 1
        with self.assertRaisesRegex(
            ValueError, f"Expected number of files: {expected} Actual: 0"
        ):
            LocalArtifact._validate_files_length(files, expected)


class PackageBuildLogTests(TestCase):
    """Tests for PackageBuildLog."""

    def setUp(self) -> None:
        """Set up for the tests."""
        super().setUp()
        self.file = FileRequest(
            size=10,
            checksums={
                "sha256": pydantic.parse_obj_as(StrMaxLength255, "hash")
            },
            type="file",
        )

        self.directory = self.create_temporary_directory()

        self.build_filename = "log.build"
        self.build_file = self.directory / self.build_filename
        self.build_file.write_text("A line of log")

    def test_create(self) -> None:
        """create() method return the expected PackageBuildLog."""
        source = "hello"
        version = "2.10-2"
        architecture = "amd64"

        artifact = PackageBuildLog.create(
            file=self.directory / "log.build",
            source=source,
            version=version,
            architecture=architecture,
        )

        expected_data = data_models.DebianPackageBuildLog(
            source=source,
            version=version,
            architecture=architecture,
            filename="log.build",
        )
        expected_files = {self.build_filename: self.build_file}
        expected_content_types = {
            self.build_filename: "text/plain; charset=utf-8"
        }

        expected = PackageBuildLog(
            category=PackageBuildLog._category,
            files=expected_files,
            content_types=expected_content_types,
            data=expected_data,
        )
        self.assertEqual(artifact, expected)
        self.assertIsInstance(artifact, PackageBuildLog)

    def test_validate_files_must_be_one_raise(self) -> None:
        """Raise ValueError: unexpected number of files."""
        with self.assertRaisesRegex(
            ValueError, "^Expected number of files: 1 Actual: 0$"
        ):
            PackageBuildLog.validate_files_length_is_one({})

    def test_validate_files_number_success(self) -> None:
        """files_number_must_be_one() return files."""
        files = {"log.build": self.file}

        self.assertEqual(
            PackageBuildLog.validate_files_length_is_one(files),
            files,
        )

    def test_validate_file_ends_with_build(self) -> None:
        """File ends with .build: files_must_end_in_build return files."""
        files = {"log.build": self.file}

        self.assertEqual(PackageBuildLog.file_must_end_in_build(files), files)

    def test_validate_file_ends_with_build_raise_value_error(self) -> None:
        """Raise ValueError: file does not end in .build."""
        file = self.create_temporary_file(suffix=".txt")

        expected_message = (
            fr"""^Valid file suffixes: \['\.build'\]. """
            fr"""Invalid filename: "{file.name}"$"""
        )

        with self.assertRaisesRegex(ValueError, expected_message):
            PackageBuildLog.file_must_end_in_build({file.name: file})

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        artifact = PackageBuildLog.create(
            file=self.build_file,
            source="hello",
            version="2.10-2",
            architecture="amd64",
        )

        # No exception is raised
        json.dumps(
            serialize_local_artifact(artifact, workspace="some-workspace")
        )


class UploadTests(TestCase):
    """Tests for Upload."""

    def setUp(self) -> None:
        """Set up test."""
        super().setUp()
        self.workspace = "workspace"

        self.file = FileRequest(
            size=10,
            checksums={
                "sha256": pydantic.parse_obj_as(StrMaxLength255, "hash")
            },
            type="file",
        )

    def test_create(self) -> None:
        """create() return the correct object."""
        directory = self.create_temporary_directory()

        changes_filename = "python-network.changes"
        changes_file = directory / changes_filename

        deb = self.create_temporary_file(
            suffix="_1.0_amd64.deb", directory=directory
        )
        dsc = self.create_temporary_file(suffix=".dsc", directory=directory)
        changes = self.write_changes_file(changes_file, [dsc, deb])

        artifact = Upload.create(changes_file=changes_file)

        files = {
            changes_filename: changes_file,
            dsc.name: dsc,
            deb.name: deb,
        }
        content_types = {changes_filename: "text/plain; charset=utf-8"}
        data = data_models.DebianUpload(changes_fields=changes, type="dpkg")

        expected = Upload(
            category=Upload._category,
            files=files,
            content_types=content_types,
            data=data,
        )

        self.assertEqual(artifact, expected)

    def test_create_skip_excluded_files(self) -> None:
        """Upload.create() does not add the excluded file."""
        changes_file = self.create_temporary_file(suffix=".changes")

        extra_file = self.create_temporary_file()

        self.write_changes_file(changes_file, files=[extra_file])

        binary_upload = Upload.create(
            changes_file=changes_file, exclude_files={extra_file}
        )

        files = {changes_file.name: changes_file}

        self.assertEqual(binary_upload.files, files)

    def test_create_no_files_in_changes(self) -> None:
        """Upload.create() return a package, .changes has no files."""
        changes_file = self.create_temporary_file(suffix=".changes")

        self.write_changes_file(changes_file, files=[])

        binary_upload = Upload.create(changes_file=changes_file)

        files = {changes_file.name: changes_file}

        self.assertEqual(binary_upload.files, files)

    def test_create_allow_remote(self) -> None:
        """Upload.create() can allow referenced files to be missing locally."""
        directory = self.create_temporary_directory()
        changes_file = directory / "foo_1.0.changes"
        deb = self.create_temporary_file(
            suffix="_1.0_amd64.deb", directory=directory
        )
        dsc = self.create_temporary_file(suffix=".dsc", directory=directory)
        changes = self.write_changes_file(changes_file, [dsc, deb])
        expected = Upload(
            category=Upload._category,
            files={changes_file.name: changes_file, dsc.name: dsc},
            remote_files={deb.name: FileRequest.create_from(deb)},
            content_types={changes_file.name: "text/plain; charset=utf-8"},
            data=data_models.DebianUpload(changes_fields=changes, type="dpkg"),
        )
        deb.unlink()

        artifact = Upload.create(changes_file=changes_file, allow_remote=True)

        self.assertEqual(artifact, expected)

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        directory = self.create_temporary_directory()
        output_file = self.create_temporary_file(
            suffix=".deb", directory=directory
        )
        changes = directory / "package.changes"
        self.write_changes_file(changes, files=[output_file])
        artifact = Upload.create(changes_file=changes)

        json.dumps(
            serialize_local_artifact(artifact, workspace="some workspace")
        )

    def test_files_no_contain_changes_raise_value_error(self) -> None:
        """Raise ValueError: missing expected .changes file."""
        file = self.create_temporary_file()

        files = {file.name: file}

        expected_message = fr"Expecting 1 \.changes file in \['{file.name}'\]"
        with self.assertRaisesRegex(ValueError, expected_message):
            Upload.files_contain_changes({"files": files})

    def test_files_contain_2_changes_raise_value_error(self) -> None:
        """Raise ValueError: missing expected .changes file."""
        file1 = self.create_temporary_file(suffix=".changes")
        file2 = self.create_temporary_file(suffix=".changes")

        files = {
            file1.name: file1,
            file2.name: file2,
        }

        expected_message = r"Expecting 1 \.changes file in \["
        with self.assertRaisesRegex(ValueError, expected_message):
            Upload.files_contain_changes({"files": files})

    def test_files_contain_changes_return_files(self) -> None:
        """Return files: .changes found."""
        changes_file = self.create_temporary_file(suffix=".changes")

        self.write_changes_file(changes_file, files=[changes_file])

        files = {changes_file.name: changes_file}

        self.assertEqual(
            Upload.files_contain_changes({"files": files}), {"files": files}
        )

    def test_files_contains_files_in_changes_call_implementation(self) -> None:
        """Test files_contains_files_in_dsc call the utils method."""
        files = {"a": Path("b")}
        patcher = mock.patch(
            "debusine.artifacts.local_artifact.files_in_meta_file_match_files"
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        self.assertEqual(
            Upload.files_contains_files_in_changes({"files": files}),
            {"files": files},
        )

        mocked.assert_called_with(
            ".changes",
            deb822.Changes,
            files,
        )


class Deb822DictToDictTests(TestCase):
    """Tests for deb822dict_to_dict function."""

    def assert_deb822dict_return_correct_value(
        self, value: Any, expected: Any
    ) -> None:
        """Use deb822dict_to_dict(value) and check compare with expected."""
        actual = deb822dict_to_dict(value)

        self.assertEqual(actual, expected)
        json.dumps(actual)

    def test_dict(self) -> None:
        """Python dictionary is returned as it is."""
        to_convert = {"a": "b"}
        expected = {"a": "b"}
        self.assert_deb822dict_return_correct_value(to_convert, expected)

    def test_deb822_dict(self) -> None:
        """Deb822Dict is converted to a Python dict."""
        to_convert = deb822.Deb822Dict({"a": "b"})
        expected = {"a": "b"}
        self.assert_deb822dict_return_correct_value(to_convert, expected)

    def test_list(self) -> None:
        """A list with a Deb822Dict is returned as expected."""
        to_convert = ["key", deb822.Deb822Dict({"a": "b"})]
        expected = ["key", {"a": "b"}]
        self.assert_deb822dict_return_correct_value(to_convert, expected)

    def test_str(self) -> None:
        """A string is returned as it is."""
        to_convert = deb822dict_to_dict("all")
        expected = "all"
        self.assert_deb822dict_return_correct_value(to_convert, expected)


class BinaryPackageTests(TestCase):
    """Tests for BinaryPackage."""

    def test_create(self) -> None:
        """create() return the expected package: correct data and files."""
        srcpkg_name = "hello-traditional"
        srcpkg_version = "2.10-2"
        version = "1.1.1"
        architecture = "amd64"
        directory = self.create_temporary_directory()
        file = directory / f"hello_{version}_amd64.deb"
        self.write_deb_file(
            file,
            source_name=srcpkg_name,
            source_version=srcpkg_version,
            control_file_names=["md5sums"],
        )

        package = BinaryPackage.create(file=file)

        expected_data = data_models.DebianBinaryPackage(
            srcpkg_name=srcpkg_name,
            srcpkg_version=srcpkg_version,
            deb_fields={
                "Package": "hello",
                "Version": version,
                "Architecture": architecture,
                "Maintainer": "Example Maintainer <example@example.org>",
                "Description": "Example description",
                "Source": f"{srcpkg_name} ({srcpkg_version})",
            },
            deb_control_files=["control", "md5sums"],
        )
        expected_files = {file.name: file}

        self.assertEqual(package.category, BinaryPackage._category)
        self.assertEqual(package.data, expected_data)
        self.assertEqual(package.files, expected_files)

    def test_files_must_end_in_deb_or_udeb(self) -> None:
        """files_must_end_in_deb_or_udeb() returns the files."""
        for name in ("hello_2.10-2_amd64.deb", "hello_2.10-2_amd64.udeb"):
            self.assertEqual(
                BinaryPackage.files_must_end_in_deb_or_udeb({name: None}),
                {name: None},
            )

    def test_files_must_end_in_deb_raise_value_error(self) -> None:
        """files_must_end_in_deb_or_udeb() raises ValueError."""
        filename = "README.txt"
        files = {filename: None}
        with self.assertRaisesRegex(
            ValueError,
            rf"^Valid file suffixes: \['.deb', '.udeb'\]. "
            rf"Invalid filename: \"{filename}\"$",
        ):
            BinaryPackage.files_must_end_in_deb_or_udeb(files)

    def test_files_exactly_one(self) -> None:
        """files_exactly_one() returns the files."""
        files = {"hello.deb": None}

        self.assertEqual(BinaryPackage.files_exactly_one(files), files)

    def test_files_exactly_one_no_files(self) -> None:
        """files_exactly_one() raises ValueError: zero files."""
        with self.assertRaisesRegex(ValueError, "Must have exactly one file"):
            BinaryPackage.files_exactly_one({})

    def test_files_exactly_one_too_many_files(self) -> None:
        """files_exactly_one() raises ValueError: too many files."""
        with self.assertRaisesRegex(ValueError, "Must have exactly one file"):
            BinaryPackage.files_exactly_one(
                {"hello.deb": None, "hello2.deb": None}
            )

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        directory = self.create_temporary_directory()
        file = directory / "hello_2.10-2_amd64.deb"
        self.write_deb_file(file)
        artifact = BinaryPackage.create(file=file)

        # This is to verify that serialize_local_artifact() is not returning
        # some object that might raise an exception on json.dumps, e.g. if
        # it returned (as a value of the dictionary) a set().
        json.dumps(
            serialize_local_artifact(artifact, workspace="some workspace")
        )


class BinaryPackagesTests(TestCase):
    """Tests for BinaryPackages."""

    def test_create(self) -> None:
        """create() return the expected package: correct data and files."""
        srcpkg_name = "hello"
        srcpkg_version = "2.10-2"
        version = "1.1.1"
        architecture = "amd64"
        directory = self.create_temporary_directory()
        files = [
            directory / f"hello-traditional_{version}_amd64.deb",
            directory / f"hello-dbg_{version}_amd64.deb",
        ]
        for file in files:
            file.touch()

        package = BinaryPackages.create(
            srcpkg_name=srcpkg_name,
            srcpkg_version=srcpkg_version,
            version=version,
            architecture=architecture,
            files=files,
        )

        expected_data = data_models.DebianBinaryPackages(
            srcpkg_name=srcpkg_name,
            srcpkg_version=srcpkg_version,
            version=version,
            architecture=architecture,
            packages=["hello-traditional", "hello-dbg"],
        )
        expected_files = {
            files[0].name: files[0],
            files[1].name: files[1],
        }

        self.assertEqual(package.category, BinaryPackages._category)
        self.assertEqual(package.data, expected_data)
        self.assertEqual(package.files, expected_files)

    def test_files_must_end_in_deb_or_udeb(self) -> None:
        """files_must_end_in_deb_or_udeb() return the files."""
        files = {
            "hello_2.10-2_amd64.deb": None,
            "hello_2.10-2_amd64.udeb": None,
        }
        self.assertEqual(
            BinaryPackages.files_must_end_in_deb_or_udeb(files),
            files,
        )

    def test_files_must_end_in_deb_raise_value_error(self) -> None:
        """files_must_end_in_deb_or_udeb() raise ValueError."""
        filename = "README.txt"
        files = {filename: None}
        with self.assertRaisesRegex(
            ValueError,
            rf"^Valid file suffixes: \['.deb', '.udeb'\]. "
            rf"Invalid filename: \"{filename}\"$",
        ):
            BinaryPackages.files_must_end_in_deb_or_udeb(files)

    def test_files_more_than_zero(self) -> None:
        """files_more_than_zero() return the files."""
        files = {"hello.deb": None, "hello2.deb": None}

        self.assertEqual(BinaryPackages.files_more_than_zero(files), files)

    def test_files_more_than_zero_raise_value_error(self) -> None:
        """files_more_than_zero() raise ValueError: zero files."""
        with self.assertRaisesRegex(ValueError, "Must have at least one file"):
            BinaryPackages.files_more_than_zero({})

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        file = self.create_temporary_file(suffix=".deb")
        artifact = BinaryPackages.create(
            srcpkg_name="hello",
            srcpkg_version="2.10-2",
            version="2.10-2",
            architecture="amd64",
            files=[file],
        )

        # This is to verify that serialize_local_artifact() is not returning
        # some object that might raise an exception on json.dumps, e.g. if
        # it returned (as a value of the dictionary) a set().
        json.dumps(
            serialize_local_artifact(artifact, workspace="some workspace")
        )


class WorkRequestDebugLogsTests(TestCase):
    """Tests for WorkRequestDebugLogs class."""

    def test_create(self) -> None:
        """Test create(): return instance with expected files and category."""
        log_file_1 = self.create_temporary_file()
        log_file_2 = self.create_temporary_file()
        artifact = WorkRequestDebugLogs.create(files=[log_file_1, log_file_2])

        expected = WorkRequestDebugLogs(
            category=WorkRequestDebugLogs._category,
            data=data_models.EmptyArtifactData(),
            files={
                log_file_1.name: log_file_1,
                log_file_2.name: log_file_2,
            },
            content_types={
                log_file_1.name: "text/plain; charset=utf-8",
                log_file_2.name: "text/plain; charset=utf-8",
            },
        )

        self.assertEqual(artifact, expected)

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        log_file_1 = self.create_temporary_file()
        log_file_2 = self.create_temporary_file()
        artifact = WorkRequestDebugLogs.create(files=[log_file_1, log_file_2])

        # This is to verify that serialize_local_artifact() is not returning
        # some object that might raise an exception on json.dumps, e.g. if
        # it returned (as a value of the dictionary) a set().
        json.dumps(
            serialize_local_artifact(artifact, workspace="some workspace")
        )


class SourcePackageTests(TestCase):
    """Tests for SourcePackage class."""

    def test_create(self) -> None:
        """Test create(): return expected instance."""
        file = self.create_temporary_file()
        dsc_file = self.create_temporary_file(suffix=".dsc")

        dsc_fields = self.write_dsc_example_file(dsc_file)

        name = dsc_fields["Source"]
        version = dsc_fields["Version"]

        expected_files = {file.name: file, dsc_file.name: dsc_file}
        expected_data = data_models.DebianSourcePackage(
            name=name,
            version=version,
            type="dpkg",
            dsc_fields=dsc_fields,
        )

        package = SourcePackage.create(
            name=name, version=version, files=[file, dsc_file]
        )

        self.assertEqual(package.category, SourcePackage._category)
        self.assertEqual(package.files, expected_files)
        self.assertEqual(package.data, expected_data)

    def test_create_invalid_dsc_raise_value_error(self) -> None:
        """Raise ValueError: invalid .dsc file."""
        file = self.create_temporary_file(suffix=".dsc")

        expected_message = fr"{file.name} is not a valid \.dsc file"
        with self.assertRaisesRegex(ValueError, expected_message):
            SourcePackage.create(name="hello", version="1.0", files=[file])

    def test_files_no_contain_dsc_raise_value_error(self) -> None:
        """Raise ValueError: missing expected .dsc file."""
        file = self.create_temporary_file()

        files = {file.name: file}

        expected_message = fr"Expecting 1 \.dsc file in \['{file.name}'\]"
        with self.assertRaisesRegex(ValueError, expected_message):
            SourcePackage.files_contain_one_dsc(
                files,
            )

    def test_files_contains_dsc_return_files(self) -> None:
        """Return all files: .dsc is in the files."""
        file = self.create_temporary_file(suffix=".dsc")

        files = {file.name: file}

        self.assertEqual(
            SourcePackage.files_contain_one_dsc(
                files,
            ),
            files,
        )

    def test_files_contains_files_in_dsc_call_implementation(self) -> None:
        """Test files_contains_files_in_dsc call the utils method."""
        files = {"a": Path("b")}
        patcher = mock.patch(
            "debusine.artifacts.local_artifact.files_in_meta_file_match_files"
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        self.assertEqual(
            SourcePackage.files_contains_files_in_dsc(files), files
        )

        mocked.assert_called_with(".dsc", deb822.Dsc, files)

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        dir_ = self.create_temporary_directory()
        dsc_file = self.create_temporary_file(suffix=".dsc", directory=dir_)
        dsc_fields = self.write_dsc_example_file(dsc_file)
        name = dsc_fields["Source"]
        version = dsc_fields["Version"]
        files = [dsc_file]
        for file in dsc_fields["Files"]:
            (dir_ / file["name"]).write_bytes(b"")
            files.append(dir_ / file["name"])
        package = SourcePackage.create(name=name, version=version, files=files)

        # This is to verify that serialize_local_artifact() is not returning
        # some object that might raise an exception on json.dumps, e.g. if
        # it returned (as a value of the dictionary) a set().
        json.dumps(
            serialize_local_artifact(package, workspace="some workspace")
        )


class LintianTests(TestCase):
    """Tests for the LintianArtifact."""

    output_content: ClassVar[bytes]
    summary: ClassVar[data_models.DebianLintianSummary]
    analysis_content: ClassVar[bytes]

    @classmethod
    def setUpClass(cls) -> None:
        """Set up common data for the tests."""
        super().setUpClass()
        cls.output_content = (
            b"W: python-ping3 source: missing-license-"
            b"paragraph-in-dep5-copyright gpl-3 [debian/copyright:33]\n"
            b"P: python-ping3 source: maintainer-manual-page [debian/ping3.1]\n"
        )
        cls.summary = data_models.DebianLintianSummary(
            tags_count_by_severity={
                data_models.DebianLintianSeverity.ERROR: 1,
                data_models.DebianLintianSeverity.WARNING: 1,
            },
            package_filename={
                "binutils": "binutils_1.0.dsc",
                "cynthiune.app": "cynthiune.app_1.0_amd64.deb",
            },
            tags_found=["license-problem-convert-utf-code", "vcs-obsolete"],
            overridden_tags_found=[],
            lintian_version="1.0.0",
            distribution="bookworm",
        )
        cls.analysis_content = json.dumps(
            {
                "tags": [
                    {
                        "package": "cynthiune.app",
                        "severity": "warning",
                        "tag": "vcs-obsolete",
                        "note": "",
                        "pointer": "",
                        "explanation": "",
                        "comment": "",
                    },
                    {
                        "package": "binutils",
                        "severity": "error",
                        "tag": "license-problem-convert-utf-code",
                        "note": "Cannot ls",
                        "pointer": "src/ls.c",
                        "explanation": "",
                        "comment": "",
                    },
                ]
            }
        ).encode("utf-8")

    def test_create(self) -> None:
        """Test create(): return expected class with the files."""
        output = self.create_temporary_file(
            suffix=".txt", contents=self.output_content
        )
        analysis = self.create_temporary_file(
            suffix=".json", contents=self.analysis_content
        )

        package = LintianArtifact.create(
            lintian_output=output,
            analysis=analysis,
            architecture="i386",
            summary=self.summary,
        )

        self.assertEqual(package._category, LintianArtifact._category)
        self.assertEqual(
            package.data,
            data_models.DebianLintian(
                architecture="i386", summary=self.summary
            ),
        )
        self.assertEqual(
            package.files,
            {
                "lintian.txt": output,
                "analysis.json": analysis,
            },
        )

    def test_validate_file_analysis_is_json_valid(self) -> None:
        """Test validate_file_analysis_is_json: return files."""
        analysis = self.create_temporary_file(contents=b"{}")
        files = {"analysis.json": analysis}

        self.assertEqual(
            LintianArtifact._validate_file_analysis_is_json(files),
            files,
        )

    def test_validate_file_analysis_json_raise_value_error(self) -> None:
        """Test validate_file_analysis_is_json: raise ValueError."""
        analysis = self.create_temporary_file(contents=b":")  # invalid

        msg = "^analysis.json is not valid JSON:"
        with self.assertRaisesRegex(ValueError, msg):
            LintianArtifact._validate_file_analysis_is_json(
                {"analysis.json": analysis}
            )

    def test_validate_required_files_valid(self) -> None:
        """Test validate_required_files: is valid. return files."""
        files = {"analysis.json": "", "lintian.txt": ""}

        self.assertEqual(LintianArtifact._validate_required_files(files), files)

    def test_validate_required_files_missing_file_raise_value_error(
        self,
    ) -> None:
        """Test validate_required_files: not valid, raise ValueError."""
        msg = r"^Files required: \['analysis.json', 'lintian.txt'\]$"

        with self.assertRaisesRegex(ValueError, msg):
            LintianArtifact._validate_required_files({"summary.json": ""})

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        output = self.create_temporary_file(
            suffix=".txt", contents=self.output_content
        )
        analysis = self.create_temporary_file(
            suffix=".json", contents=self.analysis_content
        )
        artifact = LintianArtifact.create(
            lintian_output=output,
            analysis=analysis,
            architecture="source",
            summary=self.summary,
        )

        # This is to verify that serialize_local_artifact() is not returning
        # some object that might raise an exception on json.dumps, e.g. if
        # it returned (as a value of the dictionary) a set().
        json.dumps(
            serialize_local_artifact(artifact, workspace="some workspace")
        )


class AutopkgtestArtifactTests(TestCase):
    """Tests for AutopkgtestArtifact class."""

    def setUp(self) -> None:
        """Set up test."""
        super().setUp()
        self.data_contents: dict[str, Any] = {
            "results": {
                "test": {
                    "status": "PASS",
                    "details": "test details",
                },
            },
            "cmdline": "/bin/true",
            "source_package": {
                "name": "hello",
                "version": "1.0",
                "url": "https://example.org/hello.deb",
            },
            "architecture": "amd64",
            "distribution": "debian:sid",
        }

    def test_create(self) -> None:
        """Test _create()."""
        directory = self.create_temporary_directory()

        # Create files in directory and subdirectories: to be included in
        # the package
        (summary_file := directory / "summary").write_text("summary content")

        (subdir := directory / "subdir").mkdir()
        (file_in_subdir := subdir / "some-file.txt").write_text("test")

        (binaries_subdir := directory / "binaries").mkdir()

        # Files in "binaries/" are not part of the artifact
        (binaries_subdir / "log.txt").write_text("log file")
        (binaries_subdir / "pkg.deb").write_text("deb")

        package = AutopkgtestArtifact.create(
            directory, data_models.DebianAutopkgtest(**self.data_contents)
        )

        self.assertIsInstance(package, AutopkgtestArtifact)
        self.assertEqual(
            package.data, data_models.DebianAutopkgtest(**self.data_contents)
        )

        self.assertEqual(
            package.files,
            {
                "subdir/some-file.txt": file_in_subdir,
                "summary": summary_file,
            },
        )

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        directory = self.create_temporary_directory()
        (directory / "summary").write_text("summary content")

        artifact = AutopkgtestArtifact.create(
            directory, data_models.DebianAutopkgtest(**self.data_contents)
        )

        # This is to verify that serialize_local_artifact() is not returning
        # some object that might raise an exception on json.dumps, e.g. if
        # it returned (as a value of the dictionary) a set().
        json.dumps(
            serialize_local_artifact(artifact, workspace="some workspace")
        )


class DebianSystemTarballArtifactTests(TestCase):
    """Tests for DebianSystemTarballArtifactTests."""

    def test_create(self) -> None:
        """Test _create()."""
        system_tar_xz = self.create_temporary_file(
            suffix=".tar.xz", contents=b"some-file-contents"
        )

        artifact = DebianSystemTarballArtifact.create(
            system_tar_xz,
            data={
                "vendor": "debian",
                "codename": "bookworm",
                "mirror": "https://deb.debian.org",
                "components": ["main"],
                "variant": "minbase",
                "pkglist": [],
                "architecture": "amd64",
                "with_dev": True,
                "with_init": True,
            },
        )

        self.assertEqual(artifact.files, {system_tar_xz.name: system_tar_xz})
        self.assertEqual(artifact.data.filename, system_tar_xz.name)

    def test_validate_file_name_ends_in_tar_xz(self) -> None:
        """validate_file_name_ends_in_tar_xz is valid: return files."""
        system_tar_xz = self.create_temporary_file(
            suffix=".tar.xz", contents=b"some-file-contents"
        )

        files = {system_tar_xz.name: system_tar_xz}

        self.assertEqual(
            DebianSystemTarballArtifact._validate_file_name_ends_in_tar_xz(
                files
            ),
            files,
        )

    def test_validate_file_name_ends_in_tar_xz_two_files_raise(self) -> None:
        """validate_file_name_ends_in_tar_xz raise ValueError with two files."""
        file1 = self.create_temporary_file(
            suffix="temp", contents=b"some-file-contents"
        )

        file2 = self.create_temporary_file(
            suffix="temp", contents=b"some-file-contents"
        )

        files = {file1.name: file1, file2.name: file2}

        msg = "DebianSystemTarballArtifact does not contain exactly one file"
        with self.assertRaisesRegex(ValueError, msg):
            DebianSystemTarballArtifact._validate_file_name_ends_in_tar_xz(
                files
            )

    def test_validate_file_name_ends_in_tar_xz_not_valid_raise(self) -> None:
        """validate_file_name_ends_in_tar_xz raise ValueError."""
        file = self.create_temporary_file(
            suffix="temp", contents=b"some-file-contents"
        )

        files = {file.name: file}

        msg = f"Invalid file name: '{file.name}'. Expected .tar.xz"
        with self.assertRaisesRegex(ValueError, msg):
            DebianSystemTarballArtifact._validate_file_name_ends_in_tar_xz(
                files
            )


class BlhcArtifactTests(TestCase):
    """Tests for BlhcArtifact class."""

    output_content: ClassVar[bytes]

    @classmethod
    def setUpClass(cls) -> None:
        """Set up common data for the tests."""
        super().setUpClass()
        cls.output_content = (
            b"LDFLAGS missing (-fPIE -pie): /usr/bin/c++  -g -O2 -fstack-"
            b"protector-strong -Wformat -Werror=format-security -Wdate-time "
            b"-D_FORTIFY_SOURCE=2  -Wl,-z,relro printf-test.cc.o "
            b"-o ../bin/printf-test\n"
        )

    def test_create(self) -> None:
        """Test _create()."""
        output = self.create_temporary_file(
            suffix=".txt", contents=self.output_content
        )

        package = BlhcArtifact.create(blhc_output=output)

        self.assertIsInstance(package, BlhcArtifact)
        self.assertEqual(package.data, data_models.EmptyArtifactData())

        self.assertEqual(
            package.files,
            {
                "blhc.txt": output,
            },
        )

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        """upload_artifact() and relation_create() is called."""
        # Create file for the artifact
        blhc_output = self.create_temporary_file(
            suffix=".txt", contents=b"NONVERBOSE BUILD: Compiling gkrust\n"
        )

        artifact = BlhcArtifact.create(blhc_output=blhc_output)

        # No exception is raised
        json.dumps(
            serialize_local_artifact(artifact, workspace="some-workspace")
        )


class DebDiffArtifactTests(TestCase):
    """Tests for DebDiffArtifact class."""

    output_content: ClassVar[bytes]

    @classmethod
    def setUpClass(cls) -> None:
        """Set up common data for the tests."""
        super().setUpClass()
        cls.output_content = (
            b"diff -Nru a/debian/patches/series b/debian/patches/series"
            b"--- a/debian/patches/series       2024-03-23 10:37:14.00000 +0100"
            b"+++ b/debian/patches/series       2024-08-22 12:58:30.00000 +0200"
            b"@@ -5,3 +5,4 @@"
            b" fix-ftbfs-with-gcc-12.patch"
            b" fix-aptitude-changelog-parser.patch"
            b" fix-ftbfs-with-t64.patch"
            b"+0008-Add-missing-include-to-build-with-gcc-14.patch"
        )

    def test_create(self) -> None:
        """Test _create()."""
        output = self.create_temporary_file(
            suffix=".txt", contents=self.output_content
        )

        package = DebDiffArtifact.create(
            debdiff_output=output, original="original", new="new"
        )

        self.assertIsInstance(package, DebDiffArtifact)
        self.assertEqual(
            package.data, data_models.DebDiff(original="original", new="new")
        )

        self.assertEqual(
            package.files,
            {
                "debdiff.txt": output,
            },
        )

    def test_json_serializable(self) -> None:
        """Artifact returned by create is JSON serializable."""
        # Create file for the artifact
        debdiff_output = self.create_temporary_file(
            suffix=".txt", contents=b"debdiff a.deb b.deb\n"
        )

        artifact = DebDiffArtifact.create(
            debdiff_output=debdiff_output, original="original", new="new"
        )

        # No exception is raised
        json.dumps(
            serialize_local_artifact(artifact, workspace="some-workspace")
        )


class DebianSystemImageArtifactTests(TestCase):
    """Tests for DebianSystemImageArtifactTests."""

    def test_create_tar_xz(self) -> None:
        """Test _create() with .tar.xz."""
        system_tar_xz = self.create_temporary_file(
            suffix=".tar.xz", contents=b"some-file-contents"
        )

        artifact = DebianSystemImageArtifact.create(
            system_tar_xz,
            {
                "image_format": "raw",
                "boot_mechanism": "efi",
                "vendor": "debian",
                "codename": "sid",
                "mirror": "https://deb.debian.org",
                "components": ["main"],
                "filesystem": "ext4",
                "size": 12345,
                "pkglist": {},
                "architecture": "amd64",
                "with_dev": True,
                "with_init": True,
            },
        )

        self.assertEqual(artifact.files, {system_tar_xz.name: system_tar_xz})
        self.assertEqual(artifact.data.filename, system_tar_xz.name)

    def test_create_qcow2(self) -> None:
        """Test _create() with .qcow2."""
        system_qcow2 = self.create_temporary_file(
            suffix=".qcow2", contents=b"some-file-contents"
        )

        artifact = DebianSystemImageArtifact.create(
            system_qcow2,
            {
                "image_format": "qcow2",
                "boot_mechanism": "efi",
                "vendor": "debian",
                "codename": "sid",
                "mirror": "https://deb.debian.org",
                "components": ["main"],
                "filesystem": "ext4",
                "size": 12345,
                "pkglist": {},
                "architecture": "amd64",
                "with_dev": True,
                "with_init": True,
            },
        )

        self.assertEqual(artifact.files, {system_qcow2.name: system_qcow2})
        self.assertEqual(artifact.data.filename, system_qcow2.name)

    def test_validate_file_name_ending_tar_xz(self) -> None:
        """validate_files is .tar.xz: return files."""
        system_tar_xz = self.create_temporary_file(
            suffix=".tar.xz", contents=b"some-file-contents"
        )

        files = {system_tar_xz.name: system_tar_xz}

        self.assertEqual(
            DebianSystemImageArtifact._validate_files(files),
            files,
        )

    def test_validate_file_name_ending_qcow2(self) -> None:
        """validate_files is .qcow2: return files."""
        system_qcow2 = self.create_temporary_file(
            suffix=".qcow2", contents=b"some-file-contents"
        )

        files = {system_qcow2.name: system_qcow2}

        self.assertEqual(
            DebianSystemImageArtifact._validate_files(files),
            files,
        )

    def test_validate_two_files(self) -> None:
        """validate_files raise ValueError if not one file."""
        file1 = self.create_temporary_file(
            suffix="temp", contents=b"some-file-contents"
        )

        file2 = self.create_temporary_file(
            suffix="temp", contents=b"some-file-contents"
        )

        files = {file1.name: file1, file2.name: file2}

        msg = "DebianSystemImageArtifact does not contain exactly one file"
        with self.assertRaisesRegex(ValueError, msg):
            DebianSystemImageArtifact._validate_files(files)

    def test_validate_file_name_ending_not_valid_raise(self) -> None:
        """validate_files raise ValueError with other file ending."""
        file = self.create_temporary_file(
            suffix="temp", contents=b"some-file-contents"
        )

        files = {file.name: file}

        msg = f"Invalid file name: '{file.name}'. Expected .tar.xz or qcow2"
        with self.assertRaisesRegex(ValueError, msg):
            DebianSystemImageArtifact._validate_files(files)


class SigningInputArtifactTests(TestCase):
    """Tests for SigningInputArtifact."""

    def test_create(self) -> None:
        """Create a valid SigningInputArtifact."""
        base_dir = self.create_temporary_directory()
        unsigned = base_dir / "package" / "dir" / "file"
        unsigned.parent.mkdir(parents=True)
        unsigned.write_text("contents")

        artifact = SigningInputArtifact.create([unsigned], base_dir)

        artifact.validate_model()
        self.assertEqual(
            artifact.data, {"trusted_certs": None, "binary_package_name": None}
        )
        self.assertEqual(artifact.files, {"package/dir/file": unsigned})
        self.assertEqual(
            artifact.content_types,
            {"package/dir/file": "application/octet-stream"},
        )

    def test_create_trusted_certs(self) -> None:
        """Create a SigningInputArtifact with trusted_certs."""
        base_dir = self.create_temporary_directory()
        unsigned_1 = base_dir / "package" / "dir" / "file"
        unsigned_1.parent.mkdir(parents=True)
        unsigned_1.write_text("contents")
        unsigned_2 = base_dir / "package" / "another-dir" / "another-file"
        unsigned_2.parent.mkdir(parents=True)
        unsigned_2.write_text("other-contents")

        artifact = SigningInputArtifact.create(
            [unsigned_1, unsigned_2], base_dir, trusted_certs=["0" * 64]
        )

        artifact.validate_model()
        self.assertEqual(
            artifact.data,
            {"trusted_certs": ["0" * 64], "binary_package_name": None},
        )
        self.assertEqual(
            artifact.files,
            {
                "package/dir/file": unsigned_1,
                "package/another-dir/another-file": unsigned_2,
            },
        )
        self.assertEqual(
            artifact.content_types,
            {
                "package/dir/file": "application/octet-stream",
                "package/another-dir/another-file": "application/octet-stream",
            },
        )

    def test_create_binary_package_name(self) -> None:
        """Create a valid SigningInputArtifact with a binary package name."""
        base_dir = self.create_temporary_directory()
        unsigned = base_dir / "package" / "dir" / "file"
        unsigned.parent.mkdir(parents=True)
        unsigned.write_text("contents")

        artifact = SigningInputArtifact.create(
            [unsigned], base_dir, binary_package_name="hello"
        )

        artifact.validate_model()
        self.assertEqual(
            artifact.data,
            {"trusted_certs": None, "binary_package_name": "hello"},
        )
        self.assertEqual(artifact.files, {"package/dir/file": unsigned})
        self.assertEqual(
            artifact.content_types,
            {"package/dir/file": "application/octet-stream"},
        )

    def test_create_no_files(self) -> None:
        """A SigningInputArtifact must have at least one file."""
        base_dir = self.create_temporary_directory()

        artifact = SigningInputArtifact.create([], base_dir)

        with self.assertRaisesRegex(ValueError, "Expected at least one file"):
            artifact.validate_model()


class SigningOutputArtifactTests(TestCase):
    """Tests for SigningOutputArtifact."""

    def test_create(self) -> None:
        """Create a valid SigningOutputArtifact."""
        base_dir = self.create_temporary_directory()
        output_file = base_dir / "package" / "dir" / "file.sig"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("signature")
        result = data_models.SigningResult(
            file="package/dir/file", output_file="package/dir/file.sig"
        )

        artifact = SigningOutputArtifact.create(
            KeyPurpose.UEFI,
            "0" * 64,
            [result],
            [output_file],
            base_dir,
        )

        artifact.validate_model()
        self.assertEqual(
            artifact.data,
            {
                "purpose": KeyPurpose.UEFI,
                "fingerprint": "0" * 64,
                "results": [result],
                "binary_package_name": None,
            },
        )
        self.assertEqual(artifact.files, {"package/dir/file.sig": output_file})
        self.assertEqual(
            artifact.content_types,
            {"package/dir/file.sig": "application/octet-stream"},
        )

    def test_create_binary_package_name(self) -> None:
        """Create a valid SigningOutputArtifact with a binary package name."""
        base_dir = self.create_temporary_directory()
        output_file = base_dir / "package" / "dir" / "file.sig"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("signature")
        result = data_models.SigningResult(
            file="package/dir/file", output_file="package/dir/file.sig"
        )

        artifact = SigningOutputArtifact.create(
            KeyPurpose.UEFI,
            "0" * 64,
            [result],
            [output_file],
            base_dir,
            binary_package_name="hello",
        )

        artifact.validate_model()
        self.assertEqual(
            artifact.data,
            {
                "purpose": KeyPurpose.UEFI,
                "fingerprint": "0" * 64,
                "results": [result],
                "binary_package_name": "hello",
            },
        )
        self.assertEqual(artifact.files, {"package/dir/file.sig": output_file})
        self.assertEqual(
            artifact.content_types,
            {"package/dir/file.sig": "application/octet-stream"},
        )


class RepositoryIndexTests(TestCase):
    """Tests for RepositoryIndex."""

    def test_create(self) -> None:
        """Create a valid RepositoryIndex."""
        directory = self.create_temporary_directory()
        (
            release := directory / "deb.debian.org_debian_dists_sid_Release"
        ).write_text("A Release file\n")

        artifact = RepositoryIndex.create(file=release, path="Release")

        artifact.validate_model()
        self.assertEqual(
            artifact,
            RepositoryIndex(
                category=RepositoryIndex._category,
                files={"Release": release},
                data=data_models.DebianRepositoryIndex(path="Release"),
            ),
        )
