# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Server-side task to mirror an external APT suite into a debusine."""

import functools
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from textwrap import dedent
from typing import Generic, TypeVar

from debian.deb822 import Deb822, Packages, Sources
from django.db import transaction
from django_pglocks import advisory_lock

from debusine.artifacts.local_artifact import BinaryPackage, SourcePackage
from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.locks import LockError, LockType
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Collection,
    CollectionItem,
)
from debusine.server.collections.debian_suite import DebianSuiteManager
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import APTMirrorData
from debusine.tasks import DefaultDynamicData, TaskConfigError
from debusine.tasks.models import BaseDynamicTaskData

T = TypeVar("T", bound=Deb822)


@dataclass
class PlanAdd(Generic[T]):
    """A plan for adding a single package in a collection."""

    name: str
    package: T
    component: str


@dataclass
class PlanReplace(PlanAdd[T]):
    """A plan for replacing a single package in a collection."""

    artifact: Artifact


@dataclass
class Plan(Generic[T]):
    """A plan for updating packages in a collection."""

    add: list[PlanAdd[T]] = field(default_factory=list)
    replace: list[PlanReplace[T]] = field(default_factory=list)
    remove: list[Artifact] = field(default_factory=list)


class InconsistentMirrorError(Exception):
    """The remote mirror is inconsistent."""


logger = logging.getLogger(__name__)


def _run_and_log_errors(
    args: Sequence[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, logging stderr on failure."""
    try:
        return subprocess.run(
            args, cwd=cwd, env=env, text=True, check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        logger.error("Error output from %s:\n%s", shlex.join(e.cmd), e.stderr)
        raise


class APTMirror(
    BaseServerTask[APTMirrorData, BaseDynamicTaskData],
    DefaultDynamicData[APTMirrorData],
):
    """Task that mirrors an external APT suite into a debusine collection."""

    TASK_VERSION = 1
    TASK_MANAGES_TRANSACTIONS = True

    @functools.cached_property
    def collection(self) -> Collection:
        """The `debian:suite` collection this task is updating."""
        try:
            return Collection.objects.get(
                name=self.data.collection, category=CollectionCategory.SUITE
            )
        except Collection.DoesNotExist:
            raise TaskConfigError(
                f"Collection '{self.data.collection}' with category "
                f"'{CollectionCategory.SUITE}' not found"
            )

    @functools.cached_property
    def collection_manager(self) -> DebianSuiteManager:
        """The manager for the `debian:suite` collection being updated."""
        return DebianSuiteManager(collection=self.collection)

    def make_apt_environment(self, temp_path: Path) -> dict[str, str]:
        """Make a suitable process environment for running apt commands."""
        env = os.environ.copy()
        env["APT_CONFIG"] = str(temp_path / "etc/apt/apt.conf")
        return env

    def fetch_indexes(self, temp_path: Path) -> None:
        """Fetch indexes for this suite."""
        (temp_path / "etc/apt/apt.conf.d").mkdir(parents=True)
        (temp_path / "etc/apt/preferences.d").mkdir(parents=True)
        (temp_path / "etc/apt/sources.list.d").mkdir(parents=True)
        (temp_path / "var/lib/apt/lists/partial").mkdir(parents=True)

        (temp_path / "etc/apt/apt.conf").write_text(
            dedent(
                f"""\
                APT::Architecture "{self.data.architectures[0]}";
                APT::Architectures "{",".join(self.data.architectures)}";
                Dir "{temp_path}";
                """
            )
        )

        source = {
            "Types": "deb deb-src",
            "URIs": self.data.url,
            "Suites": self.data.suite,
        }
        if self.data.components is not None:
            source["Components"] = " ".join(self.data.components)
        if self.data.signing_key is not None:
            source["Signed-By"] = "\n" + "\n".join(
                f" {line}" if line else " ."
                for line in self.data.signing_key.splitlines()
            )
        (temp_path / "etc/apt/sources.list.d/mirror.sources").write_text(
            str(Deb822(source))
        )

        _run_and_log_errors(
            ["apt-get", "update"], env=self.make_apt_environment(temp_path)
        )

    def plan_sources(self, temp_path: Path) -> Plan[Sources]:
        """Plan the update of all source packages in the collection."""
        # Source packages from the remote collection
        indexes: dict[str, tuple[Sources, str]] = {}
        for paragraph in Deb822.iter_paragraphs(
            _run_and_log_errors(
                ["apt-get", "indextargets", "Identifier: Sources"],
                env=self.make_apt_environment(temp_path),
            ).stdout
        ):
            with open(paragraph["Filename"]) as f:
                for source in Sources.iter_paragraphs(f):
                    name = "{Package}_{Version}".format(**source)
                    if name in indexes:
                        raise InconsistentMirrorError(
                            f"{name} found in multiple components: "
                            f"{indexes[name][1]} and {paragraph['Component']}"
                        )
                    indexes.setdefault(name, (source, paragraph["Component"]))

        # Source packages from the local collection
        items: dict[str, tuple[Artifact, dict[str, str]]] = {}
        for item in (
            CollectionItem.active_objects.filter(
                parent_collection=self.collection,
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.SOURCE_PACKAGE,
            )
            .only("name", "data", "artifact")
            .prefetch_related("artifact__fileinartifact_set__file")
        ):
            artifact = item.artifact
            assert artifact is not None
            items[item.name] = (
                artifact,
                {
                    file_in_artifact.path: file_in_artifact.file.sha256.hex()
                    for file_in_artifact in artifact.fileinartifact_set.all()
                },
            )

        plan = Plan[Sources]()

        for name, (source, component) in sorted(indexes.items()):
            index_checksums = {
                checksum["name"]: checksum["sha256"]
                for checksum in source["Checksums-Sha256"]
            }
            if name in items:
                artifact, item_checksums = items[name]
                if index_checksums != item_checksums:
                    plan.replace.append(
                        PlanReplace[Sources](
                            name=name,
                            package=source,
                            component=component,
                            artifact=artifact,
                        )
                    )
            else:
                plan.add.append(
                    PlanAdd[Sources](
                        name=name, package=source, component=component
                    )
                )

        for name in sorted(items.keys() - indexes.keys()):
            artifact, _ = items[name]
            plan.remove.append(artifact)

        return plan

    def add_source(
        self,
        temp_path: Path,
        *,
        name: str,
        source: Sources,
        component: str,
    ) -> None:
        """Download a source package and add it to the collection."""
        assert self.work_request is not None
        assert self.workspace is not None
        package = source["Package"]
        version = source["Version"]
        (temp_download := temp_path / "download" / name).mkdir(parents=True)
        try:
            _run_and_log_errors(
                [
                    "apt-get",
                    "--download-only",
                    "--only-source",
                    "source",
                    f"{package}={version}",
                ],
                cwd=temp_download,
                env=self.make_apt_environment(temp_path),
            )
            source_package = SourcePackage.create(
                name=package,
                version=version,
                files=list(temp_download.iterdir()),
            )
            source_artifact = Artifact.objects.create_from_local_artifact(
                source_package,
                self.workspace,
                created_by_work_request=self.work_request,
            )
            self.collection_manager.add_source_package(
                source_artifact,
                user=self.work_request.created_by,
                component=component,
                section=source["Section"],
            )
        finally:
            shutil.rmtree(temp_download)

    def update_sources(self, temp_path: Path, plan: Plan[Sources]) -> None:
        """
        Update all source packages in the collection.

        This may take a long time, so it commits transactions as it goes
        rather than taking a single long transaction.  If processing an item
        fails, then the results of earlier processing will remain visible.
        """
        for add in plan.add:
            with transaction.atomic():
                self.add_source(
                    temp_path,
                    name=add.name,
                    source=add.package,
                    component=add.component,
                )

        for replace in plan.replace:
            with transaction.atomic():
                self.collection_manager.remove_artifact(replace.artifact)
                self.add_source(
                    temp_path,
                    name=replace.name,
                    source=replace.package,
                    component=replace.component,
                )

        with transaction.atomic():
            for remove in plan.remove:
                self.collection_manager.remove_artifact(remove)

    def add_binary(
        self,
        temp_path: Path,
        *,
        name: str,
        binary: Packages,
        component: str,
    ) -> None:
        """Download a binary package and add it to the collection."""
        assert self.work_request is not None
        assert self.workspace is not None
        package = binary["Package"]
        version = binary["Version"]
        architecture = binary["Architecture"]
        (temp_download := temp_path / "download" / name).mkdir(parents=True)
        try:
            _run_and_log_errors(
                ["apt-get", "download", f"{package}:{architecture}={version}"],
                cwd=temp_download,
                env=self.make_apt_environment(temp_path),
            )
            # The download should have resulted in exactly one file.
            [file] = temp_download.iterdir()
            # Rename this to the basename of the file on the mirror, if
            # necessary.  This may differ from what "apt-get download" gives
            # us in the case where the binary has an epoch.
            expected_file = temp_download / PurePath(binary["Filename"]).name
            if file != expected_file:
                file.rename(expected_file)
            binary_package = BinaryPackage.create(file=expected_file)
            binary_artifact = Artifact.objects.create_from_local_artifact(
                binary_package,
                self.workspace,
                created_by_work_request=self.work_request,
            )
            srcpkg_name = binary_package.data.srcpkg_name
            srcpkg_version = binary_package.data.srcpkg_version
            source_item = self.collection_manager.lookup(
                f"source-version:{srcpkg_name}_{srcpkg_version}"
            )
            # Add a built-using relationship to the corresponding source if
            # we can be confident of it.  Suites with
            # may_reuse_versions=True may have source packages replaced, and
            # in that case we can't be sure that the source package version
            # in the binary package's metadata is enough to match it.
            if source_item is not None and not self.collection.data.get(
                "may_reuse_versions", False
            ):
                assert source_item.artifact is not None
                ArtifactRelation.objects.create(
                    artifact=binary_artifact,
                    target=source_item.artifact,
                    type=ArtifactRelation.Relations.BUILT_USING,
                )
            self.collection_manager.add_binary_package(
                binary_artifact,
                user=self.work_request.created_by,
                component=component,
                section=binary["Section"],
                priority=binary["Priority"],
            )
        finally:
            shutil.rmtree(temp_download)

    def plan_binaries(self, temp_path: Path) -> Plan[Packages]:
        """Plan the update of all binary packages in the collection."""
        # Binary packages from the remote collection
        indexes: dict[str, tuple[Packages, str]] = {}
        for paragraph in Deb822.iter_paragraphs(
            _run_and_log_errors(
                ["apt-get", "indextargets", "Identifier: Packages"],
                env=self.make_apt_environment(temp_path),
            ).stdout
        ):
            with open(paragraph["Filename"]) as f:
                for binary in Packages.iter_paragraphs(f):
                    name = "{Package}_{Version}_{Architecture}".format(**binary)
                    if name in indexes:
                        if indexes[name][1] != paragraph["Component"]:
                            raise InconsistentMirrorError(
                                f"{name} found in multiple components: "
                                f"{indexes[name][1]} and "
                                f"{paragraph['Component']}"
                            )
                        elif indexes[name][0] != binary:
                            raise InconsistentMirrorError(
                                f"{name} mismatch.  Conflicting Packages "
                                f"entries:\n\n"
                                + indexes[name][0].dump()
                                + "\n\n"
                                + binary.dump()
                            )
                    indexes[name] = (binary, paragraph["Component"])

        # Binary packages from the local collection
        items: dict[str, tuple[Artifact, dict[str, str]]] = {}
        for item in (
            CollectionItem.active_objects.filter(
                parent_collection=self.collection,
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.BINARY_PACKAGE,
            )
            .only("name", "data", "artifact")
            .prefetch_related("artifact__fileinartifact_set__file")
        ):
            artifact = item.artifact
            assert artifact is not None
            items[item.name] = (
                artifact,
                {
                    file_in_artifact.path: file_in_artifact.file.sha256.hex()
                    for file_in_artifact in artifact.fileinartifact_set.all()
                },
            )

        plan = Plan[Packages]()

        for name, (binary, component) in sorted(indexes.items()):
            index_checksums = {
                PurePath(binary["Filename"]).name: binary["SHA256"]
            }
            if name in items:
                artifact, item_checksums = items[name]
                if index_checksums != item_checksums:
                    plan.replace.append(
                        PlanReplace[Packages](
                            name=name,
                            package=binary,
                            component=component,
                            artifact=artifact,
                        )
                    )
            else:
                plan.add.append(
                    PlanAdd[Packages](
                        name=name, package=binary, component=component
                    )
                )

        for name in sorted(items.keys() - indexes.keys()):
            artifact, _ = items[name]
            plan.remove.append(artifact)

        return plan

    def update_binaries(self, temp_path: Path, plan: Plan[Packages]) -> None:
        """
        Update all binary packages in the collection.

        This may take a long time, so it commits transactions as it goes
        rather than taking a single long transaction.  If processing an item
        fails, then the results of earlier processing will remain visible.
        """
        for add in plan.add:
            with transaction.atomic():
                self.add_binary(
                    temp_path,
                    name=add.name,
                    binary=add.package,
                    component=add.component,
                )

        for replace in plan.replace:
            with transaction.atomic():
                self.collection_manager.remove_artifact(replace.artifact)
                self.add_binary(
                    temp_path,
                    name=replace.name,
                    binary=replace.package,
                    component=replace.component,
                )

        with transaction.atomic():
            for remove in plan.remove:
                self.collection_manager.remove_artifact(remove)

    def _execute(self) -> bool:
        """Execute the task."""
        with advisory_lock(
            (
                LockType.APT_MIRROR,
                # Only use the bottom 31 bits, in order that this fits into
                # PostgreSQL's int type.  In the unlikely event that we have
                # enough mirrored collections for there to be a collision,
                # then it just means that the colliding collections can't be
                # mirrored simultaneously.
                self.collection.id & (2**31 - 1),
            ),
            wait=False,
        ) as acquired:
            if not acquired:
                raise LockError(
                    f"Another APTMirror task for {self.data.collection} is "
                    f"already running"
                )

            # This task may take a long time, so it commits transactions as
            # it goes rather than taking a single long transaction.  If
            # processing an item fails, then the results of earlier
            # processing will remain visible.
            with tempfile.TemporaryDirectory(
                prefix="debusine-aptmirror-"
            ) as temp_dir:
                temp_path = Path(temp_dir)
                self.fetch_indexes(temp_path)
                self.update_sources(temp_path, self.plan_sources(temp_path))
                self.update_binaries(temp_path, self.plan_binaries(temp_path))
            return True

    def get_label(self) -> str:
        """Return the task label."""
        return f"mirror {self.data.collection} from {self.data.url}"
