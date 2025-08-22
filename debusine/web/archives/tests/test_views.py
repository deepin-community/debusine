# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test views for package archives."""

import hashlib
from abc import ABC
from datetime import datetime, timedelta
from pathlib import PurePath
from typing import ClassVar, overload

from django.conf import settings
from django.utils import timezone
from django.utils.cache import has_vary_header
from rest_framework import status

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.models import Artifact, Collection, CollectionItem
from debusine.db.playground import scenarios
from debusine.test.django import TestCase, TestResponseType


class ArchiveFileViewTests(TestCase, ABC):
    """Helper methods for testing archive file views."""

    playground_memory_file_store = False
    scenario = scenarios.DefaultContext()

    archive: ClassVar[Collection]
    suites: ClassVar[dict[str, Collection]]

    # Path used for some tests of generic behaviour in each subclass.
    example_path: str
    # Are paths tested here normally mutable?
    expect_mutable_no_archive: bool
    expect_mutable: bool

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.archive = cls.scenario.workspace.get_singleton_collection(
            user=cls.scenario.user, category=CollectionCategory.ARCHIVE
        )
        cls.suites = {
            name: cls.playground.create_collection(
                name, CollectionCategory.SUITE, workspace=cls.scenario.workspace
            )
            for name in ("bookworm", "trixie")
        }
        for suite_name, suite in cls.suites.items():
            cls.archive.manager.add_collection(suite, user=cls.scenario.user)

    def create_source_package(
        self, name: str, version: str, paths: list[str] | dict[str, bytes]
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
        )
        return artifact

    def create_binary_package(
        self,
        srcpkg_name: str,
        srcpkg_version: str,
        name: str,
        version: str,
        architecture: str,
        paths: list[str] | dict[str, bytes],
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
        )
        return artifact

    def create_repository_index(
        self, path: str, contents: bytes | None = None
    ) -> Artifact:
        """Create a minimal ``debian:repository-index`` artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.REPOSITORY_INDEX,
            workspace=self.scenario.workspace,
            paths=[path] if contents is None else {path: contents},
            create_files=True,
        )
        return artifact

    @overload
    def format_snapshot(self, snapshot: None) -> None: ...

    @overload
    def format_snapshot(self, snapshot: datetime) -> str: ...

    def format_snapshot(self, snapshot: datetime | None) -> str | None:
        """Format a snapshot timestamp as a URL segment."""
        return None if snapshot is None else snapshot.strftime("%Y%m%dT%H%M%SZ")

    def archive_file_get(
        self,
        *,
        workspace_name: str | None = None,
        snapshot: datetime | None = None,
        path: str,
    ) -> TestResponseType:
        """Get a file from an archive."""
        scope_name = self.scenario.scope.name
        if workspace_name is None:
            workspace_name = self.scenario.workspace.name
        url = f"/{scope_name}/{workspace_name}"
        if snapshot is not None:
            url += "/" + self.format_snapshot(snapshot)
        url += f"/{path}"
        return self.client.get(
            url, HTTP_HOST=settings.DEBUSINE_DEBIAN_ARCHIVE_FQDN
        )

    def assert_has_caching_headers(
        self, response: TestResponseType, *, mutable: bool
    ) -> None:
        self.assertEqual(
            response.headers["Cache-Control"],
            "max-age=1800, proxy-revalidate" if mutable else "max-age=31536000",
        )
        # https://github.com/typeddjango/django-stubs/pull/2704
        self.assertTrue(
            has_vary_header(response, "Authorization")  # type: ignore[arg-type]
        )

    def assert_response_ok(
        self, response: TestResponseType, *, contents: bytes, mutable: bool
    ) -> None:
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            b"".join(getattr(response, "streaming_content")), contents
        )
        self.assert_has_caching_headers(response, mutable=mutable)

    def test_workspace_not_found(self) -> None:
        for snapshot in (None, timezone.now()):
            with self.subTest(snapshot=self.format_snapshot(snapshot)):
                response = self.archive_file_get(
                    workspace_name="nonexistent",
                    snapshot=snapshot,
                    path=self.example_path,
                )

                self.assertResponseProblem(
                    response,
                    "Workspace not found",
                    detail_pattern=(
                        "Workspace nonexistent not found in scope debusine"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )
                self.assert_has_caching_headers(
                    response,
                    mutable=self.expect_mutable_no_archive and snapshot is None,
                )

    def test_archive_not_found(self) -> None:
        self.archive.child_items.all().delete()
        self.archive.delete()

        for snapshot in (None, timezone.now()):
            with self.subTest(snapshot=self.format_snapshot(snapshot)):
                response = self.archive_file_get(
                    snapshot=snapshot, path=self.example_path
                )

                self.assertResponseProblem(
                    response,
                    "Archive not found",
                    detail_pattern=(
                        f"No {CollectionCategory.ARCHIVE} collection found in "
                        f"{self.scenario.workspace}"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )
                self.assert_has_caching_headers(
                    response,
                    mutable=self.expect_mutable_no_archive and snapshot is None,
                )

    def test_file_not_found(self) -> None:
        for snapshot in (None, timezone.now()):
            with self.subTest(snapshot=self.format_snapshot(snapshot)):
                response = self.archive_file_get(
                    snapshot=snapshot, path=self.example_path
                )

                self.assertResponseProblem(
                    response,
                    "No FileInArtifact matches the given query.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
                self.assert_has_caching_headers(
                    response, mutable=self.expect_mutable and snapshot is None
                )


class DistsByHashFileViewTests(ArchiveFileViewTests):
    """Test retrieving ``by-hash`` files from ``dists/``."""

    example_path = "dists/bookworm/main/source/by-hash/SHA256/00"
    expect_mutable_no_archive = False
    expect_mutable = False

    def test_current(self) -> None:
        old_contents = b"Old Sources file\n"
        old_sha256 = hashlib.sha256(old_contents).hexdigest()
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Sources.xz", contents=old_contents),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
        )
        new_contents = b"New Sources file\n"
        new_sha256 = hashlib.sha256(new_contents).hexdigest()
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Sources.xz", contents=new_contents),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
            replace=True,
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Packages.xz"),
            user=self.scenario.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
        )
        self.suites["trixie"].manager.add_artifact(
            self.create_repository_index("Sources.xz"),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
        )

        response = self.archive_file_get(
            path=f"dists/bookworm/main/source/by-hash/SHA256/{old_sha256}"
        )

        self.assert_response_ok(response, contents=old_contents, mutable=False)

        response = self.archive_file_get(
            path=f"dists/bookworm/main/source/by-hash/SHA256/{new_sha256}"
        )

        self.assert_response_ok(response, contents=new_contents, mutable=False)

    def test_snapshot(self) -> None:
        one_month_ago = (timezone.now() - timedelta(days=30)).replace(
            microsecond=0
        )
        self.archive.child_items.filter(
            category=CollectionCategory.SUITE
        ).update(created_at=one_month_ago)
        old_contents = b"Old Sources file\n"
        old_sha256 = hashlib.sha256(old_contents).hexdigest()
        old_item = self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Sources.xz", contents=old_contents),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
        )
        old_item.created_at = one_month_ago
        old_item.save()
        new_contents = b"New Sources file\n"
        new_sha256 = hashlib.sha256(new_contents).hexdigest()
        new_item = self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Sources.xz", contents=new_contents),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
            replace=True,
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Packages.xz"),
            user=self.scenario.user,
            variables={"path": "main/binary-amd64/Packages.xz"},
        )
        self.suites["trixie"].manager.add_artifact(
            self.create_repository_index("Sources.xz"),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
        )

        for snapshot, sha256, expected_contents in (
            (old_item.created_at - timedelta(seconds=1), old_sha256, None),
            (old_item.created_at - timedelta(seconds=1), new_sha256, None),
            (old_item.created_at, old_sha256, old_contents),
            (old_item.created_at, new_sha256, None),
            (
                new_item.created_at - timedelta(seconds=1),
                old_sha256,
                old_contents,
            ),
            (new_item.created_at - timedelta(seconds=1), new_sha256, None),
            # Snapshot URLs don't include a microsecond component, so add a
            # second to avoid rounding problems.
            (
                new_item.created_at + timedelta(seconds=1),
                old_sha256,
                old_contents,
            ),
            (
                new_item.created_at + timedelta(seconds=1),
                new_sha256,
                new_contents,
            ),
        ):
            with self.subTest(
                snapshot=self.format_snapshot(snapshot), sha256=sha256
            ):
                response = self.archive_file_get(
                    snapshot=snapshot,
                    path=f"dists/bookworm/main/source/by-hash/SHA256/{sha256}",
                )

                if expected_contents is None:
                    self.assertResponseProblem(
                        response,
                        "No FileInArtifact matches the given query.",
                        status_code=status.HTTP_404_NOT_FOUND,
                    )
                else:
                    self.assert_response_ok(
                        response, contents=expected_contents, mutable=False
                    )


class DistsFileViewTests(ArchiveFileViewTests):
    """Test retrieving non-``by-hash`` files from ``dists/``."""

    example_path = "dists/bookworm/Release"
    expect_mutable_no_archive = True
    expect_mutable = True

    def test_current(self) -> None:
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index(
                "Release", contents=b"Old Release file\n"
            ),
            user=self.scenario.user,
            variables={"path": "Release"},
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index(
                "Release", contents=b"New Release file\n"
            ),
            user=self.scenario.user,
            variables={"path": "Release"},
            replace=True,
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Sources.xz"),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
        )
        self.suites["trixie"].manager.add_artifact(
            self.create_repository_index("Release"),
            user=self.scenario.user,
            variables={"path": "Release"},
        )

        response = self.archive_file_get(path="dists/bookworm/Release")

        self.assert_response_ok(
            response, contents=b"New Release file\n", mutable=True
        )

    def test_snapshot(self) -> None:
        one_month_ago = (timezone.now() - timedelta(days=30)).replace(
            microsecond=0
        )
        self.archive.child_items.filter(
            category=CollectionCategory.SUITE
        ).update(created_at=one_month_ago)
        old_contents = b"Old Release file\n"
        old_item = self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Release", contents=old_contents),
            user=self.scenario.user,
            variables={"path": "Release"},
        )
        old_item.created_at = one_month_ago
        old_item.save()
        new_contents = b"New Release file\n"
        new_item = self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Release", contents=new_contents),
            user=self.scenario.user,
            variables={"path": "Release"},
            replace=True,
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_repository_index("Sources.xz"),
            user=self.scenario.user,
            variables={"path": "main/source/Sources.xz"},
        )
        self.suites["trixie"].manager.add_artifact(
            self.create_repository_index("Release"),
            user=self.scenario.user,
            variables={"path": "Release"},
        )

        for snapshot, expected_contents in (
            (old_item.created_at - timedelta(seconds=1), None),
            (old_item.created_at, old_contents),
            (new_item.created_at - timedelta(seconds=1), old_contents),
            # Snapshot URLs don't include a microsecond component, so add a
            # second to avoid rounding problems.
            (new_item.created_at + timedelta(seconds=1), new_contents),
        ):
            with self.subTest(snapshot=self.format_snapshot(snapshot)):
                response = self.archive_file_get(
                    snapshot=snapshot, path="dists/bookworm/Release"
                )

                if expected_contents is None:
                    self.assertResponseProblem(
                        response,
                        "No FileInArtifact matches the given query.",
                        status_code=status.HTTP_404_NOT_FOUND,
                    )
                else:
                    self.assert_response_ok(
                        response, contents=expected_contents, mutable=False
                    )


class PoolFileViewTests(ArchiveFileViewTests):
    """Test retrieving files from ``pool/``."""

    example_path = "pool/main/h/hello/hello_1.0.dsc"
    expect_mutable_no_archive = True
    expect_mutable = False

    def test_current_source(self) -> None:
        for suite_name, component, name, version, paths in (
            (
                "bookworm",
                "main",
                "hello",
                "1.0",
                ["hello_1.0.dsc", "hello_1.0.tar.xz"],
            ),
            (
                "bookworm",
                "contrib",
                "hello",
                "1.1-1",
                [
                    "hello_1.1-1.dsc",
                    "hello_1.1-1.debian.tar.xz",
                    "hello_1.1.orig.tar.xz",
                ],
            ),
            (
                "bookworm",
                "contrib",
                "hello",
                "1.1-2",
                [
                    "hello_1.1-2.dsc",
                    "hello_1.1-2.debian.tar.xz",
                    "hello_1.1.orig.tar.xz",
                ],
            ),
            ("bookworm", "main", "base-files", "1.0", ["base-files_1.0.dsc"]),
            (
                "trixie",
                "main",
                "hello",
                "1.0",
                ["hello_1.0.dsc", "hello_1.0.tar.xz"],
            ),
        ):
            self.suites[suite_name].manager.add_artifact(
                self.create_source_package(
                    name,
                    version,
                    {path: f"Contents of {path}".encode() for path in paths},
                ),
                user=self.scenario.user,
                variables={"component": component, "section": "devel"},
            )

        for path in (
            "pool/main/h/hello/hello_1.0.dsc",
            "pool/main/h/hello/hello_1.0.tar.xz",
            "pool/contrib/h/hello/hello_1.1-1.dsc",
            "pool/contrib/h/hello/hello_1.1-1.debian.tar.xz",
            "pool/contrib/h/hello/hello_1.1-2.dsc",
            "pool/contrib/h/hello/hello_1.1-2.debian.tar.xz",
            "pool/contrib/h/hello/hello_1.1.orig.tar.xz",
        ):
            response = self.archive_file_get(path=path)

            self.assert_response_ok(
                response,
                contents=f"Contents of {PurePath(path).name}".encode(),
                mutable=False,
            )

    def test_current_binary(self) -> None:
        for (
            suite_name,
            component,
            srcpkg_name,
            srcpkg_version,
            name,
            version,
            architecture,
        ) in (
            ("bookworm", "main", "hello", "1.0", "libhello1", "1.0", "amd64"),
            ("bookworm", "main", "hello", "1.0", "libhello1", "1.0", "s390x"),
            ("bookworm", "main", "hello", "1.0", "libhello-doc", "1.0", "all"),
            (
                "bookworm",
                "contrib",
                "hello",
                "1.1",
                "libhello1",
                "1:1.1",
                "amd64",
            ),
            (
                "bookworm",
                "main",
                "base-files",
                "1.0",
                "base-files",
                "1.0",
                "amd64",
            ),
            ("trixie", "main", "hello", "1.0", "libhello1", "1.0", "amd64"),
        ):
            deb_path = f"{name}_{version.split(':')[-1]}_{architecture}.deb"
            self.suites[suite_name].manager.add_artifact(
                self.create_binary_package(
                    srcpkg_name,
                    srcpkg_version,
                    name,
                    version,
                    architecture,
                    {deb_path: f"Contents of {deb_path}".encode()},
                ),
                user=self.scenario.user,
                variables={
                    "component": component,
                    "section": "devel",
                    "priority": "optional",
                },
            )

        for path in (
            "pool/main/h/hello/libhello1_1.0_amd64.deb",
            "pool/main/h/hello/libhello1_1.0_s390x.deb",
            "pool/main/h/hello/libhello-doc_1.0_all.deb",
            "pool/contrib/h/hello/libhello1_1.1_amd64.deb",
        ):
            response = self.archive_file_get(path=path)

            self.assert_response_ok(
                response,
                contents=f"Contents of {PurePath(path).name}".encode(),
                mutable=False,
            )

    def test_snapshot_source(self) -> None:
        one_month_ago = (timezone.now() - timedelta(days=30)).replace(
            microsecond=0
        )
        one_day_ago = (timezone.now() - timedelta(days=1)).replace(
            microsecond=0
        )
        self.archive.child_items.filter(
            category=CollectionCategory.SUITE
        ).update(created_at=one_month_ago)
        items: list[CollectionItem] = []
        for version in ("1.1-1", "1.1-2", "1.1-3"):
            items.append(
                self.suites["bookworm"].manager.add_artifact(
                    self.create_source_package(
                        "hello",
                        version,
                        {
                            path: f"Contents of {path}".encode()
                            for path in (
                                f"hello_{version}.dsc",
                                f"hello_{version.split('-')[0]}.orig.tar.xz",
                            )
                        },
                    ),
                    user=self.scenario.user,
                    variables={"component": "main", "section": "devel"},
                )
            )
        items[0].created_at = one_month_ago
        items[0].removed_at = one_day_ago
        items[0].save()
        items[1].created_at = one_day_ago
        items[1].save()
        start = items[0].created_at - timedelta(seconds=1)
        # Snapshot URLs don't include a microsecond component, so add a
        # second to avoid rounding problems.
        end = items[2].created_at + timedelta(seconds=1)

        for snapshot, name, expected in (
            (start, "hello_1.1-1.dsc", False),
            (start, "hello_1.1-2.dsc", False),
            (start, "hello_1.1-3.dsc", False),
            (start, "hello_1.1.orig.tar.xz", False),
            (items[0].created_at, "hello_1.1-1.dsc", True),
            (items[0].created_at, "hello_1.1-2.dsc", False),
            (items[0].created_at, "hello_1.1-3.dsc", False),
            (items[0].created_at, "hello_1.1.orig.tar.xz", True),
            (items[1].created_at, "hello_1.1-1.dsc", False),
            (items[1].created_at, "hello_1.1-2.dsc", True),
            (items[1].created_at, "hello_1.1-3.dsc", False),
            (items[1].created_at, "hello_1.1.orig.tar.xz", True),
            (end, "hello_1.1-1.dsc", False),
            (end, "hello_1.1-2.dsc", True),
            (end, "hello_1.1-3.dsc", True),
            (end, "hello_1.1.orig.tar.xz", True),
        ):
            with self.subTest(
                snapshot=self.format_snapshot(snapshot), name=name
            ):
                response = self.archive_file_get(
                    snapshot=snapshot, path=f"pool/main/h/hello/{name}"
                )

                if expected:
                    self.assert_response_ok(
                        response,
                        contents=f"Contents of {name}".encode(),
                        mutable=False,
                    )
                else:
                    self.assertResponseProblem(
                        response,
                        "No FileInArtifact matches the given query.",
                        status_code=status.HTTP_404_NOT_FOUND,
                    )

    def test_wrong_component(self) -> None:
        self.suites["bookworm"].manager.add_artifact(
            self.create_source_package("hello", "1.0", ["hello_1.0.dsc"]),
            user=self.scenario.user,
            variables={"component": "main", "section": "devel"},
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_binary_package(
                "hello", "1.0", "hello", "1.0", "amd64", ["hello_1.0_amd64.deb"]
            ),
            user=self.scenario.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        for name in ("hello_1.0.dsc", "hello_1.0_amd64.deb"):
            response = self.archive_file_get(
                path=f"pool/contrib/h/hello/{name}"
            )

            self.assertResponseProblem(
                response,
                "No FileInArtifact matches the given query.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    def test_wrong_source_prefix(self) -> None:
        self.suites["bookworm"].manager.add_artifact(
            self.create_source_package("hello", "1.0", ["hello_1.0.dsc"]),
            user=self.scenario.user,
            variables={"component": "main", "section": "devel"},
        )
        self.suites["bookworm"].manager.add_artifact(
            self.create_binary_package(
                "hello", "1.0", "hello", "1.0", "amd64", ["hello_1.0_amd64.deb"]
            ),
            user=self.scenario.user,
            variables={
                "component": "main",
                "section": "devel",
                "priority": "optional",
            },
        )

        for name in ("hello_1.0.dsc", "hello_1.0_amd64.deb"):
            response = self.archive_file_get(path=f"pool/main/a/hello/{name}")

            self.assertResponseProblem(
                response,
                "No FileInArtifact matches the given query.",
                status_code=status.HTTP_404_NOT_FOUND,
            )


class TopLevelFileViewTests(ArchiveFileViewTests):
    """Test retrieving top-level files, not in any suite."""

    example_path = "README"
    expect_mutable_no_archive = True
    expect_mutable = True

    def test_current(self) -> None:
        social_contract_contents = (
            b'"Social Contract" with the Free Software Community\n'
        )
        artifact = self.create_repository_index(
            "social-contract.txt", contents=social_contract_contents
        )
        self.archive.manager.add_artifact(
            artifact,
            user=self.scenario.user,
            variables={"path": "doc/social-contract.txt"},
        )
        self.archive.manager.add_artifact(
            self.create_repository_index("README"),
            user=self.scenario.user,
            variables={"path": "README"},
        )

        response = self.archive_file_get(path="doc/social-contract.txt")

        self.assert_response_ok(
            response, contents=social_contract_contents, mutable=True
        )

    def test_snapshot(self) -> None:
        old_social_contract_contents = (
            b'"Social Contract" with the Free Software Community\n'
        )
        old_artifact = self.create_repository_index(
            "social-contract.txt", contents=old_social_contract_contents
        )
        old_item = self.archive.manager.add_artifact(
            old_artifact,
            user=self.scenario.user,
            variables={"path": "doc/social-contract.txt"},
        )
        old_item.created_at = (timezone.now() - timedelta(days=30)).replace(
            microsecond=0
        )
        old_item.save()
        new_social_contract_contents = (
            b'"Social Contract" with the Free Software Community (v2)\n'
        )
        new_artifact = self.create_repository_index(
            "social-contract.txt", contents=new_social_contract_contents
        )
        new_item = self.archive.manager.add_artifact(
            new_artifact,
            user=self.scenario.user,
            variables={"path": "doc/social-contract.txt"},
            replace=True,
        )

        for snapshot, expected_contents in (
            (old_item.created_at - timedelta(seconds=1), None),
            (old_item.created_at, old_social_contract_contents),
            (
                new_item.created_at - timedelta(seconds=1),
                old_social_contract_contents,
            ),
            # Snapshot URLs don't include a microsecond component, so add a
            # second to avoid rounding problems.
            (
                new_item.created_at + timedelta(seconds=1),
                new_social_contract_contents,
            ),
        ):
            with self.subTest(snapshot=self.format_snapshot(snapshot)):
                response = self.archive_file_get(
                    snapshot=snapshot, path="doc/social-contract.txt"
                )

                if expected_contents is None:
                    self.assertResponseProblem(
                        response,
                        "No FileInArtifact matches the given query.",
                        status_code=status.HTTP_404_NOT_FOUND,
                    )
                else:
                    self.assert_response_ok(
                        response, contents=expected_contents, mutable=False
                    )


# Avoid running tests from common base class.
del ArchiveFileViewTests
