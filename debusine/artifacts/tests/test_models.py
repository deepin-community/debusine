# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for classes in models.py."""
from typing import Any
from unittest import TestCase

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

import debusine.artifacts.models as data_models
from debusine.assets.models import KeyPurpose


class EnumTests(TestCase):
    """Tests for model enums."""

    def test_enums_str(self) -> None:
        """Test enum stringification."""
        for enum_cls in (
            data_models.DebianAutopkgtestResultStatus,
            data_models.DebianLintianSeverity,
        ):
            with self.subTest(enum_cls=enum_cls):
                for el in enum_cls:
                    with self.subTest(el=el):
                        self.assertEqual(str(el), el.value)


class DebusineHistoricalTaskRunTests(TestCase):
    """Test the DebusineHistoricalTaskRun model."""

    def test_task_type_validation(self) -> None:
        """task_type must be a valid task type."""
        kwargs: dict[str, Any] = {
            "task_name": "noop",
            "subject": "subject",
            "context": "sid/amd64",
            "timestamp": 0,
            "work_request_id": 1,
            "result": "success",
            "runtime_statistics": data_models.RuntimeStatistics(duration=1),
        }
        data_models.DebusineHistoricalTaskRun(
            task_type=data_models.TaskTypes.WORKER, **kwargs
        )
        with self.assertRaisesRegex(
            ValueError, "is not a valid enumeration member"
        ):
            data_models.DebusineHistoricalTaskRun(
                task_type="does-not-exist", **kwargs  # type: ignore[arg-type]
            )


class DebusineTaskConfigurationTests(TestCase):
    """Test the DebusineTaskConfiguration model."""

    def test_name(self) -> None:
        """Test name generation."""
        data = data_models.DebusineTaskConfiguration(template="test")
        self.assertEqual(data.name(), "template:test")

        data = data_models.DebusineTaskConfiguration(
            task_type=data_models.TaskTypes.WORKER,
            task_name="noop",
            subject="subject",
            context="sid/amd64",
        )
        self.assertEqual(data.name(), "Worker:noop:subject:sid/amd64")

        data = data_models.DebusineTaskConfiguration(
            task_type=data_models.TaskTypes.WORKER,
            task_name="noop",
            subject="subject",
            context="source:debusine",
        )
        self.assertEqual(data.name(), "Worker:noop:subject:source%3Adebusine")

    def test_task_type_validation(self) -> None:
        """task_type must be a valid task type."""
        kwargs: dict[str, Any] = {
            "task_name": "noop",
            "subject": "subject",
            "context": "sid/amd64",
        }
        data_models.DebusineTaskConfiguration(
            task_type=data_models.TaskTypes.WORKER, **kwargs
        )
        with self.assertRaisesRegex(
            ValueError, "is not a valid enumeration member"
        ):
            data_models.DebusineTaskConfiguration(
                task_type="does-not-exist", **kwargs  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "should be set"):
            data_models.DebusineTaskConfiguration(task_type=None, **kwargs)
        with self.assertRaisesRegex(ValueError, "should be set"):
            data_models.DebusineTaskConfiguration(**kwargs)

    def test_task_name_validation(self) -> None:
        """Task names must not contain colons."""
        kwargs: dict[str, Any] = {
            "task_type": data_models.TaskTypes.WORKER,
            "subject": "subject",
            "context": "sid/amd64",
        }
        data_models.DebusineTaskConfiguration(task_name="noop", **kwargs)
        with self.assertRaisesRegex(ValueError, "may not contain ':'"):
            data_models.DebusineTaskConfiguration(task_name="no:op", **kwargs)
        with self.assertRaisesRegex(ValueError, "should be set"):
            data_models.DebusineTaskConfiguration(task_name=None, **kwargs)
        with self.assertRaisesRegex(ValueError, "should be set"):
            data_models.DebusineTaskConfiguration(**kwargs)

    def test_subject_validation(self) -> None:
        """Subject must not contain colons."""
        kwargs: dict[str, Any] = {
            "task_type": data_models.TaskTypes.WORKER,
            "task_name": "noop",
            "context": "sid/amd64",
        }
        data_models.DebusineTaskConfiguration(subject="subject", **kwargs)
        data_models.DebusineTaskConfiguration(subject=None, **kwargs)
        data_models.DebusineTaskConfiguration(**kwargs)
        with self.assertRaisesRegex(ValueError, "may not contain ':'"):
            data_models.DebusineTaskConfiguration(subject="sub:ject", **kwargs)

    def test_context_validation(self) -> None:
        """Context must not contain colons."""
        kwargs: dict[str, Any] = {
            "task_type": data_models.TaskTypes.WORKER,
            "task_name": "noop",
            "subject": "subject",
        }
        data_models.DebusineTaskConfiguration(
            context="source:debusine", **kwargs
        )
        data_models.DebusineTaskConfiguration(context="sid/amd64", **kwargs)
        data_models.DebusineTaskConfiguration(context="a b c", **kwargs)
        data_models.DebusineTaskConfiguration(context=None, **kwargs)
        data_models.DebusineTaskConfiguration(**kwargs)

    def test_template_excludes_task_match(self) -> None:
        """Template excludes the use of task match fields."""
        for name, value in (
            ("task_type", data_models.TaskTypes.WORKER),
            ("task_name", "noop"),
            ("subject", "subject"),
            ("context", "sid/amd64"),
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    ValueError, "should be empty if template is set"
                ),
            ):
                kwargs: dict[str, Any] = {name: value}
                data_models.DebusineTaskConfiguration(template="test", **kwargs)

    def test_get_lookup_names(self) -> None:
        """Test get_lookup_names."""
        f = data_models.DebusineTaskConfiguration.get_lookup_names
        for args, expected in (
            (("t", "n", None, None), ["t:n::"]),
            (("t", "n", "s", None), ["t:n::", "t:n:s:"]),
            (("t", "n", None, "c"), ["t:n::", "t:n::c"]),
            (("t", "n", "s", "c"), ["t:n::", "t:n::c", "t:n:s:", "t:n:s:c"]),
            (
                ("t", "n", "s", ":"),
                ["t:n::", "t:n::%3A", "t:n:s:", "t:n:s:%3A"],
            ),
        ):
            with self.subTest(args=args):
                self.assertEqual(f(*args), expected)


class EmptyArtifactDataTest(TestCase):
    """Test the EmptyArtifactData model."""

    def test_get_label(self) -> None:
        """get_label returns None."""
        data = data_models.EmptyArtifactData()
        label = data.get_label()  # type: ignore[func-returns-value]
        self.assertIsNone(label)


class DebianPackageBuildLogTest(TestCase):
    """Test the DebianPackageBuildLog model."""

    def test_get_label(self) -> None:
        """get_label returns source."""
        data = data_models.DebianPackageBuildLog(
            source="test",
            version="1.0-1",
            architecture="amd64",
            filename="test_1.0-1_amd64.buildlog",
        )
        self.assertEqual(data.get_label(), "test_1.0-1_amd64.buildlog")


class DebianSourcePackageTests(TestCase):
    """Test the DebianSourcePackage model."""

    def test_construct(self) -> None:
        """Test model constructor."""
        data = data_models.DebianSourcePackage(
            name="test-name",
            version="test-version",
            type="dpkg",
            dsc_fields={"key": "val"},
        )

        self.assertEqual(data.name, "test-name")
        self.assertEqual(data.version, "test-version")
        self.assertEqual(data.type, "dpkg")
        self.assertEqual(data.dsc_fields, {"key": "val"})
        self.assertEqual(data.get_label(), "test-name_test-version")

    def test_dsc_fields_missing(self) -> None:
        """Test checking that dsc_fields is present."""
        error_msg = r"dsc_fields\s+field required \(type=value_error\.missing\)"

        with self.assertRaisesRegex(Exception, error_msg):
            data_models.DebianSourcePackage(  # type: ignore[call-arg]
                name="test-name", version="test-version", type="dpkg"
            )


class DebianUploadTests(TestCase):
    """Test the DebianUpload model."""

    def test_label(self) -> None:
        """Test get_label scenarios."""

        def make_upload(
            files: list[str], **kwargs: Any
        ) -> data_models.DebianUpload:
            kwargs["Files"] = [{"name": name} for name in files]
            return data_models.DebianUpload(
                type="dpkg",
                changes_fields={"Architecture": "amd64", **kwargs},
            )

        self.assertEqual(
            make_upload(
                ["test.deb"], Source="test", Version="1.0-1"
            ).get_label(),
            "test_1.0-1",
        )
        self.assertEqual(
            make_upload(["test.deb"], Source="test").get_label(),
            "test.deb",
        )
        self.assertEqual(
            make_upload(["test.deb", "test.changes"]).get_label(),
            "test.changes",
        )
        self.assertEqual(
            make_upload(["test2.deb", "test1.deb"]).get_label(),
            "test2.deb",
        )
        upload = make_upload(["test.deb"])
        upload.changes_fields["Files"] = []
        self.assertIsNone(upload.get_label())

    def test_metadata_contains_architecture(self) -> None:
        """changes_fields must contain Architecture."""
        data = {
            "Files": [{"name": "foo.dsc"}],
        }
        with self.assertRaisesRegex(
            ValueError, r"changes_fields must contain Architecture"
        ):
            data_models.DebianUpload(type="dpkg", changes_fields=data)

    def test_metadata_contains_files(self) -> None:
        """changes_fields must contain Architecture."""
        data = {
            "Architecture": "amd64",
        }
        with self.assertRaisesRegex(
            ValueError, r"changes_fields must contain Files"
        ):
            data_models.DebianUpload(type="dpkg", changes_fields=data)

    def test_metadata_contains_debs_if_binary_requires_debs_for_binaries(
        self,
    ) -> None:
        """metadata_contains_debs_if_binary requires at least one .deb."""
        data = {
            "Architecture": "amd64",
            "Files": [{"name": "foo.dsc"}],
        }
        with self.assertRaisesRegex(
            ValueError,
            r"No \.debs found in \['foo\.dsc'\] which is expected to contain "
            r"binaries for amd64",
        ):
            data_models.DebianUpload.metadata_contains_debs_if_binary(data)

    def test_metadata_contains_debs_if_binary_ignores_source_uploads(
        self,
    ) -> None:
        """metadata_contains_debs_if_binary ignores source uploads."""
        data = {
            "Architecture": "source",
            "Files": [{"name": "foo.dsc"}],
        }
        self.assertEqual(
            data_models.DebianUpload.metadata_contains_debs_if_binary(data),
            data,
        )

    def test_metadata_contains_debs_if_binary_finds_debs_in_source_uploads(
        self,
    ) -> None:
        """metadata_contains_debs_if_binary finds debs in source uploads."""
        data = {
            "Architecture": "source",
            "Files": [{"name": "foo.deb"}],
        }
        with self.assertRaisesRegex(
            ValueError,
            r"Unexpected binary packages \['foo\.deb'\] found in source-only "
            r"upload\.",
        ):
            data_models.DebianUpload.metadata_contains_debs_if_binary(data)

    def test_metadata_contains_debs_if_binary_accepts_debs(self) -> None:
        """metadata_contains_debs_if_binary will accept one .deb."""
        data = {
            "Architecture": "amd64",
            "Files": [{"name": "foo_amd64.deb"}],
        }
        self.assertEqual(
            data,
            data_models.DebianUpload.metadata_contains_debs_if_binary(data),
        )

    def test_metadata_contains_debs_if_binary_accepts_debs_in_mixed(
        self,
    ) -> None:
        """metadata_contains_debs_if_binary will accept deb in mixed upload."""
        data = {
            "Architecture": "amd64 source",
            "Files": [{"name": "foo_amd64.deb"}],
        }
        self.assertEqual(
            data,
            data_models.DebianUpload.metadata_contains_debs_if_binary(data),
        )

    def test_metadata_contains_dsc_if_source_requires_1_dsc_for_source(
        self,
    ) -> None:
        """metadata_contains_dsc_if_source requires 1 .dsc."""
        data = {
            "Architecture": "source",
            "Files": [{"name": "foo.deb"}],
        }
        with self.assertRaisesRegex(
            ValueError,
            r"Expected to find one and only one source package in source "
            r"upload\. Found \[\].",
        ):
            data_models.DebianUpload.metadata_contains_dsc_if_source(data)

    def test_metadata_contains_dsc_if_source_rejects_2_dsc_for_source(
        self,
    ) -> None:
        """metadata_contains_dsc_if_source rejects 2 .dscs."""
        data = {
            "Architecture": "source",
            "Files": [{"name": "foo.dsc"}, {"name": "bar.dsc"}],
        }
        with self.assertRaisesRegex(
            ValueError,
            r"Expected to find one and only one source package in source "
            r"upload\. Found \['foo\.dsc', 'bar\.dsc'\]\.",
        ):
            data_models.DebianUpload.metadata_contains_dsc_if_source(data)

    def test_metadata_contains_dsc_if_source_ignores_binary_uploads(
        self,
    ) -> None:
        """metadata_contains_dsc_if_source ignores binary uploads."""
        data = {
            "Architecture": "amd64",
            "Files": [{"name": "foo.deb"}],
        }
        self.assertEqual(
            data_models.DebianUpload.metadata_contains_dsc_if_source(data), data
        )

    def test_metadata_contains_dsc_if_source_finds_dsc_in_bin_uploads(
        self,
    ) -> None:
        """metadata_contains_dsc_if_source finds dsc in binary uploads."""
        data = {
            "Architecture": "amd64",
            "Files": [{"name": "foo.dsc"}],
        }
        with self.assertRaisesRegex(
            ValueError,
            r"Binary uploads cannot contain source packages. "
            r"Found: \['foo\.dsc'\].",
        ):
            data_models.DebianUpload.metadata_contains_dsc_if_source(data)

    def test_metadata_contains_dsc_if_source_accepts_1_dsc(self) -> None:
        """metadata_contains_dsc_if_source will accept one .dsc."""
        data = {
            "Architecture": "source",
            "Files": [{"name": "foo.dsc"}],
        }
        self.assertEqual(
            data, data_models.DebianUpload.metadata_contains_dsc_if_source(data)
        )

    def test_metadata_contains_dsc_if_source_accepts_dsc_in_mixed(self) -> None:
        """metadata_contains_dsc_if_source accepts source in mixed upload."""
        data = {
            "Architecture": "amd64 source",
            "Files": [{"name": "foo.dsc"}],
        }
        self.assertEqual(
            data, data_models.DebianUpload.metadata_contains_dsc_if_source(data)
        )


class DebianBinaryPackageTest(TestCase):
    """Test the DebianBinaryPackage model."""

    def test_get_label(self) -> None:
        """get_label returns srcpkg_name."""
        data = data_models.DebianBinaryPackage(
            srcpkg_name="test",
            srcpkg_version="1.0-1",
            deb_fields={
                "Package": "test-bin",
                "Version": "1.0-1+b1",
                "Architecture": "amd64",
            },
            deb_control_files=[],
        )
        self.assertEqual(data.get_label(), "test-bin_1.0-1+b1_amd64")


class DebianBinaryPackagesTest(TestCase):
    """Test the DebianBinaryPackages model."""

    def test_get_label(self) -> None:
        """get_label returns srcpkg_name."""
        data = data_models.DebianBinaryPackages(
            srcpkg_name="test",
            srcpkg_version="1.0-1",
            version="1.0",
            architecture="amd64",
            packages=[],
        )
        self.assertEqual(data.get_label(), "test_1.0-1")


class DebianSystemTarballTest(TestCase):
    """Test the DebianSystemTarball model."""

    def test_get_label(self) -> None:
        """get_label returns filename."""
        data = data_models.DebianSystemTarball(
            filename="bookworm.tar.gz",
            vendor="debian",
            codename="bookworm",
            components=["main"],
            mirror=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://deb.debian.org"
            ),
            variant=None,
            pkglist={},
            architecture="amd64",
            with_dev=False,
            with_init=False,
        )
        self.assertEqual(data.get_label(), "bookworm.tar.gz")


class DebianSystemImageTest(TestCase):
    """Test the DebianSystemImage model."""

    def test_get_label(self) -> None:
        """get_label returns filename."""
        data = data_models.DebianSystemImage(
            filename="bookworm.qcow2",
            vendor="debian",
            codename="bookworm",
            components=["main"],
            mirror=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://deb.debian.org"
            ),
            variant=None,
            pkglist={},
            architecture="amd64",
            with_dev=False,
            with_init=False,
            image_format="qcow2",
            filesystem="ext4",
            size=123456,
            boot_mechanism="efi",
        )
        self.assertEqual(data.get_label(), "bookworm.qcow2")


class DebianLintianTest(TestCase):
    """Test the DebianLintian model."""

    def test_get_label(self) -> None:
        """get_label returns package names."""

        def make_lintian(**kwargs: str) -> data_models.DebianLintian:
            return data_models.DebianLintian(
                architecture="amd64",
                summary=data_models.DebianLintianSummary(
                    tags_count_by_severity={},
                    package_filename=kwargs,
                    tags_found=[],
                    overridden_tags_found=[],
                    lintian_version="1.0",
                    distribution="bookworm",
                ),
            )

        self.assertEqual(make_lintian().get_label(), "lintian (empty)")
        self.assertEqual(
            make_lintian(hello="hello_1.0-1.dsc").get_label(), "lintian: hello"
        )
        self.assertEqual(
            make_lintian(
                libhello="libhello_1.0-1.deb", hello="hello_1.0-1.deb"
            ).get_label(),
            "lintian: hello, libhello",
        )


class DebianAutopkgtestTest(TestCase):
    """Test the DebianAutopkgtest model."""

    def test_get_label(self) -> None:
        """get_label returns source_package.name."""
        data = data_models.DebianAutopkgtest(
            results={},
            cmdline="",
            source_package=data_models.DebianAutopkgtestSource(
                name="hello",
                version="1.0-1",
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl, "https://deb.debian.org/pool/h/hello"
                ),
            ),
            architecture="amd64",
            distribution="bookworm",
        )
        self.assertEqual(data.get_label(), "hello")


class DebusineSigningInputTest(TestCase):
    """Test the DebusineSigningInput model."""

    def test_get_label(self) -> None:
        """get_label returns None."""
        data = data_models.DebusineSigningInput(trusted_certs=[])
        label = data.get_label()  # type: ignore[func-returns-value]
        self.assertIsNone(label)


class DebusinePromiseTests(TestCase):
    """Test the DebusinePromise."""

    def test_construct(self) -> None:
        """Test the model constructor with valid data."""
        data = data_models.DebusinePromise(
            promise_work_request_id=1,
            promise_workflow_id=100,
            promise_category="debian:binary-package",
        )

        self.assertEqual(data.promise_work_request_id, 1)
        self.assertEqual(data.promise_workflow_id, 100)
        self.assertEqual(data.promise_category, "debian:binary-package")

    def test_extra_fields_allowed(self) -> None:
        """Test that extra fields are allowed in the model."""
        data = data_models.DebusinePromise(
            promise_work_request_id=1,
            promise_workflow_id=100,
            promise_category="debian:binary-package",
            extra_field="extra_value",  # type: ignore
        )

        self.assertEqual(data.extra_field, "extra_value")  # type: ignore

    def test_forbidden_promise_prefix_in_extra_field(self) -> None:
        r"""Test that extra fields with 'promise\\_' are not allowed."""
        expected_msg = (
            "Field name 'promise_extra' starting with 'promise_' is not allowed"
        )
        with self.assertRaisesRegex(ValueError, expected_msg):
            data_models.DebusinePromise(
                promise_work_request_id=1,
                promise_workflow_id=100,
                promise_category="debian:binary-package",
                promise_extra="not_allowed",  # type: ignore
            )


class SigningResultTests(TestCase):
    """Test the SigningResult model."""

    def test_output_file(self) -> None:
        """A SigningResult may have an output_file and no error_message."""
        data_models.SigningResult(file="file", output_file="file.sig")

    def test_error_message(self) -> None:
        """A SigningResult may have an error_message and no output_file."""
        data_models.SigningResult(file="file", error_message="Boom")

    def test_both_output_file_and_error_message(self) -> None:
        """A SigningResult may not have both output_file and error_message."""
        with self.assertRaisesRegex(
            ValueError,
            "Exactly one of output_file and error_message must be set",
        ):
            data_models.SigningResult(
                file="file", output_file="file.sig", error_message="Boom"
            )

    def test_neither_output_file_nor_error_message(self) -> None:
        """A SigningResult must have either output_file or error_message."""
        with self.assertRaisesRegex(
            ValueError,
            "Exactly one of output_file and error_message must be set",
        ):
            data_models.SigningResult(file="file")


class DebusineSigningOutputTest(TestCase):
    """Test the DebusineSigningOutput model."""

    def test_get_label(self) -> None:
        """get_label returns None."""
        data = data_models.DebusineSigningOutput(
            purpose=KeyPurpose.UEFI,
            fingerprint="123456ABCD",
            results=[],
        )
        label = data.get_label()  # type: ignore[func-returns-value]
        self.assertIsNone(label)


class DebDiffTest(TestCase):
    """Test the DebDiff model."""

    def test_get_label(self) -> None:
        """get_label returns the debdiff command."""
        data = data_models.DebDiff(
            original="original-package",
            new="new-package",
        )
        self.assertEqual(
            data.get_label(), "debdiff original-package new-package"
        )


class DebianRepositoryIndexTests(TestCase):
    """Test the DebianRepositoryIndex model."""

    def test_get_label(self) -> None:
        data = data_models.DebianRepositoryIndex(path="main/source/Sources.xz")
        self.assertEqual(data.get_label(), "main/source/Sources.xz")


class GetSourcePackageNameTests(TestCase):
    """Tests for function get_source_package_name."""

    def test_DebianSourcePackage(self) -> None:
        """Test artifact_data is a DebianSourcePackage."""
        artifact_data = data_models.DebianSourcePackage(
            name="hello", version="1.0", type="dpkg", dsc_fields={}
        )
        self.assertEqual(
            data_models.get_source_package_name(artifact_data), "hello"
        )

    def test_DebianUpload(self) -> None:
        """Test artifact_data is a DebianUpload."""
        artifact_data = data_models.DebianUpload(
            type="dpkg",
            changes_fields={
                "Source": "hello",
                "Architecture": "amd64",
                "Files": [{"name": "hello_1.0_amd64.deb"}],
            },
        )
        self.assertEqual(
            data_models.get_source_package_name(artifact_data), "hello"
        )

    def test_DebianBinaryPackage(self) -> None:
        """Test artifact_data is a DebianBinaryPackage."""
        artifact_data = data_models.DebianBinaryPackage(
            srcpkg_name="hello",
            srcpkg_version="1.0-1",
            deb_fields={
                "Package": "hello-bin",
                "Version": "1.0-1+b1",
                "Architecture": "amd64",
            },
            deb_control_files=[],
        )
        self.assertEqual(
            data_models.get_source_package_name(artifact_data), "hello"
        )

    def test_DebianBinaryPackages(self) -> None:
        """Test artifact_data is a DebianBinaryPackages."""
        artifact_data = data_models.DebianBinaryPackages(
            srcpkg_name="hello",
            srcpkg_version="1.0-1",
            version="1.0",
            architecture="amd64",
            packages=[],
        )
        self.assertEqual(
            data_models.get_source_package_name(artifact_data), "hello"
        )

    def test_invalid_type(self) -> None:
        """Test artifact_data is not supported."""
        artifact_data = data_models.EmptyArtifactData()

        with self.assertRaisesRegex(
            TypeError, "^Unexpected type: EmptyArtifactData$"
        ):
            data_models.get_source_package_name(
                artifact_data  # type: ignore[arg-type]
            )


class GetBinaryPackageNameTests(TestCase):
    """Tests for function get_binary_package_name."""

    def test_DebianBinaryPackage(self) -> None:
        """Test artifact_data is a DebianBinaryPackage."""
        artifact_data = data_models.DebianBinaryPackage(
            srcpkg_name="hello",
            srcpkg_version="1.0-1",
            deb_fields={
                "Package": "hello-bin",
                "Version": "1.0-1+b1",
                "Architecture": "amd64",
            },
            deb_control_files=[],
        )
        self.assertEqual(
            data_models.get_binary_package_name(artifact_data), "hello-bin"
        )

    def test_invalid_type(self) -> None:
        """Test artifact_data is not supported."""
        artifact_data = data_models.EmptyArtifactData()

        with self.assertRaisesRegex(
            TypeError, "^Unexpected type: EmptyArtifactData$"
        ):
            data_models.get_binary_package_name(
                artifact_data  # type: ignore[arg-type]
            )
