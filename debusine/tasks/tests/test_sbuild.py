# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the sbuild task support on the worker client."""
import itertools
import textwrap
import types
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, call

from debusine import utils
from debusine.artifacts import (
    BinaryPackage,
    BinaryPackages,
    PackageBuildLog,
    SigningInputArtifact,
    Upload,
)
from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianSourcePackage,
    DoseDistCheck,
    EmptyArtifactData,
)
from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse, RemoteArtifact
from debusine.tasks import Sbuild, TaskConfigError
from debusine.tasks.executors import ExecutorInterface
from debusine.tasks.models import SbuildDynamicData, WorkerType
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


class SbuildTests(ExternalTaskHelperMixin[Sbuild], TestCase):
    """Test the Sbuild class."""

    SAMPLE_TASK_DATA = {
        "input": {
            "source_artifact": 5,
        },
        "environment": "debian/match:codename=bullseye",
        "host_architecture": "amd64",
        "build_components": [
            "any",
            "all",
        ],
    }

    def setUp(self) -> None:
        """
        Set a path to the ontology files used in the debusine tests.

        If the worker is moved to a separate source package, this will
        need to be updated.
        """
        self.configure_task(self.configuration("amd64", ["any"]))

    def configure_task(
        self,
        task_data: dict[str, Any] | None = None,
        override: dict[str, Any] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        """Perform further setup."""
        if hasattr(self, "task"):
            self._cleanup_debug_log_files_directory()

        super().configure_task(task_data, override, remove)

        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=int(self.task.data.input.source_artifact),
            binnmu_maintainer="Debusine <noreply@debusine.example.com>",
        )
        self.task.logger.disabled = True
        self.addCleanup(setattr, self.task.logger, "disabled", False)

    def _cleanup_debug_log_files_directory(self) -> None:
        """Delete self.task._debug_log_files_directory."""
        if self.task._debug_log_files_directory is not None:
            self.task._debug_log_files_directory.cleanup()

    def tearDown(self) -> None:
        """Cleanup at the end of a task."""
        self._cleanup_debug_log_files_directory()

    def mock_cmdline(self, cmdline: list[str]) -> None:
        """Patch self.task to return cmdline."""
        patcher = mock.patch.object(self.task, "_cmdline")
        self.cmdline_mock = patcher.start()
        self.cmdline_mock.return_value = cmdline
        self.addCleanup(patcher.stop)

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        self.configure_task(
            override={
                "backend": "unshare",
                "environment": "debian/match:codename=bookworm",
            },
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
            settings={
                "DEBUSINE_FQDN": "debusine.example.com",
            },
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            SbuildDynamicData(
                environment_id=1,
                input_source_artifact_id=5,
                input_extra_binary_artifacts_ids=[],
                binnmu_maintainer="Debusine <noreply@debusine.example.com>",
                subject="hello",
                runtime_context="all+any:amd64:amd64",
                configuration_context="sid",
            ),
        )

    def test_compute_dynamic_data_build_profiles_runtime_context(self) -> None:
        """
        Dynamic data includes build_profiles.

        If build_profiles are included then the runtime_context is None.
        """
        self.configure_task(
            override={
                "build_profiles": ["nodoc", "nocheck"],
                "backend": "unshare",
                "environment": "debian/match:codename=bookworm",
            },
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
            settings={
                "DEBUSINE_FQDN": "debusine.example.com",
            },
        )

        self.assertIsNone(
            self.task.compute_dynamic_data(task_db).runtime_context
        )

    def test_compute_dynamic_data_raise_task_config_error(self) -> None:
        """Raise TaskConfigError: invalid input_source_artifact."""
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
                    category=ArtifactCategory.BINARY_PACKAGES,
                    data=EmptyArtifactData(),
                ),
            },
            settings={
                "DEBUSINE_FQDN": "debusine.example.com",
            },
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            "^input.source_artifact: unexpected artifact category: "
            r"'debian:binary-packages'. Valid categories: "
            r"\['debian:source-package'\]$",
        ):
            self.task.compute_dynamic_data(task_db)

    def test_configure_with_unknown_data(self) -> None:
        """Configure fails if a non-recognised key is in task_data."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"extra_field": "extra_value"})

    def test_configure_fails_with_missing_required_data(self) -> None:
        """Configure fails with missing required keys in task_data."""
        for key in ("input", "host_architecture"):
            with self.subTest(f"Configure with key {key} missing"):
                with self.assertRaises(TaskConfigError):
                    self.configure_task(remove=[key])

    def test_configure_fails_required_input(self) -> None:
        """Configure fails: missing source_artifact in task_data["input"]."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"input": {}})

    def test_configure_fails_extra_properties_input(self) -> None:
        """Configure fails: extra properties in task_data["input"]."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(
                override={
                    "input": {
                        "source_artifact": 5,
                        "source_environment": 4,
                        "source_artifact_url": "https://deb.debian.org/f.dsc",
                    }
                }
            )

    def test_configure_sets_default_values(self) -> None:
        """Optional task data have good default values."""
        self.configure_task(remove=["build_components"])

        self.assertEqual(self.task.data.build_components, ["any"])

    def test_configure_fails_with_bad_build_components(self) -> None:
        """Configure fails with invalid build components."""
        with self.assertRaises(TaskConfigError):
            self.configure_task(override={"build_components": ["foo", "any"]})

    def test_configure_fails_with_indep_binnmu(self) -> None:
        """Configure fails with a binnmu and indep build."""
        error_msg = "Cannot build architecture-independent packages in a binNMU"
        with self.assertRaisesRegex(TaskConfigError, error_msg):
            self.configure_task(
                override={
                    "build_components": ["any", "all"],
                    "binnmu": {"suffix": "+1", "changelog": "foo"},
                }
            )

    def test_fetch_input(self) -> None:
        """Test fetch_input: call fetch_artifact(artifact_id, directory)."""
        directory = self.create_temporary_directory()
        source_artifact = self.fake_debian_source_package_artifact()
        self.configure_task(override={"environment": 42})
        self.task.work_request_id = 5
        self.task.dynamic_data = SbuildDynamicData(
            environment_id=42, input_source_artifact_id=source_artifact.id
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = source_artifact

        def mock_executor_executor_instance() -> None:
            self.task.executor = MagicMock()
            self.task.executor.system_image = MagicMock(spec=ArtifactResponse)
            self.task.executor.system_image.data = {"codename": "bookworm"}

        with (
            mock.patch.object(
                self.task, "fetch_artifact", autospec=True, return_value=True
            ) as fetch_artifact_mocked,
            mock.patch.object(
                self.task,
                "_prepare_executor",
                autospec=True,
                side_effect=mock_executor_executor_instance,
            ) as prepare_executor_mocked,
        ):
            result = self.task.fetch_input(directory)

        self.assertTrue(result)
        fetch_artifact_mocked.assert_called_once_with(
            source_artifact.id, directory
        )
        prepare_executor_mocked.assert_called_once_with()

    def test_fetch_input_wrong_category(self) -> None:
        """Test fetch_input when input isn't a source package."""
        directory = self.create_temporary_directory()
        source_artifact = create_artifact_response(id=12)
        self.configure_task(
            override={
                "input": {
                    "source_artifact": source_artifact.id,
                },
            }
        )
        self.task.work_request_id = 5
        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=source_artifact.id
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
        self.assertIsNone(self.task._dsc_file)

    def test_fetch_input_no_dsc(self) -> None:
        """
        Test fetch_input on a source package that has no .dsc.

        We never expect this situation to arise in production, but it exercises
        the file list searching loop.
        """
        directory = self.create_temporary_directory()
        source_artifact = self.fake_debian_source_package_artifact()
        source_artifact.files["foo_1.0-1.tar"] = source_artifact.files.pop(
            "foo_1.0-1.dsc"
        )
        self.configure_task(
            override={
                "input": {
                    "source_artifact": source_artifact.id,
                },
            }
        )
        self.task.work_request_id = 5
        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=source_artifact.id
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = source_artifact

        with (
            mock.patch.object(
                self.task, "fetch_artifact", autospec=True, return_value=True
            ) as fetch_artifact_mocked,
            self.assertRaisesRegex(
                AssertionError, r"No \.dsc file found in source package\."
            ),
        ):
            self.task.fetch_input(directory)

        fetch_artifact_mocked.assert_not_called()

    def test_fetch_input_extra_packages(self) -> None:
        """Test fetch_input: downloads extra_binary_artifacts."""
        directory = self.create_temporary_directory()
        source_artifact = self.fake_debian_source_package_artifact()
        binary_artifact = self.fake_debian_binary_package_artifact()
        binary_upload_artifact = self.fake_debian_upload_artifact(
            source_package_name="bar",
            architecture="amd64",
            names=["bar_1.0-1_all.deb", "bar-doc_1.0-1_all.deb"],
        )
        self.configure_task(
            override={
                "input": {
                    "source_artifact": source_artifact.id,
                    "extra_binary_artifacts": [
                        binary_artifact.id,
                        binary_upload_artifact.id,
                    ],
                },
            }
        )
        self.task.work_request_id = 5
        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=source_artifact.id,
            input_extra_binary_artifacts_ids=[
                binary_artifact.id,
                binary_upload_artifact.id,
            ],
        )
        debusine_mock = self.mock_debusine()

        def artifact_get(artifact_id: int) -> ArtifactResponse:
            """Fake for debusine client artifact_get."""
            if artifact_id == source_artifact.id:
                return source_artifact
            elif artifact_id == binary_artifact.id:
                return binary_artifact
            elif artifact_id == binary_upload_artifact.id:
                return binary_upload_artifact
            raise AssertionError("Unknown artifact requested.")

        debusine_mock.artifact_get.side_effect = artifact_get

        def mock_executor_executor_instance() -> None:
            self.task.executor = MagicMock()
            self.task.executor.system_image = MagicMock(spec=ArtifactResponse)
            self.task.executor.system_image.data = {"codename": "bookworm"}

        with (
            mock.patch.object(
                self.task, "fetch_artifact", autospec=True, return_value=True
            ) as fetch_artifact_mocked,
            mock.patch.object(
                self.task,
                "_prepare_executor",
                autospec=True,
                side_effect=mock_executor_executor_instance,
            ) as prepare_executor_mocked,
        ):
            result = self.task.fetch_input(directory)

        self.assertTrue(result)
        self.assertEqual(
            self.task._extra_packages,
            [
                directory / "foo_1.0-1_all.deb",
                directory / "bar_1.0-1_all.deb",
                directory / "bar-doc_1.0-1_all.deb",
            ],
        )
        fetch_artifact_mocked.assert_has_calls(
            [
                call(source_artifact.id, directory),
                call(binary_artifact.id, directory),
            ]
        )
        prepare_executor_mocked.assert_called_once_with()

    def test_fetch_input_extra_binaries_wrong_category(self) -> None:
        """Test fetch_input: checks the categories of extra_binary_artifacts."""
        directory = self.create_temporary_directory()
        source_artifact = self.fake_debian_source_package_artifact()
        self.configure_task(
            override={
                "input": {
                    "source_artifact": source_artifact.id,
                    "extra_binary_artifacts": [source_artifact.id],
                },
            }
        )
        self.task.work_request_id = 5
        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=source_artifact.id,
            input_extra_binary_artifacts_ids=[source_artifact.id],
        )
        debusine_mock = self.mock_debusine()
        debusine_mock.artifact_get.return_value = source_artifact

        with mock.patch.object(
            self.task, "fetch_artifact", autospec=True, return_value=True
        ):
            result = self.task.fetch_input(directory)

        self.assertFalse(result)
        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name) / "fetch_input.log"
        ).read_text()
        self.assertEqual(
            log_file_contents,
            (
                "input.extra_binary_artifacts includes artifact 6, a "
                "debian:source-package, not the expected "
                "debian:binary-package, debian:binary-packages, or "
                "debian:upload.\n"
            ),
        )

    def test_environment_codename_executor(self) -> None:
        """Test _environment_codename gets the executor distribution."""
        self.configure_task(override={"environment": 42})
        self.task.dynamic_data = SbuildDynamicData(
            environment_id=42,
            input_source_artifact_id=1,
            input_extra_binary_artifacts_ids=[],
        )

        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)

        self.task._prepare_executor()
        self.assertEqual(self.task._environment_codename(), "bookworm")

    def test_environment_codename_executor_unconfigured(self) -> None:
        """Test _environment_codename raises ValueError without executor."""
        self.configure_task(override={"environment": 42})
        with self.assertRaisesRegex(
            ValueError,
            r"self\.executor not set, call _prepare_executor\(\) first",
        ):
            self.task._environment_codename()

    def test_cmdline_fixed_parameters(self) -> None:
        """Test fixed command line parameters."""
        self.configure_task()
        self.patch_executor()

        cmdline = self.task._cmdline()

        self.assertEqual(cmdline[0], "sbuild")
        self.assertEqual(cmdline[1], "--purge-deps=never")
        self.assertEqual(cmdline[2], "--no-run-lintian")

    def test_cmdline_contains_arch_parameter(self) -> None:
        """Ensure --arch parameter is computed from data."""
        self.configure_task(override={"host_architecture": "mipsel"})
        self.patch_executor()
        cmdline = self.task._cmdline()

        self.assertIn("--arch=mipsel", cmdline)

    def test_cmdline_contains_dist_parameter(self) -> None:
        """Ensure --dist parameter is computed from data."""
        self.configure_task()
        self.patch_executor(codename="jessie")

        cmdline = self.task._cmdline()

        self.assertIn("--dist=jessie", cmdline)

    def test_cmdline_extra_repositories_jessie(self) -> None:
        """Ensure the --extra-repository= parameter is passed to sbuild."""
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
        self.patch_executor(codename="jessie")
        env = self.task._cmd_env()
        self.assertIsNone(env)

        cmdline = self.task._cmdline()

        self.assertIn(
            "--extra-repository=deb http://example.com/ unstable main", cmdline
        )
        self.assertIn(
            "--extra-repository=deb http://example.net/ flat/", cmdline
        )

    def test_cmdline_extra_repositories_deb822_keyring(self) -> None:
        """Ensure the --pre-build-commands= parameter is passed to sbuild."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bullseye",
                        "components": ["main"],
                    },
                ],
            }
        )
        self.patch_executor(codename="bullseye")
        env = self.task._cmd_env()
        self.assertIsNone(env)

        self.task.extra_repository_sources = [
            Path("/tmp/extra_repository_0.sources")
        ]
        self.task.extra_repository_keys = [
            Path("/tmp/key.asc"),
        ]

        cmdline = self.task._cmdline()

        self.assertIn(
            "--pre-build-commands=cat /tmp/key.asc | "
            "%SBUILD_CHROOT_EXEC sh -c 'mkdir -p /etc/apt/keyrings && cat > "
            "/etc/apt/keyrings/key.asc'",
            cmdline,
        )
        self.assertIn(
            "--pre-build-commands=cat /tmp/extra_repository_0.sources | "
            "%SBUILD_CHROOT_EXEC sh -c 'cat > "
            "/etc/apt/sources.list.d/extra_repository_0.sources'",
            cmdline,
        )

    def test_cmdline_extra_repositories_deb822_inline(self) -> None:
        """Ensure the --pre-build-commands= parameter is passed to sbuild."""
        self.configure_task(
            override={
                "extra_repositories": [
                    {
                        "url": "http://example.com/",
                        "suite": "bookworm",
                        "components": ["main"],
                    },
                ],
            }
        )
        self.patch_executor(codename="bookworm")
        env = self.task._cmd_env()
        self.assertIsNone(env)

        self.task.extra_repository_sources = [
            Path("/tmp/extra_repository_0.sources")
        ]

        cmdline = self.task._cmdline()

        self.assertIn(
            "--pre-build-commands=cat /tmp/extra_repository_0.sources | "
            "%SBUILD_CHROOT_EXEC sh -c 'cat > "
            "/etc/apt/sources.list.d/extra_repository_0.sources'",
            cmdline,
        )

    def test_cmdline_binnmu(self) -> None:
        """Check binnmu command line."""
        self.configure_task(
            override={
                "binnmu": {
                    "changelog": "Rebuild for 64bit time_t",
                    "suffix": "+b1",
                },
                "build_components": ["any"],
            }
        )
        self.patch_executor(codename="jessie")
        cmdline = self.task._cmdline()
        self.assertIn("--make-binNMU=Rebuild for 64bit time_t", cmdline)
        self.assertIn("--append-to-version=+b1", cmdline)
        self.assertIn(
            "--maintainer=Debusine <noreply@debusine.example.com>", cmdline
        )

        self.configure_task(
            override={
                "binnmu": {
                    "changelog": "Rebuild for 64bit time_t",
                    "suffix": "+b1",
                    "timestamp": "2024-05-01T12:00:00Z",
                    "maintainer": "Foo Bar <foo@debian.org>",
                },
                "build_components": ["any"],
            }
        )
        self.patch_executor(codename="jessie")
        cmdline = self.task._cmdline()
        self.assertIn("--make-binNMU=Rebuild for 64bit time_t", cmdline)
        self.assertIn("--append-to-version=+b1", cmdline)
        self.assertIn("--binNMU=0", cmdline)
        self.assertIn(
            "--binNMU-timestamp=Wed, 01 May 2024 12:00:00 +0000", cmdline
        )
        self.assertIn("--maintainer=Foo Bar <foo@debian.org>", cmdline)

    def test_cmdline_no_build_profiles(self) -> None:
        """Check that missing build_profiles key is handled."""
        self.configure_task()
        self.patch_executor(codename="jessie")
        cmdline = self.task._cmdline()
        for argument in cmdline:
            self.assertFalse(argument.startswith("--profiles="))

    def test_cmdline_empty_build_profiles(self) -> None:
        """Check that empty build_profiles don't appear on the command line."""
        self.configure_task(override={"build_profiles": []})
        self.patch_executor(codename="jessie")
        cmdline = self.task._cmdline()
        for argument in cmdline:
            self.assertFalse(argument.startswith("--profiles="))

    def test_cmdline_build_profiles(self) -> None:
        """Check that build_profiles make it onto the command line."""
        self.configure_task(override={"build_profiles": ["nodoc", "nocheck"]})
        self.patch_executor(codename="jessie")
        cmdline = self.task._cmdline()
        self.assertIn("--profiles=nodoc,nocheck", cmdline)

    def test_cmd_env_no_build_profiles(self) -> None:
        """Check that environment isn't modified, without build_profiles."""
        self.configure_task(override={"build_profiles": ["cross"]})
        self.patch_executor(codename="jessie")
        env = self.task._cmd_env()
        self.assertIsNone(env)

    def test_cmd_env_build_profiles(self) -> None:
        """Check that build_profiles are specified in DEB_BUILD_OPTIONS."""
        self.configure_task(
            override={"build_profiles": ["nodoc", "nocheck", "cross"]}
        )
        self.patch_executor(codename="jessie")
        env = self.task._cmd_env()
        self.assertIsInstance(env, dict)
        assert env
        build_options = env["DEB_BUILD_OPTIONS"].split()
        self.assertIn("nodoc", build_options)
        self.assertIn("nocheck", build_options)
        self.assertNotIn("cross", build_options)

    def test_cmdline_translation_of_build_components(self) -> None:
        """Test handling of build components."""
        option_mapping = {
            "any": ["--arch-any", "--no-arch-any"],
            "all": ["--arch-all", "--no-arch-all"],
            "source": ["--source", "--no-source"],
        }
        keywords = option_mapping.keys()
        for combination in itertools.chain(
            itertools.combinations(keywords, 1),
            itertools.combinations(keywords, 2),
            itertools.combinations(keywords, 3),
        ):
            with self.subTest(f"Test build_components={combination}"):
                self.configure_task(
                    override={"build_components": list(combination)}
                )
                self.patch_executor()
                cmdline = self.task._cmdline()
                for key, value in option_mapping.items():
                    if key in combination:
                        self.assertIn(value[0], cmdline)
                        self.assertNotIn(value[1], cmdline)
                    else:
                        self.assertNotIn(value[0], cmdline)
                        self.assertIn(value[1], cmdline)

    def test_cmdline_contains_extra_packages(self) -> None:
        """Ensure cmdline has the arguments --extra-package=."""
        self.configure_task()
        self.patch_executor()
        self.task._extra_packages = [Path("/tmp/extra.deb")]
        cmdline = self.task._cmdline()
        self.assertIn("--extra-package=/tmp/extra.deb", cmdline)

    def test_cmdline_contains_executor_unshare_build_args(self) -> None:
        """Ensure cmdline builds correct arguments for unshare executor."""
        self.configure_task(
            override={
                "backend": "unshare",
                "environment": "debian/match:codename=bookworm",
            },
        )
        self.task.dynamic_data = SbuildDynamicData(
            environment_id=42,
            input_source_artifact_id=1,
            input_extra_binary_artifacts_ids=[],
        )

        debusine_mock = self.mock_debusine()
        self.mock_image_download(debusine_mock)

        self.task._prepare_executor()
        cmdline = self.task._cmdline()

        expected_args = [
            "--dist=bookworm",
            "--chroot-mode=unshare",
            f"--chroot={self.image_cache_path}/42/system.tar.xz",
        ]

        start_index = cmdline.index(expected_args[0])

        self.assertEqual(
            cmdline[start_index : start_index + len(expected_args)],
            expected_args,
        )

    def test_cmdline_uses_autopkgtest_virtserver_build_args(self) -> None:
        """Ensure cmdline supports autopkgtest-virt-server executors."""
        data = self.configuration("amd64", ["any"])
        data["backend"] = "incus-lxc"
        data["environment"] = 1
        self.configure_task(data)

        executor_mocked = self.patch_executor()

        autopkgtest_args = [
            "--abc=123",
            "--def=1%3",
        ]
        executor_mocked.autopkgtest_virt_args.return_value = autopkgtest_args
        executor_mocked.autopkgtest_virt_server.return_value = "fake"
        executor_mocked.system_image = self.fake_system_tarball_artifact()

        cmdline = self.task._cmdline()

        expected_args = [
            "--dist=bookworm",
            "--chroot-mode=autopkgtest",
            "--autopkgtest-virt-server=fake",
            "--autopkgtest-virt-server-opt=--abc=123",
            "--autopkgtest-virt-server-opt=--def=1%%3",
        ]

        start_index = cmdline.index(expected_args[0])

        self.assertEqual(
            cmdline[start_index : start_index + len(expected_args)],
            expected_args,
        )

    def test_execute_succeeds_backend_unshare(self) -> None:
        """execute() backend "unshare" succeeds."""
        self.configure_task(
            task_data=self.configuration("amd64", ["any"]),
            override={"backend": "unshare", "environment": 42},
        )
        self.task.work_request_id = 5
        self.patch_sbuild_debusine()

        self.run_execute(["true"])

    def patch_temporary_directory(self, directories: int = 1) -> list[Path]:
        """
        Patch sbuild.tempfile.TemporaryDirectory to return fixed directories.

        :param directories: number of directories that will be returned.
        :return: list with the Paths that have been created and will be
          returned.
        """
        temporary_directories = []
        for _ in range(0, directories):
            temporary_directories.append(self.create_temporary_directory())

        class MockedPathContextManager:
            def __init__(self, directory_paths: list[Path]) -> None:
                self.paths = iter(directory_paths)

            def __enter__(self) -> Path:
                return next(self.paths)

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: types.TracebackType | None,
            ) -> None:
                exc_type, exc_val, exc_tb  # fake usage for vulture
                pass

        patcher = mock.patch.object(
            self.task, "_temporary_directory", autospec=True
        )
        temporary_directory_mock = patcher.start()
        temporary_directory_mock.return_value = MockedPathContextManager(
            temporary_directories
        )
        self.addCleanup(patcher.stop)

        return temporary_directories

    def run_execute(
        self,
        cmdline: list[str],
    ) -> bool:
        """Run Sbuild.execute() mocking cmdline. Return execute()."""
        self.mock_cmdline(cmdline)

        temporary_directory, *_ = self.patch_temporary_directory(3)
        self.write_dsc_example_file(temporary_directory / "hello.dsc")
        (temporary_directory / "log.build").write_text("log file")

        self.patch_upload_artifacts()
        self.patch_upload_work_request_debug_logs()

        return self.task.execute()

    def test_execute_returns_true(self) -> None:
        """
        The execute method returns True when cmd returns 0.

        Also check that check_directory_for_consistency_errors() didn't
        return errors.

        Assert that EXECUTE_LOG file is created.
        """
        self.patch_sbuild_debusine()
        self.patch_fetch_input(return_value=True)
        self.patch_configure_for_execution(return_value=True)
        self.patch_task_succeeded(return_value=True)

        check_directory_for_consistency_errors_mocked = (
            self.patch_check_directory_for_consistency_errors()
        )
        check_directory_for_consistency_errors_mocked.return_value = []
        stdout_output = "stdout output of the command"
        stderr_output = "stderr output of the command"
        cmd = ["sh", "-c", f"echo {stdout_output}; echo {stderr_output} >&2"]
        cmd_quoted = (
            "sh -c 'echo stdout output of the command; "
            "echo stderr output of the command >&2'"
        )

        self.assertTrue(self.run_execute(cmd))

        expected = textwrap.dedent(
            f"""\
                cmd: {cmd_quoted}
                output (contains stdout and stderr):
                {stdout_output}
                {stderr_output}

                aborted: False
                returncode: 0

                Files in working directory:
                hello.dsc
                log.build
                {Sbuild.CMD_LOG_SEPARATOR}
                """
        )

        assert self.task._debug_log_files_directory
        # debug_log_file contains the expected text
        self.assertEqual(
            (
                Path(self.task._debug_log_files_directory.name)
                / self.task.CMD_LOG_FILENAME
            ).read_text(),
            expected,
        )

        check_directory_for_consistency_errors_mocked.assert_called()

    def test_execute_return_false_no_fetch_required_input_files(self) -> None:
        """execute() method returns False: cannot fetch required input files."""
        self.patch_fetch_input(return_value=False)
        self.assertFalse(self.run_execute(["true"]))

    def test_execute_check_build_consistency_error_returns_errors(self) -> None:
        """
        execute() method returns False: check_build_consistency returned errors.

        Assert check_build_consistency() was called.
        """
        self.patch_sbuild_debusine()
        self.patch_fetch_input(return_value=True)
        self.patch_configure_for_execution(return_value=True)

        check_directory_for_consistency_errors_mocked = (
            self.patch_check_directory_for_consistency_errors()
        )
        check_directory_for_consistency_errors_mocked.return_value = [
            "some error"
        ]

        self.assertFalse(self.run_execute(["true"]))

        check_directory_for_consistency_errors_mocked.assert_called()

    def test_execute_returns_false(self) -> None:
        """
        The execute method returns False when cmd returns 1.

        Assert check_build_consistency() was not called.
        """
        check_build_consistency_mocked = (
            self.patch_check_directory_for_consistency_errors()
        )

        self.patch_fetch_input(return_value=True)

        self.assertFalse(self.run_execute(["false"]))

        check_build_consistency_mocked.assert_not_called()

    def patch_sbuild_debusine(self) -> MagicMock:
        """Patch self.task.debusine. Return mock."""
        patcher = mock.patch.object(self.task, "debusine", autospec=Debusine)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked

    def patch_fetch_input(self, *, return_value: bool) -> MagicMock:
        """
        Patch self.task.debusine.fetch_input().

        :param return_value: set mocked.return_value = return_value
        :return: the mock
        """
        patcher = mock.patch.object(self.task, "fetch_input", autospec=True)
        mocked = patcher.start()
        mocked.return_value = return_value
        self.addCleanup(patcher.stop)

        return mocked

    def patch_configure_for_execution(self, *, return_value: bool) -> MagicMock:
        """
        Patch self.task.debusine.configure_for_execution().

        :param return_value: set mocked.return_value = return_value
        :return: the mock
        """
        patcher = mock.patch.object(
            self.task, "configure_for_execution", autospec=True
        )
        mocked = patcher.start()
        mocked.return_value = return_value
        self.addCleanup(patcher.stop)

        return mocked

    def patch_upload_artifacts(self) -> mock.Mock:
        """Patch self.task.upload_artifacts."""
        patcher = mock.patch.object(
            self.task, "upload_artifacts", autospec=True
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked

    def patch_upload_work_request_debug_logs(self) -> mock.Mock:
        """Patch self.task._upload_work_request_debug_logs."""
        patcher = mock.patch.object(
            self.task, "_upload_work_request_debug_logs", autospec=True
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)

        return mocked

    def patch_executor(self, codename: str = "bookworm") -> mock.Mock:
        """
        Patch self.task._executor.

        :param codename: Provide a mock system image with this codename.
        """
        patcher = mock.patch.object(
            self.task, "executor", autospec=ExecutorInterface
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        assert self.task.executor is not None
        self.task.executor.system_image = MagicMock(spec=ArtifactResponse)
        self.task.executor.system_image.data = {"codename": codename}
        return mocked

    def patch_task_succeeded(self, *, return_value: bool) -> mock.Mock:
        """
        Patch self.task.task_succeeded.

        :param return_value: set mocked.return_value = return_value
        :return: the mock
        """
        patcher = mock.patch.object(self.task, "task_succeeded", autospec=True)
        mocked = patcher.start()
        mocked.return_value = return_value
        self.addCleanup(patcher.stop)

        return mocked

    def patch_check_directory_for_consistency_errors(self) -> mock.Mock:
        """Patch self.task.check_directory_for_consistency_errors."""
        patcher = mock.patch.object(
            self.task, "check_directory_for_consistency_errors", autospec=True
        )
        mocker = patcher.start()
        self.addCleanup(patcher.stop)

        return mocker

    def test_execute_call_upload_artifacts(self) -> None:
        """
        execute() call methods to upload artifacts.

        It calls: upload_artifacts() and _upload_work_request_debug_logs().
        """
        self.configure_task()

        self.mock_cmdline(["true"])

        self.patch_sbuild_debusine()
        self.patch_fetch_input(return_value=True)
        self.patch_configure_for_execution(return_value=True)
        self.patch_task_succeeded(return_value=True)

        execute_directory, download_directory = self.patch_temporary_directory(
            2
        )

        upload_artifacts_mocked = self.patch_upload_artifacts()
        self.patch_check_directory_for_consistency_errors().return_value = []

        upload_work_request_debug_logs_patcher = mock.patch.object(
            self.task, "_upload_work_request_debug_logs", autospec=True
        )
        upload_work_request_debug_logs_mocked = (
            upload_work_request_debug_logs_patcher.start()
        )
        self.addCleanup(upload_work_request_debug_logs_patcher.stop)

        self.task.configure_for_execution(download_directory)
        self.task.execute()

        self.assertEqual(upload_artifacts_mocked.call_count, 1)
        upload_artifacts_mocked.assert_called_with(
            execute_directory, execution_success=True
        )

        self.assertEqual(upload_work_request_debug_logs_mocked.call_count, 1)
        upload_work_request_debug_logs_mocked.assert_called_with()

    def test_execute_run_cmd_with_capture_stdout(self) -> None:
        """execute() call run_cmd with capture_stdout."""
        self.task.CAPTURE_OUTPUT_FILENAME = "cmd.out"

        self.patch_fetch_input(return_value=True)
        run_cmd_mock = self.patch_run_cmd()
        run_cmd_mock.return_value = False

        self.patch_configure_for_execution(return_value=True)
        self.patch_upload_artifacts()
        self.patch_check_directory_for_consistency_errors().return_value = []
        self.patch_upload_work_request_debug_logs()
        self.patch_executor()

        self.task.execute()

        # run_cmd was ran with:
        # capture_stdout_filename=self.task.CAPTURE_OUTPUT_FILENAME)
        self.assertEqual(
            run_cmd_mock.call_args.kwargs["capture_stdout_filename"],
            self.task.CAPTURE_OUTPUT_FILENAME,
        )

    def test_execute_does_not_run_cmd(self) -> None:
        """execute(), no call run_cmd(): configure_for_execution() failed."""
        self.configure_task()

        self.patch_sbuild_debusine()
        self.patch_fetch_input(return_value=True)

        temporary_directory, *_ = self.patch_temporary_directory(3)

        patcher_configure = mock.patch.object(
            self.task, "configure_for_execution"
        )
        configure_for_execution_mocked = patcher_configure.start()
        configure_for_execution_mocked.return_value = False
        self.addCleanup(patcher_configure.stop)

        run_cmd = self.patch_run_cmd()

        self.assertFalse(self.task.execute())

        run_cmd.assert_not_called()

    def test_analyze_worker(self) -> None:
        """Test the analyze_worker() method."""
        self.mock_is_command_available({"sbuild": True})

        metadata = self.task.analyze_worker()

        self.assertEqual(metadata["sbuild:available"], True)

    def test_analyze_worker_sbuild_not_available(self) -> None:
        """analyze_worker() handles sbuild not being available."""
        self.mock_is_command_available({"sbuild": False})

        metadata = self.task.analyze_worker()

        self.assertEqual(metadata["sbuild:available"], False)

    def worker_metadata(self) -> dict[str, Any]:
        """Return worker_metadata with sbuild:version=self.task.TASK_VERSION."""
        return {
            "system:worker_type": WorkerType.EXTERNAL,
            "system:architectures": ["amd64"],
            "system:host_architecture": "amd64",
            "sbuild:version": self.task.TASK_VERSION,
            "sbuild:available": True,
        }

    def test_can_run_mismatched_task_version(self) -> None:
        """Ensure can_run_on returns False for mismatched versions."""
        worker_metadata = self.worker_metadata()
        worker_metadata["sbuild:version"] += 1
        self.configure_task()

        self.assertFalse(self.task.can_run_on(worker_metadata))

    def test_can_run_on_sbuild_not_available(self) -> None:
        """can_run_on returns False if sbuild is not available."""
        worker_metadata = self.worker_metadata()
        worker_metadata["sbuild:available"] = False
        self.configure_task()

        self.assertFalse(self.task.can_run_on(worker_metadata))

    def test_can_run_return_true_unshare(self) -> None:
        """can_run_on returns True: using unshare."""
        worker_metadata = self.worker_metadata()
        worker_metadata["executor:unshare:available"] = True
        self.configure_task(override={"backend": "unshare", "environment": 42})

        self.assertTrue(self.task.can_run_on(worker_metadata))

    def test_can_run_on_unshare_requires_unshare(self) -> None:
        """can_run_on returns False: missing unshare executor."""
        worker_metadata = self.worker_metadata()
        self.configure_task(override={"backend": "unshare", "environment": 42})

        self.assertFalse(self.task.can_run_on(worker_metadata))

    def test_can_run_on_incus(self) -> None:
        """can_run_on returns True: with incus and autopkgtest."""
        worker_metadata = self.worker_metadata()
        worker_metadata["executor:incus-lxc:available"] = True
        worker_metadata["autopkgtest:available"] = True
        self.configure_task(
            override={"backend": "incus-lxc", "environment": 42}
        )

        self.assertTrue(self.task.can_run_on(worker_metadata))

    def test_can_run_on_incus_requires_autopkgtest(self) -> None:
        """can_run_on returns False: without autopkgtest."""
        worker_metadata = self.worker_metadata()
        worker_metadata["executor:incus-lxc:available"] = True
        self.configure_task(
            override={"backend": "incus-lxc", "environment": 42}
        )

        self.assertFalse(self.task.can_run_on(worker_metadata))

    def test_task_succeeded_returncode_aborted(self) -> None:
        """task_succeeded returns False if sbuild was aborted."""
        directory = self.create_temporary_directory()

        self.assertFalse(self.task.task_succeeded(None, directory))

    def test_task_succeeded_returncode_non_zero(self) -> None:
        """task_succeeded returns False if sbuild returns non-zero."""
        directory = self.create_temporary_directory()

        self.assertFalse(self.task.task_succeeded(1, directory))

    def test_task_succeeded_status_skipped(self) -> None:
        """task_succeeded returns False for "Status: skipped"."""
        directory = self.create_temporary_directory()
        (directory / "log.build").write_text(
            textwrap.dedent(
                """\
                Build log output; next line is from the build itself
                Status: successful
                ...
                Status: skipped
                Finished
                """
            )
        )

        self.assertFalse(self.task.task_succeeded(0, directory))

    def test_task_succeeded_status_successful(self) -> None:
        """task_succeeded returns True for "Status: successful"."""
        directory = self.create_temporary_directory()
        (directory / "log.build").write_text(
            textwrap.dedent(
                """\
                Build log output; next line is from the build itself
                Status: skipped
                ...
                Status: successful
                Finished
                """
            )
        )

        self.assertTrue(self.task.task_succeeded(0, directory))

    def test_upload_artifacts_no_deb_files(self) -> None:
        """upload_artifacts() does not try to upload deb files."""
        temp_directory = self.create_temporary_directory()

        debusine_mock = self.patch_sbuild_debusine()

        # Add log.build file: the PackageBuildLog is created
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        dsc = self.write_dsc_example_file(self.task._dsc_file)
        (log_file := temp_directory / "log.build").write_text("log file")

        # Add .changes file: Upload is created
        changes_file = temp_directory / "python-network.changes"

        (file1 := temp_directory / "package.deb").write_text("test")

        self.write_changes_file(changes_file, [file1])

        self.task.upload_artifacts(temp_directory, execution_success=True)

        self.assertEqual(debusine_mock.upload_artifact.call_count, 3)

        debusine_mock.upload_artifact.assert_has_calls(
            [
                call(
                    PackageBuildLog.create(
                        file=log_file,
                        source=dsc["Source"],
                        version=dsc["Version"],
                    ),
                    workspace=self.task.workspace_name,
                    work_request=None,
                ),
                call(
                    Upload.create(
                        changes_file=changes_file, exclude_files=set()
                    ),
                    workspace=self.task.workspace_name,
                    work_request=None,
                ),
                call(
                    SigningInputArtifact.create([changes_file], temp_directory),
                    workspace=self.task.workspace_name,
                    work_request=None,
                ),
            ]
        )

    def test_upload_artifacts_build_failure(self) -> None:
        """upload_artifacts() uploads only the .build file: build failed."""
        temp_directory = self.create_temporary_directory()

        debusine_mock = self.patch_sbuild_debusine()

        # Add log.build file
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        dsc = self.write_dsc_example_file(self.task._dsc_file)
        (log_file := temp_directory / "log.build").write_text("log file")

        self.task.work_request_id = 5

        self.task.upload_artifacts(temp_directory, execution_success=False)

        # Only one artifact is uploaded: PackageBuildLog
        # (because execution_success=False)
        self.assertEqual(debusine_mock.upload_artifact.call_count, 1)

        debusine_mock.upload_artifact.assert_has_calls(
            [
                call(
                    PackageBuildLog.create(
                        file=log_file,
                        source=dsc["Source"],
                        version=dsc["Version"],
                    ),
                    workspace=self.task.workspace_name,
                    work_request=self.task.work_request_id,
                )
            ]
        )

    def test_extract_dose3_on_failure(self) -> None:
        """upload_artifacts() scans logs for dose3 explanations on failures."""
        temp_directory = self.create_temporary_directory()
        debusine_mock = self.patch_sbuild_debusine()
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        dsc = self.write_dsc_example_file(self.task._dsc_file)
        (log_file := temp_directory / "log.build").write_text(
            """
(I)Dose_applications: Solving...
output-version: 1.2
native-architecture: amd64
report:
 -
  package: foo
  version: 1.2-3
  status: broken
  reasons: []
background-packages: 1
foreground-packages: 2
total-packages: 3
broken-packages: 4
+------------------------------------------------------------------------------+
"""
        )

        self.task.upload_artifacts(temp_directory, execution_success=False)

        debusine_mock.upload_artifact.assert_has_calls(
            [
                call(
                    PackageBuildLog.create(
                        file=log_file,
                        source=dsc["Source"],
                        version=dsc["Version"],
                        bd_uninstallable=DoseDistCheck.parse_obj(
                            {
                                "output-version": "1.2",
                                "native-architecture": "amd64",
                                "report": [
                                    {
                                        "package": "foo",
                                        "version": "1.2-3",
                                        "status": "broken",
                                        "reasons": [],
                                    }
                                ],
                                "background-packages": 1,
                                "foreground-packages": 2,
                                "total-packages": 3,
                                "broken-packages": 4,
                            }
                        ),
                    ),
                    workspace=self.task.workspace_name,
                    work_request=self.task.work_request_id,
                )
            ]
        )

    def test_skip_dose3_extraction_on_success(self) -> None:
        """upload_artifacts() doesn't scan logs on success."""
        temp_directory = self.create_temporary_directory()
        self.patch_sbuild_debusine()
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        self.write_dsc_example_file(self.task._dsc_file)
        (temp_directory / "log.build").write_text("log file")

        with mock.patch.object(
            self.task,
            "_extract_dose3_explanation",
            autospec=True,
            return_value=None,
        ) as dose3_extract_mock:
            self.task.upload_artifacts(temp_directory, execution_success=True)

        dose3_extract_mock.assert_not_called()

    def test_extract_dose3_explanation(self) -> None:
        """Extract dose3 explanation from build log."""
        # missing
        temp_directory = self.create_temporary_directory()
        (log_file := temp_directory / "log.build").write_text(
            """
Setting up sbuild-build-depends-dose3-dummy (0.invalid.0) ...
(I)Doseparse: Parsing and normalizing...
(I)Dose_deb: Parsing Packages file -...
(I)Dose_common: total packages 66138
(I)Dose_applications: Cudf Universe: 66138 packages
(I)Dose_applications: --checkonly specified, consider all packages as background packages
(I)Dose_applications: Solving...
output-version: 1.2
native-architecture: amd64
report:
 -
  package: sbuild-build-depends-main-dummy
  version: 0.invalid.0
  architecture: amd64
  status: broken
  reasons:
   -
    missing:
     pkg:
      package: sbuild-build-depends-main-dummy
      version: 0.invalid.0
      architecture: amd64
      unsat-dependency: debhelper-compat:amd64 (= 14) | debhelper-compat:amd64 (= 14)

background-packages: 66137
foreground-packages: 1
total-packages: 66138
broken-packages: 1

+------------------------------------------------------------------------------+
| Cleanup                                                                      |
+------------------------------------------------------------------------------+

"""  # noqa: E501, W293
        )
        self.assertEqual(
            self.task._extract_dose3_explanation(log_file),
            DoseDistCheck.parse_obj(
                {
                    'output-version': '1.2',
                    'native-architecture': 'amd64',
                    'report': [
                        {
                            'package': 'sbuild-build-depends-main-dummy',
                            'version': '0.invalid.0',
                            'architecture': 'amd64',
                            'status': 'broken',
                            'reasons': [
                                {
                                    'missing': {
                                        'pkg': {
                                            'package': 'sbuild-build-depends-main-dummy',  # noqa: E501
                                            'version': '0.invalid.0',
                                            'architecture': 'amd64',
                                            'unsat-dependency': 'debhelper-compat:amd64 (= 14) | debhelper-compat:amd64 (= 14)',  # noqa: E501
                                        }
                                    }
                                }
                            ],
                        }
                    ],
                    'background-packages': 66137,
                    'foreground-packages': 1,
                    'total-packages': 66138,
                    'broken-packages': 1,
                }
            ),
        )

        # conflict
        temp_directory = self.create_temporary_directory()
        (log_file := temp_directory / "log.build").write_text(
            """
Setting up sbuild-build-depends-dose3-dummy (0.invalid.0) ...
(I)Doseparse: Parsing and normalizing...
(I)Dose_deb: Parsing Packages file -...
(I)Dose_common: total packages 66081
(I)Dose_applications: Cudf Universe: 66081 packages
(I)Dose_applications: --checkonly specified, consider all packages as background packages
(I)Dose_applications: Solving...
output-version: 1.2
native-architecture: amd64
report:
 -
  package: sbuild-build-depends-main-dummy
  version: 0.invalid.0
  architecture: amd64
  status: broken
  reasons:
   -
    conflict:
     pkg1:
      package: systemd-sysv
      version: 256-1
      architecture: amd64
      unsat-conflict: sysvinit-core:amd64
     pkg2:
      package: sysvinit-core
      version: 3.09-2
      architecture: amd64
     depchain1:
      -
       depchain:
        -
         package: sbuild-build-depends-main-dummy
         version: 0.invalid.0
         architecture: amd64
         depends: systemd-sysv:amd64
     depchain2:
      -
       depchain:
        -
         package: sbuild-build-depends-main-dummy
         version: 0.invalid.0
         architecture: amd64
         depends: sysvinit-core:amd64

background-packages: 66080
foreground-packages: 1
total-packages: 66081
broken-packages: 1

+------------------------------------------------------------------------------+
| Cleanup                                                                      |
+------------------------------------------------------------------------------+
"""  # noqa: E501, W293
        )

        self.assertEqual(
            self.task._extract_dose3_explanation(log_file),
            DoseDistCheck.parse_obj(
                {
                    'output-version': '1.2',
                    'native-architecture': 'amd64',
                    'report': [
                        {
                            'package': 'sbuild-build-depends-main-dummy',
                            'version': '0.invalid.0',
                            'architecture': 'amd64',
                            'status': 'broken',
                            'reasons': [
                                {
                                    'conflict': {
                                        'pkg1': {
                                            'package': 'systemd-sysv',
                                            'version': '256-1',
                                            'architecture': 'amd64',
                                            'unsat-conflict': 'sysvinit-core:amd64',  # noqa: E501
                                        },
                                        'pkg2': {
                                            'package': 'sysvinit-core',
                                            'version': '3.09-2',
                                            'architecture': 'amd64',
                                        },
                                        'depchain1': [
                                            {
                                                'depchain': [
                                                    {
                                                        'package': 'sbuild-build-depends-main-dummy',  # noqa: E501
                                                        'version': '0.invalid.0',  # noqa: E501
                                                        'architecture': 'amd64',
                                                        'depends': 'systemd-sysv:amd64',  # noqa: E501
                                                    }
                                                ]
                                            }
                                        ],
                                        'depchain2': [
                                            {
                                                'depchain': [
                                                    {
                                                        'package': 'sbuild-build-depends-main-dummy',  # noqa: E501
                                                        'version': '0.invalid.0',  # noqa: E501
                                                        'architecture': 'amd64',
                                                        'depends': 'sysvinit-core:amd64',  # noqa: E501
                                                    }
                                                ]
                                            }
                                        ],
                                    }
                                }
                            ],
                        }
                    ],
                    'background-packages': 66080,
                    'foreground-packages': 1,
                    'total-packages': 66081,
                    'broken-packages': 1,
                }
            ),
        )

        # malformed dose3 yaml output (package names, versions)
        temp_directory = self.create_temporary_directory()
        (log_file := temp_directory / "log.build").write_text(
            """
(I)Dose_applications: Solving...
output-version: 1.2
report:
 -
  package: 0xffff
  version: 0.6e-7
  status: broken
  reasons: []
background-packages: 1
foreground-packages: 2
total-packages: 3
broken-packages: 4
+------------------------------------------------------------------------------+
"""  # noqa: E501, W293
        )
        self.assertEqual(
            self.task._extract_dose3_explanation(log_file),
            DoseDistCheck.parse_obj(
                {
                    'output-version': '1.2',
                    'report': [
                        {
                            'package': '0xffff',  # not: 65535
                            'version': '0.6e-7',  # not: 6.0e-08
                            'status': 'broken',
                            'reasons': [],
                        }
                    ],
                    'background-packages': 1,
                    'foreground-packages': 2,
                    'total-packages': 3,
                    'broken-packages': 4,
                }
            ),
        )

        # Unsupported - no exception
        temp_directory = self.create_temporary_directory()
        (log_file := temp_directory / "log.build").write_text(
            """
(I)Dose_applications: Solving...
output-version: 1.2
total-packages: 6392
broken-packages: 2
missing-packages: 2
conflict-packages: 0
unique-missing-packages: 0
unique-conflict-packages: 0
unique-self-conflicting-packages: 0
conflict-missing-ratio:
 0-1: 2
summary:
"""
        )
        self.assertIsNone(self.task._extract_dose3_explanation(log_file))

    def test_extract_dose3_invalid_utf8(self) -> None:
        """Build logs aren't necessarily valid UTF-8."""
        temp_directory = self.create_temporary_directory()
        # ISO-8559-1 encoded message from LaTeX followed by valid dose3 output
        (log_file := temp_directory / "log.build").write_bytes(
            b"""
There is no \xb2 in
(I)Dose_applications: Solving...
output-version: 1.2
native-architecture: amd64
report:
 -
  package: foo
  version: 1.2-3
  status: broken
  reasons: []
background-packages: 1
foreground-packages: 2
total-packages: 3
broken-packages: 4
+------------------------------------------------------------------------------+
"""
        )
        self.assertIsNotNone(self.task._extract_dose3_explanation(log_file))

    def test_upload_validation_errors(self) -> None:
        """upload_artifacts() does not try to upload deb files."""
        build_directory = self.create_temporary_directory()

        debusine_mock = self.patch_sbuild_debusine()

        # Add log.build file: the PackageBuildLog is created
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        dsc_data = self.write_dsc_example_file(self.task._dsc_file)

        (build_file := build_directory / "file.build").write_text("the build")

        # Add .changes file: Upload is created
        changes_file = build_directory / "python-network.changes"

        # Add *.deb file
        (file1 := build_directory / "package.deb").write_text("test")
        self.write_changes_file(changes_file, [file1])

        file1.write_text("Hash will be unexpected")

        validation_failed_log_remote_id = 25
        debusine_mock.upload_artifact.return_value = RemoteArtifact(
            id=validation_failed_log_remote_id, workspace="Test"
        )

        # Upload the artifacts
        self.task.upload_artifacts(build_directory, execution_success=True)

        source = dsc_data["Source"]
        version = dsc_data["Version"]

        calls = [
            call(
                PackageBuildLog.create(
                    file=build_file, source=source, version=version
                ),
                workspace=self.task.workspace_name,
                work_request=None,
            ),
            call(
                Upload.create(changes_file=changes_file, exclude_files=set()),
                workspace=self.task.workspace_name,
                work_request=None,
            ),
            call(
                SigningInputArtifact.create([changes_file], build_directory),
                workspace=self.task.workspace_name,
                work_request=None,
            ),
        ]

        debusine_mock.upload_artifact.assert_has_calls(calls)
        self.assertEqual(debusine_mock.upload_artifact.call_count, len(calls))

    def test_configure_for_execution_success(self) -> None:
        """configure_for_execution() succeeds if self._dsc_file is set."""
        download_directory = self.create_temporary_directory()
        self.task._dsc_file = Path(download_directory / "foo.dsc")
        self.task._dsc_file.write_text("hello!")

        self.assertTrue(self.task.configure_for_execution(download_directory))

    def test_configure_for_execution_requires_dsc_file_set(self) -> None:
        """configure_for_execution() requires self._dsc_file to be set."""
        download_directory = self.create_temporary_directory()

        self.assertFalse(self.task.configure_for_execution(download_directory))

        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()

        self.assertEqual(log_file_contents, "Input source package not found.\n")

    def test_configure_for_execution_requires_dsc_file_exists(self) -> None:
        """configure_for_execution() requires self._dsc_file to exist."""
        download_directory = self.create_temporary_directory()
        self.task._dsc_file = download_directory / "foo.dsc"

        self.assertFalse(self.task.configure_for_execution(download_directory))

        assert self.task._debug_log_files_directory
        log_file_contents = (
            Path(self.task._debug_log_files_directory.name)
            / "configure_for_execution.log"
        ).read_text()

        self.assertEqual(log_file_contents, "Input source package not found.\n")

    def patch_run_cmd(self) -> MagicMock:
        """Patch self.task.run_cmd. Return its mock."""
        patcher_build = mock.patch.object(self.task, "run_cmd")
        mocked = patcher_build.start()
        self.addCleanup(patcher_build.stop)

        return mocked

    def test_check_directory_for_consistency_errors(self) -> None:
        """The build directory is checked for consistency errors."""

        def populate_sbuild_directory(
            cmd: list[str], working_directory: Path, **kwargs: Any  # noqa: U100
        ) -> int:
            (dsc := working_directory / "hello.dsc").touch()
            self.write_changes_file(working_directory / "hello.changes", [dsc])
            dsc.unlink()
            return 0

        self.task.CAPTURE_OUTPUT_FILENAME = "cmd.out"
        self.patch_fetch_input(return_value=True)
        run_cmd_mock = self.patch_run_cmd()
        run_cmd_mock.side_effect = populate_sbuild_directory
        self.patch_configure_for_execution(return_value=True)
        self.patch_upload_work_request_debug_logs()
        self.patch_executor()
        self.assertFalse(self.task.execute())

    def patch_upload_package_build_log(self) -> MagicMock:
        """Patch self.task._upload_package_build_log. Return its mock."""
        patcher = mock.patch.object(
            self.task, "_upload_package_build_log", autospec=True
        )
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def test_upload_artifacts_create_relation_build_log_to_source(self) -> None:
        """upload_artifacts() create relation from build log to source."""
        temp_directory = self.create_temporary_directory()

        # Add log.build file
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        self.write_dsc_example_file(self.task._dsc_file)

        source_artifact_id = 8
        self.task._source_artifacts_ids = [source_artifact_id]

        upload_package_build_log_mocked = self.patch_upload_package_build_log()
        upload_package_build_log_mocked.return_value = RemoteArtifact(
            id=7, workspace="not-relevant"
        )

        (temp_directory / "log.build").write_text("the log file")

        debusine_mock = self.patch_sbuild_debusine()

        self.task.upload_artifacts(temp_directory, execution_success=False)

        debusine_mock.relation_create.assert_called_with(
            upload_package_build_log_mocked.return_value.id,
            source_artifact_id,
            "relates-to",
        )

    def test_upload_artifacts_search_for_dsc_file(self) -> None:
        """
        upload_artifacts() is called with _dsc_file already set.

        The .dsc file was set by configure_for_execution().
        """
        build_directory = self.create_temporary_directory()
        dsc_file = self.create_temporary_file(suffix=".dsc")

        self.patch_sbuild_debusine()

        self.task._dsc_file = dsc_file

        dsc_data = self.write_dsc_example_file(dsc_file)

        upload_package_build_log_mocked = self.patch_upload_package_build_log()

        self.task.upload_artifacts(build_directory, execution_success=False)

        upload_package_build_log_mocked.assert_called_with(
            build_directory,
            dsc_data["Source"],
            dsc_data["Version"],
            execution_success=False,
        )

    def test_upload_artifacts_no_dsc_file(self) -> None:
        """upload_artifacts() called without an existing .dsc file."""
        build_directory = self.create_temporary_directory()

        debusine_mock = self.patch_sbuild_debusine()

        self.task.upload_artifacts(build_directory, execution_success=False)

        debusine_mock.upload_artifact.assert_not_called()

    def test_upload_artifacts_no_build_log(self) -> None:
        """upload_artifacts() called without an existing build log."""
        build_directory = self.create_temporary_directory()
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        self.write_dsc_example_file(self.task._dsc_file)
        debusine_mock = self.patch_sbuild_debusine()

        self.task.upload_artifacts(build_directory, execution_success=False)

        debusine_mock.upload_artifact.assert_not_called()

    def test_upload_artifacts_deb_but_no_changes(self) -> None:
        """upload_artifacts() called with a .deb but no .changes file."""
        build_directory = self.create_temporary_directory()
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")
        dsc_data = self.write_dsc_example_file(self.task._dsc_file)
        self.write_deb_file(build_directory / "hello_1.0_amd64.deb")
        debusine_mock = self.patch_sbuild_debusine()
        self.task.work_request_id = 8

        self.task.upload_artifacts(build_directory, execution_success=True)

        workspace_name = self.task.workspace_name
        expected_upload_artifact_calls = [
            call(
                BinaryPackage.create(
                    file=build_directory / "hello_1.0_amd64.deb",
                ),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
            call(
                BinaryPackages.create(
                    srcpkg_name=dsc_data["Source"],
                    srcpkg_version=dsc_data["Version"],
                    version=dsc_data["Version"],
                    architecture="amd64",
                    files=[build_directory / "hello_1.0_amd64.deb"],
                ),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
        ]
        self.assertEqual(
            debusine_mock.upload_artifact.call_count,
            len(expected_upload_artifact_calls),
        )
        debusine_mock.upload_artifact.assert_has_calls(
            expected_upload_artifact_calls
        )

    def test_upload_artifacts(self) -> None:
        """upload_artifacts() call upload_artifact() with the artifacts."""
        temp_directory = self.create_temporary_directory()

        # Add log.build file: the PackageBuildLog is uploaded
        self.task._dsc_file = self.create_temporary_file(suffix=".dsc")

        dsc_data = self.write_dsc_example_file(self.task._dsc_file)

        (build_log_file := temp_directory / "log.build").write_text(
            "the log file"
        )

        debusine_mock = self.patch_sbuild_debusine()

        # Add two files: BinaryPackages is uploaded with them
        self.write_deb_file(file1 := temp_directory / "hello_1.0_amd64.deb")
        self.write_deb_file(file2 := temp_directory / "hello_1.0_amd64.udeb")

        # Add .changes file: Upload is uploaded
        changes_file = temp_directory / "python-network.changes"
        self.write_changes_file(changes_file, [file1, file2])

        self.task.work_request_id = 8

        self.task.upload_artifacts(temp_directory, execution_success=True)

        workspace_name = self.task.workspace_name

        expected_upload_artifact_calls = [
            call(
                PackageBuildLog.create(
                    file=build_log_file,
                    source=dsc_data["Source"],
                    version=dsc_data["Version"],
                ),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
            call(
                BinaryPackage.create(file=file1),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
            call(
                BinaryPackage.create(file=file2),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
            call(
                BinaryPackages.create(
                    srcpkg_name=dsc_data["Source"],
                    srcpkg_version=dsc_data["Version"],
                    version=dsc_data["Version"],
                    architecture="amd64",
                    files=[file1, file2],
                ),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
            call(
                Upload.create(
                    changes_file=changes_file,
                ),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
            call(
                SigningInputArtifact.create([changes_file], temp_directory),
                workspace=workspace_name,
                work_request=self.task.work_request_id,
            ),
        ]
        self.assertEqual(
            debusine_mock.upload_artifact.call_count,
            len(expected_upload_artifact_calls),
        )
        debusine_mock.upload_artifact.assert_has_calls(
            expected_upload_artifact_calls
        )

        # Ensure that _upload_artifacts tried to create relations
        # (the exact creation of relations is tested in
        # test_create_relations).
        self.assertGreaterEqual(debusine_mock.relation_create.call_count, 1)

    def create_remote_binary_packages_relations(
        self,
    ) -> dict[str, RemoteArtifact]:
        """
        Create RemoteArtifacts and call Sbuild method to create relations.

        Call self.task._create_remote_binary_packages_relations().

        :return: dictionary with the remote artifacts.
        """  # noqa: D402
        workspace = "debian"
        artifacts = {
            "build_log": RemoteArtifact(id=1, workspace=workspace),
            "binary_upload": RemoteArtifact(id=2, workspace=workspace),
            "binary_package": RemoteArtifact(id=3, workspace=workspace),
            "signing_input": RemoteArtifact(id=4, workspace=workspace),
        }
        self.task._create_remote_binary_packages_relations(
            artifacts["build_log"],
            artifacts["binary_upload"],
            [artifacts["binary_package"]],
            artifacts["signing_input"],
        )

        return artifacts

    def assert_create_remote_binary_packages_relations(
        self,
        debusine_mock: MagicMock,
        artifacts: dict[str, RemoteArtifact],
        *,
        source_artifacts_ids: list[int],
    ) -> None:
        """Assert that debusine_mock.relation_create was called correctly."""
        expected_relation_create_calls = [
            call(
                artifacts["build_log"].id,
                artifacts["binary_package"].id,
                "relates-to",
            ),
            call(
                artifacts["binary_upload"].id,
                artifacts["binary_package"].id,
                "extends",
            ),
            call(
                artifacts["binary_upload"].id,
                artifacts["binary_package"].id,
                "relates-to",
            ),
            call(
                artifacts["signing_input"].id,
                artifacts["binary_upload"].id,
                "relates-to",
            ),
        ]

        for source_artifact_id in source_artifacts_ids:
            expected_relation_create_calls.append(
                call(
                    artifacts["binary_package"].id,
                    source_artifact_id,
                    "built-using",
                )
            )

        self.assertEqual(
            debusine_mock.relation_create.call_count,
            len(expected_relation_create_calls),
        )

        debusine_mock.relation_create.assert_has_calls(
            expected_relation_create_calls, any_order=True
        )

    def test_create_relations(self) -> None:
        """create_relations() call relation_create()."""
        cases: list[list[int]] = [[], [9, 10]]
        for source_artifacts_ids in cases:
            with self.subTest(source_artifacts_ids=source_artifacts_ids):
                debusine_mock = self.patch_sbuild_debusine()

                self.task._source_artifacts_ids = source_artifacts_ids

                artifacts = self.create_remote_binary_packages_relations()

                self.assert_create_remote_binary_packages_relations(
                    debusine_mock,
                    artifacts,
                    source_artifacts_ids=source_artifacts_ids,
                )

    @staticmethod
    def configuration(
        host_architecture: str, build_components: list[str]
    ) -> dict[str, Any]:
        """Return configuration with host_architecture and build_components."""
        return {
            "input": {
                "source_artifact": 5,
            },
            "environment": "debian/match:codename=bullseye",
            "host_architecture": host_architecture,
            "build_components": build_components,
        }

    def create_deb_udeb_files(
        self, directory: Path, *, architecture: str
    ) -> None:
        """Create deb and udeb files for architecture."""
        self.write_deb_file(directory / f"hello_1.0_{architecture}.deb")
        self.write_deb_file(directory / f"hello_1.0_{architecture}.udeb")

    def create_and_upload_binary_packages(
        self,
        host_architecture: str,
        build_components: list[str],
        *,
        create_debs_for_architectures: list[str],
    ) -> tuple[MagicMock, Path]:
        """
        Set environment and call Sbuild._upload_binary_packages().

        Return debusine.upload_artifact mock and Path with the files.
        """
        temp_directory = self.create_temporary_directory()

        dsc_file = temp_directory / "file.dsc"

        self.write_dsc_example_file(dsc_file)

        for architecture in create_debs_for_architectures:
            self.create_deb_udeb_files(
                temp_directory, architecture=architecture
            )

        self.configure_task(
            self.configuration(host_architecture, build_components)
        )

        dsc = utils.read_dsc(dsc_file)
        assert dsc is not None

        debusine_mock = self.patch_sbuild_debusine()

        self.task._upload_binary_packages(temp_directory, dsc)

        return debusine_mock.upload_artifact, temp_directory

    def assert_upload_binary_packages_with_files(
        self, upload_artifact_mock: MagicMock, expected_files: list[list[Path]]
    ) -> None:
        """
        Assert debusine.upload_artifact() was called with expected artifacts.

        Assert that there is one call in upload_artifact_mock for each
        individual path in expected_files, and one call for each of the
        sub-lists of paths; and assert that the file names on each
        upload_artifact() are the expected ones (expected_files[i]).
        """
        self.assertEqual(
            upload_artifact_mock.call_count,
            sum(len(files) for files in expected_files) + len(expected_files),
        )

        for call_args, files in zip(
            upload_artifact_mock.call_args_list,
            itertools.chain.from_iterable(
                [[file] for file in files] + [files] for files in expected_files
            ),
        ):
            expected_filenames = {file.name for file in files}
            self.assertEqual(call_args[0][0].files.keys(), expected_filenames)

    def assert_upload_binary_packages_architectures(
        self, upload_artifact_mock: MagicMock, expected_architectures: list[str]
    ) -> None:
        """
        Assert debusine.upload_artifact() was called with expected arches.

        Assert that the debian:binary-packages artifacts uploaded via
        upload_artifact_mock have the architecture data fields given in
        expected_architectures.
        """
        artifacts = [
            call_args[0][0]
            for call_args in upload_artifact_mock.call_args_list
            if call_args[0][0].category == ArtifactCategory.BINARY_PACKAGES
        ]
        self.assertEqual(
            [artifact.data.architecture for artifact in artifacts],
            expected_architectures,
        )

    def test_upload_binary_packages_build_components_any(self) -> None:
        r"""
        upload_binary_packages() upload \*_host.deb packages.

        build_components()
        """
        (
            upload_artifact_mock,
            temp_directory,
        ) = self.create_and_upload_binary_packages(
            "amd64", ["any"], create_debs_for_architectures=["amd64", "all"]
        )

        expected_files = sorted(temp_directory.glob("*_amd64.*deb"))

        self.assert_upload_binary_packages_with_files(
            upload_artifact_mock, [expected_files]
        )
        self.assert_upload_binary_packages_architectures(
            upload_artifact_mock, ["amd64"]
        )

    def test_upload_binary_packages_build_components_all(self) -> None:
        r"""
        upload_binary_packages() upload \*_all.deb packages.

        build_components is "all". All packages are created (host and all)
        but only \*_all.deb are uploaded.
        """
        (
            upload_artifact_mock,
            temp_directory,
        ) = self.create_and_upload_binary_packages(
            "amd64", ["all"], create_debs_for_architectures=["amd64", "all"]
        )

        expected_files = sorted(temp_directory.glob("*_all.*deb"))

        self.assert_upload_binary_packages_with_files(
            upload_artifact_mock, [expected_files]
        )
        self.assert_upload_binary_packages_architectures(
            upload_artifact_mock, ["all"]
        )

    def test_upload_binary_packages_build_components_any_all(self) -> None:
        r"""upload_binary_packages() upload \*_host.deb and \*_all.deb files."""
        (
            upload_artifact_mock,
            temp_directory,
        ) = self.create_and_upload_binary_packages(
            "amd64",
            ["any", "all"],
            create_debs_for_architectures=["amd64", "all"],
        )

        expected_files_amd64 = sorted(temp_directory.glob("*_amd64.*deb"))
        expected_files_all = sorted(temp_directory.glob("*_all.*deb"))

        self.assert_upload_binary_packages_with_files(
            upload_artifact_mock, [expected_files_amd64, expected_files_all]
        )
        self.assert_upload_binary_packages_architectures(
            upload_artifact_mock, ["amd64", "all"]
        )

    def test_upload_binary_packages_build_components_any_all_without_all(
        self,
    ) -> None:
        r"""
        upload_binary_packages() upload \*_host.deb packages.

        Only host architecture packages exist: only one package created.
        """
        (
            upload_artifact_mock,
            temp_directory,
        ) = self.create_and_upload_binary_packages(
            "amd64", ["any", "all"], create_debs_for_architectures=["amd64"]
        )

        expected_files_amd64 = sorted(temp_directory.glob("*_amd64.*deb"))

        self.assert_upload_binary_packages_with_files(
            upload_artifact_mock, [expected_files_amd64]
        )
        self.assert_upload_binary_packages_architectures(
            upload_artifact_mock, ["amd64"]
        )

    def test_create_binary_package_local_artifacts(self) -> None:
        """
        _create_binary_package_local_artifact(): return BinaryPackage(s).

        Assert that it returns the expected BinaryPackage and BinaryPackages
        artifacts.
        """
        build_directory = self.create_temporary_directory()

        dsc_file = build_directory / "package.dsc"

        dsc_data = self.write_dsc_example_file(dsc_file)
        dsc = utils.read_dsc(dsc_file)
        assert dsc is not None

        package_file = build_directory / "package_1.0_amd64.deb"
        self.write_deb_file(
            package_file,
            source_name=dsc_data["Source"],
            source_version=dsc_data["Version"],
        )

        artifacts = self.task._create_binary_package_local_artifacts(
            build_directory,
            dsc,
            architecture="amd64",
            suffixes=[".deb"],
        )

        self.assertEqual(
            artifacts,
            [
                BinaryPackage(
                    category=BinaryPackage._category,
                    files={package_file.name: package_file},
                    data=DebianBinaryPackage.parse_obj(
                        {
                            "srcpkg_name": dsc_data["Source"],
                            "srcpkg_version": dsc_data["Version"],
                            "deb_fields": {
                                "Package": "package",
                                "Version": "1.0",
                                "Architecture": "amd64",
                                "Maintainer": "Example Maintainer"
                                " <example@example.org>",
                                "Description": "Example description",
                                "Source": (
                                    f"{dsc_data['Source']} "
                                    f"({dsc_data['Version']})"
                                ),
                            },
                            "deb_control_files": ["control"],
                        }
                    ),
                ),
                BinaryPackages(
                    category=BinaryPackages._category,
                    files={package_file.name: package_file},
                    data=DebianBinaryPackages.parse_obj(
                        {
                            "srcpkg_name": dsc_data["Source"],
                            "srcpkg_version": dsc_data["Version"],
                            "version": dsc_data["Version"],
                            "architecture": "amd64",
                            "packages": ["package"],
                        }
                    ),
                ),
            ],
        )

    def test_get_source_artifacts_ids(self) -> None:
        """Test get_source_artifacts_ids."""
        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=1,
            environment_id=2,
            input_extra_binary_artifacts_ids=[],
        )
        self.assertEqual(self.task.get_source_artifacts_ids(), [2, 1])

        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=1,
            environment_id=None,
            input_extra_binary_artifacts_ids=[],
        )
        self.assertEqual(self.task.get_source_artifacts_ids(), [1])

        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=1,
            environment_id=2,
            input_extra_binary_artifacts_ids=[3, 4],
        )
        self.assertEqual(self.task.get_source_artifacts_ids(), [2, 1, 3, 4])

    def test_label_subject_is_none(self) -> None:
        """Test get_label if dynamic_data.subject is None."""
        self.assertEqual(self.task.get_label(), "build a package")

    def test_label_dynamic_data_is_none(self) -> None:
        """Test get_label if dynamic_data is None."""
        self.task.dynamic_data = None
        self.assertEqual(self.task.get_label(), "build a package")

    def test_label_package_subject(self) -> None:
        """Test get_label if dynamic_data.subject is set."""
        self.task.dynamic_data = SbuildDynamicData(
            input_source_artifact_id=1, environment_id=2, subject="hello"
        )
        self.assertEqual(self.task.get_label(), "build hello")
