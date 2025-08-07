# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for SystemBootstrap class."""
import copy
import hashlib
import io
import textwrap
from pathlib import Path
from typing import Any, cast
from unittest import mock
from unittest.mock import call

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

import requests
import responses
from debian.deb822 import Deb822

from debusine.client.exceptions import ContentValidationError
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    BaseDynamicTaskData,
    SystemBootstrapData,
    SystemBootstrapRepository,
    SystemBootstrapRepositoryCheckSignatureWith,
    SystemBootstrapRepositoryKeyring,
    SystemBootstrapRepositoryType,
)
from debusine.tasks.systembootstrap import SystemBootstrap
from debusine.tasks.tests.helper_mixin import (
    ExternalTaskHelperMixin,
    FakeTaskDatabase,
)
from debusine.test import TestCase


class SystemBootstrapImpl(SystemBootstrap[SystemBootstrapData]):
    """Implementation of SystemBootstrap ontology."""

    def _cmdline(self) -> list[str]:
        return []  # pragma: no cover


class SystemBootstrapTests(
    ExternalTaskHelperMixin[SystemBootstrapImpl], TestCase
):
    """Tests SystemBootstrap class."""

    SAMPLE_TASK_DATA = {
        "bootstrap_options": {
            "architecture": "amd64",
            "variant": "buildd",
            "extra_packages": ["hello", "python3"],
        },
        "bootstrap_repositories": [
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "components": ["main", "contrib"],
                "check_signature_with": "system",
            },
            {
                "types": ["deb-src"],
                "mirror": "https://example.com",
                "suite": "bullseye",
                "components": ["main"],
                "check_signature_with": "no-check",
            },
        ],
    }

    def setUp(self) -> None:
        """Initialize test."""
        self.configure_task()

    def tearDown(self) -> None:
        """Delete objects."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()

    def test_configure_task(self) -> None:
        """Ensure self.SAMPLE_TASK_DATA is valid."""
        self.configure_task(self.SAMPLE_TASK_DATA)

        # Test default options
        sample_repositories = cast(
            list[dict[str, Any]],
            self.SAMPLE_TASK_DATA["bootstrap_repositories"],
        )
        actual_repositories = self.task.data.bootstrap_repositories

        # First sample repository does not have "types" nor
        # "signed_signature_with"
        self.assertNotIn("types", sample_repositories[0])
        self.assertNotIn("signed_signature_with", sample_repositories[0])
        # They are assigned to the default values
        self.assertEqual(actual_repositories[0].types, ["deb"])
        self.assertEqual(actual_repositories[0].check_signature_with, "system")

        #  Second sample repository has "types" / "signed_signature_with" as
        # per SAMPLE_TASK_DATA
        self.assertEqual(sample_repositories[1]["types"], ["deb-src"])
        self.assertEqual(
            actual_repositories[1].check_signature_with, "no-check"
        )

    def test_configure_task_raise_value_error_missing_keyring(self) -> None:
        """configure_task() raise ValueError: missing keyring parameter."""
        task_data = copy.deepcopy(self.SAMPLE_TASK_DATA)
        bootstrap_repo = cast(
            list[dict[str, Any]],
            copy.deepcopy(self.SAMPLE_TASK_DATA["bootstrap_repositories"]),
        )[0]
        bootstrap_repo["check_signature_with"] = "external"
        task_data["bootstrap_repositories"] = [bootstrap_repo]

        msg = (
            r"repository requires 'keyring':"
            r" 'check_signature_with' is set to 'external'"
        )

        with self.assertRaisesRegex(TaskConfigError, msg):
            self.configure_task(task_data)

    def test_compute_dynamic_data(self) -> None:
        task_database = FakeTaskDatabase()

        self.assertEqual(
            self.task.compute_dynamic_data(task_database),
            BaseDynamicTaskData(
                subject="bookworm",
                runtime_context="amd64:buildd:hello,python3",
                configuration_context="amd64",
            ),
        )

    @staticmethod
    def create_and_validate_repo(
        check_signature_with: SystemBootstrapRepositoryCheckSignatureWith,
        *,
        keyring_url: str | None = None,
        keyring_sha256sum: str = "",
    ) -> SystemBootstrapRepository:
        """Create a basic repository, validates and return it."""
        keyring: SystemBootstrapRepositoryKeyring | None = None
        if keyring_url is not None:
            keyring = SystemBootstrapRepositoryKeyring(
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl | pydantic.FileUrl, keyring_url
                ),
                sha256sum=keyring_sha256sum,
            )
        repository = SystemBootstrapRepository(
            types=[SystemBootstrapRepositoryType.DEB],
            mirror="https://example.com",
            suite="bullseye",
            components=["main", "contrib"],
            check_signature_with=check_signature_with,
            keyring=keyring,
        )

        return repository

    def test_deb822_source(self) -> None:
        """Generate a correct Deb822 sources file."""
        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.SYSTEM
        )

        actual = self.task._deb822_source(
            repository,
            keyring_directory=self.create_temporary_directory(),
            use_signed_by=True,
        )

        expected = {
            "Types": "deb",
            "URIs": "https://example.com",
            "Suites": "bullseye",
            "Components": "main contrib",
        }

        self.assertEqual(actual, expected)

    def test_deb822_source_no_components(self) -> None:
        """Use components returned by _list_components_for_suite."""
        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.NO_CHECK
        )
        repository.components = None

        components = ["main", "contrib"]
        with mock.patch(
            "debusine.tasks.systembootstrap.SystemBootstrap."
            "_list_components_for_suite",
            return_value=components,
            autospec=True,
        ) as mocked:
            actual = self.task._deb822_source(
                repository,
                keyring_directory=self.create_temporary_directory(),
                use_signed_by=False,
            )

        self.assertEqual(actual["components"], " ".join(components))

        mocked.assert_called_with(repository.mirror, repository.suite)

    def test_deb822_source_signature_no_check(self) -> None:
        """Generate Deb822 with "trusted"."""
        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.NO_CHECK
        )

        actual = self.task._deb822_source(
            repository,
            keyring_directory=self.create_temporary_directory(),
            use_signed_by=True,
        )

        self.assertEqual(actual["Trusted"], "yes")

    @responses.activate
    def test_deb822_source_file_download_keyring_binary(self) -> None:
        """Generate Deb822 and downloads a binary keyring file."""
        keyring_contents = b"\x99\x02"
        keyring_url = "https://example.com/keyring_file.gpg"
        keyring_directory = self.create_temporary_directory()

        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL,
            keyring_url=keyring_url,
        )

        self.assertIsNotNone(repository.keyring)
        assert repository.keyring
        responses.add(
            responses.GET, repository.keyring.url, body=keyring_contents
        )

        actual = self.task._deb822_source(
            repository, keyring_directory=keyring_directory, use_signed_by=True
        )

        signed_by_keyring = Path(actual["Signed-By"])
        self.assertTrue(signed_by_keyring.name.endswith(".gpg"))
        self.assertEqual(signed_by_keyring.read_bytes(), keyring_contents)

        # It was created in the correct directory
        self.assertTrue(signed_by_keyring.is_relative_to(keyring_directory))

    @responses.activate
    def test_deb822_source_file_download_keyring_ascii(self) -> None:
        """Generate Deb822 and downloads an ASCII-armored keyring file."""
        keyring_contents = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        keyring_url = "https://example.com/keyring_file.asc"
        keyring_directory = self.create_temporary_directory()

        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL,
            keyring_url=keyring_url,
        )

        self.assertIsNotNone(repository.keyring)
        assert repository.keyring
        responses.add(
            responses.GET, repository.keyring.url, body=keyring_contents
        )

        actual = self.task._deb822_source(
            repository, keyring_directory=keyring_directory, use_signed_by=True
        )

        signed_by_keyring = Path(actual["Signed-By"])
        self.assertTrue(signed_by_keyring.name.endswith(".asc"))
        self.assertEqual(signed_by_keyring.read_bytes(), keyring_contents)

        # It was created in the correct directory
        self.assertTrue(signed_by_keyring.is_relative_to(keyring_directory))

    @responses.activate
    def test_deb822_source_keyring_hash_mismatch(self) -> None:
        """Method raise ValueError: keyring sha256 mismatch."""
        keyring_contents = b"some-keyring"
        keyring_url = "https://example.com/keyring_file.txt"
        keyring_directory = self.create_temporary_directory()

        expected_sha256 = "will be a mismatch"

        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL,
            keyring_url=keyring_url,
            keyring_sha256sum=expected_sha256,
        )

        self.assertIsNotNone(repository.keyring)
        assert repository.keyring

        responses.add(
            responses.GET, repository.keyring.url, body=keyring_contents
        )

        actual_sha256 = hashlib.sha256(keyring_contents).hexdigest()

        expected = (
            f"sha256 mismatch for keyring repository {repository.mirror}. "
            f"Actual: {actual_sha256} expected: {expected_sha256}"
        )

        with self.assertRaisesRegex(ContentValidationError, expected):
            self.task._deb822_source(
                repository,
                keyring_directory=keyring_directory,
                use_signed_by=True,
            )

    @responses.activate
    def test_deb822_source_keyring_hash_correct(self) -> None:
        """Keyring sha256 is correct."""
        keyring_contents = b"some-keyring"
        keyring_url = "https://example.com/keyring_file.txt"
        keyring_directory = self.create_temporary_directory()

        actual_sha256 = hashlib.sha256(keyring_contents).hexdigest()

        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL,
            keyring_url=keyring_url,
            keyring_sha256sum=actual_sha256,
        )

        self.assertIsNotNone(repository.keyring)
        assert repository.keyring

        responses.add(
            responses.GET, repository.keyring.url, body=keyring_contents
        )

        self.task._deb822_source(
            repository, keyring_directory=keyring_directory, use_signed_by=True
        )

    def test_deb822_source_keyring_file(self) -> None:
        """The keyring may be read from a file in /usr/share/keyrings/."""
        keyring_contents = b"some-keyring"
        keyring_url = "file:///usr/share/keyrings/debian-archive-keyring.gpg"
        keyring_directory = self.create_temporary_directory()

        repository = self.create_and_validate_repo(
            SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL,
            keyring_url=keyring_url,
        )

        self.assertIsNotNone(repository.keyring)
        assert repository.keyring

        response = requests.Response()
        response.raw = io.BytesIO(keyring_contents)
        response.status_code = requests.codes.ok
        response.url = keyring_url
        with mock.patch(
            "requests_file.FileAdapter.send", return_value=response
        ):
            actual = self.task._deb822_source(
                repository,
                keyring_directory=keyring_directory,
                use_signed_by=True,
            )

        signed_by_keyring = Path(actual["Signed-By"])
        self.assertEqual(signed_by_keyring.read_bytes(), keyring_contents)

        # It was created in the correct directory
        self.assertTrue(signed_by_keyring.is_relative_to(keyring_directory))

    @responses.activate
    def test_generate_deb822_sources_file(self) -> None:
        """Assert _generate_deb822_sources_file() return the expected files."""
        keyring_url = "https://example.com/keyring_1.gpg"
        responses.add(responses.GET, keyring_url)

        repository = SystemBootstrapRepository(
            types=[SystemBootstrapRepositoryType.DEB],
            mirror="https://deb.debian.org/deb",
            suite="bookworm",
            components=["main", "contrib"],
            check_signature_with=(
                SystemBootstrapRepositoryCheckSignatureWith.EXTERNAL
            ),
            keyring=SystemBootstrapRepositoryKeyring(
                url=pydantic.parse_obj_as(pydantic.AnyUrl, keyring_url)
            ),
        )

        for use_signed_by in [True, False]:
            with self.subTest(use_signed_by=use_signed_by):
                directory = self.create_temporary_directory()

                sources = self.task._generate_deb822_sources(
                    [repository],
                    keyrings_dir=directory,
                    use_signed_by=use_signed_by,
                )

                self.assertEqual(sources[0]["URIs"], repository.mirror)
                self.assertEqual(len(sources), 1)

                if use_signed_by:
                    self.assertEqual(
                        sources[0]["Signed-By"], str(next(directory.iterdir()))
                    )
                else:
                    self.assertNotIn("Signed-By", sources[0])

    @staticmethod
    def add_responses_release_file(
        components: list[str],
    ) -> tuple[str, str, str]:
        """
        Add a mock response for a Release file with Components: line.

        :returns: mirror_url, suite, release_url.
        """
        mirror_url = "http://deb.debian.org/debian"
        suite = "bookworm"

        release_url = f"{mirror_url}/dists/{suite}/Release"

        responses.add(
            responses.GET,
            release_url,
            body=textwrap.dedent(
                f"""\
                Origin: Debian
                Label: Debian
                Suite: stable
                Version: 12.4
                Codename: bookworm
                Date: Sun, 10 Dec 2023 17:43:24 UTC
                Acquire-By-Hash: yes
                No-Support-for-Architecture-all: Packages
                Components: {' '.join(components)}
                Description: Debian 12.4 Released 10 December 2023
                MD5Sum:
                 0ed6d4c8891eb86358b94bb35d9e4da4  1484322 contrib/Contents-all
                 d0a0325a97c42fd5f66a8c3e29bcea64    981 contrib/Contents-all.gz
                 58f32d515c66daafcdac2595fc984814   84179 contrib/Contents-amd64
                """
            ),
            status=200,
        )

        return mirror_url, suite, release_url

    @responses.activate
    def test_list_components_for_suite(self) -> None:
        """_list_components_for_suite get Release URL and return components."""
        expected_components = [
            "main",
            "updates/main",
            "updates/main/something",
            "contrib",
            "non-free-firmware",
            "non-free",
        ]

        mirror_url, suite, _ = self.add_responses_release_file(
            expected_components
        )

        actual_components = SystemBootstrap._list_components_for_suite(
            mirror_url, suite
        )
        self.assertEqual(actual_components, expected_components)

    @responses.activate
    def test_list_components_for_suite_raise_value_error(self) -> None:
        """
        _list_components_for_suite get Release URL and raise exception.

        The line containing "Components:" was not found.
        """
        mirror_url = "http://deb.debian.org/debian"

        url = mirror_url + "/dists/bookworm/Release"

        responses.add(
            responses.GET,
            url,
            body="Some text not containing line starts with Component: ",
            status=200,
        )

        msg = f"^Cannot find components in {url}"
        with self.assertRaisesRegex(ValueError, msg):
            SystemBootstrap._list_components_for_suite(mirror_url, "bookworm")

    @responses.activate
    def test_list_components_raise_value_error_invalid_character_dot(
        self,
    ) -> None:
        """
        _list_components_for_suite raise ValueError: invalid component name.

        Components cannot have a ".".
        """
        component = "non.free"

        mirror_url, suite, release_url = self.add_responses_release_file(
            [component]
        )

        msg = (
            rf'^Invalid component name from {release_url}: "{component}" '
            r'must start with \[A-Za-z\] and have only \[-/A-Za-z\] characters'
        )
        with self.assertRaisesRegex(ValueError, msg):
            SystemBootstrap._list_components_for_suite(mirror_url, suite)

    @responses.activate
    def test_list_components_raise_value_error_invalid_start_with_slash(
        self,
    ) -> None:
        """
        _list_components_for_suite raise ValueError: invalid component name.

        Components cannot start with "/".
        """
        component = "/main"

        mirror_url, suite, release_url = self.add_responses_release_file(
            [component]
        )

        msg = (
            rf'^Invalid component name from {release_url}: "{component}" '
            r'must start with \[A-Za-z\] and have only \[-/A-Za-z\] characters'
        )
        with self.assertRaisesRegex(ValueError, msg):
            SystemBootstrap._list_components_for_suite(mirror_url, suite)

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(self.task.get_label(), "bootstrap a system tarball")

    @responses.activate
    def test_configure_for_execution(self) -> None:
        """Test configure_for_execution(): call _generate_deb822_sources."""
        download_dir = self.create_temporary_directory()

        # Assume that, in production code, the directory was created
        # with restrictive permissions
        download_dir.chmod(0o700)

        url_0 = "https://example.com/keyring1.asc"
        url_1 = "https://example.com/keyring2.asc"

        repositories: list[dict[str, Any]] = [
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "components": ["main", "contrib"],
                "check_signature_with": "system",
            },
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "components": ["main", "contrib"],
                "check_signature_with": "no-check",
            },
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "components": ["main", "contrib"],
                "check_signature_with": "external",
                "keyring": {"url": url_0, "install": True},
            },
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bullseye",
                "components": ["main"],
                "check_signature_with": "external",
                "keyring": {"url": url_1, "install": False},
            },
        ]

        keyring_contents_0 = (
            b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nASCII-armored keyring"
        )
        keyring_contents_1 = b"Binary keyring"

        responses.add(
            responses.GET,
            url_0,
            body=keyring_contents_0,
        )
        responses.add(
            responses.GET,
            url_1,
            body=keyring_contents_1,
        )

        self.configure_task(override={"bootstrap_repositories": repositories})

        with mock.patch.object(
            self.task, "append_to_log_file", autospec=True
        ) as append_to_log_file:
            self.assertTrue(self.task.configure_for_execution(download_dir))

        # Files were generated
        sources_files = list(download_dir.glob("*.sources"))
        self.assertEqual(len(sources_files), 2)

        assert self.task._chroot_sources_file
        # Source file Signed-By changed to the correct chroot path
        with self.task._chroot_sources_file.open() as f:
            for actual, task_repository in zip(
                Deb822.iter_paragraphs(f), repositories
            ):
                if task_repository["check_signature_with"] in (
                    "system",
                    "no-check",
                ):
                    # For a "system" or "no-check": the chroot sources list
                    # does not have any "Signed-By"
                    self.assertNotIn("Signed-By", actual)
                else:
                    # It must be "external"
                    self.assertEqual(
                        task_repository["check_signature_with"], "external"
                    )

                    if task_repository["keyring"]["install"]:
                        # The path changed to one that will make sense in chroot
                        self.assertRegex(
                            actual["Signed-By"],
                            r"^/etc/apt/keyrings-debusine/.*\.asc",
                        )
                    else:
                        # No Signed-By in the chroot: the keyring is not
                        # uploaded (it is used for the initial setup only)
                        self.assertNotIn("Signed-By", actual)

        # check files were added in the log artifact
        expected_calls = []
        for source_file in [
            download_dir / "host.sources",
            download_dir / "chroot.sources",
        ]:
            expected_calls.append(
                call(source_file.name, source_file.read_text().splitlines())
            )
        append_to_log_file.assert_has_calls(expected_calls)

        assert self.task._host_sources_file
        host_repositories = list(
            Deb822.iter_paragraphs(self.task._host_sources_file.read_text())
        )

        # Both repositories with check_signature_external have Signed-By
        # in the host.sources
        self.assertEqual(
            Path(host_repositories[2]["Signed-By"]).read_bytes(),
            keyring_contents_0,
        )
        self.assertEqual(
            Path(host_repositories[3]["Signed-By"]).read_bytes(),
            keyring_contents_1,
        )

        # And the files were added in self.task._keyrings
        self.assertIn(
            Path(host_repositories[2]["Signed-By"]), self.task._keyrings
        )
        self.assertIn(
            Path(host_repositories[3]["Signed-By"]), self.task._keyrings
        )

        # With appropriate extensions
        self.assertEqual(Path(host_repositories[2]["Signed-By"]).suffix, ".asc")
        self.assertEqual(Path(host_repositories[3]["Signed-By"]).suffix, ".gpg")

        # Two keyrings added
        self.assertEqual(len(self.task._keyrings), 2)

        # Verify that self.task._upload_keyrings contains only one, correct
        # suffix, correct content
        upload_keyrings = self.task._upload_keyrings
        self.assertEqual(len(upload_keyrings), 1)
        self.assertEqual(upload_keyrings[0].suffix, ".asc")
        self.assertEqual(upload_keyrings[0].read_bytes(), keyring_contents_0)

        # The chroot sources file have one Signed-By and the other on
        # no Signed-By
        host_repositories = list(
            Deb822.iter_paragraphs(self.task._chroot_sources_file.read_text())
        )
        self.assertRegex(
            host_repositories[2]["Signed-By"],
            r"^/etc/apt/keyrings-debusine/keyring-repo-.*\.asc$",
        )
        self.assertNotIn("Signed-By", host_repositories[3])

        # Download directory can be read by anyone (needed for mmdebstrap to
        # use the keys from the subuid, in mode unshare as it is used)
        self.assertEqual(download_dir.stat().st_mode & 0o755, 0o755)

        keyrings_dir = download_dir / "keyrings"
        self.assertEqual(keyrings_dir.stat().st_mode & 0o755, 0o755)

    @responses.activate
    def test_configure_for_execution_no_signed_by_in_repositories(self) -> None:
        """Test configure_for_execution(): use_signed_by is False."""
        download_dir = self.create_temporary_directory()

        bootstrap_options = {"architecture": "amd64", "use_signed_by": False}

        url = "https://example.com/keyring1.asc"

        repositories = [
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "components": ["main", "contrib"],
                "check_signature_with": "external",
                "keyring": {"url": url},
            },
        ]

        responses.add(
            responses.GET,
            url,
        )

        self.configure_task(
            override={
                "bootstrap_repositories": repositories,
                "bootstrap_options": bootstrap_options,
            }
        )
        self.task.configure_for_execution(download_dir)

        assert self.task._chroot_sources_file
        assert self.task._host_sources_file
        # Source file Signed-By changed to the correct chroot path
        with self.task._chroot_sources_file.open() as chroot_sources:
            with self.task._host_sources_file.open() as host_sources:
                count = 0
                for repository in [chroot_sources, host_sources]:
                    self.assertNotIn("Signed-By", repository)
                    count += 1

                # Expect two: one from chroot, one from host sources
                self.assertEqual(count, 2)

        self.assertEqual(len(self.task._keyrings), 1)
        self.assertEqual(len(self.task._upload_keyrings), 0)

    def test_configure_for_execution_customization_script(self) -> None:
        """Test configure_for_execution() write customization_script."""
        download_dir = self.create_temporary_directory()

        script = "#!/bin/sh\n\necho 'something'\n"
        self.configure_task(
            override={
                "customization_script": script,
            }
        )

        self.task.configure_for_execution(download_dir)

        assert self.task._customization_script
        self.assertEqual(
            self.task._customization_script.name, "customization_script"
        )
        self.assertEqual(self.task._customization_script.read_text(), script)

    def test_get_value_os_release(self) -> None:
        """get_value_os_release() get the correct value."""
        directory = self.create_temporary_directory()
        os_release_data = self.write_os_release(
            directory / self.task._OS_RELEASE_FILE
        )

        actual = SystemBootstrap._get_value_os_release(
            directory / self.task._OS_RELEASE_FILE, "PRETTY_NAME"
        )
        self.assertEqual(actual, os_release_data["PRETTY_NAME"])

    def test_get_value_os_release_key_error(self) -> None:
        """get_value_os_release() raise KeyError."""
        os_release = self.create_temporary_file()

        key = "PRETTY_NAME"
        with self.assertRaisesRegex(KeyError, key):
            SystemBootstrap._get_value_os_release(os_release, key)
