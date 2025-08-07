# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the artifact playground functions."""

from debusine.artifacts.local_artifact import Upload
from debusine.artifacts.playground import ArtifactPlayground
from debusine.test import TestCase


class ArtifactPlaygroundTest(TestCase):
    """Test artifact playground behaviour."""

    def test_source_package_defaults(self) -> None:
        """Check building a source package."""
        workdir = self.create_temporary_directory()
        src = ArtifactPlayground.create_source_package(workdir)
        self.assertEqual(
            src.files,
            {
                'hello_1.0-1.debian.tar.xz': workdir
                / "hello_1.0-1.debian.tar.xz",
                'hello_1.0-1.dsc': workdir / "hello_1.0-1.dsc",
                'hello_1.0.orig.tar.gz': workdir / "hello_1.0.orig.tar.gz",
            },
        )
        self.assert_source_artifact_equal(src.data)

    def test_source_package_native(self) -> None:
        """Check building a source package."""
        workdir = self.create_temporary_directory()
        src = ArtifactPlayground.create_source_package(workdir, version="1.0")
        self.assertEqual(
            src.files,
            {
                'hello_1.0.dsc': workdir / "hello_1.0.dsc",
                'hello_1.0.tar.gz': workdir / "hello_1.0.tar.gz",
            },
        )
        self.assert_source_artifact_equal(src.data, version="1.0")

    def test_package_build_log(self) -> None:
        """Check building a package build log."""
        workdir = self.create_temporary_directory()
        filename = "hello_1.0-1_source.build"
        file = workdir / filename
        file.touch()
        buildlog = ArtifactPlayground.create_package_build_log(file)
        self.assertEqual(buildlog.data.source, "hello")
        self.assertEqual(buildlog.data.version, "1.0-1")
        self.assertEqual(buildlog.data.filename, filename)

    def test_package_build_log_args(self) -> None:
        """Check building a package build log overriding source/version."""
        workdir = self.create_temporary_directory()
        filename = "hello_1.0-1_source.build"
        file = workdir / filename
        file.touch()
        buildlog = ArtifactPlayground.create_package_build_log(
            file, source="test"
        )
        self.assertEqual(buildlog.data.source, "test")
        self.assertEqual(buildlog.data.version, "1.0-1")
        self.assertEqual(buildlog.data.filename, filename)

        buildlog = ArtifactPlayground.create_package_build_log(
            file, version="2.0"
        )
        self.assertEqual(buildlog.data.source, "hello")
        self.assertEqual(buildlog.data.version, "2.0")
        self.assertEqual(buildlog.data.filename, filename)

        buildlog = ArtifactPlayground.create_package_build_log(
            file, source="test", version="2.0"
        )
        self.assertEqual(buildlog.data.source, "test")
        self.assertEqual(buildlog.data.version, "2.0")
        self.assertEqual(buildlog.data.filename, filename)

    def test_package_build_log_invalid_filename(self) -> None:
        """Check creating a build log with an invalid name."""
        workdir = self.create_temporary_directory()
        (file := workdir / "foo.build").touch()
        with self.assertRaisesRegex(ValueError, "cannot be inferred"):
            ArtifactPlayground.create_package_build_log(file)

    def test_binary_package(self) -> None:
        """Check building a binary package."""
        workdir = self.create_temporary_directory()
        binary = ArtifactPlayground.create_binary_package(workdir)
        self.assertEqual(binary.data.srcpkg_name, "hello")
        self.assertEqual(binary.data.srcpkg_version, "1.0-1")
        self.assertEqual(
            binary.data.deb_fields,
            {
                'Architecture': 'all',
                'Description': 'Example description',
                'Maintainer': 'Example Maintainer <example@example.org>',
                'Package': 'hello',
                'Version': '1.0-1',
            },
        )
        self.assertEqual(binary.data.deb_control_files, ["control"])

    def assertUpload(
        self,
        upload: Upload,
        *,
        expected_version: str = "1.0-1",
        expected_architecture: set[str] = {"source"},
        expected_files: set[str],
        expected_binaries: set[str] = set(),
    ) -> None:
        """Assert that upload has the expected data and files."""
        data_fields = upload.data.changes_fields.copy()
        files = data_fields.pop('Files')
        for field in list(data_fields.keys()):
            if field.startswith('Checksums-'):
                data_fields.pop(field)
        expected_fields = {
            "Format": "1.8",
            "Date": "Sun, 01 Dec 2024 00:00:00 -0000",
            "Source": "hello",
            "Architecture": " ".join(sorted(expected_architecture)),
            "Version": expected_version,
            "Distribution": "unstable",
            "Urgency": "medium",
            "Maintainer": "Example Maintainer <example@example.org>",
            "Changes": "\n".join(
                (
                    "",
                    (
                        f"  hello ({expected_version}) unstable; "
                        f"urgency=medium"
                    ),
                    "  .",
                    "    * Test upload.",
                )
            ),
        }
        if expected_binaries:
            expected_fields["Binary"] = " ".join(sorted(expected_binaries))
            expected_fields["Description"] = "\n" + "\n".join(
                f" {binary} - A Description" for binary in expected_binaries
            )
        self.assertEqual(
            data_fields,
            expected_fields,
        )
        file_names = {file["name"] for file in files}
        self.assertEqual(file_names, expected_files)

    def test_source_upload(self) -> None:
        """Check building a source-only upload."""
        workdir = self.create_temporary_directory()
        upload = ArtifactPlayground.create_upload(workdir)
        self.assertUpload(
            upload,
            expected_files={
                "hello_1.0-1.dsc",
                "hello_1.0-1.debian.tar.xz",
                "hello_1.0.orig.tar.gz",
            },
        )

    def test_source_native_upload(self) -> None:
        """Check building a source-only native upload."""
        workdir = self.create_temporary_directory()
        upload = ArtifactPlayground.create_upload(workdir, version="1.0")
        self.assertUpload(
            upload,
            expected_version="1.0",
            expected_files={
                "hello_1.0.dsc",
                "hello_1.0.tar.gz",
            },
        )

    def test_binary_upload(self) -> None:
        """Check building a binary-only upload."""
        workdir = self.create_temporary_directory()
        upload = ArtifactPlayground.create_upload(
            workdir,
            source=False,
            binary=True,
        )
        self.assertUpload(
            upload,
            expected_architecture={"all"},
            expected_files={
                "hello_1.0-1_all.deb",
            },
            expected_binaries={"hello"},
        )

    def test_mixed_upload(self) -> None:
        """Check building a source+binary upload."""
        workdir = self.create_temporary_directory()
        upload = ArtifactPlayground.create_upload(
            workdir, binary=True, binaries=["hello"]
        )
        self.assertUpload(
            upload,
            expected_architecture={"source", "all"},
            expected_files={
                "hello_1.0-1.dsc",
                "hello_1.0-1.debian.tar.xz",
                "hello_1.0.orig.tar.gz",
                "hello_1.0-1_all.deb",
            },
            expected_binaries={"hello"},
        )
