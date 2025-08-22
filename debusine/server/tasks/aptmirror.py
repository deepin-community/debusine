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
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Generic, TypeVar

from debian.deb822 import Deb822, Packages, Sources
from django.db import transaction
from django_pglocks import advisory_lock

from debusine.artifacts.local_artifact import (
    BinaryPackage,
    RepositoryIndex,
    SourcePackage,
)
from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.locks import LockError, LockType
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Collection,
    CollectionItem,
)
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import APTMirrorData
from debusine.tasks import DefaultDynamicData, TaskConfigError
from debusine.tasks.models import BaseDynamicTaskData
from debusine.utils import calculate_hash

T = TypeVar("T", bound=Deb822)


@dataclass
class PlanAdd(Generic[T]):
    """A plan for adding a single item to a collection."""

    name: str
    contents: T
    component: str


@dataclass
class PlanReplace(PlanAdd[T]):
    """A plan for replacing a single item in a collection."""

    item: CollectionItem


@dataclass
class Plan(Generic[T]):
    """A plan for updating items in a collection."""

    add: list[PlanAdd[T]] = field(default_factory=list)
    replace: list[PlanReplace[T]] = field(default_factory=list)
    remove: list[CollectionItem] = field(default_factory=list)


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


class _StringWithFilenoHack(str):
    """
    A string with a useless ``fileno`` method.

    This works around https://bugs.debian.org/1086512.  Once Debusine can
    require python-debian >= 1.0.0 (which is in trixie), we can just pass
    filenames directly to ``iter_paragraphs`` rather than having to wrap
    them in this class.
    """

    def fileno(self) -> int:
        return -1


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

    def make_apt_environment(self, temp_path: Path) -> dict[str, str]:
        """Make a suitable process environment for running apt commands."""
        env = os.environ.copy()
        env["APT_CONFIG"] = str(temp_path / "etc/apt/apt.conf")
        return env

    def write_apt_config(
        self, path: Path, options: list[tuple[str, str | None]]
    ) -> None:
        """Write APT configuration options out to a file."""
        apt_config = [
            f'#clear {key};' if value is None else f'{key} "{value}";'
            for key, value in options
        ]
        path.write_text("\n".join(apt_config) + "\n")

    def fetch_indexes(self, temp_path: Path) -> None:
        """Fetch indexes for this suite."""
        (temp_path / "etc/apt/apt.conf.d").mkdir(parents=True)
        (temp_path / "etc/apt/preferences.d").mkdir(parents=True)
        (temp_path / "etc/apt/sources.list.d").mkdir(parents=True)
        (temp_path / "var/lib/apt/lists/partial").mkdir(parents=True)

        apt_config: list[tuple[str, str | None]] = [
            ("APT::Architecture", self.data.architectures[0]),
            ("APT::Architectures", ",".join(self.data.architectures)),
            ("Dir", str(temp_path)),
            ("Acquire::IndexTargets", None),
        ]

        # Configure APT to avoid recompressing/uncompressing index files.
        # This involves quite a lot of work
        # (https://bugs.debian.org/1108032).
        compress_exts = (".xz", ".gz", "")
        for index_type, identifier, meta_prefix in (
            ("deb", "Packages", "$(COMPONENT)/binary-$(ARCHITECTURE)/"),
            ("deb-src", "Sources", "$(COMPONENT)/source/"),
        ):
            for i, compress_ext in enumerate(compress_exts):
                filename = f"{identifier}{compress_ext}"
                prefix = f"Acquire::IndexTargets::{index_type}::{filename}"
                apt_config += [
                    (f"{prefix}::MetaKey", f"{meta_prefix}{filename}"),
                    (f"{prefix}::flatMetaKey", filename),
                    (f"{prefix}::ShortDescription", filename),
                    (
                        f"{prefix}::Description",
                        f"$(RELEASE)/{meta_prefix}{filename}",
                    ),
                    (f"{prefix}::flatDescription", f"$(RELEASE) {filename}"),
                    (f"{prefix}::Identifier", identifier),
                    (f"{prefix}::Optional", "0"),
                ]
                if i > 0:
                    apt_config.append(
                        (
                            f"{prefix}::Fallback-Of",
                            f"{identifier}{compress_exts[i - 1]}",
                        )
                    )

        apt_config_path = temp_path / "etc/apt/apt.conf"
        self.write_apt_config(apt_config_path, apt_config)

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

    def get_indextargets(
        self, temp_path: Path, identifier: str | None = None
    ) -> Generator[Deb822, None, None]:
        """Yield relevant index targets according to APT."""
        args = ["apt-get", "indextargets"]
        if identifier is not None:
            args.append(f"Identifier: {identifier}")
        for paragraph in Deb822.iter_paragraphs(
            _run_and_log_errors(
                args, env=self.make_apt_environment(temp_path)
            ).stdout
        ):
            meta_key = PurePath(paragraph["MetaKey"])
            filename = Path(paragraph["Filename"])
            if filename.name != meta_key.name and not filename.name.endswith(
                f"_{meta_key.name}"
            ):
                # Ignore superfluous targets generated by Fallback-Of.
                continue
            yield paragraph

    def plan_sources(self, temp_path: Path) -> Plan[Sources]:
        """Plan the update of all source packages in the collection."""
        # Source packages from the remote collection
        indexes: dict[str, tuple[Sources, str]] = {}
        for paragraph in self.get_indextargets(temp_path, "Sources"):
            component = paragraph.get("Component", "main")
            for source in Sources.iter_paragraphs(
                _StringWithFilenoHack(paragraph["Filename"]), use_apt_pkg=True
            ):
                name = "{Package}_{Version}".format(**source)
                if name in indexes:
                    raise InconsistentMirrorError(
                        f"{name} found in multiple components: "
                        f"{indexes[name][1]} and {component}"
                    )
                indexes.setdefault(name, (source, component))

        # Source packages from the local collection
        items: dict[str, tuple[CollectionItem, dict[str, str]]] = {}
        for item in (
            CollectionItem.objects.active()
            .filter(
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
                item,
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
                item, item_checksums = items[name]
                if index_checksums != item_checksums:
                    plan.replace.append(
                        PlanReplace[Sources](
                            name=name,
                            contents=source,
                            component=component,
                            item=item,
                        )
                    )
            else:
                plan.add.append(
                    PlanAdd[Sources](
                        name=name, contents=source, component=component
                    )
                )

        for name in sorted(items.keys() - indexes.keys()):
            item, _ = items[name]
            plan.remove.append(item)

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
            self.collection.manager.add_artifact(
                source_artifact,
                user=self.work_request.created_by,
                variables={
                    "component": component,
                    "section": source["Section"],
                },
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
                    source=add.contents,
                    component=add.component,
                )

        for replace in plan.replace:
            with transaction.atomic():
                self.collection.manager.remove_item(replace.item)
                self.add_source(
                    temp_path,
                    name=replace.name,
                    source=replace.contents,
                    component=replace.component,
                )

        with transaction.atomic():
            for remove in plan.remove:
                self.collection.manager.remove_item(remove)

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
            source_item = self.collection.manager.lookup(
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
            self.collection.manager.add_artifact(
                binary_artifact,
                user=self.work_request.created_by,
                variables={
                    "component": component,
                    "section": binary["Section"],
                    "priority": binary["Priority"],
                },
            )
        finally:
            shutil.rmtree(temp_download)

    def plan_binaries(self, temp_path: Path) -> Plan[Packages]:
        """Plan the update of all binary packages in the collection."""
        # Binary packages from the remote collection
        indexes: dict[str, tuple[Packages, str]] = {}
        for paragraph in self.get_indextargets(temp_path, "Packages"):
            component = paragraph.get("Component", "main")
            for binary in Packages.iter_paragraphs(
                _StringWithFilenoHack(paragraph["Filename"]), use_apt_pkg=True
            ):
                name = "{Package}_{Version}_{Architecture}".format(**binary)
                if name in indexes:
                    if indexes[name][1] != component:
                        raise InconsistentMirrorError(
                            f"{name} found in multiple components: "
                            f"{indexes[name][1]} and {component}"
                        )
                    elif indexes[name][0] != binary:
                        raise InconsistentMirrorError(
                            f"{name} mismatch.  Conflicting Packages "
                            f"entries:\n\n"
                            + indexes[name][0].dump()
                            + "\n\n"
                            + binary.dump()
                        )
                indexes[name] = (binary, component)

        # Binary packages from the local collection
        items: dict[str, tuple[CollectionItem, dict[str, str]]] = {}
        for item in (
            CollectionItem.objects.active()
            .filter(
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
                item,
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
                item, item_checksums = items[name]
                if index_checksums != item_checksums:
                    plan.replace.append(
                        PlanReplace[Packages](
                            name=name,
                            contents=binary,
                            component=component,
                            item=item,
                        )
                    )
            else:
                plan.add.append(
                    PlanAdd[Packages](
                        name=name, contents=binary, component=component
                    )
                )

        for name in sorted(items.keys() - indexes.keys()):
            item, _ = items[name]
            plan.remove.append(item)

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
                    binary=add.contents,
                    component=add.component,
                )

        for replace in plan.replace:
            with transaction.atomic():
                self.collection.manager.remove_item(replace.item)
                self.add_binary(
                    temp_path,
                    name=replace.name,
                    binary=replace.contents,
                    component=replace.component,
                )

        with transaction.atomic():
            for remove in plan.remove:
                self.collection.manager.remove_item(remove)

    @contextmanager
    def apt_config(self, temp_path: Path) -> Generator[None, None, None]:
        """Temporarily use a different APT configuration."""
        import apt_pkg

        try:
            os.environ["APT_CONFIG"] = self.make_apt_environment(temp_path)[
                "APT_CONFIG"
            ]
            for key in apt_pkg.config.keys():
                apt_pkg.config.clear(key)
            apt_pkg.init_config()
            yield
        finally:
            del os.environ["APT_CONFIG"]

    def plan_indexes(self, temp_path: Path) -> Plan[Deb822]:
        """Plan the update of all indexes in the collection."""
        # We have to import this late, as otherwise mypy_django_plugin tries
        # to import it during early initialization and fails (since we can't
        # install it from PyPI and so it isn't present in mypy's virtual
        # environment).
        import apt_pkg

        # We don't mirror indexes for suites in the flat repository format,
        # because we don't currently have a reasonable way to serve them:
        # debusine.web.archives assumes a pooled layout, and we can't freely
        # convert between them because the Release file must agree with the
        # layout we serve.
        if self.data.suite.endswith("/"):
            return Plan[Deb822]()

        indexes: dict[str, tuple[Deb822, str]] = {}
        for paragraph in self.get_indextargets(temp_path):
            component = paragraph.get("Component", "main")
            if paragraph["Identifier"] in {"Packages", "Sources"}:
                name = f"index:{paragraph['MetaKey']}"
                indexes.setdefault(name, (paragraph, component))

        with self.apt_config(temp_path):
            lists_path = Path(apt_pkg.config.find_dir("Dir::State::lists"))
            source_list = apt_pkg.SourceList()
            source_list.read_main_list()
            assert len(source_list.list) == 1
            base_uri = source_list.list[0].uri

        for release_name in ("Release", "Release.gpg", "InRelease"):
            release_path = lists_path / (
                apt_pkg.uri_to_filename(base_uri)
                + f"dists_{self.data.suite}_{release_name}"
            )
            if release_path.exists():
                indexes[f"index:{release_name}"] = (
                    Deb822(
                        {"MetaKey": release_name, "Filename": str(release_path)}
                    ),
                    "",
                )

        # Repository index files from the local collection
        items: dict[str, tuple[CollectionItem, dict[str, str]]] = {}
        for item in (
            CollectionItem.objects.active()
            .filter(
                parent_collection=self.collection,
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.REPOSITORY_INDEX,
            )
            .only("name", "data", "artifact")
            .prefetch_related("artifact__fileinartifact_set__file")
        ):
            artifact = item.artifact
            assert artifact is not None
            items[item.name] = (
                item,
                {
                    file_in_artifact.path: file_in_artifact.file.sha256.hex()
                    for file_in_artifact in artifact.fileinartifact_set.all()
                },
            )

        plan = Plan[Deb822]()

        for name, (paragraph, component) in sorted(indexes.items()):
            rel_path = paragraph["MetaKey"]
            index_path = Path(paragraph["Filename"])
            index_checksums = {
                PurePath(rel_path).name: (
                    calculate_hash(index_path, "sha256").hex()
                )
            }
            if name in items:
                item, item_checksums = items[name]
                if index_checksums != item_checksums:
                    plan.replace.append(
                        PlanReplace[Deb822](
                            name=rel_path,
                            contents=paragraph,
                            component=component,
                            item=item,
                        )
                    )
            else:
                plan.add.append(
                    PlanAdd[Deb822](
                        name=rel_path, contents=paragraph, component=component
                    )
                )

        for name in sorted(items.keys() - indexes.keys()):
            item, _ = items[name]
            plan.remove.append(item)

        return plan

    def add_index(self, *, name: str, paragraph: Deb822) -> None:
        """Add an index to the collection."""
        assert self.work_request is not None
        assert self.workspace is not None
        index = RepositoryIndex.create(
            file=Path(paragraph["Filename"]), path=name
        )
        index_artifact = Artifact.objects.create_from_local_artifact(
            index, self.workspace, created_by_work_request=self.work_request
        )
        self.collection.manager.add_artifact(
            index_artifact,
            user=self.work_request.created_by,
            variables={"path": name},
        )

    def update_indexes(self, plan: Plan[Deb822]) -> None:
        """
        Update all repository indexes in the collection.

        Indexes are normally relatively small compared to packages and have
        already been downloaded by apt, so it does this in a single
        transaction.
        """
        with transaction.atomic():
            for add in plan.add:
                self.add_index(name=add.name, paragraph=add.contents)

            for replace in plan.replace:
                self.collection.manager.remove_item(replace.item)
                self.add_index(name=replace.name, paragraph=replace.contents)

            for remove in plan.remove:
                self.collection.manager.remove_item(remove)

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
                self.update_indexes(self.plan_indexes(temp_path))
            return True

    def get_label(self) -> str:
        """Return the task label."""
        return f"mirror {self.data.collection} from {self.data.url}"
