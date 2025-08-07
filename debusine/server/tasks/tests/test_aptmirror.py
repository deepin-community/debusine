# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the APTMirror task."""

import hashlib
import logging
import os
import re
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from textwrap import dedent
from typing import Any
from unittest.mock import MagicMock, patch

from debian.deb822 import Packages, Sources
from django.db import connection, connections
from django_pglocks import advisory_lock

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.locks import LockError, LockType
from debusine.db.models import Artifact, ArtifactRelation, Collection, Workspace
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.collections.debian_suite import (
    DebianSuiteManager,
    make_pool_filename,
)
from debusine.server.tasks import APTMirror
from debusine.server.tasks.aptmirror import (
    InconsistentMirrorError,
    Plan,
    PlanAdd,
    PlanReplace,
)
from debusine.tasks import TaskConfigError
from debusine.test.django import TestCase


class APTMirrorTests(TestCase):
    """Test the :py:class:`APTMirror` task."""

    def create_suite_collection(self, name: str) -> Collection:
        """Create a `debian:suite` collection."""
        return self.playground.create_collection(
            name=name, category=CollectionCategory.SUITE
        )

    def create_apt_mirror_task(
        self,
        collection_name: str,
        url: str = "https://deb.debian.org/debian",
        suite: str = "bookworm",
        architectures: list[str] | None = None,
        components: list[str] | None = None,
        signing_key: str | None = None,
    ) -> APTMirror:
        """Create an instance of the :py:class:`APTMirror` task."""
        task_data = {
            "collection": collection_name,
            "url": url,
            "suite": suite,
            "architectures": architectures or ["amd64"],
        }
        if not suite.endswith("/") and components is None:
            components = ["main"]
        if components is not None:
            task_data["components"] = components
        if signing_key is not None:
            task_data["signing_key"] = signing_key
        return APTMirror(task_data)

    def create_source_package_artifact(
        self, name: str, version: str, paths: list[str]
    ) -> Artifact:
        """Create a minimal `debian:source-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": name,
                "version": version,
                "type": "dpkg",
                "dsc_fields": {},
            },
            paths=paths,
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def create_binary_package_artifact(
        self,
        srcpkg_name: str,
        srcpkg_version: str,
        name: str,
        version: str,
        architecture: str,
        paths: list[str],
    ) -> Artifact:
        """Create a minimal `debian:binary-package` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "deb_fields": {
                    "Package": name,
                    "Version": version,
                    "Architecture": architecture,
                },
                "deb_control_files": [],
            },
            paths=paths,
            create_files=True,
            skip_add_files_in_store=True,
        )
        return artifact

    def write_sample_source_package(
        self,
        temp_path: Path,
        name: str,
        version: str,
        *,
        section: str = "devel",
    ) -> Sources:
        """Write a sample source package, returning its index entry."""
        dsc_contents = dedent(
            f"""\
            Format: 3.0 (native)
            Source: {name}
            Binary: {name}
            Architecture: any
            Version: {version}
            """
        ).encode()
        dsc_hash = hashlib.sha256(dsc_contents).hexdigest()
        dsc_size = len(dsc_contents)
        (temp_path / f"{name}_{version}.dsc").write_bytes(dsc_contents)
        tar_contents = b"tar"
        tar_hash = hashlib.sha256(tar_contents).hexdigest()
        tar_size = len(tar_contents)
        (temp_path / f"{name}_{version}.tar.xz").write_bytes(tar_contents)
        return Sources(
            {
                "Package": name,
                "Version": version,
                "Section": section,
                "Checksums-Sha256": dedent(
                    f"""\
                    {dsc_hash} {dsc_size} {name}_{version}.dsc
                    {tar_hash} {tar_size} {name}_{version}.tar.xz
                    """
                ),
            }
        )

    def write_sample_binary_package(
        self,
        temp_path: Path,
        name: str,
        version: str,
        architecture: str,
        *,
        srcpkg_name: str | None = None,
        srcpkg_version: str | None = None,
        component: str = "main",
        section: str = "devel",
    ) -> Packages:
        """Write a sample binary package, returning its index entry."""
        deb_path = temp_path / f"{name}_{version}_{architecture}.deb"
        self.write_deb_file(
            deb_path,
            source_name=srcpkg_name or name,
            source_version=srcpkg_version or version,
        )
        if m := re.match(r"^\d+:(.*)", version):
            deb_path = deb_path.rename(
                temp_path / f"{name}_{m.group(1)}_{architecture}.deb"
            )
        deb_contents = deb_path.read_bytes()
        return Packages(
            {
                "Package": name,
                "Version": version,
                "Architecture": architecture,
                "Section": section,
                "Priority": "optional",
                "Filename": make_pool_filename(
                    srcpkg_name or name, component, deb_path.name
                ),
                "SHA256": hashlib.sha256(deb_contents).hexdigest(),
            }
        )

    @contextmanager
    def patch_run_indextargets(
        self, paths_and_components: list[tuple[Path, str]]
    ) -> Generator[MagicMock, None, None]:
        """Temporarily patch the output of `apt-get indextargets`."""

        def fake_run(args: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    "".join(
                        f"Filename: {path}\nComponent: {component}\n\n"
                        for path, component in paths_and_components
                    )
                ),
                stderr="",
            )

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            yield mock_run

    def assert_ran_indextargets(
        self, mock_run: MagicMock, temp_path: Path, identifier: str
    ) -> None:
        """Assert that the test ran `apt-get indextargets`."""
        expected_env = os.environ.copy()
        expected_env["APT_CONFIG"] = str(temp_path / "etc/apt/apt.conf")
        mock_run.assert_called_with(
            ["apt-get", "indextargets", f"Identifier: {identifier}"],
            cwd=None,
            env=expected_env,
            text=True,
            check=True,
            capture_output=True,
        )

    @contextmanager
    def patch_download(
        self, last_arg_to_files: dict[str, dict[str, bytes]]
    ) -> Generator[MagicMock, None, None]:
        """Temporarily patch the effects of an `apt-get` download command."""

        def fake_run(
            args: list[str], *, cwd: Path, **kwargs: Any
        ) -> CompletedProcess[str]:
            for name, contents in last_arg_to_files[args[-1]].items():
                (cwd / name).write_bytes(contents)
            return CompletedProcess(args=args, returncode=0)

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            yield mock_run

    def assert_downloaded_source(
        self, mock_run: MagicMock, temp_path: Path, name: str, version: str
    ) -> None:
        """Assert that the test ran `apt-get --download-only source`."""
        expected_env = os.environ.copy()
        expected_env["APT_CONFIG"] = str(temp_path / "etc/apt/apt.conf")
        mock_run.assert_called_once_with(
            [
                "apt-get",
                "--download-only",
                "--only-source",
                "source",
                f"{name}={version}",
            ],
            cwd=temp_path / "download" / f"{name}_{version}",
            env=expected_env,
            text=True,
            check=True,
            capture_output=True,
        )

    def assert_downloaded_binary(
        self,
        mock_run: MagicMock,
        temp_path: Path,
        name: str,
        version: str,
        architecture: str,
    ) -> None:
        """Assert that the test ran `apt-get download`."""
        expected_env = os.environ.copy()
        expected_env["APT_CONFIG"] = str(temp_path / "etc/apt/apt.conf")
        mock_run.assert_called_once_with(
            ["apt-get", "download", f"{name}:{architecture}={version}"],
            cwd=temp_path / "download" / f"{name}_{version}_{architecture}",
            env=expected_env,
            text=True,
            check=True,
            capture_output=True,
        )

    def assert_artifact_files_match(
        self, artifact: Artifact, files: dict[str, bytes]
    ) -> None:
        """Assert that an artifact's files are as expected."""
        self.assertEqual(
            {
                file_in_artifact.path: file_in_artifact.file.hash_digest
                for file_in_artifact in artifact.fileinartifact_set.all()
            },
            {
                name: _calculate_hash_from_data(contents)
                for name, contents in files.items()
            },
        )

    def assert_artifact_matches(
        self,
        artifact: Artifact,
        category: ArtifactCategory,
        workspace: Workspace,
        data: dict[str, Any],
        files: dict[str, bytes],
    ) -> None:
        """Assert that an artifact is as expected."""
        self.assertEqual(artifact.category, category)
        self.assertEqual(artifact.workspace, workspace)
        self.assertEqual(artifact.data, data)
        self.assert_artifact_files_match(artifact, files)

    def test_collection(self) -> None:
        """`collection` looks up the requested collection by name."""
        bookworm = self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")
        self.assertEqual(task.collection, bookworm)

    def test_collection_nonexistent(self) -> None:
        """`collection` raises exception: collection doesn't exist."""
        task = self.create_apt_mirror_task("nonexistent")
        with self.assertRaisesRegex(
            TaskConfigError,
            "Collection 'nonexistent' with category 'debian:suite' not found",
        ):
            task.collection

    def test_collection_wrong_category(self) -> None:
        """`collection` raises exception: wrong category."""
        self.playground.create_collection(
            name="bookworm", category=CollectionCategory.ENVIRONMENTS
        )
        task = self.create_apt_mirror_task("bookworm")
        with self.assertRaisesRegex(
            TaskConfigError,
            "Collection 'bookworm' with category 'debian:suite' not found",
        ):
            task.collection

    def test_fetch_indexes(self) -> None:
        """`fetch_indexes` sets up apt and calls "apt-get update"."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        with patch("subprocess.run") as mock_run:
            task.fetch_indexes(temp_path)

        self.assertTrue((temp_path / "etc/apt/apt.conf.d").is_dir())
        self.assertTrue((temp_path / "etc/apt/preferences.d").is_dir())
        self.assertTrue((temp_path / "etc/apt/sources.list.d").is_dir())
        self.assertTrue((temp_path / "var/lib/apt/lists/partial").is_dir())
        self.assertEqual(
            (apt_config := temp_path / "etc/apt/apt.conf").read_text(),
            dedent(
                f"""\
                APT::Architecture "amd64";
                APT::Architectures "amd64";
                Dir "{temp_path}";
                """
            ),
        )
        self.assertEqual(
            (temp_path / "etc/apt/sources.list.d/mirror.sources").read_text(),
            dedent(
                """\
                Types: deb deb-src
                URIs: https://deb.debian.org/debian
                Suites: bookworm
                Components: main
                """
            ),
        )
        expected_env = os.environ.copy()
        expected_env["APT_CONFIG"] = str(apt_config)
        mock_run.assert_called_once_with(
            ["apt-get", "update"],
            cwd=None,
            env=expected_env,
            text=True,
            check=True,
            capture_output=True,
        )

    def test_fetch_indexes_flat(self) -> None:
        """`fetch_indexes` handles flat repositories."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task(
            "bookworm", url="https://deb.example.org/", suite="./"
        )

        with patch("subprocess.run"):
            task.fetch_indexes(temp_path)

        self.assertEqual(
            (temp_path / "etc/apt/sources.list.d/mirror.sources").read_text(),
            dedent(
                """\
                Types: deb deb-src
                URIs: https://deb.example.org/
                Suites: ./
                """
            ),
        )

    def test_fetch_indexes_with_signing_key(self) -> None:
        """`fetch_indexes` handles a signing key."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task(
            "bookworm",
            signing_key=(
                "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
                "\n"
                "test\n"
                "-----END PGP PUBLIC KEY BLOCK-----\n"
            ),
        )

        with patch("subprocess.run"):
            task.fetch_indexes(temp_path)

        self.assertEqual(
            (temp_path / "etc/apt/sources.list.d/mirror.sources").read_text(),
            dedent(
                """\
                Types: deb deb-src
                URIs: https://deb.debian.org/debian
                Suites: bookworm
                Components: main
                Signed-By:
                 -----BEGIN PGP PUBLIC KEY BLOCK-----
                 .
                 test
                 -----END PGP PUBLIC KEY BLOCK-----
                """
            ),
        )

    def test_fetch_indexes_logs_errors(self) -> None:
        """`fetch_indexes` logs stderr on failure."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        with (
            patch(
                "subprocess.run",
                side_effect=CalledProcessError(
                    returncode=1, cmd=["apt-get", "update"], stderr="Boom\n"
                ),
            ),
            self.assertLogsContains(
                "Error output from apt-get update:\nBoom",
                logger="debusine.server.tasks.aptmirror",
                level=logging.ERROR,
            ),
            self.assertRaises(CalledProcessError),
        ):
            task.fetch_indexes(temp_path)

    def test_plan_sources_add(self) -> None:
        """`plan_sources` plans to add sources to the collection."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        sources_path = temp_path / "Sources"
        with open(sources_path, "w") as sources:
            for name, version in (("pkg1", "1.0"), ("pkg2", "2.0")):
                sources.write(
                    self.write_sample_source_package(
                        temp_path, name, version
                    ).dump()
                    + "\n"
                )

        with self.patch_run_indextargets([(sources_path, "main")]) as mock_run:
            plan = task.plan_sources(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Sources")
        self.assertEqual(len(plan.add), 2)
        self.assertEqual(plan.add[0].name, "pkg1_1.0")
        self.assertEqual(plan.add[0].package["Package"], "pkg1")
        self.assertEqual(plan.add[0].component, "main")
        self.assertEqual(plan.add[1].name, "pkg2_2.0")
        self.assertEqual(plan.add[1].package["Package"], "pkg2")
        self.assertEqual(plan.add[1].component, "main")
        self.assertEqual(plan.replace, [])
        self.assertEqual(plan.remove, [])

    def test_plan_sources_replace(self) -> None:
        """`plan_sources` plans to replace sources in the collection."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        source_package_artifacts = [
            self.create_source_package_artifact(
                name=name,
                version=version,
                paths=[f"{name}_{version}.dsc", f"{name}_{version}.tar.xz"],
            )
            for name, version in (("pkg1", "1.0"), ("pkg1", "1.1"))
        ]
        for source_package_artifact in source_package_artifacts:
            manager.add_source_package(
                source_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
            )

        sources_path = temp_path / "Sources"
        with open(sources_path, "w") as sources:
            sources.write(
                self.write_sample_source_package(
                    temp_path, "pkg1", "1.0"
                ).dump()
                + "\n"
            )
            dsc_1_1_file = (
                source_package_artifacts[1]
                .fileinartifact_set.get(path="pkg1_1.1.dsc")
                .file
            )
            dsc_1_1_hash = dsc_1_1_file.hash_digest.hex()
            dsc_1_1_size = dsc_1_1_file.size
            tar_1_1_file = (
                source_package_artifacts[1]
                .fileinartifact_set.get(path="pkg1_1.1.tar.xz")
                .file
            )
            tar_1_1_hash = tar_1_1_file.hash_digest.hex()
            tar_1_1_size = tar_1_1_file.size
            sources.write(
                Sources(
                    {
                        "Package": "pkg1",
                        "Version": "1.1",
                        "Section": "devel",
                        "Checksums-Sha256": dedent(
                            f"""\
                            {dsc_1_1_hash} {dsc_1_1_size} pkg1_1.1.dsc
                            {tar_1_1_hash} {tar_1_1_size} pkg1_1.1.tar.xz
                            """
                        ),
                    }
                ).dump()
                + "\n"
            )

        with self.patch_run_indextargets([(sources_path, "main")]) as mock_run:
            plan = task.plan_sources(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Sources")
        self.assertEqual(plan.add, [])
        self.assertEqual(len(plan.replace), 1)
        self.assertEqual(plan.replace[0].name, "pkg1_1.0")
        self.assertEqual(plan.replace[0].package["Package"], "pkg1")
        self.assertEqual(plan.replace[0].component, "main")
        self.assertEqual(plan.replace[0].artifact, source_package_artifacts[0])
        self.assertEqual(plan.remove, [])

    def test_plan_sources_remove(self) -> None:
        """`plan_sources` plans to remove sources from the collection."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        source_package_artifacts = [
            self.create_source_package_artifact(
                name=name,
                version=version,
                paths=[f"{name}_{version}.dsc", f"{name}_{version}.tar.xz"],
            )
            for name, version in (("pkg1", "1.0"), ("pkg2", "2.0"))
        ]
        for source_package_artifact in source_package_artifacts:
            manager.add_source_package(
                source_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
            )

        (sources_path := temp_path / "Sources").touch()

        with self.patch_run_indextargets([(sources_path, "main")]) as mock_run:
            plan = task.plan_sources(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Sources")
        self.assertEqual(plan.add, [])
        self.assertEqual(plan.replace, [])
        self.assertEqual(plan.remove, source_package_artifacts)

    def test_plan_sources_inconsistent(self) -> None:
        """`plan_sources` fails with the same source in multiple components."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        paths_and_components = [
            (temp_path / "main_Sources", "main"),
            (temp_path / "contrib_Sources", "contrib"),
        ]
        for path, _ in paths_and_components:
            with open(path, "w") as sources:
                sources.write(
                    self.write_sample_source_package(
                        temp_path, "pkg1", "1.0"
                    ).dump()
                    + "\n"
                )

        with self.patch_run_indextargets(paths_and_components):
            self.assertRaisesRegex(
                InconsistentMirrorError,
                r"pkg1_1\.0 found in multiple components: main and contrib",
                task.plan_sources,
                temp_path,
            )

    @context.disable_permission_checks()
    def test_add_source(self) -> None:
        """`add_source` downloads and adds a source package."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        sources_entry = self.write_sample_source_package(
            temp_path, "hello", "1.0"
        )
        dsc_contents = (temp_path / "hello_1.0.dsc").read_bytes()
        tar_contents = (temp_path / "hello_1.0.tar.xz").read_bytes()
        with self.patch_download(
            {
                "hello=1.0": {
                    "hello_1.0.dsc": dsc_contents,
                    "hello_1.0.tar.xz": tar_contents,
                }
            }
        ) as mock_run:
            task.add_source(
                temp_path,
                name="hello_1.0",
                source=sources_entry,
                component="main",
            )

        self.assert_downloaded_source(mock_run, temp_path, "hello", "1.0")
        source_item = DebianSuiteManager(collection).lookup(
            "source-version:hello_1.0"
        )
        assert source_item is not None
        assert source_item.artifact is not None
        self.assert_artifact_matches(
            source_item.artifact,
            ArtifactCategory.SOURCE_PACKAGE,
            work_request.workspace,
            {
                "name": "hello",
                "version": "1.0",
                "type": "dpkg",
                "dsc_fields": {
                    "Format": "3.0 (native)",
                    "Source": "hello",
                    "Binary": "hello",
                    "Architecture": "any",
                    "Version": "1.0",
                },
            },
            {"hello_1.0.dsc": dsc_contents, "hello_1.0.tar.xz": tar_contents},
        )
        item = collection.child_items.get()
        self.assertEqual(item.created_by_user, work_request.created_by)
        self.assertEqual(item.data["component"], "main")
        self.assertEqual(item.data["section"], "devel")

    @context.disable_permission_checks()
    def test_update_sources(self) -> None:
        """`update_sources` executes a plan to update sources."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        collection.data["may_reuse_versions"] = True
        collection.save()
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        source_package_artifacts = [
            self.create_source_package_artifact(
                name=name,
                version=version,
                paths=[f"{name}_{version}.dsc", f"{name}_{version}.tar.xz"],
            )
            for name, version in (("to-replace", "1.0"), ("to-remove", "1.0"))
        ]
        for source_package_artifact in source_package_artifacts:
            manager.add_source_package(
                source_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
            )

        to_add_entry = self.write_sample_source_package(
            temp_path, "to-add", "1.0"
        )
        to_add_dsc_contents = (temp_path / "to-add_1.0.dsc").read_bytes()
        to_add_tar_contents = (temp_path / "to-add_1.0.tar.xz").read_bytes()
        to_replace_entry = self.write_sample_source_package(
            temp_path, "to-replace", "1.0"
        )
        to_replace_dsc_contents = (
            temp_path / "to-replace_1.0.dsc"
        ).read_bytes()
        to_replace_tar_contents = (
            temp_path / "to-replace_1.0.tar.xz"
        ).read_bytes()
        plan = Plan[Sources](
            add=[
                PlanAdd[Sources](
                    name="to-add", package=to_add_entry, component="main"
                )
            ],
            replace=[
                PlanReplace[Sources](
                    name="to-replace",
                    package=to_replace_entry,
                    component="main",
                    artifact=source_package_artifacts[0],
                )
            ],
            remove=[source_package_artifacts[1]],
        )

        with self.patch_download(
            {
                "to-add=1.0": {
                    "to-add_1.0.dsc": to_add_dsc_contents,
                    "to-add_1.0.tar.xz": to_add_tar_contents,
                },
                "to-replace=1.0": {
                    "to-replace_1.0.dsc": to_replace_dsc_contents,
                    "to-replace_1.0.tar.xz": to_replace_tar_contents,
                },
            }
        ):
            task.update_sources(temp_path, plan)

        active_items = {
            item.name: item
            for item in collection.child_items.all()
            if item.removed_at is None
        }
        self.assertEqual(active_items.keys(), {"to-add_1.0", "to-replace_1.0"})
        assert active_items["to-add_1.0"].artifact is not None
        self.assert_artifact_files_match(
            active_items["to-add_1.0"].artifact,
            {
                "to-add_1.0.dsc": to_add_dsc_contents,
                "to-add_1.0.tar.xz": to_add_tar_contents,
            },
        )
        assert active_items["to-replace_1.0"].artifact is not None
        self.assert_artifact_files_match(
            active_items["to-replace_1.0"].artifact,
            {
                "to-replace_1.0.dsc": to_replace_dsc_contents,
                "to-replace_1.0.tar.xz": to_replace_tar_contents,
            },
        )

    def test_plan_binaries_add(self) -> None:
        """`plan_binaries` plans to add binaries to the collection."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        packages_path = temp_path / "Packages"
        with open(packages_path, "w") as packages:
            for name, version in (("pkg1", "1.0"), ("pkg2", "2.0")):
                packages.write(
                    self.write_sample_binary_package(
                        temp_path, name, version, "amd64"
                    ).dump()
                    + "\n"
                )

        with self.patch_run_indextargets([(packages_path, "main")]) as mock_run:
            plan = task.plan_binaries(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Packages")
        self.assertEqual(len(plan.add), 2)
        self.assertEqual(plan.add[0].name, "pkg1_1.0_amd64")
        self.assertEqual(plan.add[0].package["Package"], "pkg1")
        self.assertEqual(plan.add[0].component, "main")
        self.assertEqual(plan.add[1].name, "pkg2_2.0_amd64")
        self.assertEqual(plan.add[1].package["Package"], "pkg2")
        self.assertEqual(plan.add[1].component, "main")
        self.assertEqual(plan.replace, [])
        self.assertEqual(plan.remove, [])

    def test_plan_binaries_multiple_same_component(self) -> None:
        """
        `plan_binaries` accepts multiple copies of arch-all binaries.

        `Architecture: all` binaries normally appear in `Packages` files for
        multiple architectures in the same component.
        """
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        paths_and_components = [
            (temp_path / "main_Packages_amd64", "main"),
            (temp_path / "main_Packages_s390x", "main"),
        ]
        package_entry = self.write_sample_binary_package(
            temp_path, "pkg1", "1.0", "all"
        )
        for path, _ in paths_and_components:
            with open(path, "w") as packages:
                packages.write(package_entry.dump() + "\n")

        with self.patch_run_indextargets(paths_and_components) as mock_run:
            plan = task.plan_binaries(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Packages")
        self.assertEqual(len(plan.add), 1)
        self.assertEqual(plan.add[0].name, "pkg1_1.0_all")
        self.assertEqual(plan.add[0].package["Package"], "pkg1")
        self.assertEqual(plan.add[0].component, "main")
        self.assertEqual(plan.replace, [])
        self.assertEqual(plan.remove, [])

    def test_plan_binaries_replace(self) -> None:
        """`plan_binaries` plans to replace binaries in the collection."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        binary_package_artifacts = [
            self.create_binary_package_artifact(
                srcpkg_name=name,
                srcpkg_version=version,
                name=name,
                version=version,
                architecture="amd64",
                paths=[f"{name}_{version}_amd64.deb"],
            )
            for name, version in (("pkg1", "1.0"), ("pkg1", "1.1"))
        ]
        for binary_package_artifact in binary_package_artifacts:
            manager.add_binary_package(
                binary_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
                priority="optional",
            )

        packages_path = temp_path / "Packages"
        with open(packages_path, "w") as packages:
            packages.write(
                self.write_sample_binary_package(
                    temp_path, "pkg1", "1.0", "amd64"
                ).dump()
                + "\n"
            )
            deb_1_1_file = (
                binary_package_artifacts[1].fileinartifact_set.get().file
            )
            packages.write(
                Packages(
                    {
                        "Package": "pkg1",
                        "Version": "1.1",
                        "Architecture": "amd64",
                        "Section": "devel",
                        "Priority": "optional",
                        "Filename": make_pool_filename(
                            "pkg1", "main", "pkg1_1.1_amd64.deb"
                        ),
                        "SHA256": deb_1_1_file.hash_digest.hex(),
                    }
                ).dump()
                + "\n"
            )

        with self.patch_run_indextargets([(packages_path, "main")]) as mock_run:
            plan = task.plan_binaries(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Packages")
        self.assertEqual(plan.add, [])
        self.assertEqual(len(plan.replace), 1)
        self.assertEqual(plan.replace[0].name, "pkg1_1.0_amd64")
        self.assertEqual(plan.replace[0].package["Package"], "pkg1")
        self.assertEqual(plan.replace[0].component, "main")
        self.assertEqual(plan.replace[0].artifact, binary_package_artifacts[0])
        self.assertEqual(plan.remove, [])

    def test_plan_binaries_remove(self) -> None:
        """`plan_binaries` plans to remove binaries from the collection."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        binary_package_artifacts = [
            self.create_binary_package_artifact(
                srcpkg_name=name,
                srcpkg_version=version,
                name=name,
                version=version,
                architecture="amd64",
                paths=[f"{name}_{version}_amd64.deb"],
            )
            for name, version in (("pkg1", "1.0"), ("pkg2", "2.0"))
        ]
        for binary_package_artifact in binary_package_artifacts:
            manager.add_binary_package(
                binary_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
                priority="optional",
            )

        (packages_path := temp_path / "Packages").touch()

        with self.patch_run_indextargets([(packages_path, "main")]) as mock_run:
            plan = task.plan_binaries(temp_path)

        self.assert_ran_indextargets(mock_run, temp_path, "Packages")
        self.assertEqual(plan.add, [])
        self.assertEqual(plan.replace, [])
        self.assertEqual(plan.remove, binary_package_artifacts)

    def test_plan_binaries_inconsistent_different_binaries(self) -> None:
        """`plan_binaries` fails with conflicting binaries."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        paths_and_components = [
            (temp_path / "main_Packages_amd64", "main"),
            (temp_path / "main_Packages_s390x", "main"),
        ]
        amd64_entry = self.write_sample_binary_package(
            temp_path, "pkg1", "1.0", "all"
        )
        s390x_entry = self.write_sample_binary_package(
            temp_path, "pkg1", "1.0", "all", srcpkg_version="1:1.0"
        )
        with open(paths_and_components[0][0], "w") as packages:
            packages.write(amd64_entry.dump() + "\n")
        with open(paths_and_components[1][0], "w") as packages:
            packages.write(s390x_entry.dump() + "\n")

        with self.patch_run_indextargets(paths_and_components):
            self.assertRaisesRegex(
                InconsistentMirrorError,
                r"pkg1_1\.0_all mismatch.  Conflicting Packages entries:\n\n"
                + re.escape(amd64_entry.dump())
                + r"\n\n"
                + re.escape(s390x_entry.dump()),
                task.plan_binaries,
                temp_path,
            )

    def test_plan_binaries_inconsistent_different_component(self) -> None:
        """`plan_binaries` fails with the same binary in multiple components."""
        temp_path = self.create_temporary_directory()
        self.create_suite_collection("bookworm")
        task = self.create_apt_mirror_task("bookworm")

        paths_and_components = [
            (temp_path / "main_Packages", "main"),
            (temp_path / "contrib_Packages", "contrib"),
        ]
        for path, _ in paths_and_components:
            with open(path, "w") as packages:
                packages.write(
                    self.write_sample_binary_package(
                        temp_path, "pkg1", "1.0", "amd64"
                    ).dump()
                    + "\n"
                )

        with self.patch_run_indextargets(paths_and_components):
            self.assertRaisesRegex(
                InconsistentMirrorError,
                r"pkg1_1\.0_amd64 found in multiple components: "
                r"main and contrib",
                task.plan_binaries,
                temp_path,
            )

    @context.disable_permission_checks()
    def test_add_binary(self) -> None:
        """`add_binary` downloads and adds a binary package."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        packages_entry = self.write_sample_binary_package(
            temp_path,
            "libhello1",
            "1.0-1",
            "amd64",
            srcpkg_name="hello",
            srcpkg_version="1:1.0-1",
            section="libs",
        )
        deb_contents = (temp_path / "libhello1_1.0-1_amd64.deb").read_bytes()
        with self.patch_download(
            {
                "libhello1:amd64=1.0-1": {
                    "libhello1_1.0-1_amd64.deb": deb_contents
                }
            }
        ) as mock_run:
            task.add_binary(
                temp_path,
                name="libhello1_1.0-1_amd64",
                binary=packages_entry,
                component="main",
            )

        self.assert_downloaded_binary(
            mock_run, temp_path, "libhello1", "1.0-1", "amd64"
        )
        binary_item = DebianSuiteManager(collection).lookup(
            "binary-version:libhello1_1.0-1_amd64"
        )
        assert binary_item is not None
        assert binary_item.artifact is not None
        self.assert_artifact_matches(
            binary_item.artifact,
            ArtifactCategory.BINARY_PACKAGE,
            work_request.workspace,
            {
                "srcpkg_name": "hello",
                "srcpkg_version": "1:1.0-1",
                "deb_fields": {
                    "Package": "libhello1",
                    "Version": "1.0-1",
                    "Architecture": "amd64",
                    "Maintainer": "Example Maintainer <example@example.org>",
                    "Description": "Example description",
                    "Source": "hello (1:1.0-1)",
                },
                "deb_control_files": ["control"],
            },
            {"libhello1_1.0-1_amd64.deb": deb_contents},
        )
        item = collection.child_items.get()
        self.assertEqual(item.created_by_user, work_request.created_by)
        self.assertEqual(item.data["component"], "main")
        self.assertEqual(item.data["section"], "libs")
        self.assertEqual(item.data["priority"], "optional")

    @context.disable_permission_checks()
    def test_add_binary_renames(self) -> None:
        """`add_binary` renames downloaded binary packages if necessary."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        packages_entry = self.write_sample_binary_package(
            temp_path,
            "libhello1",
            "1:1.0-1",
            "amd64",
            srcpkg_name="hello",
            section="libs",
        )
        deb_contents = (temp_path / "libhello1_1.0-1_amd64.deb").read_bytes()
        with self.patch_download(
            {
                "libhello1:amd64=1:1.0-1": {
                    "libhello1_1%3a1.0-1_amd64.deb": deb_contents
                }
            }
        ) as mock_run:
            task.add_binary(
                temp_path,
                name="libhello1_1:1.0-1_amd64",
                binary=packages_entry,
                component="main",
            )

        self.assert_downloaded_binary(
            mock_run, temp_path, "libhello1", "1:1.0-1", "amd64"
        )
        binary_item = DebianSuiteManager(collection).lookup(
            "binary-version:libhello1_1:1.0-1_amd64"
        )
        assert binary_item is not None
        assert binary_item.artifact is not None
        self.assert_artifact_matches(
            binary_item.artifact,
            ArtifactCategory.BINARY_PACKAGE,
            work_request.workspace,
            {
                "srcpkg_name": "hello",
                "srcpkg_version": "1:1.0-1",
                "deb_fields": {
                    "Package": "libhello1",
                    "Version": "1:1.0-1",
                    "Architecture": "amd64",
                    "Maintainer": "Example Maintainer <example@example.org>",
                    "Description": "Example description",
                    "Source": "hello",
                },
                "deb_control_files": ["control"],
            },
            {"libhello1_1.0-1_amd64.deb": deb_contents},
        )
        item = collection.child_items.get()
        self.assertEqual(item.created_by_user, work_request.created_by)
        self.assertEqual(item.data["component"], "main")
        self.assertEqual(item.data["section"], "libs")
        self.assertEqual(item.data["priority"], "optional")

    @context.disable_permission_checks()
    def test_add_binary_relates_to_source(self) -> None:
        """`add_binary` adds a relation to a matching source package."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        source_package_artifacts = [
            self.create_source_package_artifact(
                name="hello", version=version, paths=[]
            )
            for version in ("1:1.0-1", "1:1.0-2")
        ]
        for source_package_artifact in source_package_artifacts:
            manager.add_source_package(
                source_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
            )

        packages_entry = self.write_sample_binary_package(
            temp_path,
            "libhello1",
            "1.0-1",
            "amd64",
            srcpkg_name="hello",
            srcpkg_version="1:1.0-1",
            section="libs",
        )
        deb_contents = (temp_path / "libhello1_1.0-1_amd64.deb").read_bytes()
        with self.patch_download(
            {
                "libhello1:amd64=1.0-1": {
                    "libhello1_1.0-1_amd64.deb": deb_contents
                }
            }
        ):
            task.add_binary(
                temp_path,
                name="libhello1_1.0-1_amd64",
                binary=packages_entry,
                component="main",
            )

        binary_item = DebianSuiteManager(collection).lookup(
            "binary-version:libhello1_1.0-1_amd64"
        )
        assert binary_item is not None
        assert binary_item.artifact is not None
        relation = binary_item.artifact.relations.get()
        self.assertEqual(relation.target, source_package_artifacts[0])
        self.assertEqual(relation.type, ArtifactRelation.Relations.BUILT_USING)

    @context.disable_permission_checks()
    def test_add_binary_no_source_relation_if_may_reuse_versions(self) -> None:
        """`add_binary` doesn't add source relations if `may_reuse_versions`."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        collection.data["may_reuse_versions"] = True
        collection.save()
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        source_package_artifact = self.create_source_package_artifact(
            name="hello", version="1:1.0-1", paths=[]
        )
        manager.add_source_package(
            source_package_artifact,
            user=work_request.created_by,
            component="main",
            section="devel",
        )

        packages_entry = self.write_sample_binary_package(
            temp_path,
            "libhello1",
            "1.0-1",
            "amd64",
            srcpkg_name="hello",
            srcpkg_version="1:1.0-1",
            section="libs",
        )
        deb_contents = (temp_path / "libhello1_1.0-1_amd64.deb").read_bytes()
        with self.patch_download(
            {
                "libhello1:amd64=1.0-1": {
                    "libhello1_1.0-1_amd64.deb": deb_contents
                }
            }
        ):
            task.add_binary(
                temp_path,
                name="libhello1_1.0-1_amd64",
                binary=packages_entry,
                component="main",
            )

        binary_item = DebianSuiteManager(collection).lookup(
            "binary-version:libhello1_1.0-1_amd64"
        )
        assert binary_item is not None
        assert binary_item.artifact is not None
        self.assertFalse(binary_item.artifact.relations.exists())

    @context.disable_permission_checks()
    def test_update_binaries(self) -> None:
        """`update_binaries` executes a plan to update binaries."""
        temp_path = self.create_temporary_directory()
        collection = self.create_suite_collection("bookworm")
        collection.data["may_reuse_versions"] = True
        collection.save()
        manager = DebianSuiteManager(collection)
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        binary_package_artifacts = [
            self.create_binary_package_artifact(
                srcpkg_name=name,
                srcpkg_version=version,
                name=name,
                version=version,
                architecture="amd64",
                paths=[f"{name}_{version}_amd64.deb"],
            )
            for name, version in (("to-replace", "1.0"), ("to-remove", "1.0"))
        ]
        for binary_package_artifact in binary_package_artifacts:
            manager.add_binary_package(
                binary_package_artifact,
                user=work_request.created_by,
                component="main",
                section="devel",
                priority="optional",
            )

        to_add_entry = self.write_sample_binary_package(
            temp_path, "to-add", "1.0", "amd64"
        )
        to_add_deb_contents = (temp_path / "to-add_1.0_amd64.deb").read_bytes()
        to_replace_entry = self.write_sample_binary_package(
            temp_path, "to-replace", "1.0", "amd64"
        )
        to_replace_deb_contents = (
            temp_path / "to-replace_1.0_amd64.deb"
        ).read_bytes()
        plan = Plan[Packages](
            add=[
                PlanAdd[Packages](
                    name="to-add", package=to_add_entry, component="main"
                )
            ],
            replace=[
                PlanReplace[Packages](
                    name="to-replace",
                    package=to_replace_entry,
                    component="main",
                    artifact=binary_package_artifacts[0],
                )
            ],
            remove=[binary_package_artifacts[1]],
        )

        with self.patch_download(
            {
                "to-add:amd64=1.0": {
                    "to-add_1.0_amd64.deb": to_add_deb_contents,
                },
                "to-replace:amd64=1.0": {
                    "to-replace_1.0_amd64.deb": to_replace_deb_contents,
                },
            }
        ):
            task.update_binaries(temp_path, plan)

        active_items = {
            item.name: item
            for item in collection.child_items.all()
            if item.removed_at is None
        }
        self.assertEqual(
            active_items.keys(), {"to-add_1.0_amd64", "to-replace_1.0_amd64"}
        )
        assert active_items["to-add_1.0_amd64"].artifact is not None
        self.assert_artifact_files_match(
            active_items["to-add_1.0_amd64"].artifact,
            {"to-add_1.0_amd64.deb": to_add_deb_contents},
        )
        assert active_items["to-replace_1.0_amd64"].artifact is not None
        self.assert_artifact_files_match(
            active_items["to-replace_1.0_amd64"].artifact,
            {"to-replace_1.0_amd64.deb": to_replace_deb_contents},
        )

    def test_execute(self) -> None:
        """
        Executing the task runs through the full sequence.

        Most of the details are tested elsewhere, but we do enough to check
        that the task can add to its collection.
        """
        temp_path = self.create_temporary_directory()
        with open(temp_path / "Sources", "w") as sources:
            sources.write(
                self.write_sample_source_package(
                    temp_path, "hello", "1.0"
                ).dump()
                + "\n"
            )
        with open(temp_path / "Packages", "w") as packages:
            packages.write(
                self.write_sample_binary_package(
                    temp_path, "hello", "1.0", "amd64"
                ).dump()
                + "\n"
            )

        collection = self.create_suite_collection("bookworm")
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        def fake_run(
            args: list[str],
            cwd: str | os.PathLike[str] | None,
            env: dict[str, str],
            **kwargs: Any,
        ) -> CompletedProcess[str]:
            apt_lists_path = (
                Path(env["APT_CONFIG"]).parent.parent.parent
                / "var/lib/apt/lists"
            )
            stdout = ""
            match args:
                case ["apt-get", "update"]:
                    for name in ("Sources", "Packages"):
                        shutil.copy(temp_path / name, apt_lists_path / name)
                case ["apt-get", "indextargets", "Identifier: Sources"]:
                    stdout = dedent(
                        f"""\
                        Filename: {apt_lists_path}/Sources
                        Component: main

                        """
                    )
                case ["apt-get", "indextargets", "Identifier: Packages"]:
                    stdout = dedent(
                        f"""\
                        Filename: {apt_lists_path}/Packages
                        Component: main

                        """
                    )
                case ["apt-get", *_, "source", "hello=1.0"]:
                    assert cwd is not None
                    for name in ("hello_1.0.dsc", "hello_1.0.tar.xz"):
                        shutil.copy(temp_path / name, Path(cwd) / name)
                case ["apt-get", "download", "hello:amd64=1.0"]:
                    assert cwd is not None
                    shutil.copy(
                        temp_path / "hello_1.0_amd64.deb",
                        Path(cwd) / "hello_1.0_amd64.deb",
                    )
                case _ as unreachable:
                    raise AssertionError(
                        f"Unexpected subprocess arguments: {unreachable}"
                    )
            return CompletedProcess(
                args=args, returncode=0, stdout=stdout, stderr=""
            )

        # Pretend that a lock for another collection is held, to make sure
        # that that doesn't interfere.
        non_conflicting_alias = "non-conflicting-lock"
        non_conflicting_connection = connection.copy(non_conflicting_alias)
        connections[non_conflicting_alias] = non_conflicting_connection
        try:
            with (
                advisory_lock(
                    (LockType.APT_MIRROR, (collection.id + 1) & (2**31 - 1)),
                    wait=False,
                    using=non_conflicting_alias,
                ) as acquired,
                patch("subprocess.run", side_effect=fake_run),
            ):
                assert acquired
                self.assertTrue(task.execute())
        finally:
            del connections[non_conflicting_alias]
            non_conflicting_connection.close()

        self.assertQuerySetEqual(
            collection.child_items.values_list("name", flat=True),
            ["hello_1.0", "hello_1.0_amd64"],
            ordered=False,
        )

    def test_execute_lock_error(self) -> None:
        """The task fails if its lock is already held."""
        collection = self.create_suite_collection("bookworm")
        work_request = self.playground.create_work_request()
        task = self.create_apt_mirror_task("bookworm")
        task.set_work_request(work_request)

        conflicting_alias = "conflicting-lock"
        conflicting_connection = connection.copy(conflicting_alias)
        connections[conflicting_alias] = conflicting_connection
        try:
            with (
                advisory_lock(
                    (LockType.APT_MIRROR, collection.id & (2**31 - 1)),
                    wait=False,
                    using=conflicting_alias,
                ) as acquired,
                self.assertRaisesRegex(
                    LockError,
                    "Another APTMirror task for bookworm is already running",
                ),
            ):
                assert acquired
                task.execute()
        finally:
            del connections[conflicting_alias]
            conflicting_connection.close()

    def test_label(self) -> None:
        """Test get_label."""
        task = self.create_apt_mirror_task(
            "bookworm", url="https://deb.example.org/", suite="./"
        )
        self.assertEqual(
            task.get_label(), "mirror bookworm from https://deb.example.org/"
        )
