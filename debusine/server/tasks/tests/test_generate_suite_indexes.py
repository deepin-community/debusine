# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the task to generate indexes for a suite."""

import hashlib
import lzma
from datetime import datetime, timezone
from pathlib import PurePath
from textwrap import dedent
from typing import Any

from django.db import connection, connections
from django_pglocks import advisory_lock

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.db.locks import LockError, LockType
from debusine.db.models import Artifact, Collection, CollectionItem
from debusine.db.playground import scenarios
from debusine.server.tasks import GenerateSuiteIndexes
from debusine.server.tasks.generate_suite_indexes import IndexInfo
from debusine.server.tasks.models import (
    GenerateSuiteIndexesData,
    GenerateSuiteIndexesDynamicData,
)
from debusine.tasks import TaskConfigError
from debusine.tasks.models import LookupSingle, WorkerType
from debusine.tasks.server import CollectionInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabase
from debusine.test.django import TestCase


class GenerateSuiteIndexesTests(TestCase):
    """Tests for :py:class:`GenerateSuiteIndexes`."""

    SAMPLE_TASK_DATA = {
        "suite_collection": f"bookworm@{CollectionCategory.SUITE}",
        "generate_at": datetime.now(timezone.utc),
    }

    def create_suite_collection(
        self, name: str, data: dict[str, Any] | None = None
    ) -> Collection:
        """Create a `debian:suite` collection."""
        return self.playground.create_collection(
            name=name, category=CollectionCategory.SUITE, data=data
        )

    def create_task(
        self,
        *,
        suite_collection: LookupSingle,
        generate_at: datetime | None = None,
        assign_new_worker: bool = True,
    ) -> GenerateSuiteIndexes:
        """Create an instance of the :py:class:`GenerateSuiteIndexes` task."""
        if generate_at is None:
            generate_at = datetime.now(timezone.utc)
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.SERVER,
            task_name="generatesuiteindexes",
            task_data=GenerateSuiteIndexesData(
                suite_collection=suite_collection, generate_at=generate_at
            ),
        )
        if assign_new_worker:
            work_request.assign_worker(
                self.playground.create_worker(worker_type=WorkerType.CELERY)
            )
        task = work_request.get_task()
        assert isinstance(task, GenerateSuiteIndexes)
        task.set_work_request(work_request)
        return task

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task_db = FakeTaskDatabase(
            single_collection_lookups={
                # suite_collection
                (suite_collection, None): CollectionInfo(
                    id=1, category=CollectionCategory.SUITE, data={}
                )
            }
        )
        task = self.create_task(
            suite_collection=suite_collection, assign_new_worker=False
        )

        self.assertEqual(
            task.compute_dynamic_data(task_db),
            GenerateSuiteIndexesDynamicData(suite_collection_id=1),
        )

    def test_get_input_artifacts_ids(self) -> None:
        """``get_input_artifacts_ids`` returns the empty list."""
        self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        self.assertEqual(task.get_input_artifacts_ids(), [])

    def test_suite(self) -> None:
        """``suite`` returns the requested suite collection."""
        bookworm = self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        self.assertEqual(task.suite, bookworm)

    def test_components(self) -> None:
        """``components`` returns the configured components."""
        self.create_suite_collection(
            "bookworm", data={"components": ["main", "contrib"]}
        )
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        self.assertEqual(task.components, ["main", "contrib"])

    def test_components_unset(self) -> None:
        """``components`` raises exception: components not configured."""
        self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        with self.assertRaisesRegex(
            TaskConfigError,
            f"Collection '{suite_collection}' has no 'components' "
            f"configuration",
        ):
            task.components

    def test_components_not_list(self) -> None:
        """``components`` raises exception: components not a list."""
        self.create_suite_collection(
            "bookworm", data={"components": "not-a-list"}
        )
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        with self.assertRaisesRegex(
            TaskConfigError, "Expected 'components' as a list; got 'not-a-list'"
        ):
            task.components

    def test_architectures(self) -> None:
        """``architectures`` returns the configured architectures."""
        self.create_suite_collection(
            "bookworm", data={"architectures": ["amd64", "i386"]}
        )
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        self.assertEqual(task.architectures, ["amd64", "i386"])

    def test_architectures_unset(self) -> None:
        """``architectures`` raises exception: architectures not configured."""
        self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        with self.assertRaisesRegex(
            TaskConfigError,
            f"Collection '{suite_collection}' has no 'architectures' "
            f"configuration",
        ):
            task.architectures

    def test_architectures_not_list(self) -> None:
        """``architectures`` raises exception: architectures not a list."""
        self.create_suite_collection(
            "bookworm", data={"architectures": "not-a-list"}
        )
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        with self.assertRaisesRegex(
            TaskConfigError,
            "Expected 'architectures' as a list; got 'not-a-list'",
        ):
            task.architectures

    def test_generate_sources(self) -> None:
        """``generate_sources`` generates a ``Sources`` file."""
        scenario = scenarios.UIPlayground(set_current=True)
        self.playground.build_scenario(scenario, set_current=True)
        suite_collection = f"play_bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        temp_path = self.create_temporary_directory()

        task.generate_sources(temp_path=temp_path, component="main")

        dpkg = task.suite.manager.lookup("source:dpkg")
        assert dpkg is not None
        assert dpkg.artifact is not None
        dpkg_dsc = dpkg.artifact.fileinartifact_set.get(
            path="dpkg_1.21.22.dsc"
        ).file
        dpkg_orig = dpkg.artifact.fileinartifact_set.get(
            path="dpkg_1.21.22.tar.gz"
        ).file
        hello = task.suite.manager.lookup("source:hello")
        assert hello is not None
        assert hello.artifact is not None
        hello_dsc = hello.artifact.fileinartifact_set.get(
            path="hello_1.0-1.dsc"
        ).file
        hello_debian = hello.artifact.fileinartifact_set.get(
            path="hello_1.0-1.debian.tar.xz"
        ).file
        hello_orig = hello.artifact.fileinartifact_set.get(
            path="hello_1.0.orig.tar.gz"
        ).file
        expected_sources = dedent(
            f"""\
            Package: dpkg
            Binary: dpkg, dpkg-doc
            Version: 1.21.22
            Maintainer: Example Maintainer <example@example.org>
            Architecture: any
            Standards-Version: 4.3.0
            Format: 3.0 (quilt)
            Checksums-Sha256:
             {dpkg_dsc.sha256.hex()} {dpkg_dsc.size} dpkg_1.21.22.dsc
             {dpkg_orig.sha256.hex()} {dpkg_orig.size} dpkg_1.21.22.tar.gz
            Homepage: http://www.gnu.org/software/dpkg/
            Package-List:{" "}
             dpkg deb devel optional arch=any
             dpkg-doc deb devel optional arch=any
            Directory: pool/main/d/dpkg
            Priority: source
            Section: devel

            Package: hello
            Binary: hello
            Version: 1.0-1
            Maintainer: Example Maintainer <example@example.org>
            Architecture: any
            Standards-Version: 4.3.0
            Format: 3.0 (quilt)
            Checksums-Sha256:
             {hello_debian.sha256.hex()} {hello_debian.size} hello_1.0-1.debian.tar.xz
             {hello_dsc.sha256.hex()} {hello_dsc.size} hello_1.0-1.dsc
             {hello_orig.sha256.hex()} {hello_orig.size} hello_1.0.orig.tar.gz
            Homepage: http://www.gnu.org/software/hello/
            Package-List:{" "}
             hello deb devel optional arch=any
            Directory: pool/main/h/hello
            Priority: source
            Section: devel

            """  # noqa: E501
        )

        self.assertIsNone(
            task.suite.manager.lookup("index:main/source/Sources")
        )
        sources_xz_item = task.suite.manager.lookup(
            "index:main/source/Sources.xz"
        )
        assert sources_xz_item is not None
        self.assertEqual(sources_xz_item.created_at, task.data.generate_at)
        assert sources_xz_item.artifact is not None
        sources_xz_file = sources_xz_item.artifact.files.get()
        file_backend = scenario.scope.download_file_backend(sources_xz_file)
        with file_backend.get_stream(sources_xz_file) as compressed:
            got_sources = lzma.decompress(compressed.read())
            self.assertEqual(got_sources.decode(), expected_sources)
        self.assertEqual(
            task.index_info,
            {
                PurePath("main", "source", "Sources"): IndexInfo(
                    size=len(got_sources),
                    sha256=hashlib.sha256(got_sources).hexdigest(),
                ),
                PurePath("main", "source", "Sources.xz"): IndexInfo(
                    size=sources_xz_file.size,
                    sha256=sources_xz_file.sha256.hex(),
                    artifact=sources_xz_item.artifact,
                ),
            },
        )

    def test_generate_packages(self) -> None:
        """``generate_packages`` generates a ``Packages`` file."""
        scenario = scenarios.UIPlayground(set_current=True)
        self.playground.build_scenario(scenario, set_current=True)
        suite_collection = f"play_bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        temp_path = self.create_temporary_directory()

        task.generate_packages(
            temp_path=temp_path, component="main", architecture="amd64"
        )

        dpkg_amd64 = task.suite.manager.lookup("binary:dpkg_amd64")
        assert dpkg_amd64 is not None
        assert dpkg_amd64.artifact is not None
        dpkg_amd64_deb = dpkg_amd64.artifact.fileinartifact_set.get(
            path="dpkg_1.21.22_amd64.deb"
        ).file
        hello_amd64 = task.suite.manager.lookup("binary:hello_amd64")
        assert hello_amd64 is not None
        assert hello_amd64.artifact is not None
        hello_amd64_deb = hello_amd64.artifact.fileinartifact_set.get(
            path="hello_1.0-1_amd64.deb"
        ).file
        expected_packages = dedent(
            f"""\
            Package: dpkg
            Version: 1.21.22
            Maintainer: Example Maintainer <example@example.org>
            Architecture: amd64
            Description: Example description
            Section: devel
            Priority: optional
            Filename: pool/main/d/dpkg/dpkg_1.21.22_amd64.deb
            Size: {dpkg_amd64_deb.size}
            SHA256: {dpkg_amd64_deb.sha256.hex()}

            Package: hello
            Version: 1.0-1
            Maintainer: Example Maintainer <example@example.org>
            Architecture: amd64
            Description: Example description
            Section: devel
            Priority: optional
            Filename: pool/main/h/hello/hello_1.0-1_amd64.deb
            Size: {hello_amd64_deb.size}
            SHA256: {hello_amd64_deb.sha256.hex()}

            """
        )

        self.assertIsNone(
            task.suite.manager.lookup("index:main/binary-amd64/Packages")
        )
        packages_xz_item = task.suite.manager.lookup(
            "index:main/binary-amd64/Packages.xz"
        )
        assert packages_xz_item is not None
        self.assertEqual(packages_xz_item.created_at, task.data.generate_at)
        assert packages_xz_item.artifact is not None
        packages_xz_file = packages_xz_item.artifact.files.get()
        file_backend = scenario.scope.download_file_backend(packages_xz_file)
        with file_backend.get_stream(packages_xz_file) as compressed:
            got_packages = lzma.decompress(compressed.read())
            self.assertEqual(got_packages.decode(), expected_packages)
        self.assertEqual(
            task.index_info,
            {
                PurePath("main", "binary-amd64", "Packages"): IndexInfo(
                    size=len(got_packages),
                    sha256=hashlib.sha256(got_packages).hexdigest(),
                ),
                PurePath("main", "binary-amd64", "Packages.xz"): IndexInfo(
                    size=packages_xz_file.size,
                    sha256=packages_xz_file.sha256.hex(),
                    artifact=packages_xz_item.artifact,
                ),
            },
        )

    def test_generate_packages_duplicate_architecture_all(self) -> None:
        """The ``duplicate_architecture_all`` flag works."""
        scenario = scenarios.UIPlayground(set_current=True)
        self.playground.build_scenario(scenario, set_current=True)
        scenario.suite.data["duplicate_architecture_all"] = True
        scenario.suite.save()
        suite_collection = f"play_bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        temp_path = self.create_temporary_directory()

        task.generate_packages(
            temp_path=temp_path, component="main", architecture="amd64"
        )

        dpkg_amd64 = task.suite.manager.lookup("binary:dpkg_amd64")
        assert dpkg_amd64 is not None
        assert dpkg_amd64.artifact is not None
        dpkg_amd64_deb = dpkg_amd64.artifact.fileinartifact_set.get(
            path="dpkg_1.21.22_amd64.deb"
        ).file
        dpkg_doc_all = task.suite.manager.lookup("binary:dpkg-doc_all")
        assert dpkg_doc_all is not None
        assert dpkg_doc_all.artifact is not None
        dpkg_doc_all_deb = dpkg_doc_all.artifact.fileinartifact_set.get(
            path="dpkg-doc_1.21.22_all.deb"
        ).file
        hello_amd64 = task.suite.manager.lookup("binary:hello_amd64")
        assert hello_amd64 is not None
        assert hello_amd64.artifact is not None
        hello_amd64_deb = hello_amd64.artifact.fileinartifact_set.get(
            path="hello_1.0-1_amd64.deb"
        ).file
        expected_packages = dedent(
            f"""\
            Package: dpkg
            Version: 1.21.22
            Maintainer: Example Maintainer <example@example.org>
            Architecture: amd64
            Description: Example description
            Section: devel
            Priority: optional
            Filename: pool/main/d/dpkg/dpkg_1.21.22_amd64.deb
            Size: {dpkg_amd64_deb.size}
            SHA256: {dpkg_amd64_deb.sha256.hex()}

            Package: dpkg-doc
            Source: dpkg
            Version: 1.21.22
            Maintainer: Example Maintainer <example@example.org>
            Architecture: all
            Description: Example description
            Section: devel
            Priority: optional
            Filename: pool/main/d/dpkg/dpkg-doc_1.21.22_all.deb
            Size: {dpkg_doc_all_deb.size}
            SHA256: {dpkg_doc_all_deb.sha256.hex()}

            Package: hello
            Version: 1.0-1
            Maintainer: Example Maintainer <example@example.org>
            Architecture: amd64
            Description: Example description
            Section: devel
            Priority: optional
            Filename: pool/main/h/hello/hello_1.0-1_amd64.deb
            Size: {hello_amd64_deb.size}
            SHA256: {hello_amd64_deb.sha256.hex()}

            """
        )

        self.assertIsNone(
            task.suite.manager.lookup("index:main/binary-amd64/Packages")
        )
        packages_xz_item = task.suite.manager.lookup(
            "index:main/binary-amd64/Packages.xz"
        )
        assert packages_xz_item is not None
        assert packages_xz_item.artifact is not None
        packages_xz_file = packages_xz_item.artifact.files.get()
        file_backend = scenario.scope.download_file_backend(packages_xz_file)
        with file_backend.get_stream(packages_xz_file) as compressed:
            got_packages = lzma.decompress(compressed.read())
            self.assertEqual(got_packages.decode(), expected_packages)
        self.assertEqual(
            task.index_info,
            {
                PurePath("main", "binary-amd64", "Packages"): IndexInfo(
                    size=len(got_packages),
                    sha256=hashlib.sha256(got_packages).hexdigest(),
                ),
                PurePath("main", "binary-amd64", "Packages.xz"): IndexInfo(
                    size=packages_xz_file.size,
                    sha256=packages_xz_file.sha256.hex(),
                    artifact=packages_xz_item.artifact,
                ),
            },
        )

    def test_generate_release(self) -> None:
        """``generate_release`` generates a ``Release`` file."""
        scenario = scenarios.UIPlayground(set_current=True)
        self.playground.build_scenario(scenario, set_current=True)
        suite_collection = f"play_bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        temp_path = self.create_temporary_directory()
        sources_xz_artifact, _ = self.playground.create_artifact(
            ["Sources.xz"],
            category=ArtifactCategory.REPOSITORY_INDEX,
            create_files=True,
        )
        sources_xz = sources_xz_artifact.files.get()
        packages_xz_artifact, _ = self.playground.create_artifact(
            ["Sources.xz"],
            category=ArtifactCategory.REPOSITORY_INDEX,
            create_files=True,
        )
        packages_xz = packages_xz_artifact.files.get()
        task.index_info = {
            PurePath("main", "source", "Sources"): IndexInfo(
                size=1, sha256="sources-sha256"
            ),
            PurePath("main", "source", "Sources.xz"): IndexInfo(
                size=sources_xz.size,
                sha256=sources_xz.sha256.hex(),
                artifact=sources_xz_artifact,
            ),
            PurePath("main", "binary-amd64", "Packages"): IndexInfo(
                size=2, sha256="packages-sha256"
            ),
            PurePath("main", "binary-amd64", "Packages.xz"): IndexInfo(
                size=packages_xz.size,
                sha256=packages_xz.sha256.hex(),
                artifact=packages_xz_artifact,
            ),
        }

        task.generate_release(temp_path=temp_path)

        expected_date = task.data.generate_at.strftime(
            "%a, %d %b %Y %H:%M:%S UTC"
        )
        expected_release = dedent(
            f"""\
            Suite: stable
            Codename: bookworm
            Date: {expected_date}
            Architectures: {" ".join(task.architectures)}
            Components: {" ".join(task.components)}
            SHA256:
             packages-sha256 {2:16} main/binary-amd64/Packages
             {packages_xz.sha256.hex()} {packages_xz.size:16} main/binary-amd64/Packages.xz
             sources-sha256 {1:16} main/source/Sources
             {sources_xz.sha256.hex()} {sources_xz.size:16} main/source/Sources.xz
            """  # noqa: E501
        )

        release_item = task.suite.manager.lookup("index:Release")
        assert release_item is not None
        self.assertEqual(release_item.created_at, task.data.generate_at)
        assert release_item.artifact is not None
        release_file = release_item.artifact.files.get()
        file_backend = scenario.scope.download_file_backend(release_file)
        with file_backend.get_stream(release_file) as f:
            got_release = f.read().decode()
            self.assertEqual(got_release, expected_release)
        self.assertQuerySetEqual(
            Artifact.objects.filter(
                targeted_by__artifact=release_item.artifact
            ),
            [sources_xz_artifact, packages_xz_artifact],
            ordered=False,
        )

    def test_generate_release_field_ordering(self) -> None:
        """``generate_release`` orders ``Release`` fields appropriately."""
        scenario = scenarios.UIPlayground(set_current=True)
        self.playground.build_scenario(scenario, set_current=True)
        scenario.suite.data["release_fields"].update(
            {
                "Version": "12.0",
                "NotAutomatic": True,
                "ButAutomaticUpgrades": False,
                "Acquire-By-Hash": "yes",
                "Custom-Field": "foo",
            }
        )
        scenario.suite.data["duplicate_architecture_all"] = True
        scenario.suite.save()
        suite_collection = f"play_bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)
        temp_path = self.create_temporary_directory()
        sources_xz_artifact, _ = self.playground.create_artifact(
            ["Sources.xz"],
            category=ArtifactCategory.REPOSITORY_INDEX,
            create_files=True,
        )
        sources_xz = sources_xz_artifact.files.get()
        task.index_info = {
            PurePath("main", "source", "Sources"): IndexInfo(
                size=1, sha256="sources-sha256"
            ),
            PurePath("main", "source", "Sources.xz"): IndexInfo(
                size=sources_xz.size,
                sha256=sources_xz.sha256.hex(),
                artifact=sources_xz_artifact,
            ),
        }

        task.generate_release(temp_path=temp_path)

        expected_date = task.data.generate_at.strftime(
            "%a, %d %b %Y %H:%M:%S UTC"
        )
        expected_release = dedent(
            f"""\
            Suite: stable
            Version: 12.0
            Codename: bookworm
            Date: {expected_date}
            NotAutomatic: yes
            Acquire-By-Hash: yes
            No-Support-for-Architecture-all: Packages
            Architectures: {" ".join(task.architectures)}
            Components: {" ".join(task.components)}
            Custom-Field: foo
            SHA256:
             sources-sha256 {1:16} main/source/Sources
             {sources_xz.sha256.hex()} {sources_xz.size:16} main/source/Sources.xz
            """  # noqa: E501
        )

        release_item = task.suite.manager.lookup("index:Release")
        assert release_item is not None
        assert release_item.artifact is not None
        release_file = release_item.artifact.files.get()
        file_backend = scenario.scope.download_file_backend(release_file)
        with file_backend.get_stream(release_file) as f:
            got_release = f.read().decode()
            self.assertEqual(got_release, expected_release)
        self.assertQuerySetEqual(
            Artifact.objects.filter(
                targeted_by__artifact=release_item.artifact
            ),
            [sources_xz_artifact],
        )

    def test_supersede_older_items(self) -> None:
        """Remove superseded old items before generating new ones."""
        bookworm = self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        trixie = self.create_suite_collection("trixie")
        dts = [
            datetime(2025, month, 1, tzinfo=timezone.utc)
            for month in (1, 2, 3, 4)
        ]
        to_remove = [
            (bookworm, "index:main/source/Sources.xz"),
            (bookworm, "index:main/binary-amd64/Packages.xz"),
            (bookworm, "index:Release"),
        ]
        to_ignore = [
            (bookworm, "index:something-else"),
            (bookworm, "hello_1.0"),
            (trixie, "index:Release"),
        ]
        for collection, name in to_remove + to_ignore:
            for created_at in dts:
                # Even during test setup, we have to remove old items to
                # avoid constraint violations.
                CollectionItem.objects.active().filter(
                    parent_collection=collection,
                    child_type=CollectionItem.Types.ARTIFACT,
                    name=name,
                ).update(
                    removed_by_user=self.playground.get_default_user(),
                    removed_at=created_at,
                )
                artifact, _ = self.playground.create_artifact()
                item = CollectionItem.objects.create_from_artifact(
                    artifact,
                    parent_collection=collection,
                    name=name,
                    data={},
                    created_by_user=self.playground.get_default_user(),
                )
                item.created_at = created_at
                item.save()

        task1 = self.create_task(
            suite_collection=suite_collection,
            generate_at=datetime(2024, 12, 15, tzinfo=timezone.utc),
        )
        task1.supersede_older_items()

        expected_values: list[tuple[int, str, datetime, datetime | None]] = []
        for collection, name in to_remove + to_ignore:
            expected_values += [
                (collection.id, name, dts[0], dts[1]),
                (collection.id, name, dts[1], dts[2]),
                (collection.id, name, dts[2], dts[3]),
                (collection.id, name, dts[3], None),
            ]
        self.assertQuerySetEqual(
            CollectionItem.objects.filter(
                parent_collection__in={bookworm, trixie}
            ).values_list(
                "parent_collection", "name", "created_at", "removed_at"
            ),
            expected_values,
            ordered=False,
        )

        task2 = self.create_task(
            suite_collection=suite_collection,
            generate_at=datetime(2025, 3, 15, tzinfo=timezone.utc),
        )
        task2.supersede_older_items()

        expected_values = []
        for collection, name in to_remove:
            expected_values += [
                (collection.id, name, dts[0], dts[1]),
                (collection.id, name, dts[1], dts[2]),
                (collection.id, name, dts[2], task2.data.generate_at),
                (collection.id, name, dts[3], None),
            ]
        for collection, name in to_ignore:
            expected_values += [
                (collection.id, name, dts[0], dts[1]),
                (collection.id, name, dts[1], dts[2]),
                (collection.id, name, dts[2], dts[3]),
                (collection.id, name, dts[3], None),
            ]
        self.assertQuerySetEqual(
            CollectionItem.objects.filter(
                parent_collection__in={bookworm, trixie}
            ).values_list(
                "parent_collection", "name", "created_at", "removed_at"
            ),
            expected_values,
            ordered=False,
        )

        task3 = self.create_task(
            suite_collection=suite_collection,
            generate_at=datetime(2025, 4, 15, tzinfo=timezone.utc),
        )
        task3.supersede_older_items()

        expected_values = []
        for collection, name in to_remove:
            expected_values += [
                (collection.id, name, dts[0], dts[1]),
                (collection.id, name, dts[1], dts[2]),
                (collection.id, name, dts[2], task2.data.generate_at),
                (collection.id, name, dts[3], task3.data.generate_at),
            ]
        for collection, name in to_ignore:
            expected_values += [
                (collection.id, name, dts[0], dts[1]),
                (collection.id, name, dts[1], dts[2]),
                (collection.id, name, dts[2], dts[3]),
                (collection.id, name, dts[3], None),
            ]
        self.assertQuerySetEqual(
            CollectionItem.objects.filter(
                parent_collection__in={bookworm, trixie}
            ).values_list(
                "parent_collection", "name", "created_at", "removed_at"
            ),
            expected_values,
            ordered=False,
        )

    def test_find_newer_item(self) -> None:
        """Find the next generation of index files."""
        bookworm = self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        trixie = self.create_suite_collection("trixie")
        dts = [
            datetime(2025, month, 1, tzinfo=timezone.utc)
            for month in (1, 2, 3, 4)
        ]
        to_remove = [
            (bookworm, "index:main/source/Sources.xz"),
            (bookworm, "index:main/binary-amd64/Packages.xz"),
            (bookworm, "index:Release"),
        ]
        to_ignore = [
            (bookworm, "index:something-else"),
            (bookworm, "hello_1.0"),
            (trixie, "index:Release"),
        ]
        for collection, name in to_remove + to_ignore:
            for created_at in dts:
                # Even during test setup, we have to remove old items to
                # avoid constraint violations.
                CollectionItem.objects.active().filter(
                    parent_collection=collection,
                    child_type=CollectionItem.Types.ARTIFACT,
                    name=name,
                ).update(
                    removed_by_user=self.playground.get_default_user(),
                    removed_at=created_at,
                )
                artifact, _ = self.playground.create_artifact()
                item = CollectionItem.objects.create_from_artifact(
                    artifact,
                    parent_collection=collection,
                    name=name,
                    data={},
                    created_by_user=self.playground.get_default_user(),
                )
                item.created_at = created_at
                item.save()

        for generate_at, expected_item in (
            (
                datetime(2025, 2, 15, tzinfo=timezone.utc),
                bookworm.child_items.get(
                    name="index:main/source/Sources.xz", created_at=dts[2]
                ),
            ),
            (
                datetime(2025, 3, 1, tzinfo=timezone.utc),
                bookworm.child_items.get(
                    name="index:main/source/Sources.xz", created_at=dts[3]
                ),
            ),
            (datetime(2025, 4, 15, tzinfo=timezone.utc), None),
        ):
            with self.subTest(generate_at=generate_at):
                task = self.create_task(
                    suite_collection=suite_collection, generate_at=generate_at
                )
                self.assertEqual(task.find_newer_item(), expected_item)

    def test_execute(self) -> None:
        """Test end-to-end execution."""
        scenario = scenarios.UIPlayground(set_current=True)
        self.playground.build_scenario(scenario, set_current=True)
        suite_collection = f"play_bookworm@{CollectionCategory.SUITE}"
        dts = [
            datetime(2025, month, 1, tzinfo=timezone.utc) for month in (1, 2, 3)
        ]
        older_release, _ = self.playground.create_artifact(
            category=ArtifactCategory.REPOSITORY_INDEX
        )
        older_release_item = scenario.suite.manager.add_artifact(
            older_release,
            user=self.playground.get_default_user(),
            variables={"path": "Release"},
        )
        older_release_item.created_at = dts[0]
        older_release_item.removed_by_user = self.playground.get_default_user()
        older_release_item.removed_at = dts[2]
        older_release_item.save()
        newer_release, _ = self.playground.create_artifact(
            category=ArtifactCategory.REPOSITORY_INDEX
        )
        newer_release_item = scenario.suite.manager.add_artifact(
            newer_release,
            user=self.playground.get_default_user(),
            variables={"path": "Release"},
        )
        newer_release_item.created_at = dts[2]
        newer_release_item.save()

        task = self.create_task(
            suite_collection=suite_collection, generate_at=dts[1]
        )

        # Pretend that a lock for another collection is held, to make sure
        # that such a lock doesn't interfere.
        non_conflicting_alias = "non-conflicting-lock"
        non_conflicting_connection = connection.copy(non_conflicting_alias)
        connections[non_conflicting_alias] = non_conflicting_connection
        try:
            with advisory_lock(
                (
                    LockType.GENERATE_SUITE_INDEXES,
                    (scenario.suite.id + 1) & (2**31 - 1),
                ),
                wait=False,
                using=non_conflicting_alias,
            ) as acquired:
                assert acquired
                self.assertTrue(task.execute())
        finally:
            del connections[non_conflicting_alias]
            non_conflicting_connection.close()

        self.assertQuerySetEqual(
            scenario.suite.child_items.filter(
                name__startswith="index:"
            ).values_list("name", "created_at", "removed_at"),
            [
                (f"index:{component}/source/Sources.xz", dts[1], dts[2])
                for component in scenario.suite.data["components"]
            ]
            + [
                (
                    f"index:{component}/binary-{architecture}/Packages.xz",
                    dts[1],
                    dts[2],
                )
                for component in scenario.suite.data["components"]
                for architecture in scenario.suite.data["architectures"]
            ]
            + [
                ("index:Release", dts[0], dts[1]),
                ("index:Release", dts[1], dts[2]),
                ("index:Release", dts[2], None),
            ],
            ordered=False,
        )

    def test_execute_lock_error(self) -> None:
        """The task fails if its lock is already held."""
        bookworm = self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        task = self.create_task(suite_collection=suite_collection)

        conflicting_alias = "conflicting-lock"
        conflicting_connection = connection.copy(conflicting_alias)
        connections[conflicting_alias] = conflicting_connection
        try:
            with (
                advisory_lock(
                    (
                        LockType.GENERATE_SUITE_INDEXES,
                        bookworm.id & (2**31 - 1),
                    ),
                    wait=False,
                    using=conflicting_alias,
                ) as acquired,
                self.assertRaisesRegex(
                    LockError,
                    f"Another GenerateSuiteIndexes task for "
                    f"{suite_collection} is already running",
                ),
            ):
                assert acquired
                task.execute()
        finally:
            del connections[conflicting_alias]
            conflicting_connection.close()

    def test_get_label(self) -> None:
        """Test get_label."""
        self.create_suite_collection("bookworm")
        suite_collection = f"bookworm@{CollectionCategory.SUITE}"
        generate_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        task = self.create_task(
            suite_collection=suite_collection, generate_at=generate_at
        )
        self.assertEqual(
            task.get_label(),
            f"generate indexes for {suite_collection} "
            f"at 2025-01-01 00:00:00+00:00",
        )
